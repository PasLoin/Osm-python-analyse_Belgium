################# Warning : not optimised at all ! download https://data.mobility.brussels/fr/info/trees/ in csv lat/lon and the script display matching data based on distance ######
#### WIP 

import pandas as pd
import osmium as o
from geopy.distance import geodesic

# Specify the path to the PBF file (replace with your actual path)
pbf_file_path = 'brussels_capital_region.pbf'

# Create the NodeCacheHandler instance
class NodeCacheHandler(o.SimpleHandler):
    def __init__(self):
        super(NodeCacheHandler, self).__init__()
        self.node_cache = {}

    def node(self, n):
        # Process each node in the OSM data
        if n.location.valid():
            # If the node has a valid location, add it to the node cache
            node_location = (n.location.lat, n.location.lon)
            self.node_cache[n.id] = {'node_id': n.id, 'location': node_location, 'timestamp': n.timestamp}

# Create the NodeCacheHandler instance
handler = NodeCacheHandler()

# Create an OSM file reader for the PBF file
osm_file = o.io.Reader(pbf_file_path)

# Apply the OSM data to the handler
o.apply(osm_file, handler)

# Close the OSM file reader
osm_file.close()

# Specify the path to the CSV file
csv_file_path = 'trees.csv'

# Specify the column names in the CSV file
column_names = ['FID', 'gid', 'geom', 'numident', 'annee_plant', 'circumference', 'commune', 'couverture',
                'crown_diam', 'essence', 'hauteur', 'multitronc', 'structure_couronne', 'status',
                'espace_de_plantation', 'distribution', 'voirie']

# Specify the data types for columns 3 and 8
dtype_mapping = {'numident': str, 'crown_diam': str}

# Read the CSV file with the specified column names and data types
csv_data = pd.read_csv(csv_file_path, names=column_names, skiprows=1, dtype=dtype_mapping, decimal=',')

# Function to extract latitude and longitude from the 'geom' column
def extract_lat_lon(geom_str):
    lon, lat = map(float, geom_str.split('(')[-1].split(')')[0].split())
    return lat, lon

# Specify the threshold distance in meters
threshold_meters = 2

# Iterate over each row in the CSV file
for _, csv_row in csv_data.iterrows():
    # Extract the latitude and longitude from the 'geom' column
    lat, lon = extract_lat_lon(csv_row['geom'])

    # Iterate over each node in the PBF file
    for node_id, node_info in handler.node_cache.items():
        # Calculate the distance between CSV coordinates and PBF node coordinates
        node_lat, node_lon = node_info['location']
        distance = geodesic((lat, lon), (node_lat, node_lon)).meters

        # Check if the distance is below the threshold
        if distance < threshold_meters:
            # Match found
            print(f"Match found for CSV row {_} with Node ID {node_id} (Distance: {distance:.2f} meters), numident: {csv_row['numident']}, timestamp: {node_info['timestamp']}")
