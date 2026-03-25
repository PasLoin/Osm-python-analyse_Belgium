# generate_enrichment.py
## Add additional info on trees , use in combinaison with trees_bxl_mobility_matching_from_csv.py
### Run this script before.
import pandas as pd
import requests
import time

SPARQL_ENDPOINT = "https://query.wikidata.org/sparql"

LEAF_RETENTION_MAP = {
    'Q188235': 'deciduous',
    'Q107294': 'evergreen',
    'Q4261816': 'semi_evergreen',
}

NEEDLELEAVED_GENERA = {
    'Pinus', 'Picea', 'Abies', 'Larix', 'Cedrus', 'Taxus',
    'Juniperus', 'Cupressus', 'Chamaecyparis', 'Pseudotsuga',
    'Sequoia', 'Thuja', 'Cryptomeria', 'Metasequoia',
}

# Overrides espèce-spécifiques — priorité absolue sur tout le reste
SPECIES_LEAF_CYCLE_OVERRIDE = {
    # Magnolia : genre mixte deciduous/evergreen
    'Magnolia grandiflora':     'evergreen',
    'Magnolia virginiana':      'semi_evergreen',
    # Quercus persistants
    'Quercus ilex':             'evergreen',
    'Quercus suber':            'evergreen',
    'Quercus coccifera':        'evergreen',
    # Prunus persistants
    'Prunus laurocerasus':      'evergreen',
    'Prunus lusitanica':        'evergreen',
}

GENUS_LEAF_CYCLE = {
    # Deciduous
    'Acer': 'deciduous', 'Aesculus': 'deciduous', 'Ailanthus': 'deciduous',
    'Alnus': 'deciduous', 'Amelanchier': 'deciduous', 'Betula': 'deciduous',
    'Carpinus': 'deciduous', 'Castanea': 'deciduous', 'Catalpa': 'deciduous',
    'Celtis': 'deciduous', 'Cercis': 'deciduous', 'Cornus': 'deciduous',
    'Corylus': 'deciduous', 'Crataegus': 'deciduous', 'Fagus': 'deciduous',
    'Fraxinus': 'deciduous', 'Ginkgo': 'deciduous', 'Gleditsia': 'deciduous',
    'Gymnocladus': 'deciduous', 'Juglans': 'deciduous', 'Koelreuteria': 'deciduous',
    'Laburnum': 'deciduous', 'Larix': 'deciduous', 'Liquidambar': 'deciduous',
    'Liriodendron': 'deciduous', 'Magnolia': 'deciduous', 'Malus': 'deciduous',
    'Metasequoia': 'deciduous', 'Morus': 'deciduous', 'Platanus': 'deciduous',
    'Populus': 'deciduous', 'Prunus': 'deciduous', 'Pterocarya': 'deciduous',
    'Pyrus': 'deciduous', 'Quercus': 'deciduous', 'Robinia': 'deciduous',
    'Salix': 'deciduous', 'Sophora': 'deciduous', 'Sorbus': 'deciduous',
    'Tilia': 'deciduous', 'Ulmus': 'deciduous', 'Zelkova': 'deciduous',
    # Evergreen
    'Abies': 'evergreen', 'Araucaria': 'evergreen', 'Buxus': 'evergreen',
    'Cedrus': 'evergreen', 'Chamaecyparis': 'evergreen', 'Cupressus': 'evergreen',
    'Ilex': 'evergreen', 'Juniperus': 'evergreen', 'Picea': 'evergreen',
    'Pinus': 'evergreen', 'Pseudotsuga': 'evergreen', 'Sequoia': 'evergreen',
    'Taxus': 'evergreen', 'Thuja': 'evergreen', 'Cryptomeria': 'evergreen',
}

GENUS_LEAF_TYPE = {
    'broadleaved': [
        'Acer', 'Aesculus', 'Ailanthus', 'Alnus', 'Amelanchier', 'Betula',
        'Carpinus', 'Castanea', 'Catalpa', 'Celtis', 'Cercis', 'Cornus',
        'Corylus', 'Crataegus', 'Fagus', 'Fraxinus', 'Ginkgo', 'Gleditsia',
        'Gymnocladus', 'Juglans', 'Koelreuteria', 'Laburnum', 'Liquidambar',
        'Liriodendron', 'Magnolia', 'Malus', 'Morus', 'Platanus', 'Populus',
        'Prunus', 'Pterocarya', 'Pyrus', 'Quercus', 'Robinia', 'Salix',
        'Sophora', 'Sorbus', 'Tilia', 'Ulmus', 'Zelkova', 'Ilex', 'Buxus',
        'Araucaria',
    ],
    'needleleaved': [
        'Abies', 'Cedrus', 'Chamaecyparis', 'Cryptomeria', 'Cupressus',
        'Juniperus', 'Larix', 'Metasequoia', 'Picea', 'Pinus', 'Pseudotsuga',
        'Sequoia', 'Taxus', 'Thuja',
    ],
}

