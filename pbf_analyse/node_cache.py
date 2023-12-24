import osmium as o

class NodeCacheHandler(o.SimpleHandler):
    def __init__(self):
        super(NodeCacheHandler, self).__init__()
        self.node_cache = {}

    def node(self, n):
        # Process each node in the OSM data
        if n.location.valid():
            # If the node has a valid location, add it to the node cache
            node_location = (n.location.lat, n.location.lon)
            self.node_cache[n.id] = {'node_id': n.id, 'location': node_location}
        else:
            # For nodes with invalid locations, you may choose to handle differently or skip them
            print(f"Node ID: {n.id}, Location: Invalid")

# Create the NodeCacheHandler instance
handler = NodeCacheHandler()

# Specify the path to the PBF file (replace with your actual path)
pbf_file_path = 'brussels_capital_region.pbf'

# Create an OSM file reader for the PBF file
osm_file = o.io.Reader(pbf_file_path)

# Apply the OSM data to the handler
o.apply(osm_file, handler)

# Close the OSM file reader
osm_file.close()

# Prompt the user to enter a node ID
while True:
    user_input = input("Enter Node ID (or 'exit' to quit): ")
    if user_input.lower() == 'exit':
        break
    
    try:
        # Try to convert the user input to an integer (node ID)
        node_id = int(user_input)
        # Retrieve node information from the cache based on the entered node ID
        node_info = handler.node_cache.get(node_id)
        if node_info:
            # If node information exists, print it
            print(f"Node ID: {node_info['node_id']}, Location: {node_info['location']}")
        else:
            # If node ID is not found in the cache, inform the user
            print(f"Node ID {node_id} not found in the cache.")
    except ValueError:
        # Handle cases where the user input is not a valid integer
        print("Invalid input. Please enter a valid integer or 'exit'.")
