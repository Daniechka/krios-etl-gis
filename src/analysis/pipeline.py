"""
Site Selection pipeline

Chains Stage 1 (fatal flaw filtering) with Stage 2 (opportunity scoring) and
exports a ranked GeoPackage of suitable parcels plus a separate top-sites layer.

Usage
-------------
Run the full two-stage pipeline from the command line:

    python -m src.analysis.pipeline

Or call `run_pipeline()` programmatically, ie from a notebook or downstream script.

Output files
------------
  data/outputs/parcels_stage2.gpkg   all Stage 1 survivors with Stage 2 scores
  data/outputs/top_sites.gpkg        top N sites by composite_score (default 20)
"""

import logging
from pathlib import Path

import geopandas as gpd
import numpy as np

from src.analysis.fatal_flaws import (
    FLOOD_PATH,
    NATURA_PATH,
    PARCELS_PATH,
    SLOPE_RASTER_PATH,
    run_fatal_flaw_analysis,
)
from src.analysis.scoring import (
    FINGRID_PATH,
    OSM_INFRASTRUCTURE_PATH,
    run_scoring,
)
from src.config import OUTPUT_DIR, WEIGHTS

logger = logging.getLogger(__name__)

STAGE1_OUTPUT = OUTPUT_DIR / "parcels_stage1.gpkg"
STAGE2_OUTPUT = OUTPUT_DIR / "parcels_stage2.gpkg"
TOP_SITES_OUTPUT = OUTPUT_DIR / "top_sites.gpkg"

