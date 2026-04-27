# Design decisions and assumptions

This document captures key technical decisions, trade-offs, assumptions made during the GIS site selection project.


---

## Technical stack decisions

### UV instead of pip/poetry
- fast, no manual lock file management, growing adoption in Python geospatial community
- less mature tooling than pip/poetry, but acceptable for MVP

### why EPSG:3067 (TM35FIN)
- Official Finnish national projection - minimizes distortion in AOI
- Preferred by Finnish authorities (MML, SYKE, Fingrid)
- Meters-based
- **Alternative considered**: WGS84 (EPSG:4326) - poor area/distance accuracy at Finland's latitude

### visualization
- tba

---

## Data source decisions

### Priority 1: core lyers (MVP must-have)

#### 1. Land parcels (>=10 ha)
- **Source**: MML Kiinteistöjaotus WFS service
- Why: authoritative cadastral data
- **Limitation**: no ownership or zoning status or landuse included (would require manual procurement or 3rd party data layer)

#### 2. Natura 2000 protected areas
- **Source**: European Environment Agency (EEA) WFS
- **Why**: EU-wide consistency, well-maintained
- **Assumption**: any overlap -> automatic exclusion
- **Limitation**: doesn't include national/regional protected areas (ie Finnish nature reserves)

#### 3. Flood hazard zones
- **Source**: SYKE (Finnish Environment Institute) WFS
- **Why**: oOfficial Finnish authority for environmental data
- **Assumption**: 1/100-year flood zones -> automatic exclusion
- **Limitation**: tba

#### 4. Grid capacity
- **Source**: Fingrid open data portal (Sähkön kulutuskapasiteetti)
- **Why**: TSO operator's official capacity headroom data
- **Assumption**: published capacity -> available capacity (no allocation modeling)
- **Limitation**: no explicit API, only manual data extraction? 

### Priority 2: scoring layers

#### 5. Electricity network
- **Source**: OSM Overpass API (`power=line`)
- **Why**: best free global coverage
- **Assumption**: OSM tagging is sufficient in Finland
- **Limitation**: OSM quality varies; may miss private/unpublished lines

#### 6. Existing data centers
- **Source**: OSM (`building=data_center` or `telecom=data_center`) + manual DCD/BroadGroup fallback
- **Why**: quick MVP baseline
- **Assumption**: OSM has major facilities (not exhaustive)
- **Limitation**: small DCs likely missing
- others: https://www.datacentermap.com/datacenters/

#### 7. DEM - gradient
- **Source**: MML 10m DEM ( else EU-DEM 25m)
- **Why**: authoritative topographic data
- **Assumption**: >8% slope -> exclusion
- **Limitation**: 10m resolution might miss topographical features

---

## Suitability model decisions

### Fatal flaw criteria (hard filters)

| Criteria | Threshold | Rationale |
|-----------|-----------|-----------|
| Min size | 10 ha | Industry standard for hyperscale DC (allows 50MW+ facility) |
| Max size | 100 ha | Practical upper limit (pricey + may have ownership issues) |
| Natura 2000 overlap | Any intersection | Legal prohibition  |
| Flood hazard overlap | Any intersection | Risk mitigation  |
| Slope | >8% | Excavation costs  |

**Alternative**: we can exclude upper area threshold, as parcel can be used only partially. Conversely, being more defensive irt to slope 
and go for max 5% elevation.

**Trade-off**: Binary exclusion is conservative but avoids false positives. More sophisticated approach would use buffers or gradients. Ie if Natura2000 overlaps with 100-500m buffer - apply a scoring penalty (ie -30 points) instead of full exclusion.

### Opportunity scoring weights

| Factor | Weight | Justification |
|--------|--------|---------------|
| Grid capacity headroom | 30% | critical blocker: no capacity = no DC |
| Distance to 220/400kV | 25% | cost driver: connection costs scale ~500k Euro/km |
| Distance to urban center | 20% | operations: workforce availability |
| Parcel size | 15% | scalability: larger sites enable expansion |
| Distance to existing DCs | 10% | strategy: clustering by workforce vs. competition for power |

**Assumption**: weights reflect generic priorities. Actual clients would communicate these to the team.

**Limitation**: with single weights we don't capture multi-objective optimization. Pareto fronts: 

- Site A: [grid +++++, distance ++---, urban ++++-, size +++--]
- Site B: [grid +++--, distance +++++, urban ++---, size ++++-]

Both are "optimal" - need to choose based on strategic priorities.

### Scoring Functions

#### Grid capacity: linear scale
```
score = min(capacity_MW / 100, 1.0) * 100
```
- **Rationale**: 100MW+ = ideal (full score); 0MW = unusable
- **Assumption**: linear relationship (reality may have step functions based on transformer sizes)

#### Distance to grid: inverse decay
```
score = 100 * exp(-distance_km / 10)
```
- **Rationale**: exponential cost increase beyond 10km
- **Cutoff**: sites >50km score near zero


#### Distance to urban center: inverse decay
```
score = 100 * exp(-distance_km / 50)
```
- **Rationale**: 50km = acceptable commute; beyond 100km problematic
- **Assumption**: Road travel time ~ Euclidean distance * 1.3 (no routing analysis)

#### Parcel size: logarithmic scale
```
score = 100 * log10(size_ha / 10) / log10(10)
```
- **Rationale**: diminishing returns after 50ha (most DCs don't need 100ha immediately)
- **Assumption**: ignore parcel shape

---

## ETL pipeline decisions

### CRS transformation strategy
- Transform all inputs to EPSG:3067 immediately after ingestion
- Single CRS is must-have, no on-the-fly reprojections


### Geometry validation
- Mind and remove empty/null geometries
- Fix invalid geometries with `buffer(0)` trick
- Explode multi-parts to single-parts
- Topology cleaning?  good for production
- We assume data sources are reasonably clean (true for Finland and MML/EEA)


### Spatial indexing
- to be considered

---

## Data quality assumptions

### Coverage gaps
- Parcels: assume MML data is 100% complete
- OSM power lines: maybe miss some of rural network
- Data centers: only major facilities, very few points from OSM

### Temporal freshness
- Fingrid capacity: tba
- Natura 2000: updated annually
- OSM: continuous updates

