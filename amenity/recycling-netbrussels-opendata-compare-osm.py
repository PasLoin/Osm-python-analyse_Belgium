#!/usr/bin/env python3
"""
Comparaison des bulles à verre bruxelloises
  • OpenData Brussels  — opendata.bruxelles.be (CC BY 4.0)
  • OpenStreetMap      — Brussels-daily.pbf

Fichiers produits (dans le même dossier que ce script) :
  report_glass_bins.txt             — rapport lisible
  report_glass_bins.json            — rapport structuré
  missing_from_osm.geojson          — bulles OpenData absentes d'OSM,
                                       tags OSM prêts à l'emploi
  missing_from_opendata.geojson     — nœuds OSM absents de l'OpenData,
                                       tags OSM existants tels quels

Variable d'environnement optionnelle :
  MATCH_THRESHOLD_M  (défaut : 50)  seuil d'appariement en mètres
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
MATCH_THRESHOLD_M = int(os.environ.get("MATCH_THRESHOLD_M", "50"))

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

# Tags OSM complets à appliquer sur les bulles à créer dans OSM
OSM_TAGS_TEMPLATE: dict[str, str] = {
    "amenity":                 "recycling",
    "recycling:glass_bottles": "yes",
    "recycling_type":          "container",
    "operator":                "Bruxelles-Propreté - Net Brussel",
    "operator:fr":             "Bruxelles-Propreté",
    "operator:nl":             "Net Brussel",
    "operator:wikidata":       "Q23021854",
    # "location" est ajouté dynamiquement selon la catégorie OpenData
}


# ── Structures de données ──────────────────────────────────────────────────────
@dataclass
class ODPoint:
    """Un conteneur extrait de l'OpenData."""
    uid:          str
    lat:          float
    lon:          float
    address:      str
    municipality: str
    postalcode:   str
    category:     str          # ex. "bulle aerienne couleur"
    matched_osm_id:   Optional[int]   = None
    matched_osm_type: Optional[str]   = None
    match_dist_m:     Optional[float] = None


@dataclass
class OSMPoint:
    """Un nœud/way OSM amenity=recycling + recycling:glass_bottles=yes."""
    osm_id:   int
    osm_type: str
    lat:      float
    lon:      float
    tags:     dict = field(default_factory=dict)
    matched_od_uid: Optional[str]   = None
    match_dist_m:   Optional[float] = None


