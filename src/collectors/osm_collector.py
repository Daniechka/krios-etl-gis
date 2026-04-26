"""OpenStreetMap data collector using Overpass API."""

import time
from typing import Optional, List, Dict, Tuple
from pathlib import Path
import requests
import geopandas as gpd
from shapely.geometry import Point, LineString, Polygon, box
import pandas as pd

from .base import BaseCollector
from ..config import (
    RAW_DATA_DIR, 
    DATA_DIR,
    CRS_WGS84
)


class OSMCollector(BaseCollector):
    """Collector for OpenStreetMap data via Overpass API."""
    
    OVERPASS_URL = "https://overpass-api.de/api/interpreter"
    
    def __init__(self, aoi_path: Optional[Path] = None, buffer_percent: float = 15.0):
        """
        Initialize OSM collector.
        
        Args:
            aoi_path: Path to AOI GeoJSON file. If None, uses data/aoi_test.geojson
            buffer_percent: Percentage buffer to add to AOI (default 15%)
        """
        super().__init__(RAW_DATA_DIR)
        self.timeout = 60
        self.aoi_path = aoi_path or DATA_DIR / "aoi_test.geojson"
        self.buffer_percent = buffer_percent
        self._aoi_bbox = None
    
    def _load_aoi_with_buffer(self) -> Tuple[float, float, float, float]:
        """
        Load AOI from file and return bbox with buffer.
        
        Returns:
            Tuple of (min_lat, min_lon, max_lat, max_lon) for Overpass API
        """
        if self._aoi_bbox is not None:
            return self._aoi_bbox
        
        # Load AOI
        aoi = gpd.read_file(self.aoi_path)
        
        # Get bounds
        bounds = aoi.total_bounds  # (minx, miny, maxx, maxy)
        
        # Calculate buffer size as percentage of dimensions
        width = bounds[2] - bounds[0]
        height = bounds[3] - bounds[1]
        buffer_x = width * (self.buffer_percent / 100)
        buffer_y = height * (self.buffer_percent / 100)
        
        # Apply buffer and convert to Python float (not numpy.float64)
        min_lon = float(bounds[0] - buffer_x)
        min_lat = float(bounds[1] - buffer_y)
        max_lon = float(bounds[2] + buffer_x)
        max_lat = float(bounds[3] + buffer_y)
        
        self._aoi_bbox = (min_lat, min_lon, max_lat, max_lon)
        self.logger.info(f"AOI bbox with {self.buffer_percent}% buffer: {self._aoi_bbox}")
        
        return self._aoi_bbox
        
    def _query_overpass(self, query: str, retry: int = 3) -> Optional[Dict]:
        """
        Execute Overpass API query with retry logic.
        
        Args:
            query: Overpass QL query string
            retry: Number of retry attempts
            
        Returns:
            JSON response or None on failure
        """
        for attempt in range(retry):
            try:
                self.logger.info(f"Querying Overpass API (attempt {attempt + 1}/{retry})...")
                # Overpass API requires User-Agent header and expects form data with 'data' parameter
                response = requests.post(
                    self.OVERPASS_URL,
                    data={'data': query},
                    headers={'User-Agent': 'krios-gis-site-selection/0.1.0'},
                    timeout=self.timeout
                )
                response.raise_for_status()
                return response.json()
            except requests.exceptions.RequestException as e:
                self.logger.warning(f"Attempt {attempt + 1} failed: {e}")
                if attempt < retry - 1:
                    time.sleep(5)  # Wait before retry
                else:
                    self.logger.error(f"FAILED - All {retry} attempts to query Overpass API failed")
                    self.logger.error("NO FALLBACK DATA AVAILABLE - OSM data collection failed")
                    return None
    
    def collect_power_lines(self) -> Optional[gpd.GeoDataFrame]:
        """
        Collect high-voltage power lines.
        Includes lines tagged as power=line or power=cable.
        
        Returns:
            GeoDataFrame with power lines
        """
        bbox = self._load_aoi_with_buffer()
        
        # Overpass query for power lines and cables
        # Query format: (south, west, north, east)
        query = f"""[out:json][timeout:{self.timeout}];
(
  way["power"="line"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
  way["power"="cable"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
);
out geom;"""
        
        data = self._query_overpass(query)
        if not data or 'elements' not in data:
            self.logger.error("FAILED - No power lines data returned from Overpass API")
            return None
        
        features = []
        for element in data['elements']:
            if element['type'] == 'way' and 'geometry' in element:
                coords = [(node['lon'], node['lat']) for node in element['geometry']]
                if len(coords) >= 2:
                    tags = element.get('tags', {})
                    features.append({
                        'geometry': LineString(coords),
                        'osm_id': element['id'],
                        'voltage': tags.get('voltage', 'unknown'),
                        'power': tags.get('power', 'line'),
                        'name': tags.get('name', None)
                    })
        
        if not features:
            self.logger.error("FAILED - No valid power line geometries extracted from OSM data")
            return None
            
        gdf = gpd.GeoDataFrame(features, crs=CRS_WGS84)
        self.logger.info(f"Collected {len(gdf)} power lines")
        self.save_geodataframe(gdf, "osm_power_lines")
        return gdf
    
    def collect_substations(self) -> Optional[gpd.GeoDataFrame]:
        """
        Collect electricity substations.
        
        Returns:
            GeoDataFrame with substations
        """
        bbox = self._load_aoi_with_buffer()
        
        query = f"""[out:json][timeout:{self.timeout}];
(
  node["power"="substation"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
  way["power"="substation"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
  relation["power"="substation"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
);
out center;"""
        
        data = self._query_overpass(query)
        if not data or 'elements' not in data:
            self.logger.error("FAILED - No substations data returned from Overpass API")
            return None
        
        features = []
        for element in data['elements']:
            tags = element.get('tags', {})
            
            # Get coordinates
            if element['type'] == 'node':
                coords = (element['lon'], element['lat'])
            elif 'center' in element:
                coords = (element['center']['lon'], element['center']['lat'])
            else:
                continue
            
            features.append({
                'geometry': Point(coords),
                'osm_id': element['id'],
                'voltage': tags.get('voltage', None),
                'name': tags.get('name', None),
                'operator': tags.get('operator', None)
            })
        
        if not features:
            self.logger.error("FAILED - No valid substation geometries extracted from OSM data")
            return None
            
        gdf = gpd.GeoDataFrame(features, crs=CRS_WGS84)
        self.logger.info(f"Collected {len(gdf)} substations")
        self.save_geodataframe(gdf, "osm_substations")
        return gdf
    
    def collect_data_centers(self) -> Optional[gpd.GeoDataFrame]:
        """
        Collect existing data centers using telecom=data_center tag.
        
        Returns:
            GeoDataFrame with data centers
        """
        bbox = self._load_aoi_with_buffer()
        
        # Use the correct tag: telecom=data_center (as shown in working example)
        query = f"""[out:json][timeout:{self.timeout}];
(
  node["telecom"="data_center"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
  way["telecom"="data_center"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
  relation["telecom"="data_center"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
);
out geom;"""
        
        data = self._query_overpass(query)
        if not data or 'elements' not in data:
            self.logger.error("FAILED - No data centers data returned from Overpass API")
            return None
        
        features = []
        for element in data['elements']:
            tags = element.get('tags', {})
            
            # Get coordinates - handle nodes, ways, and relations
            if element['type'] == 'node':
                coords = (element['lon'], element['lat'])
                geom = Point(coords)
            elif element['type'] == 'way' and 'geometry' in element:
                # For ways, create a point from the first coordinate or centroid
                way_coords = [(node['lon'], node['lat']) for node in element['geometry']]
                if way_coords:
                    # Use centroid of the way
                    geom = Point(sum(c[0] for c in way_coords) / len(way_coords),
                               sum(c[1] for c in way_coords) / len(way_coords))
                else:
                    continue
            elif 'center' in element:
                coords = (element['center']['lon'], element['center']['lat'])
                geom = Point(coords)
            else:
                continue
            
            features.append({
                'geometry': geom,
                'osm_id': element['id'],
                'osm_type': element['type'],
                'name': tags.get('name', None),
                'name_fi': tags.get('name:fi', None),
                'operator': tags.get('operator', None),
                'telecom': tags.get('telecom', None),
                'description': tags.get('description', None)
            })
        
        if not features:
            self.logger.warning("No data centers found in AOI")
            return None
            
        gdf = gpd.GeoDataFrame(features, crs=CRS_WGS84)
        self.logger.info(f"Collected {len(gdf)} data centers")
        self.save_geodataframe(gdf, "osm_data_centers")
        return gdf
    
    def collect_power_plants(self) -> Optional[gpd.GeoDataFrame]:
        """
        Collect power plant locations and types.
        
        Returns:
            GeoDataFrame with power plants
        """
        bbox = self._load_aoi_with_buffer()
        
        query = f"""[out:json][timeout:{self.timeout}];
(
  node["power"="plant"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
  way["power"="plant"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
  relation["power"="plant"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
  node["power"="generator"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
  way["power"="generator"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
  relation["power"="generator"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
);
out center;"""
        
        data = self._query_overpass(query)
        if not data or 'elements' not in data:
            self.logger.error("FAILED - No power plants data returned from Overpass API")
            return None
        
        features = []
        for element in data['elements']:
            tags = element.get('tags', {})
            
            # Get coordinates
            if element['type'] == 'node':
                coords = (element['lon'], element['lat'])
            elif 'center' in element:
                coords = (element['center']['lon'], element['center']['lat'])
            else:
                continue
            
            features.append({
                'geometry': Point(coords),
                'osm_id': element['id'],
                'osm_type': element['type'],
                'power': tags.get('power', None),
                'name': tags.get('name', None),
                'operator': tags.get('operator', None),
                'plant_source': tags.get('plant:source', None),
                'plant_output': tags.get('plant:output:electricity', None),
                'generator_source': tags.get('generator:source', None),
                'generator_method': tags.get('generator:method', None),
                'generator_output': tags.get('generator:output:electricity', None)
            })
        
        if not features:
            self.logger.warning("No power plants found in AOI")
            return None
            
        gdf = gpd.GeoDataFrame(features, crs=CRS_WGS84)
        self.logger.info(f"Collected {len(gdf)} power plants/generators")
        self.save_geodataframe(gdf, "osm_power_plants")
        return gdf
    
    def collect_urban_centers(self) -> Optional[gpd.GeoDataFrame]:
        """
        Collect major urban centers (cities with 100k+ population).
        
        Returns:
            GeoDataFrame with urban centers
        """
        bbox = self._load_aoi_with_buffer()
        
        query = f"""[out:json][timeout:{self.timeout}];
(
  node["place"="city"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
  node["place"="town"]["population"~"^[1-9][0-9]{{5,}}"]({bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]});
);
out;"""
        
        data = self._query_overpass(query)
        if not data or 'elements' not in data:
            self.logger.error("FAILED - No urban centers data returned from Overpass API")
            return None
        
        features = []
        for element in data['elements']:
            if element['type'] != 'node':
                continue
                
            tags = element.get('tags', {})
            pop_str = tags.get('population', '0')
            
            # Try to parse population
            try:
                population = int(pop_str.replace(',', '').replace(' ', ''))
            except (ValueError, AttributeError):
                population = 0
            
            features.append({
                'geometry': Point(element['lon'], element['lat']),
                'osm_id': element['id'],
                'name': tags.get('name', None),
                'population': population,
                'place': tags.get('place', None)
            })
        
        if not features:
            self.logger.error("FAILED - No valid urban center geometries extracted from OSM data")
            return None
            
        gdf = gpd.GeoDataFrame(features, crs=CRS_WGS84)
        
        # Filter by population if available
        if 'population' in gdf.columns:
            gdf = gdf[gdf['population'] >= 50000]  # Lower threshold to catch more cities
        
        self.logger.info(f"Collected {len(gdf)} urban centers")
        self.save_geodataframe(gdf, "osm_urban_centers")
        return gdf
    
    def collect(self) -> Dict[str, Optional[gpd.GeoDataFrame]]:
        """
        Collect all OSM datasets.
        
        Returns:
            Dictionary with all collected datasets
        """
        self.logger.info("Starting OSM data collection...")
        
        results = {
            'power_lines': self.collect_power_lines(),
            'substations': self.collect_substations(),
            'power_plants': self.collect_power_plants(),
            'data_centers': self.collect_data_centers(),
            'urban_centers': self.collect_urban_centers()
        }
        
        self.logger.info("OSM data collection complete")
        return results


if __name__ == "__main__":
    import sys
    
    print("="*80)
    print("OSM Infrastructure data collection")
    print("="*80)
    
    # Initialize collector
    collector = OSMCollector()
    
    # Run collection
    results = collector.collect()
    
    # Report results
    print("\n" + "="*80)
    print("Collection Results:")
    print("="*80)
    
    for dataset_name, gdf in results.items():
        if gdf is not None:
            print(f"[ok] {dataset_name:20s}: {len(gdf):4d} features -> data/raw/osm_{dataset_name}.geojson")
        else:
            print(f"[x] {dataset_name:20s}: No data found")
    
    print("\n" + "="*80)
    print("OSM data collection complete")
    print("="*80)
