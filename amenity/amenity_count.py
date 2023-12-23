import osmium
import csv

class OSMHandler(osmium.SimpleHandler):
    def __init__(self):
        super(OSMHandler, self).__init__()
        self.amenity_counts = {}

    def node(self, n):
        if 'amenity' in n.tags:
            amenity_value = n.tags['amenity']
            self.amenity_counts[amenity_value] = self.amenity_counts.get(amenity_value, 0) + 1

    def way(self, w):
        if 'amenity' in w.tags:
            amenity_value = w.tags['amenity']
            self.amenity_counts[amenity_value] = self.amenity_counts.get(amenity_value, 0) + 1

def main(input_pbf_file, output_csv_file):
    handler = OSMHandler()
    handler.apply_file(input_pbf_file)

    with open(output_csv_file, 'w', newline='') as csvfile:
        csv_writer = csv.writer(csvfile)
        csv_writer.writerow(['Amenity Tag', 'Count'])
        for amenity_value, count in handler.amenity_counts.items():
            csv_writer.writerow([amenity_value, count])

    print(f"Amenity occurrence counts saved to {output_csv_file}")

if __name__ == "__main__":
    input_pbf_file = 'belgium-latest.osm.pbf'
    output_csv_file = 'amenity_counts.csv'
    main(input_pbf_file, output_csv_file)
