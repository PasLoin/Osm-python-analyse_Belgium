import os
import osmium
import geopandas as gpd
import matplotlib.pyplot as plt
import contextily as ctx
from shapely.geometry import Point

class OSMHandler(osmium.SimpleHandler):
    def __init__(self):
        super(OSMHandler, self).__init__()
        self.tagged_locations = []

    def way(self, w):
        if 'cuisine' in w.tags and w.tags['cuisine'] == 'friture':
            # Calculate the centroid of the way by averaging node coordinates
            lon_sum, lat_sum = 0, 0
            num_nodes = len(w.nodes)
            for node in w.nodes:
                if node.location.valid():
                    lon_sum += node.location.lon
                    lat_sum += node.location.lat
            if num_nodes > 0 and lon_sum != 0 and lat_sum != 0:  # Check for valid coordinates
                centroid_lon = lon_sum / num_nodes
                centroid_lat = lat_sum / num_nodes
                self.tagged_locations.append(Point(centroid_lon, centroid_lat))

    def node(self, n):
        if 'cuisine' in n.tags and n.tags['cuisine'] == 'friture':
            if n.location.valid():
                self.tagged_locations.append(Point(n.location.lon, n.location.lat))

# Print current working directory
print("Current working directory:", os.getcwd())

# Download OSM data
os.system("wget https://download.geofabrik.de/europe/belgium-latest.osm.pbf -O graphs/belgium-latest.osm.pbf")

# Specify the input PBF file
input_pbf_file = 'graphs/belgium-latest.osm.pbf'

# Initialize the OSMHandler and apply it to the input file
handler = OSMHandler()
handler.apply_file(input_pbf_file)

# Create a GeoDataFrame from the tagged locations
gdf = gpd.GeoDataFrame(geometry=handler.tagged_locations)

# Set the coordinate reference system explicitly
gdf.crs = 'EPSG:4326'

# Reproject the GeoDataFrame to Web Mercator (EPSG:3857)
gdf = gdf.to_crs(epsg=3857)

# Print the output path
output_path = os.path.abspath("graphs/fritures.jpg")
print("Saving to:", output_path)

# Plot the GeoDataFrame with a background basemap using contextily
ax = gdf.plot(figsize=(10, 10), color='red', marker='o', markersize=50, alpha=0.5)

# Add a background basemap
ctx.add_basemap(ax, crs=gdf.crs, source=ctx.providers.OpenStreetMap.Mapnik)

# Save the resulting map
plt.savefig(output_path)

# Print a success message
print("File saved successfully!")

# Show the plot (optional)
plt.show()
