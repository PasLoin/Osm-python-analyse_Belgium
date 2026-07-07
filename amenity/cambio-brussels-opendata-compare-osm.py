#!/usr/bin/env python3
"""
Comparaison des stations Cambio (car-sharing) de la Region Bruxelles-Capitale
  - Cambio API    : https://cwapi.cambio-carsharing.com/pub/stations/BEL
                    (couvre toute la Belgique, filtre ici sur Bruxelles)
                    Creative Commons Attribution 4.0 International
  - OpenStreetMap : Brussels-daily.pbf

Fichiers produits :
  report_cambio_stations.txt
  report_cambio_stations.json
  missing_in_osm.geojson
  missing_in_opendata.geojson
  tag_issues.geojson

Variable d'environnement :
  MATCH_THRESHOLD_M (defaut 30)
"""

import json
import math
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import numpy as np
import osmium
import requests
from scipy.spatial import cKDTree

SCRIPT_DIR   = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT    = os.path.join(SCRIPT_DIR, "..")
OSM_PBF_PATH = os.path.join(REPO_ROOT, "pbf_analyse", "history", "Brussels-daily.pbf")
OUTPUT_DIR   = SCRIPT_DIR

CAMBIO_API_URL = "https://cwapi.cambio-carsharing.com/pub/stations/BEL"

MATCH_THRESHOLD_M = float(os.environ.get("MATCH_THRESHOLD_M", "100"))
DEDUP_RADIUS_M    = 1.0

REQUIRED_TAGS  = {"amenity": "car_sharing"}
EXPECTED_ATTRS = {
    "brand":              "Cambio",
    "operator":           "cambio CarSharing",
    "operator:short":     "Cambio",
    "operator:type":      "private",
    "operator:wikidata":  "Q1028155",
    "operator:wikipedia": "de:Cambio CarSharing",
    "short_name":         "Cambio",
}

BRUSSELS_POSTAL_MIN = 1000
BRUSSELS_POSTAL_MAX = 1212

BRUSSELS_MUNICIPALITIES = {
    "anderlecht", "auderghem", "oudergem",
    "berchem-sainte-agathe", "sint-agatha-berchem",
    "bruxelles", "brussel",
    "etterbeek", "evere",
    "forest", "vorst",
    "ganshoren",
    "ixelles", "elsene",
    "jette", "koekelberg",
    "molenbeek-saint-jean", "sint-jans-molenbeek",
    "saint-gilles", "sint-gillis",
    "saint-josse-ten-noode", "sint-joost-ten-node",
    "schaerbeek", "schaarbeek",
    "uccle", "ukkel",
    "watermael-boitsfort", "watermaal-bosvoorde",
    "woluwe-saint-lambert", "sint-lambrechts-woluwe",
    "woluwe-saint-pierre", "sint-pieters-woluwe",
}


@dataclass
class CambioPoint:
    uid:           str
    station_id:    str
    name:          str
    lat:           float
    lon:           float
    street:        str
    street_number: str
    municipality:  str
    postalcode:    str
    vehicle_count: int
    nearest_osm_id:   Optional[int]   = None
    nearest_osm_type: Optional[str]   = None
    nearest_osm_dist: Optional[float] = None


@dataclass
class OSMCarSharingPoint:
    osm_id:   int
    osm_type: str
    lat:      float
    lon:      float
    tags:     dict = field(default_factory=dict)
    nearest_cambio_uid:  Optional[str]   = None
    nearest_cambio_dist: Optional[float] = None


def make_projector(ref_lat: float):
    m_per_deg_lat = 110_540.0
    m_per_deg_lon = 111_320.0 * math.cos(math.radians(ref_lat))

    def project(lat: float, lon: float) -> tuple[float, float]:
        return (lon * m_per_deg_lon, lat * m_per_deg_lat)

    return project


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R  = 6_371_000
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a  = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def normalize(s: str) -> str:
    s = s.lower().strip()
    for a, b in (("é", "e"), ("è", "e"), ("ê", "e"), ("à", "a"),
                 ("â", "a"), ("ô", "o"), ("î", "i"), ("ç", "c")):
        s = s.replace(a, b)
    return s


def split_bilingual_name(display_name: str) -> tuple[str, str]:
    if "/" in display_name:
        fr, nl = display_name.split("/", 1)
    elif " - " in display_name:
        fr, nl = display_name.split(" - ", 1)
    else:
        fr = nl = display_name
    return fr.strip(), nl.strip()


def is_brussels(postalcode: str, municipality: str) -> bool:
    try:
        pc = int(postalcode)
        if BRUSSELS_POSTAL_MIN <= pc <= BRUSSELS_POSTAL_MAX:
            return True
    except (ValueError, TypeError):
        pass
    return normalize(municipality) in BRUSSELS_MUNICIPALITIES


