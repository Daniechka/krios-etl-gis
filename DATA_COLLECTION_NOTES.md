# Data Collection Notes

This document tracks manual data preparation steps for the Krios site selection project.

## How to run processing scripts

**Prerequisites:**
```bash
# dependencies are installed (from project root)
pip3 install -e .
# or if using uv:
uv sync
```

**Run scripts:**
```bash
# 1. Create tile index from DEM files
python scripts/create_dem_tindex.py

# 2. Process DEM to slope gradient raster
python -m src.processors.dem_to_slope
```

All scripts run from the project root directory and automatically find their input/output paths.

## Data organization philosophy

**`/data/raw/`**: All manually downloaded or scraped source data, preserved in original format and CRS. Never modified.

**`/data/processed/`**: Derived datasets that have been:
- Cropped to Area of Interest (AOI)
- Reprojected to match project CRS (ETRS-TM35FIN / EPSG:3067)
- Transformed for analysis (ie DEM -> slope gradient)

**AOI Definition**: `data/aoi_test.geojson` (WGS84 / EPSG:4326) - Test area covering approximately 25.33-27.28°E, 64.37-65.20°N.

**Processing workflow**: 
1. Reproject AOI to match raw data CRS
2. Select/filter raw data using tile index or spatial query
3. Merge, crop, and transform to create analysis-ready processed datasets

## DEM (Digital Elevation Model) - MML

**Date:** 2026-04-26

**Source:** Maanmittauslaitos (MML)

**Downloaded tiles:** 88 TIFF files

**Location:** `data/raw/etrs-tm35fin-n2000/`

**Metadata:**
- Coordinate system: ETRS-TM35FIN (EPSG:3067)
- File format: GeoTIFF (LZW compression)
- Height System: N2000
- Resolution: 10m x 10m
- Tile size: 24km x 12km (2400 x 1200 pixels)
- Map sheet coverage: R4, R5, Q4, Q5 series
- NoData value: -9999

**Processing steps:**
1. Downloaded DEM tiles manually from MML
2. Organized by map sheet number (ie, R5111, R4432)
3. Created tile index vector file to catalog extents and metadata
   - Script: `scripts/create_dem_tindex.py` (uses rasterio + geopandas)
   - Output: `data/processed/dem_tile_index.gpkg`
4. Generated slope gradient raster for AOI
   - Script: `src/processors/dem_to_slope.py` (run as: `python -m src.processors.dem_to_slope`)
   - Process: selected 40 tiles intersecting AOI -> merged -> calculated slope (%) -> cropped to AOI
   - Output: `data/processed/slope_gradient_percent.tif`
   - Stats: Min=0%, Max=157%, Mean=1.74%, StdDev=2.78%
   - Resolution: 10m, Size: 9391×9347 pixels (~94km × 93km)

**Technology stack:**
- **Rasterio**: Primary library for raster I/O and operations
- **Geopandas**: Vector data handling and spatial joins
- **Numpy**: Array operations and slope calculations
- **Shapely**: Geometry operations (via geopandas)

**Data format considerations:**
- **Cloud-Optimized GeoTIFF (COG)**: files already have internal tiling (256x256 blocks) and LZW compression. No conversion needed since we're working locally, not on S3/cloud storage.
- **STAC catalog**: Not implemented. Overkill for static, manually-collected datasets. Would add complexity without benefit for local analysis workflows. Consider if managing many temporal datasets or building a data portal.
- **Zarr format**: Not used. GeoTIFF has better tool support (QGIS, GDAL) and Zarr's benefits (cloud object storage, chunked array access) don't apply to local filesystem analysis.

**DEM Automation Note:**
- **NB!!!** Automated DEM collection via OGC API Processes is not working (see MML DEM section below)
- DEM is very static data (infrequent updates), so API automation is less critical than for dynamic data like parcels
- In production, Finland's full DEM is typically downloaded once, indexed, stored locally
- Current manual download + tile index approach is sufficient

---

## MML Cadastral Parcels

**Date:** 2026-04-26

**Source:** Maanmittauslaitos (MML) - Finnish Land Survey

