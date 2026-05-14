#!/usr/bin/env python3
"""
Check associatedStreet relations in a Brussels OSM PBF for:
  1. Missing tags: addr:city, addr:country, addr:postcode
  2. Duplicate names (same name + same city + same postcode = duplicate)
  3. wikidata tag
Produces a plain-text report: associated-streets-report.txt
"""

import sys
import os
import urllib.request
import osmium
from collections import defaultdict

PBF_FILE = 'brussels_capital_region-latest.osm.pbf'
OSM_PBF_URL = (
    'https://raw.githubusercontent.com/PasLoin/'
    'Osm-python-analyse_Belgium/main/pbf_analyse/history/Brussels-daily.pbf'
)
HEADERS = {'User-Agent': 'Mozilla/5.0 (compatible; UrbIS-Sync/1.0)'}
REQUIRED_TAGS = ('addr:city', 'addr:country', 'addr:postcode')
OUTPUT_FILE = 'associated-streets-report.txt'


class AssociatedStreetCollector(osmium.SimpleHandler):
    """Collect all relations with type=associatedStreet."""

    def __init__(self):
        super().__init__()
        self.relations = []

    def relation(self, r):
        if r.tags.get('type') != 'associatedStreet':
            return
        tags = {t.k: t.v for t in r.tags}
        self.relations.append({
            'id': r.id,
            'tags': tags,
        })


def download_pbf(dest):
    print(f'[DL] Téléchargement du PBF depuis {OSM_PBF_URL}...')
    req = urllib.request.Request(OSM_PBF_URL, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=300) as resp:
        total = int(resp.headers.get('Content-Length', 0))
        downloaded = 0
        with open(dest, 'wb') as f:
            while chunk := resp.read(65536):
                f.write(chunk)
                downloaded += len(chunk)
                if total > 0:
                    pct = min(downloaded * 100 // total, 100)
                    print(f'\r    {pct}%', end='', flush=True)
    print()


def check_missing_tags(relations):
    """Return list of (relation, [missing_tags])."""
    issues = []
    for rel in relations:
        missing = [t for t in REQUIRED_TAGS if t not in rel['tags']]
        if missing:
            issues.append((rel, missing))
    return issues


def check_missing_wikidata(relations):
    """Return relations missing the wikidata tag."""
    return [rel for rel in relations if not rel['tags'].get('wikidata', '').strip()]


def _values_conflict(a, b):
    """Return True only if both values are non-empty AND different."""
    return bool(a) and bool(b) and a != b


def check_duplicates(relations):
    """
    Group by name, then cluster relations that are NOT differentiated
    by an explicit difference in addr:city or addr:postcode.
    A missing (empty) tag is compatible with any value.
    """
    by_name = defaultdict(list)
    for rel in relations:
        name = rel['tags'].get('name', '').strip()
        if not name:
            continue
        by_name[name].append(rel)

    duplicates = {}
    for name, rels in by_name.items():
        if len(rels) < 2:
            continue
        # Build clusters of non-differentiated relations (union-find)
        parent = list(range(len(rels)))

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(x, y):
            parent[find(x)] = find(y)

        for i in range(len(rels)):
            ci = rels[i]['tags'].get('addr:city', '').strip()
            pi = rels[i]['tags'].get('addr:postcode', '').strip()
            for j in range(i + 1, len(rels)):
                cj = rels[j]['tags'].get('addr:city', '').strip()
                pj = rels[j]['tags'].get('addr:postcode', '').strip()
                # If neither city nor postcode positively differs → duplicate
                if not _values_conflict(ci, cj) and not _values_conflict(pi, pj):
                    union(i, j)

        clusters = defaultdict(list)
        for i in range(len(rels)):
            clusters[find(i)].append(rels[i])
        for cluster in clusters.values():
            if len(cluster) > 1:
                r0 = cluster[0]
                city = r0['tags'].get('addr:city', '').strip()
                postcode = r0['tags'].get('addr:postcode', '').strip()
                duplicates[(name, city, postcode)] = cluster

    return duplicates


def write_report(relations, missing_issues, duplicates, missing_wikidata, path):
    with open(path, 'w', encoding='utf-8') as f:
        f.write('=== associatedStreet relations – Rapport de vérification ===\n')
        f.write(f'Total relations analysées : {len(relations)}\n\n')

        # --- Missing tags --------------------------------------------------
        f.write(f'--- Tags manquants ({len(missing_issues)} relations) ---\n\n')
        if not missing_issues:
            f.write('Aucun problème détecté.\n\n')
        for rel, missing in missing_issues:
            rid = rel['id']
            name = rel['tags'].get('name', '(sans nom)')
            f.write(
                f'  relation/{rid}  {name}\n'
                f'    manquant : {", ".join(missing)}\n'
                f'    https://www.openstreetmap.org/relation/{rid}\n\n'
            )

        # --- Duplicates ----------------------------------------------------
        dup_count = sum(len(v) for v in duplicates.values())
        f.write(f'--- Doublons (même name + city + postcode) '
                f'({dup_count} relations dans {len(duplicates)} groupes) ---\n\n')
        if not duplicates:
            f.write('Aucun doublon détecté.\n\n')
        for (name, city, postcode), rels in sorted(duplicates.items()):
            ctx = f'city={city or "(vide)"}  postcode={postcode or "(vide)"}'
            f.write(f'  « {name} »  ({ctx})\n')
            for rel in rels:
                f.write(
                    f'    relation/{rel["id"]}  '
                    f'https://www.openstreetmap.org/relation/{rel["id"]}\n'
                )
            f.write('\n')

        # --- Missing wikidata ----------------------------------------------
        f.write(f'--- Tag wikidata manquant ({len(missing_wikidata)} relations) ---\n\n')
        if not missing_wikidata:
            f.write('Aucun problème détecté.\n\n')
        for rel in missing_wikidata:
            rid = rel['id']
            name = rel['tags'].get('name', '(sans nom)')
            f.write(
                f'  relation/{rid}  {name}\n'
                f'    https://www.openstreetmap.org/relation/{rid}\n\n'
            )

    print(f'[OK] Rapport écrit : {path}')


def main():
    pbf_path = sys.argv[1] if len(sys.argv) > 1 else PBF_FILE
    output = sys.argv[2] if len(sys.argv) > 2 else OUTPUT_FILE

    if not os.path.isfile(pbf_path):
        download_pbf(pbf_path)

    print(f'[OSM] Lecture des relations associatedStreet dans {pbf_path}...')
    handler = AssociatedStreetCollector()
    handler.apply_file(pbf_path)
    relations = handler.relations
    print(f'[OSM] {len(relations)} relations associatedStreet trouvées')

    missing_issues = check_missing_tags(relations)
    print(f'[CHECK] {len(missing_issues)} relations avec tags manquants')

    duplicates = check_duplicates(relations)
    print(f'[CHECK] {len(duplicates)} groupes de doublons')

    missing_wikidata = check_missing_wikidata(relations)
    print(f'[CHECK] {len(missing_wikidata)} relations sans tag wikidata')

    write_report(relations, missing_issues, duplicates, missing_wikidata, output)


if __name__ == '__main__':
    main()
