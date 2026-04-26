"""SYKE (Finnish Environment Institute) data collector."""

from typing import Optional
import geopandas as gpd
from owslib.wfs import WebFeatureService

from .base import BaseCollector
from ..config import (
    RAW_DATA_DIR,
    AOI_CENTER_LAT,
    AOI_CENTER_LON,
    AOI_RADIUS_KM,
    CRS_WGS84,
    CRS_FINLAND
)


class SYKECollector(BaseCollector):
    """Collector for SYKE environmental data (flood zones)."""
    
    # SYKE WFS endpoint for flood hazard zones (INSPIRE)
    WFS_URL = "https://paikkatiedot.ymparisto.fi/geoserver/inspire_nz/wfs"
    
    def __init__(self):
        super().__init__(RAW_DATA_DIR)
    
    def collect_flood_zones(self, return_period: str = "100a", buffer_percent: float = 15.0) -> Optional[gpd.GeoDataFrame]:
        """
        Collect flood hazard zones from SYKE WFS service.
        
        Args:
            return_period: Flood return period to query (default: "100a" = 1 in 100 year flood)
                          Options: "10a", "20a", "50a", "100a", "250a", "1000a"
            buffer_percent: Percentage to buffer AOI for data collection (default: 15%)
        
        Returns:
            GeoDataFrame with flood zones, or None if collection fails
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
                
                # Convert back to WGS84
                aoi_wgs84 = aoi_buffered.to_crs(CRS_WGS84)
            
            # Use buffered AOI in EPSG:3067 for query
            aoi_finland = aoi_wgs84.to_crs(CRS_FINLAND)
            bbox = aoi_finland.total_bounds
            
            self.logger.info(f"Fetching SYKE flood data (return period: 1:{return_period}) for bbox: {bbox}")
            
            try:
                wfs = WebFeatureService(url=self.WFS_URL, version='2.0.0')
                
                # Query riverine flood hazard zones (most relevant for inland Finland)
                layer_name = f"inspire_nz:NZ.Tulvavaaravyohykkeet_Vesistotulva_1_{return_period}"
                
                if layer_name not in wfs.contents:
                    self.logger.error(f"FAILED - Layer {layer_name} not found in WFS service")
                    self.logger.info(f"Available layers: {list(wfs.contents.keys())[:10]}")
                    return None
                
                self.logger.info(f"Querying flood layer: {layer_name}")
                
                response = wfs.getfeature(
                    typename=layer_name,
                    bbox=tuple(bbox),
                    srsname='EPSG:3067'
                )
                
                temp_file = self.output_dir / "temp_floods.gml"
                with open(temp_file, 'wb') as f:
                    f.write(response.read())
                
                gdf = gpd.read_file(temp_file)
                temp_file.unlink()
                
                if gdf.empty:
                    self.logger.warning(f"No flood hazard zones found in AOI for return period 1:{return_period}")
                    self.logger.info("This may be normal if the area is not at risk of flooding")
                    return gpd.GeoDataFrame()
                
                # Ensure CRS is set
                if gdf.crs is None:
                    gdf = gdf.set_crs(CRS_FINLAND)
                
                # Convert to WGS84 for consistency
                gdf = gdf.to_crs(CRS_WGS84)
                
                # Clip to actual AOI (unbuffered original AOI for final output)
                # Read original AOI without buffer for clipping
                from pathlib import Path
                aoi_original = gpd.read_file(Path(__file__).parent.parent.parent / "data" / "aoi_test.geojson")
                if aoi_original.crs != CRS_WGS84:
                    aoi_original = aoi_original.to_crs(CRS_WGS84)
                
                gdf = gpd.clip(gdf, aoi_original)
                
                if gdf.empty:
                    self.logger.warning("No flood zones remain after clipping to circular AOI")
                    return gpd.GeoDataFrame()
                
                self.logger.info(f"Successfully collected {len(gdf)} flood hazard zones")
                self.logger.info(f"Fields: {list(gdf.columns)}")
                
                self.save_geodataframe(gdf, f"syke_flood_zones_{return_period}")
                return gdf
                    
            except Exception as e:
                self.logger.error(f"FAILED to access SYKE WFS service: {e}")
                self.logger.error("NO FALLBACK DATA AVAILABLE - Flood zone data collection failed")
                return None
                
        except Exception as e:
            self.logger.error(f"FAILED to collect SYKE flood data - Unexpected error: {e}")
            self.logger.error("NO FALLBACK DATA AVAILABLE - Flood zone data collection failed")
            return None
    
    def collect(self) -> Optional[gpd.GeoDataFrame]:
        """
        Collect all SYKE datasets.
        
        Returns:
            GeoDataFrame with flood zones
        """
        self.logger.info("Starting SYKE data collection...")
        return self.collect_flood_zones()


if __name__ == "__main__":
    """Run SYKE collector when executed as module."""
    import sys
    
    collector = SYKECollector()
    
    print("Collecting flood hazard zones from SYKE...")
    print(f"AOI: {AOI_CENTER_LAT}, {AOI_CENTER_LON} (radius: {AOI_RADIUS_KM} km)")
    print("Return period: 1:100 years (standard for planning)")
    
    gdf = collector.collect()
    
    if gdf is not None and not gdf.empty:
        print(f"\n Successfully collected {len(gdf)} flood hazard zones")
        print(f"   Saved to: {RAW_DATA_DIR / 'syke_flood_zones_100a.geojson'}")
        print(f"   Fields: {list(gdf.columns)}")
    elif gdf is not None and gdf.empty:
        print("\n No flood hazard zones found in AOI (this may be normal)")
        print("   Note: This is a valid result if there is no flood risk in the region")
    else:
        print("\n Failed to collect flood hazard data")
        sys.exit(1)
    
    print("\nNote: Flood hazard maps are relatively static datasets. In production:")
    print("  - Download full dataset once and store in cloud storage (S3/GCS)")
    print("  - Index in PostGIS database for fast spatial queries")
    print("  - Processors would query PostGIS instead of WFS")
    print("  - Update when SYKE releases new flood models (typically every few years)")
