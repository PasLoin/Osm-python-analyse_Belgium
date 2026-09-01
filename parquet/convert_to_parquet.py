#!/usr/bin/env python3

from pathlib import Path

import quackosm as qosm

SOURCE_PBF_URL = (
    "https://raw.githubusercontent.com/PasLoin/"
    "Osm-python-analyse_Belgium/main/pbf_analyse/history/Brussels-daily.pbf"
)

OUTPUT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

output_path = OUTPUT_DIR / "brussels-latest.parquet"

print(f"Source : {SOURCE_PBF_URL}")
print(f"Sortie : {output_path}")

result_path = qosm.convert_pbf_to_parquet(
    SOURCE_PBF_URL,
    result_file_path=output_path,
    working_directory="/tmp/quackosm-work",
    ignore_cache=True,
    verbosity_mode="verbose",
)

print(f"Terminé : {result_path}")
