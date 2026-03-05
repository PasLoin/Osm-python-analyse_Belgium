# ============================================================
# Snapping photos sur des way IDs OpenStreetMap
# v2 — interpolation à vitesse constante le long de la route
# ============================================================
# Prérequis : pip install piexif shapely requests tqdm numpy
# ============================================================

import os
import shutil
import time
import zipfile
from fractions import Fraction

import numpy as np
import requests
from PIL import Image
from shapely.geometry import Point, LineString
from tqdm import tqdm
import piexif

# === CONFIGURATION ===
folder             = "./to-snap"
output_folder      = "./photos_snapped"
VALID_EXTENSIONS   = (".jpg", ".jpeg")
OVERPASS_URL       = "https://overpass-api.de/api/interpreter"
OVERPASS_RETRIES   = 3
OVERPASS_DELAY     = 5   # secondes entre retries

# Liste ordonnée des way IDs OpenStreetMap
way_ids = [791373002, 31275936, 14622087, 442601787, 31275937, 351522303, 408211682, 791336247]

os.makedirs(output_folder, exist_ok=True)


# ── Lecture GPS ──────────────────────────────────────────────

def dms_to_deg(d, m, s, ref):
    deg = d + m / 60 + s / 3600
    if ref in ('S', 'W'):
        deg *= -1
    return deg


def get_gps_from_exif(file_path):
    try:
        img       = Image.open(file_path)
        exif_data = piexif.load(img.info.get('exif', b''))
        gps       = exif_data.get('GPS', {})
        if not gps:
            return None

        def val(tag):
            nums = gps[tag]
            return (
                nums[0][0] / nums[0][1],
                nums[1][0] / nums[1][1],
                nums[2][0] / nums[2][1],
            )

        lat = dms_to_deg(*val(piexif.GPSIFD.GPSLatitude),
                         gps[piexif.GPSIFD.GPSLatitudeRef].decode())
        lon = dms_to_deg(*val(piexif.GPSIFD.GPSLongitude),
                         gps[piexif.GPSIFD.GPSLongitudeRef].decode())
        return (lat, lon)

    except Exception as e:
        print(f"⚠️  Lecture EXIF impossible ({file_path}) : {e}")
        return None


# ── Overpass API ─────────────────────────────────────────────

def overpass_query_from_ids(way_ids):
    """Charge la géométrie de tous les ways en une seule requête."""
    ids_str = "".join(f"way({wid});" for wid in way_ids)
    query = f"""
    [out:json];
    (
      {ids_str}
    );
    out geom;
    """
    for attempt in range(1, OVERPASS_RETRIES + 1):
        try:
            response = requests.get(OVERPASS_URL, params={"data": query}, timeout=20)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            print(f"⚠️  Overpass tentative {attempt}/{OVERPASS_RETRIES} échouée : {e}")
            if attempt < OVERPASS_RETRIES:
                time.sleep(OVERPASS_DELAY)

    return {"elements": []}


# ── Construction de la route ordonnée ────────────────────────

def build_ordered_linestring(ways_data, way_ids):
    """
    Construit une LineString continue depuis les ways dans l'ordre fourni.
    Retourne (linestring, longueur_totale_metres_approx).

    Chaque way est orienté automatiquement pour se raccorder
    à l'extrémité du way précédent (gestion des sens OSM).
    """
    way_by_id = {w['id']: w for w in ways_data.get('elements', [])}

    all_coords = []
    prev_end   = None

    for wid in way_ids:
        way = way_by_id.get(wid)
        if not way:
            print(f"⚠️  Way {wid} absent de la réponse Overpass, ignoré.")
            continue

        coords = [(p['lon'], p['lat']) for p in way['geometry']]

        if prev_end is None:
            all_coords.extend(coords)
        else:
            # Choisir l'orientation qui minimise le saut entre ways
            dist_start = (coords[0][0]  - prev_end[0])**2 + (coords[0][1]  - prev_end[1])**2
            dist_end   = (coords[-1][0] - prev_end[0])**2 + (coords[-1][1] - prev_end[1])**2
            if dist_end < dist_start:
                coords = list(reversed(coords))
            # On saute le premier point (= dernier point du way précédent)
            all_coords.extend(coords[1:])

        prev_end = all_coords[-1]

    if len(all_coords) < 2:
        raise ValueError("Impossible de construire une route valide depuis les ways fournis.")

    line = LineString(all_coords)
    print(f"✅ Route construite : {len(all_coords)} nœuds, "
          f"~{line.length * 111_000:.0f} m (approx. à l'équateur)")
    return line


# ── Interpolation à vitesse constante le long de la route ────

