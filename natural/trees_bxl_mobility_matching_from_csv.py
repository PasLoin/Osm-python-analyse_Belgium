import pandas as pd
import osmium as o
from geopy.distance import geodesic

# Define a handler for processing OSM nodes
class NodeCacheHandler(o.SimpleHandler):
    def __init__(self):
        super(NodeCacheHandler, self).__init__()
        self.node_cache = {}

    def node(self, n):
        # Process each node in the OSM data
        if n.location.valid():
            node_location = (n.location.lat, n.location.lon)
            # Store relevant information about the node in the cache
            self.node_cache[n.id] = {'node_id': n.id, 'location': node_location, 'tags': dict(n.tags)}

# Define a class for matching trees between OSM and CSV data
class OSMTreeMatcher:
    def __init__(self, pbf_file_path, csv_file_path, output_csv_file_path, threshold_meters=2):
        # Initialize the OSMTreeMatcher with file paths and a distance threshold
        self.pbf_file_path = pbf_file_path
        self.csv_file_path = csv_file_path
        self.output_csv_file_path = output_csv_file_path
        self.threshold_meters = threshold_meters
        self.handler = NodeCacheHandler()
        self.csv_data = None

    def read_osm_data(self):
        # Read OSM data from the PBF file using the NodeCacheHandler
        osm_file = o.io.Reader(self.pbf_file_path)
        o.apply(osm_file, self.handler)
        osm_file.close()

    def read_csv_data(self):
        # Read CSV data with specified column names and data types
        column_names = ['FID', 'gid', 'geom', 'numident', 'annee_plant', 'circumference', 'commune', 'couverture',
                        'crown_diam', 'essence', 'hauteur', 'multitronc', 'structure_couronne', 'status',
                        'espace_de_plantation', 'distribution', 'voirie']
        dtype_mapping = {'numident': str, 'crown_diam': str}
        self.csv_data = pd.read_csv(self.csv_file_path, names=column_names, skiprows=1, dtype=dtype_mapping, decimal=',')

    def extract_lat_lon(self, geom_str):
        # Extract latitude and longitude from the 'geom' column in the CSV data
        lon, lat = map(float, geom_str.split('(')[-1].split(')')[0].split())
        return lat, lon

    def search_pbf_nodes(self):
        # Search for nodes in the PBF file with 'natural=tree' and 'circumference=0'
        tree_nodes = []

        for node_id, node_info in self.handler.node_cache.items():
            tags = node_info['tags']
            circumference = tags.get('circumference')

            # Check if the node has 'natural=tree' and 'circumference=0'
            if tags.get('natural') == 'tree' and circumference is not None and circumference == '0':
                # Collect additional tags for the tree node
                additional_tags = {key: value for key, value in tags.items() if key != 'natural' and key != 'circumference'}

                tree_nodes.append({
                    'node_id': node_id,
                    'location': node_info['location'],
                    'circumference': circumference,
                    'additional_tags': additional_tags
                })

        # Print the count of tree nodes found
        print(f"Number of tree nodes found in PBF: {len(tree_nodes)}")

        return tree_nodes

    def match_trees(self, tree_nodes):
        # Create a list to store the matching data
        matched_data_list = []

        # Match trees based on coordinates between CSV and PBF data
        for _, csv_row in self.csv_data.iterrows():
            lat, lon = self.extract_lat_lon(csv_row['geom'])

            for node_info in tree_nodes:
                node_lat, node_lon = node_info['location']
                distance = geodesic((lat, lon), (node_lat, node_lon)).meters

                if distance < self.threshold_meters:
                    # Append matching data to the list
                    matched_data_list.append({
                        'Node_ID': node_info['node_id'],
                        'CSV_Row_Index': _,
                        'Distance': distance,
                        'Numident': csv_row['numident'],
                        'Circumference': node_info['circumference']
                    })

                    # Print matching data during the process
                    print(f"Match found for CSV row {_} with Node ID {node_info['node_id']} "
                          f"(Distance: {distance:.2f} meters), "
                          f"Numident: {csv_row['numident']}, Circumference: {node_info['circumference']}")

        # Create a DataFrame from the list of dictionaries
        matched_data = pd.DataFrame(matched_data_list)

        # Save the matching data to a new CSV file
        matched_data.to_csv(self.output_csv_file_path, index=False)
        print(f"Matching data saved to {self.output_csv_file_path}")

if __name__ == "__main__":
    # Specify the paths to the PBF and CSV files
    pbf_path = 'brussels_capital_region.pbf'
    csv_path = 'trees.csv'
    output_csv_path = 'matched_data.csv'  # Specify the desired output CSV file path

    # Create an instance of OSMTreeMatcher with the output CSV file path
    tree_matcher = OSMTreeMatcher(pbf_path, csv_path, output_csv_path)

    # Read OSM data
    tree_matcher.read_osm_data()

    # Search for tree nodes in the PBF file
    tree_nodes = tree_matcher.search_pbf_nodes()

    # Read CSV data
    tree_matcher.read_csv_data()

    # Match trees between CSV and PBF data
    tree_matcher.match_trees(tree_nodes)
