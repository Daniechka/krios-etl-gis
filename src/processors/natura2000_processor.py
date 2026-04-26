"""
Process Natura 2000 protected areas data for site selection analysis.

This processor handles Natura 2000 data collected from EEA:
1. Loads raw GeoJSON (in EPSG:4326)
2. Translates key field names to English
3. Selects relevant fields for constraint analysis
4. Reprojects to project CRS (ETRS-TM35FIN, EPSG:3067)
5. Crops to Area of Interest (AOI)
6. Outputs analysis-ready protected areas data

Output: GeoPackage with Natura 2000 site boundaries within AOI.
"""

import logging
from pathlib import Path

import geopandas as gpd

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class Natura2000Processor:
    """Process Natura 2000 protected areas data within AOI."""
    
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
            raw_data_path: Path to raw Natura 2000 GeoJSON (data/raw/natura2000_sites.geojson)
            aoi_path: Path to AOI GeoJSON (any CRS)
            output_path: Path for output GeoPackage (data/processed/natura2000_sites.gpkg)
            target_crs: Target CRS for processing (default: ETRS-TM35FIN / EPSG:3067)
        """
        self.raw_data_path = raw_data_path
        self.aoi_path = aoi_path
        self.output_path = output_path
        self.target_crs = target_crs
        
    def load_and_translate_fields(self) -> gpd.GeoDataFrame:
        """
        Load raw Natura 2000 data and translate field names to English.
        
        Returns:
            GeoDataFrame with English field names
        """
        logger.info(f"Loading Natura 2000 data from {self.raw_data_path}")
        
        # Read raw GeoJSON
        gdf = gpd.read_file(self.raw_data_path)
        
        # Ensure CRS is set (should be WGS84 from API)
        if gdf.crs is None:
            logger.warning("No CRS defined, setting to EPSG:4326 (WGS84)")
            gdf = gdf.set_crs("EPSG:4326")
        
        logger.info(f"Loaded {len(gdf)} Natura 2000 sites")
        logger.info(f"CRS: {gdf.crs}")
        
        # Field name translations (keep existing English names, add clarity where needed)
        # Most fields are already in English from the EEA API
        field_mapping = {
            'SITECODE': 'site_code',
            'SITENAME': 'site_name',
            'SITETYPE': 'site_type',
            'MS': 'member_state',
            'Area_ha': 'area_ha',
            'Area_km2': 'area_km2',
            'RELEASE_DATE': 'release_date',
        }
        
        # Rename fields that exist
        existing_fields = {k: v for k, v in field_mapping.items() if k in gdf.columns}
        gdf = gdf.rename(columns=existing_fields)
        
        logger.info(f"Translated {len(existing_fields)} field names to English")
        return gdf
        
    def select_relevant_fields(self, gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        """
        Select only relevant fields needed for site selection constraint analysis.
        
        Args:
            gdf: GeoDataFrame with all fields
            
        Returns:
            GeoDataFrame with only relevant fields
        """
        # Fields to keep for site selection analysis
        relevant_fields = [
            'site_code',           # Unique Natura 2000 code
            'site_name',           # Protected area name
            'site_type',           # A=Birds, B=Habitats, C=Both
            'member_state',        # Should be 'FI' for Finland
            'area_ha',             # Area in hectares
            'area_km2',            # Area in km2
            'release_date',        # Data release date
            'geometry'             # Polygon boundaries
        ]
        
        # Filter to only relevant fields that exist
        existing_fields = [f for f in relevant_fields if f in gdf.columns]
        gdf_filtered = gdf[existing_fields].copy()
        
        logger.info(f"Kept {len(existing_fields)} relevant fields, dropped {len(gdf.columns) - len(existing_fields)} fields")
        return gdf_filtered
        
    def reproject_to_target_crs(self, gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        """
        Reproject data to target CRS.
        
        Args:
            gdf: GeoDataFrame in original CRS
            
        Returns:
            GeoDataFrame in target CRS
        """
        if gdf.crs is None:
            logger.warning("No CRS defined, assuming EPSG:4326 (WGS84)")
            gdf = gdf.set_crs("EPSG:4326")
            
        if gdf.crs.to_string() != self.target_crs:
            logger.info(f"Reprojecting from {gdf.crs} to {self.target_crs}")
            gdf = gdf.to_crs(self.target_crs)
        else:
            logger.info(f"Data already in target CRS: {self.target_crs}")
            
        return gdf
        
    def crop_to_aoi(self, gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        """
        Crop protected areas to Area of Interest (AOI).
        
        Args:
            gdf: GeoDataFrame with all protected sites
            
        Returns:
            GeoDataFrame with only sites within or intersecting AOI
        """
        logger.info(f"Loading AOI from {self.aoi_path}")
        
        # Read AOI
        aoi_gdf = gpd.read_file(self.aoi_path)
        
        # Reproject AOI to match data CRS
        if aoi_gdf.crs.to_string() != self.target_crs:
            logger.info(f"Reprojecting AOI from {aoi_gdf.crs} to {self.target_crs}")
            aoi_gdf = aoi_gdf.to_crs(self.target_crs)
        
        # Clip to AOI (keeps geometries that intersect)
        logger.info("Clipping Natura 2000 sites to AOI")
        gdf_aoi = gpd.clip(gdf, aoi_gdf)
        
        logger.info(f"Filtered from {len(gdf)} to {len(gdf_aoi)} sites within AOI")
        
        if len(gdf_aoi) == 0:
            logger.warning("No Natura 2000 sites found within AOI! This may be normal if area has no protected sites.")
        
        return gdf_aoi
        
    def save_output(self, gdf: gpd.GeoDataFrame):
        """
        Save processed data to GeoPackage.
        
        Args:
            gdf: Processed GeoDataFrame
        """
        # Ensure output directory exists
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"Saving processed data to {self.output_path}")
        gdf.to_file(self.output_path, driver='GPKG', layer='natura2000_sites')
        
        # Log summary statistics
        if len(gdf) > 0:
            logger.info(f"Saved {len(gdf)} Natura 2000 sites")
            
            if 'site_type' in gdf.columns:
                site_type_counts = gdf['site_type'].value_counts()
                logger.info("Site types:")
                for site_type, count in site_type_counts.items():
                    logger.info(f"  {site_type}: {count} sites")
            
            if 'area_ha' in gdf.columns:
                total_protected_area = gdf['area_ha'].sum()
                logger.info(f"Total protected area: {total_protected_area:.1f} ha ({total_protected_area/100:.1f} km²)")
        
        logger.info("Processing complete!")
        
    def process(self):
        """
        Run full processing pipeline.
        
        Pipeline:
        1. Load raw data and translate field names
        2. Select relevant fields
        3. Reproject to target CRS
        4. Crop to AOI
        5. Save to GeoPackage
        """
        logger.info("=" * 60)
        logger.info("Starting Natura 2000 protected areas processing")
        logger.info("=" * 60)
        
        # Step 1: Load and translate
        gdf = self.load_and_translate_fields()
        
        # Step 2: Select relevant fields
        gdf = self.select_relevant_fields(gdf)
        
        # Step 3: Reproject to target CRS
        gdf = self.reproject_to_target_crs(gdf)
        
        # Step 4: Crop to AOI
        gdf = self.crop_to_aoi(gdf)
        
        # Step 5: Save output
        self.save_output(gdf)


def main():
    """Main entry point for running the processor standalone."""
    from pathlib import Path
    
    # Define paths relative to project root
    project_root = Path(__file__).parent.parent.parent
    
    raw_data_path = project_root / "data" / "raw" / "natura2000_sites.geojson"
    aoi_path = project_root / "data" / "aoi_test.geojson"
    output_path = project_root / "data" / "processed" / "natura2000_sites.gpkg"
    
    # Check if input files exist
    if not raw_data_path.exists():
        logger.error(f"Raw data file not found: {raw_data_path}")
        logger.error("Please run the Natura 2000 collector first:")
        logger.error("  python -m src.collectors.natura2000_collector")
        return
        
    if not aoi_path.exists():
        logger.error(f"AOI file not found: {aoi_path}")
        return
    
    # Initialize and run processor
    processor = Natura2000Processor(
        raw_data_path=raw_data_path,
        aoi_path=aoi_path,
        output_path=output_path,
        target_crs="EPSG:3067"
    )
    
    processor.process()


if __name__ == "__main__":
    main()