def interpolate_constant_speed(coords, line):
    """
    Répartit toutes les photos à vitesse constante le long de la route.

    Ancres : premier et dernier point GPS valide parmi les photos.
    Toutes les photos entre ces ancres reçoivent une position calculée
    par interpolation linéaire sur la distance curviligne de la route.

    Les photos AVANT la première ancre ou APRÈS la dernière
    conservent (ou reçoivent) leur coordonnée snappée individuelle.
    """
    n = len(coords)

    # Trouver première et dernière ancre GPS
    first_idx = next((i for i, c in enumerate(coords) if c), None)
    last_idx  = next((i for i, c in reversed(list(enumerate(coords))) if c), None)

    if first_idx is None or last_idx is None:
        print("⚠️  Aucune coordonnée GPS — interpolation impossible.")
        return coords

    if first_idx == last_idx:
        print("⚠️  Une seule photo avec GPS — vitesse constante impossible.")
        return coords

    print(f"ℹ️  Ancre début : photo #{first_idx + 1}  |  Ancre fin : photo #{last_idx + 1}")
    print(f"ℹ️  {last_idx - first_idx + 1} photo(s) interpolées sur la route.")

    # Projeter les ancres sur la route
    def project(lat, lon):
        return line.project(Point(lon, lat))

    d_start = project(*coords[first_idx])
    d_end   = project(*coords[last_idx])

    if d_end <= d_start:
        print("⚠️  La projection de l'ancre finale est avant l'ancre initiale — "
              "vérifier l'ordre des photos ou l'orientation des ways.")

    # Distances curvilignes uniformes entre les deux ancres
    n_seg     = last_idx - first_idx          # nombre d'intervalles
    distances = np.linspace(d_start, d_end, n_seg + 1)

    new_coords = list(coords)
    for idx, dist in zip(range(first_idx, last_idx + 1), distances):
        pt = line.interpolate(dist)
        new_coords[idx] = (pt.y, pt.x)       # (lat, lon)

    return new_coords


# ── Écriture EXIF (sans recompression) ───────────────────────

def deg_to_dms_rational(deg):
    """Conversion précise via Fraction pour éviter les erreurs de flottants."""
    f   = Fraction(abs(deg)).limit_denominator(1_000_000)
    d   = int(f)
    m_f = (f - d) * 60
    m   = int(m_f)
    s_f = (m_f - m) * 60
    s_r = s_f.limit_denominator(10_000)
    return (
        (d, 1),
        (m, 1),
        (s_r.numerator, s_r.denominator),
    )


def update_exif_no_recompress(src_path, dst_path, lat, lon):
    """Copie le fichier source et injecte les nouvelles coordonnées sans recompresser."""
    shutil.copy2(src_path, dst_path)
    exif_dict        = piexif.load(dst_path)
    exif_dict['GPS'] = {
        piexif.GPSIFD.GPSLatitudeRef:  b'N' if lat >= 0 else b'S',
        piexif.GPSIFD.GPSLatitude:     deg_to_dms_rational(lat),
        piexif.GPSIFD.GPSLongitudeRef: b'E' if lon >= 0 else b'W',
        piexif.GPSIFD.GPSLongitude:    deg_to_dms_rational(lon),
    }
    piexif.insert(piexif.dump(exif_dict), dst_path)


# === TRAITEMENT PRINCIPAL ====================================

# Chargement OSM
print("🌐 Chargement des ways OSM...")
ways_data = overpass_query_from_ids(way_ids)

if not ways_data.get("elements"):
    raise SystemExit("❌ Aucun élément OSM récupéré — vérifier les way IDs ou la connexion.")

print(f"✅ {len(ways_data['elements'])} way(s) chargé(s).\n")

# Construction de la route continue ordonnée
print("🗺️  Construction de la route ordonnée...")
route_line = build_ordered_linestring(ways_data, way_ids)
print()

# Lecture des photos et de leurs coordonnées brutes
photos = sorted([f for f in os.listdir(folder) if f.lower().endswith(VALID_EXTENSIONS)])

if not photos:
    raise SystemExit("❌ Aucune photo trouvée dans le dossier source.")

paths  = [os.path.join(folder, p) for p in photos]
coords = [get_gps_from_exif(p) for p in paths]

n_with_gps = sum(1 for c in coords if c)
print(f"📷 {len(photos)} photo(s) trouvée(s), {n_with_gps} avec coordonnées GPS.\n")

# ── Interpolation à vitesse constante sur la route ──────────
print("📐 Interpolation à vitesse constante...")
coords = interpolate_constant_speed(coords, route_line)
print()

# ── Écriture des fichiers ────────────────────────────────────
print("💾 Écriture des EXIF...")
for photo, path, coord in tqdm(zip(photos, paths, coords), total=len(photos)):
    if not coord:
        print(f"⚠️  Coordonnées manquantes, photo ignorée : {photo}")
        continue

    lat, lon = coord
    out_path = os.path.join(output_folder, photo)

    try:
        update_exif_no_recompress(path, out_path, lat, lon)
    except Exception as e:
        print(f"❌ Erreur écriture EXIF pour {photo} : {e}")

# === EXPORT ZIP =============================================

zip_path = "./photos_snapped.zip"
with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
    for root, _, files in os.walk(output_folder):
        for file in files:
            filepath = os.path.join(root, file)
            arcname  = os.path.relpath(filepath, output_folder)
            zipf.write(filepath, arcname)

print(f"\n✅ Terminé : {zip_path}")
