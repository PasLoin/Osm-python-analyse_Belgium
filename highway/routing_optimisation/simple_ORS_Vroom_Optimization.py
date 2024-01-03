#this a simple refactored script from exemples of ORS to use the optimisation api (based on VROOM)

import folium
from folium.plugins import BeautifyIcon
import pandas as pd
import openrouteservice as ors

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
                capacity=[capacity],
                time_window=time_window,
                profile='cycling-regular'  # Specify the desired profile     cycling-regular  driving-car        
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
        


# Example usage:
# The expected order for all coordinates arrays is [lon, lat]
# All timings are in seconds
# All distances are in meters
# Time window is in unix format



delivery_map = DeliveryMap(center=[50.8467, 4.3524], zoom=10)
delivery_map.load_deliveries_data('input.csv') #format of csv is ID,Lon,Lat,Open_From,Open_To,Needed_Amount
delivery_map.add_delivery_markers()
delivery_map.add_depot_marker(depot_location=[50.84263, 4.36264])
delivery_map.setup_vehicles(num_vehicles=3, depot_location=[50.84263, 4.36264],
                             capacity=9999, time_window=[1553241600, 1553284800])
delivery_map.setup_deliveries(service_time=1200) #how many time on the drop in secondes
delivery_map.calculate_optimal_route(api_key='MY_API_KEY') #replace by your Openrouteservice API 
delivery_map.add_routes_to_map()
delivery_map.display_map()
delivery_map.save_map()
