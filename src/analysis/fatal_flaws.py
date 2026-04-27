"""
Stage 1 Fatal Flaw analysis

Enriches each parcel with binary suitability flags and continuous slope score.
All *_suitable fields are 0/1; final `suitable` is 0 if any component is 0.

New fields added to parcels layer:
  avg_slope_pct     : mean slope (%) across all raster pixels within the parcel
  slope_score       : continuous [0,1] score - 0 if avg_slope_pct > MAX_SLOPE_PERCENT,
                      else (1 - avg_slope_pct / 100)
  slope_suitable    : 0 if avg_slope_pct > MAX_SLOPE_PERCENT, 1 otherwise
  area_suitable     : 0 if area_ha < MIN_PARCEL_SIZE_HA, 1 otherwise
  natura_overlap_ha : area of overlap with Natura 2000 sites (hectares)
  natura_overlap_pct: percentage of parcel overlapping Natura 2000 sites
  nature_suitable   : 0 if natura_overlap_pct > 5%, 1 otherwise
  flood_suitable    : 0 if parcel intersects any SYKE flood zone, 1 otherwise
  landuse_suitable  : 1 for all parcels (placeholder - no landuse data yet)
  suitable          : 0 if any *_suitable == 0, 1 otherwise
"""

import logging
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.features import rasterize
from shapely.geometry import box

from src.config import (
    MAX_SLOPE_PERCENT,
    MIN_PARCEL_SIZE_HA,
    PROCESSED_DATA_DIR,
)

logger = logging.getLogger(__name__)

PARCELS_PATH = PROCESSED_DATA_DIR / "parcels.gpkg"
SLOPE_RASTER_PATH = PROCESSED_DATA_DIR / "slope_gradient_percent.tif"
NATURA_PATH = PROCESSED_DATA_DIR / "natura2000_sites.gpkg"
FLOOD_PATH = PROCESSED_DATA_DIR / "syke_flood_zones.gpkg"

# Note: parcels.gpkg is actually a GeoJSON file with a misleading extension
# (created by the MML processor before the project standardised on GPKG).
# Stage 1 output is saved to a proper GeoPackage in data/outputs/.
OUTPUT_PARCELS_PATH = PROCESSED_DATA_DIR.parent / "outputs" / "parcels_stage1.gpkg"

SUITABLE_FLAGS = [
    "slope_suitable",
    "area_suitable",
    "nature_suitable",
    "flood_suitable",
    "landuse_suitable",
]


# ---------------------------------------------------------------------------
# Zonal statistics: average slope per parcel
# ---------------------------------------------------------------------------

def _rasterize_parcel_ids(parcels: gpd.GeoDataFrame, transform, shape, crs) -> np.ndarray:
    """Burn parcel index (1-based) into a raster matching the slope grid."""
    if parcels.crs != crs:
        parcels = parcels.to_crs(crs)

    shapes = (
        (geom, idx + 1)
        for idx, geom in enumerate(parcels.geometry)
        if geom is not None and geom.is_valid
    )
    return rasterize(
        shapes,
        out_shape=shape,
        transform=transform,
        fill=0,
        dtype=np.int32,
    )


