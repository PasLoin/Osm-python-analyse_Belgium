#!/usr/bin/env bash
#### this a exemple in bash to use parquet file to caluclate area on landuse
set -euo pipefail

URL="https://raw.githubusercontent.com/PasLoin/Osm-python-analyse_Belgium/main/parquet/brussels-latest.parquet"
MAX_AREA_M2="${1:-100}"
TMP_DIR="$(mktemp -d)"
FILE="$TMP_DIR/brussels-latest.parquet"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv"
OUTPUT_CSV="$SCRIPT_DIR/small_landuse.csv"
OUTPUT_GEOJSON="$SCRIPT_DIR/small_landuse.geojson"

curl -L -o "$FILE" "$URL"

if [ ! -x "$VENV_DIR/bin/python3" ]; then
    python3 -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" show duckdb >/dev/null 2>&1 || "$VENV_DIR/bin/pip" install --quiet duckdb

"$VENV_DIR/bin/python3" - "$FILE" "$MAX_AREA_M2" "$OUTPUT_CSV" "$OUTPUT_GEOJSON" <<'PYEOF'
import sys
import json
import csv
from pathlib import Path

import duckdb

file_path = sys.argv[1]
max_area = float(sys.argv[2])
output_csv = Path(sys.argv[3])
output_geojson = Path(sys.argv[4])

con = duckdb.connect()
con.execute("INSTALL spatial")
con.execute("LOAD spatial")
con.execute("SET geometry_always_xy = true")

query = """
WITH candidates AS (
    SELECT
        feature_id,
        tags['landuse'] AS landuse,
        ST_Area_Spheroid(geometry) AS area_m2,
        geometry,
        tags
    FROM read_parquet(?)
    WHERE tags['landuse'] IN ('residential', 'commercial')
      AND ST_GeometryType(geometry) IN ('POLYGON', 'MULTIPOLYGON')
)
SELECT feature_id, landuse, area_m2, ST_AsGeoJSON(geometry) AS geojson, tags
FROM candidates
WHERE area_m2 < ?
ORDER BY area_m2
"""

rows = con.execute(query, [file_path, max_area]).fetchall()

csv_rows = []
geojson_features = []

for feature_id, landuse, area_m2, geojson_str, tags in rows:
    osm_type, osm_id = feature_id.split("/", 1)
    row = {
        "feature_id": feature_id,
        "osm_type": osm_type,
        "osm_id": osm_id,
        "osm_url": f"https://www.openstreetmap.org/{osm_type}/{osm_id}",
        "landuse": landuse,
        "area_m2": round(area_m2, 2),
    }
    csv_rows.append(row)
    geojson_features.append({
        "type": "Feature",
        "geometry": json.loads(geojson_str),
        "properties": {**row, "tags": dict(tags)},
    })

with output_csv.open("w", newline="", encoding="utf-8") as f:
    fieldnames = ["feature_id", "osm_type", "osm_id", "osm_url", "landuse", "area_m2"]
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(csv_rows)

with output_geojson.open("w", encoding="utf-8") as f:
    json.dump({"type": "FeatureCollection", "features": geojson_features}, f, ensure_ascii=False)

print(f"landuse=residential/commercial avec superficie < {max_area:.0f} m2 : {len(rows)}")
print()
for r in csv_rows:
    print(f"{r['feature_id']} ({r['landuse']}, {r['area_m2']} m2) -> {r['osm_url']}")
print()
print(f"CSV : {output_csv}")
print(f"GeoJSON : {output_geojson}")
PYEOF

rm -rf "$TMP_DIR"
