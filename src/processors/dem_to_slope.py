"""
Process DEM tiles to slope gradient raster for site selection analysis.

This processor uses rasterio and geopandas for clean, Pythonic geospatial operations.

Pipeline:
1. Reprojects AOI from WGS84 to ETRS-TM35FIN
2. Selects DEM tiles that intersect the AOI using the tile index
3. Merges selected tiles into a single mosaic
4. Calculates slope gradient in percent
5. Crops to AOI extent
6. Outputs analysis-ready slope raster

Output: Slope gradient in percent (0-100+), suitable for filtering areas with <8% gradient.
"""

import logging
from pathlib import Path
from typing import List, Tuple

import numpy as np
import rasterio
from rasterio.merge import merge
from rasterio.mask import mask
import geopandas as gpd

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class DEMToSlopeProcessor:
    """Process DEM tiles to slope gradient raster within AOI."""
    
    def __init__(
        self,
        aoi_path: Path,
        tile_index_path: Path,
        dem_base_path: Path,
        output_path: Path,
        target_crs: str = "EPSG:3067"
    ):
        """
        Initialize processor.
        
        Args:
            aoi_path: Path to AOI GeoJSON (any CRS)
            tile_index_path: Path to DEM tile index GeoPackage
            dem_base_path: Base directory containing DEM tiles
            output_path: Path for output slope raster
            target_crs: Target CRS for processing (default: ETRS-TM35FIN)
        """
        self.aoi_path = aoi_path
        self.tile_index_path = tile_index_path
        self.dem_base_path = dem_base_path
        self.output_path = output_path
        self.target_crs = target_crs
        
    def reproject_aoi(self) -> Tuple[gpd.GeoDataFrame, tuple]:
        """
        Reproject AOI to target CRS.
        
        Returns:
            Tuple of (reprojected GeoDataFrame, bounding box in target CRS)
        """
        logger.info(f"Reprojecting AOI from {self.aoi_path} to {self.target_crs}")
        
        # Read AOI
        aoi_gdf = gpd.read_file(self.aoi_path)
        
        # Reproject to target CRS
        aoi_reprojected = aoi_gdf.to_crs(self.target_crs)
        
        # Get bounding box (minx, miny, maxx, maxy)
        bounds = aoi_reprojected.total_bounds
        
        logger.info(f"Reprojected AOI extent: {bounds.tolist()}")
        return aoi_reprojected, tuple(bounds)
        
    def select_intersecting_tiles(self, aoi_gdf: gpd.GeoDataFrame) -> List[Path]:
        """
        Select DEM tiles that intersect with AOI.
        
        Args:
            aoi_gdf: GeoDataFrame with AOI geometry in target CRS
            
        Returns:
            List of paths to intersecting DEM tiles
        """
        logger.info("Selecting DEM tiles that intersect AOI")
        
        # Read tile index
        tile_index = gpd.read_file(self.tile_index_path)
        
        # Spatial join to find intersecting tiles
        intersecting = gpd.sjoin(
            tile_index,
            aoi_gdf,
            how='inner',
            predicate='intersects'
        )
        
        # Get project root (dem_base_path is project_root/data/raw/etrs-tm35fin-n2000)
        project_root = self.dem_base_path.parent.parent.parent
        
        # Collect tile paths
        tile_paths = []
        for _, row in intersecting.iterrows():
            filepath = row['filepath']
            full_path = project_root / filepath
            
            if full_path.exists():
                tile_paths.append(full_path)
            else:
                logger.warning(f"Tile not found: {full_path}")
                
        logger.info(f"Found {len(tile_paths)} tiles intersecting AOI")
        return tile_paths
        
    def merge_tiles(self, tile_paths: List[Path]) -> Tuple[np.ndarray, dict]:
        """
        Merge DEM tiles into single mosaic.
        
        Args:
            tile_paths: List of DEM tile paths
            
        Returns:
            Tuple of (merged array, rasterio profile)
        """
        logger.info(f"Merging {len(tile_paths)} DEM tiles")
        
        # Open all tiles
        src_files = [rasterio.open(str(p)) for p in tile_paths]
        
        # Merge tiles
        mosaic, out_transform = merge(src_files)
        
        # Get metadata from first tile and update
        out_meta = src_files[0].meta.copy()
        out_meta.update({
            "driver": "GTiff",
            "height": mosaic.shape[1],
            "width": mosaic.shape[2],
            "transform": out_transform,
            "compress": "lzw",
            "tiled": True
        })
        
        # Close all files
        for src in src_files:
            src.close()
        
        logger.info(f"Merged DEM shape: {mosaic.shape}")
        return mosaic, out_meta
        
    def calculate_slope(
        self,
        dem_array: np.ndarray,
        transform: rasterio.Affine,
        resolution: float = 10.0
    ) -> np.ndarray:
        """
        Calculate slope gradient in percent from DEM.
        
        Args:
            dem_array: DEM elevation array (2D or 3D with single band)
            transform: Rasterio affine transform
            resolution: Cell size in meters (default: 10m)
            
        Returns:
            Slope array in percent
        """
        logger.info("Calculating slope gradient (percent)")
        
        # Get DEM band (handle both 2D and 3D arrays)
        if dem_array.ndim == 3:
            dem = dem_array[0]
        else:
            dem = dem_array
            
        # Handle nodata
        nodata = -9999
        valid_mask = dem != nodata
        
        # Calculate gradients (derivatives in x and y directions)
        # np.gradient returns change per cell
        dy, dx = np.gradient(dem.astype(float))
        
        # Convert elevation change to slope percent
        # slope = sqrt(dx^2 + dy^2) / resolution * 100
        slope = np.sqrt(dx**2 + dy**2) / resolution * 100
        
        # Apply nodata mask
        slope[~valid_mask] = nodata
        
        # Get statistics for valid cells
        valid_slope = slope[valid_mask]
        logger.info(f"Slope stats: min={valid_slope.min():.2f}%, "
                   f"max={valid_slope.max():.2f}%, "
                   f"mean={valid_slope.mean():.2f}%")
        
        return slope
        
    def crop_to_aoi(
        self,
        raster_array: np.ndarray,
        raster_meta: dict,
        aoi_gdf: gpd.GeoDataFrame
    ) -> Tuple[np.ndarray, dict]:
        """
        Crop raster to AOI extent.
        
        Args:
            raster_array: Input raster array
            raster_meta: Rasterio metadata/profile
            aoi_gdf: GeoDataFrame with AOI geometry
            
        Returns:
            Tuple of (cropped array, updated metadata)
        """
        logger.info("Cropping to AOI")
        
        # Write to temporary file (rasterio.mask requires a dataset)
        import tempfile
        with tempfile.NamedTemporaryFile(suffix='.tif', delete=False) as tmp:
            tmp_path = Path(tmp.name)
            
        try:
            # Write temporary raster
            with rasterio.open(tmp_path, 'w', **raster_meta) as tmp_dst:
                if raster_array.ndim == 2:
                    tmp_dst.write(raster_array, 1)
                else:
                    tmp_dst.write(raster_array)
            
            # Crop using mask
            with rasterio.open(tmp_path) as src:
                cropped, out_transform = mask(
                    src,
                    aoi_gdf.geometry,
                    crop=True,
                    nodata=-9999
                )
                
                # Update metadata
                out_meta = src.meta.copy()
                out_meta.update({
                    "height": cropped.shape[1],
                    "width": cropped.shape[2],
                    "transform": out_transform
                })
                
        finally:
            # Cleanup temp file
            if tmp_path.exists():
                tmp_path.unlink()
        
        logger.info(f"Cropped shape: {cropped.shape}")
        return cropped, out_meta
        
    def process(self) -> Path:
        """
        Execute full processing pipeline.
        
        Returns:
            Path to output slope raster
        """
        logger.info("=== Starting DEM to Slope processing ===")
        
        # Step 1: Reproject AOI
        aoi_reprojected, aoi_bbox = self.reproject_aoi()
        
        # Step 2: Select intersecting tiles
        tile_paths = self.select_intersecting_tiles(aoi_reprojected)
        
        if not tile_paths:
            raise ValueError("No DEM tiles found intersecting AOI")
            
        # Step 3: Merge tiles
        merged_dem, dem_meta = self.merge_tiles(tile_paths)
        
        # Step 4: Calculate slope
        slope_array = self.calculate_slope(
            merged_dem,
            dem_meta['transform'],
            resolution=10.0
        )
        
        # Update metadata for slope raster
        slope_meta = dem_meta.copy()
        slope_meta.update({
            "dtype": rasterio.float32,
            "nodata": -9999,
            "count": 1
        })
        
        # Step 5: Crop to AOI
        slope_cropped, final_meta = self.crop_to_aoi(
            slope_array,
            slope_meta,
            aoi_reprojected
        )
        
        # Step 6: Write output
        logger.info(f"Writing output to: {self.output_path}")
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        
        with rasterio.open(self.output_path, 'w', **final_meta) as dst:
            dst.write(slope_cropped)
        
        logger.info("=== Processing complete ===")
        logger.info(f"Output: {self.output_path}")
        
        return self.output_path


def main():
    """Main execution function."""
    # Define paths
    project_root = Path(__file__).parent.parent.parent
    aoi_path = project_root / "data" / "aoi_test.geojson"
    tile_index_path = project_root / "data" / "processed" / "dem_tile_index.gpkg"
    dem_base_path = project_root / "data" / "raw" / "etrs-tm35fin-n2000"
    output_path = project_root / "data" / "processed" / "slope_gradient_percent.tif"
    
    # Check inputs exist
    if not aoi_path.exists():
        raise FileNotFoundError(f"AOI not found: {aoi_path}")
    if not tile_index_path.exists():
        raise FileNotFoundError(f"Tile index not found: {tile_index_path}")
    if not dem_base_path.exists():
        raise FileNotFoundError(f"DEM directory not found: {dem_base_path}")
        
    # Create processor and run
    processor = DEMToSlopeProcessor(
        aoi_path=aoi_path,
        tile_index_path=tile_index_path,
        dem_base_path=dem_base_path,
        output_path=output_path
    )
    
    processor.process()
    
    logger.info("Done!")


if __name__ == "__main__":
    main()
