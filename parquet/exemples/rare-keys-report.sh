#!/usr/bin/env bash
set -euo pipefail

URL="https://github.com/PasLoin/Osm-python-analyse_Belgium/releases/download/daily/Brussels-daily.quackosm.parquet"
MAX_USES="${1:-3}"
TMP_DIR="$(mktemp -d)"
FILE="${PARQUET_FILE:-$TMP_DIR/Brussels-daily.quackosm.parquet}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/venv"
OUTPUT_CSV="$SCRIPT_DIR/rare_keys.csv"

if [ ! -f "$FILE" ]; then
    curl -L -o "$FILE" "$URL"
fi

if [ ! -x "$VENV_DIR/bin/python3" ]; then
    python3 -m venv "$VENV_DIR"
fi

"$VENV_DIR/bin/pip" install --quiet --upgrade pip
"$VENV_DIR/bin/pip" show duckdb >/dev/null 2>&1 || "$VENV_DIR/bin/pip" install --quiet duckdb

"$VENV_DIR/bin/python3" - "$FILE" "$MAX_USES" "$OUTPUT_CSV" <<'PYEOF'
import sys

import duckdb

file_path = sys.argv[1]
max_uses = int(sys.argv[2])
output_csv = sys.argv[3]

con = duckdb.connect()
path = "'" + file_path.replace("'", "''") + "'"
rare = con.sql(f"""
    SELECT key, count(*) AS usages
    FROM (SELECT unnest(map_keys(tags)) AS key FROM read_parquet({path}))
    GROUP BY key
    HAVING count(*) <= {max_uses}
    ORDER BY usages, key
""")
rare.write_csv(output_csv)

rows = rare.fetchall()
print(f"Clés utilisées par {max_uses} objets ou moins : {len(rows)}")
print()
for key, usages in rows:
    print(f"{usages:>3}  {key}")
print()
print(f"CSV : {output_csv}")
PYEOF

rm -rf "$TMP_DIR"
