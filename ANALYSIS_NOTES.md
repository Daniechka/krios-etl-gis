# Analysis Notes

This document tracks design decisions, assumptions and implementation notes for the Krios site selection analysis pipeline.

## How to run analysis scripts

**Prerequisites:**
```bash
# dependencies are installed (from project root)
pip install -e .
# or if using uv:
uv sync
```

**Run Stage 1 fatal flaw analysis:**
```bash
python -m src.analysis.fatal_flaws
```

Reads `data/processed/parcels.gpkg`, enriches it with suitability flags, saves the result back to new file.

---

## Stage 1 - Fatal Flaw filtering

**Date:** 2026-04-27

**Module:** `src/analysis/fatal_flaws.py`

**Purpose:** Binary pass/fail screen that eliminates parcels with at least one disqualifying characteristic. A parcel must clear *all* constraints to survive Stage 1. Survivors proceed to Stage 2 (opportunity scoring).

### Design philosophy

Fatal flaws are hard constraints - there is no amount of grid capacity or proximity that can compensate for a parcel sitting inside a Natura 2000 protected area or a floodplain. Stage 1 uses strict 0/1 flags rather than continuous scores (the exception is `slope_score`, which is preserved for Stage 2 use). This keeps the logic transparent and auditable.

### New fields added to `parcels.gpkg`

| Field | Type | Description |
|---|---|---|
| `avg_slope_pct` | float | **Mean** slope (%) across all 10m raster pixels within the parcel boundary |
| `slope_std_pct` | float | Population **std dev** of slope % across the same pixels - terrain variability indicator |
| `slope_score` | float | Continuous score: 0 if `avg_slope_pct > 8`, else `1 - avg_slope_pct / 100` |
| `slope_suitable` | int8 0/1 | 0 if `avg_slope_pct > 8%`, 1 otherwise |
| `area_suitable` | int8 0/1 | 0 if `area_ha < 10`, 1 otherwise |
| `natura_overlap_ha` | float | Area of overlap with Natura 2000 sites (hectares) |
| `natura_overlap_pct` | float | Percentage of parcel area overlapping Natura 2000 sites |
| `nature_suitable` | int8 0/1 | 0 if `natura_overlap_pct > 5%`, 1 otherwise |
| `flood_suitable` | int8 0/1 | 0 if parcel intersects any SYKE flood zone (1:100a), 1 otherwise |
| `landuse_suitable` | int8 0/1 | Placeholder - all 1 (no landuse data available yet) |
| `suitable` | int8 0/1 | 0 if *any* `*_suitable` field is 0, 1 otherwise |

Thresholds for area and slope are read from `src/config.py` (`MIN_PARCEL_SIZE_HA = 10`, `MAX_SLOPE_PERCENT = 8`).

### Slope statistics - implementation approach

Per-parcel **mean** and **std dev** of slope are computed together in a single vectorised rasterio + numpy pass (**median is not computed** - bincount aggregation does not support it without sorting per parcel, which would be orders of magnitude slower):

1. Open `slope_gradient_percent.tif` once and read into memory (~350 MB for the 9391×9347 pixel AOI raster at float32)
2. **Rasterize** all parcel geometries onto a grid matching the slope raster, burning 1-based integer parcel indices with `rasterio.features.rasterize`
3. **Bincount aggregation** - flatten both rasters to 1D, mask out background (0) and NoData pixels, then run three `numpy.bincount` calls in one pass: pixel counts, weighted sum of slope values, weighted sum of slope^2 values
4. Mean = sum / count; std dev via population variance identity `Var = E[x^2] − E[x]^2` (clamped to => 0 to absorb floating-point noise); parcels with <= 1 valid pixel receive `NaN` for std dev
5. Parcels with zero valid pixels (outside raster extent) receive `NaN` and are treated as suitable (no evidence of exclusion)

This approach is orders of magnitude faster than reading individual raster windows per parcel - O(raster pixels) instead of O(n_parcels × parcel_pixels).

**Caveat:** The entire slope raster is loaded into RAM. For much larger AOIs or higher-resolution DEMs this may need chunked processing. For the current Oulu 94×93 km test area at 10m resolution this is well within acceptable memory bounds.

### Slope variability (slope_std_pct)

`slope_std_pct` measures terrain "bumpiness" within the parcel:

- **low std dev** -> uniformly inclined surface - easy grading in one direction, predictable earthworks cost
- **high std dev** -> uneven surface - complex earthworks, potentially hidden ridges

**Why it matters:** 2 parcels can share the same `avg_slope_pct` but differ dramatically in character. A parcel gently rising from 0% to 4% across 500 m has the same mean as a parcel alternating between 0% and 4% every 50 m - but the second is far more costly to level.

