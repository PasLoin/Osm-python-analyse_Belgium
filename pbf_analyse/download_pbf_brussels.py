#!/usr/bin/python3
import os
from datetime import datetime, timezone

import requests

URL = "https://download.openstreetmap.fr/extracts/europe/belgium/brussels_capital_region-latest.osm.pbf"
HISTORY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "history")


def main():
    os.makedirs(HISTORY_DIR, exist_ok=True)
    current_date = datetime.now(timezone.utc).strftime("%d_%m_%Y")
    filename = os.path.join(HISTORY_DIR, f"{current_date}_brussels_capital_region.pbf")
    tmp = filename + ".part"

    try:
        with requests.get(URL, stream=True, timeout=(30, 300)) as r:
            r.raise_for_status()
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    f.write(chunk)

        with open(tmp, "rb") as f:
            if b"OSMHeader" not in f.read(64):
                raise RuntimeError(f"{URL} did not return an OSM PBF file")

        os.replace(tmp, filename)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

    print(f"{filename} ({os.path.getsize(filename)} bytes)")


if __name__ == "__main__":
    main()
