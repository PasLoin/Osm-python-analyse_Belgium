###### This script extract openstreetmap data dynamicly #see configuration list # and create a sqlite3 database.
### FEATURE_TAG : list of tag or tag=value data from the pbf set to define what we want in our db.
### FIXED_TAGS : list of tag converted in colomn
### "hardcoded tag" : OsmType / Osmid / Geometry (WKT) / OsmTags (all others tags  in one colomn except ignored tag)
### IGNORE_TAGS : tag we don't want to include in OsmTags colomn.
######

#!pip install osmium
import osmium
import pandas as pd
from shapely.geometry import Point, Polygon, MultiPolygon
import sqlite3
import re
import json
!wget -O input.pbf https://download.openstreetmap.fr/extracts/europe/belgium/brussels_capital_region-latest.osm.pbf

# -- Configuration List --

FEATURE_TAGS = [
    "shop",
    "amenity=restaurant",
    "amenity=fast_food",
    "amenity=pub",
    "amenity=bar",
    "amenity=cafe",
    "tourism=museum"
]

FIXED_TAGS = [
    "name",
    "shop",
    "amenity",
    "opening_hours",
    "description",
    "official_name",
    "alt_name",
    "contact:website",
    "contact:email",
    "contact:phone",
    "tourism"
]

IGNORE_TAGS = [
    "bus",
    "pergola",
    "website",
    "was:name",
    "check_date:opening_hours",
    "opening_hours:signed"
]

# ------------------------

class ShopHandler(osmium.SimpleHandler):
    def __init__(self, feature_tags, fixed_tags, ignore_tags):
        super(ShopHandler, self).__init__()
        self.nodes = {}
        self.ways = {}
        self.shop_data = []
        self.feature_tags = feature_tags
        self.fixed_tags = fixed_tags
        self.ignore_tags = ignore_tags
        self.feature_columns = set()  # Track feature tag keys as columns
        self._extract_feature_columns() # Extract before any use

    def _extract_feature_columns(self):
        """Extract feature tag keys for column creation."""
        for tag_filter in self.feature_tags:
          if "=" in tag_filter:
            key, _ = tag_filter.split("=")
            self.feature_columns.add(key)
          else:
            self.feature_columns.add(tag_filter)

    def _check_tags(self, tags):
        """Helper function to check if any of the defined tags are present."""
        for tag_filter in self.feature_tags:
            if "=" in tag_filter:
                key, value = tag_filter.split("=")
                if key in tags and tags[key] == value:
                    return True
            elif tag_filter in tags:
                return True
        return False

    def _extract_tags(self, tags):
        """Extracts fixed tags, feature tags, remaining tags, and creates the osm_tags dict."""
        fixed_data = {tag: tags.get(tag) for tag in self.fixed_tags}
        feature_data = {key: tags.get(key) for key in self.feature_columns}
        osm_tags = {k: v for k, v in tags.items() if k not in self.fixed_tags and k not in self.ignore_tags and k not in self.feature_columns}

        return feature_data, fixed_data, osm_tags

    def clean_text(self, text):
        if text:
            text = re.sub(r'[^a-zA-Z0-9\s]', '', text)
            text = re.sub(r'\s+', ' ', text).strip()
            return text
        return None

    def node(self, n):
        self.nodes[n.id] = (n.location.lon, n.location.lat)
        if self._check_tags(n.tags):
            feature_tags, fixed_tags, osm_tags = self._extract_tags(dict(n.tags))
            self.shop_data.append({
                'OsmType': 'node',
                'Osmid': n.id,
                **feature_tags, # Add feature tags as columns
                 **fixed_tags, # Add fixed tags as columns
                'OsmTags':osm_tags,
                'Geometry': Point(n.location.lon, n.location.lat)
            })

    def way(self, w):
        coords = [self.nodes[n.ref] for n in w.nodes if n.ref in self.nodes]
        if len(coords) > 2:
            polygon = Polygon(coords)
            self.ways[w.id] = polygon
            if self._check_tags(w.tags):
                 feature_tags, fixed_tags, osm_tags = self._extract_tags(dict(w.tags))
                 self.shop_data.append({
                    'OsmType': 'way',
                    'Osmid': w.id,
                     **feature_tags, # Add feature tags as columns
                     **fixed_tags, # Add fixed tags as columns
                    'OsmTags':osm_tags,
                    'Geometry': polygon
                 })

    def relation(self, r):
        if self._check_tags(r.tags):
             feature_tags, fixed_tags, osm_tags = self._extract_tags(dict(r.tags))
             outer_boundaries = []
             inner_boundaries = []
             default_boundaries = []

             for member in r.members:
                  if member.type == 'w' and member.ref in self.ways:
                      way = self.ways[member.ref]
                      if member.role == 'outer':
                          outer_boundaries.append(way)
                      elif member.role == 'inner':
                          inner_boundaries.append(way)
                      else:
                          default_boundaries.append(way)

             if not outer_boundaries:
                outer_boundaries = default_boundaries

             if outer_boundaries:
                relation_polygon = MultiPolygon(outer_boundaries + inner_boundaries)
                self.shop_data.append({
                      'OsmType': 'relation',
                      'Osmid': r.id,
                      **feature_tags, # Add feature tags as columns
                      **fixed_tags, # Add fixed tags as columns
                      'OsmTags':osm_tags,
                      'Geometry': relation_polygon
                  })

# Initialize handler with all config lists
handler = ShopHandler(FEATURE_TAGS, FIXED_TAGS, IGNORE_TAGS)
file_path = 'input.pbf' # Ensure 'input.pbf' is in the same folder or provide the full path
handler.apply_file(file_path)

# Prepare database connection
conn = sqlite3.connect('shops.db')
cursor = conn.cursor()

# Create table with dynamic columns
base_columns = ['OsmType TEXT', 'Osmid INTEGER', 'Geometry TEXT']
dynamic_columns = [f'"{col.replace(":", "_")}" TEXT' for col in FIXED_TAGS]
extra_columns = ['"OsmTags" TEXT']

create_table_query = f"CREATE TABLE IF NOT EXISTS shops ({', '.join(base_columns + dynamic_columns + extra_columns)})"
cursor.execute(create_table_query)

# Insert records into the database
for entry in handler.shop_data:
  columns = ['OsmType', 'Osmid', 'Geometry'] + [col.replace(":", "_") for col in FIXED_TAGS] + ['"OsmTags"']
  placeholders = ', '.join(['?'] * (3 + len(FIXED_TAGS) + 1))
  values = [
      entry['OsmType'],
      entry['Osmid'],
      entry['Geometry'].wkt,
      *[entry.get(col, None) for col in FIXED_TAGS],
      str(entry['OsmTags'])
  ]
  insert_query = f"INSERT INTO shops ({', '.join(columns)}) VALUES ({placeholders})"
  cursor.execute(insert_query, values)

conn.commit()

# Print final database structure
print("\nDatabase table schema:")
cursor.execute("PRAGMA table_info(shops)")
for column in cursor.fetchall():
    print(column)
