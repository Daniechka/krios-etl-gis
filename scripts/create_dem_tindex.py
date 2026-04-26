"""
Create a tile index (vector file) from DEM GeoTIFF tiles.

This script scans all DEM tiles in the raw data directory and creates
a GeoPackage with polygon features representing each tile's extent along
with metadata (map sheet number, coordinate system, file format, height system).
"""

import logging
from pathlib import Path

import rasterio
from rasterio.crs import CRS
import geopandas as gpd
from shapely.geometry import box

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def get_raster_extent(raster_path: Path) -> dict:
    """
    Extract bounding box and metadata from a raster file.
    
    Args:
        raster_path: Path to the raster file
        
    Returns:
        Dictionary with geometry and metadata, or None if error
    """
    try:
        with rasterio.open(raster_path) as src:
            # Get bounds (minx, miny, maxx, maxy)
            bounds = src.bounds
            
            # Create polygon geometry from bounds
            geom = box(bounds.left, bounds.bottom, bounds.right, bounds.top)
            
            # Get CRS
            crs = src.crs
            
            # Extract map sheet number from filename (e.g., R5111 from R5111.tif)
            map_sheet = raster_path.stem
            
            # Get relative path from project root
            try:
                rel_path = raster_path.relative_to(Path.cwd())
            except ValueError:
                rel_path = raster_path
            
            # Calculate dimensions
            width_m = bounds.right - bounds.left
            height_m = bounds.top - bounds.bottom
            
            return {
                'geometry': geom,
                'filename': raster_path.name,
                'filepath': str(rel_path),
                'map_sheet': map_sheet,
                'coord_system': 'ETRS-TM35FIN',
                'file_format': 'GeoTIFF',
                'height_system': 'N2000',
                'width_m': width_m,
                'height_m': height_m,
                'crs': crs
            }
            
    except Exception as e:
        logger.error(f"Error processing {raster_path}: {e}")
        return None


def create_tile_index(dem_dir: Path, output_file: Path):
    """
    Create a tile index GeoPackage from DEM tiles.
    
    Args:
        dem_dir: Directory containing DEM tiles
        output_file: Path for output GeoPackage
    """
    # Find all TIFF files
    tif_files = list(dem_dir.rglob("*.tif")) + list(dem_dir.rglob("*.tiff"))
    
    if not tif_files:
        logger.error(f"No TIFF files found in {dem_dir}")
        return
        
    logger.info(f"Found {len(tif_files)} DEM tiles")
    
    # Process each tile
    tiles_data = []
    for tif_file in tif_files:
        tile_info = get_raster_extent(tif_file)
        if tile_info:
            tiles_data.append(tile_info)
    
    if not tiles_data:
        logger.error("No valid tiles could be processed")
        return
    
    # Create GeoDataFrame
    # Extract CRS from first tile
    crs = tiles_data[0].pop('crs')
    
    gdf = gpd.GeoDataFrame(tiles_data, crs=crs)
    
    # Ensure output directory exists
    output_file.parent.mkdir(parents=True, exist_ok=True)
    
    # Save to GeoPackage
    gdf.to_file(output_file, driver='GPKG', layer='dem_tiles')
    
    logger.info(f"Successfully created tile index with {len(tiles_data)} tiles")
    logger.info(f"Output saved to: {output_file}")


def main():
    """Main execution function."""
    # Define paths
    project_root = Path(__file__).parent.parent
    dem_dir = project_root / "data" / "raw" / "etrs-tm35fin-n2000"
    output_file = project_root / "data" / "processed" / "dem_tile_index.gpkg"
    
    # Ensure output directory exists
    output_file.parent.mkdir(parents=True, exist_ok=True)
    
    # Create tile index
    logger.info("Creating DEM tile index...")
    create_tile_index(dem_dir, output_file)
    logger.info("Done!")


if __name__ == "__main__":
    main()
