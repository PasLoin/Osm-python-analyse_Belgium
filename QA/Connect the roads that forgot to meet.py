#!/usr/bin/env python3
import osmium
import geopandas as gpd
import pandas as pd
from shapely.geometry import LineString, Point, Polygon, MultiPolygon
from shapely.ops import transform as shapely_transform
from functools import partial
from pyproj import Transformer
import json
import urllib.request

PBF_FILE = "input.pbf"
OUTPUT_FILE = "missing_nodes.geojson"          # MapRoulette (line-delimited)
OUTPUT_FILE_JOSM = "missing_nodes_josm.geojson"  # JOSM (valid GeoJSON)
POLY_URL = "https://polygons.openstreetmap.fr/get_poly.py?id=52411"

# =========================
# STOCKAGE
# =========================
ways = []
node_locations = {}

# =========================
# NODE HANDLER
# =========================
class NodeHandler(osmium.SimpleHandler):
    def node(self, n):
        try:
            node_locations[n.id] = (n.location.lon, n.location.lat)
        except Exception:
            pass

# =========================
# WAY HANDLER
# =========================
class WayHandler(osmium.SimpleHandler):
    def way(self, w):
        if "highway" not in w.tags:
            return
        if w.tags.get("highway") in ("no", "proposed", "platform", "pedestrian", "construction", "services", "rest_area", "razed"):
            return
        if "level" in w.tags:
            return
        if w.tags.get("area") == "yes":
            return
        try:
            coords = []
            node_refs = []
            for n in w.nodes:
                if n.ref not in node_locations:
                    return
                coords.append(node_locations[n.ref])
                node_refs.append(n.ref)
            if len(coords) < 2:
                return
            geom = LineString(coords)
            ways.append({
                "id": w.id,
                "highway": w.tags.get("highway"),
                "bridge": w.tags.get("bridge", "no") or "no",
                "tunnel": w.tags.get("tunnel", "no") or "no",
                "layer": w.tags.get("layer", "0") or "0",
                "node_refs": node_refs,
                "nodes": set(node_refs),
                "geometry": geom,
            })
        except Exception:
            pass

# =========================
# LOAD NODES
# =========================
print("Loading nodes...")
nh = NodeHandler()
nh.apply_file(PBF_FILE)
print(f"Nodes loaded: {len(node_locations)}")

# =========================
# LOAD WAYS
# =========================
print("Loading highways...")
wh = WayHandler()
wh.apply_file(PBF_FILE)
print(f"Ways loaded: {len(ways)}")

# =========================
# GEODATAFRAME
# =========================
gdf = gpd.GeoDataFrame(ways, geometry="geometry", crs="EPSG:4326")

# Sécuriser les NaN éventuels
gdf["bridge"] = gdf["bridge"].fillna("no")
gdf["tunnel"] = gdf["tunnel"].fillna("no")
gdf["layer"] = gdf["layer"].fillna("0")

# Projection locale belge pour les calculs géométriques
gdf = gdf.to_crs(31370)

# Précalculer les coordonnées des nœuds en projeté (pour le check précis)
transformer = Transformer.from_crs("EPSG:4326", "EPSG:31370", always_xy=True)

node_locations_proj = {}
for nid, (lon, lat) in node_locations.items():
    x, y = transformer.transform(lon, lat)
    node_locations_proj[nid] = (x, y)

# =========================
# SPATIAL INDEX
# =========================
sindex = gdf.sindex
results = []
print("Searching crossings...")

# =========================
# DETECTION
# =========================
for idx, row in gdf.iterrows():
    geom1 = row.geometry
    candidates = list(sindex.intersection(geom1.bounds))

    for j in candidates:
        if idx >= j:
            continue

        other = gdf.iloc[j]

        # Skip même objet
        if row["id"] == other["id"]:
            continue

        # Skip si bridge ou tunnel (pas sur le même plan physique)
        if row["bridge"] not in ("", "no"):
            continue
        if other["bridge"] not in ("", "no"):
            continue
        if row["tunnel"] not in ("", "no"):
            continue
        if other["tunnel"] not in ("", "no"):
            continue

        # Même layer uniquement
        if str(row["layer"]) != str(other["layer"]):
            continue

        geom2 = other.geometry

        # Intersection géométrique
        if not geom1.intersects(geom2):
            continue

        inter = geom1.intersection(geom2)
        if inter.is_empty:
            continue

        # On ne traite que les intersections ponctuelles
        if inter.geom_type == "Point":
            points = [inter]
        elif inter.geom_type == "MultiPoint":
            points = list(inter.geoms)
        else:
            # Lignes superposées, on ignore
            continue

        # Pour chaque point d'intersection, vérifier
        # s'il y a un nœud partagé À CET ENDROIT
        shared_nodes = row["nodes"].intersection(other["nodes"])
        TOLERANCE = 0.5  # mètres en EPSG:31370

        for pt in points:
            node_at_intersection = False
            for nid in shared_nodes:
                if nid in node_locations_proj:
                    nx, ny = node_locations_proj[nid]
                    if pt.distance(Point(nx, ny)) < TOLERANCE:
                        node_at_intersection = True
                        break

            if not node_at_intersection:
                results.append({
                    "way1": row["id"],
                    "way2": other["id"],
                    "highway1": row["highway"],
                    "highway2": other["highway"],
                    "layer": row["layer"],
                    "geometry": pt,
                    "geom_way1": geom1,
                    "geom_way2": geom2,
                })

