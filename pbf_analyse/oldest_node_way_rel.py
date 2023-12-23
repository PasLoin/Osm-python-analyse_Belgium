import osmium
from datetime import datetime, timezone

class OldestEntityHandler(osmium.SimpleHandler):
    def __init__(self):
        super(OldestEntityHandler, self).__init__()
        self.oldest_node = (None, datetime.max.replace(tzinfo=timezone.utc))
        self.oldest_way = (None, datetime.max.replace(tzinfo=timezone.utc))
        self.oldest_relation = (None, datetime.max.replace(tzinfo=timezone.utc))

    def node(self, n):
        timestamp = n.timestamp
        if timestamp and timestamp < self.oldest_node[1]:
            self.oldest_node = (n.id, timestamp)

    def way(self, w):
        timestamp = w.timestamp
        if timestamp and timestamp < self.oldest_way[1]:
            self.oldest_way = (w.id, timestamp)

    def relation(self, r):
        timestamp = r.timestamp
        if timestamp and timestamp < self.oldest_relation[1]:
            self.oldest_relation = (r.id, timestamp)

    def get_oldest_entities(self):
        return self.oldest_node, self.oldest_way, self.oldest_relation

if __name__ == "__main__":
    input_pbf_file = 'belgium-latest.osm.pbf'

    oldest_entity_handler = OldestEntityHandler()

    # Apply the handler to the input file
    oldest_entity_handler.apply_file(input_pbf_file)

    # Get the oldest entities after parsing is complete
    oldest_node, oldest_way, oldest_relation = oldest_entity_handler.get_oldest_entities()

    # Print the results
    print(f"Oldest Node (ID, timestamp): {oldest_node}")
    print(f"Oldest Way (ID, timestamp): {oldest_way}")
    print(f"Oldest Relation (ID, timestamp): {oldest_relation}")
