# KRIOS GIS Site Selection — Oulu AOI

Geospatial site-selection scaffold for finding candidate data center / industrial sites
in northern Finland. Combines parcels, grid capacity, environmental constraints and
infrastructure into a reproducible pipeline plus a Leaflet web map.

## Project layout

```
src/
  collectors/     - one module per source (MML, Fingrid, OSM, SYKE, Natura 2000)
  processors/     - clip to AOI, harmonise CRS to EPSG:3067, write GeoPackages
  analysis/       - fatal_flaws.py (Stage 1)  — Stage 2 scoring lives on a branch
  visualization/  - generate_map.py: builds a single self-contained Leaflet HTML
scripts/          - DEM tindex helpers
data/
  raw/            - source downloads (gitignored)
  processed/      - cleaned, projected layers (gitignored - can be overridden via env)
  outputs/        - analysis outputs (gitignored)
docs/
  index.html      - generated, committed - this is what GitHub Pages serves
```

## Setup

```bash
uv sync               # creates .venv with all deps
cp .env.example .env  # then edit if processed data lives elsewhere
```

`.env` keys:
- `PROCESSED_DATA_DIR` — overrides the default `data/processed/` location
  (handy when the processed GeoPackages live on a shared drive instead of inside
  the repo, e.g. `PROCESSED_DATA_DIR=/mnt/d/data/kryos/processed`).

## Running the pipeline

```bash
# Stage 1 - fatal flaw filter (slope, area, Natura, flood)
uv run python -m src.analysis.fatal_flaws
```

See [ANALYSIS_NOTES.md](ANALYSIS_NOTES.md) for design decisions and runtime stats.

## Building the web map

```bash
uv run python -m src.visualization.generate_map
```

This reads from `PROCESSED_DATA_DIR`, simplifies geometries, reprojects to WGS84,
inlines all GeoJSON as JS variables, and writes a single self-contained
`docs/index.html` (~8 MB). The file:

- Opens by **double-clicking** in any browser (no server required)
- Can be sent as an **email attachment** (data is inlined, no fetches)
- Can be hosted on **GitHub Pages** (or any static host)

### Layers shown

| Layer | Source | Notes |
|---|---|---|
| AOI boundary | `data/aoi_test.geojson` | dashed outline |
| Parcels (≥ 10 ha) | `parcels.gpkg` | filtered + simplified, neutral fill |
| Top-ranked sites | placeholder | populated when scoring stage produces ranked output |
| Fingrid substations | `fingrid_capacity_aoi.gpkg` | sized by available_capacity_mw, traffic-light tier |
| OSM substations | `osm_infrastructure.gpkg::substations` | off by default |
| HV power lines | `osm_infrastructure.gpkg::power_lines` | styled by voltage (110 / 220 / 400 kV) |
| Power plants | `osm_infrastructure.gpkg::power_plants` | colored by fuel |
| Urban centres | `osm_infrastructure.gpkg::urban_centers` | labelled (Oulu) |
| Natura 2000 | `natura2000_sites.gpkg` | constraint, orange |
| Flood zones | `syke_flood_zones.gpkg` | constraint, blue |

### Local preview

```bash
cd docs && python3 -m http.server 8080
# open http://localhost:8080
```

### Publish to GitHub Pages

GitHub Pages only allows `/` (root) or `/docs` as the source folder when
deploying from a branch — that's why the map is generated into `docs/`.

1. Commit `docs/index.html` to `main`:
   ```bash
   git add docs/index.html
   git commit -m "publish webmap"
   git push origin main
   ```
2. GitHub repo → **Settings → Pages**:
   - Source: `Deploy from a branch`
   - Branch: `main`, Folder: `/docs`
   - Save.
3. Wait ~60 s. The map will be live at:
   ```
   https://<your-github-username>.github.io/<repo-name>/
   ```
4. Re-run the generator and push whenever data or styling changes — each push
   triggers a fresh Pages deploy in ~10–20 s:
   ```bash
   uv run python -m src.visualization.generate_map
   git add docs/index.html
   git commit -m "update map"
   git push
   ```
   Hard-refresh (Ctrl/Cmd + Shift + R) the live URL after deploys; GH Pages
   caches `index.html` aggressively. Build status is visible under the repo's
   **Actions** tab (`pages-build-deployment` workflow).

## Caveats

- Parcels are filtered to ≥ 10 ha but **not yet scored or ranked** — the
  `top_sites` layer in the map is a placeholder that the scoring branch will
  populate.
- Distances elsewhere in the analysis are Euclidean; no road-network routing.
- See [ANALYSIS_NOTES.md](ANALYSIS_NOTES.md) and
  [DATA_COLLECTION_NOTES.md](DATA_COLLECTION_NOTES.md) for source-specific
  caveats and known data quirks.
