#!/usr/bin/env python3
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request

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

OSMFR_EXTRACT = os.environ.get(
    "OSMFR_EXTRACT",
    "https://download.openstreetmap.fr/extracts/europe/belgium/brussels_capital_region-latest.osm.pbf",
)
OSMFR_STATE = os.environ.get(
    "OSMFR_STATE",
    "https://download.openstreetmap.fr/extracts/europe/belgium/brussels_capital_region.state.txt",
)
OSMFR_REPLICATION = os.environ.get(
    "OSMFR_REPLICATION",
    "https://download.openstreetmap.fr/replication/europe/belgium/brussels_capital_region/minute/",
)

USER_AGENT = "Osm-python-analyse_Belgium backup (github.com/PasLoin)"
CHUNK_KB = 50 * 1024

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
HISTORY_DIR = os.path.join(BASE_DIR, "history")
BACKUP_DIR = os.path.join(BASE_DIR, "backup")
BASE_PBF = os.path.join(BACKUP_DIR, "osmfr_brussels_capital_region-latest.osm.pbf")
BASE_STATE = os.path.join(BACKUP_DIR, "osmfr_brussels_capital_region.state.txt")
OUTPUT_PBF = os.path.join(HISTORY_DIR, "Brussels-daily.pbf")
STATE_FILE = os.path.join(HISTORY_DIR, "state.txt")


def http_get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def parse_state(text: str) -> dict:
    state = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        state[key.strip()] = value.strip().replace("\\:", ":")
    if "sequenceNumber" not in state:
        raise ValueError(f"State invalide : {text!r}")
    state["sequenceNumber"] = int(state["sequenceNumber"])
    return state


def read_remote_state() -> tuple[dict, str] | None:
    try:
        text = http_get(OSMFR_STATE, timeout=30).decode()
        return parse_state(text), text
    except Exception as exc:
        print(f"State OSM-FR indisponible : {exc}")
        return None


def read_local_state() -> dict | None:
    if not (os.path.isfile(BASE_STATE) and os.path.isfile(BASE_PBF)):
        return None
    with open(BASE_STATE) as f:
        return parse_state(f.read())


def header_sequence(path: str) -> int | None:
    reader = osmium.io.Reader(path, osmium.osm.osm_entity_bits.NOTHING)
    try:
        value = reader.header().get("osmosis_replication_sequence_number", "")
    finally:
        reader.close()
    return int(value) if value.strip().isdigit() else None


def download_base(state_text: str):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    tmp = BASE_PBF.replace(".osm.pbf", ".part.osm.pbf")
    print(f"Téléchargement de {OSMFR_EXTRACT} …")
    req = urllib.request.Request(OSMFR_EXTRACT, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=300) as resp, open(tmp, "wb") as f:
            shutil.copyfileobj(resp, f)
        subprocess.run(["osmium", "fileinfo", tmp], check=True, stdout=subprocess.DEVNULL)
        os.replace(tmp, BASE_PBF)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    with open(BASE_STATE, "w") as f:
        f.write(state_text)
    size_mb = os.path.getsize(BASE_PBF) / (1024 * 1024)
    print(f"Base enregistrée : {BASE_PBF} ({size_mb:.1f} MB)")


def ensure_base(force: bool) -> tuple[int, str]:
    remote = read_remote_state()
    local = read_local_state()

    if remote is None and local is None:
        raise RuntimeError("Ni state OSM-FR distant, ni base locale : impossible de continuer.")

    if remote is None:
        print("OSM-FR injoignable, utilisation de la base du dépôt.")
    elif force or local is None or local["sequenceNumber"] != remote[0]["sequenceNumber"]:
        print(
            f"Base distante seq={remote[0]['sequenceNumber']}, "
            f"locale seq={local['sequenceNumber'] if local else 'aucune'} → téléchargement."
        )
        download_base(remote[1])
        after = read_remote_state()
        if after and after[0]["sequenceNumber"] != remote[0]["sequenceNumber"]:
            print("L'extrait OSM-FR a changé pendant le téléchargement, nouvel essai.")
            download_base(after[1])
        local = read_local_state()
    else:
        print(f"Base du dépôt identique à OSM-FR (seq={local['sequenceNumber']}), pas de téléchargement.")

    seq = header_sequence(BASE_PBF)
    if seq is not None and seq != local["sequenceNumber"]:
        print(f"Séquence du header PBF ({seq}) ≠ state.txt ({local['sequenceNumber']}), on suit le header.")
    else:
        seq = local["sequenceNumber"]
    return seq, local.get("timestamp", "")