GENUS_TO_LEAF_TYPE = {}
for leaf_type, genera in GENUS_LEAF_TYPE.items():
    for genus in genera:
        GENUS_TO_LEAF_TYPE[genus] = leaf_type


def query_wikidata_sparql(species_name):
    query = f"""
    SELECT ?taxon ?genusLabel ?leafRetention ?genusLeafRetention WHERE {{
      ?taxon wdt:P225 "{species_name}" .
      OPTIONAL {{
        ?taxon wdt:P171* ?genus .
        ?genus wdt:P105 wd:Q34740 .
        OPTIONAL {{ ?genus wdt:P3014 ?genusLeafRetention . }}
      }}
      OPTIONAL {{ ?taxon wdt:P3014 ?leafRetention . }}
      SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en" . }}
    }}
    LIMIT 1
    """
    headers = {
        'Accept': 'application/sparql-results+json',
        'User-Agent': 'OSMTreeMatcher/1.0 (openstreetmap import script)'
    }
    try:
        r = requests.get(SPARQL_ENDPOINT, params={'query': query}, headers=headers, timeout=15)
        r.raise_for_status()
        bindings = r.json()['results']['bindings']
        if not bindings:
            return None, None, None

        b = bindings[0]
        qid = b['taxon']['value'].split('/')[-1] if 'taxon' in b else None
        genus = b['genusLabel']['value'] if 'genusLabel' in b else None

        retention_qid = None
        if 'leafRetention' in b:
            retention_qid = b['leafRetention']['value'].split('/')[-1]
        elif 'genusLeafRetention' in b:
            retention_qid = b['genusLeafRetention']['value'].split('/')[-1]

        leaf_cycle = LEAF_RETENTION_MAP.get(retention_qid) if retention_qid else None
        return qid, genus, leaf_cycle

    except Exception as e:
        print(f"  [WARN] SPARQL failed for '{species_name}': {e}")
        return None, None, None


def resolve_leaf_cycle(leaf_cycle_sparql, genus, species):
    """Priorité : override espèce > Wikidata SPARQL > fallback genre."""
    if species in SPECIES_LEAF_CYCLE_OVERRIDE:
        return SPECIES_LEAF_CYCLE_OVERRIDE[species], 'override'
    if leaf_cycle_sparql:
        return leaf_cycle_sparql, 'Wikidata'
    if genus:
        val = GENUS_LEAF_CYCLE.get(genus, '')
        return val, 'fallback dict' if val else 'manquant'
    return '', 'manquant'


def resolve_leaf_type(genus):
    if genus:
        return GENUS_TO_LEAF_TYPE.get(genus, 'broadleaved')
    return 'broadleaved'


def generate_enrichment_csv(csv_path, output_path='species_enrichment.csv'):
    cols = ['FID', 'gid', 'geom', 'numident', 'annee_plant', 'circumference', 'commune',
            'couverture', 'crown_diam', 'essence', 'hauteur', 'multitronc',
            'structure_couronne', 'status', 'espace_de_plantation', 'distribution', 'voirie']
    df = pd.read_csv(csv_path, names=cols, skiprows=1, dtype=str, decimal=',')

    unique_species = set()
    for essence in df['essence'].dropna().unique():
        s = str(essence).strip()
        species = s.split("'")[0].strip() if "'" in s else s
        if species:
            unique_species.add(species)

    print(f"{len(unique_species)} espèces uniques trouvées.\n")

    rows = []
    for i, species in enumerate(sorted(unique_species)):
        print(f"[{i+1}/{len(unique_species)}] {species} ...", end=' ', flush=True)

        qid, genus, leaf_cycle_sparql = query_wikidata_sparql(species)
        leaf_cycle, lc_source = resolve_leaf_cycle(leaf_cycle_sparql, genus, species)
        leaf_type = resolve_leaf_type(genus)

        found = "Wikidata" if qid else "non trouvé"
        print(f"{found} | QID={qid} | genus={genus} | leaf_cycle={leaf_cycle} [{lc_source}] | leaf_type={leaf_type}")

        rows.append({
            'species':          species,
            'genus':            genus or '',
            'species:wikidata': qid or '',
            'leaf_cycle':       leaf_cycle,
            'leaf_type':        leaf_type,
        })

        time.sleep(1.0)

    result = pd.DataFrame(rows)
    result.to_csv(output_path, index=False)
    print(f"\nEnrichment CSV saved: {output_path}")
    print("=> Vérifie les lignes [manquant] avant de relancer le script principal.")


if __name__ == '__main__':
    generate_enrichment_csv('trees.csv')
