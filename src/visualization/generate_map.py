"""
Generate a single self-contained Leaflet web map at docs/index.html.

All GeoJSON layers are inlined as JS variables, so the resulting HTML can be:
  - Opened by double-clicking (no server needed)
  - Served from GitHub Pages (or any static host)
  - Sent as an email attachment

Source data paths are configurable via PROCESSED_DATA_DIR and OUTPUT_DIR
env vars - see src/config.py and .env.example. This module only renders the
map; suitability scoring lives in src/analysis (parcels_stage1.gpkg /
parcels_stage2.gpkg / top_sites.gpkg are read as-is).

Usage
-----
    python -m src.visualization.generate_map
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

import geopandas as gpd

from src.config import (
    AOI_FILE,
    CRS_FINLAND,
    CRS_WGS84,
    OUTPUT_DIR,
    PROCESSED_DATA_DIR,
    PROJECT_ROOT,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

WEBMAP_DIR = PROJECT_ROOT / "docs"
OUTPUT_HTML = WEBMAP_DIR / "index.html"

OSM_GPKG = "osm_infrastructure.gpkg"

# Simplification tolerances in metres (applied in EPSG:3067 before reprojecting
# to WGS84). Picked to keep visual fidelity at zoom levels 8-13 while shrinking
# the inlined GeoJSON payload.
SIMPLIFY_M = {
    "parcels": 30,
    "rejected": 50,
    "natura": 100,
    "flood": 80,
    "hv_lines": 50,
}

MIN_PARCEL_AREA_HA = 10  # Hard filter from the assignment brief

# Coordinate decimals in the inlined GeoJSON. 5 decimals ≈ 1.1 m at the equator
# (better at 65°N), more than enough for a web map and ~halves payload size.
COORD_DECIMALS = 5

# Matches a coordinate-style float like "25.123456789012345" inside the
# GeoJSON output of GeoPandas. We round to COORD_DECIMALS, in place.
_FLOAT_RE = re.compile(r"-?\d+\.\d+")


def _round_floats(geojson: str, decimals: int = COORD_DECIMALS) -> str:
    """Trim float precision in a GeoJSON string to keep payloads small."""
    fmt = "{:." + str(decimals) + "f}"

    def _sub(m: re.Match) -> str:
        v = float(m.group(0))
        s = fmt.format(v).rstrip("0").rstrip(".")
        return s if s else "0"

    return _FLOAT_RE.sub(_sub, geojson)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_geojson(
    gdf: gpd.GeoDataFrame,
    simplify_m: float | None = None,
    columns: list[str] | None = None,
    centroids: bool = False,
) -> str:
    """
    Reproject to WGS84, optionally simplify (in projected CRS), drop unused
    columns, and serialise to a compact GeoJSON string with rounded floats.
    """
    if gdf.empty:
        return '{"type":"FeatureCollection","features":[]}'

    gdf = gdf.copy()

    if centroids:
        gdf["geometry"] = gdf.geometry.centroid

    if simplify_m:
        # Simplify in the projected CRS so the tolerance is in metres.
        proj = gdf if (gdf.crs and gdf.crs.is_projected) else gdf.to_crs(CRS_FINLAND)
        proj = proj.assign(
            geometry=proj.geometry.simplify(simplify_m, preserve_topology=True)
        )
        proj = proj[proj.geometry.notna() & ~proj.geometry.is_empty]
        gdf = proj

    if gdf.crs is None or gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(CRS_WGS84)

    if columns is not None:
        keep = [c for c in columns if c in gdf.columns] + ["geometry"]
        gdf = gdf[keep]

    return _round_floats(gdf.to_json(drop_id=True))


# ---------------------------------------------------------------------------
# Layer loading
# ---------------------------------------------------------------------------

def _load_layers() -> dict[str, str]:
    """Read each source layer, transform, and return a {name: geojson_str} dict."""
    pp = PROCESSED_DATA_DIR
    layers: dict[str, str] = {}

    logger.info("AOI…")
    aoi = gpd.read_file(AOI_FILE)
    layers["aoi"] = _to_geojson(aoi)

    logger.info("Parcels — Stage 2 (scored, >= %d ha)…", MIN_PARCEL_AREA_HA)
    stage2 = gpd.read_file(OUTPUT_DIR / "parcels_stage2.gpkg")
    stage2 = stage2[stage2["area_ha"] >= MIN_PARCEL_AREA_HA]
    logger.info("  %d scored parcels", len(stage2))
    stage2_cols = [
        "property_id", "area_ha", "avg_slope_pct", "slope_score",
        "composite_score",
        "nearest_capacity_mw", "nearest_capacity_station", "score_grid_capacity",
        "nearest_grid_dist_km", "score_grid_distance",
        "nearest_urban_dist_km", "score_urban_distance",
        "nearest_dc_dist_km", "score_dc_distance",
        "score_parcel_size",
    ]
    layers["parcels"] = _to_geojson(
        stage2,
        simplify_m=SIMPLIFY_M["parcels"],
        columns=stage2_cols,
    )

    logger.info("Parcels — Stage 1 (rejected, >= %d ha)…", MIN_PARCEL_AREA_HA)
    stage1 = gpd.read_file(OUTPUT_DIR / "parcels_stage1.gpkg")
    rejected = stage1[(stage1["area_ha"] >= MIN_PARCEL_AREA_HA) & (stage1["suitable"] == 0)]
    logger.info("  %d rejected parcels (after >= %d ha filter)", len(rejected), MIN_PARCEL_AREA_HA)
    layers["rejected"] = _to_geojson(
        rejected,
        simplify_m=SIMPLIFY_M["rejected"],
        columns=[
            "property_id", "area_ha", "avg_slope_pct",
            "area_suitable", "slope_suitable", "nature_suitable",
            "flood_suitable", "landuse_suitable",
        ],
    )

    # top_sites is a separate, smaller curated layer (top-N candidates).
    # Read if available; otherwise stay empty so the template still renders.
    top_path = OUTPUT_DIR / "top_sites.gpkg"
    if top_path.exists():
        logger.info("Top sites…")
        top = gpd.read_file(top_path)
        layers["top_sites"] = _to_geojson(
            top,
            simplify_m=SIMPLIFY_M["parcels"],
            columns=stage2_cols + ["rank"],
        )
    else:
        layers["top_sites"] = '{"type":"FeatureCollection","features":[]}'

    logger.info("Fingrid capacity nodes…")
    fingrid = gpd.read_file(pp / "fingrid_capacity_aoi.gpkg")
    layers["fingrid"] = _to_geojson(
        fingrid,
        columns=["station_name", "total_capacity_mw", "available_capacity_mw"],
    )

    logger.info("OSM HV power lines…")
    hv = gpd.read_file(pp / OSM_GPKG, layer="power_lines")
    layers["hv_lines"] = _to_geojson(
        hv,
        simplify_m=SIMPLIFY_M["hv_lines"],
        columns=["voltage", "name"],
    )

    logger.info("OSM substations…")
    subs = gpd.read_file(pp / OSM_GPKG, layer="substations")
    layers["substations"] = _to_geojson(
        subs,
        columns=["name", "voltage", "operator"],
        centroids=True,
    )

    logger.info("OSM power plants…")
    plants = gpd.read_file(pp / OSM_GPKG, layer="power_plants")
    # Many OSM plants only fill `generator_source`, not `plant_source` -
    # coalesce into a single `fuel` field for the popup.
    src_a = plants["plant_source"] if "plant_source" in plants.columns else None
    src_b = plants["generator_source"] if "generator_source" in plants.columns else None
    if src_a is not None and src_b is not None:
        plants["fuel"] = src_a.fillna(src_b)
    elif src_a is not None:
        plants["fuel"] = src_a
    elif src_b is not None:
        plants["fuel"] = src_b
    else:
        plants["fuel"] = None
    layers["power_plants"] = _to_geojson(
        plants,
        columns=["name", "fuel", "operator"],
        centroids=True,
    )

    logger.info("OSM urban centers…")
    urban = gpd.read_file(pp / OSM_GPKG, layer="urban_centers")
    layers["urban"] = _to_geojson(
        urban, columns=["name", "population"], centroids=True
    )

    logger.info("Natura 2000 sites…")
    natura = gpd.read_file(pp / "natura2000_sites.gpkg")
    layers["natura"] = _to_geojson(
        natura,
        simplify_m=SIMPLIFY_M["natura"],
        columns=["site_code", "site_name", "site_type"],
    )

    logger.info("SYKE flood zones…")
    flood = gpd.read_file(pp / "syke_flood_zones.gpkg")
    layers["flood"] = _to_geojson(
        flood,
        simplify_m=SIMPLIFY_M["flood"],
        columns=["name", "return_period", "depth_zone_class"],
    )

    return layers


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

def _inject_layers(template: str, layers: dict[str, str]) -> str:
    out = template
    for name, gj in layers.items():
        token = f"/*__DATA_{name.upper()}__*/null"
        if token not in out:
            logger.warning("Token %s not found in template", token)
        out = out.replace(token, gj)
    return out


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    logger.info("Reading source layers from %s", PROCESSED_DATA_DIR)
    layers = _load_layers()

    for name, gj in layers.items():
        n_features = len(json.loads(gj).get("features", []))
        logger.info("  %-13s %5d features  %6d KB", name, n_features, len(gj) // 1024)

    html = _inject_layers(HTML_TEMPLATE, layers)

    WEBMAP_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_HTML.write_text(html, encoding="utf-8")
    logger.info(
        "Wrote %s (%.1f MB)",
        OUTPUT_HTML,
        OUTPUT_HTML.stat().st_size / 1024 / 1024,
    )


# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------
# Sentinels of the form `/*__DATA_<NAME>__*/null` are replaced by injected
# GeoJSON strings. The `null` keeps the template parseable as valid JS even
# before substitution (handy when editing the template directly).

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>KRIOS — Site Selection (Oulu AOI)</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
      integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin=""/>
<style>
  :root {
    --sidebar-w: 340px;
    --bg: #ffffff;
    --bg-soft: #f8fafc;
    --fg: #0f172a;
    --muted: #64748b;
    --border: #e2e8f0;
    --accent: #0f172a;

    --cap-high: #16a34a;
    --cap-mid:  #f59e0b;
    --cap-low:  #dc2626;

    --line-400: #6d28d9;
    --line-220: #8b5cf6;
    --line-110: #c4b5fd;

    --natura:   #4ade80;
    --flood:    #2563eb;

    --plant-wind:    #0ea5e9;
    --plant-solar:   #eab308;
    --plant-hydro:   #06b6d4;
    --plant-biomass: #84cc16;
    --plant-oil:     #525252;
    --plant-gas:     #f97316;
    --plant-nuclear: #a855f7;
    --plant-other:   #94a3b8;

    --parcel:        #475569;
    --top-low:       #fee08b;
    --top-mid:       #fdae61;
    --top-high:      #1a9850;
  }

  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; height: 100%; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
    color: var(--fg);
    background: var(--bg);
    display: flex;
    height: 100vh;
    overflow: hidden;
    -webkit-font-smoothing: antialiased;
  }

  /* ---------- Sidebar ---------- */
  aside#sidebar {
    width: var(--sidebar-w);
    flex-shrink: 0;
    background: var(--bg-soft);
    border-right: 1px solid var(--border);
    overflow-y: auto;
    overflow-x: hidden;
    font-size: 13px;
    line-height: 1.5;
  }
  aside header {
    padding: 18px 20px 14px;
    border-bottom: 1px solid var(--border);
    background: var(--bg);
  }
  aside header h1 {
    margin: 0 0 2px;
    font-size: 16px;
    font-weight: 700;
    letter-spacing: -0.01em;
  }
  aside header p {
    margin: 0;
    color: var(--muted);
    font-size: 12px;
  }

  aside section {
    padding: 14px 20px;
    border-bottom: 1px solid var(--border);
  }
  aside section:last-child { border-bottom: none; }
  aside h2 {
    margin: 0 0 10px;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--muted);
    font-weight: 600;
  }
  .section-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    margin: 0 0 10px;
  }
  .section-head h2 { margin: 0; }
  .btn-link {
    appearance: none;
    background: transparent;
    border: 1px solid var(--border);
    color: var(--fg);
    font: inherit;
    font-size: 11px;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    font-weight: 600;
    padding: 3px 8px;
    border-radius: 4px;
    cursor: pointer;
  }
  .btn-link:hover { background: var(--bg-soft); border-color: #94a3b8; }
  .btn-link:active { background: #e2e8f0; }
  .btn-link.is-icon {
    padding: 3px 6px;
    display: inline-flex;
    align-items: center;
    justify-content: center;
  }
  .btn-link.is-icon svg { display: block; }

  /* Basemap pill row */
  .basemap-row {
    display: flex;
    gap: 6px;
  }
  .basemap-pill {
    appearance: none;
    flex: 1;
    background: var(--bg);
    border: 1px solid var(--border);
    color: var(--muted);
    font: inherit;
    font-size: 12px;
    font-weight: 500;
    padding: 6px 8px;
    border-radius: 6px;
    cursor: pointer;
    transition: background 80ms ease, border-color 80ms ease, color 80ms ease;
  }
  .basemap-pill:hover { background: var(--bg-soft); color: var(--fg); }
  .basemap-pill.is-active {
    background: var(--fg);
    border-color: var(--fg);
    color: #fff;
    font-weight: 600;
  }

  /* Per-rank quick-jump buttons under the Top-ranked parcels layer */
  .rank-row {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    margin: 4px 0 8px 26px;
  }
  .rank-btn {
    appearance: none;
    background: var(--bg);
    border: 1px solid var(--border);
    color: var(--fg);
    font: inherit;
    font-size: 11px;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
    width: 26px;
    height: 24px;
    border-radius: 5px;
    cursor: pointer;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    padding: 0;
    transition: background 80ms ease, border-color 80ms ease, color 80ms ease, transform 80ms ease;
  }
  .rank-btn:hover  { background: var(--bg-soft); border-color: var(--fg); color: var(--fg); }
  .rank-btn:active { transform: translateY(1px); }
  .rank-btn.is-active {
    background: var(--fg);
    border-color: var(--fg);
    color: #fff;
  }

  /* Measure tooltip on the map */
  .leaflet-tooltip.measure-tooltip {
    background: rgba(15, 23, 42, 0.9);
    color: #fff;
    border: none;
    border-radius: 4px;
    padding: 3px 7px;
    font-size: 12px;
    font-weight: 600;
    box-shadow: 0 1px 3px rgba(0,0,0,0.3);
    white-space: nowrap;
  }
  .leaflet-tooltip.measure-tooltip:before { display: none; }
  aside h3 {
    margin: 12px 0 6px;
    font-size: 11px;
    color: var(--muted);
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.04em;
  }
  aside h3:first-child { margin-top: 0; }

  /* Layer & legend rows */
  .row, label.layer {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 3px 0;
    cursor: default;
  }

  label.layer { cursor: pointer; user-select: none; }
  label.layer input { margin: 0 4px 0 0; cursor: pointer; }
  label.layer:hover { color: #000; }

  /* Swatches */
  .sw {
    display: inline-block;
    flex-shrink: 0;
    width: 18px;
    height: 12px;
    border-radius: 2px;
    border: 1px solid rgba(0,0,0,0.1);
  }
  .sw.dot { width: 12px; height: 12px; border-radius: 50%; }
  .sw.line {
    height: 0; border: none; border-top-width: 3px; border-top-style: solid;
    border-radius: 0;
    align-self: center;
  }
  .sw.line.dashed { border-top-style: dashed; }

  /* Caveats */
  .caveats p {
    margin: 0 0 8px;
    color: var(--muted);
    font-size: 12px;
  }
  .caveats p:last-child { margin-bottom: 0; }
  .caveats a { color: var(--muted); }

  /* ---------- Map ---------- */
  #map {
    flex: 1;
    height: 100vh;
    background: #eef2f7;
  }

  /* Top-rank label divIcons */
  .rank-badge {
    background: #0f172a;
    color: #fff;
    border-radius: 9999px;
    padding: 1px 7px;
    font-size: 11px;
    font-weight: 700;
    box-shadow: 0 1px 3px rgba(0,0,0,0.4);
    border: 1px solid #fff;
  }

  /* ---------- Mobile ---------- */
  #sidebar-toggle {
    display: none;
    position: absolute;
    top: 10px; left: 10px;
    z-index: 1000;
    background: var(--bg);
    border: 1px solid var(--border);
    border-radius: 4px;
    padding: 6px 10px;
    font-size: 14px;
    cursor: pointer;
    box-shadow: 0 1px 4px rgba(0,0,0,0.15);
  }
  @media (max-width: 800px) {
    body { flex-direction: column; }
    aside#sidebar {
      width: 100%;
      max-height: 50vh;
      border-right: none;
      border-bottom: 1px solid var(--border);
      display: none;
    }
    body.sidebar-open aside#sidebar { display: block; }
    #sidebar-toggle { display: block; }
    #map { height: auto; flex: 1; }
  }

  .leaflet-popup-content {
    margin: 10px 12px;
    font-size: 13px;
    line-height: 1.4;
  }
  .leaflet-popup-content b { color: var(--accent); }
  .leaflet-popup-content table { border-collapse: collapse; margin-top: 4px; }
  .leaflet-popup-content td { padding: 1px 8px 1px 0; vertical-align: top; }
  .leaflet-popup-content td:first-child { color: var(--muted); }
</style>
</head>
<body>

<button id="sidebar-toggle" aria-label="Toggle sidebar">☰ Layers</button>

<aside id="sidebar">
  <header>
    <h1>KRIOS Site Selection</h1>
    <p>Oulu AOI · suitability map (v0)</p>
  </header>

  <section>
    <h2>Basemap</h2>
    <div class="basemap-row">
      <button class="basemap-pill is-active" type="button" data-basemap="carto">Carto Light</button>
      <button class="basemap-pill" type="button" data-basemap="osm">OSM</button>
      <button class="basemap-pill" type="button" data-basemap="satellite">Satellite</button>
    </div>
  </section>

  <section>
    <div class="section-head">
      <h2>Layers</h2>
      <div style="display:flex; gap:6px;">
        <button id="layers-home" class="btn-link is-icon" type="button" title="Snap the map back to the AOI" aria-label="Home"><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 11l9-8 9 8"/><path d="M5 10v10h14V10"/><path d="M10 20v-6h4v6"/></svg></button>
        <button id="layers-toggle-all" class="btn-link" type="button" title="Hide all layers">Hide all</button>
        <button id="layers-reset" class="btn-link" type="button" title="Reset layers and view to defaults">Reset</button>
      </div>
    </div>
    <label class="layer"><input type="checkbox" data-layer="aoi" checked>
      <span class="sw line dashed" style="border-top-color:#000"></span> AOI boundary</label>
    <label class="layer"><input type="checkbox" data-layer="rejected">
      <span class="sw" style="background:rgba(239,68,68,0.30); border-color:#dc2626"></span> Stage 1 — rejected parcels</label>
    <label class="layer"><input type="checkbox" data-layer="parcels" checked>
      <span class="sw" style="background:linear-gradient(90deg,#f46d43,#fdae61,#fee08b,#d9ef8b,#a6d96a,#1a9850)"></span> Stage 2 — scored parcels</label>
    <label class="layer"><input type="checkbox" data-layer="top_sites" checked>
      <span class="sw" style="background:rgba(245,158,11,0.15); border:2px solid #f59e0b"></span> Top-ranked parcels</label>
    <div id="top-rank-list" class="rank-row" aria-label="Jump to a top-ranked parcel"></div>
    <label class="layer"><input type="checkbox" data-layer="fingrid" checked>
      <span class="sw dot" style="background:var(--cap-high)"></span> Fingrid substations (capacity)</label>
    <label class="layer"><input type="checkbox" data-layer="substations">
      <span class="sw dot" style="background:#94a3b8"></span> OSM substations</label>
    <label class="layer"><input type="checkbox" data-layer="hv_lines" checked>
      <span class="sw line" style="border-top-color:var(--line-400)"></span> HV power lines</label>
    <label class="layer"><input type="checkbox" data-layer="power_plants" checked>
      <span class="sw dot" style="background:var(--plant-solar)"></span> Power plants</label>
    <label class="layer"><input type="checkbox" data-layer="urban" checked>
      <span class="sw dot" style="background:#0f172a"></span> Urban centres</label>
    <label class="layer"><input type="checkbox" data-layer="natura" checked>
      <span class="sw" style="background:rgba(134,239,172,0.45); border-color:var(--natura)"></span> Natura 2000</label>
    <label class="layer"><input type="checkbox" data-layer="flood" checked>
      <span class="sw" style="background:rgba(37,99,235,0.30); border-color:var(--flood)"></span> Flood zones</label>
  </section>

  <section>
    <h2>Legend</h2>

    <h3>Composite score (Stage 2)</h3>
    <div class="row"><span class="sw" style="background:#1a9850"></span> ≥ 0.75 — excellent</div>
    <div class="row"><span class="sw" style="background:#a6d96a"></span> 0.60 – 0.75</div>
    <div class="row"><span class="sw" style="background:#d9ef8b"></span> 0.45 – 0.60</div>
    <div class="row"><span class="sw" style="background:#fee08b"></span> 0.30 – 0.45</div>
    <div class="row"><span class="sw" style="background:#fdae61"></span> 0.15 – 0.30</div>
    <div class="row"><span class="sw" style="background:#f46d43"></span> &lt; 0.15 — marginal</div>

    <h3>Rejection reasons (Stage 1)</h3>
    <div class="row"><span class="sw" style="background:rgba(239,68,68,0.30); border-color:#dc2626"></span> Failed at least one fatal-flaw filter (slope / nature / flood / land-use)</div>

    <h3>Substation capacity (Fingrid)</h3>
    <div class="row"><span class="sw dot" style="background:var(--cap-high)"></span> ≥ 200 MW available</div>
    <div class="row"><span class="sw dot" style="background:var(--cap-mid)"></span> 50 – 200 MW</div>
    <div class="row"><span class="sw dot" style="background:var(--cap-low)"></span> &lt; 50 MW</div>

    <h3>Power lines</h3>
    <div class="row"><span class="sw line" style="border-top-color:var(--line-400); border-top-width:3px"></span> 400 kV</div>
    <div class="row"><span class="sw line" style="border-top-color:var(--line-220); border-top-width:2px"></span> 220 kV</div>
    <div class="row"><span class="sw line" style="border-top-color:var(--line-110); border-top-width:1.5px"></span> 110 kV</div>

    <h3>Power plants (fuel)</h3>
    <div class="row"><span class="sw dot" style="background:var(--plant-wind)"></span> Wind</div>
    <div class="row"><span class="sw dot" style="background:var(--plant-solar)"></span> Solar</div>
    <div class="row"><span class="sw dot" style="background:var(--plant-hydro)"></span> Hydro</div>
    <div class="row"><span class="sw dot" style="background:var(--plant-biomass)"></span> Biomass</div>
    <div class="row"><span class="sw dot" style="background:var(--plant-gas)"></span> Gas / oil</div>
    <div class="row"><span class="sw dot" style="background:var(--plant-other)"></span> Other / unknown</div>
  </section>

  <section>
    <div class="section-head">
      <h2>Measure</h2>
      <button class="btn-link" type="button" data-measure-clear title="Remove all measurements">Clear</button>
    </div>
    <div class="basemap-row">
      <button class="basemap-pill" type="button" data-measure="distance">Distance</button>
      <button class="basemap-pill" type="button" data-measure="area">Area</button>
    </div>
    <p style="margin:8px 0 0; font-size:11px; color:var(--muted); line-height:1.4;">
      Click to add points · double‑click to finish · Esc to cancel
    </p>
  </section>

</aside>

<div id="map"></div>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
        integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin=""></script>
<script>
/* -----------------------------------------------------------------------
 * Inlined data (replaced at build time by src/visualization/generate_map.py)
 * --------------------------------------------------------------------- */
const LAYER_AOI          = /*__DATA_AOI__*/null;
const LAYER_PARCELS      = /*__DATA_PARCELS__*/null;
const LAYER_REJECTED     = /*__DATA_REJECTED__*/null;
const LAYER_TOP_SITES    = /*__DATA_TOP_SITES__*/null;
const LAYER_FINGRID      = /*__DATA_FINGRID__*/null;
const LAYER_HV_LINES     = /*__DATA_HV_LINES__*/null;
const LAYER_SUBSTATIONS  = /*__DATA_SUBSTATIONS__*/null;
const LAYER_POWER_PLANTS = /*__DATA_POWER_PLANTS__*/null;
const LAYER_URBAN        = /*__DATA_URBAN__*/null;
const LAYER_NATURA       = /*__DATA_NATURA__*/null;
const LAYER_FLOOD        = /*__DATA_FLOOD__*/null;

/* -----------------------------------------------------------------------
 * Map setup
 * --------------------------------------------------------------------- */
// Heavy polygon layers (parcels, flood) use a canvas renderer for performance.
// Points and lines use SVG so click hit-testing is reliable through stacked layers.
const canvasRenderer = L.canvas({ padding: 0.5 });
const svgRenderer    = L.svg({ padding: 0.5 });

const map = L.map("map", {
  zoomControl: true,
  renderer: svgRenderer,
  maxBoundsViscosity: 1.0,
  worldCopyJump: false,
}).setView([65.0, 26.0], 8);
map.attributionControl.setPrefix("Created by <strong>Daria Snytkina</strong>");

const basemaps = {
  carto: L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
    attribution: '&copy; OpenStreetMap &copy; CARTO', subdomains: 'abcd', maxZoom: 19,
  }),
  osm: L.tileLayer("https://{s}.tile.openstreetmap.fr/osmfr/{z}/{x}/{y}.png", {
    attribution: '&copy; OpenStreetMap France &copy; OpenStreetMap contributors',
    subdomains: 'abc', maxZoom: 20,
  }),
  satellite: L.tileLayer("https://mt{s}.google.com/vt/lyrs=s&x={x}&y={y}&z={z}", {
    attribution: 'Imagery &copy; Google', subdomains: ['0','1','2','3'], maxZoom: 20,
  }),
};
let activeBasemap = basemaps.carto.addTo(map);
let activeBasemapKey = "carto";
function setBasemap(key) {
  const next = basemaps[key];
  if (!next || next === activeBasemap) return;
  map.removeLayer(activeBasemap);
  next.addTo(map);
  activeBasemap = next;
  activeBasemapKey = key;
  document.querySelectorAll("[data-basemap]").forEach(el => {
    el.classList.toggle("is-active", el.dataset.basemap === key);
  });
  // Repaint parcels with a stroke appropriate for the basemap
  if (typeof applyParcelStrokeForBasemap === "function") {
    applyParcelStrokeForBasemap(key);
  }
}
document.querySelectorAll("[data-basemap]").forEach(el => {
  el.addEventListener("click", () => setBasemap(el.dataset.basemap));
});

L.control.scale({ imperial: false }).addTo(map);

/* -----------------------------------------------------------------------
 * Custom measure tool (distance + area, geodesic on WGS84)
 * --------------------------------------------------------------------- */
const measureLayer = L.layerGroup().addTo(map);
let measureMode = null;       // 'distance' | 'area' | null
let measurePoints = [];
let measurePreview = null;

function fmtDistance(m) {
  if (m >= 1000) return (m / 1000).toFixed(2) + " km";
  return Math.round(m) + " m";
}
function fmtArea(sqm) {
  if (sqm >= 1e6) return (sqm / 1e6).toFixed(2) + " km²";
  if (sqm >= 1e4) return (sqm / 1e4).toFixed(2) + " ha";
  return Math.round(sqm) + " m²";
}
function totalDistance(latlngs) {
  let d = 0;
  for (let i = 1; i < latlngs.length; i++) d += latlngs[i-1].distanceTo(latlngs[i]);
  return d;
}
// Geodesic polygon area on WGS84 sphere (good enough for AOI scale)
function geodesicArea(latlngs) {
  if (latlngs.length < 3) return 0;
  const R = 6378137, rad = Math.PI / 180;
  let area = 0;
  const n = latlngs.length;
  for (let i = 0, j = n - 1; i < n; j = i++) {
    const xi = latlngs[i].lng * rad, yi = latlngs[i].lat * rad;
    const xj = latlngs[j].lng * rad, yj = latlngs[j].lat * rad;
    area += (xj - xi) * (2 + Math.sin(yi) + Math.sin(yj));
  }
  return Math.abs(area * R * R / 2);
}

function setMeasureButtons() {
  document.querySelectorAll("[data-measure]").forEach(b =>
    b.classList.toggle("is-active", b.dataset.measure === measureMode)
  );
}
function redrawPreview(hover) {
  if (measurePreview) { measureLayer.removeLayer(measurePreview); measurePreview = null; }
  const pts = hover ? [...measurePoints, hover] : [...measurePoints];
  if (pts.length < 2) return;
  const style = { color: "#0891b2", weight: 2, dashArray: "4 4" };
  measurePreview = (measureMode === "area")
    ? L.polygon(pts, { ...style, fillColor: "#06b6d4", fillOpacity: 0.15 })
    : L.polyline(pts, style);
  measurePreview.addTo(measureLayer);
  const label = (measureMode === "area")
    ? fmtArea(geodesicArea(pts))
    : fmtDistance(totalDistance(pts));
  measurePreview.bindTooltip(label, { permanent: true, direction: "right", className: "measure-tooltip" }).openTooltip();
}
function onMeasureClick(e) { measurePoints.push(e.latlng); redrawPreview(); }
function onMeasureMove(e)  { redrawPreview(e.latlng); }
function finishMeasure(e) {
  if (e && e.originalEvent) L.DomEvent.preventDefault(e.originalEvent);
  if (measurePoints.length < 2) { cancelMeasure(); return; }
  let label, geom;
  if (measureMode === "area") {
    label = fmtArea(geodesicArea(measurePoints));
    geom = L.polygon(measurePoints, { color: "#0e7490", weight: 2, fillColor: "#06b6d4", fillOpacity: 0.20 });
  } else {
    label = fmtDistance(totalDistance(measurePoints));
    geom = L.polyline(measurePoints, { color: "#0e7490", weight: 3 });
  }
  geom.bindTooltip(label, { permanent: true, direction: "right", className: "measure-tooltip" });
  geom.addTo(measureLayer);
  geom.openTooltip();
  cancelMeasure();
}
function cancelMeasure() {
  measureMode = null;
  measurePoints = [];
  if (measurePreview) { measureLayer.removeLayer(measurePreview); measurePreview = null; }
  document.getElementById("map").style.cursor = "";
  setMeasureButtons();
  map.off("click", onMeasureClick);
  map.off("mousemove", onMeasureMove);
  map.off("dblclick", finishMeasure);
  map.doubleClickZoom.enable();
}
function startMeasure(mode) {
  cancelMeasure();
  measureMode = mode;
  document.getElementById("map").style.cursor = "crosshair";
  setMeasureButtons();
  map.on("click", onMeasureClick);
  map.on("mousemove", onMeasureMove);
  map.on("dblclick", finishMeasure);
  map.doubleClickZoom.disable();
}
function clearMeasures() { cancelMeasure(); measureLayer.clearLayers(); }

document.querySelectorAll("[data-measure]").forEach(btn => {
  btn.addEventListener("click", () => {
    const mode = btn.dataset.measure;
    measureMode === mode ? cancelMeasure() : startMeasure(mode);
  });
});
document.querySelector("[data-measure-clear]").addEventListener("click", clearMeasures);
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && measureMode) cancelMeasure();
});

/* -----------------------------------------------------------------------
 * Style helpers
 * --------------------------------------------------------------------- */
function maxVoltageKV(v) {
  if (v == null) return 0;
  const parts = String(v).split(/[;,]/);
  let best = 0;
  for (const p of parts) {
    const n = parseInt(p, 10);
    if (!isNaN(n) && n > best) best = n;
  }
  return best / 1000;
}
function lineStyle(f) {
  const kv = maxVoltageKV(f.properties.voltage);
  if (kv >= 400) return { color: "#6d28d9", weight: 3,   opacity: 0.95 };
  if (kv >= 220) return { color: "#8b5cf6", weight: 2,   opacity: 0.90 };
  if (kv >= 110) return { color: "#c4b5fd", weight: 1.5, opacity: 0.85 };
  return                { color: "#cbd5e1", weight: 1,   opacity: 0.7  };
}
function capacityColor(mw) {
  if (mw == null) return "#94a3b8";
  if (mw >= 200) return "#16a34a";
  if (mw >=  50) return "#f59e0b";
  return "#dc2626";
}
function capacityRadius(mw) {
  if (!mw || mw <= 0) return 6;
  return Math.max(7, Math.min(28, Math.sqrt(mw) * 1.4));
}
const PLANT_COLORS = {
  wind: "#0ea5e9", solar: "#eab308", hydro: "#06b6d4",
  biomass: "#84cc16", oil: "#525252", gas: "#f97316",
  nuclear: "#a855f7", coal: "#3f3f46",
};
function plantColor(props) {
  const f = (props.fuel || "").toString().toLowerCase();
  return PLANT_COLORS[f] || "#94a3b8";
}
function fmt(n, digits = 1) {
  return n == null || isNaN(n) ? "—" : Number(n).toFixed(digits);
}
function row(label, value) {
  return `<tr><td>${label}</td><td>${value}</td></tr>`;
}

/* -----------------------------------------------------------------------
 * Layer factories - one function per layer, all return an L.Layer
 * --------------------------------------------------------------------- */
function makeAoiLayer() {
  return L.geoJSON(LAYER_AOI, {
    style: { color: "#000", weight: 2, dashArray: "6 4", fillOpacity: 0 },
    interactive: false,
  });
}

// 6-bin diverging RdYlGn ramp (orange → yellow → green). Bins ~0.15 wide,
// chosen to spread across the actual score range (0.05–0.84). Aligned with
// the legend swatches in the sidebar.
function scoreColor(s) {
  if (s == null || isNaN(s)) return "#cbd5e1";
  if (s >= 0.75) return "#1a9850";  // deep green
  if (s >= 0.60) return "#a6d96a";  // light green
  if (s >= 0.45) return "#d9ef8b";  // yellow-green
  if (s >= 0.30) return "#fee08b";  // warm yellow
  if (s >= 0.15) return "#fdae61";  // light orange
  return "#f46d43";                 // deep orange
}

const PARCEL_STROKE_LIGHT  = { color: "#334155", weight: 0.4 };  // for Carto / OSM
const PARCEL_STROKE_DARK   = { color: "#ffffff", weight: 0.8 };  // for satellite
let   parcelStroke         = PARCEL_STROKE_LIGHT;
const PARCEL_STYLE_SELECTED = { color: "#06b6d4", weight: 2.8, fillColor: "#06b6d4", fillOpacity: 0.30 };

// Generic feature-selection state. Works for any polygon layer that
// registers a click handler via `selectFeature(lyr, restoreFn)`.
let   selectedFeature        = null;
let   selectedFeatureRestore = null;

function parcelDefaultStyle(f) {
  return {
    color:       parcelStroke.color,
    weight:      parcelStroke.weight,
    fillColor:   scoreColor(f.properties?.composite_score),
    fillOpacity: 0.65,
  };
}

function clearParcelSelection() {
  if (selectedFeature && selectedFeatureRestore) {
    try { selectedFeatureRestore(selectedFeature); } catch (e) { /* noop */ }
  }
  selectedFeature = null;
  selectedFeatureRestore = null;
}

function selectFeature(lyr, restoreFn) {
  if (selectedFeature === lyr) return;
  clearParcelSelection();
  selectedFeature = lyr;
  selectedFeatureRestore = restoreFn;
  lyr.setStyle(PARCEL_STYLE_SELECTED);
  if (lyr.bringToFront) { try { lyr.bringToFront(); } catch (e) {} }
}

function applyParcelStrokeForBasemap(key) {
  parcelStroke = (key === "satellite") ? PARCEL_STROKE_DARK : PARCEL_STROKE_LIGHT;
  if (layers && layers.parcels) {
    layers.parcels.setStyle(f =>
      (selectedFeature && selectedFeature.feature === f)
        ? PARCEL_STYLE_SELECTED
        : parcelDefaultStyle(f)
    );
  }
}

function _kmOrDash(v, decimals = 1) {
  return (v == null || isNaN(v)) ? "—" : fmt(v, decimals) + " km";
}

// Score component definitions - keep in sync with src/analysis/scoring.py weights.
// `detail` returns a short, human-readable line describing the underlying input.
const SCORE_COMPONENTS = [
  { key: "score_grid_capacity",  label: "Grid capacity",  weight: 0.30,
    detail: p => {
      const cap = p.nearest_capacity_mw, st = p.nearest_capacity_station;
      if (cap == null && !st) return "no capacity data";
      const parts = [];
      if (cap != null) parts.push(fmt(cap, 0) + " MW headroom");
      if (st)          parts.push("@ " + st);
      return parts.join(" ");
    } },
  { key: "score_grid_distance",  label: "Grid distance",  weight: 0.25,
    detail: p => _kmOrDash(p.nearest_grid_dist_km) + " to nearest line" },
  { key: "score_urban_distance", label: "Urban distance", weight: 0.20,
    detail: p => _kmOrDash(p.nearest_urban_dist_km) + " to nearest city" },
  { key: "score_parcel_size",    label: "Parcel size",    weight: 0.15,
    detail: p => fmt(p.area_ha, 1) + " ha" },
  { key: "score_dc_distance",    label: "DC distance",    weight: 0.10,
    detail: p => (p.nearest_dc_dist_km == null || isNaN(p.nearest_dc_dist_km))
                   ? "no nearby data center"
                   : fmt(p.nearest_dc_dist_km, 1) + " km to nearest DC" },
];

// Compact "score chip" - colored fill with dark text + subtle dark border so
// it stays legible across the full RdYlGn ramp (pale yellows would be unreadable
// as colored text on white).
function _scoreChip(score, big) {
  const has   = (score != null && !isNaN(score));
  const value = has ? fmt(score, 2) : "—";
  const color = has ? scoreColor(score) : "#cbd5e1";
  const dim   = big
    ? "padding:3px 10px;font-size:20px;border-radius:6px"
    : "padding:1px 7px;font-size:12px;border-radius:5px";
  return `<span style="display:inline-block;${dim};font-weight:700;color:#0f172a;
                       background:${color};border:1px solid rgba(15,23,42,0.35);
                       font-variant-numeric:tabular-nums;line-height:1.2">${value}</span>`;
}

// Single horizontal bar row: label · weight · value · detail.
function _scoreBar(label, score, weight, detail) {
  const has   = (score != null && !isNaN(score));
  const pct   = has ? Math.max(0, Math.min(1, score)) * 100 : 0;
  const color = has ? scoreColor(score) : "#cbd5e1";
  const wPct  = (weight * 100).toFixed(0);
  return `
    <div style="margin:7px 0 9px">
      <div style="display:flex;justify-content:space-between;align-items:center;font-size:12px;line-height:1.2;gap:8px">
        <span>
          <span style="font-weight:600;color:#0f172a">${label}</span>
          <span style="color:var(--muted);font-size:10px;margin-left:5px;font-variant-numeric:tabular-nums">w ${wPct}%</span>
        </span>
        ${_scoreChip(score, false)}
      </div>
      <div style="height:6px;background:#e2e8f0;border-radius:3px;margin:5px 0 3px;overflow:hidden;border:1px solid rgba(15,23,42,0.08)">
        <div style="height:100%;width:${pct.toFixed(1)}%;background:${color};border-radius:3px"></div>
      </div>
      ${detail ? `<div style="font-size:11px;color:var(--muted);line-height:1.3">${detail}</div>` : ""}
    </div>
  `;
}

// Renders all five score component bars from a parcel's properties.
function _scoreBars(p) {
  return SCORE_COMPONENTS
    .map(c => _scoreBar(c.label, p[c.key], c.weight, c.detail(p)))
    .join("");
}

// Big composite-score hero card. `meta` is HTML rendered on the right (area / slope / rank).
function _compositeHero(score, meta) {
  const color = (score != null && !isNaN(score)) ? scoreColor(score) : "#cbd5e1";
  return `
    <div style="display:flex;align-items:center;gap:12px;margin:6px 0 8px;padding:9px 11px;
                border-radius:8px;background:linear-gradient(135deg,#f8fafc,#eef2f7);
                border-left:4px solid ${color}">
      <div style="line-height:1.1">
        <div style="font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:600;margin-bottom:4px">Composite</div>
        ${_scoreChip(score, true)}
        <div style="font-size:10px;color:var(--muted);margin-top:5px">weighted 0 – 1</div>
      </div>
      ${meta ? `<div style="margin-left:auto;text-align:right;font-size:11px;color:#475569;line-height:1.4">${meta}</div>` : ""}
    </div>
  `;
}

function makeParcelsLayer() {
  return L.geoJSON(LAYER_PARCELS, {
    renderer: canvasRenderer,
    style: parcelDefaultStyle,
    onEachFeature: (f, lyr) => {
      const p = f.properties || {};
      const meta = `
        <div style="font-size:10px;text-transform:uppercase;letter-spacing:.05em;color:var(--muted);font-weight:600">Parcel #${p.property_id ?? "?"}</div>
        <div style="margin-top:3px"><b>${fmt(p.area_ha, 1)}</b> ha</div>
        <div>${fmt(p.avg_slope_pct, 2)} % slope</div>
      `;
      const html = `
        ${_compositeHero(p.composite_score, meta)}
        <div style="font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);font-weight:600;margin:4px 0 0">Score components</div>
        ${_scoreBars(p)}
      `;
      lyr.bindPopup(html, { maxWidth: 320, minWidth: 270 });
      lyr.on("click", () => selectFeature(lyr, l => l.setStyle(parcelDefaultStyle(l.feature))));
      lyr.on("popupclose", () => {
        if (selectedFeature === lyr) clearParcelSelection();
      });
    },
  });
}

const REJECTED_STYLE = { color: "#dc2626", weight: 0.6, fillColor: "#ef4444", fillOpacity: 0.30 };

// Renders one pass/fail badge for a Stage 1 suitability flag.
// `flag` is 1 (pass), 0 (fail) or null/undefined (unknown).
function _suitBadge(label, flag) {
  let bg, border, color, mark;
  if (flag === 1) {
    bg = "rgba(22,163,74,0.12)"; border = "#16a34a"; color = "#15803d"; mark = "✓";
  } else if (flag === 0) {
    bg = "rgba(220,38,38,0.15)"; border = "#dc2626"; color = "#991b1b"; mark = "✗";
  } else {
    bg = "rgba(148,163,184,0.15)"; border = "#94a3b8"; color = "#475569"; mark = "·";
  }
  return `<span style="display:inline-block;margin:0 4px 4px 0;padding:2px 8px;` +
         `background:${bg};border:1px solid ${border};border-radius:10px;` +
         `font-size:11px;color:${color};font-weight:600">` +
         `<span style="margin-right:4px">${mark}</span>${label}</span>`;
}

function makeRejectedLayer() {
  return L.geoJSON(LAYER_REJECTED, {
    renderer: canvasRenderer,
    style: REJECTED_STYLE,
    onEachFeature: (f, lyr) => {
      const p = f.properties || {};
      const badges =
        _suitBadge("Area",      p.area_suitable) +
        _suitBadge("Slope",     p.slope_suitable) +
        _suitBadge("Nature",    p.nature_suitable) +
        _suitBadge("Flood",     p.flood_suitable) +
        _suitBadge("Land use",  p.landuse_suitable);
      const html = `
        <b>Parcel ${p.property_id ?? "?"}</b>
        <div style="margin:4px 0 6px;font-size:12px;color:#dc2626;font-weight:600">
          Rejected by Stage 1 filter
        </div>
        <table>
          ${row("Area",        fmt(p.area_ha, 1) + " ha")}
          ${row("Avg slope",   fmt(p.avg_slope_pct, 2) + " %")}
        </table>
        <div style="margin:8px 0 4px;font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);font-weight:600">Suitability checks</div>
        <div>${badges}</div>
      `;
      lyr.bindPopup(html, { maxWidth: 320 });
      lyr.on("click", () => selectFeature(lyr, l => l.setStyle(REJECTED_STYLE)));
      lyr.on("popupclose", () => {
        if (selectedFeature === lyr) clearParcelSelection();
      });
    },
  });
}

// Populated by makeTopSitesLayer - lets the rank-shortcut buttons in the
// sidebar look up the corresponding leaflet sub-layer in O(1).
const topSitesByRank = new Map();

// Top sites are usually viewed on satellite, so the default symbology is a
// thick amber outline with a barely-there fill - the imagery underneath stays
// readable but the parcel boundary is unmistakable.
const TOP_SITE_BORDER = "#f59e0b";  // amber - matches the rank-pill gradient

function topSiteDefaultStyle(f) {
  return {
    color:       TOP_SITE_BORDER,
    weight:      3,
    opacity:     1,
    fillColor:   scoreColor(f.properties?.composite_score),
    fillOpacity: 0.12,
  };
}

function makeTopSitesLayer() {
  topSitesByRank.clear();
  return L.geoJSON(LAYER_TOP_SITES, {
    style: topSiteDefaultStyle,
    onEachFeature: (f, lyr) => {
      const p = f.properties || {};
      if (p.rank != null) topSitesByRank.set(Number(p.rank), lyr);
      const meta = `
        <div style="font-size:10px;text-transform:uppercase;letter-spacing:.05em;color:#b45309;font-weight:700">Rank #${p.rank ?? "—"}</div>
        <div style="margin-top:3px;color:var(--muted);font-size:11px">Parcel ${p.property_id ?? "?"}</div>
        <div style="margin-top:2px"><b>${fmt(p.area_ha, 1)}</b> ha · ${fmt(p.avg_slope_pct, 2)} % slope</div>
      `;
      const html = `
        ${_compositeHero(p.composite_score, meta)}
        <div style="font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--muted);font-weight:600;margin:4px 0 0">Score components</div>
        ${_scoreBars(p)}
      `;
      lyr.bindPopup(html, { maxWidth: 320, minWidth: 270 });
      lyr.on("click", () => selectFeature(lyr, l => l.setStyle(topSiteDefaultStyle(l.feature))));
      lyr.on("popupclose", () => {
        if (selectedFeature === lyr) clearParcelSelection();
      });
    },
  });
}

function makeFingridLayer() {
  return L.geoJSON(LAYER_FINGRID, {
    pointToLayer: (f, ll) => L.circleMarker(ll, {
      radius: capacityRadius(f.properties.available_capacity_mw),
      color: "#0f172a",
      weight: 1.2,
      fillColor: capacityColor(f.properties.available_capacity_mw),
      fillOpacity: 0.85,
    }),
    onEachFeature: (f, lyr) => {
      const p = f.properties || {};
      lyr.bindPopup(
        `<b>${p.station_name ?? "Fingrid node"}</b><br>` +
        `<table>` +
        row("Available", fmt(p.available_capacity_mw, 0) + " MW") +
        row("Total",     fmt(p.total_capacity_mw, 0) + " MW") +
        `</table>`
      );
    },
  });
}

function makeSubstationsLayer() {
  return L.geoJSON(LAYER_SUBSTATIONS, {
    pointToLayer: (f, ll) => L.circleMarker(ll, {
      radius: 4, color: "#475569", weight: 1, fillColor: "#cbd5e1", fillOpacity: 0.9,
    }),
    onEachFeature: (f, lyr) => {
      const p = f.properties || {};
      const kv = maxVoltageKV(p.voltage);
      lyr.bindPopup(
        `<b>${p.name || "Substation"}</b><br>` +
        `<table>` +
        row("Voltage",  kv ? fmt(kv, 0) + " kV" : "—") +
        row("Operator", p.operator || "—") +
        `</table>`
      );
    },
  });
}

function makeHvLinesLayer() {
  return L.geoJSON(LAYER_HV_LINES, {
    style: lineStyle,
    onEachFeature: (f, lyr) => {
      const p = f.properties || {};
      const kv = maxVoltageKV(p.voltage);
      lyr.bindPopup(
        `<b>${p.name || "HV line"}</b><br>` +
        (kv ? `${fmt(kv, 0)} kV` : "voltage unknown")
      );
    },
  });
}

function makePowerPlantsLayer() {
  return L.geoJSON(LAYER_POWER_PLANTS, {
    pointToLayer: (f, ll) => L.circleMarker(ll, {
      radius: 5, color: "#0f172a", weight: 1,
      fillColor: plantColor(f.properties || {}), fillOpacity: 0.9,
    }),
    onEachFeature: (f, lyr) => {
      const p = f.properties || {};
      lyr.bindPopup(
        `<b>${p.name || "Power plant"}</b><br>` +
        `<table>` +
        row("Fuel",     p.fuel || "—") +
        row("Operator", p.operator || "—") +
        `</table>`
      );
    },
  });
}

function makeUrbanLayer() {
  const lyr = L.geoJSON(LAYER_URBAN, {
    pointToLayer: (f, ll) => {
      const m = L.circleMarker(ll, {
        radius: 8, color: "#000", weight: 1.5,
        fillColor: "#0f172a", fillOpacity: 0.85,
      });
      m.bindTooltip(f.properties.name || "", {
        permanent: true, direction: "right", offset: [10, 0],
        className: "urban-label",
      });
      return m;
    },
    onEachFeature: (f, lyr) => {
      const p = f.properties || {};
      lyr.bindPopup(
        `<b>${p.name || "Urban centre"}</b><br>` +
        `Population: ${p.population ? Number(p.population).toLocaleString() : "—"}`
      );
    },
  });
  return lyr;
}

// Reusable "exclusion zone" badge - red pill with a no-entry (ban) icon,
// for layers that act as fatal-flaw filters in the suitability model.
function exclusionBadge(label) {
  return `
    <div style="display:inline-flex;align-items:center;gap:6px;margin:6px 0 4px;
                padding:3px 9px;background:rgba(220,38,38,0.10);border:1px solid #dc2626;
                border-radius:12px;font-size:11px;font-weight:700;color:#991b1b;
                text-transform:uppercase;letter-spacing:.04em">
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
           stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
        <circle cx="12" cy="12" r="10"/>
        <line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/>
      </svg>
      ${label}
    </div>
  `;
}

function makeNaturaLayer() {
  return L.geoJSON(LAYER_NATURA, {
    renderer: canvasRenderer,
    style: { color: "#4ade80", weight: 0.8, fillColor: "#86efac", fillOpacity: 0.40 },
    onEachFeature: (f, lyr) => {
      const p = f.properties || {};
      lyr.bindPopup(
        `<b>${p.site_name || "Natura 2000"}</b>` +
        exclusionBadge("Protected site — exclusion zone") +
        `<table>` +
        row("Code", p.site_code || "—") +
        row("Type", p.site_type || "—") +
        `</table>`
      );
    },
  });
}

function makeFloodLayer() {
  return L.geoJSON(LAYER_FLOOD, {
    renderer: canvasRenderer,
    style: { color: "#2563eb", weight: 0.6, fillColor: "#2563eb", fillOpacity: 0.25 },
    onEachFeature: (f, lyr) => {
      const p = f.properties || {};
      lyr.bindPopup(
        `<b>${p.name || "Flood zone"}</b>` +
        exclusionBadge("Flood-risk area — exclusion zone") +
        `<table>` +
        row("Return period", p.return_period || "—") +
        row("Depth class",   p.depth_zone_class || "—") +
        `</table>`
      );
    },
  });
}

/* -----------------------------------------------------------------------
 * Wire layers to sidebar checkboxes
 * --------------------------------------------------------------------- */
const layerFactories = {
  aoi:           makeAoiLayer,
  parcels:       makeParcelsLayer,
  rejected:      makeRejectedLayer,
  top_sites:     makeTopSitesLayer,
  fingrid:       makeFingridLayer,
  substations:   makeSubstationsLayer,
  hv_lines:      makeHvLinesLayer,
  power_plants:  makePowerPlantsLayer,
  urban:         makeUrbanLayer,
  natura:        makeNaturaLayer,
  flood:         makeFloodLayer,
};

const layers = {};
for (const [name, factory] of Object.entries(layerFactories)) {
  try {
    layers[name] = factory();
  } catch (err) {
    console.error("Failed to build layer", name, err);
  }
}

// Layers that should auto-zoom-to-fit when the user enables them via the
// sidebar (typically small/curated datasets that are hard to find at AOI scale).
const ZOOM_TO_LAYER_ON_ENABLE = new Set(["top_sites"]);

// Set true while the code itself toggles checkboxes (init / show-all / reset)
// so we don't auto-zoom on those programmatic changes.
let programmaticToggle = false;

function zoomToLayer(layer) {
  if (!layer || !layer.getBounds) return;
  try {
    const b = layer.getBounds();
    if (b && b.isValid && b.isValid()) {
      map.fitBounds(b, { padding: [40, 40], maxZoom: 13 });
    }
  } catch (e) { /* noop */ }
}

// Initial layer state mirrors the `checked` attributes on each input
const INITIAL_LAYER_STATE = {};
document.querySelectorAll("input[data-layer]").forEach(cb => {
  INITIAL_LAYER_STATE[cb.dataset.layer] = cb.checked;
  const layer = layers[cb.dataset.layer];
  if (!layer) return;
  if (cb.checked) layer.addTo(map);
  cb.addEventListener("change", () => {
    if (cb.checked) {
      layer.addTo(map);
      if (!programmaticToggle && ZOOM_TO_LAYER_ON_ENABLE.has(cb.dataset.layer)) {
        zoomToLayer(layer);
      }
    } else {
      map.removeLayer(layer);
    }
  });
});

// Layer paint order: heavy fills below, points above
const PAINT_ORDER = [
  "natura", "flood", "rejected", "parcels", "top_sites",
  "hv_lines", "substations", "fingrid", "power_plants", "urban", "aoi",
];
PAINT_ORDER.forEach(n => {
  if (layers[n] && map.hasLayer(layers[n])) layers[n].bringToFront();
});

// Fit to AOI on load and constrain panning/zoom to a generous region around it
// so users can't accidentally fly off to Russia / America while exploring.
if (LAYER_AOI && layers.aoi) {
  try {
    const aoiBounds = layers.aoi.getBounds();
    map.fitBounds(aoiBounds, { padding: [20, 20] });
    // Nudge the initial view one step closer - the bare fitBounds leaves a
    // lot of empty padding around the AOI on wide screens.
    map.setZoom(map.getZoom() + 1, { animate: false });
    map.setMaxBounds(aoiBounds.pad(1.0));     // ~3x AOI extent of pannable area
    map.setMinZoom(Math.max(5, map.getZoom() - 2));
    map.setMaxZoom(18);
  } catch (e) { console.warn("Could not fit AOI bounds", e); }
}

/* -----------------------------------------------------------------------
 * Hide-all / show-all layer toggle  +  Reset button
 * --------------------------------------------------------------------- */
const layerCheckboxes = Array.from(document.querySelectorAll("input[data-layer]"));
const homeBtn   = document.getElementById("layers-home");
const allOffBtn = document.getElementById("layers-toggle-all");
const resetBtn  = document.getElementById("layers-reset");

function setToggleAllLabel() {
  const anyOn = layerCheckboxes.some(cb => cb.checked);
  allOffBtn.textContent = anyOn ? "Hide all" : "Show all";
  allOffBtn.title       = anyOn ? "Hide all layers" : "Show all layers";
}

// Home: snap the view back to the AOI without touching layer visibility,
// selection or measurements - matches the initial-load framing.
function snapHome() {
  if (!LAYER_AOI || !layers.aoi) return;
  try {
    map.fitBounds(layers.aoi.getBounds(), { padding: [20, 20] });
    map.setZoom(map.getZoom() + 1, { animate: false });
  } catch (e) { /* noop */ }
}
if (homeBtn) homeBtn.addEventListener("click", snapHome);

allOffBtn.addEventListener("click", () => {
  const anyOn = layerCheckboxes.some(cb => cb.checked);
  const target = !anyOn;
  programmaticToggle = true;
  try {
    layerCheckboxes.forEach(cb => {
      if (cb.checked !== target) {
        cb.checked = target;
        cb.dispatchEvent(new Event("change"));
      }
    });
  } finally {
    programmaticToggle = false;
  }
  setToggleAllLabel();
  if (!target) setActiveRankBtn(null);
});

resetBtn.addEventListener("click", () => {
  // Restore default layer visibility
  programmaticToggle = true;
  try {
    layerCheckboxes.forEach(cb => {
      const want = !!INITIAL_LAYER_STATE[cb.dataset.layer];
      if (cb.checked !== want) {
        cb.checked = want;
        cb.dispatchEvent(new Event("change"));
      }
    });
  } finally {
    programmaticToggle = false;
  }
  // Clear any active measurement and remove finished measures
  if (typeof clearMeasures === "function") clearMeasures();
  // Clear parcel selection highlight
  if (typeof clearParcelSelection === "function") clearParcelSelection();
  // Snap view back to the AOI (matches the initial-load framing)
  snapHome();
  setToggleAllLabel();
  setActiveRankBtn(null);
});

/* -----------------------------------------------------------------------
 * Top-ranked-parcels quick-jump buttons
 *  - one button per rank found in the top_sites layer
 *  - click  → switch basemap to satellite, enable the layer if needed,
 *             fit bounds to that parcel and open its popup
 * --------------------------------------------------------------------- */
const topRankList = document.getElementById("top-rank-list");
const topSitesCb  = document.querySelector('input[data-layer="top_sites"]');

function setActiveRankBtn(rank) {
  if (!topRankList) return;
  topRankList.querySelectorAll(".rank-btn").forEach(b => {
    b.classList.toggle("is-active", Number(b.dataset.rank) === Number(rank));
  });
}

// Stage 1 / Stage 2 cover the whole AOI and would just clutter a focused
// satellite view of a single top site - hide them when zooming to a rank.
const HIDE_ON_RANK_FOCUS = ["parcels", "rejected"];

function focusTopSite(rank) {
  const lyr = topSitesByRank.get(Number(rank));
  if (!lyr) return;
  if (activeBasemapKey !== "satellite") setBasemap("satellite");

  programmaticToggle = true;
  try {
    if (topSitesCb && !topSitesCb.checked) {
      topSitesCb.checked = true;
      topSitesCb.dispatchEvent(new Event("change"));
    }
    HIDE_ON_RANK_FOCUS.forEach(name => {
      const cb = document.querySelector(`input[data-layer="${name}"]`);
      if (cb && cb.checked) {
        cb.checked = false;
        cb.dispatchEvent(new Event("change"));
      }
    });
  } finally {
    programmaticToggle = false;
  }
  if (typeof setToggleAllLabel === "function") setToggleAllLabel();

  try {
    const b = lyr.getBounds();
    if (b && b.isValid && b.isValid()) {
      map.fitBounds(b, { padding: [60, 60], maxZoom: 16 });
    }
  } catch (e) { /* noop */ }
  selectFeature(lyr, l => l.setStyle(topSiteDefaultStyle(l.feature)));
  if (lyr.openPopup) lyr.openPopup();
  setActiveRankBtn(rank);
}

// Cap the rank shortcut row to keep it compact (two rows of 8 in the sidebar).
// Sites beyond this rank are still on the map and clickable, just not promoted
// as a quick-jump button.
const RANK_BUTTON_LIMIT = 16;

if (topRankList && topSitesByRank.size) {
  const ranks = Array.from(topSitesByRank.keys())
    .sort((a, b) => a - b)
    .slice(0, RANK_BUTTON_LIMIT);
  ranks.forEach(r => {
    const btn = document.createElement("button");
    btn.type            = "button";
    btn.className       = "rank-btn";
    btn.dataset.rank    = String(r);
    btn.textContent     = String(r);
    btn.title           = `Zoom to rank #${r} (switches to satellite)`;
    btn.addEventListener("click", () => focusTopSite(r));
    topRankList.appendChild(btn);
  });
}

/* -----------------------------------------------------------------------
 * Sidebar toggle (mobile)
 * --------------------------------------------------------------------- */
const toggleBtn = document.getElementById("sidebar-toggle");
toggleBtn.addEventListener("click", () => {
  document.body.classList.toggle("sidebar-open");
  setTimeout(() => map.invalidateSize(), 200);
});
window.addEventListener("resize", () => map.invalidateSize());
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
