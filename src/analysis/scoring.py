"""
Stage 2 Opportunity Scoring

Computes 5 continuous opportunity scores [0, 1] for Stage 1-eligible parcels
and combines them into a single weighted composite score.

Score components and weights (from src/config.py WEIGHTS dict):
  score_grid_capacity   (0.30) - Fingrid available capacity at nearest node
  score_grid_distance   (0.25) - inverse distance to nearest 220 / 400 kV line
  score_urban_distance  (0.20) - inverse distance to nearest big city
  score_parcel_size     (0.15) - logarithmic area score (10ha -> 0, 100ha -> 1)
  score_dc_distance     (0.10) - inverse distance to nearest existing data center

New columns added to parcels layer:
  nearest_capacity_mw       raw Fingrid available capacity MW at closest node
  nearest_capacity_station  name of that Fingrid substation node
  nearest_grid_dist_km      km to nearest HV (220/400 kV) power line
  nearest_urban_dist_km     km to nearest qualifying urban center
  nearest_dc_dist_km        km to nearest existing data center
  score_grid_capacity       [0, 1]
  score_grid_distance       [0, 1]
  score_urban_distance      [0, 1]
  score_parcel_size         [0, 1]
  score_dc_distance         [0, 1]
  composite_score           [0, 1] weighted sum of the five component scores

Scoring formulas (see DECISIONS.md for rationale):
  grid_capacity  = clip(capacity_MW / 100, 0, 1)
  grid_distance  = exp( -dist_km / 10)
  urban_distance = exp( -dist_km / 50)
  parcel_size    = clip(log10(area_ha / 10), 0, 1)
  dc_distance    = exp( -dist_km / 30)
"""

import logging
from pathlib import Path

import geopandas as gpd
import numpy as np

