# This script read a list of streets in a txt format (separated by a return) find them in the provided pbf file. (read name:fr)
# Generate a distance matrix file (can be use on other tsp solver) 
# Perform a TSP resolution with OR-Tools and return the streets name in ordered solution.
# Export the result to a gpx file ( including all nodes of each street : you should post-process the gpx file with tools like ORS or https://brouter.m11n.de)
# Purpose of gpx file is to ensure that all street parts are covered for a ground survey. <trkpt inside street section IS NOT optimised and it's not the goal of this script

import numpy as np
import osmium
import networkx as nx
import folium
import gpxpy
import gpxpy.gpx
from ortools.sat.python import cp_model
from geopy.distance import geodesic
import seaborn as sns
import matplotlib.pyplot as plt

route_info2 = {'Street Name Route': None}

class OSMStreetGraphBuilder(osmium.SimpleHandler):
    def __init__(self, street_names, pbf_file):
        super(OSMStreetGraphBuilder, self).__init__()
        self.street_names = street_names
        self.pbf_file = pbf_file
        self.street_graph = nx.Graph()
        self.node_locations = {}

    def way(self, w):
        street_name = None

        if 'name:fr' in w.tags:
            street_name = w.tags['name:fr']
        elif 'name' in w.tags:
            street_name = w.tags['name']

        if street_name is None or street_name not in self.street_names:
            return  # Skip the street if no suitable name is found or it's not in the list

        osm_id = w.id
        coords = [(self.node_locations[n.ref].lat, self.node_locations[n.ref].lon)
                  for n in w.nodes if n.ref in self.node_locations]

        if not coords or any(None in coord[:2] for coord in coords):
            print(f"Skipping way {osm_id} for street {street_name} due to missing or invalid coordinates.")
            return

        if street_name not in self.street_graph:
            self.street_graph.add_node(street_name, osm_ids=[], coords=[])
        self.street_graph.nodes[street_name]['osm_ids'].append(osm_id)
        self.street_graph.nodes[street_name]['coords'].extend(coords)

    def node(self, n):
        self.node_locations[n.id] = n.location

    def relation(self, r):
        pass

    def build_street_graph(self):
        self.apply_file(self.pbf_file)

    def haversine(self, coord1, coord2):
        return geodesic(coord1, coord2).meters

    def build_distance_matrix(self):
        num_nodes = len(self.street_graph.nodes)
        distance_matrix = np.zeros((num_nodes, num_nodes))

        for i, (street_name_i, data_i) in enumerate(self.street_graph.nodes(data=True)):
            for j, (street_name_j, data_j) in enumerate(self.street_graph.nodes(data=True)):
                if i != j and 'coords' in data_i and 'coords' in data_j:
                    coord_i, coord_j = data_i['coords'][0], data_j['coords'][0]
                    distance = self.haversine(coord_i, coord_j) / 1000.0  # Convert meters to kilometers
                    distance_matrix[i, j] = distance

        return distance_matrix


class OSMStreetGraphPlotter:
    def __init__(self, street_graph):
        self.street_graph = street_graph

    def plot_street_graph(self):
        pos = {}
        for street_name, data in self.street_graph.nodes(data=True):
            if 'coords' in data and all(len(coord) == 2 for coord in data['coords']):
                sorted_coords = sorted(data['coords'], key=lambda x: (x[0], x[1]))
                pos[street_name] = tuple(sorted_coords[0])

        valid_nodes = [node for node in self.street_graph.nodes if node in pos]
        street_graph_sub = self.street_graph.subgraph(valid_nodes)

        center = pos[list(street_graph_sub.nodes)[0]]
        m = folium.Map(location=center, zoom_start=15)

        for street_name, data in street_graph_sub.nodes(data=True):
            for coord in data['coords']:
                lat, lon = coord
                osm_id = data['osm_ids'][0] if data['osm_ids'] else None
                node_id_str = f"Node ID: {osm_id}" if osm_id is not None else "Node ID: N/A"
                folium.CircleMarker(location=(lat, lon), radius=2, color='black', fill=True, fill_color='black',
                                    popup=f"{street_name}\n{node_id_str}\nLat: {lat}\nLon: {lon}").add_to(m)

        m.save('street_nodes.html')
        print("Map generated and saved as 'street_nodes.html.'")

    def export_sorted_segments_to_gpx(self, sorted_segments, output_filename='sorted_segments.gpx'):
        gpx = gpxpy.gpx.GPX()

        for street_name, data in sorted_segments:
            if 'coords' not in data:
                continue
            for i, coord in enumerate(data['coords']):
                gpx_waypoint = gpxpy.gpx.GPXWaypoint(coord[0], coord[1], elevation=0)
                gpx_waypoint.name = f"{street_name} - Node {i + 1}"
                gpx.waypoints.append(gpx_waypoint)

        with open(output_filename, 'w', encoding='utf-8') as gpx_file:
            gpx_file.write(gpx.to_xml())

        print(f"Sorted segments exported to '{output_filename}'.")


