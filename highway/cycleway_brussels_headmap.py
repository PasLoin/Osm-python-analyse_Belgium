import osmium
import folium
from folium.plugins import HeatMap

class NodeInfoHandler(osmium.SimpleHandler):
    def __init__(self, filter_tag):
        super(NodeInfoHandler, self).__init__()
        self.filter_tag = filter_tag
        self.way_nodes = []  # List to store all cycleway nodes

    def way(self, w):
        # Check if the current way has the specified tag
        if self.filter_tag in w.tags and w.tags[self.filter_tag] == 'cycleway':
            try:
                way_nodes = [(node.location.lat, node.location.lon) for node in w.nodes]
                self.way_nodes.extend(way_nodes)
            except osmium.InvalidLocationError:
                print(f"Invalid location in Way ID {w.id}. Skipping...")

    def node(self, n):
        pass

    def plot_combined_heatmap(self):
        # Create a folium map centered around the first node in the cycleway
        map_center = self.way_nodes[0]
        my_map = folium.Map(location=map_center, zoom_start=14)

        # Create a heatmap layer using the cycleway nodes
        HeatMap(self.way_nodes).add_to(my_map)

        # Save the combined heatmap to an HTML file
        my_map.save('combined_cycleways_heatmap.html')
        print("Combined Cycleways Heatmap saved to 'combined_cycleways_heatmap.html'")

# Specify the filter tag and create a NodeInfoHandler instance
filter_tag = 'highway'
node_info_handler = NodeInfoHandler(filter_tag)

# Apply the PBF file to find ways with the specified tag
node_info_handler.apply_file("brussels_capital_region.pbf", locations=True, idx='flex_mem')

# Plot a combined heatmap for all cycleways found
node_info_handler.plot_combined_heatmap()
