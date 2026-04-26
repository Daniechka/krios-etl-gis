"""
Process MML cadastral parcel data for analysis.

This processor:
1. Reads raw parcel data from MML collector
2. Translates field names to English
3. Ensures CRS is EPSG:3067 (ETRS-TM35FIN)
4. Crops to Area of Interest (AOI)
5. Saves to processed data folder
"""

import logging
from pathlib import Path
from typing import Dict, Optional

import geopandas as gpd

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class MMLParcelProcessor:
    """Process MML cadastral parcel data."""
    
    # Field name translations from Finnish to English
    FIELD_TRANSLATIONS = {
        'kiinteistotunnus': 'property_id',
        'rekisteriyksikkolaji': 'property_type',
        'pinta_ala': 'area_m2',
        'area_ha': 'area_ha',
        'geometry': 'geometry'
    }
    
    def __init__(
        self,
        raw_data_path: Path,
        aoi_path: Path,
        output_path: Path,
        target_crs: str = "EPSG:3067"
    ):
        """
        Initialize processor.
        
        Args:
            raw_data_path: Path to raw MML parcel GeoJSON
            aoi_path: Path to AOI GeoJSON
            output_path: Path for processed output
            target_crs: Target CRS (default: EPSG:3067)
        """
        self.raw_data_path = raw_data_path
        self.aoi_path = aoi_path
        self.output_path = output_path
        self.target_crs = target_crs
    
    def translate_fields(self, gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        """
        Translate Finnish field names to English.
        
        Args:
            gdf: Input GeoDataFrame with Finnish field names
            
        Returns:
            GeoDataFrame with English field names
        """
        logger.info("Translating field names to English")
        
        # Create mapping for fields that exist in the data
        rename_dict = {}
        for finnish_name, english_name in self.FIELD_TRANSLATIONS.items():
            if finnish_name in gdf.columns:
                rename_dict[finnish_name] = english_name
        
        if rename_dict:
            gdf = gdf.rename(columns=rename_dict)
            logger.info(f"Translated fields: {list(rename_dict.values())}")
        else:
            logger.warning("No field names matched for translation")
        
        return gdf
    
    def ensure_crs(self, gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        """
        Ensure data is in target CRS.
        
        Args:
            gdf: Input GeoDataFrame
            
        Returns:
            GeoDataFrame in target CRS
        """
        if gdf.crs is None:
            logger.warning(f"No CRS found, assuming {self.target_crs}")
            gdf.set_crs(self.target_crs, inplace=True)
        elif gdf.crs != self.target_crs:
            logger.info(f"Reprojecting from {gdf.crs} to {self.target_crs}")
            gdf = gdf.to_crs(self.target_crs)
        else:
            logger.info(f"CRS already {self.target_crs}")
        
        return gdf
    
    def crop_to_aoi(
        self,
        gdf: gpd.GeoDataFrame,
        aoi_gdf: gpd.GeoDataFrame
    ) -> gpd.GeoDataFrame:
        """
        Crop parcels to AOI extent.
        
        Args:
            gdf: Parcel GeoDataFrame
            aoi_gdf: AOI GeoDataFrame
            
        Returns:
            Cropped GeoDataFrame
        """
        logger.info("Cropping parcels to AOI")
        
        # Ensure both are in same CRS
        if aoi_gdf.crs != gdf.crs:
            aoi_gdf = aoi_gdf.to_crs(gdf.crs)
        
        # Spatial join to find intersecting parcels
        cropped = gpd.sjoin(
            gdf,
            aoi_gdf,
            how='inner',
            predicate='intersects'
        )
        
        # Remove join index column if it exists
        if 'index_right' in cropped.columns:
            cropped = cropped.drop(columns=['index_right'])
        
        logger.info(f"Kept {len(cropped)} parcels (from {len(gdf)} total)")
        
        return cropped
    
    def process(self) -> Optional[gpd.GeoDataFrame]:
        """
        Execute full processing pipeline.
        
        Returns:
            Processed GeoDataFrame or None if processing fails
        """
        logger.info("=== Starting MML Parcel Processing ===")
        
        try:
            # Step 1: Read raw parcel data
            logger.info(f"Reading raw parcel data: {self.raw_data_path}")
            if not self.raw_data_path.exists():
                raise FileNotFoundError(f"Raw data not found: {self.raw_data_path}")
            
            parcels = gpd.read_file(self.raw_data_path)
            logger.info(f"Loaded {len(parcels)} parcels")
            
            # Step 2: Translate field names
            parcels = self.translate_fields(parcels)
            
            # Step 3: Ensure CRS
            parcels = self.ensure_crs(parcels)
            
            # Step 4: Read and prepare AOI
            logger.info(f"Reading AOI: {self.aoi_path}")
            if not self.aoi_path.exists():
                raise FileNotFoundError(f"AOI not found: {self.aoi_path}")
            
            aoi = gpd.read_file(self.aoi_path)
            
            # Step 5: Crop to AOI
            parcels_cropped = self.crop_to_aoi(parcels, aoi)
            
            if parcels_cropped.empty:
                logger.warning("No parcels found within AOI")
                return None
            
            # Step 6: Calculate area if not present
            if 'area_ha' not in parcels_cropped.columns:
                logger.info("Calculating parcel areas")
                parcels_cropped['area_ha'] = parcels_cropped.geometry.area / 10000
            
            # Step 7: Save processed data
            logger.info(f"Saving processed data to: {self.output_path}")
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            parcels_cropped.to_file(self.output_path, driver='GeoJSON')
            
            logger.info("=== Processing complete ===")
            logger.info(f"Output: {self.output_path}")
            logger.info(f"Final parcel count: {len(parcels_cropped)}")
            
            return parcels_cropped
            
        except Exception as e:
            logger.error(f"Processing failed: {e}")
            return None


def main():
    """Main execution function."""
    # Define paths
    project_root = Path(__file__).parent.parent.parent
    raw_data_path = project_root / "data" / "raw" / "mml_parcels.geojson"
    aoi_path = project_root / "data" / "aoi_test.geojson"
    output_path = project_root / "data" / "processed" / "parcels.geojson"
    
    # Check inputs exist
    if not raw_data_path.exists():
        raise FileNotFoundError(
            f"Raw parcel data not found: {raw_data_path}\n"
            "Run the MML collector first: python3 -m src.collectors.mml_collector"
        )
    if not aoi_path.exists():
        raise FileNotFoundError(f"AOI not found: {aoi_path}")
    
    # Create processor and run
    processor = MMLParcelProcessor(
        raw_data_path=raw_data_path,
        aoi_path=aoi_path,
        output_path=output_path
    )
    
    processor.process()
    
    logger.info("Done!")


if __name__ == "__main__":
    main()
