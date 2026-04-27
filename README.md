# KRIOS GIS data center site selection

Geospatial analysis pipeline for identifying optimal data center locations in the Oulu region, Finland (50-100km radius).

## Project overview

This project implements a multi-criteria site selection model that:
- Filters land parcels based on fatal flaw criteria (protected areas, flood zones, size, slope)
- Scores remaining sites based on proximity to grid infrastructure, urban centers, and existing facilities
- Produces an interactive map with top candidate locations

## Tech stack

- **Python 3.10+** with UV package manager
- **GIS libraries**: GeoPandas, Rasterio, Shapely, PyProj
- **Visualization**: Folium (interactive web maps)
- **CRS**: ETRS89 / TM35FIN (EPSG:3067) - Finland's national projection

## Project structure

```
krios/
├── data/
│   ├── raw/           # OG downloaded data - bronze
│   ├── processed/     # cleaned, harmonized data - silver
│   └── outputs/       # final results (GeoJSON, maps) - gold
├── src/
│   ├── collectors/    # data collection modules
│   ├── processors/    # ETL and harmonization
│   ├── analysis/      # suitability scoring logic
│   └── visualization/ # map generation
├── notebooks/         # exploratory analysis (just in case)
├── pyproject.toml     # UV dependencies
├── README.md
├── ANALYSIS_NOTES.md  # progress monitoring of the GIS analysis
├── DATA_COLLECTION_NOTES.md  # progress monitoring of the ETL pipeline developemnt
├── DECISIONS.md       # trade-offs and assumptions
└── .gitignore
```

## Setup

### Prerequisites
- Python 3.10 or higher
- [UV](https://github.com/astral-sh/uv) package manager

### Installation

```bash
git clone <repository-url>
cd krios

# Create virtualenv and install all dependencies (including the `krios` CLI)
uv sync
source .venv/bin/activate   # Windows: .venv\Scripts\activate
```

### API keys

Create a `.env` file in the project root (already in `.gitignore`):

```bash
MML_API_KEY=your_mml_api_key_here
FINGRID_API_KEY=your_fingrid_key_here
```

- **MML**: [Register here](https://omatili.maanmittauslaitos.fi/user/new/avoimet-rajapintapalvelut) - required for parcels and DEM
- **Fingrid**: [Register here](https://data.fingrid.fi/)

## Running the pipeline

All commands can be run from the project root.

### 1.  Collect geodata

Run **all** collectors at once:

```bash
# via the installed CLI (after uv sync)
krios collect

# or directly from the project root
python main.py collect

# or as a module
python -m src.collectors.run_all
```

Run **specific** collectors only:

```bash
krios collect --collectors osm natura2000 syke

# available names: osm, natura2000, syke, mml, fingrid
```

Raw data is saved to `data/raw/`.

| Collector | Source | API key needed? |
|-----------|--------|-----------------|
| `osm` | OpenStreetMap (Overpass) | No |
| `natura2000` | EEA Natura 2000 REST | No |
| `syke` | SYKE flood zones WFS | No |
| `mml` | MML land parcels OGC API | **Yes** - `MML_API_KEY` |
| `fingrid` | Fingrid grid capacity | Stub - manual workflow |

### 2. Process raw data

Each processor reprojects its source to EPSG:3067, clips to the AOI, writes to `data/processed/`.
Run them after the corresponding collector has finished:

```bash
# MML land parcels (translate field names, crop to AOI, compute area_ha)
python -m src.processors.mml_parcel_processor

# OSM infrastructure layers (reproject, clip, QC checks)
python -m src.processors.osm_processor

# Natura 2000 protected areas (translate fields, reproject, clip)
python -m src.processors.natura2000_processor

# SYKE flood hazard zones (translate fields, filter noise polygons < 5 m^2, clip)
python -m src.processors.syke_processor

# Fingrid grid capacity (set CRS to EPSG:3067, translate fields, clip)
python -m src.processors.fingrid_processor
```

**DEM / slope** - requires a manual download first (automated DEM API is not implemented yet, see `DATA_COLLECTION_NOTES.md`):

```bash
# Build tile index from manually downloaded tiles (data/raw/etrs-tm35fin-n2000/)
python scripts/create_dem_tindex.py

# Merge tiles intersecting the AOI, compute slope gradient, save raster
python -m src.processors.dem_to_slope
```

Outputs: `data/processed/parcels.gpkg`, `data/processed/osm_infrastructure.gpkg`,
`data/processed/natura2000_sites.gpkg`, `data/processed/syke_flood_zones.gpkg`,
`data/processed/fingrid_capacity_aoi.gpkg`, `data/processed/slope_gradient_percent.tif`

### 3. Analyse

**Stage 1 - Fatal flaw filtering** (~3.5 min)

Hard pass/fail screen across four constraints: minimum area (10+ ha), maximum slope (<5%), Natura 2000 overlap (< 5% of parcel area), flood zone intersection. Roughly 12% of parcels survive.

```bash
python -m src.analysis.fatal_flaws
```

Output: `data/outputs/parcels_stage1.gpkg`

---

**Stage 2 - Opportunity scoring** (requires Stage 1 output)

Ranks Stage 1 survivors by a weighted composite score across five criteria: grid capacity headroom (30%), distance to HV lines (25%), distance to urban centre (20%), parcel size (15%), distance to existing data centres (10%).

```bash
python -m src.analysis.pipeline --skip-stage1

# Export more top candidates (default is 20)
python -m src.analysis.pipeline --skip-stage1 --top-n 20
```

Outputs: `data/outputs/parcels_stage2.gpkg`, `data/outputs/top_sites.gpkg`

---

**Full pipeline - Stage 1 + Stage 2 in one go** 

```bash
python -m src.analysis.pipeline
```

See `ANALYSIS_NOTES.md` for scoring formulas, field definitions, and runtime benchmarks.

## Data sources

1. **Land parcels**: MML Kiinteistöjaotus (Finnish Land Survey)
2. **Exclusion zones**: 
   - Natura 2000 (EEA)
   - Flood hazard zones (SYKE)
3. **Grid network**: Fingrid capacity data
4. **Urban centers**: Statistics Finland / OpenStreetMap
5. **Electricity infrastructure**: OpenStreetMap (power lines, substations)
6. **DEM/Elevation**: MML elevation model

## Scoring methodology

See `DECISIONS.md` for detailed rationale.

**Weights**:
- Grid capacity headroom: 30%
- Distance to 220/400kV network: 25%
- Distance to urban center: 20%
- Parcel size: 15%
- Distance to existing data centers: 10%

## Export options

- **GeoJSON**: compatible with QGIS, ArcGIS, other GIS software
- **HTML map**: self-contained interactive map (no server required)
- Future: REST API for live queries

## Known limitations

- OpenStreetMap data completeness varies by region
- Fingrid capacity data may have temporal lag
- Static snapshot analysis (no temporal trends)
- Simplified scoring model (no multi-criteria optimization)
- No ground-truthing of parcel conditions

## Production roadmap

See `DECISIONS.md` for full details on:
- PostGIS migration strategy
- Automated data refresh workflows
- Multi-country scaling architecture
- API / frontend development