from src.config import (
    DC_DISTANCE_DECAY_KM,
    GRID_CAPACITY_IDEAL_MW,
    GRID_DISTANCE_DECAY_KM,
    MIN_URBAN_POPULATION,
    PROCESSED_DATA_DIR,
    URBAN_DISTANCE_DECAY_KM,
    WEIGHTS,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Default input paths (can be overridden at call time)
# ---------------------------------------------------------------------------
FINGRID_PATH = PROCESSED_DATA_DIR / "fingrid_capacity_aoi.gpkg"
OSM_INFRASTRUCTURE_PATH = PROCESSED_DATA_DIR / "osm_infrastructure.gpkg"
OUTPUT_PARCELS_PATH = PROCESSED_DATA_DIR.parent / "outputs"

# HV voltage strings that count as 220/400 kV grid
_HV_VOLTAGE_PATTERN = r"220000|400000"

SCORE_COLS = [
    "score_grid_capacity",
    "score_grid_distance",
    "score_urban_distance",
    "score_parcel_size",
    "score_dc_distance",
]

WEIGHT_KEYS = [
    "grid_capacity",
    "distance_to_grid",
    "distance_to_urban",
    "parcel_size",
    "distance_to_dc",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _centroids(parcels: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Return a copy of parcels with geometry replaced by centroids."""
    pts = parcels[["geometry"]].copy()
    pts.geometry = parcels.geometry.centroid
    return pts


def _nearest_join(
    left_pts: gpd.GeoDataFrame,
    right: gpd.GeoDataFrame,
    distance_col: str,
    extra_cols: list[str],
) -> gpd.GeoDataFrame:
    """
    sjoin_nearest of left_pts (points) -> right (any geometry).

    Returns a df indexed like left_pts with distance_col and extra_cols
    from the nearest right feature.  Duplicate indices (rare equidistant ties)
    are dropped keeping the first match.
    """
    joined = gpd.sjoin_nearest(
        left_pts,
        right[["geometry"] + extra_cols],
        how="left",
        distance_col=distance_col,
    )
    return joined[~joined.index.duplicated(keep="first")]


# ---------------------------------------------------------------------------
# Score 1: Grid capacity
# ---------------------------------------------------------------------------

def compute_grid_capacity_score(
    parcels: gpd.GeoDataFrame,
    fingrid_path: Path = FINGRID_PATH,
) -> gpd.GeoDataFrame:
    """
    Assign each parcel the available grid capacity (MW) of its nearest
    Fingrid substation node, then score linearly: clip(capacity / 100, 0, 1).

    Reads from the processed GeoPackage (fingrid_capacity_aoi.gpkg) which is
    already in EPSG:3067 and uses English field names translated by
    FingridCapacityProcessor:
      available_capacity_mw  - MW currently available for sale
      station_name           - substation name

    NaN capacity values (incomplete data) are treated as 0 MW.
    """
    logger.info("Computing grid capacity score (Fingrid nearest-node)…")

    fingrid = gpd.read_file(fingrid_path)[
        ["geometry", "available_capacity_mw", "station_name"]
    ].copy()
    if fingrid.crs != parcels.crs:
        fingrid = fingrid.to_crs(parcels.crs)

    pts = _centroids(parcels)
    joined = _nearest_join(
        pts, fingrid, "_fg_dist_m", ["available_capacity_mw", "station_name"]
    )

    capacity_mw = joined["available_capacity_mw"].fillna(0.0).values.astype(float)

    parcels = parcels.copy()
    parcels["nearest_capacity_mw"] = capacity_mw
    parcels["nearest_capacity_station"] = joined["station_name"].values
    parcels["score_grid_capacity"] = np.clip(
        capacity_mw / GRID_CAPACITY_IDEAL_MW, 0.0, 1.0
    )

    mean_cap = float(np.nanmean(capacity_mw))
    logger.info(
        f"  Grid capacity done - mean nearest capacity: {mean_cap:.0f} MW, "
        f"mean score: {parcels['score_grid_capacity'].mean():.3f}"
    )
    return parcels


# ---------------------------------------------------------------------------
# Score 2: Distance to HV grid
# ---------------------------------------------------------------------------

def compute_grid_distance_score(
    parcels: gpd.GeoDataFrame,
    osm_infrastructure_path: Path = OSM_INFRASTRUCTURE_PATH,
) -> gpd.GeoDataFrame:
    """
    Distance from each parcel centroid to the nearest 220 / 400 kV power line.
    Score: exp( -dist_km / 10).

    Reads the 'power_lines' layer from the processed osm_infrastructure.gpkg.
    Lines with voltage strings containing '220000' or '400000' are retained.
    Lines with unknown / missing voltage are excluded.
    """
    logger.info("Computing grid distance score (220/400 kV lines)…")

    lines = gpd.read_file(osm_infrastructure_path, layer="power_lines")
    if lines.crs != parcels.crs:
        lines = lines.to_crs(parcels.crs)

    hv_lines = lines[
        lines["voltage"].str.contains(_HV_VOLTAGE_PATTERN, na=False)
    ].copy()
    logger.info(f"  {len(hv_lines)} HV lines (220/400 kV) after voltage filter")

    if hv_lines.empty:
        logger.warning("  No HV lines found - setting grid distance score to 0")
        parcels = parcels.copy()
        parcels["nearest_grid_dist_km"] = np.nan
        parcels["score_grid_distance"] = 0.0
        return parcels

    pts = _centroids(parcels)
    joined = _nearest_join(pts, hv_lines, "_grid_dist_m", [])

    dist_m = joined["_grid_dist_m"].fillna(np.inf).values.astype(float)
    dist_km = dist_m / 1_000.0

    parcels = parcels.copy()
    parcels["nearest_grid_dist_km"] = dist_km
    parcels["score_grid_distance"] = np.exp(-dist_km / GRID_DISTANCE_DECAY_KM)

    logger.info(
        f"  Grid distance done - median: {np.nanmedian(dist_km):.1f} km, "
        f"mean score: {parcels['score_grid_distance'].mean():.3f}"
    )
    return parcels


# ---------------------------------------------------------------------------
# Score 3: Distance to urban center
# ---------------------------------------------------------------------------

def compute_urban_distance_score(
    parcels: gpd.GeoDataFrame,
    osm_infrastructure_path: Path = OSM_INFRASTRUCTURE_PATH,
) -> gpd.GeoDataFrame:
    """
    Distance from each parcel centroid to the nearest qualifying urban center
    (population >= MIN_URBAN_POPULATION = 100k).
    Score: exp( -dist_km / 50).

    Reads the 'urban_centers' layer from the processed osm_infrastructure.gpkg.
    If no qualifying center exists, falls back to the nearest center regardless
    of population and logs a warning.
    """
    logger.info(
        f"Computing urban distance score (pop >= {MIN_URBAN_POPULATION:,})…"
    )

    urban = gpd.read_file(osm_infrastructure_path, layer="urban_centers")
    if urban.crs != parcels.crs:
        urban = urban.to_crs(parcels.crs)

    qualified = urban[urban["population"].fillna(0) >= MIN_URBAN_POPULATION].copy()
    if qualified.empty:
        logger.warning(
            f"  No urban center with pop >= {MIN_URBAN_POPULATION:,}; "
            "using all centers as fallback"
        )
        qualified = urban.copy()

    logger.info(
        f"  {len(qualified)} qualifying urban center(s): "
        + ", ".join(qualified["name"].dropna().tolist())
    )

    pts = _centroids(parcels)
    extra = [c for c in ["name", "population"] if c in qualified.columns]
    joined = _nearest_join(pts, qualified, "_urban_dist_m", extra)

    dist_m = joined["_urban_dist_m"].fillna(np.inf).values.astype(float)
    dist_km = dist_m / 1_000.0

    parcels = parcels.copy()
    parcels["nearest_urban_dist_km"] = dist_km
    parcels["score_urban_distance"] = np.exp(-dist_km / URBAN_DISTANCE_DECAY_KM)

    logger.info(
        f"  Urban distance done - median: {np.nanmedian(dist_km):.1f} km, "
        f"mean score: {parcels['score_urban_distance'].mean():.3f}"
    )
    return parcels


# ---------------------------------------------------------------------------
# Score 4: Parcel size
# ---------------------------------------------------------------------------

def compute_parcel_size_score(parcels: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Logarithmic size score: clip(log10(area_ha / 10), 0, 1).
    Anchors: 10 ha -> 0.0, 100 ha -> 1.0, >100 ha -> capped at 1.0.
    """
    logger.info("Computing parcel size score (log10 scale)…")

    area_ha = parcels["area_ha"].values.astype(float)
    with np.errstate(divide="ignore", invalid="ignore"):
        raw = np.log10(np.where(area_ha > 0, area_ha / 10.0, np.nan))

    parcels = parcels.copy()
    parcels["score_parcel_size"] = np.clip(raw, 0.0, 1.0)

    logger.info(
        f"  Parcel size done - mean score: {parcels['score_parcel_size'].mean():.3f}"
    )
    return parcels


# ---------------------------------------------------------------------------
# Score 5: Distance to existing data centers
# ---------------------------------------------------------------------------

def compute_dc_distance_score(
    parcels: gpd.GeoDataFrame,
    osm_infrastructure_path: Path = OSM_INFRASTRUCTURE_PATH,
) -> gpd.GeoDataFrame:
    """
    Distance from each parcel centroid to the nearest known data center.
    Score: exp( -dist_km / 30).

    Closer = higher score (workforce-clustering assumption).

    Reads the 'data_centers' layer from osm_infrastructure.gpkg. Degrades
    gracefully if the file is missing, the layer does not exist (e.g. no DCs
    survived the AOI clip), or the layer is empty — in all cases
    nearest_dc_dist_km is NaN and score_dc_distance is 0.0.
    """
    logger.info("Computing data center distance score…")

    def _zero_dc(parcels: gpd.GeoDataFrame, reason: str) -> gpd.GeoDataFrame:
        logger.warning(f"  {reason} - DC distance score set to 0")
        parcels = parcels.copy()
        parcels["nearest_dc_dist_km"] = np.nan
        parcels["score_dc_distance"] = 0.0
        return parcels

    try:
        dcs = gpd.read_file(osm_infrastructure_path, layer="data_centers")
    except Exception as exc:
        return _zero_dc(parcels, f"data_centers layer not available ({exc})")

    if dcs.empty:
        return _zero_dc(parcels, "data_centers layer is empty")

    if dcs.crs != parcels.crs:
        dcs = dcs.to_crs(parcels.crs)

    logger.info(f"  {len(dcs)} data center(s) loaded")

    pts = _centroids(parcels)
    extra = [c for c in ["name"] if c in dcs.columns]
    joined = _nearest_join(pts, dcs, "_dc_dist_m", extra)

    dist_m = joined["_dc_dist_m"].fillna(np.inf).values.astype(float)
    dist_km = dist_m / 1_000.0

    parcels = parcels.copy()
    parcels["nearest_dc_dist_km"] = dist_km
    parcels["score_dc_distance"] = np.exp(-dist_km / DC_DISTANCE_DECAY_KM)

    logger.info(
        f"  DC distance done - median: {np.nanmedian(dist_km):.1f} km, "
        f"mean score: {parcels['score_dc_distance'].mean():.3f}"
    )
    return parcels


# ---------------------------------------------------------------------------
# Composite score
# ---------------------------------------------------------------------------

def compute_composite_score(parcels: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Weighted sum of the five component scores using WEIGHTS from config.
    Result is in [0, 1]; a higher value means a more attractive site.
    """
    logger.info("Computing composite weighted score…")

    component_map = {
        "grid_capacity": "score_grid_capacity",
        "distance_to_grid": "score_grid_distance",
        "distance_to_urban": "score_urban_distance",
        "parcel_size": "score_parcel_size",
        "distance_to_dc": "score_dc_distance",
    }

    total_weight = sum(WEIGHTS[k] for k in component_map)
    composite = np.zeros(len(parcels), dtype=float)
    for key, col in component_map.items():
        w = WEIGHTS[key] / total_weight  # normalise in case weights don't sum to 1
        composite += w * parcels[col].fillna(0.0).values

    parcels = parcels.copy()
    parcels["composite_score"] = composite

    logger.info(
        f"  Composite score - min: {composite.min():.3f}, "
        f"median: {np.median(composite):.3f}, "
        f"max: {composite.max():.3f}"
    )
    return parcels


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_scoring(
    parcels: gpd.GeoDataFrame,
    fingrid_path: Path = FINGRID_PATH,
    osm_infrastructure_path: Path = OSM_INFRASTRUCTURE_PATH,
) -> gpd.GeoDataFrame:
    """
    Run all five Stage 2 scoring steps on *parcels* (expected: Stage 1 survivors)
    and return the enriched GeoDataFrame with component + composite scores.

    Parameters
    ----------
    parcels                : GeoDataFrame with 'area_ha' and 'geometry' columns
    fingrid_path           : processed fingrid_capacity_aoi.gpkg
    osm_infrastructure_path: processed osm_infrastructure.gpkg (multi-layer GPKG
                             with layers: power_lines, urban_centers, data_centers)

    Returns
    -------
    Enriched GeoDataFrame with all score columns added.
    """
    logger.info(f"Starting Stage 2 scoring on {len(parcels):,} parcels…")

    parcels = compute_grid_capacity_score(parcels, fingrid_path)
    parcels = compute_grid_distance_score(parcels, osm_infrastructure_path)
    parcels = compute_urban_distance_score(parcels, osm_infrastructure_path)
    parcels = compute_parcel_size_score(parcels)
    parcels = compute_dc_distance_score(parcels, osm_infrastructure_path)
    parcels = compute_composite_score(parcels)

    logger.info("Stage 2 scoring complete.")
    return parcels


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    run_scoring(parcels=gpd.read_file(OUTPUT_PARCELS_PATH / "parcels_stage2.gpkg"))