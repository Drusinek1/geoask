# Raw data sources

These files are redistributed here so the database can be rebuilt from scratch
(`python etl/build_db.py --raw data/raw`). They are not mine; credit goes to:

**datacenters.db** — Data centers (c) Ringmast4r - Global-Data-Center-Map
<https://github.com/Ringmast4r/Global-Data-Center-Map>
License: attribution required (see that repository's LICENSE). Coordinates vary
in precision: building, city, or country centroid. Not verified ground truth.

**findingflock-cameras-by-city.geojson** — Camera locations (c) OpenStreetMap
contributors, Open Database License (ODbL) v1.0. City boundaries and land area:
U.S. Census Bureau 2025 Cartographic Boundary Files. Compiled by Finding Flock
<https://www.findingflock.com>.
