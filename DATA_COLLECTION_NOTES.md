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
python src/processors/dem_to_slope.py
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
   - Script: `src/processors/dem_to_slope.py` (uses rasterio + geopandas + numpy)
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