# ── Géométrie ──────────────────────────────────────────────────────────────────
def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance orthodromique en mètres (formule de Haversine)."""
    R = 6_371_000
    φ1, φ2 = math.radians(lat1), math.radians(lat2)
    dφ = math.radians(lat2 - lat1)
    dλ = math.radians(lon2 - lon1)
    a  = math.sin(dφ / 2) ** 2 + math.cos(φ1) * math.cos(φ2) * math.sin(dλ / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ── Détection du tag location depuis la catégorie OpenData ────────────────────
def detect_location(category: str) -> Optional[str]:
    """
    "bulle aerienne ..."  →  location=overground
    "bulle enterree ..."  →  location=underground
    Inconnu               →  None (tag omis, à vérifier sur le terrain)
    """
    c = (category.lower()
         .replace("é", "e").replace("è", "e").replace("ê", "e")
         .replace("à", "a").replace("â", "a"))
    if "aerien" in c:
        return "overground"
    if "enterr" in c or "souterr" in c or "underground" in c:
        return "underground"
    return None


# ── 1. Chargement OpenData ─────────────────────────────────────────────────────
def fetch_opendata() -> list[ODPoint]:
    print("📥  OpenData Brussels — téléchargement du GeoJSON …")
    try:
        r = requests.get(OPENDATA_GEOJSON, timeout=60)
        r.raise_for_status()
    except requests.RequestException as exc:
        sys.exit(f"❌  Impossible de télécharger l'OpenData : {exc}")

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

    print(f"     → {len(pts)} bulles chargées")
    return pts


# ── 2. Lecture du PBF OSM ──────────────────────────────────────────────────────
class GlassRecyclingHandler(osmium.SimpleHandler):
    """Collecte les objets OSM : amenity=recycling + recycling:glass_bottles=yes."""

    def __init__(self):
        super().__init__()
        self.pts: list[OSMPoint] = []

    @staticmethod
    def _is_glass_recycling(tags: dict) -> bool:
        return (
            tags.get("amenity") == "recycling"
            and tags.get("recycling:glass_bottles") == "yes"
        )

    def node(self, n):
        tags = dict(n.tags)
        if self._is_glass_recycling(tags):
            self.pts.append(OSMPoint(
                osm_id   = n.id,
                osm_type = "node",
                lat      = n.location.lat,
                lon      = n.location.lon,
                tags     = tags,
            ))

    def way(self, w):
        tags = dict(w.tags)
        if not self._is_glass_recycling(tags):
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
            print(f"     ⚠  way/{w.id} ignoré : {exc}")


def fetch_osm(pbf: str) -> list[OSMPoint]:
    pbf = os.path.realpath(pbf)
    if not os.path.exists(pbf):
        sys.exit(
            f"❌  Fichier PBF introuvable : {pbf}\n"
            "     Vérifiez que le checkout inclut le LFS."
        )
    size_mb = os.path.getsize(pbf) / 1_048_576
    print(f"🔍  OSM PBF : {pbf}  ({size_mb:.1f} Mo)")
    handler = GlassRecyclingHandler()
    handler.apply_file(pbf, locations=True)
    print(f"     → {len(handler.pts)} conteneurs verre trouvés dans OSM")
    return handler.pts


# ── 3. Appariement spatial ─────────────────────────────────────────────────────
def spatial_match(od_list: list[ODPoint], osm_list: list[OSMPoint]) -> None:
    """Appariement glouton plus proche voisin (1-1) dans un rayon de MATCH_THRESHOLD_M m."""
    print(f"🔗  Appariement spatial (seuil = {MATCH_THRESHOLD_M} m) …")
    used_osm_ids: set[int] = set()

    for od in od_list:
        best_dist, best_osm = float("inf"), None
        for osm in osm_list:
            if osm.osm_id in used_osm_ids:
                continue
            d = haversine_m(od.lat, od.lon, osm.lat, osm.lon)
            if d < best_dist:
                best_dist, best_osm = d, osm

        if best_osm is not None and best_dist <= MATCH_THRESHOLD_M:
            od.matched_osm_id    = best_osm.osm_id
            od.matched_osm_type  = best_osm.osm_type
            od.match_dist_m      = round(best_dist, 1)
            best_osm.matched_od_uid = od.uid
            best_osm.match_dist_m   = round(best_dist, 1)
            used_osm_ids.add(best_osm.osm_id)


# ── 4. Évaluation de la qualité des tags ──────────────────────────────────────
def assess_tags(osm: OSMPoint) -> tuple[list[str], list[str]]:
    """Retourne (erreurs_bloquantes, avertissements)."""
    errors:   list[str] = []
    warnings: list[str] = []
    t = osm.tags

    for key, expected in REQUIRED_TAGS.items():
        actual = t.get(key)
        if actual != expected:
            errors.append(f"{key}={expected!r}  →  actuel : {actual!r}")

    loc = t.get("location")
    if loc is None:
        warnings.append("location absent (attendu : 'underground' ou 'overground')")
    elif loc not in VALID_LOCATIONS:
        warnings.append(f"location={loc!r} — valeur inattendue")

    for key, expected in EXPECTED_OPERATORS.items():
        actual = t.get(key)
        if actual is None:
            warnings.append(f"{key} absent (attendu : {expected!r})")
        elif actual != expected:
            warnings.append(f"{key}={actual!r}  ≠  {expected!r}")

    return errors, warnings


# ── 5a. GeoJSON — bulles absentes d'OSM ───────────────────────────────────────
def write_geojson_missing_from_osm(miss_osm: list[ODPoint]) -> str:
    """GeoJSON prêt à l'emploi : tags OSM uniquement, rien d'autre."""
    features = []
    for b in sorted(miss_osm, key=lambda x: (x.postalcode, x.address)):
        location = detect_location(b.category)
        osm_tags: dict = {**OSM_TAGS_TEMPLATE}
        if location:
            osm_tags["location"] = location
        features.append({
            "type": "Feature",
            "geometry": {
                "type":        "Point",
                "coordinates": [round(b.lon, 7), round(b.lat, 7)],
            },
            "properties": osm_tags,
        })
    return json.dumps(
        {"type": "FeatureCollection", "features": features},
        ensure_ascii=False,
        indent=2,
    )


# ── 5b. GeoJSON — nœuds OSM absents de l'OpenData ────────────────────────────
def write_geojson_missing_from_opendata(miss_od: list[OSMPoint]) -> str:
    """GeoJSON des nœuds OSM sans correspondance : tags OSM existants tels quels."""
    features = []
    for b in miss_od:
        features.append({
            "type": "Feature",
            "geometry": {
                "type":        "Point",
                "coordinates": [round(b.lon, 7), round(b.lat, 7)],
            },
            "properties": dict(b.tags),
        })
    return json.dumps(
        {"type": "FeatureCollection", "features": features},
        ensure_ascii=False,
        indent=2,
    )


# ── 5c. Rapport texte + JSON ───────────────────────────────────────────────────
def write_reports(od_list: list[ODPoint], osm_list: list[OSMPoint]) -> None:
    by_osm_id: dict[int, OSMPoint] = {p.osm_id: p for p in osm_list}

    miss_osm = [p for p in od_list  if p.matched_osm_id  is None]
    miss_od  = [p for p in osm_list if p.matched_od_uid  is None]
    matched  = [p for p in od_list  if p.matched_osm_id  is not None]

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    W   = 72
    SEP = "═" * W
    sep = "─" * W

    # Pré-calcul des évaluations de tags
    tag_results: list[tuple[ODPoint, OSMPoint, list[str], list[str]]] = []
    cnt_ok = cnt_warn = cnt_err = 0
    for od in matched:
        osm = by_osm_id[od.matched_osm_id]
        errs, warns = assess_tags(osm)
        if errs:
            cnt_err  += 1
        elif warns:
            cnt_warn += 1
        else:
            cnt_ok   += 1
        tag_results.append((od, osm, errs, warns))

    # ── Rapport texte ──────────────────────────────────────────────────────────
    L: list[str] = [
        SEP,
        "  BULLES À VERRE — OpenData Brussels ↔ OpenStreetMap",
        SEP,
        f"  Généré le       : {now}",
        f"  Seuil spatial   : {MATCH_THRESHOLD_M} m",
        "",
        f"  OpenData         : {len(od_list):4d} bulles",
        f"  OSM              : {len(osm_list):4d} conteneurs verre "
        f"(amenity=recycling + recycling:glass_bottles=yes)",
        f"  Appariés         : {len(matched):4d}",
        f"  Absents d'OSM    : {len(miss_osm):4d}  → missing_from_osm.geojson",
        f"  Absents OpenData : {len(miss_od):4d}  → missing_from_opendata.geojson",
        "",
    ]

    # --- Section 1 : absents d'OSM ---
    L += [
        sep,
        f"  1. BULLES ABSENTES D'OSM ({len(miss_osm)})",
        sep,
        f"  Présentes dans l'OpenData mais sans nœud OSM dans un rayon de {MATCH_THRESHOLD_M} m.",
        "",
    ]
    for b in sorted(miss_osm, key=lambda x: (x.postalcode, x.address)):
        loc = detect_location(b.category)
        L += [
            f"  • [{b.postalcode} {b.municipality}]  {b.address}",
            f"    Catégorie   : {b.category}  →  location={loc or '?'}",
            f"    Coordonnées : {b.lat:.6f}, {b.lon:.6f}",
            f"    Carte OSM   : https://www.openstreetmap.org/"
            f"?mlat={b.lat}&mlon={b.lon}#map=19/{b.lat}/{b.lon}",
            "",
        ]

    # --- Section 2 : absents de l'OpenData ---
    L += [
        sep,
        f"  2. BULLES ABSENTES DE L'OPENDATA ({len(miss_od)})",
        sep,
        "  Nœuds OSM sans correspondance dans l'OpenData officielle.",
        "",
    ]
    for b in miss_od:
        L += [
            f"  • {b.osm_type}/{b.osm_id}  ({b.lat:.6f}, {b.lon:.6f})",
            f"    Tags    : { {k: v for k, v in list(b.tags.items())[:10]} }",
            f"    URL OSM : https://www.openstreetmap.org/{b.osm_type}/{b.osm_id}",
            "",
        ]

    # --- Section 3 : qualité des tags ---
    L += [
        sep,
        f"  3. QUALITÉ DES TAGS OSM — BULLES APPARIÉES ({len(matched)})",
        sep,
        f"  ✅  {cnt_ok} corrects  |  ⚠️   {cnt_warn} avertissements  |  ❌  {cnt_err} erreurs",
        "",
    ]
    for od, osm, errs, warns in tag_results:
        if not errs and not warns:
            continue
        status = "❌  ERREUR" if errs else "⚠️   AVERT."
        L += [
            f"  {status} — {osm.osm_type}/{osm.osm_id}  (dist = {od.match_dist_m} m)",
            f"    OpenData : {od.address}, {od.postalcode} {od.municipality}",
            f"    OSM      : https://www.openstreetmap.org/{osm.osm_type}/{osm.osm_id}",
        ]
        if errs:
            L.append("    Tags requis incorrects :")
            L += [f"      ✗  {e}" for e in errs]
        if warns:
            L.append("    Avertissements :")
            L += [f"      ~  {w}" for w in warns]
        L.append("")

    txt = "\n".join(L)

    # ── Rapport JSON ───────────────────────────────────────────────────────────
    json_tag_issues = []
    for od, osm, errs, warns in tag_results:
        if errs or warns:
            json_tag_issues.append({
                "osm_id":       osm.osm_id,
                "osm_type":     osm.osm_type,
                "osm_url":      f"https://www.openstreetmap.org/{osm.osm_type}/{osm.osm_id}",
                "address":      od.address,
                "municipality": od.municipality,
                "postalcode":   od.postalcode,
                "distance_m":   od.match_dist_m,
                "all_tags":     dict(osm.tags),
                "errors":       errs,
                "warnings":     warns,
            })

    jdata = {
        "generated_at":      now,
        "match_threshold_m": MATCH_THRESHOLD_M,
        "stats": {
            "opendata_total":   len(od_list),
            "osm_total":        len(osm_list),
            "matched":          len(matched),
            "missing_from_osm": len(miss_osm),
            "missing_from_od":  len(miss_od),
            "tag_ok":           cnt_ok,
            "tag_warn":         cnt_warn,
            "tag_err":          cnt_err,
        },
        "missing_from_osm": [
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
            for b in sorted(miss_osm, key=lambda x: (x.postalcode, x.address))
        ],
        "missing_from_opendata": [
            {
                "osm_id":   b.osm_id,
                "osm_type": b.osm_type,
                "lat":      b.lat,
                "lon":      b.lon,
                "osm_url":  f"https://www.openstreetmap.org/{b.osm_type}/{b.osm_id}",
                "tags":     dict(b.tags),
            }
            for b in miss_od
        ],
        "tag_issues": json_tag_issues,
    }

    # ── Écriture ───────────────────────────────────────────────────────────────
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    files: dict[str, str] = {
        "report_glass_bins.txt":         txt,
        "report_glass_bins.json":        json.dumps(jdata, ensure_ascii=False, indent=2),
        "missing_from_osm.geojson":      write_geojson_missing_from_osm(miss_osm),
        "missing_from_opendata.geojson": write_geojson_missing_from_opendata(miss_od),
    }

    print()
    print(txt)
    for fname, content in files.items():
        path = os.path.join(OUTPUT_DIR, fname)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        print(f"📄  {path}")


# ── Point d'entrée ─────────────────────────────────────────────────────────────
def main() -> None:
    od_list  = fetch_opendata()
    osm_list = fetch_osm(OSM_PBF_PATH)
    spatial_match(od_list, osm_list)
    write_reports(od_list, osm_list)


if __name__ == "__main__":
    main()