DEFAULT_TOP_N = 20


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _print_top_sites(top: gpd.GeoDataFrame) -> None:
    """Log a concise ranked table of the top sites."""
    cols = [
        "composite_score",
        "score_grid_capacity",
        "score_grid_distance",
        "score_urban_distance",
        "score_parcel_size",
        "score_dc_distance",
        "area_ha",
        "nearest_capacity_mw",
        "nearest_grid_dist_km",
        "nearest_urban_dist_km",
    ]
    present = [c for c in cols if c in top.columns]
    logger.info("\n=== TOP SITES ===")
    for rank, (idx, row) in enumerate(top.iterrows(), start=1):
        parts = [f"#{rank:>2}  composite={row['composite_score']:.3f}"]
        for c in present[1:]:
            val = row.get(c, np.nan)
            if isinstance(val, float):
                parts.append(f"{c.replace('score_','').replace('nearest_','')}"
                              f"={val:.2f}")
        logger.info("  " + "  ".join(parts))


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_pipeline(
    # Stage 1 inputs
    parcels_path: Path = PARCELS_PATH,
    slope_path: Path = SLOPE_RASTER_PATH,
    natura_path: Path = NATURA_PATH,
    flood_path: Path = FLOOD_PATH,
    # Stage 2 inputs
    fingrid_path: Path = FINGRID_PATH,
    osm_infrastructure_path: Path = OSM_INFRASTRUCTURE_PATH,
    # Pipeline options
    stage1_output: Path = STAGE1_OUTPUT,
    stage2_output: Path = STAGE2_OUTPUT,
    top_sites_output: Path = TOP_SITES_OUTPUT,
    top_n: int = DEFAULT_TOP_N,
    skip_stage1: bool = False,
) -> gpd.GeoDataFrame:
    """
    Run the full site selection pipeline.

    Parameters
    ----------
    parcels_path / slope_path / natura_path / flood_path
        Stage 1 input data paths (passed through to run_fatal_flaw_analysis).
    fingrid_path / osm_infrastructure_path / data_centers_path
        Stage 2 input data paths (passed through to run_scoring).
    stage1_output : where to load / save Stage 1 results.
    stage2_output : where to save Stage 2 results (suitable parcels only).
    top_sites_output : where to save the top-N ranked sites layer.
    top_n : number of top sites to export.
    skip_stage1 : if True, load parcels_stage1.gpkg instead of re-running
                  Stage 1 (useful during development to save ~3 min runtimes).

    Returns
    -------
    GeoDataFrame of Stage 1-suitable parcels enriched with Stage 2 scores,
    sorted descending by composite_score.
    """

    # ------------------------------------------------------------------
    # Stage 1
    # ------------------------------------------------------------------
    if skip_stage1:
        if not stage1_output.exists():
            raise FileNotFoundError(
                f"skip_stage1=True but {stage1_output} not found. "
                "Run Stage 1 first or set skip_stage1=False."
            )
        logger.info(f"skip_stage1=True — loading Stage 1 results from {stage1_output}")
        all_parcels = gpd.read_file(stage1_output)
        logger.info(f"  Loaded {len(all_parcels):,} parcels")
    else:
        logger.info("=== Stage 1: Fatal Flaw Analysis ===")
        all_parcels = run_fatal_flaw_analysis(
            parcels_path=parcels_path,
            slope_path=slope_path,
            natura_path=natura_path,
            flood_path=flood_path,
            output_path=stage1_output,
        )

    # ------------------------------------------------------------------
    # Filter to Stage 1 survivors
    # ------------------------------------------------------------------
    suitable = all_parcels[all_parcels["suitable"] == 1].copy()
    total = len(all_parcels)
    n_suitable = len(suitable)
    logger.info(
        f"\n=== Stage 2: Opportunity Scoring ===\n"
        f"  Input: {n_suitable:,} suitable parcels "
        f"({n_suitable / total * 100:.1f}% of {total:,} total)"
    )

    # ------------------------------------------------------------------
    # Stage 2
    # ------------------------------------------------------------------
    suitable = run_scoring(
        parcels=suitable,
        fingrid_path=fingrid_path,
        osm_infrastructure_path=osm_infrastructure_path
    )

    # Sort by composite score descending
    suitable = suitable.sort_values("composite_score", ascending=False).reset_index(
        drop=True
    )

    # ------------------------------------------------------------------
    # Save Stage 2 output
    # ------------------------------------------------------------------
    stage2_output.parent.mkdir(parents=True, exist_ok=True)
    logger.info(f"Saving Stage 2 results to {stage2_output}…")
    suitable.to_file(stage2_output, driver="GPKG", layer="parcels")

    # ------------------------------------------------------------------
    # Top N sites
    # ------------------------------------------------------------------
    top = suitable.head(top_n).copy()
    top["rank"] = range(1, len(top) + 1)
    top_sites_output.parent.mkdir(parents=True, exist_ok=True)
    logger.info(f"Saving top {top_n} sites to {top_sites_output}…")
    top.to_file(top_sites_output, driver="GPKG", layer="top_sites")

    _print_top_sites(top)

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    logger.info("\n=== Pipeline complete ===")
    logger.info(f"  Suitable parcels scored : {n_suitable:,}")
    logger.info(f"  Composite score range   : "
                f"{suitable['composite_score'].min():.3f} – "
                f"{suitable['composite_score'].max():.3f}")
    logger.info(f"  Stage 2 output          : {stage2_output}")
    logger.info(f"  Top {top_n} sites output       : {top_sites_output}")
    logger.info(
        f"\n  Weights used:\n" +
        "\n".join(f"    {k}: {v}" for k, v in WEIGHTS.items())
    )

    return suitable


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    import argparse

    parser = argparse.ArgumentParser(
        description="Run the Krios site selection pipeline (Stage 1 + Stage 2)."
    )
    parser.add_argument(
        "--skip-stage1",
        action="store_true",
        default=False,
        help=(
            "Load existing parcels_stage1.gpkg instead of re-running Stage 1 "
            "(saves ~3 min of computation)."
        ),
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=DEFAULT_TOP_N,
        help=f"Number of top sites to export (default: {DEFAULT_TOP_N}).",
    )
    args = parser.parse_args()

    run_pipeline(skip_stage1=args.skip_stage1, top_n=args.top_n)
