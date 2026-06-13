#!/usr/bin/env python3
"""
Clip a yearly Geofabrik Belgium extract to the Brussels-Capital Region
boundary (OSM relation 54094), using the .poly file hosted on GitHub.

Usage:
    python historical_snapshot.py <geofabrik_code> <output_date_label>

Example:
    python historical_snapshot.py 220101 01_01_2022

This downloads https://download.geofabrik.de/europe/belgium-220101.osm.pbf,
clips it to the buffered Brussels-Capital Region boundary, and writes
./output/01_01_2022_brussels_capital_region.pbf
"""

import math
import os
import subprocess
import sys
import urllib.request

from shapely.geometry import Polygon, MultiPolygon
from shapely.ops import transform

# ── Configuration ──────────────────────────────────────────────────
GEOFABRIK_BASE = "https://download.geofabrik.de/europe/"

BOUNDARY_RELATION_ID = 54094
BOUNDARY_POLY_URL = (
    "https://raw.githubusercontent.com/PasLoin/"
    "Osm-python-analyse_Belgium/main/pbf_analyse/54094.poly"
)
BUFFER_METERS = 100

OUTPUT_DIR = "output"


# ── Boundary polygon (same logic as the daily script) ─────────────

def fetch_poly_file() -> str:
    """Download the .poly file for the Brussels-Capital boundary from GitHub."""
    print(f"Fetching boundary polygon from {BOUNDARY_POLY_URL} …")
    req = urllib.request.Request(
        BOUNDARY_POLY_URL,
        headers={"User-Agent": "brussels-historical-clip-script"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        text = resp.read().decode()
    print(f"  received {len(text)} bytes")
    return text


def parse_poly(text: str) -> list[tuple[list[tuple[float, float]], bool]]:
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
            is_hole = stripped.startswith("!")
            current_coords = []

    return rings


def build_geometry(rings: list) -> Polygon | MultiPolygon:
    """Convert parsed rings into a Shapely geometry."""
    outers = [coords for coords, hole in rings if not hole]
    holes = [coords for coords, hole in rings if hole]

    if len(outers) == 1:
        return Polygon(outers[0], holes)

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

    to_meters = lambda x, y: (x * m_per_deg_lon, y * m_per_deg_lat)
    to_degrees = lambda x, y: (x / m_per_deg_lon, y / m_per_deg_lat)

    projected = transform(to_meters, geom)
    buffered = projected.buffer(meters, resolution=32)
    return transform(to_degrees, buffered)


def write_poly_file(geom, path: str):
    """Write a Shapely geometry as an Osmosis .poly file."""
    if isinstance(geom, Polygon):
        geom = MultiPolygon([geom])

    with open(path, "w") as f:
        f.write("brussels-buffered\n")
        idx = 1
        for poly in geom.geoms:
            f.write(f"{idx}\n")
            for lon, lat in poly.exterior.coords:
                f.write(f"\t{lon:.7E}\t{lat:.7E}\n")
            f.write("END\n")

            for hole in poly.interiors:
                idx += 1
                f.write(f"!{idx}\n")
                for lon, lat in hole.coords:
                    f.write(f"\t{lon:.7E}\t{lat:.7E}\n")
                f.write("END\n")

            idx += 1
        f.write("END\n")

    print(f"Wrote buffered .poly → {path}")


# ── Download ────────────────────────────────────────────────────

def download_file(url: str, dest: str):
    """Download a (possibly large) file with a User-Agent header and progress log."""
    print(f"Downloading {url} …")
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "brussels-historical-clip-script"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as out:
        total = resp.headers.get("Content-Length")
        total = int(total) if total else None
        downloaded = 0
        chunk_size = 1024 * 1024  # 1 MB
        last_pct_reported = -1

        while True:
            chunk = resp.read(chunk_size)
            if not chunk:
                break
            out.write(chunk)
            downloaded += len(chunk)

            if total:
                pct = int(downloaded * 100 / total)
                if pct != last_pct_reported and pct % 10 == 0:
                    print(f"  … {pct}% ({downloaded / (1024*1024):.0f} MB)")
                    last_pct_reported = pct

    size_mb = os.path.getsize(dest) / (1024 * 1024)
    print(f"Saved {dest} ({size_mb:.1f} MB)")


# ── Clipping ──────────────────────────────────────────────────────

def clip_pbf(poly_path: str, input_pbf: str, output_pbf: str):
    """Clip a PBF file to a .poly boundary using osmium extract."""
    print("Clipping with osmium extract …")
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

    if size_mb > 95:
        print(
            f"WARNING: output file is {size_mb:.1f} MB, close to GitHub's "
            f"100 MB per-file limit. Consider Git LFS if this grows further."
        )


# ── Main ────────────────────────────────────────────────────────

def main():
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <geofabrik_code> <output_date_label>")
        print(f"Example: {sys.argv[0]} 220101 01_01_2022")
        sys.exit(1)

    geofabrik_code = sys.argv[1]      # e.g. "220101"
    output_date_label = sys.argv[2]   # e.g. "01_01_2022"

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    raw_pbf = os.path.join(OUTPUT_DIR, f"belgium-{geofabrik_code}-raw.pbf")
    poly_path = os.path.join(OUTPUT_DIR, "brussels-boundary.poly")
    output_pbf = os.path.join(
        OUTPUT_DIR, f"{output_date_label}_brussels_capital_region.pbf"
    )

    # 1 — Download the yearly Belgium extract
    belgium_url = f"{GEOFABRIK_BASE}belgium-{geofabrik_code}.osm.pbf"
    download_file(belgium_url, raw_pbf)

    # 2 — Fetch boundary, buffer, write .poly
    poly_text = fetch_poly_file()
    rings = parse_poly(poly_text)
    boundary = build_geometry(rings)
    buffered = buffer_wgs84(boundary, BUFFER_METERS)
    write_poly_file(buffered, poly_path)

    # 3 — Clip
    clip_pbf(poly_path, raw_pbf, output_pbf)

    # 4 — Clean up raw file
    os.remove(raw_pbf)

    print("Done ✓")


if __name__ == "__main__":
    main()
