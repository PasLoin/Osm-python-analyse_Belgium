import pandas as pd
import osmium as o
from geopy.distance import geodesic

class NodeCacheHandler(o.SimpleHandler):
    def __init__(self):
        super(NodeCacheHandler, self).__init__()
        self.node_cache = {}

    def node(self, n):
        if n.location.valid():
            node_location = (n.location.lat, n.location.lon)
            self.node_cache[n.id] = {'node_id': n.id, 'location': node_location, 'tags': dict(n.tags)}

class OSMTreeMatcher:
    def __init__(self, pbf_file_path, csv_file_path, output_csv_file_path, output_osm_file_path, threshold_meters=2, max_tree_nodes=None):
        self.pbf_file_path = pbf_file_path
        self.csv_file_path = csv_file_path
        self.output_csv_file_path = output_csv_file_path
        self.output_osm_file_path = output_osm_file_path
        self.threshold_meters = threshold_meters
        self.max_tree_nodes = max_tree_nodes
        self.handler = NodeCacheHandler()
        self.csv_data = None
        self.matched_data = None

    def read_osm_data(self):
        osm_file = o.io.Reader(self.pbf_file_path)
        o.apply(osm_file, self.handler)
        osm_file.close()

    def read_csv_data(self):
        column_names = ['FID', 'gid', 'geom', 'numident', 'annee_plant', 'circumference', 'commune', 'couverture',
                        'crown_diam', 'essence', 'hauteur', 'multitronc', 'structure_couronne', 'status',
                        'espace_de_plantation', 'distribution', 'voirie']
        dtype_mapping = {'numident': str, 'crown_diam': str}
        self.csv_data = pd.read_csv(self.csv_file_path, names=column_names, skiprows=1, dtype=dtype_mapping, decimal=',')

    def extract_lat_lon(self, geom_str):
        lon, lat = map(float, geom_str.split('(')[-1].split(')')[0].split())
        return lat, lon

    def search_pbf_nodes(self):
        tree_nodes = []

        for node_id, node_info in self.handler.node_cache.items():
            tags = node_info['tags']
            circumference = tags.get('circumference')

            if tags.get('natural') == 'tree' and circumference is not None and float(circumference) > 100:
                additional_tags = {key: value for key, value in tags.items() if key != 'natural' and key != 'circumference'}
                tree_nodes.append({
                    'node_id': node_id,
                    'location': node_info['location'],
                    'additional_tags': additional_tags
                })

                if self.max_tree_nodes is not None and len(tree_nodes) >= self.max_tree_nodes:
                    break

        print(f"Number of tree nodes found in PBF: {len(tree_nodes)}")
        return tree_nodes

    def match_trees(self, tree_nodes):
        matched_data_list = []

        for _, csv_row in self.csv_data.iterrows():
            if csv_row['status'] != 'en vie' or csv_row['circumference'] == '0':
                continue

            lat, lon = self.extract_lat_lon(csv_row['geom'])

            for node_info in tree_nodes:
                node_lat, node_lon = node_info['location']
                distance = geodesic((lat, lon), (node_lat, node_lon)).meters

                if distance < self.threshold_meters:
                    matched_data_list.append({
                        'Node_ID': node_info['node_id'],
                        'CSV_Row_Index': _,
                        'Distance': distance,
                        'Numident': csv_row['numident'],
                        'Circumference': csv_row['circumference']
                    })

                    print(f"Match found for CSV row {_} with Node ID {node_info['node_id']} "
                          f"(Distance: {distance:.2f} meters), "
                          f"Numident: {csv_row['numident']}, Circumference (CSV): {csv_row['circumference']}")

        self.matched_data = pd.DataFrame(matched_data_list)
        self.matched_data.to_csv(self.output_csv_file_path, index=False)
        print(f"Matching data saved to {self.output_csv_file_path}")

    def generate_osm_file(self, tree_nodes):
        with open(self.output_osm_file_path, 'w', encoding='utf-8') as osm_file:
            osm_file.write('<?xml version="1.0" encoding="UTF-8"?>\n')
            osm_file.write('<osm version="0.6" generator="osmium/1.14">\n')

            # Write nodes from matching CSV data
            for _, row in self.matched_data.iterrows():
                node_id = row['Node_ID']
                lat, lon = self.extract_lat_lon(self.csv_data.loc[row['CSV_Row_Index'], 'geom'])
                circumference_m = float(row['Circumference']) / 100.0

                osm_file.write(f'  <node id="{node_id}" lat="{lat}" lon="{lon}" version="1">\n')
                osm_file.write('    <tag k="natural" v="tree" />\n')
                osm_file.write(f'    <tag k="circumference" v="{circumference_m}" />\n')
                osm_file.write(f'    <tag k="height" v="{self.csv_data.loc[row["CSV_Row_Index"], "hauteur"]}" />\n')
                osm_file.write(f'    <tag k="species" v="{self.csv_data.loc[row["CSV_Row_Index"], "essence"]}" />\n')
                osm_file.write('  </node>\n')

            osm_file.write('</osm>\n')
            print(f"OSM file generated and saved to {self.output_osm_file_path}")

if __name__ == "__main__":
    # Specify the paths to the PBF and CSV files
    pbf_path = 'brussels_capital_region.pbf'
    csv_path = 'trees.csv'
    output_csv_path = 'matched_data.csv'
    output_osm_path = 'matched_data.osm'

    # Prompt the user for the maximum number of tree nodes
    max_tree_nodes = input("Enter the maximum number of tree nodes to process (or press Enter for no limit): ")
    if max_tree_nodes.strip():
        max_tree_nodes = int(max_tree_nodes)
    else:
        max_tree_nodes = None

    # Prompt the user for the threshold distance
    threshold_meters = input("Enter the threshold distance in meters (press Enter for the default value of 0.2): ")
    if threshold_meters.strip():
        threshold_meters = float(threshold_meters)
    else:
        threshold_meters = 0.2

    # Create an instance of OSMTreeMatcher with the output CSV and OSM file paths
    tree_matcher = OSMTreeMatcher(pbf_path, csv_path, output_csv_path, output_osm_path, threshold_meters, max_tree_nodes=max_tree_nodes)

    # Read OSM data
    tree_matcher.read_osm_data()

    # Search for tree nodes in the PBF file
    tree_nodes = tree_matcher.search_pbf_nodes()

    # Read CSV data
    tree_matcher.read_csv_data()

    # Match trees between CSV and PBF data
    tree_matcher.match_trees(tree_nodes)

    # Generate OSM file
    tree_matcher.generate_osm_file(tree_nodes)