**Collection method:**  Automated via OGC API Features

**Location (raw):** `data/raw/mml_parcels.geojson`

**Location (processed):** `data/processed/parcels.geojson`

**Metadata:**
- API Endpoint: `https://avoin-paikkatieto.maanmittauslaitos.fi/kiinteisto-avoin/simple-features/v3`
- Collection: `PalstanSijaintitiedot` (Parcel Location Data)
- Coordinate system: EPSG:3067 (ETRS-TM35FIN)
- Authentication: API key required (as query parameter `api-key`)

**Prerequisites:**
```bash
# dependencies are installed
pip3 install -e .
# or: uv sync

# MML API key
export MML_API_KEY=your_key_here
```

**Collection steps:**
1. Run MML collector to fetch parcels within AOI
   ```bash
   python -m src.collectors.mml_collector
   ```
   - Reads AOI from `data/aoi_test.geojson`
   - Buffers AOI by 15% to avoid edge effects in data collection
   - Queries OGC API Features with buffered bounding box
   - Filters parcels by minimum size (configurable in `src/config.py`)
   - Saves to `data/raw/mml_parcels.geojson`

2. Process parcels for analysis
   ```bash
   python -m src.processors.mml_parcel_processor
   ```
   - Translates Finnish field names to English
   - Ensures CRS is EPSG:3067
   - Crops to AOI
   - Calculates area in hectares
   - Saves to `data/processed/parcels.geojson`

**Field translations:**
- `kiinteistotunnus` -> `property_id`
- `rekisteriyksikkolaji`-> `property_type`
- `pinta_ala` -> `area_m2`
- `area_ha` ->`area_ha` (calculated)

**Notes:**
- Parcel boundaries can change over time, so automated collection is valuable
- API key required - get from https://omatili.maanmittauslaitos.fi
- Set environment variable: `export MML_API_KEY=your_key_here`
- Collector spent hours debugging - cadastral API works reliably

**Automation status:** Working

---

## MML DEM (Elevation Model) - Automation Attempts

**Status:** Automated collection NOT working - using manual download instead

**Issue:** Unable to find correct API request combination for DEM tiles via OGC API Processes endpoint after multiple attempts.

**Attempted approach:**
- Endpoint: `https://avoin-paikkatieto.maanmittauslaitos.fi/tiedostopalvelu/ogcproc/v1`
- Process: `korkeusmalli_10m_bbox` (bbox-based DEM tiles)
- Multiple payload structures tested
- See `src/collectors/mml_collector.py` `collect_dem()` method for implementation attempts

**Why automation is less critical for DEM:**
- DEM is very static data (updates are infrequent, measured in years)
- In production environments, Finland's complete DEM dataset is downloaded once, indexed, and stored locally
- On-demand API queries for DEM are less valuable than for dynamic data (like parcels that change boundaries)
- Manual download + tile index approach works well and unblocks analysis

**Current approach:** Manual download (see DEM section above)

**Future consideration:** Automation can be revisited if needed, but not blocking analysis work.

---

## Grid capacity & headroom - Fingrid

**Date:** 2026-04-26

**Source:** Fingrid Oyj (Finnish transmission system operator)

**Location:** `data/raw/fingrid_capacity_headroom.geojson`

**Source URL:** https://karttapalaute.fingrid.fi/?setlanguage=en&?link=3opMB

**Collection method:** Manual extraction via browser Developer Tools

**Metadata:**
- Coordinate system: ETRS-TM35FIN (EPSG:3067) - **Note:** GeoJSON lacks CRS metadata, but coordinates are in EPSG:3067
- File format: GeoJSON
- Feature type: Point geometries (substation locations)
- Total features: 213 substations across Finland

**Data extraction:**
1. Opened Fingrid map portal in browser
2. Activated "Sähkön kulutuskapasiteetti" (consumption capacity) layer
3. Used browser DevTools > Network tab to capture the GeoJSON response
4. Exported full GeoJSON to `data/raw/fingrid_capacity_headroom.geojson`

