# Import necessary libraries
import osmium as o
import folium
from folium.plugins import MarkerCluster
import shapely.wkb as wkblib

# Create a WKBFactory instance for handling geometries
wkbfab = o.geom.WKBFactory()

# Define a class for handling amenities in the OSM file
class AmenityMapHandler(o.SimpleHandler):

    def __init__(self):
        super(AmenityMapHandler, self).__init__()
        self.amenities = []

    def add_amenity(self, tags, lon, lat):
        # Check if the object is a bicycle parking amenity
        if 'amenity' in tags and tags['amenity'] == 'bicycle_parking':
            # Extract relevant information and add to the amenities list
            name = tags.get('name', '')
            capacity = tags.get('capacity', '')  # Use get with a default value
            self.amenities.append({
                'name': name,
                'amenity_type': 'bicycle_parking',
                'location': (lat, lon),
                'capacity': capacity
            })

    def node(self, n):
        # Process nodes and add relevant amenities to the list
        if 'amenity' in n.tags:
            self.add_amenity(n.tags, n.location.lon, n.location.lat)

    def area(self, a):
        # Process areas (polygons) and add relevant amenities to the list
        if 'amenity' in a.tags:
            wkb = wkbfab.create_multipolygon(a)
            poly = wkblib.loads(wkb, hex=True)
            centroid = poly.representative_point()
            self.add_amenity(a.tags, centroid.x, centroid.y)

def main():
    # Create an instance of the AmenityMapHandler class
    handler = AmenityMapHandler()

    # Replace 'brussels_capital_region.pbf' with the actual file path
    osmfile = 'brussels_capital_region.pbf'

    # Apply the handler to process the OSM file
    handler.apply_file(osmfile)

    # Create a folium map centered around the first amenity
    if handler.amenities:
        map_center = handler.amenities[0]['location']
        my_map = folium.Map(location=map_center, zoom_start=14)

        # Create a MarkerCluster layer for better performance
        marker_cluster = MarkerCluster().add_to(my_map)

        # Add markers for each bicycle parking amenity
        for amenity in handler.amenities:
            popup_content = f"{amenity['amenity_type']}: {amenity['name']}"
            if amenity['capacity']:
                popup_content += f"<br>Capacity: {amenity['capacity']}"
            folium.Marker(location=amenity['location'],
                          popup=popup_content,
                          icon=None).add_to(marker_cluster)

        # Save the map to an HTML file
        my_map.save('bicycle_parking_map.html')
        print("Bicycle Parking Map saved to 'bicycle_parking_map.html'")
    else:
        print("No bicycle parking amenities found in the specified OSM file.")

    return 0

# Run the main function if the script is executed directly
if __name__ == '__main__':
    
    exit(main())