def compute_slope_stats(parcels: gpd.GeoDataFrame, slope_path: Path) -> gpd.GeoDataFrame:
    """
    Compute per-parcel mean slope from the slope raster using a vectorised
    bincount approach: burn parcel IDs once, then aggregate in one numpy pass.
    Orders of magnitude faster than reading individual raster windows.
    """
    logger.info("Computing slope statistics (rasterio bincount method)…")

    with rasterio.open(slope_path) as src:
        raster_crs = src.crs
        transform = src.transform
        shape = (src.height, src.width)
        nodata = src.nodata

        # Clip to raster extent before rasterizing to avoid unnecessary work
        raster_bbox = box(*src.bounds)
        parcels_proj = parcels.to_crs(raster_crs) if parcels.crs != raster_crs else parcels
        in_extent = parcels_proj.intersects(raster_bbox)

        logger.info(
            f"  {in_extent.sum():,} / {len(parcels):,} parcels overlap slope raster"
        )

        # Rasterize only parcels that touch the raster extent
        parcel_id_raster = _rasterize_parcel_ids(parcels_proj, transform, shape, raster_crs)

        # Read slope - float32 to keep memory reasonable (~350 MB for 9k×9k grid)
        slope = src.read(1).astype(np.float32)

    # Mask nodata
    if nodata is not None:
        slope = np.where(slope == nodata, np.nan, slope)

    # --- Vectorised aggregation via bincount ---
    flat_ids = parcel_id_raster.ravel()          # values: 0 (bg) or 1..n
    flat_slope = slope.ravel()

    valid_mask = (flat_ids > 0) & ~np.isnan(flat_slope)
    valid_ids = flat_ids[valid_mask]
    valid_slope = flat_slope[valid_mask]

    n = len(parcels)
    counts = np.bincount(valid_ids, minlength=n + 1)[1:]          # skip background bucket
    sums = np.bincount(valid_ids, weights=valid_slope.astype(np.float64), minlength=n + 1)[1:]

    with np.errstate(invalid="ignore", divide="ignore"):
        avg_slopes = np.where(counts > 0, sums / counts, np.nan)

    parcels = parcels.copy()
    parcels["avg_slope_pct"] = avg_slopes

    # Parcels with no raster coverage get slope_suitable = 1 (no evidence of exclusion)
    parcels["slope_score"] = np.where(
        np.isnan(avg_slopes),
        np.nan,
        np.where(avg_slopes > MAX_SLOPE_PERCENT, 0.0, 1.0 - avg_slopes / 100.0),
    )
    parcels["slope_suitable"] = np.where(
        np.isnan(avg_slopes),
        1,
        np.where(avg_slopes > MAX_SLOPE_PERCENT, 0, 1),
    ).astype(np.int8)

    covered = int((~np.isnan(avg_slopes)).sum())
    excluded = int((parcels["slope_suitable"] == 0).sum())
    logger.info(
        f"  Slope stats done - {covered:,} parcels with data, "
        f"{excluded:,} excluded (avg_slope > {MAX_SLOPE_PERCENT}%)"
    )
    return parcels


# ---------------------------------------------------------------------------
# Area suitability
# ---------------------------------------------------------------------------

