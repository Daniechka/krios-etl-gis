"""
Process Fingrid grid capacity headroom data for site selection analysis.

This processor handles the manually-extracted Fingrid capacity data:
1. Loads raw GeoJSON (in EPSG:3067)
2. Translates Finnish field names to English
3. Drops irrelevant fields
4. Reprojects to project CRS (ETRS-TM35FIN, EPSG:3067)
5. Crops to Area of Interest (AOI)
6. Outputs analysis-ready substation capacity data

Output: GeoPackage with substations and available grid capacity (MW) within AOI.
"""

import logging
from pathlib import Path

import geopandas as gpd

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class FingridCapacityProcessor:
    """Process Fingrid grid capacity headroom data within AOI."""
    
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
            raw_data_path: Path to raw Fingrid GeoJSON (data/raw/fingrid_capacity_headroom.geojson)
            aoi_path: Path to AOI GeoJSON (any CRS)
            output_path: Path for output GeoPackage (data/processed/fingrid_capacity_aoi.gpkg)
            target_crs: Target CRS for processing (default: ETRS-TM35FIN / EPSG:3067)
        """
        self.raw_data_path = raw_data_path
        self.aoi_path = aoi_path
        self.output_path = output_path
        self.target_crs = target_crs
        
    def load_and_translate_fields(self) -> gpd.GeoDataFrame:
        """
        Load raw Fingrid data and translate field names to English.
        
        Note: The raw GeoJSON file doesn't include CRS metadata, but the coordinates
        are in EPSG:3067 (ETRS-TM35FIN). We manually set this CRS after loading.
        
        Returns:
            GeoDataFrame with English field names
        """
        logger.info(f"Loading Fingrid capacity data from {self.raw_data_path}")
        
        # Read raw GeoJSON
        gdf = gpd.read_file(self.raw_data_path)
        
        # The GeoJSON lacks CRS metadata, but coordinates are in EPSG:3067
        # Example coords: [356669.2621, 6992357.268] are clearly in meters (Finnish TM35FIN)
        if gdf.crs is None or gdf.crs.to_string() == "EPSG:4326":
            logger.info("Setting CRS to EPSG:3067 (coordinates are in Finnish TM35FIN, not WGS84)")
            gdf = gdf.set_crs(self.target_crs, allow_override=True)
        
        logger.info(f"Loaded {len(gdf)} substations")
        logger.info(f"CRS: {gdf.crs}")
        
        # Field name translations (Finnish -> English)
        field_mapping = {
            'STATION': 'station_name',
            'VOLUME': 'total_capacity_mw',
            'f_1_myytavissa_nyt': 'available_capacity_mw',
            'F_2_Kaavoitusmenettely_kaynnist': 'reserved_zoning_process_mw',
            'F_3_OAS_ollut_nahtavilla': 'reserved_eia_published_mw',
            'F_4_Luonnos_ollut_nahtavilla': 'reserved_draft_published_mw',
            'F_5_Ehdotus_ollut_nahtavilla': 'reserved_proposal_published_mw',
            'F_6_Kaava_hyvaksytty': 'reserved_zoning_approved_mw',
            'F_7_Kaava_lainvoimainen': 'reserved_zoning_binding_mw',
            'F_8_Hanke_rakenteilla': 'reserved_under_construction_mw',
            'YEAR': 'data_year',
            'VUOSI': 'data_year_fi',
        }
        
        # Rename fields
        gdf = gdf.rename(columns=field_mapping)
        
        logger.info("Translated field names to English")
        return gdf
        
    def select_relevant_fields(self, gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        """
        Drop irrelevant fields, keep only what's needed for analysis.
        
        Args:
            gdf: GeoDataFrame with all fields
            
        Returns:
            GeoDataFrame with only relevant fields
        """
        # Fields to keep for site selection analysis
        relevant_fields = [
            'station_name',                    # Substation name
            'total_capacity_mw',               # Total capacity at substation
            'available_capacity_mw',           # PRIMARY: MW available now
            'reserved_zoning_process_mw',      # Reserved capacity (planning stages)
            'reserved_eia_published_mw',
            'reserved_draft_published_mw',
            'reserved_proposal_published_mw',
            'reserved_zoning_approved_mw',
            'reserved_zoning_binding_mw',
            'reserved_under_construction_mw',
            'data_year',                       # Data vintage
            'geometry'                         # Point geometry
        ]
        
        # Filter to only relevant fields that exist
        existing_fields = [f for f in relevant_fields if f in gdf.columns]
        gdf_filtered = gdf[existing_fields].copy()
        
        logger.info(f"Kept {len(existing_fields)} relevant fields, dropped {len(gdf.columns) - len(existing_fields)} irrelevant fields")
        return gdf_filtered
        
    def reproject_to_target_crs(self, gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        """
        Reproject data to target CRS if needed.
        
        Args:
            gdf: GeoDataFrame in original CRS
            
        Returns:
            GeoDataFrame in target CRS
        """
        if gdf.crs is None:
            logger.warning("No CRS defined, assuming EPSG:3067 (ETRS-TM35FIN)")
            gdf = gdf.set_crs(self.target_crs)
        elif gdf.crs.to_string() != self.target_crs:
            logger.info(f"Reprojecting from {gdf.crs} to {self.target_crs}")
            gdf = gdf.to_crs(self.target_crs)
        else:
            logger.info(f"Data already in target CRS: {self.target_crs}")
            
        return gdf
        
    def crop_to_aoi(self, gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        """
        Crop substations to Area of Interest (AOI).
        
        Args:
            gdf: GeoDataFrame with all substations
            
        Returns:
            GeoDataFrame with only substations within AOI
        """
        logger.info(f"Loading AOI from {self.aoi_path}")
        
        # Read AOI
        aoi_gdf = gpd.read_file(self.aoi_path)
        
        # Reproject AOI to match data CRS
        if aoi_gdf.crs.to_string() != self.target_crs:
            logger.info(f"Reprojecting AOI from {aoi_gdf.crs} to {self.target_crs}")
            aoi_gdf = aoi_gdf.to_crs(self.target_crs)
        
        # Spatial join to filter substations within AOI
        logger.info("Filtering substations within AOI")
        gdf_aoi = gpd.sjoin(
            gdf,
            aoi_gdf,
            how='inner',
            predicate='within'
        )
        
        # Drop the index_right column from spatial join
        if 'index_right' in gdf_aoi.columns:
            gdf_aoi = gdf_aoi.drop(columns=['index_right'])
        
        logger.info(f"Filtered from {len(gdf)} to {len(gdf_aoi)} substations within AOI")
        
        if len(gdf_aoi) == 0:
            logger.warning("No substations found within AOI! Check AOI extent.")
        
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
        gdf.to_file(self.output_path, driver='GPKG')
        
        # Log summary statistics
        if 'available_capacity_mw' in gdf.columns:
            capacity_stats = gdf['available_capacity_mw'].describe()
            logger.info(f"Available capacity statistics (MW):")
            logger.info(f"  Min: {capacity_stats['min']:.1f}")
            logger.info(f"  Max: {capacity_stats['max']:.1f}")
            logger.info(f"  Mean: {capacity_stats['mean']:.1f}")
            logger.info(f"  Median: {capacity_stats['50%']:.1f}")
        
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
        logger.info("Starting Fingrid capacity headroom processing")
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
    
    raw_data_path = project_root / "data" / "raw" / "fingrid_capacity_headroom.geojson"
    aoi_path = project_root / "data" / "aoi_test.geojson"
    output_path = project_root / "data" / "processed" / "fingrid_capacity_aoi.gpkg"
    
    # Check if input files exist
    if not raw_data_path.exists():
        logger.error(f"Raw data file not found: {raw_data_path}")
        logger.error("Please extract data manually from Fingrid map portal first.")
        logger.error("See DATA_COLLECTION_NOTES.md for instructions.")
        return
        
    if not aoi_path.exists():
        logger.error(f"AOI file not found: {aoi_path}")
        return
    
    # Initialize and run processor
    processor = FingridCapacityProcessor(
        raw_data_path=raw_data_path,
        aoi_path=aoi_path,
        output_path=output_path,
        target_crs="EPSG:3067"
    )
    
    processor.process()


if __name__ == "__main__":
    main()
