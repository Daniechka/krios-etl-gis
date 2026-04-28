"""Natura 2000 protected areas collector."""

from typing import Optional
import geopandas as gpd
import requests
from urllib.parse import urlencode

from .base import BaseCollector
from ..config import (
    RAW_DATA_DIR,
    AOI_CENTER_LAT,
    AOI_CENTER_LON,
    AOI_RADIUS_KM,
    CRS_WGS84,
    CRS_FINLAND
)


class Natura2000Collector(BaseCollector):
    """Collector for Natura 2000 protected areas from EEA ArcGIS REST API."""
    
    # European Environment Agency ArcGIS REST endpoint
    BASE_URL = "https://bio.discomap.eea.europa.eu/arcgis/rest/services/ProtectedSites/Natura2000Sites/MapServer"
    
    # Layer IDs:
    # 0 = Habitats Directive Sites (pSCI, SCI or SAC)
    # 1 = Birds Directive Sites (SPA)
    # 2 = Habitats and Birds Directive Sites (combined)
    LAYER_ID = 2  # Use combined layer
    
    def __init__(self):
        super().__init__(RAW_DATA_DIR)
    
    def collect(self, buffer_percent: float = 15.0) -> Optional[gpd.GeoDataFrame]:
        """
        Collect Natura 2000 sites from EEA ArcGIS REST API.
        
        This method queries Finnish Natura 2000 sites (MS='FI') from the EEA's
        ArcGIS REST service and filters them to the AOI.
        
        Args:
            buffer_percent: Percentage to buffer AOI for data collection (default: 15%)
        
        Returns:
            GeoDataFrame with Natura 2000 boundaries, or None if collection fails
        """
        try:
            # Read AOI from file instead of creating circle
            from pathlib import Path
            aoi_file = Path(__file__).parent.parent.parent / "data" / "aoi_test.geojson"
            
            if not aoi_file.exists():
                self.logger.warning(f"AOI file not found: {aoi_file}, falling back to circle AOI")
                aoi_wgs84 = self.create_aoi_circle(AOI_CENTER_LAT, AOI_CENTER_LON, AOI_RADIUS_KM, crs=CRS_WGS84)
            else:
                self.logger.info(f"Using AOI from: {aoi_file}")
                aoi_gdf = gpd.read_file(aoi_file)
                
                # Ensure WGS84
                if aoi_gdf.crs != CRS_WGS84:
                    aoi_gdf = aoi_gdf.to_crs(CRS_WGS84)
                
                # Buffer the AOI by buffer_percent
                # First convert to projected CRS for accurate buffering in meters
                aoi_projected = aoi_gdf.to_crs(CRS_FINLAND)
                
                # Calculate buffer distance: diagonal of bbox * buffer_percent
                bounds = aoi_projected.total_bounds
                bbox_width = bounds[2] - bounds[0]
                bbox_height = bounds[3] - bounds[1]
                diagonal = (bbox_width**2 + bbox_height**2)**0.5
                buffer_distance = diagonal * (buffer_percent / 100.0)
                
                self.logger.info(f"Buffering AOI by {buffer_percent}% (~{buffer_distance/1000:.1f} km)")
                
                # Apply buffer
                aoi_buffered = aoi_projected.copy()
                aoi_buffered.geometry = aoi_buffered.geometry.buffer(buffer_distance)
                
                # Convert back to WGS84 for clipping
                aoi_wgs84 = aoi_buffered.to_crs(CRS_WGS84)
            
            # Use buffered AOI in EPSG:3067 for query
            aoi_finland = aoi_wgs84.to_crs(CRS_FINLAND)
            bbox = aoi_finland.total_bounds
            
            self.logger.info(f"Fetching Natura 2000 data for Finnish sites in AOI")
            self.logger.info(f"AOI bbox (EPSG:3067): {bbox}")
            
            try:
                # Query Finnish Natura 2000 sites via ArcGIS REST API
                query_url = f"{self.BASE_URL}/{self.LAYER_ID}/query"
                
                # Convert bbox to EPSG:3067 envelope string
                bbox_str = f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}"
                
                params = {
                    'where': "MS='FI'",  # Filter to Finland
                    'geometry': bbox_str,
                    'geometryType': 'esriGeometryEnvelope',
                    'inSR': '3067',  # Input spatial reference (Finland TM35FIN)
                    'spatialRel': 'esriSpatialRelIntersects',
                    'outFields': '*',  # Get all attributes
                    'returnGeometry': 'true',
                    'outSR': '4326',  # Output as WGS84
                    'f': 'geojson'
                }
                
                self.logger.info(f"Querying: {query_url}")
                self.logger.info(f"Parameters: {params}")
                
                response = requests.get(query_url, params=params, timeout=60)
                response.raise_for_status()
                
                # Parse GeoJSON response directly into GeoDataFrame
                gdf = gpd.read_file(response.text, driver='GeoJSON')
                
                if gdf.empty:
                    self.logger.warning("No Natura 2000 sites found in AOI")
                    self.logger.info("This may be normal if there are no protected areas in the selected region")
                    return gpd.GeoDataFrame()
                
                # Ensure CRS is set
                if gdf.crs is None:
                    gdf = gdf.set_crs(CRS_WGS84)
                
                # Clip to actual AOI (unbuffered original AOI for final output)
                # Read original AOI without buffer for clipping
                aoi_original = gpd.read_file(Path(__file__).parent.parent.parent / "data" / "aoi_test.geojson")
                if aoi_original.crs != CRS_WGS84:
                    aoi_original = aoi_original.to_crs(CRS_WGS84)
                
                gdf = gpd.clip(gdf, aoi_original)
                
                if gdf.empty:
                    self.logger.warning("No Natura 2000 sites remain after clipping to AOI")
                    return gpd.GeoDataFrame()
                
                self.logger.info(f"Successfully collected {len(gdf)} Natura 2000 sites")
                self.logger.info(f"Fields: {list(gdf.columns)}")
                
                self.save_geodataframe(gdf, "natura2000_sites")
                return gdf
                    
            except requests.RequestException as e:
                self.logger.error(f"FAILED to access Natura 2000 ArcGIS REST API: {e}")
                self.logger.error("NO FALLBACK DATA AVAILABLE - Natura 2000 data collection failed")
                return None
                
        except Exception as e:
            self.logger.error(f"FAILED to collect Natura 2000 data - Unexpected error: {e}")
            self.logger.error("NO FALLBACK DATA AVAILABLE - Natura 2000 data collection failed")
            return None


if __name__ == "__main__":
    """Run Natura 2000 collector when executed as module."""
    import sys
    
    collector = Natura2000Collector()
    
    print("Collecting Natura 2000 protected areas from EEA...")
    print(f"AOI: {AOI_CENTER_LAT}, {AOI_CENTER_LON} (radius: {AOI_RADIUS_KM} km)")
    
    gdf = collector.collect()
    
    if gdf is not None and not gdf.empty:
        print(f"\n Successfully collected {len(gdf)} Natura 2000 sites")
        print(f"   Saved to: {RAW_DATA_DIR / 'natura2000_sites.geojson'}")
        print(f"   Fields: {list(gdf.columns)}")
    elif gdf is not None and gdf.empty:
        print("\n No Natura 2000 sites found in AOI (this may be normal)")
        print("   Note: This is a valid result if there are no protected areas in the region")
    else:
        print("\n Failed to collect Natura 2000 data")
        sys.exit(1)
    
    print("\nNote: Natura 2000 is a static dataset. In production:")
    print("  - Download full dataset once and store in cloud storage (S3/GCS)")
    print("  - Index in PostGIS database for fast spatial queries")
    print("  - Processors would query PostGIS instead of API")
    print("  - Update annually when new data is released")
