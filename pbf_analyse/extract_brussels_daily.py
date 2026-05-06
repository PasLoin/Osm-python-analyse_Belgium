#!/usr/bin/env python3
"""
Download Belgium PBF from Geofabrik, extract Brussels-Capital Region
using its exact administrative boundary (+ 500m buffer) via osmium-tool.

Boundary source: polygons.openstreetmap.fr (OSM relation 54094)
"""

import subprocess
import os
import json
from datetime import datetime, timezone

# --- Configuration ---
GEOFABRIK_URL = "https://download.geofabrik.de/europe/belgium-latest.osm.pbf"
GEOFABRIK_STATE_URL = "https://download.geofabrik.de/europe/belgium-updates/state.txt"

# Brussels-Capital Region boundary from OSM France polygon service
# Relation 54094 = Région de Bruxelles-Capitale
BOUNDARY_GEOJSON_URL = "https://polygons.openstreetmap.fr/get_geojson.py?id=54094&params=0"

BUFFER_METERS = 500

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "history")
BELGIUM_PBF = os.path.join(OUTPUT_DIR, "belgium-latest.osm.pbf")
BRUSSELS_PBF = os.path.join(OUTPUT_DIR, "Brussels-daily.pbf")
BOUNDARY_GEOJSON = os.path.join(OUTPUT_DIR, "brussels-boundary.geojson")
STATE_FILE = os.path.join(OUTPUT_DIR, "state.txt")


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run a command with error checking."""
    return subprocess.run(cmd, check=True, **kwargs)


def download_file(url: str, dest: str) -> None:
    """Download a file using curl."""
    print(f"Downloading {url} ...")
    run(["curl", "-fSL", "--retry", "3", "-o", dest, url])
    size_mb = os.path.getsize(dest) / (1024 * 1024)
    print(f"  -> {dest} ({size_mb:.1f} MB)")


def fetch_geofabrik_state() -> str:
    """Fetch the Geofabrik replication state to get the data timestamp."""
    result = subprocess.run(
        ["curl", "-fsSL", GEOFABRIK_STATE_URL],
        capture_output=True, text=True,
    )
    if result.returncode == 0:
        for line in result.stdout.splitlines():
            if line.startswith("timestamp="):
                return line.split("=", 1)[1].strip().replace("\\", "")
    return "unknown"


def fetch_and_buffer_boundary() -> None:
    """
    Download the Brussels-Capital Region GeoJSON boundary from
    polygons.openstreetmap.fr, apply a 500m buffer, and save
    as a GeoJSON FeatureCollection for osmium extract.
    """
    from shapely.geometry import shape, mapping
    from shapely.ops import transform
    import pyproj

    # 1. Download raw boundary
    raw_path = BOUNDARY_GEOJSON + ".raw"
    print(f"Fetching boundary from polygons.openstreetmap.fr (relation 54094) ...")
    run(["curl", "-fsSL", "--retry", "3", "-o", raw_path, BOUNDARY_GEOJSON_URL])

    with open(raw_path) as f:
        raw = json.load(f)
    os.remove(raw_path)

    # The service returns a GeoJSON geometry (Polygon or MultiPolygon)
    # or a Feature/FeatureCollection — handle both
    if raw.get("type") in ("Polygon", "MultiPolygon"):
        geom = shape(raw)
    elif raw.get("type") == "Feature":
        geom = shape(raw["geometry"])
    elif raw.get("type") == "FeatureCollection":
        geom = shape(raw["features"][0]["geometry"])
    else:
        raise ValueError(f"Unexpected GeoJSON type: {raw.get('type')}")

    print(f"  Boundary: {geom.geom_type}, area ~ {geom.area:.6f} deg²")

    # 2. Buffer in metric CRS (Belgian Lambert 2008, EPSG:3812)
    to_m = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:3812", always_xy=True).transform
    to_ll = pyproj.Transformer.from_crs("EPSG:3812", "EPSG:4326", always_xy=True).transform

    buffered = transform(to_ll, transform(to_m, geom).buffer(BUFFER_METERS))
    print(f"  Buffered by {BUFFER_METERS}m")

    # 3. Save as FeatureCollection (osmium extract format)
    fc = {
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "properties": {
                "name": "Brussels-Capital Region",
                "osm_relation": 54094,
                "buffer_m": BUFFER_METERS,
            },
            "geometry": mapping(buffered),
        }],
    }
    with open(BOUNDARY_GEOJSON, "w") as f:
        json.dump(fc, f)
    print(f"  -> {BOUNDARY_GEOJSON}")


def extract_brussels() -> None:
    """Extract Brussels-Capital Region from Belgium PBF using osmium extract."""
    print("Extracting Brussels-Capital Region (polygon) ...")
    tmp_output = BRUSSELS_PBF + ".tmp"
    run([
        "osmium", "extract",
        "--polygon", BOUNDARY_GEOJSON,
        "--strategy", "smart",
        "--overwrite",
        "--output-format", "pbf",
        "-o", tmp_output,
        BELGIUM_PBF,
    ])
    os.replace(tmp_output, BRUSSELS_PBF)
    size_mb = os.path.getsize(BRUSSELS_PBF) / (1024 * 1024)
    print(f"  -> {BRUSSELS_PBF} ({size_mb:.1f} MB)")

    # Validate the PBF is readable
    print("Validating PBF ...")
    run(["osmium", "fileinfo", BRUSSELS_PBF])


def write_state(data_timestamp: str) -> None:
    """Write state.txt with creation and data timestamps."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    content = (
        f"# Brussels-daily.pbf state file\n"
        f"creation_date={now}\n"
        f"data_date={data_timestamp}\n"
        f"boundary=OSM relation 54094 (Brussels-Capital Region) + {BUFFER_METERS}m buffer\n"
        f"boundary_source={BOUNDARY_GEOJSON_URL}\n"
        f"source={GEOFABRIK_URL}\n"
    )
    with open(STATE_FILE, "w") as f:
        f.write(content)
    print(f"  -> {STATE_FILE}")
    print(content)


def main() -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 1. Fetch boundary & apply buffer
    fetch_and_buffer_boundary()

    # 2. Download Belgium PBF
    download_file(GEOFABRIK_URL, BELGIUM_PBF)

    # 3. Get data timestamp
    data_timestamp = fetch_geofabrik_state()
    print(f"Geofabrik data timestamp: {data_timestamp}")

    # 4. Extract Brussels region
    extract_brussels()

    # 5. Write state file
    write_state(data_timestamp)

    # 6. Clean up Belgium file
    os.remove(BELGIUM_PBF)
    print(f"Cleaned up {BELGIUM_PBF}")

    print("\nDone!")


if __name__ == "__main__":
    main()
