#!/usr/bin/env python3

import json
import os
import re
import urllib.request
from datetime import datetime, timezone

REPOSITORY = "PasLoin/Osm-python-analyse_Belgium"
API_URL = os.environ.get("GITHUB_API_URL", "https://api.github.com")

HISTORY_DIR = os.path.join(os.path.dirname(__file__), "history")
OUTPUT_FILE = os.path.join(os.path.dirname(__file__), "history-list.json")

RELEASE_BASE = f"https://github.com/{REPOSITORY}/releases/download"
HISTORY_RELEASE_TAG = "history"
DAILY_RELEASE_TAG = "daily"

PATTERN = re.compile(r"^(\d{2})_(\d{2})_(\d{4})_brussels_capital_region\.pbf$")

DAILY_FILENAME = "Brussels-daily.pbf"


def api_get(path: str):
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "Osm-python-analyse_Belgium history list",
    }
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{API_URL}/repos/{REPOSITORY}/{path}", headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def release_assets(tag: str) -> dict:
    release = api_get(f"releases/tags/{tag}")
    assets = {}
    page = 1
    while True:
        batch = api_get(f"releases/{release['id']}/assets?per_page=100&page={page}")
        for asset in batch:
            if asset.get("state", "uploaded") == "uploaded":
                assets[asset["name"]] = asset["size"]
        if len(batch) < 100:
            break
        page += 1
    return assets


def main():
    entries = []

    for fname, size in release_assets(HISTORY_RELEASE_TAG).items():
        m = PATTERN.match(fname)
        if not m:
            continue

        dd, mm, yyyy = m.groups()
        url = f"{RELEASE_BASE}/{HISTORY_RELEASE_TAG}/{fname}"
        entries.append({
            "date": f"{yyyy}-{mm}-{dd}",
            "filename": fname,
            "url": url,
            "release_url": url,
            "size_bytes": size,
            "type": "snapshot",
        })

    if not entries:
        raise RuntimeError(f"No snapshot found in release '{HISTORY_RELEASE_TAG}'")

    entries.sort(key=lambda e: e["date"])

    daily_path = os.path.join(HISTORY_DIR, DAILY_FILENAME)
    if os.path.isfile(daily_path):
        daily_size = os.path.getsize(daily_path)
    else:
        daily_size = release_assets(DAILY_RELEASE_TAG).get(DAILY_FILENAME)

    if daily_size is not None:
        url = f"{RELEASE_BASE}/{DAILY_RELEASE_TAG}/{DAILY_FILENAME}"
        entries.append({
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "filename": DAILY_FILENAME,
            "url": url,
            "release_url": url,
            "size_bytes": daily_size,
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
