#Compare trees data between OSM and 
## http://data-mobility.irisnet.be/fr/info/trees/
### Purpose of this script is to find trees in OSM that have incorrect circumference and matching them with opendata to fix this.
### Use csv lat/lon

#!pip install osmium
# Automatic download datas : 
#import requests

#url = "https://data.mobility.brussels/geoserver/bm_public_space/wfs?service=wfs&version=1.1.0&request=GetFeature&typeName=bm_public_space:trees&outputFormat=csv&srsName=EPSG:4326"
#r = requests.get(url)
#with open("trees.csv", "wb") as f:
#    f.write(r.content)

#url = "http://download.openstreetmap.fr/extracts/europe/belgium/brussels_capital_region-latest.osm.pbf"
#r = requests.get(url)
#with open("brussels_capital_region.pbf", "wb") as f:
#    f.write(r.content)

#!pip install rtree

import pandas as pd
import osmium as o
from math import radians, sin, cos, sqrt, atan2
from rtree import index


def haversine_distance(coord1, coord2):
    """
    Calculate the great-circle distance between two points on the Earth (specified in decimal degrees).
    Returns distance in kilometers.
    """
    R = 6371.0  # Earth radius in kilometers

    lat1, lon1 = map(radians, coord1)
    lat2, lon2 = map(radians, coord2)

    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat / 2)**2 + cos(lat1) * cos(lat2) * sin(dlon / 2)**2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))

    return R * c


class Node:
    def __init__(self, node_id, location, tags, version):
        self.node_id = node_id
        self.location = location
        self.tags = tags
        self.version = version


class NodeCacheHandler(o.SimpleHandler):
    def __init__(self):
        super().__init__()
        self.node_cache = {}

    def node(self, n):
        if n.location.valid():
            loc = (n.location.lat, n.location.lon)
            self.node_cache[n.id] = Node(n.id, loc, dict(n.tags), n.version)


