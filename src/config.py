"""Configuration settings for the GIS site selection project."""

from pathlib import Path

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
AOI_RADIUS_KM = 75  # 75km radius = ~50-100km effective coverage

# Coordinate Reference Systems
CRS_WGS84 = "EPSG:4326"  # Input CRS (lat/lon)
CRS_FINLAND = "EPSG:3067"  # ETRS89 / TM35FIN (Finland national projection)

# Site selection criteria
MIN_PARCEL_SIZE_HA = 10
MAX_PARCEL_SIZE_HA = 100
MAX_SLOPE_PERCENT = 8

# Data source URLs (to be populated)
DATA_SOURCES = {
    "mml_parcels": "https://avoin-paikkatieto.maanmittauslaitos.fi/",
    "natura2000": "https://www.eea.europa.eu/",
    "syke_floods": "https://www.syke.fi/",
    "fingrid_capacity": "https://data.fingrid.fi/",
    "osm_overpass": "https://overpass-api.de/api/interpreter",
}

# Ensure directories exist
RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
PROCESSED_DATA_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
