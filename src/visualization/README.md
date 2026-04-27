# Webmap generator

`generate_map.py` builds a single self-contained Leaflet HTML at
`docs/index.html`. All GeoJSON layers are inlined as JS variables, so the
output works from `file://`, a local server, or GitHub Pages with no fetches.

## Dependencies (runtime)

The map uses [**Leaflet 1.9.4**](https://leafletjs.com/) loaded from the unpkg
CDN with a Subresource Integrity hash:

```html
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
        integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo="
        crossorigin=""></script>
```

That single CDN script is the only third-party JS the page loads — everything
else (sidebar UI, basemap pills, measure tool, score popups, parcel selection,
rank quick-jump buttons, choropleth styling) is hand-written vanilla JS inside
`HTML_TEMPLATE` in `generate_map.py`. The integrity hash means an offline
browser will refuse to execute a tampered Leaflet bundle, and pinning to
`1.9.4` keeps the visualization reproducible over time.

Tile providers used at runtime (no API keys required):

- **Carto Light** — `cartodb-basemaps-{s}.global.ssl.fastly.net`
- **OpenStreetMap (France mirror)** — `tile.openstreetmap.fr/osmfr/...`
- **Esri World Imagery** — `services.arcgisonline.com/.../World_Imagery/MapServer`

## Prerequisites

- `uv sync` has been run from the repo root (creates `.venv` with `geopandas`,
  `pyogrio`, etc.).
- `.env` points at the data the map should read. Two paths are needed:
  ```bash
  PROCESSED_DATA_DIR=/mnt/d/data/kryos/processed   # GPKGs from the ETL pipeline
  OUTPUT_DIR=/mnt/d/data/kryos/outputs             # parcels_stage1/2 + top_sites
  ```
  See `.env.example` in the repo root.

## Build the map

From the repo root:

```bash
uv run python -m src.visualization.generate_map
```

What it does:

1. Reads each source layer (AOI, parcels stages 1/2, Fingrid, OSM, SYKE,
   Natura 2000, top_sites).
2. Filters parcels to ≥ 10 ha; for Stage 1 keeps only `suitable == 0`.
3. Reprojects to WGS84, simplifies geometries (tolerances tuned per layer),
   trims float precision to 5 decimals.
4. Inlines everything into the HTML template via `/*__DATA_<NAME>__*/null`
   sentinels.
5. Writes `docs/index.html` (~13 MB).

Re-run any time data or styling changes.

## Preview locally

```bash
cd docs && python3 -m http.server 8765
# open http://localhost:8765
```

The `docs/` folder is exactly what GitHub Pages serves, so what you see locally
is what reviewers will see online. Hard-refresh (Ctrl/Cmd + Shift + R) after
each rebuild — the browser caches `index.html` aggressively.

## Sharing without a server

Because all data is inlined, you can also:

- **Double-click** `docs/index.html` to open it in a browser (no server needed).
- **Email it as an attachment** — the recipient gets the full interactive map.

OSM tiles may 403 from `file://` due to OSM's referer policy; switch to the
**Carto Light** or **Satellite** basemap pill if that happens. From a real
HTTP origin (localhost, GitHub Pages) all three basemaps work.

## Layout

- `generate_map.py`
  - `_load_layers()` — reads GPKGs, builds the `{name: geojson_str}` dict.
  - `_to_geojson()` — reproject + simplify + drop columns + round coords.
  - `HTML_TEMPLATE` — full self-contained HTML/CSS/JS, sidebar UI, layer
    factories, basemap pills, measure tool, parcel selection, etc.
  - `main()` — orchestrates load → inject → write.
