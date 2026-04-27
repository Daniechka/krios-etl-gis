"""
Generate a single self-contained Leaflet web map at webmap/index.html.

All GeoJSON layers are inlined as JS variables, so the resulting HTML can be:
  - Opened by double-clicking (no server needed)
  - Served from GitHub Pages (or any static host)
  - Sent as an email attachment

Layers come from the friend's processed dataset (path configurable via
PROCESSED_DATA_DIR env var, see src/config.py and .env). No scoring is
performed here - a placeholder slot for `top_sites` is included so a future
scoring stage can drop scored parcels in without changing this module.

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
    PROCESSED_DATA_DIR,
    PROJECT_ROOT,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

WEBMAP_DIR = PROJECT_ROOT / "webmap"
OUTPUT_HTML = WEBMAP_DIR / "index.html"

OSM_GPKG = "osm_infrastructure.gpkg"

# Simplification tolerances in metres (applied in EPSG:3067 before reprojecting
# to WGS84). Picked to keep visual fidelity at zoom levels 8-13 while shrinking
# the inlined GeoJSON payload.
SIMPLIFY_M = {
    "parcels": 30,
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

    logger.info("Parcels (>= %d ha)…", MIN_PARCEL_AREA_HA)
    parcels = gpd.read_file(pp / "parcels.gpkg")
    parcels = parcels[parcels["area_ha"] >= MIN_PARCEL_AREA_HA]
    logger.info("  %d parcels after area filter", len(parcels))
    layers["parcels"] = _to_geojson(
        parcels,
        simplify_m=SIMPLIFY_M["parcels"],
        columns=["property_id", "area_ha"],
    )

    # Empty placeholder so the JS template always has a valid array to render.
    # When a scoring branch produces scored parcels, swap in a real GeoJSON here.
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
<link rel="stylesheet" href="https://unpkg.com/leaflet-measure@3.1.0/dist/leaflet-measure.css"/>
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
    <div class="section-head">
      <h2>Layers</h2>
      <button id="layers-toggle-all" class="btn-link" type="button" title="Hide all layers">Hide all</button>
    </div>
    <label class="layer"><input type="checkbox" data-layer="aoi" checked>
      <span class="sw line dashed" style="border-top-color:#000"></span> AOI boundary</label>
    <label class="layer"><input type="checkbox" data-layer="parcels" checked>
      <span class="sw" style="background:rgba(71,85,105,0.25); border-color:#475569"></span> Parcels (≥10 ha)</label>
    <label class="layer"><input type="checkbox" data-layer="top_sites" checked>
      <span class="sw" style="background:linear-gradient(90deg,#fee08b,#fdae61,#1a9850)"></span> Top-ranked sites</label>
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

  <section class="caveats">
    <h2>Caveats</h2>
    <p>Data sources: <b>MML</b> (parcels), <b>OSM</b> (grid + plants + urban),
      <b>Fingrid</b> (substation capacity), <b>SYKE</b> (flood zones), <b>EEA</b> (Natura 2000).</p>
    <p>Parcels filtered to ≥ 10 ha (assignment threshold). Slope, exclusions and
      composite ranking are <i>not</i> applied in this map yet — top-ranked layer
      is a placeholder, populated by a separate scoring pipeline.</p>
    <p>All distances are Euclidean (no road routing). CRS: EPSG:4326 in browser,
      analysis done in EPSG:3067.</p>
  </section>
</aside>

<div id="map"></div>

<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
        integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin=""></script>
<script src="https://unpkg.com/leaflet-measure@3.1.0/dist/leaflet-measure.js"></script>
<script>
/* -----------------------------------------------------------------------
 * Inlined data (replaced at build time by src/visualization/generate_map.py)
 * --------------------------------------------------------------------- */
const LAYER_AOI          = /*__DATA_AOI__*/null;
const LAYER_PARCELS      = /*__DATA_PARCELS__*/null;
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

const map = L.map("map", { zoomControl: true, renderer: svgRenderer })
  .setView([65.0, 26.0], 8);
map.attributionControl.setPrefix("Created by <strong>Daria Snytkina</strong>");

const basemaps = {
  "Carto Light":  L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
    attribution: '&copy; OpenStreetMap &copy; CARTO', subdomains: 'abcd', maxZoom: 19,
  }),
  "Carto Voyager": L.tileLayer("https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png", {
    attribution: '&copy; OpenStreetMap &copy; CARTO', subdomains: 'abcd', maxZoom: 19,
  }),
  "OpenStreetMap": L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: '&copy; OpenStreetMap', maxZoom: 19,
  }),
  "Google Satellite": L.tileLayer("https://mt{s}.google.com/vt/lyrs=s&x={x}&y={y}&z={z}", {
    attribution: 'Imagery &copy; Google', subdomains: ['0','1','2','3'], maxZoom: 20,
  }),
  "Google Hybrid": L.tileLayer("https://mt{s}.google.com/vt/lyrs=y&x={x}&y={y}&z={z}", {
    attribution: 'Imagery &copy; Google', subdomains: ['0','1','2','3'], maxZoom: 20,
  }),
};
basemaps["Carto Light"].addTo(map);
L.control.layers(basemaps, null, { collapsed: true, position: "topright" }).addTo(map);
L.control.scale({ imperial: false }).addTo(map);

L.control.measure({
  position: "topleft",
  primaryLengthUnit: "kilometers",
  secondaryLengthUnit: "meters",
  primaryAreaUnit: "hectares",
  secondaryAreaUnit: "sqmeters",
  activeColor: "#0891b2",
  completedColor: "#0e7490",
  captureZIndex: 10000,
}).addTo(map);

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

function makeParcelsLayer() {
  return L.geoJSON(LAYER_PARCELS, {
    renderer: canvasRenderer,
    style: { color: "#475569", weight: 0.4, fillColor: "#475569", fillOpacity: 0.18 },
    onEachFeature: (f, lyr) => {
      const p = f.properties || {};
      lyr.bindPopup(
        `<b>Parcel ${p.property_id ?? "?"}</b><br>` +
        `<table>${row("Area", fmt(p.area_ha) + " ha")}</table>`
      );
    },
  });
}

function makeTopSitesLayer() {
  // Empty placeholder - populated later when scoring stage produces ranked output.
  return L.geoJSON(LAYER_TOP_SITES, {
    style: f => ({
      color: "#1a9850", weight: 1.5,
      fillColor: f.properties?.composite_score != null
        ? scoreColor(f.properties.composite_score) : "#1a9850",
      fillOpacity: 0.7,
    }),
    onEachFeature: (f, lyr) => {
      const p = f.properties || {};
      let html = `<b>Rank ${p.rank ?? "?"}</b> · score ${fmt(p.composite_score, 3)}<br>`;
      html += `<table>` +
              row("Area", fmt(p.area_ha) + " ha") +
              row("Grid capacity",  fmt(p.score_grid_capacity, 2)) +
              row("Grid distance",  fmt(p.score_grid_distance, 2)) +
              row("Urban distance", fmt(p.score_urban_distance, 2)) +
              row("Parcel size",    fmt(p.score_parcel_size, 2)) +
              `</table>`;
      lyr.bindPopup(html);
    },
  });
}
function scoreColor(s) {
  if (s >= 0.66) return "#1a9850";
  if (s >= 0.33) return "#fdae61";
  return "#fee08b";
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

function makeNaturaLayer() {
  return L.geoJSON(LAYER_NATURA, {
    renderer: canvasRenderer,
    style: { color: "#4ade80", weight: 0.8, fillColor: "#86efac", fillOpacity: 0.40 },
    onEachFeature: (f, lyr) => {
      const p = f.properties || {};
      lyr.bindPopup(
        `<b>${p.site_name || "Natura 2000"}</b><br>` +
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
        `<b>${p.name || "Flood zone"}</b><br>` +
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

// Initial layer state mirrors the `checked` attributes on each input
document.querySelectorAll("input[data-layer]").forEach(cb => {
  const layer = layers[cb.dataset.layer];
  if (!layer) return;
  if (cb.checked) layer.addTo(map);
  cb.addEventListener("change", () => {
    if (cb.checked) layer.addTo(map);
    else map.removeLayer(layer);
  });
});

// Layer paint order: heavy fills below, points above
const PAINT_ORDER = [
  "natura", "flood", "parcels", "top_sites",
  "hv_lines", "substations", "fingrid", "power_plants", "urban", "aoi",
];
PAINT_ORDER.forEach(n => {
  if (layers[n] && map.hasLayer(layers[n])) layers[n].bringToFront();
});

// Fit to AOI on load
if (LAYER_AOI && layers.aoi) {
  try { map.fitBounds(layers.aoi.getBounds(), { padding: [20, 20] }); }
  catch (e) { console.warn("Could not fit AOI bounds", e); }
}

/* -----------------------------------------------------------------------
 * Hide-all / show-all layer toggle
 * --------------------------------------------------------------------- */
const layerCheckboxes = Array.from(document.querySelectorAll("input[data-layer]"));
const allOffBtn = document.getElementById("layers-toggle-all");
allOffBtn.addEventListener("click", () => {
  const anyOn = layerCheckboxes.some(cb => cb.checked);
  const target = !anyOn; // if any are on -> turn all off; else turn all on
  layerCheckboxes.forEach(cb => {
    if (cb.checked !== target) {
      cb.checked = target;
      cb.dispatchEvent(new Event("change"));
    }
  });
  allOffBtn.textContent = target ? "Hide all" : "Show all";
  allOffBtn.title = target ? "Hide all layers" : "Show all layers";
});

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