**Field definitions:**
- `STATION`: substation name (ie, "ALAJÄRVI 400 kV")
- `VOLUME`: total grid capacity available at substation (MW)
- `f_1_myytavissa_nyt`: **Grid capacity headroom currently available for sale (MW)** - PRIMARY METRIC
- `F_2_Kaavoitusmenettely_kaynnist`: Capacity reserved for projects in zoning process (MW)
- `F_3_OAS_ollut_nahtavilla`: Capacity reserved for projects with EIA published (MW)
- `F_4_Luonnos_ollut_nahtavilla`: Capacity reserved for draft plan published (MW)
- `F_5_Ehdotus_ollut_nahtavilla`: Capacity reserved for proposal published (MW)
- `F_6_Kaava_hyvaksytty`: Capacity reserved for approved zoning (MW)
- `F_7_Kaava_lainvoimainen`: Capacity reserved for legally binding zoning (MW)
- `F_8_Hanke_rakenteilla`: Capacity reserved for projects under construction (MW)
- `YEAR`/`VUOSI`: Data year (2026)
- `IDF`: Internal feature ID
- `feat_RADIUS`: Display radius on map (30m)
- `viivaleveys`: Line width for visualization (2)
- `TYYPPI`, `DESCRIPTION`: Additional metadata (mostly null)
- `geometry`: Point coordinates in EPSG:3067 (easting, northing)

**Key metrics for analysis:**
- **Primary:** `f_1_myytavissa_nyt` - MW available now for new connections
- **Secondary:** `STATION` - nearest substation name for reference
- **Total capacity:** `VOLUME` - total substation capacity

**Automation status:**
Data currently collected manually due to Fingrid API authentication challenges. The map portal uses a protected endpoint that requires session cookies and specific parameters. Automated collection via `src/collectors/fingrid_collector.py` is documented but not yet functional. Future automation will require reverse-engineering the API authentication or using Fingrid's official API (if available).

**Important note on CRS:**
The raw GeoJSON file lacks proper CRS metadata. While the file may appear to be in WGS84 (EPSG:4326) when loaded, the coordinates are actually in EPSG:3067 (ETRS-TM35FIN). This is evident from the coordinate values ie [356669, 6992357] which are in meters, not degrees. The processor handles this by explicitly setting the correct CRS.

**Processing steps:**
1. Load raw GeoJSON and set CRS to EPSG:3067 (coordinates are in Finnish TM35FIN)
2. Translate Finnish field names to English
3. Drop irrelevant fields (visual styling, duplicate year fields)
4. Crop to AOI (data/aoi_test.geojson)
5. Save to GeoPackage: `data/processed/fingrid_capacity_aoi.gpkg`

**Processing script:** `src/processors/fingrid_processor.py`

**AOI results:**
- 9 substations within test AOI
- Available capacity: 0-1000 MW (mean: 264 MW, median: 200 MW)
- Notable substations: PYHÄNSELKÄ 400 kV (1000 MW), PIKKARALA 400 kV (600 MW)

---

## OpenStreetMap (OSM) Infrastructure Data

**Date:** 2026-04-26

**Source:** OpenStreetMap via Overpass API

**Collection method:** Automated via Overpass API

**Location (raw):** Multiple files in `data/raw/`:
- `osm_data_centers.geojson`
- `osm_power_plants.geojson`
- `osm_power_lines.geojson`
- `osm_substations.geojson`
- `osm_urban_centers.geojson`

**Metadata:**
- API Endpoint: `https://overpass-api.de/api/interpreter`
- Coordinate system: EPSG:4326 (WGS84)
- Query format: Overpass QL
- Timeout: 60 seconds (configurable)

**Prerequisites:**
```bash
# dependencies are installed
pip3 install -e .
# or: uv sync
```

**Collection steps:**
1. Run OSM collector to fetch infrastructure data within AOI:
   ```bash
   python -m src.collectors.osm_collector
   ```
   - Reads AOI from `data/aoi_test.geojson`
   - Buffers AOI by 15% to avoid edge effects
   - Queries Overpass API for each dataset
   - Saves results to `data/raw/osm_*.geojson`

