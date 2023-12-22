import osmium
import csv

class OSMHandler(osmium.SimpleHandler):
    def __init__(self):
        super(OSMHandler, self).__init__()
        self.building_counts = {}

    def way(self, w):
        if 'building' in w.tags:
            building_value = w.tags['building']
            self.building_counts[building_value] = self.building_counts.get(building_value, 0) + 1

# Specify the input PBF file
input_pbf_file = 'belgium-latest.osm.pbf'

# Initialize the OSMHandler and apply it to the input file
handler = OSMHandler()
handler.apply_file(input_pbf_file)

# Specify the output CSV file
output_csv_file = 'building_counts.csv'

# Write the counts to the CSV file
with open(output_csv_file, 'w', newline='') as csvfile:
    csv_writer = csv.writer(csvfile)
    csv_writer.writerow(['Building Tag', 'Count'])  # Write header
    for building_value, count in handler.building_counts.items():
        csv_writer.writerow([building_value, count])

print(f"Building counts saved to {output_csv_file}")
