#!/usr/bin/env python3
"""
Comparaison des bulles à verre bruxelloises
  • OpenData Brussels  — opendata.bruxelles.be (CC BY 4.0)
  • OpenStreetMap      — Brussels-daily.pbf

Fichiers produits (dans le même dossier que ce script) :
  report_glass_bins.txt          — rapport lisible
  report_glass_bins.json         — rapport structuré
  missing_in_osm.geojson         — bulles OpenData sans aucun nœud OSM
                                    à proximité, tags OSM prêts à l'emploi
  missing_in_opendata.geojson    — nœuds OSM sans aucun point OpenData
                                    à proximité, tags OSM existants

Variable d'environnement optionnelle :
  MATCH_THRESHOLD_M  (défaut : 50)  seuil de proximité en mètres

── Note de conception (appariement) ──────────────────────────────────────────
La classification "manquant" est faite par PRÉSENCE, pas par appariement
exclusif 1-à-1 : pour chaque point OpenData, on cherche s'il existe AU MOINS
UN nœud OSM (verre) dans le rayon, et vice-versa. Plusieurs points OpenData
(ex. bulle "couleur" et bulle "blanche" du même site) peuvent légitimement
pointer vers le même nœud OSM : OSM ne mappe parfois qu'un seul point pour
un site qui contient plusieurs bulles physiques.
Une exclusivité 1-à-1 stricte produit de faux "missing" dès que deux points
OpenData proches partagent un même nœud OSM. C'est corrigé ici.

La recherche du plus proche voisin utilise un index spatial (KDTree, via
scipy.spatial.cKDTree) sur des coordonnées projetées en mètres
(approximation équirectangulaire, valide à l'échelle d'une ville).
"""

import json
import math
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import osmium
import requests
import numpy as np
from scipy.spatial import cKDTree

# ── Chemins ────────────────────────────────────────────────────────────────────
SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT    = os.path.join(SCRIPT_DIR, "..")
OSM_PBF_PATH = os.path.join(REPO_ROOT, "pbf_analyse", "history", "Brussels-daily.pbf")
OUTPUT_DIR   = SCRIPT_DIR

# ── OpenData ───────────────────────────────────────────────────────────────────
OPENDATA_GEOJSON = (
    "https://opendata.bruxelles.be/api/explore/v2.1/catalog/datasets/"
    "bulles-a-verre-glasbollen/exports/geojson"
    "?lang=fr&timezone=Europe%2FBerlin"
)

# ── Paramètres ─────────────────────────────────────────────────────────────────
MATCH_THRESHOLD_M = float(os.environ.get("MATCH_THRESHOLD_M", "50"))
DEDUP_RADIUS_M     = 2.0   # rayon de déduplication des doublons exacts OpenData

# Tags OBLIGATOIRES pour un conteneur correctement tagué dans OSM
REQUIRED_TAGS: dict[str, str] = {
    "amenity":                 "recycling",
    "recycling:glass_bottles": "yes",
    "recycling_type":          "container",
}

# Tags opérateur attendus (optionnels mais souhaitables)
EXPECTED_OPERATORS: dict[str, str] = {
    "operator":          "Bruxelles-Propreté - Net Brussel",
    "operator:fr":       "Bruxelles-Propreté",
    "operator:nl":       "Net Brussel",
    "operator:wikidata": "Q23021854",
}

VALID_LOCATIONS = {"underground", "overground"}

# Tags OSM à appliquer sur les bulles à créer dans OSM
OSM_TAGS_TEMPLATE: dict[str, str] = {
    "amenity":                 "recycling",
    "recycling:glass_bottles": "yes",
    "recycling_type":          "container",
    "operator":                "Bruxelles-Propreté - Net Brussel",
    "operator:fr":             "Bruxelles-Propreté",
    "operator:nl":             "Net Brussel",
    "operator:wikidata":       "Q23021854",
}


# ── Structures de données ──────────────────────────────────────────────────────
@dataclass
class ODPoint:
    uid:          str
    lat:          float
    lon:          float
    address:      str
    municipality: str
    postalcode:   str
    category:     str
    # Nœud OSM le plus proche (non-exclusif : plusieurs OD peuvent partager le même)
    nearest_osm_id:   Optional[int]   = None
    nearest_osm_type: Optional[str]   = None
    nearest_osm_dist: Optional[float] = None


