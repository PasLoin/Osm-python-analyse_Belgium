### This script create multiple bbox around drivable network on a determined area and search if there's is a 360 deg coverage for each road segments.
### You can customise the start date changing this line : 'start_captured_at': '2003-04-15T16:42:46Z'
### After determine if a road segment is covered by Mapillary pictures , the script search first and last nodes of each segments and create
### a csv filte that can be used to generate a gpx using simple_ORS_Vroom_Optimization.py 
### This is a working POC and is not optimized and probably need some improvement.


#!pip install osmapi
#!pip install osmnx
import osmnx as ox
from shapely.geometry import box
import matplotlib.pyplot as plt
import requests
import time
import csv
import itertools
import xml.etree.ElementTree as ET

def total_street_network_length(place):
    G = ox.graph_from_place(place, network_type='drive')

    total_length_meters = sum(data['length'] for u, v, data in G.edges(data=True))
    total_length_kilometers = total_length_meters / 1000.0

    return total_length_kilometers

def create_bounding_boxes_for_osmids(place, num_streets):
    G = ox.graph_from_place(place, network_type='drive')

    street_info = {}
    for u, v, data in G.edges(data=True):
        if 'name' in data and 'osmid' in data:
            if isinstance(data['name'], list):
                for name in data['name']:
                    street_info[name] = data['osmid']
            else:
                street_info[data['name']] = data['osmid']

    num_streets = min(num_streets, len(street_info))
    selected_streets = list(street_info.keys())[:num_streets]

    bboxes = []
    unique_bboxes = set()

    ox.plot_graph(G, bgcolor='white', edge_color='blue', figsize=(15, 15), show=False, close=False)

    for street_name in selected_streets:
        edges = [(u, v, data) for u, v, data in G.edges(data=True) if 'name' in data and street_name in data['name']]

        if not edges:
            print(f"No information found for {street_name}")
            continue

        for edge in edges:
            u, v, data = edge
            osmids = data.get('osmid', [])
            geometry = data.get('geometry')
            length = data.get('length', 0)

            if not osmids or not geometry:
                continue

            if isinstance(osmids, int):
                osmids = [osmids]

            for osmid in osmids:
                edge_bbox = box(*geometry.bounds)

                if edge_bbox not in unique_bboxes:
                    unique_bboxes.add(edge_bbox)
                    bboxes.append((edge_bbox, street_name, osmid, length))

                    minx, miny, maxx, maxy = edge_bbox.bounds
                    rect = plt.Rectangle((minx, miny), maxx - minx, maxy - miny, linewidth=1, edgecolor='red',
                                        facecolor='none')
                    plt.gca().add_patch(rect)

    plt.title("Bounding Boxes for Selected Streets")
    plt.show()

    return G, bboxes

class Mapillary:
    def __init__(self, app_access_token, G):
        self.app_access_token = app_access_token
        self.url = 'https://graph.mapillary.com/images'
        self.uncovered_streets = {}
        self.uncovered_coordinates = []
        self.G = G

    def get_image_data(self, bbox, street_name, osmid, length):
        delay = 1  # Initialize delay
        while True:
            try:
                params = {
                    'access_token': self.app_access_token,
                    'fields': 'id,captured_at,is_pano,osm_id',
                    'bbox': ','.join(map(str, bbox.bounds)),
                    'start_captured_at': '2003-04-15T16:42:46Z',
                    'is_pano': 'true'
                }

                response = requests.get(self.url, params=params, timeout=10)

                if response.status_code == 200:
                    break

            except requests.exceptions.Timeout:
                print(f"Request timed out. Retrying in {delay} seconds...")
                time.sleep(delay)
                delay *= 2

        if response.status_code == 200:
            data = response.json()

            if data.get('data'):
                if len(data['data']) > 3:
                    print(f"Images found for {street_name} in bounding box {bbox.bounds}")
                else:
                    print(f"No images found for {street_name}, considered uncovered in bounding box {bbox.bounds}")

                    if all(osmid != section.get('osmid', 'N/A') for sections in self.uncovered_streets.values() for section in sections):
                        self.uncovered_streets.setdefault(street_name, []).append({'osmid': osmid, 'length': length, 'bbox': bbox.bounds})
                        self.uncovered_coordinates.append({
                            'street_name': street_name,
                            'osmid': osmid,
                            'length': length,
                            'bbox': bbox.bounds
                        })
            else:
                print(f"No images found for {street_name}, considered uncovered in bounding box {bbox.bounds}")

                if all(osmid != section.get('osmid', 'N/A') for sections in self.uncovered_streets.values() for section in sections):
                    self.uncovered_streets.setdefault(street_name, []).append({'osmid': osmid, 'length': length, 'bbox': bbox.bounds})
                    self.uncovered_coordinates.append({
                        'street_name': street_name,
                        'osmid': osmid,
                        'length': length,
                        'bbox': bbox.bounds
                    })

    def get_uncovered_streets(self):
        uncovered_streets_info = []
        for street, sections in self.uncovered_streets.items():
            for section in sections:
                osmid = section.get('osmid', 'N/A')
                length = section.get('length', 0)
                uncovered_streets_info.append((street, osmid, length))

        return uncovered_streets_info

    def total_uncovered_length(self):
        total_length = sum(section.get('length', 0) for sections in self.uncovered_streets.values() for section in sections)
        return total_length