2. Process OSM data for analysis:
   ```bash
   python -m src.processors.osm_processor
   ```
   - Reprojects all layers from EPSG:4326 to EPSG:3067
   - Crops point features (data centers, substations, etc.) to AOI
   - Clips power lines with 10km buffer (preserves network connectivity)
   - Performs basic QC checks (geometry validation, duplicate detection)
   - Saves to `data/processed/osm_infrastructure.gpkg`

**Datasets collected:**

1. **Data centers** (`osm_data_centers.geojson`)
   - query: `telecom=data_center`
   - geom: Point
   - attributes: name, operator, description, Finnish name
   - example: CSC - IT Center for Science (Kajaani)

2. **Power plants** (`osm_power_plants.geojson`)
   - query: `power=plant` and `power=generator`
   - geom: Point (centroids for ways/relations)
   - attributes: name, operator, plant source (wind/solar/hydro), capacity
   - use case: energy source locations and types

3. **Power Lines** (`osm_power_lines.geojson`)
   - query: `power=line` and `power=cable`
   - geom: LineString
   - attributes: voltage, name, operator
   - use case: high-voltage transmission network

4. **Substations** (`osm_substations.geojson`)
   - query: `power=substation`
   - geom: Point (centroids for ways/relations)
   - attributes: voltage, name, operator
   - use case: grid connection points

5. **Urban centers** (`osm_urban_centers.geojson`)
   - query: `place=city` and large towns (100k+ population)
   - geom: Point
   - attributes: name, population, place type
   - use case: proximity to population centers

**Overpass query:**
```overpass
[out:json][timeout:60];
(
  node["tag"="value"](south, west, north, east);
  way["tag"="value"](south, west, north, east);
  relation["tag"="value"](south, west, north, east);
);
out geom;
```

**Key implementation notes:**
- Bbox format for Overpass API is `(south, west, north, east)` or `(min_lat, min_lon, max_lat, max_lon)`
- Data centers use OSM tag `telecom=data_center` (not `building=data_centre`)
- Handles nodes, ways, and relations - converts non-point geometries to centroids where needed
- Retry logic: 3 attempts with 5-second delays between failures
- **CRITICAL:** Overpass API requires `User-Agent` header in all requests, otherwise returns 406 error

**AOI results (example for test area):**
- Data centers: 0-2 features (depends on OSM tagging completeness)
- Power plants: varies by region
- Power lines: varies by region
- Substations: varies by region
- Urban centers: 5-20 features depending on buffer

**Data quality notes:**
- OSM data completeness varies by region and feature type
- Data centers may be underreported (not all tagged with `telecom=data_center`)
- Manual verification recommended for critical infrastructure
- **QC REQUIRED:** OSM data needs quality control checks:
  - Power line topology validation (lines should connect to substations)
  - Voltage attribute completeness and consistency
  - Duplicate feature detection
  - Geometry validity checks

**TODO - additional data center sources:**
- https://www.datacentermap.com/ for more data center locations, OSM only as LUMI
- Statistics Finland or other for proximity signal for workforce


**Automation status:** Working

**Processing steps:**
1. Reproject all layers from EPSG:4326 to EPSG:3067
2. Crop to AOI (Note: power lines should NOT be cropped - need to preserve network connectivity beyond AOI)
3. Validate topology and attributes
4. Save to GeoPackage: `data/processed/osm_infrastructure.gpkg` (multiple layers)

**Processing script:** `src/processors/osm_processor.py` (created)

---

## Natura 2000 Protected Areas - EEA

**Date:** 2026-04-26

**Source:** European Environment Agency (EEA)

**Collection method:** Automated via ArcGIS REST API

**Location (raw):** `data/raw/natura2000_sites.geojson`

**Metadata:**
- API endpoint: `https://bio.discomap.eea.europa.eu/arcgis/rest/services/ProtectedSites/Natura2000Sites/MapServer`
- Layers available:
  - layer 0: Habitats Directive Sites (pSCI, SCI or SAC)
  - layer 1: Birds Directive Sites (SPA)
  - layer 2: Habitats and Birds Directive Sites (combined) - **used by collector**
