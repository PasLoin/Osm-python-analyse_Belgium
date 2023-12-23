import osmium
import csv

class OSMHandler(osmium.SimpleHandler):
    def __init__(self):
        super(OSMHandler, self).__init__()
        self.building_counts = {}

    def way(self, w):
        if 'building' in w.tags:
            building_value = w.tags['building']
            if building_value in self.building_counts:
                self.building_counts[building_value]['count'] += 1
            else:
                self.building_counts[building_value] = {'count': 1, 'osmid': w.id}

def main(input_pbf_file, output_csv_file):
    handler = OSMHandler()
    handler.apply_file(input_pbf_file)

    filtered_counts = {k: v for k, v in handler.building_counts.items() if v['count'] == 1}

    with open(output_csv_file, 'w', newline='') as csvfile:
        csv_writer = csv.writer(csvfile)
        csv_writer.writerow(['Building Tag', 'Count', 'OSM ID'])
        for building_value, data in filtered_counts.items():
            csv_writer.writerow([building_value, data['count'], data['osmid']])

    print(f"Filtered building counts (count=1) with OSM ID saved to {output_csv_file}")

if __name__ == "__main__":
    input_pbf_file = 'belgium-latest.osm.pbf'
    output_csv_file = 'building_counts.csv'
    main(input_pbf_file, output_csv_file)
