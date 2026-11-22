#!/usr/bin/env python3
import datetime as dt
import json
import os
import shutil
import sys
import tempfile

import osmium
from osmium.replication.server import ReplicationServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extract_brussels_daily import (
    BOUNDARY_POLY_URL,
    BOUNDARY_RELATION_ID,
    BUFFER_METERS,
    build_geometry,
    buffer_wgs84,
    clip_pbf,
    fetch_poly_file,
    parse_poly,
    write_poly_file,
)

REPLICATION_URL = os.environ.get(
    "REPLICATION_URL", "https://planet.openstreetmap.org/replication/hour/"
).rstrip("/") + "/"
USER_AGENT = "Osm-python-analyse_Belgium hourly update (github.com/PasLoin)"
CHUNK_KB = int(os.environ.get("CHUNK_KB", str(256 * 1024)))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_DIR = os.environ.get("HISTORY_DIR", os.path.join(BASE_DIR, "history"))
DAILY_PBF = os.path.join(HISTORY_DIR, "Brussels-daily.pbf")
STATE_FILE = os.path.join(HISTORY_DIR, "state.txt")


def parse_ts(value: str) -> dt.datetime | None:
    if not value:
        return None
    ts = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return ts if ts.tzinfo else ts.replace(tzinfo=dt.timezone.utc)


def fmt_ts(ts: dt.datetime) -> str:
    return ts.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_header(path: str) -> dict:
    reader = osmium.io.Reader(path, osmium.osm.osm_entity_bits.NOTHING)
    try:
        h = reader.header()
        return {
            "base_url": h.get("osmosis_replication_base_url", ""),
            "sequence": h.get("osmosis_replication_sequence_number", ""),
            "timestamp": h.get("osmosis_replication_timestamp", ""),
        }
    finally:
        reader.close()


def read_state() -> dict:
    if not os.path.isfile(STATE_FILE):
        return {}
    with open(STATE_FILE) as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}


def same_server(url: str) -> bool:
    return bool(url) and url.rstrip("/") + "/" == REPLICATION_URL


def find_start(svr: ReplicationServer, header: dict, state: dict) -> tuple[int, dt.datetime, str]:
    if same_server(header["base_url"]) and header["sequence"].isdigit():
        seq = int(header["sequence"])
        info = svr.get_state_info(seq)
        ts = info.timestamp if info else parse_ts(header["timestamp"])
        return seq + 1, ts, f"header PBF (seq {seq})"

    if same_server(state.get("replication_url", "")) and str(state.get("sequence_number", "")).isdigit():
        seq = int(state["sequence_number"])
        info = svr.get_state_info(seq)
        ts = info.timestamp if info else parse_ts(state.get("timestamp", ""))
        return seq + 1, ts, f"state.txt (seq {seq})"

    ts = parse_ts(header["timestamp"]) or parse_ts(state.get("timestamp", ""))
    if ts is None:
        raise RuntimeError("Aucun timestamp de réplication dans le header PBF ni dans state.txt.")
    seq = svr.timestamp_to_sequence(ts, limit_by_oldest_available=True)
    if seq is None:
        raise RuntimeError(f"Aucune séquence trouvée pour {fmt_ts(ts)} sur {REPLICATION_URL}")
    origin = "header PBF" if parse_ts(header["timestamp"]) else "state.txt"
    return seq, ts, f"timestamp {fmt_ts(ts)} ({origin})"


def build_buffered_poly(path: str):
    rings = parse_poly(fetch_poly_file())
    write_poly_file(buffer_wgs84(build_geometry(rings), BUFFER_METERS), path)


