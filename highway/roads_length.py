import osmium as o
import sys

class RoadLengthHandler(o.SimpleHandler):
    def __init__(self):
        super(RoadLengthHandler, self).__init__()
        self.length = 0.0

    def way(self, w):
        if 'highway' in w.tags:
            try:
                self.length += o.geom.haversine_distance(w.nodes)
            except o.InvalidLocationError:
                # A location error might occur if the osm file is an extract
                # where nodes of ways near the boundary are missing.
                print("WARNING: way %d incomplete. Ignoring." % w.id)

def main():
    input_pbf_file = 'belgium-latest.osm.pbf'  # Set the PBF file path here
    h = RoadLengthHandler()
    # As we need the geometry, the node locations need to be cached. Therefore
    # set 'locations' to true.
    h.apply_file(input_pbf_file, locations=True)

    total_length_km = h.length / 1000

    # Save the result to a file
    output_file_path = 'output.txt'
    with open(output_file_path, 'w') as output_file:
        output_file.write('Total way length: %.2f km' % total_length_km)
    
    print(f'Result written to {output_file_path}')
    
    sys.exit(0)  # Terminate the script

if __name__ == '__main__':
    main()
