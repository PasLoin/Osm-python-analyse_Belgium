#!/usr/bin/env python3
"""
1) Télécharge le GeoPackage UrbIS-Topo (région Bruxelles entière),
   extrait la couche TOPO_POINTS et génère un GeoJSON par valeur du champ
   DESCRFRE (reprojeté de Lambert 72 / EPSG:31370 vers WGS84 / EPSG:4326).

2) Compare TOPO_POINTS_Arbre_haute_tige.geojson et TOPO_POINTS_Banc.geojson
   avec les données déjà présentes dans OpenStreetMap (natural=tree et
   amenity=bench, extraits d'un PBF), en utilisant une distance configurable
   (défaut 1 m). Les points UrbIS sans correspondance OSM sont écrits dans
   candidate_tree.geojson et candidate_bench.geojson, ne contenant que le
   tag principal (natural=tree ou amenity=bench).

   Le PBF OSM est SYSTÉMATIQUEMENT re-téléchargé à chaque exécution pour
   garantir des données fraîches.

Dépendances :
    pip install geopandas fiona pyproj requests lxml osmium shapely rtree
"""

import os
import re
import sys
import zipfile
import tempfile
from pathlib import Path
from xml.etree import ElementTree as ET

import requests
import geopandas as gpd
import osmium
from shapely.geometry import Point

# ── Configuration UrbIS ──────────────────────────────────────────────
ATOM_FEED_URL = (
    "https://urbisdownload.datastore.brussels/atomfeed/"
    "10ded91e-6a63-11ed-9d77-010101010000-en.xml"
)
OUTPUT_DIR = Path("geojson_topo_points")
LAYER_NAME = "Topo_points"
FIELD_NAME = "DESCRFRE"
SOURCE_CRS = "EPSG:31370"   # Belgian Lambert 72
TARGET_CRS = "EPSG:4326"    # WGS84 (lat/lon) — compatible OpenStreetMap
METRIC_CRS = "EPSG:31370"   # Pour les calculs de distance (en mètres)

# ── Configuration OSM ────────────────────────────────────────────────
OSM_PBF_URL = (
    "https://raw.githubusercontent.com/PasLoin/Osm-python-analyse_Belgium/"
    "main/pbf_analyse/history/Brussels-daily.pbf"
)
CANDIDATES_DIR = Path("candidates")
DEFAULT_DISTANCE_M = 1.0


# ─────────────────────────────────────────────────────────────────────
# Partie 1 : Téléchargement et extraction UrbIS
# ─────────────────────────────────────────────────────────────────────

def fetch_gpkg_url_from_feed(feed_url: str) -> str:
    """Parse le flux Atom et renvoie l'URL du GPKG le plus récent."""
    print("Téléchargement du flux Atom…")
    resp = requests.get(feed_url, timeout=60)
    resp.raise_for_status()

    root = ET.fromstring(resp.content)
    REGION_CODE = "04000"  # Région Bruxelles entière

    gpkg_links: list[tuple[str, str]] = []
    for link in root.iter("{http://www.w3.org/2005/Atom}link"):
        href = link.get("href", "")
        if "/GPKG/" in href and href.endswith(".zip") and f"_{REGION_CODE}_" in href:
            m = re.search(r"_(\d{8})\.zip$", href)
            date_str = m.group(1) if m else "00000000"
            gpkg_links.append((date_str, href))

    if not gpkg_links:
        print("Aucun lien GPKG région trouvé dans le flux, tentative avec l'URL directe…")
        import datetime
        base = "https://urbisdownload.datastore.brussels/UrbIS/Vector/M8/UrbIS-TOPO/GPKG/"
        today = datetime.date.today()
        for delta in range(0, 120):
            d = today - datetime.timedelta(days=delta)
            url = f"{base}UrbISTopo_31370_GPKG_{REGION_CODE}_{d.strftime('%Y%m%d')}.zip"
            r = requests.head(url, timeout=10, allow_redirects=True)
            if r.status_code == 200:
                print(f"  → Trouvé : {url}")
                return url
        sys.exit("Impossible de trouver le fichier GPKG région.")

    gpkg_links.sort(key=lambda x: x[0], reverse=True)
    url = gpkg_links[0][1]
    print(f"  → GPKG région entière le plus récent : {url}")
    return url