- CRS: EPSG:4326 (WGS84) - returned by API
- Query filter: `MS='FI'` (Finland only)
- Dataset version: 2024



**Collection steps:**
1. Run Natura 2000 collector to fetch protected areas within AOI:
   ```bash
   python -m src.collectors.natura2000_collector
   # or with uv:
   uv run python -m src.collectors.natura2000_collector
   ```
   - Reads AOI from `data/aoi_test.geojson`
   - Buffers AOI by 15% to avoid edge effects in data collection
   - Queries ArcGIS REST API for Finnish sites intersecting buffered AOI bbox
   - Filters to `MS='FI'` (Finland member state)
   - Clips results to original (unbuffered) AOI
   - Saves to `data/raw/natura2000_sites.geojson`

**Field descriptions:**
- `SITECODE`: Unique Natura 2000 site code (e.g., "FI0800011")
- `SITENAME`: Protected area name (e.g., "Sanginjoki")
- `SITETYPE`: Site designation type
  - A = Birds Directive (SPA)
  - B = Habitats Directive (SCI/SAC)
  - C = Both directives
- `MS`: Member State code (FI = Finland)
- `Area_ha`: Site area in hectares
- `Area_km2`: Site area in square kilometers
- `RELEASE_DATE`: Data release date
- `POINT_X`, `POINT_Y`: Representative point coordinates
- `A`, `B`, `C`, `D`: Habitat quality indicators
- `geometry`: Polygon boundaries (MultiPolygon in EPSG:4326)

**AOI results (Oulu region test area):**
- 20 Natura 2000 sites found within 30km radius
- Mix of site types (Habitats, Birds, combined)
- Used for constraint analysis (avoid building in protected areas)

**Data quality:**
- Official EU dataset, expect to be correct
- Complete coverage for Finland
- Polygons represent legal boundaries of protected areas

**Production deployment strategy:**
This is a **static dataset** that changes infrequently (annual updates). In production, store in your own cloud warehouse:

1. **One-time download:** download full European dataset from EEA data portal
   - https://www.eea.europa.eu/data-and-maps/data/natura-12
   - download GeoPackage vector data
   - ~28,000+ protected sites across EU

2. **Cloud warehouse storage:** store in your own object storage (S3/GCS/Azure Blob)
   - organized by country: `s3://your-bucket/natura2000/FI/natura2000_finland.gpkg`
   - enable versioning to track annual updates
   - **Why:** eliminates dependency on external APIs, ensures data availability and consistency

3. **PostGIS indexing:** load into PostGIS database (part of your cloud warehouse) for fast queries
   ```sql
   CREATE TABLE natura2000_sites (
       id SERIAL PRIMARY KEY,
       sitecode VARCHAR(20) UNIQUE NOT NULL,
       sitename VARCHAR(255),
       sitetype CHAR(1),
       country CHAR(2),
       area_ha NUMERIC,
       geom GEOMETRY(MultiPolygon, 3067),
       updated_at TIMESTAMP
   );
   CREATE INDEX idx_natura2000_geom ON natura2000_sites USING GIST(geom);
   CREATE INDEX idx_natura2000_country ON natura2000_sites(country);
   ```

4. **Processor workflow (production):**
   - `natura2000_processor.py` queries PostGIS directly (not API) fast wit spatial index:
     ```python
     # Instead of painful WFS API call:
     query = """
         SELECT sitecode, sitename, sitetype, area_ha, ST_Transform(geom, 4326) as geom
         FROM natura2000_sites
         WHERE country = 'FI'
           AND ST_Intersects(geom, ST_Transform(ST_GeomFromText(%s, 4326), 3067))
     """
     ```

5. **Why this cloud warehouse approach:**
   - Natura 2000 boundaries rarely change
   - API is useful for prototyping, but cumbersome in production
   - Your own cloud warehouse (S3 + PostGIS) ensures consistent, reproducible results
   - Eliminates external API dependencies
   - Standard pattern for regulatory and cadastral data (parcels, zoning, protected areas)
   - Full control over data versioning, access, and updates