def apply_minutely(base_seq: int, base_ts: str, workdir: str) -> tuple[str, int, str]:
    with ReplicationServer(OSMFR_REPLICATION) as svr:
        svr.set_request_parameter("headers", {"User-Agent": USER_AGENT})
        svr.set_request_parameter("timeout", 60)

        newest = svr.get_state_info()
        if newest is None:
            raise RuntimeError(f"Impossible de lire {OSMFR_REPLICATION}state.txt")
        print(f"Réplication OSM-FR : seq={newest.sequence} ({newest.timestamp:%Y-%m-%dT%H:%M:%SZ})")

        if newest.sequence <= base_seq:
            print("Aucun diff plus récent que la base.")
            return BASE_PBF, base_seq, base_ts

        print(f"{newest.sequence - base_seq} diffs minute à appliquer.")
        infile, seq, step = BASE_PBF, base_seq, 0
        while seq < newest.sequence:
            step += 1
            outfile = os.path.join(workdir, f"step{step}.osm.pbf")
            res = svr.apply_diffs_to_file(
                infile,
                outfile,
                seq + 1,
                max_size=CHUNK_KB,
                end_id=newest.sequence,
                extra_headers={"generator": "backup_osmfr_minutely.py"},
            )
            if res is None:
                raise RuntimeError(
                    f"Échec du téléchargement des diffs à partir de {seq + 1} "
                    "(diffs plus disponibles sur le serveur ?)"
                )
            if infile != BASE_PBF:
                os.remove(infile)
            infile, seq = outfile, res[0]
            print(f"  appliqué jusqu'à seq={seq}")

        info = svr.get_state_info(seq)
        ts = info.timestamp.strftime("%Y-%m-%dT%H:%M:%SZ") if info else ""
        return infile, seq, ts


def build_buffered_poly(path: str):
    rings = parse_poly(fetch_poly_file())
    buffered = buffer_wgs84(build_geometry(rings), BUFFER_METERS)
    write_poly_file(buffered, path)


def write_state(seq: int, ts: str, base_seq: int):
    info = {
        "source": "openstreetmap.fr (backup)",
        "replication_url": OSMFR_REPLICATION,
        "sequence_number": seq,
        "base_sequence_number": base_seq,
        "timestamp": ts,
        "size_bytes": os.path.getsize(OUTPUT_PBF),
        "boundary_relation": BOUNDARY_RELATION_ID,
        "boundary_poly_url": BOUNDARY_POLY_URL,
        "buffer_meters": BUFFER_METERS,
    }
    with open(STATE_FILE, "w") as f:
        json.dump(info, f, indent=2)
    print(f"State écrit dans {STATE_FILE}")


def write_summary(base_seq: int, seq: int, ts: str):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    with open(path, "a") as f:
        f.write("### Backup Brussels PBF (OSM-FR)\n\n")
        f.write(f"- Base OSM-FR : seq `{base_seq}`\n")
        f.write(f"- Diffs appliqués : `{seq - base_seq}`\n")
        f.write(f"- Données à jour au : `{ts}` (seq `{seq}`)\n")
        f.write(f"- Taille : {os.path.getsize(OUTPUT_PBF) / (1024 * 1024):.1f} MB\n")


def main():
    force = os.environ.get("FORCE_DOWNLOAD", "false").lower() == "true"
    os.makedirs(HISTORY_DIR, exist_ok=True)

    base_seq, base_ts = ensure_base(force)

    with tempfile.TemporaryDirectory() as workdir:
        updated, seq, ts = apply_minutely(base_seq, base_ts, workdir)
        poly_path = os.path.join(workdir, "brussels-boundary.poly")
        build_buffered_poly(poly_path)
        clipped = os.path.join(workdir, "Brussels-daily.osm.pbf")
        clip_pbf(poly_path, updated, clipped)
        shutil.move(clipped, OUTPUT_PBF)

    write_state(seq, ts, base_seq)
    write_summary(base_seq, seq, ts)
    print("Terminé ✓")


if __name__ == "__main__":
    main()
