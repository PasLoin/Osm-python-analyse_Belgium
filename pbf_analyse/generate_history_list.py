#!/usr/bin/env python3

import json
import os
import re
from datetime import datetime, timezone

HISTORY_DIR = os.path.join(os.path.dirname(__file__), "history")
OUTPUT_FILE = os.path.join(os.path.dirname(__file__), "history-list.json")

RELEASE_BASE = (
    "https://github.com/PasLoin/"
    "Osm-python-analyse_Belgium/releases/download"
)
HISTORY_RELEASE_TAG = "history"
DAILY_RELEASE_TAG = "daily"

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
            "url": f"{RELEASE_BASE}/{HISTORY_RELEASE_TAG}/{fname}",
            "release_url": f"{RELEASE_BASE}/{HISTORY_RELEASE_TAG}/{fname}",
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
            "url": f"{RELEASE_BASE}/{DAILY_RELEASE_TAG}/{DAILY_FILENAME}",
            "release_url": f"{RELEASE_BASE}/{DAILY_RELEASE_TAG}/{DAILY_FILENAME}",
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
