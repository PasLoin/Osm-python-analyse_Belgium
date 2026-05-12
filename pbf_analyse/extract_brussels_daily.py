#!/usr/bin/env python3
"""
Daily Brussels PBF extract via SliceOSM API.
https://github.com/SliceOSM/sliceosm-api

The API submits an extract job against a continuously-updated OSMX
database (minute-level replication), so the result is always fresh.
"""

import json
import os
import sys
import time
import urllib.request

# ── Configuration ──────────────────────────────────────────────────
SLICEOSM_API = "https://slice.openstreetmap.us/api/"
SLICEOSM_FILES = "https://slice.openstreetmap.us/files/"

# Brussels bbox  –  min_lat, min_lon, max_lat, max_lon
BBOX = [50.749369, 4.202947, 50.944910, 4.512645]

POLL_INTERVAL = 5        # seconds between status checks
POLL_TIMEOUT  = 600      # give up after 10 minutes

HISTORY_DIR = os.path.join(os.path.dirname(__file__), "history")
OUTPUT_PBF  = os.path.join(HISTORY_DIR, "Brussels-daily.pbf")
STATE_FILE  = os.path.join(HISTORY_DIR, "state.txt")


def submit_task() -> str:
    """POST a bbox extract job and return the task UUID."""
    payload = json.dumps({
        "Name": "brussels-daily",
        "RegionType": "bbox",
        "RegionData": BBOX,
    }).encode()

    req = urllib.request.Request(
        SLICEOSM_API,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        uuid = resp.read().decode().strip()

    if not uuid or len(uuid) < 10:
        raise RuntimeError(f"Unexpected API response: {uuid!r}")

    print(f"Task submitted: {uuid}")
    return uuid


def wait_for_completion(uuid: str) -> dict:
    """Poll the task status until Complete or timeout."""
    url = f"{SLICEOSM_API}{uuid}"
    deadline = time.time() + POLL_TIMEOUT

    while time.time() < deadline:
        with urllib.request.urlopen(url, timeout=15) as resp:
            status = json.loads(resp.read())

        if status.get("Complete"):
            print(f"Extract complete – {status.get('SizeBytes', '?')} bytes, "
                  f"elapsed {status.get('Elapsed', '?')}")
            return status

        cells = status.get("CellsProg", "?")
        cells_total = status.get("CellsTotal", "?")
        print(f"  … progress: cells {cells}/{cells_total}")
        time.sleep(POLL_INTERVAL)

    raise TimeoutError(f"Task {uuid} did not complete within {POLL_TIMEOUT}s")


def download_pbf(uuid: str):
    """Download the resulting .osm.pbf file."""
    url = f"{SLICEOSM_FILES}{uuid}.osm.pbf"
    os.makedirs(HISTORY_DIR, exist_ok=True)

    print(f"Downloading {url} …")
    urllib.request.urlretrieve(url, OUTPUT_PBF)
    size_mb = os.path.getsize(OUTPUT_PBF) / (1024 * 1024)
    print(f"Saved {OUTPUT_PBF} ({size_mb:.1f} MB)")


def write_state(uuid: str, status: dict):
    """Write a small state file with timestamp and metadata."""
    info = {
        "uuid": uuid,
        "timestamp": status.get("Timestamp", ""),
        "size_bytes": status.get("SizeBytes", ""),
        "elapsed": status.get("Elapsed", ""),
        "bbox": BBOX,
    }
    with open(STATE_FILE, "w") as f:
        json.dump(info, f, indent=2)
    print(f"State written to {STATE_FILE}")


def main():
    uuid = submit_task()
    status = wait_for_completion(uuid)
    download_pbf(uuid)
    write_state(uuid, status)
    print("Done ✓")


if __name__ == "__main__":
    main()