**Current implementation:**
- Development/prototype: API-based collection (implemented)
- Production: Cloud warehouse with PostGIS querying (documented, not yet implemented)
- Migration path: Load `natura2000_sites.geojson` into your S3/PostGIS warehouse, update processor to query DB

**Automation status:** Working (ArcGIS REST API-based)

**Processing steps:**
1. Run Natura 2000 processor to prepare data for analysis:
   ```bash
   python -m src.processors.natura2000_processor
   # or with uv:
   uv run python -m src.processors.natura2000_processor
   ```
   - Loads raw data from `data/raw/natura2000_sites.geojson`
   - Translates field names to English
   - Selects relevant fields for constraint analysis
   - Reprojects from EPSG:4326 to EPSG:3067
   - Clips to AOI from `data/aoi_test.geojson`
   - Saves to `data/processed/natura2000_sites.gpkg`

**Processed output:**
- Location: `data/processed/natura2000_sites.gpkg`
- Layer: `natura2000_sites`
- CRS: EPSG:3067 (ETRS-TM35FIN)
- Fields (English):
  - `site_code`: Unique Natura 2000 code
  - `site_name`: Protected area name
  - `site_type`: A=Birds Directive, B=Habitats Directive, C=Both
  - `member_state`: FI (Finland)
  - `area_ha`: Area in hectares
  - `area_km2`: Area in square kilometers
  - `release_date`: Data release date
  - `geometry`: Polygon boundaries in EPSG:3067

**Processing script:** `src/processors/natura2000_processor.py`

---

## Flood Hazard zones - SYKE

**Date:** 2026-04-26

**Source:** Finnish Environment Institute (SYKE)

**Collection method:** Automated via WFS (GeoServer)

**Location (raw):** `data/raw/syke_flood_zones_100a.geojson`

**Metadata:**
- WFS Endpoint: `https://paikkatiedot.ymparisto.fi/geoserver/inspire_nz/wfs`
- Layer: `inspire_nz:NZ.Tulvavaaravyohykkeet_Vesistotulva_1_100a`
- Return period: 1:100 years
- CRS: EPSG:3067 (ETRS-TM35FIN) - native CRS
- Data standard: INSPIRE Natural Risk Zones
- License: cleared

**Available flood layers:**
1. **Riverine flood hazard zones** (`Tulvavaaravyohykkeet_Vesistotulva_*`):
   - 10a, 20a, 50a, 100a, 250a, 1000a return periods
   - Most relevant for inland Finland
2. **Coastal flood hazard zones** (`Tulvavaaravyohykkeet_Meritulva_*`):
   - Same return periods
   - Relevant for coastal areas
3. **Significant flood risk areas** (`Merkittavat_tulvariskialueet`)
4. **Flood-mapped areas** (`Tulvavaarakartoitetut_alueet_*`)


**Collection steps:**
1. Run SYKE collector to fetch flood hazard zones within AOI:
   ```bash
   python -m src.collectors.syke_collector
   # or with uv:
   uv run python -m src.collectors.syke_collector
   ```
   - AOI from `data/aoi_test.geojson`
   - buffers AOI by 15% to avoid edge effects in data collection
   - queries WFS for riverine flood hazard zones (1:100 year return period)
   - clips results to original (unbuffered) AOI
   - saves to `data/raw/syke_flood_zones_100a.geojson`

**Field descriptions:**
- `nimi`: Name of flooded area/watercourse
- `tulvakartoitustyyppi`: Flood mapping type
- `toistuvuus`: Return period/recurrence interval (ie, "100")
- `syvsuojluokka`: Flood depth protection class
- `syvvyohluokka`: Flood depth zone class
- `tulvasuojluokka`: Flood protection class
- `maarityswmenetelma`: Determination method
- `korkeusain`: Elevation data source
- `korkeusvirhe_m`: Elevation error in meters
- `muutospvm`: Last modification date
- `geometry`: Polygon boundaries of flood zones

**AOI results (Oulu region test area):**
- 9410 flood hazard zones found within 30km radius
- Represents areas at risk of flooding with 1% annual probability (1:100 year flood)
- Used as constraint layer (avoid building in flood-prone areas)

