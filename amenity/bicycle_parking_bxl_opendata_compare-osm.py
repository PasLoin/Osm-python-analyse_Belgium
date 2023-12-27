# This script analyse opendata about bicycle parking in Brussels
# input file can be found here : https://datastore.brussels/web/data/dataset/9ae57108-6bc4-4793-bd8e-93c1d28e1183
# can also work for https://datastore.brussels/web/data/dataset/39b2a24f-7263-42a1-b381-1a70d2098a06 but you need to change capacity
# <tag k="capacity" v="{json_node["properties"].get("capacity", "")}"/>\n') by get capacity_classic

import json
import pandas as pd
import osmium as o
from geopy.distance import geodesic
from pyproj import Transformer

class BicycleParkingHandler(o.SimpleHandler):
    def __init__(self):
        super(BicycleParkingHandler, self).__init__()
        self.bicycle_parking_cache = {}

    def node(self, n):
        if n.location.valid():
            tags = dict(n.tags)
            if 'amenity' in tags and tags['amenity'] == 'bicycle_parking':
                node_location = (n.location.lat, n.location.lon)
                self.bicycle_parking_cache[n.id] = {'node_id': n.id, 'location': node_location, 'tags': tags}

class BicycleParkingMatcher:
    def __init__(self, json_file_path, pbf_file_path, output_csv_file_path, unmatched_osm_file_path):
        self.json_file_path = json_file_path
        self.pbf_file_path = pbf_file_path
        self.output_csv_file_path = output_csv_file_path
        self.unmatched_osm_file_path = unmatched_osm_file_path
        self.handler = BicycleParkingHandler()
        self.matched_df = None

    def read_json_data(self):
        print("Reading JSON data...")
        with open(self.json_file_path, 'r') as json_file:
            json_data = json.load(json_file)
            for feature in json_data['features']:
                coordinates = feature['geometry']['coordinates']
                lon, lat = self.transform_coordinates(coordinates[0], coordinates[1])
                feature['geometry']['coordinates'] = [lat, lon]
        return json_data['features']

    def read_osm_data(self):
        print("Reading OSM data...")
        osm_file = o.io.Reader(self.pbf_file_path)
        o.apply(osm_file, self.handler)
        osm_file.close()

    def match_bicycle_parking(self, bicycle_parking_nodes, max_data_count, threshold_meters):
        print("Matching bicycle parking nodes...")
        matched_data = []

        for json_node in bicycle_parking_nodes[:max_data_count]:
            json_location = tuple(json_node['geometry']['coordinates'])

            for osm_node_id, osm_node_info in self.handler.bicycle_parking_cache.items():
                osm_location = osm_node_info['location']
                distance = geodesic(json_location, osm_location).meters

                if distance < threshold_meters:
                    matched_data.append({
                        'JSON_ID': json_node['id'],
                        'OSM_Node_ID': osm_node_id,
                        'Distance': distance
                    })
                    print(f"Bicycle parking found - OSM ID: {osm_node_id}, JSON ID: {json_node['id']}, Distance: {distance} meters")

        self.matched_df = pd.DataFrame(matched_data)
        self.matched_df.to_csv(self.output_csv_file_path, index=False)
        print(f"Matching data saved to {self.output_csv_file_path}")

    def generate_unmatched_osm_file(self, max_data_count, threshold_meters):
        print(f"Generating Unmatched OSM file for the first {max_data_count} bicycle parking nodes...")
        with open(self.unmatched_osm_file_path, 'w') as osm_file:
            osm_file.write('<?xml version="1.0" encoding="UTF-8"?>\n')
            osm_file.write('<osm version="0.6" generator="BicycleParkingMatcher">\n')

            node_id_counter = 1
            for json_node in bicycle_parking_nodes[:max_data_count]:
                json_location = tuple(json_node['geometry']['coordinates'])
                is_matched = False

                for osm_node_id, osm_node_info in self.handler.bicycle_parking_cache.items():
                    osm_location = osm_node_info['location']
                    distance = geodesic(json_location, osm_location).meters

                    if distance < threshold_meters:
                        is_matched = True
                        break

                if not is_matched:
                    lat, lon = json_location
                    osm_file.write(f'  <node id="{node_id_counter}" lat="{lat}" lon="{lon}" version="1">\n')
                    osm_file.write(f'    <tag k="amenity" v="bicycle_parking"/>\n')
                    osm_file.write(f'    <tag k="capacity" v="{json_node["properties"].get("capacity", "")}"/>\n')
                    osm_file.write('  </node>\n')
                    node_id_counter += 1

            osm_file.write('</osm>\n')
            print(f"Unmatched OSM file generated and saved to {self.unmatched_osm_file_path}")

    def transform_coordinates(self, x, y):
        in_proj = 'EPSG:31370'
        out_proj = 'EPSG:4326'
        transformer = Transformer.from_crs(in_proj, out_proj, always_xy=True)
        return transformer.transform(x, y)

if __name__ == "__main__":
    json_path = 'geoserver-GetFeature.application.json'
    pbf_path = 'brussels_capital_region.pbf'
    output_csv_path = 'matched_bicycle_parking.csv'
    unmatched_osm_path = 'unmatched_bicycle_parking.osm'

    max_data_count = int(input("Enter the maximum data count: "))
    threshold_meters = float(input("Enter the threshold distance in meters: "))

    bike_parking_matcher = BicycleParkingMatcher(
        json_path, pbf_path, output_csv_path, unmatched_osm_path
    )

    bicycle_parking_nodes = bike_parking_matcher.read_json_data()
    bike_parking_matcher.read_osm_data()
    bike_parking_matcher.match_bicycle_parking(bicycle_parking_nodes, max_data_count, threshold_meters)
    bike_parking_matcher.generate_unmatched_osm_file(max_data_count, threshold_meters)