def fetch_cambio() -> list[CambioPoint]:
    print("Cambio API - telechargement des stations Belgique ...")
    try:
        r = requests.get(CAMBIO_API_URL, timeout=60)
        r.raise_for_status()
    except requests.RequestException as exc:
        sys.exit(f"Impossible de telecharger l'API Cambio : {exc}")

    data = r.json()
    if isinstance(data, dict):
        for key in ("stations", "items", "data", "results"):
            if isinstance(data.get(key), list):
                data = data[key]
                break

    if not isinstance(data, list):
        sys.exit("Format de reponse Cambio inattendu")

    print(f"  -> {len(data)} stations Belgique")

    pts: list[CambioPoint] = []
    for i, s in enumerate(data):
        addr = s.get("address") or {}
        geo  = s.get("geoposition") or {}
        lat, lon = geo.get("latitude"), geo.get("longitude")
        if lat is None or lon is None:
            continue

        postalcode   = str(addr.get("postalCode", "")).strip()
        municipality = (addr.get("addressLocation") or "").strip()
        if not is_brussels(postalcode, municipality):
            continue

        pts.append(CambioPoint(
            uid           = str(i),
            station_id    = str(s.get("id", "")),
            name          = (s.get("displayName") or s.get("name") or "").strip(),
            lat           = float(lat),
            lon           = float(lon),
            street        = (addr.get("streetAddress") or "").strip(),
            street_number = (addr.get("streetNumber") or "").strip(),
            municipality  = municipality,
            postalcode    = postalcode,
            vehicle_count = int(s.get("vehicleCount", 0) or 0),
        ))

    print(f"  -> {len(pts)} stations Bruxelles-Capitale retenues")
    return pts


def deduplicate_cambio(pts: list[CambioPoint], radius_m: float = DEDUP_RADIUS_M) -> list[CambioPoint]:
    kept: list[CambioPoint] = []
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
        print(f"  -> {removed} doublon(s) Cambio supprimes")
    return kept


class CarSharingHandler(osmium.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.pts: list[OSMCarSharingPoint] = []

    @staticmethod
    def _is_cambio(tags: dict) -> bool:
        if tags.get("amenity") != "car_sharing":
            return False
        for key in ("brand", "operator", "operator:short"):
            if "cambio" in (tags.get(key) or "").lower():
                return True
        return False

    def node(self, n):
        tags = dict(n.tags)
        if self._is_cambio(tags):
            self.pts.append(OSMCarSharingPoint(
                osm_id   = n.id,
                osm_type = "node",
                lat      = n.location.lat,
                lon      = n.location.lon,
                tags     = tags,
            ))

    def way(self, w):
        tags = dict(w.tags)
        if not self._is_cambio(tags):
            return
        try:
            valid = [(nd.lat, nd.lon) for nd in w.nodes if nd.location.valid()]
            if valid:
                lat = sum(p[0] for p in valid) / len(valid)
                lon = sum(p[1] for p in valid) / len(valid)
                self.pts.append(OSMCarSharingPoint(
                    osm_id   = w.id,
                    osm_type = "way",
                    lat      = lat,
                    lon      = lon,
                    tags     = tags,
                ))
        except Exception as exc:
            print(f"  ! way/{w.id} ignore : {exc}")


def fetch_osm(pbf: str) -> list[OSMCarSharingPoint]:
    pbf = os.path.realpath(pbf)
    if not os.path.exists(pbf):
        sys.exit(f"Fichier PBF introuvable : {pbf}\nVerifiez que le checkout inclut le LFS.")
    size_mb = os.path.getsize(pbf) / 1_048_576
    print(f"OSM PBF : {pbf}  ({size_mb:.1f} Mo)")
    handler = CarSharingHandler()
    handler.apply_file(pbf, locations=True)
    print(f"  -> {len(handler.pts)} stations Cambio trouvees dans OSM")
    return handler.pts


def spatial_match(cambio_list: list[CambioPoint], osm_list: list[OSMCarSharingPoint]) -> None:
    print(f"Appariement spatial par KDTree (seuil = {MATCH_THRESHOLD_M} m) ...")
    if not cambio_list or not osm_list:
        print("  -> liste vide, rien a apparier")
        return

    ref_lat = sum(p.lat for p in osm_list) / len(osm_list)
    project = make_projector(ref_lat)

    cambio_xy = np.array([project(p.lat, p.lon) for p in cambio_list])
    osm_xy    = np.array([project(p.lat, p.lon) for p in osm_list])

    osm_tree    = cKDTree(osm_xy)
    cambio_tree = cKDTree(cambio_xy)

    dists, idxs = osm_tree.query(cambio_xy, k=1)
    for p, d, idx in zip(cambio_list, dists, idxs):
        if d <= MATCH_THRESHOLD_M:
            osm = osm_list[idx]
            p.nearest_osm_id   = osm.osm_id
            p.nearest_osm_type = osm.osm_type
            p.nearest_osm_dist = round(float(d), 1)

    dists, idxs = cambio_tree.query(osm_xy, k=1)
    for p, d, idx in zip(osm_list, dists, idxs):
        if d <= MATCH_THRESHOLD_M:
            c = cambio_list[idx]
            p.nearest_cambio_uid  = c.uid
            p.nearest_cambio_dist = round(float(d), 1)

    n_c = sum(1 for p in cambio_list if p.nearest_osm_id is not None)
    n_o = sum(1 for p in osm_list if p.nearest_cambio_uid is not None)
    print(f"  -> {n_c}/{len(cambio_list)} stations Cambio ont un noeud OSM a proximite")
    print(f"  -> {n_o}/{len(osm_list)} noeuds OSM ont une station Cambio a proximite")


def assess_tags(tags: dict) -> tuple[list[str], list[str]]:
    errors:   list[str] = []
    warnings: list[str] = []

    for key, expected in REQUIRED_TAGS.items():
        actual = tags.get(key)
        if actual != expected:
            errors.append(f"{key}={expected!r}  ->  actuel : {actual!r}")

    for key, expected in EXPECTED_ATTRS.items():
        actual = tags.get(key)
        if actual is None:
            warnings.append(f"{key} absent (attendu : {expected!r})")
        elif actual.lower() != expected.lower():
            warnings.append(f"{key}={actual!r}  !=  {expected!r}")

    return errors, warnings


def _geojson(features: list) -> str:
    return json.dumps(
        {"type": "FeatureCollection", "features": features},
        ensure_ascii=False,
        indent=2,
    )


def geojson_missing_in_osm(pts: list[CambioPoint]) -> str:
    features = []
    for p in sorted(pts, key=lambda x: (x.postalcode, x.street)):
        name_fr, name_nl = split_bilingual_name(p.name)
        tags: dict = {**REQUIRED_TAGS, **EXPECTED_ATTRS}
        tags["name"]    = p.name if name_fr == name_nl else f"{name_fr} - {name_nl}"
        tags["name:fr"] = name_fr
        tags["name:nl"] = name_nl
        tags["ref"]     = p.station_id
        if p.vehicle_count:
            tags["capacity"] = str(p.vehicle_count)
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point",
                         "coordinates": [round(p.lon, 7), round(p.lat, 7)]},
            "properties": tags,
        })
    return _geojson(features)


