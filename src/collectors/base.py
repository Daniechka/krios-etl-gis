"""Base class for data collectors."""

import logging
from pathlib import Path
from typing import Optional
import geopandas as gpd
from shapely.geometry import Point

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class BaseCollector:
    """Base class for all data collectors."""
    
    def __init__(self, output_dir: Path):
        """
        Initialize the collector.
        
        Args:
            output_dir: Directory to save raw data
        """
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger(self.__class__.__name__)
    
    def create_aoi_circle(
        self, 
        center_lat: float, 
        center_lon: float, 
        radius_km: float,
        crs: str = "EPSG:4326"
    ) -> gpd.GeoDataFrame:
        """
        Create a circular AOI polygon.
        
        Args:
            center_lat: Latitude of center point
            center_lon: Longitude of center point
            radius_km: Radius in kilometers
            crs: Target CRS for the circle
            
        Returns:
            GeoDataFrame with circular polygon
        """
        # Create point in projected CRS for accurate buffer
        point = Point(center_lon, center_lat)
        gdf = gpd.GeoDataFrame({'geometry': [point]}, crs="EPSG:4326")
        
        # Convert to metric CRS for buffering
        gdf_projected = gdf.to_crs("EPSG:3067")  # Finland TM35FIN
        
        # Buffer by radius (in meters)
        gdf_projected['geometry'] = gdf_projected.buffer(radius_km * 1000)
        
        # Convert back to requested CRS
        return gdf_projected.to_crs(crs)
    
    def save_geodataframe(
        self, 
        gdf: gpd.GeoDataFrame, 
        filename: str,
        target_crs: Optional[str] = None
    ) -> Path:
        """
        Save GeoDataFrame to file.
        
        Args:
            gdf: GeoDataFrame to save
            filename: Output filename (without extension)
            target_crs: Optional CRS to convert to before saving
            
        Returns:
            Path to saved file
        """
        # Convert CRS if specified
        if target_crs and gdf.crs != target_crs:
            gdf = gdf.to_crs(target_crs)
            self.logger.info(f"Converted to {target_crs}")
        
        output_path = self.output_dir / f"{filename}.gpkg"
        gdf.to_file(output_path, driver="GPKG")
        self.logger.info(f"Saved {len(gdf)} features to {output_path}")
        return output_path
    
    def collect(self) -> Optional[gpd.GeoDataFrame]:
        """
        Collect data from source. Must be implemented by subclasses.
        
        Returns:
            GeoDataFrame with collected data, or None on failure
        """
        raise NotImplementedError("Subclasses must implement collect()")