**10m DEM limitation:** at 10m resolution, each pixel covers 100 m^2. Features narrower than ~20 m (ditches, small ridges) are invisible. `slope_std_pct` at this resolution captures only broad-scale terrain. With a 2m LiDAR DEM, this metric would be a much stronger differentiator of micro-topography complexity and is worth revisiting in production.

**Fatal flaw criterion:** `slope_suitable` is based on `avg_slope_pct` only. `slope_std_pct` does **not** affect Stage 1 exclusion - it is stored for potential use as a soft scoring penalty in Stage 2 and for manual inspection of borderline sites.

### Slope score formula

```
slope_score = 0.0                       if avg_slope_pct > 8
slope_score = 1 - (avg_slope_pct / 100) if avg_slope_pct ≤ 8
```

A flat parcel (0%) scores 1.0; a parcel at exactly the 8% threshold scores 0.92. The division by 100 (not by 8) is deliberate - it produces a gentle curve rather than a cliff at the boundary, making the score useful as a continuous input to Stage 2 weighted scoring.

### Spatial constraint checks (Natura 2000 & flood zones)

**Natura 2000 - Area-based threshold (updated approach):**

Pure GIS intersection (`predicate='intersects'`) is overly defensive for Natura 2000 sites - it excludes parcels that merely touch a protected area boundary or have negligible overlap from digitization errors. Real-world site selection should tolerate minor edge overlaps, especially for large parcels where a 5% boundary overlap might still leave 95% usable area.

Current implementation uses **area-based threshold** (default: 5%):
1. Calculate actual overlap area between parcel and Natura 2000 sites
2. Compute `natura_overlap_pct = (overlap_ha / parcel_area_ha) × 100`
3. Exclude only if `natura_overlap_pct > 5%`

This approach:
- Preserves parcels with <5% overlap (likely boundary effects or digitization artifacts)
- Documents exact overlap via `natura_overlap_ha` and `natura_overlap_pct` columns for transparency
- More aligned with real-world permitting where minor overlaps can be mitigated

**Flood zones - Binary intersection:**

Flood zone checks still use `geopandas.sjoin` with `predicate='intersects'`. Any overlap with SYKE 1:100 year flood zones disqualifies the parcel. Flooding is a binary hazard - there's no "acceptable percentage" of flood risk for critical infrastructure.

### Land use suitability (placeholder)

`landuse_suitable` is set to 1 for all parcels. No zoning or land use classification layer is currently integrated. When a suitable dataset is available (ie Finnish national land use layer from SYKE or municipality zoning data), this field should encode whether the parcel's current designated use is compatible with industrial/infrastructure development.

**Fields to watch:** Forest land, agricultural land and certain protected landscape categories may require rezoning; brownfield/industrial sites score highest.

### Input data

| Layer | Path | Notes |
|---|---|---|
| Parcels | `data/processed/parcels.gpkg` | 112,871 parcels, EPSG:3067 |
| Slope raster | `data/processed/slope_gradient_percent.tif` | 10m resolution, % gradient |
| Natura 2000 | `data/processed/natura2000_sites.gpkg` | EEA dataset, 2024 vintage |
| Flood zones | `data/processed/syke_flood_zones.gpkg` | SYKE 1:100 year return period |

### Output

`data/outputs/parcels_stage1.gpkg` - proper GeoPackage with all original parcel attributes plus 8 new suitability columns.

### Stage 1 results (Oulu AOI)

| Constraint | Excluded | Surviving |
|---|---|---|
| Area < 10 ha | 97,822 | 15,049 |
| Slope > 8% | 5,119 | 107,752 |
| Natura 2000 overlap > 5% | 2,742 | 110,129 |
| Flood zone overlap | 4,811 | 108,060 |
| Land use (placeholder) | 0 | 112,871 |
| **Final suitable** | **99,031** | **13,840 (12.3%)** |

Area is by far the dominant constraint - nearly 87% of parcels are below the 10 ha threshold. Slope is largely benign for this region (terrain is flat to gently rolling around Oulu), with only 4.5% of parcels exceeding the 8% cutoff. Switching Natura 2000 from binary intersection to the >5% area threshold recovered ~4,100 parcels previously excluded by digitisation boundary touches.

### Slope distribution (suitable parcels, n = 13,840)

| Stat | avg_slope_pct | slope_std_pct |
|---|---|---|
| count | 13,669 | 13,667 |
| mean | 2.05% | 1.92% |
| std | 1.14% | 1.22% |
| 25th pct | 1.27% | 1.09% |
| median | 1.73% | 1.58% |
| 75th pct | 2.54% | 2.41% |
| max | 7.99% | 13.79% |

The ~170 parcels with `NaN` slope values lie outside the raster extent and are treated as suitable. The 2-parcel gap between `avg_slope_pct` count and `slope_std_pct` count reflects single-pixel parcels (count = 1) for which std dev is undefined.

