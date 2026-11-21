Daily OpenStreetMap extracts of the Brussels-Capital Region ([relation 54094]lipped to the regional boundary with a 100 m buffer. All files in this release are rebuilt from the same data every day, and `state.txt` gives their source and timestamp.

File names follow `Brussels-daily.<schema>.<format>`, where the schema is the tool that defines the data layout.

| File | Format | Schema |
|---|---|---|
| `Brussels-daily.pbf` | OSM PBF | Raw OpenStreetMap data: nodes, ways and relations with all their tags, without user metadata. Readable by osmium, GDAL, QGIS, osm2pgsql and most OSM tools. |
| `state.txt` | JSON | Source of the extract (SliceOSM, or the openstreetmap.fr backup), replication sequence number, data timestamp, file size, boundary relation and buffer. |
| `Brussels-daily.gob` | GeoDesk Geo-Object Bundle | Compressed GeoDesk tiles with all tags and relations. Load it with `gol load Brussels Brussels-daily.gob` (GOL Tool 2.x), then query it with GOQL from the command line, Python, Java or C++. Built without waynode IDs, so `gol update` does not apply. |
| `Brussels-daily.quackosm.parquet` | GeoParquet | Built with [QuackOSM](https://github.com/kraina-ai/quackosm). One row per feature with three columns: `feature_id` (string such as `node/123`, `way/123` or `relation/123`), `tags` (map of string to string holding every tag) and `geometry` (WKB, CRS84). Geometry types are point, linestring, polygon and multipolygon. Read it with DuckDB (`tags['amenity']`), GeoPandas or QGIS. |

## Licence

OpenStreetMap data is © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright) and available under the Open Database License (ODbL).