@dataclass
class OSMPoint:
    osm_id:   int
    osm_type: str
    lat:      float
    lon:      float
    tags:     dict = field(default_factory=dict)
    # Point OpenData le plus proche (non-exclusif)
    nearest_od_uid:  Optional[str]   = None
    nearest_od_dist: Optional[float] = None


# ── Projection équirectangulaire (mètres) ──────────────────────────────────────
def make_projector(ref_lat: float):
    """
    Projection plane locale simple, valide pour de petites étendues
    (échelle d'une ville). Erreur négligeable sur quelques km.
    """
    m_per_deg_lat = 110_540.0
    m_per_deg_lon = 111_320.0 * math.cos(math.radians(ref_lat))

    def project(lat: float, lon: float) -> tuple[float, float]:
        return (lon * m_per_deg_lon, lat * m_per_deg_lat)

    return project


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Conservé pour la déduplication (précision exacte, peu d'appels)."""
    R  = 6_371_000
    φ1 = math.radians(lat1)
    φ2 = math.radians(lat2)
    dφ = math.radians(lat2 - lat1)
    dλ = math.radians(lon2 - lon1)
    a  = math.sin(dφ / 2) ** 2 + math.cos(φ1) * math.cos(φ2) * math.sin(dλ / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ── Détection du tag location ──────────────────────────────────────────────────
def detect_location(category: str) -> Optional[str]:
    c = (category.lower()
         .replace("é", "e").replace("è", "e").replace("ê", "e")
         .replace("à", "a").replace("â", "a"))
    if "aerien" in c:
        return "overground"
    if "enterr" in c or "souterr" in c or "underground" in c:
        return "underground"
    return None


# ── Déduplication OpenData (vrais doublons exacts uniquement) ─────────────────
def deduplicate_opendata(pts: list[ODPoint], radius_m: float = DEDUP_RADIUS_M) -> list[ODPoint]:
    """
    Supprime uniquement les entrées OpenData quasi-identiques en coordonnées
    (même conteneur physique décrit deux fois, ex. "couleur" + "blanche" à
    la même position exacte). Ne touche pas aux bulles simplement voisines.
    """
    kept: list[ODPoint] = []
    absorbed: set[str] = set()

    for i, a in enumerate(pts):
        if a.uid in absorbed:
            continue
        kept.append(a)
        for b in pts[i + 1:]:
            if b.uid in absorbed:
                continue
            if haversine_m(a.lat, a.lon, b.lat, b.lon) <= radius_m:
                absorbed.add(b.uid)

    removed = len(pts) - len(kept)
    if removed:
        print(f"  -> {removed} doublon(s) OpenData supprimes "
              f"(meme emplacement a moins de {radius_m} m)")
    return kept


# ── 1. Chargement OpenData ─────────────────────────────────────────────────────
def fetch_opendata() -> list[ODPoint]:
    print("OpenData Brussels - telechargement du GeoJSON ...")
    try:
        r = requests.get(OPENDATA_GEOJSON, timeout=60)
        r.raise_for_status()
    except requests.RequestException as exc:
        sys.exit(f"Impossible de telecharger l'OpenData : {exc}")

    pts: list[ODPoint] = []
    for i, feat in enumerate(r.json().get("features", [])):
        geom  = feat.get("geometry") or {}
        props = feat.get("properties") or {}
        if geom.get("type") != "Point":
            continue
        lon, lat = geom["coordinates"]
        pts.append(ODPoint(
            uid          = str(i),
            lat          = float(lat),
            lon          = float(lon),
            address      = (props.get("address")         or "").strip(),
            municipality = (props.get("municipality_fr")
                            or props.get("municipality_nl") or "").strip(),
            postalcode   = str(props.get("postalcode", "")).strip(),
            category     = (props.get("category_fr")
                            or props.get("category_nl") or "").strip(),
        ))

    print(f"  -> {len(pts)} bulles chargees")
    return pts


# ── 2. Lecture du PBF OSM ──────────────────────────────────────────────────────
class GlassRecyclingHandler(osmium.SimpleHandler):
    """Collecte : amenity=recycling ET recycling:glass_bottles=yes."""

    def __init__(self):
        super().__init__()
        self.pts: list[OSMPoint] = []

    @staticmethod
    def _is_glass(tags: dict) -> bool:
        return (
            tags.get("amenity") == "recycling"
            and tags.get("recycling:glass_bottles") == "yes"
        )

    def node(self, n):
        tags = dict(n.tags)
        if self._is_glass(tags):
            self.pts.append(OSMPoint(
                osm_id   = n.id,
                osm_type = "node",
                lat      = n.location.lat,
                lon      = n.location.lon,
                tags     = tags,
            ))

    def way(self, w):
        tags = dict(w.tags)
        if not self._is_glass(tags):
            return
        try:
            valid = [(nd.lat, nd.lon) for nd in w.nodes if nd.location.valid()]
            if valid:
                lat = sum(p[0] for p in valid) / len(valid)
                lon = sum(p[1] for p in valid) / len(valid)
                self.pts.append(OSMPoint(
                    osm_id   = w.id,
                    osm_type = "way",
                    lat      = lat,
                    lon      = lon,
                    tags     = tags,
                ))
        except Exception as exc:
            print(f"  ! way/{w.id} ignore : {exc}")


def fetch_osm(pbf: str) -> list[OSMPoint]:
    pbf = os.path.realpath(pbf)
    if not os.path.exists(pbf):
        sys.exit(f"Fichier PBF introuvable : {pbf}\nVerifiez que le checkout inclut le LFS.")
    size_mb = os.path.getsize(pbf) / 1_048_576
    print(f"OSM PBF : {pbf}  ({size_mb:.1f} Mo)")
    handler = GlassRecyclingHandler()
    handler.apply_file(pbf, locations=True)
    print(f"  -> {len(handler.pts)} conteneurs verre trouves dans OSM")
    return handler.pts


# ── 3. Appariement par plus-proche-voisin (KDTree, non-exclusif) ──────────────
def spatial_match(od_list: list[ODPoint], osm_list: list[OSMPoint]) -> None:
    """
    Pour chaque point OpenData : cherche le nœud OSM le plus proche (KDTree).
    Pour chaque nœud OSM       : cherche le point OpenData le plus proche (KDTree).

    Les deux recherches sont indépendantes et NON exclusives : un même nœud
    OSM peut être "le plus proche" pour plusieurs points OpenData différents
    (cas réel d'un site avec plusieurs bulles physiques mais un seul nœud
    OSM). C'est la présence d'un voisin sous le seuil qui détermine
    "manquant", pas une réservation 1-à-1.
    """
    print(f"Appariement spatial par KDTree (seuil = {MATCH_THRESHOLD_M} m) ...")

    if not od_list or not osm_list:
        print("  -> liste vide, rien a apparier")
        return

    ref_lat   = sum(p.lat for p in osm_list) / len(osm_list)
    project   = make_projector(ref_lat)

    od_xy  = np.array([project(p.lat, p.lon) for p in od_list])
    osm_xy = np.array([project(p.lat, p.lon) for p in osm_list])

    osm_tree = cKDTree(osm_xy)
    od_tree  = cKDTree(od_xy)

    # OD -> nœud OSM le plus proche
    dists, idxs = osm_tree.query(od_xy, k=1)
    for od, d, idx in zip(od_list, dists, idxs):
        if d <= MATCH_THRESHOLD_M:
            osm = osm_list[idx]
            od.nearest_osm_id   = osm.osm_id
            od.nearest_osm_type = osm.osm_type
            od.nearest_osm_dist = round(float(d), 1)

    # OSM -> point OpenData le plus proche
    dists, idxs = od_tree.query(osm_xy, k=1)
    for osm, d, idx in zip(osm_list, dists, idxs):
        if d <= MATCH_THRESHOLD_M:
            od = od_list[idx]
            osm.nearest_od_uid  = od.uid
            osm.nearest_od_dist = round(float(d), 1)

    n_od_matched  = sum(1 for p in od_list  if p.nearest_osm_id  is not None)
    n_osm_matched = sum(1 for p in osm_list if p.nearest_od_uid  is not None)
    print(f"  -> {n_od_matched}/{len(od_list)} points OpenData ont un noeud OSM a proximite")
    print(f"  -> {n_osm_matched}/{len(osm_list)} noeuds OSM ont un point OpenData a proximite")


# ── 4. Évaluation de la qualité des tags ──────────────────────────────────────
def assess_tags(tags: dict, expected_location: Optional[str] = None) -> tuple[list[str], list[str]]:
    """
    expected_location : valeur 'underground'/'overground' déduite de la
    catégorie OpenData (bulle aerienne / bulle enterree), utilisée pour
    enrichir le message si le tag location est absent dans OSM.

    Les avertissements liés à l'opérateur sont placés en premier dans la
    liste retournée (plus prioritaires à corriger que le tag location).
    """
    errors:            list[str] = []
    operator_warnings: list[str] = []
    other_warnings:    list[str] = []

    for key, expected in REQUIRED_TAGS.items():
        actual = tags.get(key)
        if actual != expected:
            errors.append(f"{key}={expected!r}  ->  actuel : {actual!r}")

    for key, expected in EXPECTED_OPERATORS.items():
        actual = tags.get(key)
        if actual is None:
            operator_warnings.append(f"{key} absent (attendu : {expected!r})")
        elif actual != expected:
            operator_warnings.append(f"{key}={actual!r}  !=  {expected!r}")

    loc = tags.get("location")
    if loc is None:
        hint = f" — d'après l'OpenData, attendu : {expected_location!r}" if expected_location else ""
        other_warnings.append(f"location absent (attendu : 'underground' ou 'overground'){hint}")
    elif loc not in VALID_LOCATIONS:
        other_warnings.append(f"location={loc!r} — valeur inattendue")

    return errors, operator_warnings + other_warnings


# ── 5. GeoJSON ─────────────────────────────────────────────────────────────────
def _geojson(features: list) -> str:
    return json.dumps(
        {"type": "FeatureCollection", "features": features},
        ensure_ascii=False,
        indent=2,
    )


def geojson_missing_in_osm(pts: list[ODPoint]) -> str:
    """Tags OSM prêts à l'emploi pour les bulles à créer."""
    features = []
    for b in sorted(pts, key=lambda x: (x.postalcode, x.address)):
        location = detect_location(b.category)
        tags = {**OSM_TAGS_TEMPLATE}
        if location:
            tags["location"] = location
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point",
                         "coordinates": [round(b.lon, 7), round(b.lat, 7)]},
            "properties": tags,
        })
    return _geojson(features)