def apply_hourly(svr: ReplicationServer, start: int, poly: str, workdir: str) -> tuple[str, int] | None:
    newest = svr.get_state_info()
    if newest is None:
        raise RuntimeError(f"Impossible de lire {REPLICATION_URL}state.txt")
    print(f"Serveur : seq={newest.sequence} ({fmt_ts(newest.timestamp)})")
    if start > newest.sequence:
        return None

    print(f"Diffs à appliquer : {start} → {newest.sequence} ({newest.sequence - start + 1})")
    infile, seq, step = DAILY_PBF, start - 1, 0
    while seq < newest.sequence:
        step += 1
        merged = os.path.join(workdir, f"merged{step}.osm.pbf")
        clipped = os.path.join(workdir, f"clipped{step}.osm.pbf")
        res = svr.apply_diffs_to_file(
            infile,
            merged,
            seq + 1,
            max_size=CHUNK_KB,
            end_id=newest.sequence,
            extra_headers={"generator": "update_daily_hourly.py"},
        )
        if res is None:
            raise RuntimeError(f"Échec du téléchargement des diffs à partir de {seq + 1}")
        clip_pbf(poly, merged, clipped)
        os.remove(merged)
        if infile != DAILY_PBF:
            os.remove(infile)
        infile, seq = clipped, res[0]
        print(f"  appliqué jusqu'à seq={seq}")
    return infile, seq


def write_state(previous: dict, base_ts: dt.datetime, seq: int, ts: dt.datetime, applied: int):
    base_source = previous.get("source") or ("SliceOSM" if previous.get("uuid") else "inconnue")
    info = {
        "source": "openstreetmap.org (diffs horaires)",
        "replication_url": REPLICATION_URL,
        "sequence_number": seq,
        "timestamp": fmt_ts(ts),
        "base_source": previous.get("base_source", base_source),
        "base_timestamp": previous.get("base_timestamp", fmt_ts(base_ts)),
        "diffs_applied": applied,
        "size_bytes": os.path.getsize(DAILY_PBF),
        "boundary_relation": BOUNDARY_RELATION_ID,
        "boundary_poly_url": BOUNDARY_POLY_URL,
        "buffer_meters": BUFFER_METERS,
    }
    with open(STATE_FILE, "w") as f:
        json.dump(info, f, indent=2)
    print(f"State écrit dans {STATE_FILE}")


def write_outputs(updated: bool, lines: list[str]):
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"updated={'true' if updated else 'false'}\n")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as f:
            f.write("### Mise à jour horaire de Brussels-daily\n\n")
            f.write("".join(f"- {line}\n" for line in lines))


def main():
    if not os.path.isfile(DAILY_PBF):
        raise RuntimeError(f"{DAILY_PBF} introuvable")

    header = read_header(DAILY_PBF)
    state = read_state()

    with ReplicationServer(REPLICATION_URL) as svr:
        svr.set_request_parameter("headers", {"User-Agent": USER_AGENT})
        svr.set_request_parameter("timeout", 120)

        start, base_ts, origin = find_start(svr, header, state)
        print(f"Point de départ : seq={start} d'après {origin}")

        with tempfile.TemporaryDirectory() as workdir:
            poly = os.path.join(workdir, "brussels-boundary.poly")
            build_buffered_poly(poly)
            result = apply_hourly(svr, start, poly, workdir)
            if result is None:
                print("Aucun diff horaire plus récent que le fichier.")
                write_outputs(False, [f"Départ : {origin}", "Aucun nouveau diff, fichier inchangé"])
                return
            path, seq = result
            shutil.move(path, DAILY_PBF)

        info = svr.get_state_info(seq)
        ts = info.timestamp if info else dt.datetime.now(dt.timezone.utc)

    applied = seq - start + 1
    write_state(state, base_ts, seq, ts, applied)
    write_outputs(True, [
        f"Départ : {origin}",
        f"Diffs appliqués : `{applied}` (seq `{start}` → `{seq}`)",
        f"Données à jour au : `{fmt_ts(ts)}`",
        f"Taille : {os.path.getsize(DAILY_PBF) / (1024 * 1024):.1f} MB",
    ])
    print("Terminé ✓")


if __name__ == "__main__":
    main()