def geojson_missing_in_opendata(pts: list[OSMCarSharingPoint]) -> str:
    features = []
    for p in pts:
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point",
                         "coordinates": [round(p.lon, 7), round(p.lat, 7)]},
            "properties": dict(p.tags),
        })
    return _geojson(features)


def geojson_tag_issues(
    tag_results: list[tuple[CambioPoint, OSMCarSharingPoint, list[str], list[str]]]
) -> str:
    features = []
    for c, osm, errs, warns in tag_results:
        if not errs and not warns:
            continue
        corrected = dict(osm.tags)
        corrected.update(REQUIRED_TAGS)
        corrected.update(EXPECTED_ATTRS)
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point",
                         "coordinates": [round(osm.lon, 7), round(osm.lat, 7)]},
            "properties": corrected,
        })
    return _geojson(features)


def write_reports(cambio_list: list[CambioPoint], osm_list: list[OSMCarSharingPoint]) -> None:
    by_osm_id: dict[int, OSMCarSharingPoint] = {p.osm_id: p for p in osm_list}

    missing_in_osm = [p for p in cambio_list if p.nearest_osm_id  is None]
    missing_in_od  = [p for p in osm_list    if p.nearest_cambio_uid is None]
    matched        = [p for p in cambio_list if p.nearest_osm_id  is not None]

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    W   = 72
    SEP = "=" * W
    sep = "-" * W

    tag_results: list[tuple[CambioPoint, OSMCarSharingPoint, list[str], list[str]]] = []
    cnt_ok = cnt_warn = cnt_err = 0
    for c in matched:
        osm = by_osm_id[c.nearest_osm_id]
        errs, warns = assess_tags(osm.tags)
        if errs:
            cnt_err  += 1
        elif warns:
            cnt_warn += 1
        else:
            cnt_ok   += 1
        tag_results.append((c, osm, errs, warns))

    L: list[str] = [
        SEP,
        "  STATIONS CAMBIO -- Bruxelles-Capitale <-> OpenStreetMap",
        SEP,
        f"  Genere le       : {now}",
        f"  Seuil spatial   : {MATCH_THRESHOLD_M} m",
        "",
        f"  Cambio (Bruxelles)    : {len(cambio_list):4d} stations",
        f"  OSM                   : {len(osm_list):4d} stations",
        f"  Avec voisin proche    : {len(matched):4d}",
        f"  Manquants in OSM      : {len(missing_in_osm):4d}  -> missing_in_osm.geojson",
        f"  Manquants in OpenData : {len(missing_in_od):4d}  -> missing_in_opendata.geojson",
        f"  Tags a corriger       : {cnt_warn + cnt_err:4d}  -> tag_issues.geojson",
        "",
    ]

    L += [
        sep,
        f"  1. MISSING IN OSM ({len(missing_in_osm)})",
        sep,
        "",
    ]
    for p in sorted(missing_in_osm, key=lambda x: (x.postalcode, x.street)):
        L += [
            f"  * [{p.postalcode} {p.municipality}]  {p.street} {p.street_number}  ({p.name})",
            f"    Coordonnees : {p.lat:.6f}, {p.lon:.6f}",
            f"    Carte OSM   : https://www.openstreetmap.org/"
            f"?mlat={p.lat}&mlon={p.lon}#map=19/{p.lat}/{p.lon}",
            "",
        ]

    L += [
        sep,
        f"  2. MISSING IN OPENDATA ({len(missing_in_od)})",
        sep,
        "",
    ]
    for p in missing_in_od:
        L += [
            f"  * {p.osm_type}/{p.osm_id}  ({p.lat:.6f}, {p.lon:.6f})",
            f"    Tags    : {p.tags}",
            f"    URL OSM : https://www.openstreetmap.org/{p.osm_type}/{p.osm_id}",
            "",
        ]

    L += [
        sep,
        f"  3. QUALITE DES TAGS OSM ({len(matched)})",
        sep,
        f"  OK : {cnt_ok}  |  Avertissements : {cnt_warn}  |  Erreurs : {cnt_err}",
        "",
    ]
    for c, osm, errs, warns in tag_results:
        if not errs and not warns:
            continue
        status = "ERREUR" if errs else "AVERT."
        L += [
            f"  [{status}] {osm.osm_type}/{osm.osm_id}  (dist = {c.nearest_osm_dist} m)",
            f"    Cambio : {c.name}, {c.street} {c.street_number}, {c.postalcode} {c.municipality}",
            f"    OSM    : https://www.openstreetmap.org/{osm.osm_type}/{osm.osm_id}",
        ]
        if errs:
            L.append("    Erreurs :")
            L += [f"      x  {e}" for e in errs]
        if warns:
            L.append("    Avertissements :")
            L += [f"      ~  {w}" for w in warns]
        L.append("")

    txt = "\n".join(L)

    jdata = {
        "generated_at":      now,
        "match_threshold_m": MATCH_THRESHOLD_M,
        "stats": {
            "cambio_total":        len(cambio_list),
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
                "station_id":    p.station_id,
                "name":          p.name,
                "lat":           p.lat,
                "lon":           p.lon,
                "street":        p.street,
                "street_number": p.street_number,
                "municipality":  p.municipality,
                "postalcode":    p.postalcode,
                "vehicle_count": p.vehicle_count,
                "osm_map_url":   (
                    f"https://www.openstreetmap.org/"
                    f"?mlat={p.lat}&mlon={p.lon}#map=19/{p.lat}/{p.lon}"
                ),
            }
            for p in sorted(missing_in_osm, key=lambda x: (x.postalcode, x.street))
        ],
        "missing_in_opendata": [
            {
                "osm_id":   p.osm_id,
                "osm_type": p.osm_type,
                "lat":      p.lat,
                "lon":      p.lon,
                "osm_url":  f"https://www.openstreetmap.org/{p.osm_type}/{p.osm_id}",
                "tags":     dict(p.tags),
            }
            for p in missing_in_od
        ],
        "tag_issues": [
            {
                "osm_id":     osm.osm_id,
                "osm_type":   osm.osm_type,
                "osm_url":    f"https://www.openstreetmap.org/{osm.osm_type}/{osm.osm_id}",
                "station_id": c.station_id,
                "name":       c.name,
                "distance_m": c.nearest_osm_dist,
                "all_tags":   dict(osm.tags),
                "errors":     errs,
                "warnings":   warns,
            }
            for c, osm, errs, warns in tag_results
            if errs or warns
        ],
    }

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    files: dict[str, str] = {
        "report_cambio_stations.txt":   txt,
        "report_cambio_stations.json":  json.dumps(jdata, ensure_ascii=False, indent=2),
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


def main() -> None:
    cambio_list = fetch_cambio()
    cambio_list = deduplicate_cambio(cambio_list)
    osm_list    = fetch_osm(OSM_PBF_PATH)
    spatial_match(cambio_list, osm_list)
    write_reports(cambio_list, osm_list)


if __name__ == "__main__":
    main()
