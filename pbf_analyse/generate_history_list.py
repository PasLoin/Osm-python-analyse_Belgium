#!/usr/bin/env python3
"""
Generate history-list.json: an index of all dated Brussels snapshot
PBF files present in pbf_analyse/history/, plus the daily extract
(Brussels-daily.pbf).

Filenames for dated snapshots follow the convention
DD_MM_YYYY_brussels_capital_region.pbf (DD is not always "01" — some
daily-runner snapshots land on other days of the month).

Brussels-daily.pbf is updated twice a day (00:30 and 12:30 UTC) and
always reflects "today" — it's listed separately with today's date
and a "type": "daily" marker, since per-run precision isn't useful.

Output is consumed by other workflows/sites (e.g. Brussels Pedestrian
Network) to avoid querying the GitHub API repeatedly.
"""

import json
import os
import re
from datetime import datetime, timezone

HISTORY_DIR = os.path.join(os.path.dirname(__file__), "history")
OUTPUT_FILE = os.path.join(os.path.dirname(__file__), "history-list.json")

RAW_BASE = (
    "https://raw.githubusercontent.com/PasLoin/"
    "Osm-python-analyse_Belgium/main/pbf_analyse/history"
)

PATTERN = re.compile(r"^(\d{2})_(\d{2})_(\d{4})_brussels_capital_region\.pbf$")

DAILY_FILENAME = "Brussels-daily.pbf"


def main():
    if not os.path.isdir(HISTORY_DIR):
        raise FileNotFoundError(f"History directory not found: {HISTORY_DIR}")

    entries = []

    # ── Dated monthly/yearly snapshots ─────────────────────────────
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
            "type": "snapshot",
        })

    entries.sort(key=lambda e: e["date"])

    # ── Daily extract (always "today", appended last) ─────────────
    daily_path = os.path.join(HISTORY_DIR, DAILY_FILENAME)
    if os.path.isfile(daily_path):
        today_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        entries.append({
            "date": today_iso,
            "filename": DAILY_FILENAME,
            "url": f"{RAW_BASE}/{DAILY_FILENAME}",
            "size_bytes": os.path.getsize(daily_path),
            "type": "daily",
        })
    else:
        print(f"Note: {DAILY_FILENAME} not found, skipping daily entry.")

    with open(OUTPUT_FILE, "w") as f:
        json.dump(entries, f, indent=2)
        f.write("\n")

    print(f"Wrote {len(entries)} entries to {OUTPUT_FILE}")
    for e in entries:
        print(f"  {e['date']}  {e['filename']}  ({e['size_bytes']} bytes)  [{e['type']}]")


if __name__ == "__main__":
    main()
