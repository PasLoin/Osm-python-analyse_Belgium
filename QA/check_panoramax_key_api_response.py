#### check if a panoramax key response of api is not empty (picture removed or not uploaded even UID is correct)

import requests
import time
import logging
import json
from typing import List

# Configuration
OVERPASS_URL = "http://overpass-api.de/api/interpreter"
PANORAMAX_API_URL = "https://api.panoramax.xyz/api/search?ids="
DELAY_SECONDS = 0.2  # délai entre les requêtes à l'API
LOG_FILE = "errors.log"
NO_HD_FILE = "no_hd_found.jsonl"

# Setup logging
logging.basicConfig(filename=LOG_FILE, level=logging.ERROR, format='%(asctime)s %(levelname)s: %(message)s')

def fetch_panoramax_ids() -> List[str]:
    """Effectue une requête Overpass pour récupérer les valeurs de la clé panoramax=*."""
    query = """
    [out:json];
    area["name"="Région de Bruxelles-Capitale - Brussels Hoofdstedelijk Gewest"]->.searchArea;
    (
      node["panoramax"](area.searchArea);
      way["panoramax"](area.searchArea);
      relation["panoramax"](area.searchArea);
    );
    out body;
    """
    response = requests.post(OVERPASS_URL, data={'data': query})
    response.raise_for_status()
    data = response.json()

    ids = []
    for element in data.get('elements', []):
        panoramax_id = element.get('tags', {}).get('panoramax')
        if panoramax_id:
            ids.append(panoramax_id)
    return ids

def query_panoramax_api(pano_ids: List[str]):
    """Interroge l'API Panoramax avec les IDs donnés et vérifie la présence d'une image HD."""
    with open(NO_HD_FILE, "w", encoding="utf-8") as outfile:
        for pano_id in pano_ids:
            url = f"{PANORAMAX_API_URL}{pano_id}"
            try:
                response = requests.get(url)
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
    print("Récupération des IDs Panoramax...")
    pano_ids = fetch_panoramax_ids()
    print(f"{len(pano_ids)} ID(s) récupéré(s). Lancement des requêtes API.")
    query_panoramax_api(pano_ids)

if __name__ == "__main__":
    main()