class OSMTreeMatcher:
    def __init__(self, pbf_path, csv_path, osm_out, csv_out,
                 threshold_meters=0.2, max_tree_nodes=None):
        self.pbf_path = pbf_path
        self.csv_path = csv_path
        self.osm_out = osm_out
        self.csv_out = csv_out
        self.threshold_m = threshold_meters
        self.threshold_km = threshold_meters / 1000.0  # convert meters to km
        self.max_tree_nodes = max_tree_nodes
        
        self.handler = NodeCacheHandler()
        self.csv_data = None
        self.tree_nodes = {}
        self.spatial_index = None
        self.matched_data = pd.DataFrame(columns=[
            'Node_ID', 'CSV_index', 'Distance_km', 'Numident', 'Circ_cm'
        ])

    def read_osm(self):
        """Read OSM PBF and cache nodes."""
        reader = o.io.Reader(self.pbf_path)
        o.apply(reader, self.handler)
        reader.close()

    def read_csv(self):
        cols = ['FID','gid','geom','numident','annee_plant','circumference','commune',
                'couverture','crown_diam','essence','hauteur','multitronc',
                'structure_couronne','status','espace_de_plantation','distribution','voirie']
        dtype = {'numident': str, 'crown_diam': str}
        self.csv_data = pd.read_csv(self.csv_path, names=cols, skiprows=1, dtype=dtype, decimal=',')

    @staticmethod
    def extract_lat_lon(geom_str):
        """
        Extract latitude, longitude from 'POINT(lon lat)' string.
        """
        lon, lat = map(float, geom_str.split('(')[-1].split(')')[0].split())
        return lat, lon

    def search_pbf_nodes(self):
        """Filter OSM nodes tagged as trees and cache them (no circumference check)."""
        counter = 0
        direction = input("Sorting direction (asc/desc)? [desc]: ").lower() or 'desc'
        if direction not in ('asc','desc'):
            direction = 'desc'

        sorted_items = sorted(self.handler.node_cache.items(), key=lambda x: x[0],
                              reverse=(direction=='desc'))

        for nid, node in sorted_items:
            if isinstance(node.location, tuple) and node.tags.get('natural') == 'tree':
                # Add all tree nodes without filtering by circumference
                self.tree_nodes[nid] = {
                    'location': node.location,
                    'tags': {k:v for k,v in node.tags.items() if k != 'natural'},
                    'version': node.version
                }
                if counter < 10:
                    print(f"[DEBUG] Node {nid}: loc={node.location}, ver={node.version}")
                    counter += 1
                if self.max_tree_nodes and len(self.tree_nodes) >= self.max_tree_nodes:
                    break

        print(f"Total trees indexed: {len(self.tree_nodes)}")
        if not self.tree_nodes:
            print("Aucun arbre OSM trouvé. Arrêt du processus de matching.")
            return
        self.build_rtree_index()

    def build_rtree_index(self):
        """Construct an R-tree index over tree node locations."""
        prop = index.Property()
        prop.dimension = 2
        idx = index.Index(properties=prop)
        for nid, info in self.tree_nodes.items():
            lat, lon = info['location']
            idx.insert(int(nid), (lon, lat, lon, lat))
        self.spatial_index = idx

    def match_trees(self):
        """Match CSV trees to nearest OSM tree nodes within threshold."""
        if not self.tree_nodes or self.spatial_index is None:
            print("Pas d'index spatial disponible. Veuillez exécuter search_pbf_nodes() avec des résultats valides.")
            return

        matches = []
        seen = set()

        deg_buffer = self.threshold_km / 111.32  # approx km to degrees

        for i, row in self.csv_data.iterrows():
            if row['status'] != 'en vie' or row['circumference'] == '0':
                continue

            lat, lon = self.extract_lat_lon(row['geom'])
            minx, miny = lon - deg_buffer, lat - deg_buffer
            maxx, maxy = lon + deg_buffer, lat + deg_buffer
            candidates = list(self.spatial_index.intersection((minx, miny, maxx, maxy)))

            best = None
            best_dist = float('inf')

            for cid in candidates:
                info = self.tree_nodes.get(cid)
                if not info:
                    continue
                d = haversine_distance((lat, lon), info['location'])
                if d < self.threshold_km and d < best_dist:
                    best_dist = d
                    best = {
                        'Node_ID': cid,
                        'CSV_index': i,
                        'Distance_km': d,
                        'Numident': row['numident'],
                        'Circ_cm': row['circumference']
                    }
            if best:
                matches.append(best)
                seen.add(i)

        self.matched_data = pd.DataFrame(matches, columns=[
            'Node_ID', 'CSV_index', 'Distance_km', 'Numident', 'Circ_cm'
        ])
        print(f"Matched rows: {len(seen)}")

    def generate_outputs(self, coord_source='csv'):
        """Generate .osm and .csv of matched data."""
        if self.matched_data.empty:
            print("Aucune correspondance trouvée. Fichiers de sortie non générés.")
            return

        unique = self.matched_data.drop_duplicates('Node_ID')
        unique[['Node_ID', 'Numident']].to_csv(self.csv_out, index=False)
        print(f"CSV saved: {self.csv_out}")

        with open(self.osm_out, 'w', encoding='utf-8') as f:
            f.write('<?xml version="1.0" encoding="UTF-8"?>\n<osm version="0.6">\n')
            seen_nodes = set()
            for _, r in self.matched_data.iterrows():
                nid = r['Node_ID']
                if nid in seen_nodes:
                    continue
                seen_nodes.add(nid)
                if coord_source == 'csv':
                    lat, lon = self.extract_lat_lon(self.csv_data.loc[r['CSV_index'], 'geom'])
                else:
                    lat, lon = self.tree_nodes[nid]['location']
                ver = self.tree_nodes[nid]['version']
                circ_m = float(r['Circ_cm']) / 100.0
                f.write(f'  <node id="{nid}" action="modify" lat="{lat}" lon="{lon}" version="{ver}">\n')
                f.write('    <tag k="natural" v="tree" />\n')
                f.write(f'    <tag k="circumference" v="{circ_m}" />\n')
                f.write(f'    <tag k="height" v="{self.csv_data.loc[r["CSV_index"], "hauteur"]}" />\n')
                f.write(f'    <tag k="species" v="{self.csv_data.loc[r["CSV_index"], "essence"]}" />\n')
                f.write('  </node>\n')
            f.write('</osm>')
        print(f"OSM saved: {self.osm_out}")


if __name__ == '__main__':
    pbf = 'brussels_capital_region.pbf'
    csvf = 'trees.csv'
    out_csv = 'matched_data.csv'
    out_osm = 'matched_data.osm'

    max_nodes = input("Max tree nodes to load? (enter for no limit): ")
    max_nodes = int(max_nodes) if max_nodes.strip() else None

    thresh = input("Threshold distance in meters [0.2]: ")
    thresh = float(thresh) if thresh.strip() else 0.2

    coord_src = input("Coordinates from csv or pbf? [csv]: ").lower() or 'csv'

    matcher = OSMTreeMatcher(pbf, csvf, out_osm, out_csv,
                             threshold_meters=thresh,
                             max_tree_nodes=max_nodes)
    matcher.read_osm()
    matcher.search_pbf_nodes()
    matcher.read_csv()
    matcher.match_trees()
    matcher.generate_outputs(coord_source=coord_src)
