#!/usr/bin/env python3
"""
UrbIS 3D Buildings → OpenStreetMap Simple 3D Buildings (S3DB)
=============================================================
Produit deux couches OSM S3DB correctes :

  building_outline  (1 feature par BUSOLID_ID)
    building=yes
    height=*            hauteur totale, cabanons exclus
    building:levels=*   estimation

  building_parts   (N features par BUSOLID_ID)
    ┌─ part_type='wall_body'  (1 par bâtiment, geom=GROUNDSURFACE)
    │   building:part=yes
    │   height=wall_height          ← sommet des murs depuis le sol
    │   roof:shape=flat
    │   roof:height=0
    │   building:levels=*
    │   (min_height omis : démarre au sol)
    │
    ├─ part_type='roof_part'  (1 par ROOFSURFACE réelle, ΔZ >= cabanon_threshold)
    │   building:part=yes
    │   height=z_max - ground_z     ← sommet de cette face depuis le sol
    │   min_height=z_min - ground_z ← base de cette face depuis le sol
    │   roof:height=z_max - z_min   ← épaisseur verticale
    │   roof:shape=flat             ← seulement si ΔZ < flat_threshold
    │   (building:levels omis)
    │
    └─ part_type='cabanon'  (1 par ROOFSURFACE avec ΔZ < cabanon_threshold)
        building:part=yes
        height=z_max - ground_z
        min_height=z_min - ground_z
        roof:shape=flat             ← toujours plat
        roof:height=z_max - z_min

Règles OSM S3DB respectées :
  - building=yes et building:part=yes ne coexistent jamais sur le même objet
  - min_height = hauteur depuis le sol où commence le part (omis si = 0)
  - height = hauteur totale depuis le sol où se termine le part
  - roof:shape omis si non détectable (trop complexe)
  - roof:shape détecté depuis la géométrie 3D des ROOFSURFACE :
      flat      : toutes faces ΔZ < flat_threshold
      skillion  : 1 seule face non-plate
      pyramidal : N faces, toutes au même apex Z (±0.2 m)
      gabled    : 2 faces inclinées (pignons en WALLSURFACE)
                  OU 4 faces dont 2 avec aire 2D projetée ≈ 0 (pignons en ROOFSURFACE)
      hipped    : 4 faces inclinées, apex différents
      mansard   : 4 faces dont 2 plates + 2 inclinées

Usage :
  python urbis_3d_to_osm.py                          # auto-détection
  python urbis_3d_to_osm.py --zone 21004
  python urbis_3d_to_osm.py --floor-height 3.8       # hauts plafonds
  python urbis_3d_to_osm.py --cabanon-threshold 2.0  # seuil cabanon
  python urbis_3d_to_osm.py --flat-threshold 0.5     # seuil toit plat
  python urbis_3d_to_osm.py --keep-temp
"""

import argparse
import glob
import logging
import re
import subprocess
import sys
from pathlib import Path

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Defaults ──────────────────────────────────────────────────────────────────
DEFAULT_FLOOR_HEIGHT      = 3.5   # m/étage — immeubles bruxellois typiques
DEFAULT_CABANON_THRESHOLD = 1.5   # m — ROOFSURFACE plus mince = cabanon
DEFAULT_FLAT_THRESHOLD    = 0.3   # m — ΔZ < seuil = roof:shape=flat


# ── Helpers ───────────────────────────────────────────────────────────────────

