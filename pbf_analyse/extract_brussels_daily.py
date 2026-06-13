#!/usr/bin/env python3
"""
Daily Brussels PBF extract via SliceOSM API + precise boundary clipping.

1. Fetch a bbox extract from SliceOSM (minute-level replication freshness).
2. Download the Brussels-Capital Region boundary (OSM relation 54094)
   from a custom .poly file hosted on GitHub, apply a 100 m buffer.
3. Clip the PBF to that buffered polygon with osmium-tool.
"""

import json
import math
import os
import subprocess
import sys
import time
import urllib.request

from shapely.geometry import Polygon, MultiPolygon
from shapely.ops import transform, unary_union

# ── Configuration ──────────────────────────────────────────────────
SLICEOSM_API   = "https://slice.openstreetmap.us/api/"
SLICEOSM_FILES = "https://slice.openstreetmap.us/files/"

# Brussels bbox – must fully enclose the buffered boundary
# min_lat, min_lon, max_lat, max_lon
BBOX = [50.749369, 4.202947, 50.944910, 4.512645]

# Brussels-Capital Region (OSM relation)
BOUNDARY_RELATION_ID = 54094

# Custom .poly file hosted on GitHub (raw content)
BOUNDARY_POLY_URL = (
    "https://raw.githubusercontent.com/PasLoin/"
    "Osm-python-analyse_Belgium/main/pbf_analyse/54094.poly"
)
BUFFER_METERS = 100

POLL_INTERVAL = 5        # seconds between status checks
POLL_TIMEOUT  = 600      # give up after 10 minutes

HISTORY_DIR = os.path.join(os.path.dirname(__file__), "history")
OUTPUT_PBF  = os.path.join(HISTORY_DIR, "Brussels-daily.pbf")
STATE_FILE  = os.path.join(HISTORY_DIR, "state.txt")

# ── SliceOSM ───────────────────────────────────────────────────────