def geojson_missing_in_opendata(pts: list[OSMPoint]) -> str:
    """Tags OSM existants tels quels pour les nœuds sans point OpenData proche."""
    features = []
    for b in pts:
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point",
                         "coordinates": [round(b.lon, 7), round(b.lat, 7)]},
            "properties": dict(b.tags),
        })
    return _geojson(features)


def geojson_tag_issues(
    tag_results: list[tuple["ODPoint", "OSMPoint", list[str], list[str]]]
) -> str:
    """
    Nœuds OSM appariés dont les tags posent problème.

    Les propriétés sont directement les tags CORRIGÉS, prêts à coller sur
    le nœud existant dans JOSM/iD : on part des tags actuels du nœud (donc
    tout tag non lié au recyclage — poubelle, papier, vêtements, etc. —
    est conservé tel quel) et on corrige uniquement les clés en cause
    (amenity, recycling*, operator*, location).
    """
    features = []
    for od, osm, errs, warns in tag_results:
        if not errs and not warns:
            continue

        corrected = dict(osm.tags)          # tags existants, base de depart
        corrected.update(REQUIRED_TAGS)     # corrige amenity / recycling* si besoin
        corrected.update(EXPECTED_OPERATORS)  # corrige les tags operateur

        loc = detect_location(od.category)
        if loc:
            corrected["location"] = loc     # corrige/ajoute location si deductible

        features.append({
            "type": "Feature",
            "geometry": {"type": "Point",
                         "coordinates": [round(osm.lon, 7), round(osm.lat, 7)]},
            "properties": corrected,
        })
    return _geojson(features)