def download_and_extract_gpkg(url: str, work_dir: str) -> str:
    """Télécharge le ZIP et extrait le fichier .gpkg."""
    zip_path = os.path.join(work_dir, "urbis_topo.zip")
    print(f"Téléchargement du GeoPackage ({url})…")
    with requests.get(url, stream=True, timeout=600) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        downloaded = 0
        with open(zip_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded * 100 // total
                    print(f"\r  {downloaded // (1 << 20)} Mo / {total // (1 << 20)} Mo ({pct}%)",
                          end="", flush=True)
    print()

    print("Extraction du ZIP…")
    with zipfile.ZipFile(zip_path) as zf:
        gpkg_files = [n for n in zf.namelist() if n.lower().endswith(".gpkg")]
        if not gpkg_files:
            sys.exit("Aucun fichier .gpkg trouvé dans l'archive.")
        zf.extractall(work_dir, gpkg_files)
        gpkg_path = os.path.join(work_dir, gpkg_files[0])
        print(f"  → {gpkg_files[0]}")
    return gpkg_path


def list_layers(gpkg_path: str):
    import fiona
    layers = fiona.listlayers(gpkg_path)
    print(f"\nCouches disponibles ({len(layers)}) :")
    for l in layers:
        print(f"  • {l}")
    return layers


def process_topo_points(gpkg_path: str, layer_name: str):
    """Lit la couche points, reprojette et exporte un GeoJSON par DESCRFRE."""
    print(f"\nLecture de la couche '{layer_name}'…")
    gdf = gpd.read_file(gpkg_path, layer=layer_name)
    print(f"  {len(gdf)} entités chargées")
    print(f"  CRS source : {gdf.crs}")

    if FIELD_NAME not in gdf.columns:
        print(f"\n⚠ Le champ '{FIELD_NAME}' n'existe pas. Champs disponibles :")
        for c in gdf.columns:
            print(f"    {c}")
        sys.exit(1)

    print(f"\nReprojection {SOURCE_CRS} → {TARGET_CRS}…")
    gdf = gdf.to_crs(TARGET_CRS)

    OUTPUT_DIR.mkdir(exist_ok=True)

    categories = gdf[FIELD_NAME].dropna().unique()
    print(f"\n{len(categories)} catégories DESCRFRE trouvées :")

    for cat in sorted(categories):
        subset = gdf[gdf[FIELD_NAME] == cat].copy()
        safe_name = re.sub(r'[^\w\s-]', '', str(cat)).strip()
        safe_name = re.sub(r'[\s]+', '_', safe_name)
        if not safe_name:
            safe_name = "sans_nom"
        filename = f"TOPO_POINTS_{safe_name}.geojson"
        filepath = OUTPUT_DIR / filename
        if filepath.exists():
            filepath.unlink()
        subset.to_file(filepath, driver="GeoJSON")
        print(f"  ✓ {filename:60s} ({len(subset):>5d} points)")

    print(f"\nTerminé ! {len(categories)} fichiers GeoJSON créés dans ./{OUTPUT_DIR}/")


# ─────────────────────────────────────────────────────────────────────
# Partie 2 : Comparaison avec OpenStreetMap
# ─────────────────────────────────────────────────────────────────────

class TagNodeHandler(osmium.SimpleHandler):
    """Collecte les nœuds OSM correspondant à des paires (key, value) données."""

    def __init__(self, filters):
        super().__init__()
        self.filters = filters
        self.results = {f: [] for f in filters}

    def node(self, n):
        if not n.location.valid():
            return
        tags = n.tags
        for f in self.filters:
            key, value = f
            if key in tags and tags[key] == value:
                self.results[f].append({
                    "osm_id": n.id,
                    "lon": n.location.lon,
                    "lat": n.location.lat,
                })


def download_pbf(url: str, dest_path: Path):
    """Télécharge SYSTÉMATIQUEMENT le PBF OSM (écrase la version locale)."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if dest_path.exists():
        print(f"Suppression de la version locale existante : {dest_path}")
        dest_path.unlink()
    print(f"Téléchargement du PBF OSM ({url})…")
    with requests.get(url, stream=True, timeout=600) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        downloaded = 0
        with open(dest_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded * 100 // total
                    print(f"\r  {downloaded // (1 << 20)} Mo / {total // (1 << 20)} Mo ({pct}%)",
                          end="", flush=True)
    print()


def extract_osm_nodes(pbf_path: Path, filters):
    """Extrait les nœuds OSM correspondant aux filtres (key, value)."""
    print("\nExtraction des nœuds OSM…")
    h = TagNodeHandler(filters)
    h.apply_file(str(pbf_path))
    gdfs = {}
    for f, pts in h.results.items():
        if pts:
            gdf = gpd.GeoDataFrame(
                pts,
                geometry=[Point(p["lon"], p["lat"]) for p in pts],
                crs=TARGET_CRS,
            )
        else:
            gdf = gpd.GeoDataFrame(geometry=[], crs=TARGET_CRS)
        print(f"  {f[0]}={f[1]} : {len(gdf)} nœuds OSM")
        gdfs[f] = gdf
    return gdfs


def find_candidates(urbis_geojson: Path, osm_gdf: gpd.GeoDataFrame,
                    distance_m: float, main_tag: tuple, output_path: Path):
    """
    Trouve les points UrbIS sans correspondance OSM dans `distance_m` mètres,
    et écrit un GeoJSON ne contenant que le tag principal.
    """
    if not urbis_geojson.exists():
        print(f"\n⚠ {urbis_geojson} introuvable, étape ignorée.")
        return

    print(f"\nAnalyse de {urbis_geojson.name}…")
    urbis = gpd.read_file(urbis_geojson)
    print(f"  {len(urbis)} points UrbIS chargés")
    print(f"  {len(osm_gdf)} points OSM ({main_tag[0]}={main_tag[1]})")

    key, value = main_tag

    if len(urbis) == 0:
        candidates_wgs = gpd.GeoDataFrame(geometry=[], crs=TARGET_CRS)
    else:
        # Reprojection en CRS métrique pour le calcul de distance
        urbis_m = urbis.to_crs(METRIC_CRS).reset_index(drop=True)

        if len(osm_gdf) == 0:
            # Aucun point OSM : tous les points UrbIS sont candidats
            candidates_m = urbis_m
        else:
            osm_m = osm_gdf.to_crs(METRIC_CRS).reset_index(drop=True)
            joined = gpd.sjoin_nearest(
                urbis_m, osm_m,
                how="left",
                max_distance=distance_m,
                distance_col="dist_m",
            )
            # Indices UrbIS qui ont trouvé une correspondance OSM
            matched_idx = set(joined.loc[joined["index_right"].notna()].index)
            candidates_m = urbis_m.loc[~urbis_m.index.isin(matched_idx)]

        candidates_wgs = candidates_m.to_crs(TARGET_CRS)

    # Sortie propre : on garde uniquement le tag principal + la géométrie
    n = len(candidates_wgs)
    candidates_out = gpd.GeoDataFrame(
        {key: [value] * n},
        geometry=candidates_wgs.geometry.values if n else [],
        crs=TARGET_CRS,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()
    candidates_out.to_file(output_path, driver="GeoJSON")
    print(f"  → {n} candidat(s) absent(s) d'OSM (seuil < {distance_m} m)")
    print(f"     écrits dans {output_path}")


def prompt_distance() -> float:
    raw = input(f"\nDistance de référence en mètres [défaut {DEFAULT_DISTANCE_M}] : ").strip()
    if not raw:
        return DEFAULT_DISTANCE_M
    try:
        d = float(raw.replace(",", "."))
        if d <= 0:
            raise ValueError
        return d
    except ValueError:
        print(f"  Valeur invalide, utilisation de {DEFAULT_DISTANCE_M} m")
        return DEFAULT_DISTANCE_M


def compare_with_osm():
    distance_m = prompt_distance()
    print(f"Distance utilisée : {distance_m} m")

    CANDIDATES_DIR.mkdir(exist_ok=True)
    pbf_path = CANDIDATES_DIR / "Brussels-daily.pbf"
    download_pbf(OSM_PBF_URL, pbf_path)

    filters = [("natural", "tree"), ("amenity", "bench")]
    osm_data = extract_osm_nodes(pbf_path, filters)

    find_candidates(
        urbis_geojson=OUTPUT_DIR / "TOPO_POINTS_Arbre_haute_tige.geojson",
        osm_gdf=osm_data[("natural", "tree")],
        distance_m=distance_m,
        main_tag=("natural", "tree"),
        output_path=CANDIDATES_DIR / "candidate_tree.geojson",
    )

    find_candidates(
        urbis_geojson=OUTPUT_DIR / "TOPO_POINTS_Banc.geojson",
        osm_gdf=osm_data[("amenity", "bench")],
        distance_m=distance_m,
        main_tag=("amenity", "bench"),
        output_path=CANDIDATES_DIR / "candidate_bench.geojson",
    )

    print(f"\n{'='*60}")
    print(f"Comparaison terminée. Fichiers dans ./{CANDIDATES_DIR}/")


# ─────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────

def main():
    needed = [
        OUTPUT_DIR / "TOPO_POINTS_Arbre_haute_tige.geojson",
        OUTPUT_DIR / "TOPO_POINTS_Banc.geojson",
    ]

    if not all(p.exists() for p in needed):
        # Étape 1 : extraction UrbIS
        with tempfile.TemporaryDirectory(prefix="urbis_") as tmpdir:
            gpkg_url = fetch_gpkg_url_from_feed(ATOM_FEED_URL)
            gpkg_path = download_and_extract_gpkg(gpkg_url, tmpdir)
            layers = list_layers(gpkg_path)
            layer_map = {l.lower(): l for l in layers}
            actual_layer = layer_map.get(LAYER_NAME.lower())
            if actual_layer is None:
                print(f"\n⚠ La couche '{LAYER_NAME}' n'existe pas dans ce GeoPackage.")
                sys.exit(1)
            process_topo_points(gpkg_path, actual_layer)
    else:
        print(f"Fichiers UrbIS déjà présents dans ./{OUTPUT_DIR}/ — étape 1 sautée.")

    # Étape 2 : comparaison avec OSM
    compare_with_osm()


if __name__ == "__main__":
    main()
