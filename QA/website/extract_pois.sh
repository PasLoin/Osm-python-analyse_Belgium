#!/usr/bin/env bash
# Extrait les nœuds/ways/relations portant website=* ou contact:website=*
# depuis un fichier PBF, et les exporte en GeoJSON.
#
# Usage: ./extract_pois.sh input.osm.pbf output.geojson

set -euo pipefail

INPUT_PBF="${1:?Usage: extract_pois.sh input.osm.pbf output.geojson}"
OUTPUT_GEOJSON="${2:?Usage: extract_pois.sh input.osm.pbf output.geojson}"

FILTERED_PBF="$(mktemp --suffix=.osm.pbf)"

echo "==> Filtrage des objets avec website / contact:website..."
osmium tags-filter \
  "${INPUT_PBF}" \
  n/website n/contact:website \
  w/website w/contact:website \
  r/website r/contact:website \
  -o "${FILTERED_PBF}" \
  --overwrite

echo "==> Export en GeoJSON..."
osmium export "${FILTERED_PBF}" \
  -o "${OUTPUT_GEOJSON}" \
  --output-format=geojson \
  --add-unique-id=type_id \
  --overwrite

rm -f "${FILTERED_PBF}"

COUNT=$(python3 -c "import json,sys; print(len(json.load(open('${OUTPUT_GEOJSON}'))['features']))")
echo "==> ${COUNT} objets extraits -> ${OUTPUT_GEOJSON}"
