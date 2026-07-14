import requests
import time
import logging
import json
import os
from typing import List
import osmium

PBF_URL = "https://raw.githubusercontent.com/PasLoin/Osm-python-analyse_Belgium/main/pbf_analyse/history/Brussels-daily.pbf"
PBF_FILE = "Brussels-daily.pbf"
PANORAMAX_API_URL = "https://api.panoramax.xyz/api/search?ids="
DELAY_SECONDS = 0.2
LOG_FILE = "errors.log"
NO_HD_FILE = "no_hd_found.jsonl"

HEADERS = {
    "User-Agent": "panoramax-checker/1.0",
}

logging.basicConfig(filename=LOG_FILE, level=logging.ERROR, format='%(asctime)s %(levelname)s: %(message)s')


class PanoramaxHandler(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.ids = set()

    def node(self, n):
        self._check(n.tags)

    def way(self, w):
        self._check(w.tags)

    def relation(self, r):
        self._check(r.tags)

    def _check(self, tags):
        if "panoramax" in tags:
            for value in tags["panoramax"].split(";"):
                value = value.strip()
                if value:
                    self.ids.add(value)


def download_pbf():
    if os.path.exists(PBF_FILE):
        return
    response = requests.get(PBF_URL, headers=HEADERS, timeout=120)
    response.raise_for_status()
    with open(PBF_FILE, "wb") as f:
        f.write(response.content)


def fetch_panoramax_ids() -> List[str]:
    download_pbf()
    handler = PanoramaxHandler()
    handler.apply_file(PBF_FILE)
    return list(handler.ids)


def query_panoramax_api(pano_ids: List[str]):
    with open(NO_HD_FILE, "w", encoding="utf-8") as outfile:
        for pano_id in pano_ids:
            url = f"{PANORAMAX_API_URL}{pano_id}"
            try:
                response = requests.get(url, headers=HEADERS, timeout=30)
                if not response.ok:
                    logging.error(f"Erreur HTTP {response.status_code} pour l'ID {pano_id}: {response.text}")
                    outfile.write(json.dumps({
                        "id": pano_id,
                        "status": response.status_code,
                        "reason": "HTTP error"
                    }) + "\n")
                else:
                    data = response.json()
                    features = data.get("features", [])
                    if not features:
                        print(f"⚠️ {pano_id} : aucune feature trouvée.")
                        outfile.write(json.dumps({
                            "id": pano_id,
                            "status": 200,
                            "reason": "no features"
                        }) + "\n")
                    else:
                        assets = features[0].get("assets", {})
                        if "hd" in assets and "href" in assets["hd"]:
                            print(f"✅ {pano_id} : image HD trouvée.")
                        else:
                            print(f"❌ {pano_id} : pas d'image HD.")
                            outfile.write(json.dumps({
                                "id": pano_id,
                                "status": 200,
                                "reason": "no hd image",
                                "assets": assets
                            }) + "\n")
            except Exception as e:
                logging.error(f"Exception pour l'ID {pano_id}: {str(e)}")
                outfile.write(json.dumps({
                    "id": pano_id,
                    "status": "exception",
                    "reason": str(e)
                }) + "\n")
            time.sleep(DELAY_SECONDS)


def main():
    print("Récupération des IDs Panoramax depuis le PBF...")
    pano_ids = fetch_panoramax_ids()
    print(f"{len(pano_ids)} ID(s) récupéré(s). Lancement des requêtes API.")
    query_panoramax_api(pano_ids)


if __name__ == "__main__":
    main()
