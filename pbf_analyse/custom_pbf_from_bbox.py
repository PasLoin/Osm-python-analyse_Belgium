import requests
import osmium

url = "https://download.geofabrik.de/europe/belgium-latest.osm.pbf"
r = requests.get(url)
with open("belgium-latest.osm.pbf", "wb") as f:
    f.write(r.content)

class ExtractHandler(osmium.SimpleHandler):
    def __init__(self, bbox, output_pbf):
        super(ExtractHandler, self).__init__()
        self.bbox = bbox
        self.output_pbf = output_pbf
        self.writer = osmium.SimpleWriter(self.output_pbf)

    def node(self, n):
        if self.bbox[0] <= n.location.lon <= self.bbox[2] and \
           self.bbox[1] <= n.location.lat <= self.bbox[3]:
            self.writer.add_node(n)

    def way(self, w):
        self.writer.add_way(w)

    def relation(self, r):
        self.writer.add_relation(r)

if __name__ == "__main__":
    bbox = (4.336338, 50.831963, 4.370499, 50.858410)  # Replace with your desired bounding box coordinates http://bboxfinder.com   
    input_pbf_file = "belgium-latest.osm.pbf"
    output_pbf_file = "brussels-pentagone.osm.pbf"  # Replace with your desired name

    handler = ExtractHandler(bbox, output_pbf_file)
    handler.apply_file(input_pbf_file)
    handler.writer.close()  
