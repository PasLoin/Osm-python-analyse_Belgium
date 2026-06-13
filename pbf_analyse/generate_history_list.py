#!/usr/bin/env python3
"""
Generate history-list.json: an index of all dated Brussels snapshot
PBF files present in pbf_analyse/history/.

Filenames follow the convention DD_MM_YYYY_brussels_capital_region.pbf
(DD is not always "01" — some daily-runner snapshots land on other
days of the month).

Output is consumed by other workflows/sites (e.g. Brussels Pedestrian
Network) to avoid querying the GitHub API repeatedly.
"""

import json
import os
import re

HISTORY_DIR = os.path.join(os.path.dirname(__file__), "history")
OUTPUT_FILE = os.path.join(os.path.dirname(__file__), "history-list.json")

RAW_BASE = (
    "https://raw.githubusercontent.com/PasLoin/"
    "Osm-python-analyse_Belgium/main/pbf_analyse/history"
)

PATTERN = re.compile(r"^(\d{2})_(\d{2})_(\d{4})_brussels_capital_region\.pbf$")


def main():
    if not os.path.isdir(HISTORY_DIR):
        raise FileNotFoundError(f"History directory not found: {HISTORY_DIR}")

    entries = []
    for fname in sorted(os.listdir(HISTORY_DIR)):
        m = PATTERN.match(fname)
        if not m:
            continue

        dd, mm, yyyy = m.groups()
        date_iso = f"{yyyy}-{mm}-{dd}"
        path = os.path.join(HISTORY_DIR, fname)

        entries.append({
            "date": date_iso,
            "filename": fname,
            "url": f"{RAW_BASE}/{fname}",
            "size_bytes": os.path.getsize(path),
        })

    entries.sort(key=lambda e: e["date"])

    with open(OUTPUT_FILE, "w") as f:
        json.dump(entries, f, indent=2)
        f.write("\n")

    print(f"Wrote {len(entries)} entries to {OUTPUT_FILE}")
    for e in entries:
        print(f"  {e['date']}  {e['filename']}  ({e['size_bytes']} bytes)")


if __name__ == "__main__":
    main()
