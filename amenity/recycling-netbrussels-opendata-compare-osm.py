#!/usr/bin/env python3
"""
Comparaison des bulles à verre bruxelloises
  • OpenData Brussels  — opendata.bruxelles.be (CC BY 4.0)
  • OpenStreetMap      — Brussels-daily.pbf

Fichiers produits (dans le même dossier que ce script) :
  report_glass_bins.txt          — rapport lisible
  report_glass_bins.json         — rapport structuré
  missing_in_osm.geojson         — bulles OpenData absentes d'OSM,
                                    tags OSM prêts à l'emploi
  missing_in_opendata.geojson    — nœuds OSM absents de l'OpenData,
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
    matched_osm_id:   Optional[int]   = None
    matched_osm_type: Optional[str]   = None
    match_dist_m:     Optional[float] = None


@dataclass
class OSMPoint:
    osm_id:   int
    osm_type: str
    lat:      float
    lon:      float
    tags:     dict = field(default_factory=dict)
    matched_od_uid: Optional[str]   = None
    match_dist_m:   Optional[float] = None


# ── Géométrie ──────────────────────────────────────────────────────────────────
def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R  = 6_371_000
    φ1 = math.radians(lat1)
    φ2 = math.radians(lat2)
    dφ = math.radians(lat2 - lat1)
    dλ = math.radians(lon2 - lon1)
    a  = math.sin(dφ / 2) ** 2 + math.cos(φ1) * math.cos(φ2) * math.sin(dλ / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))



# ── Déduplication OpenData ─────────────────────────────────────────────────────
def deduplicate_opendata(pts: list[ODPoint], radius_m: float = 2.0) -> list[ODPoint]:
    """
    L'OpenData liste parfois le même conteneur physique deux fois :
    une fois "couleur" et une fois "blanche". OSM n'a qu'un seul nœud.
    Sans déduplication, l'une des deux entrées tombe toujours en
    "missing in OSM" même quand le nœud est à 0 m.

    On supprime les doublons dont les coordonnées sont distantes de
    moins de radius_m mètres, en gardant la première occurrence.
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
    """
    Collecte uniquement les nœuds/ways avec :
      amenity=recycling  ET  recycling:glass_bottles=yes
    """

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


# ── 3. Appariement spatial ─────────────────────────────────────────────────────
def spatial_match(od_list: list[ODPoint], osm_list: list[OSMPoint]) -> None:
    """
    Appariement 1-à-1 trié globalement par distance croissante.

    Toutes les paires candidates sont calculées et triées avant toute
    assignation, ce qui évite qu'un point traité tôt s'approprie un
    nœud OSM qui était plus proche d'un autre point OpenData.
    """
    print(f"Appariement spatial (seuil = {MATCH_THRESHOLD_M} m) ...")

    candidates: list[tuple[float, ODPoint, OSMPoint]] = []
    for od in od_list:
        for osm in osm_list:
            d = haversine_m(od.lat, od.lon, osm.lat, osm.lon)
            if d <= MATCH_THRESHOLD_M:
                candidates.append((d, od, osm))

    candidates.sort(key=lambda x: x[0])

    used_od:  set[str] = set()
    used_osm: set[int] = set()

    for d, od, osm in candidates:
        if od.uid in used_od or osm.osm_id in used_osm:
            continue
        od.matched_osm_id    = osm.osm_id
        od.matched_osm_type  = osm.osm_type
        od.match_dist_m      = round(d, 1)
        osm.matched_od_uid   = od.uid
        osm.match_dist_m     = round(d, 1)
        used_od.add(od.uid)
        used_osm.add(osm.osm_id)

    print(f"  -> {len(used_od)} paires appariees")


# ── 4. Évaluation de la qualité des tags ──────────────────────────────────────
def assess_tags(osm: OSMPoint) -> tuple[list[str], list[str]]:
    errors:   list[str] = []
    warnings: list[str] = []
    t = osm.tags

    for key, expected in REQUIRED_TAGS.items():
        actual = t.get(key)
        if actual != expected:
            errors.append(f"{key}={expected!r}  ->  actuel : {actual!r}")

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
            warnings.append(f"{key}={actual!r}  !=  {expected!r}")

    return errors, warnings


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
    """Tags OSM existants tels quels pour les nœuds sans correspondance OpenData."""
    features = []
    for b in pts:
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point",
                         "coordinates": [round(b.lon, 7), round(b.lat, 7)]},
            "properties": dict(b.tags),
        })
    return _geojson(features)


# ── 6. Rapport texte + JSON ────────────────────────────────────────────────────
def write_reports(od_list: list[ODPoint], osm_list: list[OSMPoint]) -> None:
    by_osm_id: dict[int, OSMPoint] = {p.osm_id: p for p in osm_list}

    missing_in_osm = [p for p in od_list  if p.matched_osm_id  is None]
    missing_in_od  = [p for p in osm_list if p.matched_od_uid  is None]
    matched        = [p for p in od_list  if p.matched_osm_id  is not None]

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    W   = 72
    SEP = "=" * W
    sep = "-" * W

    # Tag quality
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
        f"  Apparies              : {len(matched):4d}",
        f"  Manquants in OSM      : {len(missing_in_osm):4d}  -> missing_in_osm.geojson",
        f"  Manquants in OpenData : {len(missing_in_od):4d}  -> missing_in_opendata.geojson",
        "",
    ]

    # --- Section 1 : missing in OSM ---
    L += [
        sep,
        f"  1. MISSING IN OSM ({len(missing_in_osm)})",
        sep,
        f"  Presents dans l'OpenData, aucun noeud OSM correspondant dans {MATCH_THRESHOLD_M} m.",
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
        "  Noeuds OSM (recycling:glass_bottles=yes) sans correspondance OpenData.",
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
        f"  3. QUALITE DES TAGS OSM -- BULLES APPARIEES ({len(matched)})",
        sep,
        f"  OK : {cnt_ok}  |  Avertissements : {cnt_warn}  |  Erreurs : {cnt_err}",
        "",
    ]
    for od, osm, errs, warns in tag_results:
        if not errs and not warns:
            continue
        status = "ERREUR" if errs else "AVERT."
        L += [
            f"  [{status}] {osm.osm_type}/{osm.osm_id}  (dist = {od.match_dist_m} m)",
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
            "opendata_total":       len(od_list),
            "osm_total":            len(osm_list),
            "matched":              len(matched),
            "missing_in_osm":       len(missing_in_osm),
            "missing_in_opendata":  len(missing_in_od),
            "tag_ok":               cnt_ok,
            "tag_warn":             cnt_warn,
            "tag_err":              cnt_err,
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
                "distance_m":   od.match_dist_m,
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