def submit_task() -> str:
    """POST a bbox extract job and return the task UUID."""
    payload = json.dumps({
        "Name": "brussels-daily",
        "RegionType": "bbox",
        "RegionData": BBOX,
    }).encode()

    req = urllib.request.Request(
        SLICEOSM_API,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        uuid = resp.read().decode().strip()

    if not uuid or len(uuid) < 10:
        raise RuntimeError(f"Unexpected API response: {uuid!r}")

    print(f"Task submitted: {uuid}")
    return uuid


def wait_for_completion(uuid: str) -> dict:
    """Poll the task status until Complete or timeout."""
    url = f"{SLICEOSM_API}{uuid}"
    deadline = time.time() + POLL_TIMEOUT

    while time.time() < deadline:
        with urllib.request.urlopen(url, timeout=15) as resp:
            status = json.loads(resp.read())

        if status.get("Complete"):
            print(f"Extract complete – {status.get('SizeBytes', '?')} bytes, "
                  f"elapsed {status.get('Elapsed', '?')}")
            return status

        cells = status.get("CellsProg", "?")
        cells_total = status.get("CellsTotal", "?")
        print(f"  … progress: cells {cells}/{cells_total}")
        time.sleep(POLL_INTERVAL)

    raise TimeoutError(f"Task {uuid} did not complete within {POLL_TIMEOUT}s")


def download_pbf(uuid: str, dest: str):
    """Download the resulting .osm.pbf file to *dest*."""
    url = f"{SLICEOSM_FILES}{uuid}.osm.pbf"
    os.makedirs(os.path.dirname(dest), exist_ok=True)

    print(f"Downloading {url} …")
    urllib.request.urlretrieve(url, dest)
    size_mb = os.path.getsize(dest) / (1024 * 1024)
    print(f"Saved {dest} ({size_mb:.1f} MB)")


# ── Boundary polygon ──────────────────────────────────────────────

def fetch_poly_file() -> str:
    """Download the .poly file for the Brussels-Capital boundary from GitHub."""
    print(f"Fetching boundary polygon from {BOUNDARY_POLY_URL} …")
    req = urllib.request.Request(
        BOUNDARY_POLY_URL,
        headers={"User-Agent": "brussels-pbf-clip-script"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        text = resp.read().decode()
    print(f"  received {len(text)} bytes")
    return text


def parse_poly(text: str) -> list[list[tuple[float, float]]]:
    """
    Parse an Osmosis .poly file into a list of (ring, is_hole) pairs.

    Format:
        polygon_name
        ring_name          (prefixed with ! for holes)
            lon  lat
            …
        END
        END
    """
    rings: list[tuple[list[tuple[float, float]], bool]] = []
    current_coords: list[tuple[float, float]] | None = None
    is_hole = False

    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue

        if stripped == "END":
            if current_coords is not None:
                rings.append((current_coords, is_hole))
                current_coords = None
                is_hole = False
            continue

        parts = stripped.split()
        if len(parts) == 2:
            try:
                lon, lat = float(parts[0]), float(parts[1])
                if current_coords is None:
                    current_coords = []
                current_coords.append((lon, lat))
            except ValueError:
                pass
        elif len(parts) == 1:
            # Ring name line – start collecting coordinates
            is_hole = stripped.startswith("!")
            current_coords = []

    return rings


def build_geometry(rings: list) -> Polygon | MultiPolygon:
    """Convert parsed rings into a Shapely geometry."""
    # Separate outer rings and holes
    outers = [coords for coords, hole in rings if not hole]
    holes  = [coords for coords, hole in rings if hole]

    if len(outers) == 1:
        return Polygon(outers[0], holes)
    else:
        # Multiple outer rings → MultiPolygon
        # Simple assignment: holes go to whichever outer ring contains them
        polys = []
        for outer_coords in outers:
            outer_poly = Polygon(outer_coords)
            inner = [h for h in holes if outer_poly.contains(Polygon(h))]
            polys.append(Polygon(outer_coords, inner))
        return MultiPolygon(polys) if len(polys) > 1 else polys[0]


def buffer_wgs84(geom, meters: float):
    """
    Buffer a WGS84 geometry by an approximate metric distance.

    Projects to a local equirectangular approximation (good enough for
    city-scale polygons at mid-latitudes).
    """
    center_lat = geom.centroid.y
    m_per_deg_lat = 111_320
    m_per_deg_lon = 111_320 * math.cos(math.radians(center_lat))

    to_meters   = lambda x, y: (x * m_per_deg_lon, y * m_per_deg_lat)
    to_degrees  = lambda x, y: (x / m_per_deg_lon, y / m_per_deg_lat)

    projected = transform(to_meters, geom)
    buffered  = projected.buffer(meters, resolution=32)
    return transform(to_degrees, buffered)


def write_poly_file(geom, path: str):
    """Write a Shapely geometry as an Osmosis .poly file."""
    if isinstance(geom, Polygon):
        geom = MultiPolygon([geom])

    with open(path, "w") as f:
        f.write("brussels-buffered\n")
        idx = 1
        for poly in geom.geoms:
            # Outer ring
            f.write(f"{idx}\n")
            for lon, lat in poly.exterior.coords:
                f.write(f"\t{lon:.7E}\t{lat:.7E}\n")
            f.write("END\n")

            # Inner rings (holes)
            for hole in poly.interiors:
                idx += 1
                f.write(f"!{idx}\n")
                for lon, lat in hole.coords:
                    f.write(f"\t{lon:.7E}\t{lat:.7E}\n")
                f.write("END\n")

            idx += 1
        f.write("END\n")

    print(f"Wrote buffered .poly → {path}")


# ── Clipping ──────────────────────────────────────────────────────

def clip_pbf(poly_path: str, input_pbf: str, output_pbf: str):
    """Clip a PBF file to a .poly boundary using osmium extract."""
    print(f"Clipping with osmium extract …")
    subprocess.run(
        [
            "osmium", "extract",
            "-p", poly_path,
            "-s", "smart",
            input_pbf,
            "-o", output_pbf,
            "--overwrite",
        ],
        check=True,
    )
    size_mb = os.path.getsize(output_pbf) / (1024 * 1024)
    print(f"Clipped PBF: {output_pbf} ({size_mb:.1f} MB)")


# ── State file ────────────────────────────────────────────────────

def write_state(uuid: str, status: dict):
    """Write a small state file with timestamp and metadata."""
    info = {
        "uuid": uuid,
        "timestamp": status.get("Timestamp", ""),
        "size_bytes": status.get("SizeBytes", ""),
        "elapsed": status.get("Elapsed", ""),
        "bbox": BBOX,
        "boundary_relation": BOUNDARY_RELATION_ID,
        "boundary_poly_url": BOUNDARY_POLY_URL,
        "buffer_meters": BUFFER_METERS,
    }
    with open(STATE_FILE, "w") as f:
        json.dump(info, f, indent=2)
    print(f"State written to {STATE_FILE}")


# ── Main ──────────────────────────────────────────────────────────

def main():
    os.makedirs(HISTORY_DIR, exist_ok=True)
    raw_pbf  = os.path.join(HISTORY_DIR, "Brussels-daily-raw.pbf")
    poly_path = os.path.join(HISTORY_DIR, "brussels-boundary.poly")

    # 1 — Fetch bbox extract from SliceOSM
    uuid   = submit_task()
    status = wait_for_completion(uuid)
    download_pbf(uuid, raw_pbf)

    # 2 — Fetch boundary, buffer, write .poly
    poly_text = fetch_poly_file()
    rings     = parse_poly(poly_text)
    boundary  = build_geometry(rings)
    buffered  = buffer_wgs84(boundary, BUFFER_METERS)
    write_poly_file(buffered, poly_path)

    # 3 — Clip
    clip_pbf(poly_path, raw_pbf, OUTPUT_PBF)

    # 4 — Clean up raw file & write state
    os.remove(raw_pbf)
    write_state(uuid, status)

    print("Done ✓")


if __name__ == "__main__":
    main()
