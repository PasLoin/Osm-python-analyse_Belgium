# ============================================================
# Snapping photos sur des way IDs OpenStreetMap
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
folder             = "/content/photos"
output_folder      = "/content/photos_snapped"
VALID_EXTENSIONS   = (".jpg", ".jpeg")
OVERPASS_URL       = "https://overpass-api.de/api/interpreter"  # HTTPS
OVERPASS_RETRIES   = 3
OVERPASS_DELAY     = 5   # secondes entre retries

# Liste ordonnée des way IDs OpenStreetMap
way_ids = [1302798281, 1155053411, 30639600, 38343374]

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


# ── Interpolation GPS ────────────────────────────────────────

def interpolate_missing_gps(coords):
    """Interpole linéairement entre chaque paire de points GPS valides."""
    indices_with_gps = [i for i, gps in enumerate(coords) if gps]

    if len(indices_with_gps) < 2:
        print("⚠️  Moins de 2 points GPS valides — interpolation impossible.")
        return coords

    for i in range(1, len(indices_with_gps)):
        start_idx   = indices_with_gps[i - 1]
        end_idx     = indices_with_gps[i]
        start_coord = coords[start_idx]
        end_coord   = coords[end_idx]
        num_missing = end_idx - start_idx - 1

        if num_missing > 0:
            lats = np.linspace(start_coord[0], end_coord[0], num_missing + 2)[1:-1]
            lons = np.linspace(start_coord[1], end_coord[1], num_missing + 2)[1:-1]
            for j, lat, lon in zip(range(start_idx + 1, end_idx), lats, lons):
                coords[j] = (lat, lon)

    return coords


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

    return {"elements": []}  # fallback vide


# ── Snap géométrique ─────────────────────────────────────────

def snap_to_way(lat, lon, ways_data):
    point     = Point(lon, lat)
    best_line = None
    best_dist = float('inf')

    for way in ways_data.get("elements", []):
        line_coords = [(p['lon'], p['lat']) for p in way['geometry']]
        line = LineString(line_coords)
        dist = line.distance(point)
        if dist < best_dist:
            best_dist = dist
            best_line = line

    if best_line:
        snapped = best_line.interpolate(best_line.project(point))
        return (snapped.y, snapped.x)
    return (lat, lon)


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
    exif_dict         = piexif.load(dst_path)
    exif_dict['GPS']  = {
        piexif.GPSIFD.GPSLatitudeRef:  b'N' if lat >= 0 else b'S',  # bytes, pas str
        piexif.GPSIFD.GPSLatitude:     deg_to_dms_rational(lat),
        piexif.GPSIFD.GPSLongitudeRef: b'E' if lon >= 0 else b'W',  # bytes, pas str
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

# Photos
photos = sorted([f for f in os.listdir(folder) if f.lower().endswith(VALID_EXTENSIONS)])

if not photos:
    raise SystemExit("❌ Aucune photo trouvée dans le dossier source.")

paths  = [os.path.join(folder, p) for p in photos]
coords = [get_gps_from_exif(p) for p in paths]
coords = interpolate_missing_gps(coords)

for photo, path, coord in tqdm(zip(photos, paths, coords), total=len(photos)):
    if not coord:
        print(f"⚠️  Coordonnées manquantes, photo ignorée : {photo}")
        continue

    lat, lon         = coord
    new_lat, new_lon = snap_to_way(lat, lon, ways_data)
    out_path         = os.path.join(output_folder, photo)

    try:
        update_exif_no_recompress(path, out_path, new_lat, new_lon)
    except Exception as e:
        print(f"❌ Erreur écriture EXIF pour {photo} : {e}")

# === EXPORT ZIP =============================================

zip_path = "/content/photos_snapped.zip"
with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
    for root, _, files in os.walk(output_folder):
        for file in files:
            filepath = os.path.join(root, file)
            arcname  = os.path.relpath(filepath, output_folder)
            zipf.write(filepath, arcname)

print(f"\n✅ Terminé : {zip_path}")
