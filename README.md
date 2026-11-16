# Osm-python-analyse_Belgium

Python scripts to analyse OpenStreetMap data for Belgium, mostly the Brussels-Capital Region, plus ready-to-use Brussels extracts published every day.

## Brussels data files

The extracts are published as GitHub release assets, not stored in the Git repository. The URLs below are stable and always return the latest version.

| File | Content | URL |
|---|---|---|
| `Brussels-daily.pbf` | Daily OSM extract of the Brussels-Capital Region | https://github.com/PasLoin/Osm-python-analyse_Belgium/releases/download/daily/Brussels-daily.pbf |
| `state.txt` | Timestamp and metadata of the daily extract (JSON) | https://github.com/PasLoin/Osm-python-analyse_Belgium/releases/download/daily/state.txt |
| `brussels-latest.parquet` | Same data as GeoParquet, built with QuackOSM | https://github.com/PasLoin/Osm-python-analyse_Belgium/releases/download/daily/brussels-latest.parquet |
| `DD_MM_YYYY_brussels_capital_region.pbf` | Historical snapshots, from 2005 to today | `https://github.com/PasLoin/Osm-python-analyse_Belgium/releases/download/history/<file name>` |

[`pbf_analyse/history-list.json`](pbf_analyse/history-list.json) lists every snapshot with its date, URL and size, so other projects can find them without calling the GitHub API.

Download the daily extract:

```bash
curl -L -o Brussels-daily.pbf \
  https://github.com/PasLoin/Osm-python-analyse_Belgium/releases/download/daily/Brussels-daily.pbf
osmium fileinfo Brussels-daily.pbf
```

Release URLs answer with a redirect, so use `curl -L`, `wget`, or any client that follows redirects. They do not send CORS headers and cannot be fetched from JavaScript running in a browser.

### How the extracts are built

- **Daily extract**: requested from [SliceOSM](https://slice.openstreetmap.us/), then clipped with `osmium` to the boundary of the Brussels-Capital Region ([relation 54094](https://www.openstreetmap.org/relation/54094), file [`pbf_analyse/54094.poly`](pbf_analyse/54094.poly)) with a 100 m buffer.
- **Backup source**: if SliceOSM is unavailable, a manual workflow rebuilds the same file from the [openstreetmap.fr extract](https://download.openstreetmap.fr/extracts/europe/belgium/) and its minutely diffs.
- **Monthly snapshots**: copied from the openstreetmap.fr extract on the first day of each month.
- **Older yearly snapshots**: clipped from [Geofabrik](https://download.geofabrik.de/europe/belgium.html) archives.

For the whole of Belgium, use the Geofabrik or openstreetmap.fr extracts directly.

## Scripts

Each script is standalone. Most of them read a local `.pbf` file whose name is set at the top of the script or passed as an argument.

| Folder | What the scripts do |
|---|---|
| [`amenity/`](amenity) | Count amenities, map bicycle parkings and chip shops, and compare OSM with Brussels open data (bicycle parkings, Cambio car-sharing stations, glass recycling containers) |
| [`building/`](building) | Building type statistics, and conversion of UrbIS 3D buildings to OSM Simple 3D Buildings |
| [`highway/`](highway) | Road lengths, cycleway and footway maps, contributors to soft-mobility ways, contiguous crossings, street tours solved as a travelling salesman problem |
| [`highway/routing_optimisation/`](highway/routing_optimisation) | Route optimisation with openrouteservice, routes from OSM notes, Mapillary coverage of a road network |
| [`highway/Mapillary-Panoramax-exif-correction/`](highway/Mapillary-Panoramax-exif-correction) | Fix or interpolate the GPS position of photos before uploading them to Mapillary or Panoramax |
| [`natural/`](natural) | Match OSM trees with the Brussels Mobility tree inventory |
| [`UrbIsTopo/`](UrbIsTopo) | Export UrbIS-Topo points to GeoJSON and list candidates missing in OSM |
| [`QA/`](QA) | Find road ends that are close but not connected, and check the `website` URLs of OSM features |
| [`parquet/`](parquet) | Convert the daily extract to GeoParquet, with a DuckDB usage example |
| [`pbf_analyse/`](pbf_analyse) | Build the extracts and snapshots, load a PBF into SQLite, find the oldest objects |
| [`panoramax/`](panoramax) | Read the RSS feed of the Belgian Panoramax instance |

The open data comparisons are aids for manual mapping. Do not use their output for bulk imports into OpenStreetMap: always check each object against imagery or on the ground.

## Installation

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

`requirements.txt` covers all the scripts. Some of them also need the [`osmium-tool`](https://osmcode.org/osmium-tool/) command line program (`sudo apt install osmium-tool`).

A few scripts call external services and need your own API key: openrouteservice for `simple_ORS_Vroom_Optimization.py`, Mapillary for `Mapillary-coverage.py`.

## Automated workflows

| Workflow | Schedule | Result |
|---|---|---|
| Daily Brussels PBF Extract (SliceOSM) | every day, 00:30 UTC | updates `Brussels-daily.pbf` and `state.txt` in the `daily` release |
| Brussels PBF to Parquet | every day, 11:00 UTC | updates `brussels-latest.parquet` in the `daily` release |
| Download_pbf_Brussels | 1st of each month, 02:00 UTC | adds a snapshot to the `history` release |
| Backup Brussels PBF (OSM-FR + diffs minute) | manual | rebuilds the daily extract from openstreetmap.fr |
| Historical Brussels snapshots (one-shot) | manual | adds yearly snapshots clipped from Geofabrik archives |
| Stations Cambio - Bruxelles vs OSM | manual | comparison report, as a workflow artifact |
| Bulles a verre - OpenData vs OSM | manual | comparison report, as a workflow artifact |
| Vérification des sites web OSM | manual | report on broken `website` URLs, as a workflow artifact |

## Licence

The code is released under [CC0 1.0 Universal](LICENSE). OpenStreetMap data is © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright) and available under the Open Database License (ODbL).