def run(cmd: list[str], step: str) -> None:
    log.info("▶ %s", step)
    log.debug("  cmd: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        log.error("Échec à l'étape « %s »\nSTDERR:\n%s", step, result.stderr)
        raise RuntimeError(f"ogr2ogr a échoué : {step}")
    if result.stderr:
        log.debug("  stderr: %s", result.stderr.strip())


def detect_zone(zone_arg: str | None) -> tuple[str, Path]:
    if zone_arg:
        src = Path(f"UrbISBuildings3D_{zone_arg}.gpkg")
        if not src.exists():
            raise FileNotFoundError(f"Fichier source introuvable : {src}")
        return zone_arg, src

    candidates = sorted(glob.glob("UrbISBuildings3D_[0-9]*.gpkg"))
    candidates = [c for c in candidates if "_work" not in c]
    if not candidates:
        raise FileNotFoundError(
            "Aucun fichier UrbISBuildings3D_XXXXX.gpkg trouvé dans le répertoire courant."
        )
    src = Path(candidates[0])
    m = re.search(r"(\d{5})", src.stem)
    if not m:
        raise ValueError(f"Impossible d'extraire le code de zone depuis : {src}")
    zone = m.group(1)
    log.info("Zone détectée : %s  (%s)", zone, src)
    return zone, src


def drop_layer(gpkg: str, layer: str) -> None:
    subprocess.run(
        ["ogrinfo", gpkg, "-sql", f"DROP TABLE IF EXISTS \"{layer}\""],
        capture_output=True,
    )


# ── SQL factories ─────────────────────────────────────────────────────────────

def sql_ground_ref() -> str:
    return """
        SELECT BUSOLID_ID,
               MIN(ST_MinZ(geom)) AS ground_z
        FROM   BuildingFaces
        WHERE  TYPE = 'GROUNDSURFACE'
        GROUP  BY BUSOLID_ID
    """


def sql_roof_shape_signals(cabanon_threshold: float, flat_threshold: float) -> str:
    """
    Étape 2b — Calcul des signaux géométriques 3D pour la détection du roof:shape.
    Travaille sur les ROOFSURFACE 3D originales (avant CastToXY).

    Signaux produits par BUSOLID_ID :
      roof_face_count  : nombre de ROOFSURFACE réelles (hors cabanons)
      flat_face_count  : nombre de faces avec ΔZ < flat_threshold
      sloped_face_count: nombre de faces avec ΔZ >= flat_threshold
      shared_apex      : 1 si toutes les faces partagent le même z_max (±0.2 m) → pyramidal
      min_2d_area      : aire 2D projetée minimale d'une face (proche de 0 = pignon vertical)
      max_face_dz      : ΔZ maximal parmi les faces

    Règle de détection (appliquée dans sql_faces_enriched via JOIN) :
      flat      : flat_face_count = roof_face_count
      skillion  : sloped_face_count = 1
      pyramidal : shared_apex = 1 AND sloped_face_count >= 3
      gabled    : sloped_face_count = 2
                  OU (sloped_face_count = 4 AND min_2d_area < gable_area_threshold)
      hipped    : sloped_face_count = 4 AND shared_apex = 0 AND min_2d_area >= gable_area_threshold
      mansard   : sloped_face_count = 2 AND flat_face_count = 2
      (sinon)   : NULL → omis dans le tag OSM
    """
    ct = cabanon_threshold
    ft = flat_threshold
    # Seuil d'aire 2D projetée sous lequel une face est considérée comme un pignon vertical.
    # Un pignon réel vu du dessus est une surface très mince (triangle aplati).
    # Valeur empirique : < 2 m² projeté = quasi-vertical.
    gable_area_threshold = 2.0

    return f"""
        SELECT
            BUSOLID_ID,
            -- Nombre total de faces toit réelles (hors cabanons)
            COUNT(*) AS roof_face_count,

            -- Faces plates
            SUM(CASE WHEN (ST_MaxZ(geom) - ST_MinZ(geom)) < {ft}
                     THEN 1 ELSE 0 END) AS flat_face_count,

            -- Faces inclinées
            SUM(CASE WHEN (ST_MaxZ(geom) - ST_MinZ(geom)) >= {ft}
                     THEN 1 ELSE 0 END) AS sloped_face_count,

            -- Apex partagé ? (toutes faces ont le même z_max ± 0.2 m)
            CASE WHEN (MAX(ST_MaxZ(geom)) - MIN(ST_MaxZ(geom))) < 0.2
                 THEN 1 ELSE 0 END AS shared_apex,

            -- Aire 2D minimale (pignon vertical → proche de 0 vu du dessus)
            MIN(ST_Area(CastToXY(geom))) AS min_2d_area,

            -- ΔZ maximal (indicateur de pente)
            ROUND(MAX(ST_MaxZ(geom) - ST_MinZ(geom)), 2) AS max_face_dz,

            -- Détection roof:shape
            CASE
                -- Tout plat
                WHEN SUM(CASE WHEN (ST_MaxZ(geom) - ST_MinZ(geom)) < {ft}
                              THEN 1 ELSE 0 END) = COUNT(*)
                    THEN 'flat'

                -- 1 seule face inclinée → skillion (appentis)
                WHEN SUM(CASE WHEN (ST_MaxZ(geom) - ST_MinZ(geom)) >= {ft}
                              THEN 1 ELSE 0 END) = 1
                    THEN 'skillion'

                -- Toutes faces au même apex + ≥ 3 faces inclinées → pyramidal
                WHEN (MAX(ST_MaxZ(geom)) - MIN(ST_MaxZ(geom))) < 0.2
                 AND SUM(CASE WHEN (ST_MaxZ(geom) - ST_MinZ(geom)) >= {ft}
                              THEN 1 ELSE 0 END) >= 3
                    THEN 'pyramidal'

                -- 2 faces plates + 2 inclinées → mansard
                WHEN SUM(CASE WHEN (ST_MaxZ(geom) - ST_MinZ(geom)) < {ft}
                              THEN 1 ELSE 0 END) = 2
                 AND SUM(CASE WHEN (ST_MaxZ(geom) - ST_MinZ(geom)) >= {ft}
                              THEN 1 ELSE 0 END) = 2
                    THEN 'mansard'

                -- 4 faces inclinées dont certaines quasi-verticales (pignons) → gabled
                WHEN SUM(CASE WHEN (ST_MaxZ(geom) - ST_MinZ(geom)) >= {ft}
                              THEN 1 ELSE 0 END) = 4
                 AND MIN(ST_Area(CastToXY(geom))) < {gable_area_threshold}
                    THEN 'gabled'

                -- 2 faces inclinées (pignons en WALLSURFACE) → gabled
                WHEN SUM(CASE WHEN (ST_MaxZ(geom) - ST_MinZ(geom)) >= {ft}
                              THEN 1 ELSE 0 END) = 2
                    THEN 'gabled'

                -- 4 faces inclinées, apex différents, pas de pignons → hipped
                WHEN SUM(CASE WHEN (ST_MaxZ(geom) - ST_MinZ(geom)) >= {ft}
                              THEN 1 ELSE 0 END) = 4
                 AND (MAX(ST_MaxZ(geom)) - MIN(ST_MaxZ(geom))) >= 0.2
                 AND MIN(ST_Area(CastToXY(geom))) >= {gable_area_threshold}
                    THEN 'hipped'

                -- Complexe ou ambigu → omis
                ELSE NULL
            END AS detected_shape

        FROM BuildingFaces
        WHERE TYPE = 'ROOFSURFACE'
          AND (ST_MaxZ(geom) - ST_MinZ(geom)) >= {ct}
        GROUP BY BUSOLID_ID
    """


def sql_building_stats(cabanon_threshold: float) -> str:
    """
    Statistiques globales par bâtiment avec exclusion des cabanons.

    wall_height  = distance sol → base du toit réel (= début du toit en OSM)
    height       = distance sol → sommet du toit réel (cabanons exclus)
    roof_height  = épaisseur verticale du toit réel
    has_cabanon  = 1 si au moins une ROOFSURFACE < cabanon_threshold

    Fallback COALESCE : si aucun toit réel trouvé (bâtiment entièrement plat
    avec seulement des cabanons), on utilise le max absolu pour ne pas perdre
    le bâtiment.
    """
    t = cabanon_threshold
    return f"""
        SELECT
            f.BUSOLID_ID,
            CastToXY(ST_Union(
                CASE WHEN f.TYPE = 'GROUNDSURFACE' THEN f.geom END
            )) AS geom,

            ROUND(
                COALESCE(
                    MAX(CASE
                        WHEN f.TYPE = 'ROOFSURFACE'
                         AND (ST_MaxZ(f.geom) - ST_MinZ(f.geom)) >= {t}
                        THEN ST_MaxZ(f.geom) END),
                    MAX(CASE WHEN f.TYPE = 'ROOFSURFACE'
                        THEN ST_MaxZ(f.geom) END)
                )
              - MIN(CASE WHEN f.TYPE = 'GROUNDSURFACE'
                    THEN ST_MinZ(f.geom) END),
            1) AS height,

            ROUND(
                MAX(CASE
                    WHEN f.TYPE = 'ROOFSURFACE'
                     AND (ST_MaxZ(f.geom) - ST_MinZ(f.geom)) >= {t}
                    THEN ST_MaxZ(f.geom) END)
              - MIN(CASE
                    WHEN f.TYPE = 'ROOFSURFACE'
                     AND (ST_MaxZ(f.geom) - ST_MinZ(f.geom)) >= {t}
                    THEN ST_MinZ(f.geom) END),
            1) AS roof_height,

            ROUND(
                MIN(CASE
                    WHEN f.TYPE = 'ROOFSURFACE'
                     AND (ST_MaxZ(f.geom) - ST_MinZ(f.geom)) >= {t}
                    THEN ST_MinZ(f.geom) END)
              - MIN(CASE WHEN f.TYPE = 'GROUNDSURFACE'
                    THEN ST_MinZ(f.geom) END),
            1) AS wall_height,

            MAX(CASE
                WHEN f.TYPE = 'ROOFSURFACE'
                 AND (ST_MaxZ(f.geom) - ST_MinZ(f.geom)) < {t}
                THEN 1 ELSE 0
            END) AS has_cabanon

        FROM BuildingFaces f
        GROUP BY f.BUSOLID_ID
    """


def sql_faces_enriched() -> str:
    """
    Faces 2D individuelles avec attributs Z relatifs au sol.
    Joint roof_shape_signals pour propager detected_shape à chaque face.
    On garde chaque face séparément (pas d'union) pour
    permettre un building:part par face dans OSM.
    Inclut WALLSURFACE pour inspection / validation des données source.
    """
    return """
        SELECT
            f.id,
            f.BUSOLID_ID,
            f.TYPE,
            CastToXY(f.geom)                              AS geom,
            ROUND(ST_MinZ(f.geom), 1)                     AS z_min,
            ROUND(ST_MaxZ(f.geom), 1)                     AS z_max,
            ROUND(ST_MaxZ(f.geom) - ST_MinZ(f.geom), 1)  AS face_dz,
            ROUND(ST_MinZ(f.geom) - g.ground_z, 1)        AS min_height,
            ROUND(ST_MaxZ(f.geom) - g.ground_z, 1)        AS max_height,
            s.height                                       AS total_height,
            s.roof_height                                  AS total_roof_height,
            s.wall_height                                  AS wall_height,
            s.has_cabanon                                  AS has_cabanon,
            r.detected_shape                               AS detected_shape,
            r.roof_face_count                              AS roof_face_count
        FROM   BuildingFaces f
        JOIN   ground_ref g      ON g.BUSOLID_ID = f.BUSOLID_ID
        JOIN   building_stats s  ON s.BUSOLID_ID = f.BUSOLID_ID
        LEFT JOIN roof_shape_signals r ON r.BUSOLID_ID = f.BUSOLID_ID
        WHERE  f.TYPE IN ('GROUNDSURFACE', 'ROOFSURFACE', 'WALLSURFACE')
    """


def sql_building_outline(floor_height: float) -> str:
    """
    Couche OSM : building=yes — 1 feature par bâtiment.
    Géométrie = union des GROUNDSURFACE déjà reprojetées en WGS84 dans la couche faces.
    Stats (height, wall_height...) jointes depuis building_stats (attributs seuls, sans geom).
    → Évite une reprojection coûteuse des polygones complexes.
    """
    return f"""
        SELECT
            f.BUSOLID_ID                                        AS osm_ref,
            ST_Union(f.geom)                                    AS geom,
            'yes'                                               AS building,
            CAST(MAX(f.total_height) AS TEXT)                   AS height,
            CAST(MAX(1, ROUND(MAX(f.wall_height) / {floor_height}, 0))
                 AS INTEGER)                                    AS "building:levels",
            CASE MAX(f.has_cabanon) WHEN 1 THEN 'yes' ELSE 'no' END
                                                                AS cabanon_detected
        FROM faces f
        WHERE f.TYPE = 'GROUNDSURFACE'
        GROUP BY f.BUSOLID_ID
    """


def sql_building_parts(
    cabanon_threshold: float,
    flat_threshold: float,
    floor_height: float,
) -> str:
    """
    Couche OSM : building:part=yes — N features par bâtiment.

    UNION ALL de 3 sous-requêtes, toutes depuis la couche faces (WGS84) :
      1. Corps principal (murs) — GROUNDSURFACE agrégé par bâtiment
      2. Toits réels — 1 row par ROOFSURFACE avec ΔZ >= cabanon_threshold
         roof:shape = detected_shape (calculé sur géom 3D en étape 2b)
                    = 'flat' forcé si ΔZ < flat_threshold
                    = NULL si non détectable (omis dans OSM)
      3. Cabanons  — 1 row par ROOFSURFACE avec ΔZ < cabanon_threshold
         roof:shape = 'flat' toujours

    Règles OSM S3DB :
      - min_height omis (NULL) pour les parts au sol
      - building:levels seulement sur le corps principal
    """
    ct = cabanon_threshold
    ft = flat_threshold
    fh = floor_height
    return f"""
        -- ── 1. Corps principal (murs) ────────────────────────────────────────
        SELECT
            f.BUSOLID_ID                                        AS osm_ref,
            ST_Union(f.geom)                                    AS geom,
            'wall_body'                                         AS part_type,
            'yes'                                               AS "building:part",
            CAST(MAX(f.wall_height) AS TEXT)                    AS height,
            NULL                                                AS min_height,
            'flat'                                              AS "roof:shape",
            '0'                                                 AS "roof:height",
            CAST(MAX(1, ROUND(MAX(f.wall_height) / {fh}, 0))
                 AS INTEGER)                                    AS "building:levels",
            'no'                                                AS cabanon,
            NULL                                                AS roof_shape_raw
        FROM faces f
        WHERE f.TYPE = 'GROUNDSURFACE'
        GROUP BY f.BUSOLID_ID

        UNION ALL

        -- ── 2. Toits réels ────────────────────────────────────────────────────
        SELECT
            f.BUSOLID_ID                                        AS osm_ref,
            f.geom,
            'roof_part'                                         AS part_type,
            'yes'                                               AS "building:part",
            CAST(f.max_height AS TEXT)                          AS height,
            CAST(f.min_height AS TEXT)                          AS min_height,
            CASE
                WHEN f.face_dz < {ft}              THEN 'flat'
                WHEN f.detected_shape IS NOT NULL  THEN f.detected_shape
                ELSE NULL
            END                                                 AS "roof:shape",
            CAST(f.face_dz AS TEXT)                             AS "roof:height",
            NULL                                                AS "building:levels",
            'no'                                                AS cabanon,
            f.detected_shape                                    AS roof_shape_raw
        FROM faces f
        WHERE f.TYPE = 'ROOFSURFACE'
          AND f.face_dz >= {ct}

        UNION ALL

        -- ── 3. Cabanons ───────────────────────────────────────────────────────
        SELECT
            f.BUSOLID_ID                                        AS osm_ref,
            f.geom,
            'cabanon'                                           AS part_type,
            'yes'                                               AS "building:part",
            CAST(f.max_height AS TEXT)                          AS height,
            CAST(f.min_height AS TEXT)                          AS min_height,
            'flat'                                              AS "roof:shape",
            CAST(f.face_dz AS TEXT)                             AS "roof:height",
            NULL                                                AS "building:levels",
            'yes'                                               AS cabanon,
            NULL                                                AS roof_shape_raw
        FROM faces f
        WHERE f.TYPE = 'ROOFSURFACE'
          AND f.face_dz < {ct}
    """


# ── Pipeline ──────────────────────────────────────────────────────────────────

def process(
    zone: str,
    src: Path,
    floor_height: float,
    cabanon_threshold: float,
    flat_threshold: float,
    keep_temp: bool = False,
) -> dict[str, Path]:

    work_gpkg  = f"UrbISBuildings3D_{zone}_work.gpkg"
    stats_gpkg = f"buildings_osm_ready_{zone}.gpkg"
    faces_gpkg = f"building_faces_2d_{zone}.gpkg"
    wgs84_gpkg = f"building_faces_2d_{zone}_wgs84.gpkg"
    osm_gpkg   = f"osm_3d_tags_{zone}.gpkg"

    log.info(
        "Paramètres : floor_height=%.1f m | cabanon_threshold=%.1f m | flat_threshold=%.1f m",
        floor_height, cabanon_threshold, flat_threshold,
    )

    # ── Étape 1 : copie BuildingFaces → work ──────────────────────────────────
    run([
        "ogr2ogr", "-f", "GPKG",
        work_gpkg, str(src), "BuildingFaces",
    ], "Étape 1 — Copie BuildingFaces → work GPKG")

    # ── Étape 2 : ground_ref ──────────────────────────────────────────────────
    drop_layer(work_gpkg, "ground_ref")
    run([
        "ogr2ogr", "-f", "GPKG", "-update",
        work_gpkg, str(src),
        "-dialect", "SQLITE", "-sql", sql_ground_ref(),
        "-nln", "ground_ref",
    ], "Étape 2 — ground_ref")

    # ── Étape 2b : roof_shape_signals (sur géom 3D originale) ────────────────
    # Calculé avant CastToXY pour avoir accès aux Z réels de chaque face.
    # Produit detected_shape par BUSOLID_ID, joint dans l'étape 4.
    drop_layer(work_gpkg, "roof_shape_signals")
    run([
        "ogr2ogr", "-f", "GPKG", "-update",
        work_gpkg, str(src),
        "-dialect", "SQLITE",
        "-sql", sql_roof_shape_signals(cabanon_threshold, flat_threshold),
        "-nln", "roof_shape_signals",
    ], f"Étape 2b — Détection roof:shape (cabanon={cabanon_threshold} m, flat={flat_threshold} m)")

    # ── Étape 3 : building_stats ──────────────────────────────────────────────
    run([
        "ogr2ogr", "-f", "GPKG",
        stats_gpkg, str(src),
        "-dialect", "SQLITE",
        "-sql", sql_building_stats(cabanon_threshold),
        "-nln", "buildings",
    ], f"Étape 3 — building_stats (seuil cabanon={cabanon_threshold} m)")

    drop_layer(work_gpkg, "building_stats")
    run([
        "ogr2ogr", "-f", "GPKG", "-update",
        work_gpkg, stats_gpkg,
        "-nln", "building_stats",
    ], "Étape 3b — Injection building_stats → work GPKG")

    # ── Étape 4 : faces 2D enrichies (EPSG:31370) ────────────────────────────
    run([
        "ogr2ogr", "-f", "GPKG",
        faces_gpkg, work_gpkg,
        "-dialect", "SQLITE", "-sql", sql_faces_enriched(),
        "-nln", "faces",
        "-nlt", "MULTIPOLYGON",
        "-a_srs", "EPSG:31370",
    ], "Étape 4 — Faces 2D enrichies (EPSG:31370)")

    # ── Étape 5a : faces → WGS84 ─────────────────────────────────────────────
    run([
        "ogr2ogr", "-f", "GPKG",
        wgs84_gpkg, faces_gpkg,
        "-s_srs", "EPSG:31370", "-t_srs", "EPSG:4326",
        "-nlt", "MULTIPOLYGON",
        "-skipfailures",
    ], "Étape 5a — Faces reprojection → WGS84")

    # ── Étape 6a : couche building_outline ───────────────────────────────────
    # Géométrie dérivée des GROUNDSURFACE déjà en WGS84 dans wgs84_gpkg.
    # Pas de reprojection séparée des building_stats — évite un traitement très long.
    run([
        "ogr2ogr", "-f", "GPKG",
        osm_gpkg, wgs84_gpkg,
        "-dialect", "SQLITE",
        "-sql", sql_building_outline(floor_height),
        "-nln", "building_outline",
        "-nlt", "MULTIPOLYGON",
    ], "Étape 6a — Couche OSM building_outline (building=yes)")

    # ── Étape 6b : couche building_parts ─────────────────────────────────────
    drop_layer(osm_gpkg, "building_parts")
    run([
        "ogr2ogr", "-f", "GPKG", "-update",
        osm_gpkg, wgs84_gpkg,
        "-dialect", "SQLITE",
        "-sql", sql_building_parts(cabanon_threshold, flat_threshold, floor_height),
        "-nln", "building_parts",
        "-nlt", "MULTIPOLYGON",
    ], "Étape 6b — Couche OSM building_parts (building:part=yes)")

    outputs = {
        "work":        Path(work_gpkg),
        "stats":       Path(stats_gpkg),
        "faces_31370": Path(faces_gpkg),
        "faces_wgs84": Path(wgs84_gpkg),
        "osm_tags":    Path(osm_gpkg),
    }

    if not keep_temp:
        for key in ("work", "stats", "faces_31370"):
            p = outputs[key]
            if p.exists():
                p.unlink()
                log.info("Supprimé (intermédiaire) : %s", p)

    return outputs


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="UrbIS 3D Buildings → OSM Simple 3D Buildings (S3DB)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--zone", "-z",
        help="Code de zone à 5 chiffres (ex: 21004). Auto-détecté si absent.")
    parser.add_argument("--floor-height", "-f",
        type=float, default=DEFAULT_FLOOR_HEIGHT, metavar="M",
        help="Hauteur d'un étage en mètres (pour building:levels).")
    parser.add_argument("--cabanon-threshold", "-c",
        type=float, default=DEFAULT_CABANON_THRESHOLD, metavar="M",
        help="ΔZ max d'une ROOFSURFACE pour être classée cabanon (machinerie, gaine...).")
    parser.add_argument("--flat-threshold", "-t",
        type=float, default=DEFAULT_FLAT_THRESHOLD, metavar="M",
        help=(
            "ΔZ max d'une ROOFSURFACE réelle pour recevoir roof:shape=flat. "
            "Au-delà, roof:shape est omis (toit pentu / non détectable)."
        ))
    parser.add_argument("--keep-temp", action="store_true",
        help="Conserver les fichiers intermédiaires.")
    parser.add_argument("--verbose", "-v", action="store_true",
        help="Afficher les commandes ogr2ogr complètes.")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if subprocess.run(["ogr2ogr", "--version"], capture_output=True).returncode != 0:
        log.error("ogr2ogr introuvable — installez GDAL : sudo apt install gdal-bin")
        sys.exit(1)

    try:
        zone, src = detect_zone(args.zone)
        outputs = process(
            zone, src,
            floor_height=args.floor_height,
            cabanon_threshold=args.cabanon_threshold,
            flat_threshold=args.flat_threshold,
            keep_temp=args.keep_temp,
        )

        log.info("=" * 62)
        log.info("✅  Zone %s traitée avec succès.", zone)
        log.info("Paramètres utilisés :")
        log.info("  floor_height       : %.1f m/étage", args.floor_height)
        log.info("  cabanon_threshold  : %.1f m (ΔZ < seuil = cabanon)", args.cabanon_threshold)
        log.info("  flat_threshold     : %.1f m (ΔZ < seuil = roof:shape=flat)", args.flat_threshold)
        log.info("Fichiers produits :")
        for key, path in outputs.items():
            if path.exists():
                log.info("  %-18s %s  (%d KB)", key, path, path.stat().st_size // 1024)
        log.info("")
        log.info("Fichier OSM S3DB : osm_3d_tags_%s.gpkg", zone)
        log.info("  building_outline : building=yes | height | building:levels | cabanon_detected")
        log.info("  building_parts   : building:part=yes | part_type (wall_body/roof_part/cabanon)")
        log.info("                     height | min_height | roof:shape | roof:height | building:levels")

    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        log.error("❌ %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