def compute_area_suitability(parcels: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Flag parcels smaller than MIN_PARCEL_SIZE_HA as unsuitable."""
    logger.info(f"Computing area suitability (min {MIN_PARCEL_SIZE_HA} ha)…")
    parcels = parcels.copy()
    parcels["area_suitable"] = (parcels["area_ha"] >= MIN_PARCEL_SIZE_HA).astype(np.int8)
    excluded = int((parcels["area_suitable"] == 0).sum())
    logger.info(f"  {excluded:,} parcels excluded (area < {MIN_PARCEL_SIZE_HA} ha)")
    return parcels


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_constraint(path: Path, target_crs, simplify_tolerance_m: float = 10.0) -> gpd.GeoDataFrame:
    """
    Load a constraint layer, reproject, repair, and simplify geometries.

    Natura 2000 polygons can have >250k vertices each; precise intersection
    tests at that resolution are overkill for parcels of 10+ ha. Simplifying
    to 10m (= slope raster pixel size) reduces vertex counts by 10-100x with
    negligible impact on suitability results.
    """
    gdf = gpd.read_file(path)[["geometry"]].copy()
    if gdf.crs != target_crs:
        gdf = gdf.to_crs(target_crs)
    gdf["geometry"] = gdf.geometry.buffer(0)
    gdf = gdf[gdf.geometry.is_valid & ~gdf.geometry.is_empty]
    gdf["geometry"] = gdf.geometry.simplify(simplify_tolerance_m, preserve_topology=True)
    gdf = gdf[gdf.geometry.is_valid & ~gdf.geometry.is_empty]
    return gdf


def _flag_conflicted(
    parcels: gpd.GeoDataFrame,
    constraint: gpd.GeoDataFrame,
    col: str,
) -> gpd.GeoDataFrame:
    """Spatial join parcels vs constraint, set col=0 for intersecting parcels."""
    joined = gpd.sjoin(
        parcels[["geometry"]],
        constraint,
        how="left",
        predicate="intersects",
    )
    conflicted = joined.index[joined["index_right"].notna()].unique()
    parcels = parcels.copy()
    parcels[col] = np.int8(1)
    parcels.loc[conflicted, col] = np.int8(0)
    return parcels


# ---------------------------------------------------------------------------
# Natura 2000 suitability
# ---------------------------------------------------------------------------

def compute_nature_suitability(
    parcels: gpd.GeoDataFrame, natura_path: Path, overlap_threshold_pct: float = 5.0
) -> gpd.GeoDataFrame:
    """
    Flag parcels based on area of overlap with Natura 2000 sites.
    
    Uses area-based threshold instead of simple intersection to avoid excluding
    parcels that only share a border or have negligible overlap (digitization errors).
    
    Args:
        parcels: GeoDataFrame with parcels
        natura_path: path to Natura 2000 sites
        overlap_threshold_pct: threshold percentage (default: 5%). Parcels with
                               >5% overlap are excluded, <=5% are kept.
    
    Returns:
        GeoDataFrame with added columns:
            - natura_overlap_ha: area of overlap in hectares
            - natura_overlap_pct: percentage of parcel overlapping Natura sites
            - nature_suitable: 0 if overlap > threshold, 1 otherwise
    
    Geometries are simplified to 10m before joining - Natura 2000 MultiPolygons
    can have >250k vertices, making raw operations slow for large parcel sets.
    """
    logger.info("Computing Natura 2000 suitability...")
    natura = _load_constraint(natura_path, parcels.crs)
    logger.info(f"  Loaded {len(natura)} Natura 2000 sites (geometries simplified to 10m)")
    
    # Dissolve all Natura sites into a single geometry for efficient intersection
    logger.info("  Dissolving Natura 2000 sites into single geometry...")
    natura_union = natura.unary_union
    
    # Calculate overlap area for each parcel
    parcels = parcels.copy()
    
    # Initialize columns with explicit dtypes
    parcels["natura_overlap_ha"] = np.float64(0.0)
    parcels["natura_overlap_pct"] = np.float64(0.0)
    parcels["nature_suitable"] = np.int8(1)  # Initialize all as suitable (1)
    
    logger.info(f"  Calculating overlap areas for {len(parcels):,} parcels...")
    
    # Calculate intersection area for each parcel
    for idx in parcels.index:
        parcel_geom = parcels.loc[idx, "geometry"]
        if parcel_geom.intersects(natura_union):
            intersection = parcel_geom.intersection(natura_union)
            overlap_area_m2 = intersection.area
            overlap_ha = overlap_area_m2 / 10000

            parcel_area_ha = parcels.loc[idx, "area_ha"]
            overlap_pct = (overlap_ha / parcel_area_ha * 100) if parcel_area_ha > 0 else 0
            parcels.loc[idx, "natura_overlap_ha"] = overlap_ha
            parcels.loc[idx, "natura_overlap_pct"] = overlap_pct
            # Only mark as unsuitable if overlap exceeds threshold
            if overlap_pct > overlap_threshold_pct:
                parcels.loc[idx, "nature_suitable"] = np.int8(0)
    excluded = int((parcels["nature_suitable"] == 0).sum())
    total_overlap = parcels[parcels["natura_overlap_pct"] > 0]

    logger.info(f"  {len(total_overlap):,} parcels have some Natura 2000 overlap")
    logger.info(f"  {excluded:,} parcels excluded (>{overlap_threshold_pct}% overlap)")
    logger.info(f"  {len(total_overlap) - excluded:,} parcels kept (<={overlap_threshold_pct}% overlap)")

    return parcels


# ---------------------------------------------------------------------------
# Flood zone suitability
# ---------------------------------------------------------------------------

def compute_flood_suitability(
    parcels: gpd.GeoDataFrame, flood_path: Path
) -> gpd.GeoDataFrame:
    """
    Flag parcels that intersect any SYKE flood hazard zone (1:100 year return period).
    Geometries simplified to 10m for performance.
    """
    logger.info("Computing flood zone suitability...")
    flood = _load_constraint(flood_path, parcels.crs)
    logger.info(f"  Loaded {len(flood)} flood zone polygons (geometries simplified to 10m)")

    parcels = _flag_conflicted(parcels, flood, "flood_suitable")

    excluded = int((parcels["flood_suitable"] == 0).sum())
    logger.info(f"  {excluded:,} parcels excluded (flood zone overlap)")
    return parcels


# ---------------------------------------------------------------------------
# Land use suitability (placeholder)
# ---------------------------------------------------------------------------

def compute_landuse_suitability(parcels: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Placeholder: all parcels receive landuse_suitable = 1.
    No landuse constraint data is currently available.
    Replace this logic when a landuse layer is integrated.
    """
    logger.info("Computing landuse suitability (placeholder - all parcels = 1)…")
    parcels = parcels.copy()
    parcels["landuse_suitable"] = np.int8(1)
    return parcels


# ---------------------------------------------------------------------------
# Final suitable flag
# ---------------------------------------------------------------------------

def compute_final_suitability(parcels: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Aggregate all *_suitable flags into a single `suitable` column.
    A parcel is suitable (1) only if every component flag is 1.
    """
    logger.info("Computing final suitability flag…")
    parcels = parcels.copy()
    flag_matrix = parcels[SUITABLE_FLAGS].values  # shape (n, 5), dtype int8
    parcels["suitable"] = np.where(
        (flag_matrix == 0).any(axis=1), np.int8(0), np.int8(1)
    )
    total = len(parcels)
    suitable = int((parcels["suitable"] == 1).sum())
    logger.info(
        f"  Final result - {suitable:,} suitable / {total:,} total "
        f"({suitable / total * 100:.1f}%)"
    )
    return parcels


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_fatal_flaw_analysis(
    parcels_path: Path = PARCELS_PATH,
    slope_path: Path = SLOPE_RASTER_PATH,
    natura_path: Path = NATURA_PATH,
    flood_path: Path = FLOOD_PATH,
    output_path: Path = OUTPUT_PARCELS_PATH,
) -> gpd.GeoDataFrame:
    """
    Run the full Stage 1 fatal flaw analysis and save enriched parcels.

    Parameters
    ----------
    parcels_path : path to input parcels file (GeoJSON or GPKG)
    slope_path   : path to slope gradient raster (%)
    natura_path  : path to Natura 2000 sites GeoPackage
    flood_path   : path to SYKE flood zones GeoPackage
    output_path  : where to save the enriched GeoPackage
                   (defaults to data/outputs/parcels_stage1.gpkg)

    Returns
    -------
    Enriched GeoDataFrame with all new suitability fields.
    """

    logger.info(f"Loading parcels from {parcels_path}…")
    parcels = gpd.read_file(parcels_path)
    logger.info(f"  Loaded {len(parcels):,} parcels (CRS: {parcels.crs})")

    # Drop any pre-existing suitability columns so we start clean
    cols_to_drop = [
        c for c in parcels.columns
        if c in SUITABLE_FLAGS + ["suitable", "avg_slope_pct", "slope_score",
                                   "natura_overlap_ha", "natura_overlap_pct"]
    ]
    if cols_to_drop:
        parcels = parcels.drop(columns=cols_to_drop)
        logger.info(f"  Dropped pre-existing columns: {cols_to_drop}")

    parcels = compute_area_suitability(parcels)
    parcels = compute_slope_stats(parcels, slope_path)
    parcels = compute_nature_suitability(parcels, natura_path)
    parcels = compute_flood_suitability(parcels, flood_path)
    parcels = compute_landuse_suitability(parcels)
    parcels = compute_final_suitability(parcels)

    logger.info(f"Saving enriched parcels to {output_path}…")
    parcels.to_file(output_path, driver="GPKG", layer="parcels")
    logger.info("Done.")

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
    run_fatal_flaw_analysis()
