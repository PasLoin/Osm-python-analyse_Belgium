import osmium as o
import matplotlib.pyplot as plt
from matplotlib.patches import Patch

class OSMDataProcessor:
    def __init__(self, pbf_file_path):
        self.pbf_file_path = pbf_file_path
        self.node_cache = {}
        self.highway_cycleway_ways = {'lit_yes': set(), 'lit_no': set(), 'lit_unknown': set()}

    def process_data(self):
        handler = self.NodeCacheHandler(self.node_cache, self.highway_cycleway_ways)
        handler.apply_file(self.pbf_file_path)

    def plot_cycleways(self):
        if self.node_cache:
            fig, ax = plt.subplots(figsize=(10, 10))

            # Initialize legend labels and colors
            legend_data = {'lit=yes': 'green', 'lit=no': 'black', 'lit=unknown': 'red'}

            # Plot cycleways with lit=yes in green
            for way_nodes in self.highway_cycleway_ways['lit_yes']:
                locations = [location for (_, location) in way_nodes if location != (0, 0)]
                ax.plot(*zip(*locations), color=legend_data['lit=yes'])

            # Plot cycleways with lit=no in black
            for way_nodes in self.highway_cycleway_ways['lit_no']:
                locations = [location for (_, location) in way_nodes if location != (0, 0)]
                ax.plot(*zip(*locations), color=legend_data['lit=no'])

            # Plot cycleways with unknown lit status in red
            for way_nodes in self.highway_cycleway_ways['lit_unknown']:
                locations = [location for (_, location) in way_nodes if location != (0, 0)]
                ax.plot(*zip(*locations), color=legend_data['lit=unknown'])

            ax.set_xlabel('Longitude')
            ax.set_ylabel('Latitude')
            ax.set_title('Cycleways Plot')

            # Manually create legend using Patch objects
            legend_patches = [Patch(color=color, label=label) for label, color in legend_data.items()]
            ax.legend(handles=legend_patches, loc='upper right')

            plt.savefig('cycleways_plot.png')
            print("Cycleways Plot saved to 'cycleways_plot.png'")
        else:
            print("No nodes found in the specified OSM file.")

    class NodeCacheHandler(o.SimpleHandler):
        def __init__(self, node_cache, highway_cycleway_ways):
            super(OSMDataProcessor.NodeCacheHandler, self).__init__()
            self.node_cache = node_cache
            self.highway_cycleway_ways = highway_cycleway_ways

        def node(self, n):
            if n.location.valid():
                node_location = (n.location.lat, n.location.lon)
                self.node_cache[n.id] = {'node_id': n.id, 'location': node_location}

        def way(self, w):
            if 'highway' in w.tags and w.tags['highway'] == 'cycleway':
                lit_value = w.tags.get('lit', 'unknown')
                way_nodes = [(node.ref, self.node_cache.get(node.ref, {'location': (0, 0)})['location']) for node in w.nodes]
                if all(location != (0, 0) for (_, location) in way_nodes):
                    if lit_value == 'yes':
                        self.highway_cycleway_ways['lit_yes'].add(tuple(way_nodes))
                    elif lit_value == 'no':
                        self.highway_cycleway_ways['lit_no'].add(tuple(way_nodes))
                    else:
                        self.highway_cycleway_ways['lit_unknown'].add(tuple(way_nodes))

# Specify the path to the PBF file (replace with your actual path)
pbf_file_path = 'brussels_capital_region.pbf'

# Create the OSMDataProcessor instance
osm_processor = OSMDataProcessor(pbf_file_path)

# Process the data
osm_processor.process_data()

# Create and save the plot
osm_processor.plot_cycleways()
