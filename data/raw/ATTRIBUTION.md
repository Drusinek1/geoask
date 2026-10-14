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

**census_place_population.csv** — U.S. Census Bureau, Population Estimates
Program, Vintage 2023 (sub-est2023.csv), incorporated places (SUMLEV 162).
Public domain. Fetched by `etl/fetch_census.py`.

**ccap/2021_CCAP_J1466617tR0_C0.tif** — NOAA Office for Coastal Management,
Coastal Change Analysis Program (C-CAP) 2021 Regional Land Cover, 30 m, one tile
(Washington DC / Northern Virginia / central Maryland). Public domain.
<https://coast.noaa.gov/digitalcoast/data/ccapregional.html>