print(f"Potential issues: {len(results)}")

# =========================
# FILTRE PAR ZONE (poly OSM)
# =========================
def parse_poly(text):
    """Parse le format .poly d'OSM en liste de Polygon."""
    polygons = []
    lines = text.strip().splitlines()
    i = 1  # skip le nom global
    while i < len(lines):
        line = lines[i].strip()
        if line == "END":
            break
        is_hole = line.startswith("!")
        i += 1
        coords = []
        while i < len(lines) and lines[i].strip() != "END":
            parts = lines[i].strip().split()
            coords.append((float(parts[0]), float(parts[1])))
            i += 1
        i += 1  # skip END du ring
        if coords and not is_hole:
            polygons.append(Polygon(coords))
    if len(polygons) == 1:
        return polygons[0]
    return MultiPolygon(polygons)

print("Downloading boundary polygon...")
with urllib.request.urlopen(POLY_URL) as resp:
    poly_text = resp.read().decode("utf-8")

boundary_wgs = parse_poly(poly_text)

# Projeter en EPSG:31370 (même CRS que les résultats)
to_31370 = Transformer.from_crs("EPSG:4326", "EPSG:31370", always_xy=True)
boundary = shapely_transform(partial(to_31370.transform), boundary_wgs)

before = len(results)
results = [r for r in results if boundary.contains(r["geometry"])]
print(f"After boundary filter: {before} → {len(results)}")

# =========================
# EXPORT
# =========================
if len(results) == 0:
    print("No issues found.")
    exit()

# Transformer projeté → WGS84
to_wgs84 = Transformer.from_crs("EPSG:31370", "EPSG:4326", always_xy=True)

def project_to_wgs(geom):
    return shapely_transform(partial(to_wgs84.transform), geom)

all_josm_features = []

with open(OUTPUT_FILE, "w") as f:
    for r in results:
        pt_wgs = project_to_wgs(r["geometry"])
        way1_wgs = project_to_wgs(r["geom_way1"])
        way2_wgs = project_to_wgs(r["geom_way2"])

        props = {
            "way1": int(r["way1"]),
            "way2": int(r["way2"]),
            "highway1": r["highway1"],
            "highway2": r["highway2"],
            "layer": r["layer"],
        }

        feat_point = {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": list(pt_wgs.coords[0]),
            },
            "properties": {
                **props,
                "role": "intersection",
                "marker-color": "#e74c3c",
            },
        }
        feat_way1 = {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [list(c) for c in way1_wgs.coords],
            },
            "properties": {
                "osm_way_id": int(r["way1"]),
                "highway": r["highway1"],
                "role": "way",
                "stroke": "#2980b9",
                "stroke-width": 4,
                "stroke-opacity": 0.9,
            },
        }
        feat_way2 = {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                "coordinates": [list(c) for c in way2_wgs.coords],
            },
            "properties": {
                "osm_way_id": int(r["way2"]),
                "highway": r["highway2"],
                "role": "way",
                "stroke": "#e67e22",
                "stroke-width": 4,
                "stroke-opacity": 0.9,
            },
        }

        # MapRoulette : une FeatureCollection par ligne
        task = {
            "type": "FeatureCollection",
            "features": [feat_point, feat_way1, feat_way2],
        }
        f.write(json.dumps(task) + "\n")

        # JOSM : on accumule toutes les features
        all_josm_features.extend([feat_point, feat_way1, feat_way2])

print(f"Saved: {OUTPUT_FILE} ({len(results)} tasks)")

# JOSM : un seul FeatureCollection valide
with open(OUTPUT_FILE_JOSM, "w") as f:
    json.dump({
        "type": "FeatureCollection",
        "features": all_josm_features,
    }, f)

print(f"Saved: {OUTPUT_FILE_JOSM} ({len(all_josm_features)} features)")