def main():
    with open('rues.txt', 'r', encoding='utf-8') as street_file:
        street_names = street_file.read().splitlines()

    pbf_file = 'brussels_capital_region.pbf'
    builder = OSMStreetGraphBuilder(street_names, pbf_file)
    builder.build_street_graph()
    street_graph = builder.street_graph

    print("Street graph built successfully.")

    plotter = OSMStreetGraphPlotter(street_graph)
    plotter.plot_street_graph()

    # Calculate distance matrix using the build_distance_matrix method
    distance_matrix = builder.build_distance_matrix()

    # Save matrix to txt file for future use
    with open('distance_matrix.txt', 'w') as file:
        file.write('[')
        for row in distance_matrix:
            file.write('[' + ', '.join(f'{value:.8f}' for value in row) + '],\n')
        file.write(']')

    # Solve TSP
    with open('distance_matrix.txt', 'r') as file:
        distance_matrix_str = file.read()

    DISTANCE_MATRIX = eval(distance_matrix_str)

    num_nodes = len(DISTANCE_MATRIX)
    all_nodes = range(num_nodes)
    print("Num Streets =", num_nodes)

    # Model.
    model = cp_model.CpModel()

    obj_vars = []
    obj_coeffs = []

    # Create the circuit constraint.
    arcs = []
    arc_literals = {}
    for i in all_nodes:
        for j in all_nodes:
            if i == j:
                continue

            lit = model.NewBoolVar("%i follows %i" % (j, i))
            arcs.append((i, j, lit))
            arc_literals[i, j] = lit

            obj_vars.append(lit)
            obj_coeffs.append(DISTANCE_MATRIX[i][j])

    model.AddCircuit(arcs)


    # Minimize weighted sum of arcs.
    model.Minimize(sum(obj_vars[i] * obj_coeffs[i] for i in range(len(obj_vars))))

    # Solve and print out the solution.
    solver = cp_model.CpSolver()
    solver.parameters.log_search_progress = True
    solver.parameters.linearization_level = 2

    solver.Solve(model)
    print(solver.ResponseStats())

    current_node = 0
    num_order_route = "%i" % current_node
    street_name_route = street_names[current_node]
    route_is_finished = False
    route_distance = 0

    while not route_is_finished:
        for i in all_nodes:
            if i == current_node:
                continue
            if solver.BooleanValue(arc_literals[current_node, i]):
                num_order_route += f" -> {i}"
                street_name_route += f" -> {street_names[i]}"
                route_distance += DISTANCE_MATRIX[current_node][i]
                current_node = i
                if current_node == 0:
                    route_is_finished = True
                break
   
            
    print("Numerical Order Route:", num_order_route)
    print("Street Name Route:", street_name_route)
    print("Travelled distance as the crow flies : ", route_distance)

   
    route_info2['Street Name Route'] = street_name_route

    # Extract street names directly from Street Name Route
    cleaned_street_names = [name.strip() for name in route_info2['Street Name Route'].split("->")]

    # Create a new dictionary with the extracted street names
    street_names_dict = {'Street Names': cleaned_street_names}


    for cleaned_street_name in cleaned_street_names:
        if cleaned_street_name in street_graph.nodes:
            street_data = street_graph.nodes[cleaned_street_name]

    # Create a GPX object
    gpx = gpxpy.gpx.GPX()

    # Add a track to the GPX object
    track = gpxpy.gpx.GPXTrack()
    gpx.tracks.append(track)

    # Add a segment to the track
    segment = gpxpy.gpx.GPXTrackSegment()
    track.segments.append(segment)

# Set to keep track of unique coordinates
    unique_coords = set()


    # Add waypoints to the segment
    for cleaned_street_name in cleaned_street_names:
        if cleaned_street_name in street_graph.nodes:
            street_data = street_graph.nodes[cleaned_street_name]
            for coord in street_data['coords']:
                # Check for duplicates before adding to the set
                if coord not in unique_coords:
                    waypoint = gpxpy.gpx.GPXWaypoint(coord[0], coord[1])
                    waypoint.name = cleaned_street_name
                    segment.points.append(waypoint)
                    unique_coords.add(coord)

    # Save the GPX file
    output_filename = 'output.gpx'
    with open(output_filename, 'w', encoding='utf-8') as gpx_file:
        gpx_file.write(gpx.to_xml())

    print(f"GPX file saved as '{output_filename}'.")
    


if __name__ == '__main__':
    main()