**Data quality notes:**
- Official regulatory dataset used for spatial planning in Finland
- Based on hydrological modeling and historical flood observations
- Updated when new flood models or observations become available
- High quality and legally binding for land use planning
- Flood zones are detailed polygons with depth classifications

**Production deployment strategy:**
This is a **relatively static dataset** that updates infrequently (every few years when flood models are updated). In production, store in your own cloud warehouse:

1. **One-time download:** download full Finnish flood hazard dataset from SYKE
   - Download all return periods (10a, 100a, 1000a) for comprehensive analysis
   - Include both riverine and coastal flood zones
   - GeoPackage or Shapefile format from SYKE open data portal

2. **Cloud warehouse storage:** store in your own object storage (S3/GCS/Azure blob)
   -  `s3://your-bucket/syke/flood_hazard/vesistotulva_100a.gpkg`
   - no dependency on SYKE WFS, ensures data availability and consistent performance

3. **PostGIS indexing:** Load into PostGIS database (part of your cloud warehouse) for fast queries
   ```sql
   CREATE TABLE flood_hazard_zones (
       id SERIAL PRIMARY KEY,
       name VARCHAR(255),
       return_period INTEGER,
       flood_type VARCHAR(50),
       depth_class VARCHAR(100),
       protection_class VARCHAR(100),
       geom GEOMETRY(MultiPolygon, 3067),
       updated_at TIMESTAMP
   );
   CREATE INDEX idx_flood_geom ON flood_hazard_zones USING GIST(geom);
   CREATE INDEX idx_flood_return_period ON flood_hazard_zones(return_period);
   ```

4. **Processor workflow (production):**
   - `syke_processor.py` queries PostGIS  (not WFS):
     ```python
     # Query flood zones intersecting AOI
     query = """
         SELECT name, return_period, depth_class, ST_Transform(geom, 4326) as geom
         FROM flood_hazard_zones
         WHERE return_period = 100
           AND ST_Intersects(geom, ST_Transform(ST_GeomFromText(%s, 4326), 3067))
     """
     ```


**Current implementation:**
- Development/prototype: WFS-based collection (implemented and working)
- Production: Cloud warehouse with PostGIS querying (documented, not yet implemented)
- Migration path: Load `syke_flood_zones_*.geojson` into your S3/PostGIS warehouse, update processor to query DB

**Automation status:** Working (WFS-based)

**Processing steps:**
1. Run SYKE flood processor to prepare data for analysis:
   ```bash
   python -m src.processors.syke_processor
   # or with uv:
   uv run python -m src.processors.syke_processor
   ```
   - Loads raw data from `data/raw/syke_flood_zones_100a.geojson`
   - Translates Finnish field names to English
   - Selects relevant fields for constraint analysis
   - Reprojects from EPSG:4326 to EPSG:3067
   - Clips to AOI from `data/aoi_test.geojson`
   - Saves to `data/processed/syke_flood_zones.gpkg`

**Processed output:**
- Location: `data/processed/syke_flood_zones.gpkg`
- Layer: `flood_hazard_zones`
- CRS: EPSG:3067 (ETRS-TM35FIN)
- Fields (English):
  - `name`: Name of flooded area/watercourse
  - `return_period`: Return period (e.g., "100" for 1:100 year)
  - `flood_mapping_type`: Type of flood mapping
  - `depth_zone_class`: Flood depth zone classification
  - `depth_protection_class`: Flood depth protection class
  - `flood_protection_class`: Flood protection class
  - `determination_method`: How flood zone was determined
  - `elevation_source`: Source of elevation data
  - `elevation_error_m`: Elevation uncertainty in meters
  - `modification_date`: Last modification date
  - `geometry`: Polygon boundaries in EPSG:3067

**Processing script:** `src/processors/syke_processor.py`

**Use case in site selection:**
- **Constraint layer:** Exclude flood-prone areas from site selection
- **Risk assessment:** Calculate distance to nearest flood zone
- **Scenario analysis:** Compare different return periods (100a vs 1000a)
- **Regulatory compliance:** Ensure sites meet flood safety requirements

---