Notable: `slope_std_pct` max (13.8%) is substantially higher than `avg_slope_pct` max (8.0%) among suitable parcels - confirming that some parcels with a gentle mean have locally variable terrain. These would be candidates for manual review when a higher-resolution DEM is available.

**Runtime (approx, local machine):**
- Load 112k parcels: ~22s
- Area filter: <1s
- Slope raster bincount (mean + std dev): ~52s
- Natura 2000 dissolve + area overlap: ~105s (bottleneck)
- Flood zone simplify + sjoin: ~6s
- Save to GPKG: ~32s
---

## Stage 2 - Opportunity scoring

**Date:** 2026-04-27

**Module:** `src/analysis/scoring.py`
**Orchestrator:** `src/analysis/pipeline.py`

**Purpose:** Rank the 12.5K Stage 1 survivors by opportunity value using five continuous scores combined into a weighted composite.

### How to run

```bash
# Full pipeline (Stage 1 + 2)- ~3.5 min
python -m src.analysis.pipeline

# Stage 2 only, loading existing Stage 1 output - ~10 sec
python -m src.analysis.pipeline --skip-stage1

# Custom top-N export
python -m src.analysis.pipeline --skip-stage1 --top-n 50
```

### Scoring components

| Component | Weight | Formula | Data source |
|---|---|---|---|
| `score_grid_capacity` | 0.30 | `clip(capacity_MW / 100, 0, 1)` | `fingrid_capacity_headroom.geojson` |
| `score_grid_distance` | 0.25 | `exp(−dist_km / 10)` | `osm_power_lines.geojson` (220/400 kV) |
| `score_urban_distance` | 0.20 | `exp(−dist_km / 50)` | `osm_urban_centers.geojson` |
| `score_parcel_size` | 0.15 | `clip(log10(area_ha / 10), 0, 1)` | `area_ha` from Stage 1 output |
| `score_dc_distance` | 0.10 | `exp(−dist_km / 30)` | `osm_data_centers.geojson` |

`composite_score = Σ(weight_i × score_i)` - range [0, 1].

### New fields added to `parcels_stage2.gpkg`

| Field | Type | Description |
|---|---|---|
| `nearest_capacity_mw` | float | Available Fingrid capacity at closest node (MW) |
| `nearest_capacity_station` | str | Name of closest Fingrid substation node |
| `nearest_grid_dist_km` | float | Distance to nearest 220/400 kV power line (km) |
| `nearest_urban_dist_km` | float | Distance to nearest qualifying urban center (km) |
| `nearest_dc_dist_km` | float | Distance to nearest data center (km) |
| `score_grid_capacity` | float [0,1] | Grid capacity component score |
| `score_grid_distance` | float [0,1] | HV grid proximity score |
| `score_urban_distance` | float [0,1] | Urban proximity score |
| `score_parcel_size` | float [0,1] | Logarithmic area score |
| `score_dc_distance` | float [0,1] | Data center proximity score |
| `composite_score` | float [0,1] | Weighted composite (final rank metric) |


### Distance computation method

All distance scores use **parcel centroid -> nearest feature** via `geopandas.sjoin_nearest`. Distances are in metres (EPSG:3067) then converted to km. This is Euclidean distance - no road-network routing.

### Stage 2 results (Oulu AOI, 12.5K suitable parcels)

| Metric | Value |
|---|---|
| Composite score range | 0.052 - 0.842 |
| Composite score median | 0.486 |
| Mean grid capacity (nearest node) | 230 MW |
| Median distance to HV grid | 4.9 km |
| Median distance to Oulu | 49.8 km |

**Top site characteristics (rank #1):** 2431 ha parcel ~250 m from a 110 kV line with 200 MW available at PIKKARALA substation, 15.4 km south of Oulu city centre. Composite score 0.842.

**Data limitations:**
- Only 2 data centers in OSM for the AOI -> `score_dc_distance` has low discriminating power (10% weight is masked)
- Fingrid capacity nodes are at substation level - nearest-node assignment may span large distances in rural areas
- Urban centres dataset has only 1 qualifying city (Oulu); all sites score on the same decay curve from Oulu

### Output files

| File | Description |
|---|---|
| `data/outputs/parcels_stage2.gpkg` | All 12.5K suitable parcels with Stage 2 scores, sorted by composite_score desc |
| `data/outputs/top_sites.gpkg` | Top 20 sites (separate layer for final map styling) |

### Runtime (Stage 2 only, skip-stage1 mode)
- Load 112k parcels: ~6s
- Filter to 12466 suitable: <1s
- Grid capacity (sjoin_nearest vs 213 Fingrid nodes): <1s
- Grid distance (sjoin_nearest vs 63 HV lines): ~1s
- Urban distance (sjoin_nearest vs 1 city): <1s
- Parcel size score: <1s
- DC distance (sjoin_nearest vs 2 DCs): <1s
- Save to GPKG: ~2s
- **Total: ~10s**
---
