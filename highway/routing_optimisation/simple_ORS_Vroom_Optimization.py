# this a simple refactored script from exemples of ORS to use the optimisation api (based on VROOM)
# Optimized route is saved on a gpx file
import folium
from folium.plugins import BeautifyIcon
import pandas as pd
import openrouteservice as ors
import json
import polyline
from gpxpy.gpx import GPX, GPXTrack, GPXTrackSegment, GPXTrackPoint, GPXWaypoint

class DeliveryMap:
    def __init__(self, center, zoom):
        self.map = folium.Map(location=center, tiles='OpenStreetMap', zoom_start=zoom)
        self.depot = None
        self.deliveries_data = None
        self.vehicles = None
        self.deliveries = None
        self.result = None

    def load_deliveries_data(self, csv_path):
        self.deliveries_data = pd.read_csv(
            csv_path,
            index_col="ID",
            parse_dates=["Open_From", "Open_To"]
        )

    def add_delivery_markers(self):
        for location in self.deliveries_data.itertuples():
            tooltip = folium.map.Tooltip("<h4><b>ID {}</b></p><p>Supplies needed: <b>{}</b></p>".format(
                location.Index, location.Needed_Amount
            ))

            folium.Marker(
                location=[location.Lat, location.Lon],
                tooltip=tooltip,
                icon=BeautifyIcon(
                    icon_shape='marker',
                    number=int(location.Index),
                    spin=True,
                    text_color='red',
                    background_color="#FFF",
                    inner_icon_style="font-size:12px;padding-top:-5px;"
                )
            ).add_to(self.map)

    def add_depot_marker(self, depot_location):
        self.depot = folium.Marker(
            location=depot_location,
            icon=folium.Icon(color="green", icon="bicycle", prefix='fa'),
            setZIndexOffset=1000
        )
        self.depot.add_to(self.map)

    def setup_vehicles(self, num_vehicles, depot_location, capacity, time_window):
        self.vehicles = [
            ors.optimization.Vehicle(
                id=idx,
                start=list(reversed(depot_location)),
                end=list (reversed(depot_location)), #stop position remove this line if you want the vehicle stop on last delivery
                capacity=[capacity],
                time_window=time_window,
                profile='cycling-regular'  # Specify the desired profile "driving-car", "driving-hgv", "foot-walking","foot-hiking", "cycling-regular", "cycling-road","cycling-mountain", "cycling-electric"        
            )
            for idx in range(num_vehicles)
        ]

    def setup_deliveries(self, service_time):
        self.deliveries = [
            ors.optimization.Job(
                id=delivery.Index,
                location=[delivery.Lon, delivery.Lat],
                service=service_time,
                amount=[delivery.Needed_Amount],
                time_windows=[[
                    int(delivery.Open_From.timestamp()),  # VROOM expects UNIX timestamp
                    int(delivery.Open_To.timestamp())
                ]]
            )
            for delivery in self.deliveries_data.itertuples()
        ]
 

    def calculate_optimal_route(self, api_key):
        ors_client = ors.Client(key=api_key)

        # Print the request content
        print("API Request Content:")
        print({
            'jobs': [job.__dict__ for job in self.deliveries],
            'vehicles': [vehicle.__dict__ for vehicle in self.vehicles],
            'geometry': True
        })

        
        self.result = ors_client.optimization(jobs=self.deliveries, vehicles=self.vehicles, geometry=True)
        # Print the API response
        print("API Response:")
        print(self.result)
        
        # Save result to a file
        with open('result.json', 'w') as f:
            json.dump(self.result, f)       
    
    def add_routes_to_map(self):
        for color, route in zip(['green', 'red', 'blue'], self.result['routes']):
            decoded = ors.convert.decode_polyline(route['geometry'])
            gj = folium.GeoJson(
                name='Vehicle {}'.format(route['vehicle']),
                data={"type": "FeatureCollection", "features": [{"type": "Feature",
                                                                 "geometry": decoded,
                                                                 "properties": {"color": color}
                                                                 }]},
                style_function=lambda x: {"color": x['properties']['color']}
            )
            gj.add_child(folium.Tooltip(
                """<h4>Vehicle {vehicle}</h4>
                <b>Distance</b> {distance} m <br>
                <b>Duration</b> {duration} secs
                """.format(**route)
            ))
            gj.add_to(self.map)

    def display_map(self):
        folium.LayerControl().add_to(self.map)
        self.map
    def save_map(self, filename='delivery_map.html'):
        self.map.save(filename)
        print(f"Map saved as {filename}")

class GPXConverter:
    def __init__(self, json_file, gpx_file):
        self.json_file = json_file
        self.gpx_file = gpx_file
        self.gpx = GPX()

    def load_json(self):
        with open(self.json_file, 'r') as f:
            self.data = json.load(f)
        print(f"Loaded data from {self.json_file}")

    def decode_geometry(self):
        for route in self.data.get('routes', []):
            geometry = polyline.decode(route.get('geometry', ''))
            print(f"Decoded {len(geometry)} points from the geometry")
            yield geometry

    def add_track_segment(self, geometry):
        track = GPXTrack()
        self.gpx.tracks.append(track)
        segment = GPXTrackSegment()
        track.segments.append(segment)
        for point in geometry:
            segment.points.append(GPXTrackPoint(latitude=point[0], longitude=point[1]))
        print(f"Added {len(segment.points)} points to the GPX track segment")

    def add_waypoints(self):
        for route in self.data.get('routes', []):
            for step in route.get('steps', []):
                if step['type'] == 'job':
                    waypoint = GPXWaypoint(latitude=step['location'][0], longitude=step['location'][1])
                    waypoint.name = f"Job {step['id']} Number of Waypoints remaining {step['load'][0]}"
                    waypoint.description = f"Distance: {step['distance']/1000} kilometers"
                    self.gpx.waypoints.append(waypoint)
        print(f"Added {len(self.gpx.waypoints)} waypoints to the GPX file")

    def write_gpx(self):
        with open(self.gpx_file, 'w') as f:
            f.write(self.gpx.to_xml())
        print(f"Wrote GPX data to {self.gpx_file}")

    def convert(self):
        self.load_json()
        for geometry in self.decode_geometry():
            self.add_track_segment(geometry)
        self.add_waypoints()
        self.write_gpx()

# Usage Optimmization 
# The expected order for all coordinates arrays in ORS is [lon, lat]
# All timings are in seconds
# All distances are in meters
# Time window is in unix format

delivery_map = DeliveryMap(center=[50.8467, 4.3524], zoom=10)
delivery_map.load_deliveries_data('input.csv') #format of csv is ID,Lon,Lat,Open_From,Open_To,Needed_Amount #### MAXIMUM 58 entry if using API
delivery_map.add_delivery_markers()
delivery_map.add_depot_marker(depot_location=[50.84263, 4.36264])
delivery_map.setup_vehicles(num_vehicles=3, depot_location=[50.84263, 4.36264],
                             capacity=9999, time_window=[1553241600, 1553284800])
delivery_map.setup_deliveries(service_time=0) #default 0
delivery_map.calculate_optimal_route(api_key='MY_API_KEY') ########### Replace by your Openrouteservice API 
delivery_map.add_routes_to_map()
delivery_map.display_map()
delivery_map.save_map()
# Usage GPX 
# 
converter = GPXConverter('result.json', 'result.gpx')
converter.convert()
