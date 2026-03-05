# ============================================================
# Correction linéaire GPS : first and last pictures correct
# (building passage straight indoor mapping)
# Images sans GPS ==> on interpolle linéairement
# ============================================================
# Prérequis : pip install piexif pillow numpy
# ============================================================

import os
import shutil
import zipfile
from fractions import Fraction
from PIL import Image
import piexif
import numpy as np


def get_gps_from_exif(img_path):
    try:
        img = Image.open(img_path)
        exif_data = img.getexif()
        if not exif_data:
            return None

        # Tag 34853 = GPSInfo IFD — use get_ifd() instead of iterating raw value
        gps_info = exif_data.get_ifd(34853)

        if not gps_info:
            return None

        lat     = gps_info.get(2)   # GPSLatitude
        lat_ref = gps_info.get(1)   # GPSLatitudeRef
        lon     = gps_info.get(4)   # GPSLongitude
        lon_ref = gps_info.get(3)   # GPSLongitudeRef

        if not lat or not lon:
            return None

        def convert_to_degrees(value):
            d, m, s = value
            return float(d) + float(m) / 60 + float(s) / 3600

        latitude  = convert_to_degrees(lat)
        longitude = convert_to_degrees(lon)
        if lat_ref != 'N':
            latitude  = -latitude
        if lon_ref != 'E':
            longitude = -longitude

        return (latitude, longitude)

    except Exception as e:
        print(f"⚠️  Impossible de lire les EXIF de {img_path} : {e}")
        return None


def deg_to_dms_rational(deg):
    """Conversion degrés décimaux → DMS avec Fraction pour éviter les erreurs de flottants."""
    deg_abs = abs(deg)
    f = Fraction(deg_abs).limit_denominator(1_000_000)

    d = int(f)
    m_frac = (f - d) * 60
    m = int(m_frac)
    s_frac = (m_frac - m) * 60

    s_rational = s_frac.limit_denominator(10_000)
    return (
        (d, 1),
        (m, 1),
        (s_rational.numerator, s_rational.denominator),
    )


def create_gps_ifd(lat, lon):
    lat_ref = 'N' if lat >= 0 else 'S'
    lon_ref = 'E' if lon >= 0 else 'W'
    return {
        piexif.GPSIFD.GPSLatitudeRef:  lat_ref.encode(),
        piexif.GPSIFD.GPSLatitude:     deg_to_dms_rational(lat),
        piexif.GPSIFD.GPSLongitudeRef: lon_ref.encode(),
        piexif.GPSIFD.GPSLongitude:    deg_to_dms_rational(lon),
    }


def interpolate_coords(start, end, n):
    lats = np.linspace(start[0], end[0], n)
    lons = np.linspace(start[1], end[1], n)
    return list(zip(lats, lons))


# === CONFIGURATION ===
input_folder  = "./to-snap"
output_folder = "./to-snap/photos_corrigees"
os.makedirs(output_folder, exist_ok=True)

# Extensions acceptées (insensible à la casse)
VALID_EXTENSIONS = (".jpg", ".jpeg")
photos = sorted([
    f for f in os.listdir(input_folder)
    if f.lower().endswith(VALID_EXTENSIONS)
])

if not photos:
    raise SystemExit("❌ Aucune photo JPG trouvée dans le dossier source.")

# Récupérer les coordonnées GPS (None si absentes/invalides)
coords = [get_gps_from_exif(os.path.join(input_folder, p)) for p in photos]

# Seules la première et la dernière sont considérées valides (cas indoor)
first_valid = next((c for c in coords if c), None)
last_valid  = next((c for c in reversed(coords) if c), None)

if not first_valid or not last_valid:
    raise SystemExit("❌ Pas assez de photos avec GPS valides pour l'interpolation.")

# Interpolation linéaire sur l'ensemble des photos
interpolated_coords = interpolate_coords(first_valid, last_valid, len(photos))

# Écriture des coordonnées interpolées sur chaque photo
for photo, new_coord in zip(photos, interpolated_coords):
    src_path = os.path.join(input_folder, photo)
    dst_path = os.path.join(output_folder, photo)
    try:
        shutil.copy2(src_path, dst_path)

        exif_dict         = piexif.load(dst_path)
        exif_dict["GPS"]  = create_gps_ifd(*new_coord)
        exif_bytes        = piexif.dump(exif_dict)
        piexif.insert(exif_bytes, dst_path)

        print(f"✅ {photo} → {new_coord[0]:.6f}, {new_coord[1]:.6f}")

    except Exception as e:
        print(f"❌ Erreur sur {photo} : {e}")

# === EXPORT ZIP ===
zip_filename = output_folder + ".zip"
with zipfile.ZipFile(zip_filename, 'w', zipfile.ZIP_DEFLATED) as zipf:
    for root, dirs, files in os.walk(output_folder):
        for file in files:
            filepath = os.path.join(root, file)
            arcname  = os.path.relpath(filepath, output_folder)
            zipf.write(filepath, arcname)

print(f"\n✅ Archive ZIP créée : {zip_filename}")
