import osmium as o
import matplotlib.pyplot as plt

class NodeCacheHandler(o.SimpleHandler):
    def __init__(self):
        super(NodeCacheHandler, self).__init__()
        self.node_cache = {}
        self.highway_footway_ways = set()

    def node(self, n):
        if n.location.valid():
            node_location = (n.location.lat, n.location.lon)
            self.node_cache[n.id] = {'node_id': n.id, 'location': node_location}

    def way(self, w):
        # Process each way in the OSM data
        if 'highway' in w.tags and w.tags['highway'] == 'footway':
            way_nodes = [(node.ref, self.node_cache.get(node.ref, {'location': (0, 0)})['location']) for node in w.nodes]
            if all(location != (0, 0) for (_, location) in way_nodes):
                self.highway_footway_ways.add(tuple(way_nodes))

# Specify the path to the PBF file (replace with your actual path)
pbf_file_path = 'brussels_capital_region.pbf'

# Create the NodeCacheHandler instance
handler = NodeCacheHandler()

# Apply the OSM data to the handler to create the node cache
handler.apply_file(pbf_file_path)

# Create a plot using matplotlib
if handler.node_cache:
    fig, ax = plt.subplots(figsize=(10, 10))

    # Plot each footway on the map, excluding locations with (0, 0)
    for way_nodes in handler.highway_footway_ways:
        locations = [location for (_, location) in way_nodes if location != (0, 0)]
        ax.plot(*zip(*locations), color='blue')

    # Set plot parameters if needed
    ax.set_xlabel('Longitude')
    ax.set_ylabel('Latitude')
    ax.set_title('Footways Plot')

    # Save the plot to a PNG file
    plt.savefig('footways_plot.png')
    print("Footways Plot saved to 'footways_plot.png'")
else:
    print("No nodes found in the specified OSM file.")