# ── 6. Rapport texte + JSON ────────────────────────────────────────────────────
def write_reports(od_list: list[ODPoint], osm_list: list[OSMPoint]) -> None:
    by_osm_id: dict[int, OSMPoint] = {p.osm_id: p for p in osm_list}

    missing_in_osm = [p for p in od_list  if p.nearest_osm_id is None]
    missing_in_od  = [p for p in osm_list if p.nearest_od_uid is None]
    matched        = [p for p in od_list  if p.nearest_osm_id is not None]

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    W   = 72
    SEP = "=" * W
    sep = "-" * W

    # Qualité des tags — un même nœud OSM peut apparaître pour plusieurs OD
    # (site avec plusieurs bulles physiques, un seul nœud OSM). C'est voulu.
    tag_results: list[tuple[ODPoint, OSMPoint, list[str], list[str]]] = []
    cnt_ok = cnt_warn = cnt_err = 0
    for od in matched:
        osm = by_osm_id[od.nearest_osm_id]
        errs, warns = assess_tags(osm.tags, expected_location=detect_location(od.category))
        if errs:
            cnt_err  += 1
        elif warns:
            cnt_warn += 1
        else:
            cnt_ok   += 1
        tag_results.append((od, osm, errs, warns))

    # ── Texte ──────────────────────────────────────────────────────────────────
    L: list[str] = [
        SEP,
        "  BULLES A VERRE -- OpenData Brussels <-> OpenStreetMap",
        SEP,
        f"  Genere le       : {now}",
        f"  Seuil spatial   : {MATCH_THRESHOLD_M} m",
        "",
        f"  OpenData              : {len(od_list):4d} bulles",
        f"  OSM                   : {len(osm_list):4d} conteneurs verre",
        f"  Avec voisin proche    : {len(matched):4d}",
        f"  Manquants in OSM      : {len(missing_in_osm):4d}  -> missing_in_osm.geojson",
        f"  Manquants in OpenData : {len(missing_in_od):4d}  -> missing_in_opendata.geojson",
        f"  Tags a corriger       : {cnt_warn + cnt_err:4d}  -> tag_issues.geojson",
        "",
        "  NB : plusieurs points OpenData peuvent partager le meme noeud OSM",
        "  le plus proche (site avec plusieurs bulles physiques, un seul noeud",
        "  OSM). Ce n'est pas une erreur de comptage.",
        "",
    ]

    # --- Section 1 : missing in OSM ---
    L += [
        sep,
        f"  1. MISSING IN OSM ({len(missing_in_osm)})",
        sep,
        f"  Aucun noeud OSM (verre) trouve dans un rayon de {MATCH_THRESHOLD_M} m.",
        "",
    ]
    for b in sorted(missing_in_osm, key=lambda x: (x.postalcode, x.address)):
        loc = detect_location(b.category)
        L += [
            f"  * [{b.postalcode} {b.municipality}]  {b.address}",
            f"    Categorie   : {b.category}  ->  location={loc or '?'}",
            f"    Coordonnees : {b.lat:.6f}, {b.lon:.6f}",
            f"    Carte OSM   : https://www.openstreetmap.org/"
            f"?mlat={b.lat}&mlon={b.lon}#map=19/{b.lat}/{b.lon}",
            "",
        ]

    # --- Section 2 : missing in OpenData ---
    L += [
        sep,
        f"  2. MISSING IN OPENDATA ({len(missing_in_od)})",
        sep,
        "  Noeuds OSM (recycling:glass_bottles=yes) sans point OpenData a proximite.",
        "",
    ]
    for b in missing_in_od:
        L += [
            f"  * {b.osm_type}/{b.osm_id}  ({b.lat:.6f}, {b.lon:.6f})",
            f"    Tags    : { {k: v for k, v in list(b.tags.items())[:10]} }",
            f"    URL OSM : https://www.openstreetmap.org/{b.osm_type}/{b.osm_id}",
            "",
        ]

    # --- Section 3 : tag quality ---
    L += [
        sep,
        f"  3. QUALITE DES TAGS OSM -- BULLES AVEC VOISIN PROCHE ({len(matched)})",
        sep,
        f"  OK : {cnt_ok}  |  Avertissements : {cnt_warn}  |  Erreurs : {cnt_err}",
        "",
    ]
    for od, osm, errs, warns in tag_results:
        if not errs and not warns:
            continue
        status = "ERREUR" if errs else "AVERT."
        L += [
            f"  [{status}] {osm.osm_type}/{osm.osm_id}  (dist = {od.nearest_osm_dist} m)",
            f"    OpenData : {od.address}, {od.postalcode} {od.municipality}",
            f"    OSM      : https://www.openstreetmap.org/{osm.osm_type}/{osm.osm_id}",
        ]
        if errs:
            L.append("    Tags requis incorrects :")
            L += [f"      x  {e}" for e in errs]
        if warns:
            L.append("    Avertissements :")
            L += [f"      ~  {w}" for w in warns]
        L.append("")

    txt = "\n".join(L)

    # ── JSON ───────────────────────────────────────────────────────────────────
    jdata = {
        "generated_at":      now,
        "match_threshold_m": MATCH_THRESHOLD_M,
        "stats": {
            "opendata_total":      len(od_list),
            "osm_total":           len(osm_list),
            "with_nearby_match":   len(matched),
            "missing_in_osm":      len(missing_in_osm),
            "missing_in_opendata": len(missing_in_od),
            "tag_ok":              cnt_ok,
            "tag_warn":            cnt_warn,
            "tag_err":             cnt_err,
        },
        "missing_in_osm": [
            {
                "lat":          b.lat,
                "lon":          b.lon,
                "address":      b.address,
                "municipality": b.municipality,
                "postalcode":   b.postalcode,
                "category":     b.category,
                "location_tag": detect_location(b.category),
                "osm_map_url":  (
                    f"https://www.openstreetmap.org/"
                    f"?mlat={b.lat}&mlon={b.lon}#map=19/{b.lat}/{b.lon}"
                ),
            }
            for b in sorted(missing_in_osm, key=lambda x: (x.postalcode, x.address))
        ],
        "missing_in_opendata": [
            {
                "osm_id":   b.osm_id,
                "osm_type": b.osm_type,
                "lat":      b.lat,
                "lon":      b.lon,
                "osm_url":  f"https://www.openstreetmap.org/{b.osm_type}/{b.osm_id}",
                "tags":     dict(b.tags),
            }
            for b in missing_in_od
        ],
        "tag_issues": [
            {
                "osm_id":       osm.osm_id,
                "osm_type":     osm.osm_type,
                "osm_url":      f"https://www.openstreetmap.org/{osm.osm_type}/{osm.osm_id}",
                "address":      od.address,
                "municipality": od.municipality,
                "postalcode":   od.postalcode,
                "distance_m":   od.nearest_osm_dist,
                "all_tags":     dict(osm.tags),
                "errors":       errs,
                "warnings":     warns,
            }
            for od, osm, errs, warns in tag_results
            if errs or warns
        ],
    }

    # ── Ecriture ───────────────────────────────────────────────────────────────
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    files: dict[str, str] = {
        "report_glass_bins.txt":        txt,
        "report_glass_bins.json":       json.dumps(jdata, ensure_ascii=False, indent=2),
        "missing_in_osm.geojson":       geojson_missing_in_osm(missing_in_osm),
        "missing_in_opendata.geojson":  geojson_missing_in_opendata(missing_in_od),
        "tag_issues.geojson":           geojson_tag_issues(tag_results),
    }

    print()
    print(txt)
    for fname, content in files.items():
        path = os.path.join(OUTPUT_DIR, fname)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        print(f"-> {path}")


# ── Point d'entrée ─────────────────────────────────────────────────────────────
def main() -> None:
    od_list  = fetch_opendata()
    od_list  = deduplicate_opendata(od_list)
    osm_list = fetch_osm(OSM_PBF_PATH)
    spatial_match(od_list, osm_list)
    write_reports(od_list, osm_list)


if __name__ == "__main__":
    main()
