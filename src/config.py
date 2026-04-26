"""Configuration settings for the GIS site selection project."""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Project root directory
PROJECT_ROOT = Path(__file__).parent.parent

# Data directories
DATA_DIR = PROJECT_ROOT / "data"
RAW_DATA_DIR = DATA_DIR / "raw"
PROCESSED_DATA_DIR = DATA_DIR / "processed"
OUTPUT_DIR = DATA_DIR / "outputs"

# Area of Interest (AOI) settings
AOI_CENTER_LAT = 65.0121  # Oulu, Finland
AOI_CENTER_LON = 25.4651
AOI_RADIUS_KM = 10  # 75km radius = ~50-100km effective coverage

# Coordinate Reference Systems
CRS_WGS84 = "EPSG:4326"  # WGS84 - for data collection (lat/lon)
CRS_FINLAND = "EPSG:3067"  # ETRS89 / TM35FIN - projected CRS for analysis (meters)

# CRS Strategy:
# - RAW data: Keep in original CRS (usually WGS84 from APIs)
# - PROCESSED data: Convert ALL to EPSG:3067 for accurate distance/area calculations
# - OUTPUT data: EPSG:3067 for GIS tools, can export to WGS84 for web maps

# Site selection criteria
MIN_PARCEL_SIZE_HA = 10
MAX_PARCEL_SIZE_HA = 100
MAX_SLOPE_PERCENT = 8

# Scoring weights (must sum to 1.0)
WEIGHTS = {
    "grid_capacity": 0.30,      # grid capacity headroom
    "distance_to_grid": 0.25,   # distance to 220/400kV network
    "distance_to_urban": 0.20,  # distance to urban center
    "parcel_size": 0.15,        # parcel size (larger - better)
    "distance_to_dc": 0.10,     # distance to existing data centers
}

# Scoring parameters
GRID_CAPACITY_IDEAL_MW = 100  # 100+ MW = full score
GRID_DISTANCE_DECAY_KM = 10   # exponential decay rate
URBAN_DISTANCE_DECAY_KM = 50  # exponential decay rate
MIN_URBAN_POPULATION = 100000 # min population for "urban center"

# Data source URLs - REAL endpoints only
DATA_SOURCES = {
    # MML (Finnish Land Survey)
    "mml_cadastral_ogc": "https://avoin-paikkatieto.maanmittauslaitos.fi/kiinteisto-avoin/simple-features/v3",
    "mml_dem_ogc": "https://avoin-paikkatieto.maanmittauslaitos.fi/tiedostopalvelu/ogcproc/v1",
    "mml_inspire_wfs": "https://inspire-wfs.maanmittauslaitos.fi/inspire-wfs/cp/ows",

    # Environmental data
    "natura2000_wfs": "https://bio.discomap.eea.europa.eu/arcgis/services/ProtectedSites/Natura2000Sites/MapServer/WFSServer",
    "syke_floods_wfs": "https://paikkatieto.ymparisto.fi/arcgis/services/INSPIRE/SYKE_Hydro/MapServer/WFSServer",

    # Infrastructure
    "fingrid_api": "https://data.fingrid.fi/api",
    "osm_overpass": "https://overpass-api.de/api/interpreter",
}

# API Keys (set via environment variables for production)
# export MML_API_KEY="your_key_here"
# export FINGRID_API_KEY="your_key_here"

# Ensure directories exist
RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
PROCESSED_DATA_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