class UncoveredWayToNodes:
    def __init__(self, app_access_token, uncovered_streets_info):
        self.app_access_token = app_access_token
        self.uncovered_streets_info = uncovered_streets_info
        self.osm_api_url = 'https://api.openstreetmap.org/api/0.6'

    def get_first_and_last_nodes_for_osmid(self, osmid):
        response = requests.get(f'{self.osm_api_url}/way/{osmid}.xml')
        #time.sleep(1.5)  # Adjusted sleep duration to stay below the rate limit
        root = ET.fromstring(response.content)
        way = root.find('way')
        nodes = way.findall('nd')
        first_node_id = nodes[0].get('ref')
        last_node_id = nodes[-1].get('ref')
        return first_node_id, last_node_id

    def get_node_coordinates(self, node_id):
        response = requests.get(f'{self.osm_api_url}/node/{node_id}.xml')
        #time.sleep(1.5)  # Adjusted sleep duration to stay below the rate limit
        root = ET.fromstring(response.content)
        node = root.find('node')
        lat = node.get('lat')
        lon = node.get('lon')
        return float(lat), float(lon)

    def print_first_and_last_nodes_coordinates(self):
        node_coordinates = {}  # Dictionary to store unique nodes and their coordinates

        for street, osmid, length in self.uncovered_streets_info:
            print(f"\nCoordinates for Uncovered Street: {street}, OSMID: {osmid}")
            first_node_id, last_node_id = self.get_first_and_last_nodes_for_osmid(osmid)

            # Get coordinates for the first node
            first_lat, first_lon = self.get_node_coordinates(first_node_id)
            print(f"First Node ID: {first_node_id}, Latitude: {first_lat}, Longitude: {first_lon}")
            node_coordinates[first_node_id] = (first_lat, first_lon)

            # Get coordinates for the last node
            last_lat, last_lon = self.get_node_coordinates(last_node_id)
            print(f"Last Node ID: {last_node_id}, Latitude: {last_lat}, Longitude: {last_lon}")
            node_coordinates[last_node_id] = (last_lat, last_lon)

        # Remove duplicates from the dictionary  (we need to search nodes of each uncovered segments because we don't know if they are connected or not)
        unique_node_coordinates = {node_id: coordinates for node_id, coordinates in node_coordinates.items()}

        return unique_node_coordinates

    def generate_csv(self, node_coordinates, csv_filename):
        with open(csv_filename, 'w', newline='') as csv_file:
            fieldnames = ['ID', 'Lon', 'Lat', 'Open_From', 'Open_To', 'Needed_Amount']
            writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
            writer.writeheader()

            for node_id, (lat, lon) in node_coordinates.items():
                writer.writerow({
                    'ID': node_id,
                    'Lon': lon,
                    'Lat': lat,
                    'Open_From': '2024-02-12 00:01:00',
                    'Open_To': '2098-01-12 23:59:00',
                    'Needed_Amount': 1
                })


if __name__ == "__main__":
    place_name = 'Bruxelles, Belgium'
    total_length = total_street_network_length(place_name)

    print(f"Total length of the street network in {place_name}: {total_length:.2f} kilometers")

    num_streets = int(input("Enter the number of streets: "))

    G, bboxes = create_bounding_boxes_for_osmids(place_name, num_streets)

    app_access_token = 'MLY|123456789'  ############### add here your Mapillary token
    mapillary = Mapillary(app_access_token, G)

    for bbox, street, osmid, length in bboxes:
        mapillary.get_image_data(bbox, street, osmid, length)

    print("\nUncovered Streets with Total Length:")
    for street, sections in itertools.groupby(mapillary.get_uncovered_streets(), key=lambda x: x[0]):
        total_length = sum(section[2] for section in sections) / 1000.0  # Convert meters to kilometers
        print(f"{street}: Total Length: {total_length:.2f} km")

    total_uncovered_km = mapillary.total_uncovered_length() / 1000.0
    print(f"\nTotal kilometers of uncovered streets: {total_uncovered_km:.2f} km")

    top_uncovered_roads = sorted(mapillary.get_uncovered_streets(), key=lambda x: x[2], reverse=True)[:58]
    with open("top_uncovered_roads.txt", "w") as txt_file:
        for road in top_uncovered_roads:
            txt_file.write(f"{road[0]}: OSMID {road[1]}, length: {road[2]:.2f}\n")

    print("Top 58 longest uncovered roads information saved to top_uncovered_roads.txt")


    uncovered_streets_info = mapillary.get_uncovered_streets()

    # Create an instance of UncoveredWayToNodes class
    uncovered_to_nodes = UncoveredWayToNodes(app_access_token, uncovered_streets_info)

    # Print coordinates for the first and last nodes of uncovered streets
    uncovered_to_nodes.print_first_and_last_nodes_coordinates()


    # Retrieve unique node coordinates
    unique_node_coordinates = uncovered_to_nodes.print_first_and_last_nodes_coordinates()

    # Specify the CSV filename
    csv_filename = "input.csv"

    # Generate and save the CSV file using the instance method
    uncovered_to_nodes.generate_csv(unique_node_coordinates, csv_filename)

    print(f"CSV file '{csv_filename}' generated successfully.")
