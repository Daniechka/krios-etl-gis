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
| `avg_slope_pct` | float | Mean slope (%) across all 10m raster pixels within the parcel boundary |
| `slope_score` | float | Continuous score: 0 if `avg_slope_pct > 8`, else `1 - avg_slope_pct / 100` |
| `slope_suitable` | int8 0/1 | 0 if `avg_slope_pct > 8%`, 1 otherwise |
| `area_suitable` | int8 0/1 | 0 if `area_ha < 10`, 1 otherwise |
| `nature_suitable` | int8 0/1 | 0 if parcel intersects any Natura 2000 site, 1 otherwise |
| `flood_suitable` | int8 0/1 | 0 if parcel intersects any SYKE flood zone (1:100a), 1 otherwise |
| `landuse_suitable` | int8 0/1 | Placeholder - all 1 (no landuse data available yet) |
| `suitable` | int8 0/1 | 0 if *any* `*_suitable` field is 0, 1 otherwise |

Thresholds for area and slope are read from `src/config.py` (`MIN_PARCEL_SIZE_HA = 10`, `MAX_SLOPE_PERCENT = 8`).

### Slope statistics - implementation approach

Per-parcel mean slope is computed with a vectorised rasterio + numpy approach:

1. Open `slope_gradient_percent.tif` once and read into memory (~350 MB for the 9391×9347 pixel AOI raster at float32)
2. **Rasterize** all parcel geometries onto a grid matching the slope raster, burning 1-based integer parcel indices with `rasterio.features.rasterize`
3. **Bincount aggregation** - flatten both rasters to 1D, mask out background (0) and NoData pixels, then use `numpy.bincount` (once for pixel counts, once weighted by slope values) to compute per-parcel sums and counts in a single vectorised pass
4. Mean slope = sum / count; parcels with zero valid pixels (outside raster extent) receive `NaN` and are treated as suitable (no evidence of exclusion)

This approach is orders of magnitude faster than reading individual raster windows per parcel - O(raster pixels) instead of O(n_parcels × parcel_pixels).

**Caveat:** The entire slope raster is loaded into RAM. For much larger AOIs or higher-resolution DEMs this may need chunked processing. For the current Oulu 94×93 km test area at 10m resolution this is well within acceptable memory bounds.

### Slope score formula

```
slope_score = 0.0                       if avg_slope_pct > 8
slope_score = 1 - (avg_slope_pct / 100) if avg_slope_pct ≤ 8
```

A flat parcel (0%) scores 1.0; a parcel at exactly the 8% threshold scores 0.92. The division by 100 (not by 8) is deliberate - it produces a gentle curve rather than a cliff at the boundary, making the score useful as a continuous input to Stage 2 weighted scoring.

### Spatial constraint checks (Natura 2000 & flood zones)

Both constraint checks use `geopandas.sjoin` with `predicate='intersects'`. This covers three spatial relationships in one pass: parcels that **overlap**, **touch**, or are **contained within** a constraint polygon are all flagged as unsuitable. Invalid constraint geometries are repaired with a zero-width buffer before joining.

**Why not `predicate='within'`?** A parcel that merely touches a protected area boundary without being fully inside it is still operationally problematic (permitting risk, buffer requirements). Intersects is conservative and defensible choice.

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
| Natura 2000 overlap | 6,870 | 106,001 |
| Flood zone overlap | 4,811 | 108,060 |
| Land use (placeholder) | 0 | 112,871 |
| **Final suitable** | **100,405** | **12,466 (11.0%)** |

Area is by far the dominant constraint - nearly 87% of parcels are below the 10 ha threshold. This is expected for Finnish cadastral data, which includes many small residential and agricultural lots. Slope is largely benign for this region (mean 3.0%, terrain is flat to gently rolling around Oulu), with only 4.5% of parcels exceeding the 8% cutoff.

**Runtime (3.3 minutes on local machine):**
- Load 112k parcels: ~22s
- Area filter: <1s
- Slope raster bincount: ~27s
- Natura 2000 simplify + sjoin: ~134s (bottleneck - complex MultiPolygons, see note above)
- Flood zone simplify + sjoin: ~6s
- Save to GPKG: ~5s
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
- Fingrid capacity nodes are at substation level — nearest-node assignment may span large distances in rural areas
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
