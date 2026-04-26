"""
Process SYKE flood hazard zones data for site selection analysis.

This processor handles flood hazard data collected from SYKE:
1. Loads raw GeoJSON (in EPSG:4326)
2. Translates Finnish field names to English
3. Selects relevant fields for constraint analysis
4. Reprojects to project CRS (ETRS-TM35FIN, EPSG:3067)
5. Crops to Area of Interest (AOI)
6. Outputs analysis-ready flood hazard zones

Output: GeoPackage with flood hazard zone boundaries within AOI.
"""

import logging
from pathlib import Path

import geopandas as gpd

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class SYKEFloodProcessor:
    """Process SYKE flood hazard zones data within AOI."""
    
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
            raw_data_path: Path to raw SYKE flood GeoJSON (data/raw/syke_flood_zones_*.geojson)
            aoi_path: Path to AOI GeoJSON (any CRS)
            output_path: Path for output GeoPackage (data/processed/syke_flood_zones.gpkg)
            target_crs: Target CRS for processing (default: ETRS-TM35FIN / EPSG:3067)
        """
        self.raw_data_path = raw_data_path
        self.aoi_path = aoi_path
        self.output_path = output_path
        self.target_crs = target_crs
        
    def load_and_translate_fields(self) -> gpd.GeoDataFrame:
        """
        Load raw SYKE flood data and translate field names to English.
        
        Returns:
            GeoDataFrame with English field names
        """
        logger.info(f"Loading SYKE flood hazard data from {self.raw_data_path}")
        
        # Read raw GeoJSON
        gdf = gpd.read_file(self.raw_data_path)
        
        # Ensure CRS is set (should be WGS84 from collector)
        if gdf.crs is None:
            logger.warning("No CRS defined, setting to EPSG:4326 (WGS84)")
            gdf = gdf.set_crs("EPSG:4326")
        
        logger.info(f"Loaded {len(gdf)} flood hazard zones")
        logger.info(f"CRS: {gdf.crs}")
        
        # Field name translations (Finnish -> English)
        field_mapping = {
            'nimi': 'name',
            'kohdenro': 'target_number',
            'tulvakartoitustyyppi': 'flood_mapping_type',
            'toistuvuus': 'return_period',
            'syvsuojluokka': 'depth_protection_class',
            'syvvyohluokka': 'depth_zone_class',
            'tulvasuojluokka': 'flood_protection_class',
            'maarityswmenetelma': 'determination_method',
            'korkeusain': 'elevation_source',
            'korkeusainnro': 'elevation_source_number',
            'korkeusvirhe_m': 'elevation_error_m',
            'digpohja': 'digital_base',
            'digorg': 'digital_org',
            'muutospvm': 'modification_date',
            'shape_length': 'shape_length',
            'shape_area': 'shape_area',
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
            'name',                      # Name of flooded area/watercourse
            'return_period',             # Return period (e.g., "100" for 1:100 year)
            'flood_mapping_type',        # Type of flood mapping
            'depth_zone_class',          # Flood depth zone classification
            'depth_protection_class',    # Flood depth protection classification
            'flood_protection_class',    # Flood protection class
            'determination_method',      # How flood zone was determined
            'elevation_source',          # Source of elevation data
            'elevation_error_m',         # Elevation uncertainty in meters
            'modification_date',         # Last modification date
            'geometry'                   # Polygon boundaries
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
        Crop flood hazard zones to Area of Interest (AOI).
        
        Args:
            gdf: GeoDataFrame with all flood zones
            
        Returns:
            GeoDataFrame with only zones within or intersecting AOI
        """
        logger.info(f"Loading AOI from {self.aoi_path}")
        
        # Read AOI
        aoi_gdf = gpd.read_file(self.aoi_path)
        
        # Reproject AOI to match data CRS
        if aoi_gdf.crs.to_string() != self.target_crs:
            logger.info(f"Reprojecting AOI from {aoi_gdf.crs} to {self.target_crs}")
            aoi_gdf = aoi_gdf.to_crs(self.target_crs)
        
        # Clip to AOI (keeps geometries that intersect)
        logger.info("Clipping flood hazard zones to AOI")
        gdf_aoi = gpd.clip(gdf, aoi_gdf)
        
        logger.info(f"Filtered from {len(gdf)} to {len(gdf_aoi)} flood zones within AOI")
        
        if len(gdf_aoi) == 0:
            logger.warning("No flood hazard zones found within AOI! This may be normal if area has no flood risk.")
        
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
        gdf.to_file(self.output_path, driver='GPKG', layer='flood_hazard_zones')
        
        # Log summary statistics
        if len(gdf) > 0:
            logger.info(f"Saved {len(gdf)} flood hazard zones")
            
            if 'return_period' in gdf.columns:
                return_periods = gdf['return_period'].value_counts()
                logger.info("Return periods:")
                for period, count in return_periods.items():
                    logger.info(f"  1:{period} year: {count} zones")
            
            if 'depth_zone_class' in gdf.columns:
                depth_classes = gdf['depth_zone_class'].value_counts()
                logger.info("Depth zone classes (top 5):")
                for depth_class, count in depth_classes.head(5).items():
                    logger.info(f"  {depth_class}: {count} zones")
        
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
        logger.info("Starting SYKE flood hazard zones processing")
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
    
    # Default to 100-year return period (most common for planning)
    raw_data_path = project_root / "data" / "raw" / "syke_flood_zones_100a.geojson"
    aoi_path = project_root / "data" / "aoi_test.geojson"
    output_path = project_root / "data" / "processed" / "syke_flood_zones.gpkg"
    
    # Check if input files exist
    if not raw_data_path.exists():
        logger.error(f"Raw data file not found: {raw_data_path}")
        logger.error("Please run the SYKE collector first:")
        logger.error("  python -m src.collectors.syke_collector")
        return
        
    if not aoi_path.exists():
        logger.error(f"AOI file not found: {aoi_path}")
        return
    
    # Initialize and run processor
    processor = SYKEFloodProcessor(
        raw_data_path=raw_data_path,
        aoi_path=aoi_path,
        output_path=output_path,
        target_crs="EPSG:3067"
    )
    
    processor.process()


if __name__ == "__main__":
    main()
