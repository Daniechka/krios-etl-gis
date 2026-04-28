"""
Process OpenStreetMap infrastructure data for site selection analysis.

This processor handles OSM data collected via Overpass API:
1. Loads raw GeoJSON layers (in EPSG:4326)
2. Reprojects to project CRS (ETRS-TM35FIN, EPSG:3067)
3. Crops point features to AOI with layer-specific buffers:
   - Data centers: 10% buffer to capture nearby facilities
   - Power lines: 10km buffer to preserve network connectivity
   - Other layers: exact AOI boundary
4. Performs basic quality control checks
5. Outputs analysis-ready infrastructure data

Output: GeoPackage with multiple layers (substations, power_lines, data_centers, etc.)
"""

import logging
from pathlib import Path
from typing import Optional, Dict

import geopandas as gpd
import pandas as pd

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class OSMInfrastructureProcessor:
    """Process OSM infrastructure data within AOI."""
    
    def __init__(
        self,
        raw_data_dir: Path,
        aoi_path: Path,
        output_path: Path,
        target_crs: str = "EPSG:3067"
    ):
        """
        Initialize processor.
        
        Args:
            raw_data_dir: Directory with raw OSM GeoPackage files (data/raw/)
            aoi_path: Path to AOI GeoJSON (any CRS)
            output_path: Path for output GeoPackage (data/processed/osm_infrastructure.gpkg)
            target_crs: Target CRS for processing (default: ETRS-TM35FIN / EPSG:3067)
        """
        self.raw_data_dir = raw_data_dir
        self.aoi_path = aoi_path
        self.output_path = output_path
        self.target_crs = target_crs
        
        # Define expected input files (collector saves as .gpkg via save_geodataframe)
        self.input_files = {
            'data_centers': 'osm_data_centers.gpkg',
            'power_plants': 'osm_power_plants.gpkg',
            'power_lines': 'osm_power_lines.gpkg',
            'substations': 'osm_substations.gpkg',
            'urban_centers': 'osm_urban_centers.gpkg',
        }
        
    def load_aoi(self) -> gpd.GeoDataFrame:
        """Load and reproject AOI to target CRS."""
        logger.info(f"Loading AOI from {self.aoi_path}")
        aoi = gpd.read_file(self.aoi_path)
        
        if aoi.crs != self.target_crs:
            logger.info(f"Reprojecting AOI from {aoi.crs} to {self.target_crs}")
            aoi = aoi.to_crs(self.target_crs)
        
        return aoi
    
    def load_osm_layer(self, layer_name: str) -> Optional[gpd.GeoDataFrame]:
        """
        Load a single OSM layer from raw data.
        
        Args:
            layer_name: Name of the layer (key from input_files dict)
            
        Returns:
            GeoDataFrame or None if file doesn't exist
        """
        filepath = self.raw_data_dir / self.input_files[layer_name]
        
        if not filepath.exists():
            logger.warning(f"File not found: {filepath}")
            return None
        
        logger.info(f"Loading {layer_name} from {filepath}")
        gdf = gpd.read_file(filepath)
        
        if len(gdf) == 0:
            logger.warning(f"No features in {layer_name}")
            return None
        
        logger.info(f"Loaded {len(gdf)} features for {layer_name}")
        return gdf
    
    def reproject_layer(self, gdf: gpd.GeoDataFrame, layer_name: str) -> gpd.GeoDataFrame:
        """Reproject layer to target CRS."""
        if gdf.crs != self.target_crs:
            logger.info(f"Reprojecting {layer_name} from {gdf.crs} to {self.target_crs}")
            gdf = gdf.to_crs(self.target_crs)
        return gdf
    
    def crop_to_aoi(self, gdf: gpd.GeoDataFrame, aoi: gpd.GeoDataFrame, layer_name: str, buffer_percent: float = 0.0) -> gpd.GeoDataFrame:
        """
        Crop layer to AOI boundary with optional buffer.
        
        Args:
            gdf: GeoDataFrame to crop
            aoi: AOI boundary
            layer_name: Name of layer (for logging)
            buffer_percent: Percentage to buffer AOI (default: 0%, no buffer)
            
        Returns:
            Cropped GeoDataFrame
        """
        # Apply buffer if specified
        aoi_for_crop = aoi.copy()
        if buffer_percent > 0:
            bounds = aoi.total_bounds
            bbox_width = bounds[2] - bounds[0]
            bbox_height = bounds[3] - bounds[1]
            diagonal = (bbox_width**2 + bbox_height**2)**0.5
            buffer_distance = diagonal * (buffer_percent / 100.0)

            logger.info(f"Buffering AOI by {buffer_percent}% (~{buffer_distance/1000:.1f} km) for {layer_name}")
            aoi_for_crop.geometry = aoi_for_crop.geometry.buffer(buffer_distance)

        before_count = len(gdf)
        gdf_cropped = gpd.sjoin(gdf, aoi_for_crop, predicate='within')
        
        # Drop the index columns added by sjoin
        gdf_cropped = gdf_cropped.drop(columns=[col for col in gdf_cropped.columns if col.startswith('index_')])
        
        after_count = len(gdf_cropped)
        buffer_msg = f" (with {buffer_percent}% buffer)" if buffer_percent > 0 else ""
        logger.info(f"Cropped {layer_name}{buffer_msg}: {before_count} -> {after_count} features")
        
        return gdf_cropped
    
    def process_power_lines(self, gdf: gpd.GeoDataFrame, aoi: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
        """
        Process power lines - clip to extended AOI to preserve network connectivity.
        
        Power lines should extend beyond AOI to show connections to substations
        and maintain network topology.
        """
        # Buffer AOI by 20% to keep power lines that connect to features outside AOI
        aoi_buffered = aoi.copy()
        aoi_buffered.geometry = aoi.geometry.buffer(10000)  # 10km buffer in meters
        
        before_count = len(gdf)
        # Use intersects instead of within to keep lines that cross AOI boundary
        gdf_clipped = gdf[gdf.intersects(aoi_buffered.unary_union)]
        after_count = len(gdf_clipped)
        
        logger.info(f"Clipped power_lines with 10km buffer: {before_count} -> {after_count} features")
        logger.info("Power lines preserved beyond AOI for network connectivity")
        
        return gdf_clipped
    
    def validate_layer(self, gdf: gpd.GeoDataFrame, layer_name: str) -> Dict[str, any]:
        """
        Perform basic quality control checks on layer.
        
        Returns:
            Dictionary with QC metrics
        """
        qc = {
            'layer': layer_name,
            'total_features': len(gdf),
            'geometry_valid': gdf.geometry.is_valid.sum(),
            'geometry_invalid': (~gdf.geometry.is_valid).sum(),
            'has_duplicates': gdf.duplicated(subset=['osm_id']).sum() if 'osm_id' in gdf.columns else 0,
            'null_geometries': gdf.geometry.isna().sum()
        }
        
        # Layer-specific checks
        if layer_name == 'power_lines':
            if 'voltage' in gdf.columns:
                qc['voltage_missing'] = gdf['voltage'].isna().sum()
                qc['voltage_unknown'] = (gdf['voltage'] == 'unknown').sum()
        
        if layer_name == 'substations':
            if 'voltage' in gdf.columns:
                qc['voltage_missing'] = gdf['voltage'].isna().sum()
        
        # Log QC results
        logger.info(f"QC - {layer_name}:")
        logger.info(f"  Total features: {qc['total_features']}")
        logger.info(f"  Invalid geometries: {qc['geometry_invalid']}")
        if qc['has_duplicates'] > 0:
            logger.warning(f"  Duplicate OSM IDs: {qc['has_duplicates']}")
        if qc['null_geometries'] > 0:
            logger.warning(f"  Null geometries: {qc['null_geometries']}")
        
        return qc
    
    def process(self) -> Dict[str, gpd.GeoDataFrame]:
        """
        Process all OSM layers.
        
        Returns:
            Dictionary of processed GeoDataFrames
        """
        logger.info("="*80)
        logger.info("OSM Infrastructure Processing")
        logger.info("="*80)
        
        # Load AOI
        aoi = self.load_aoi()
        
        # Process each layer
        processed_layers = {}
        qc_results = []
        
        for layer_name in self.input_files.keys():
            logger.info(f"\nProcessing {layer_name}...")
            
            # Load layer
            gdf = self.load_osm_layer(layer_name)
            if gdf is None:
                continue
            
            # Reproject to target CRS
            gdf = self.reproject_layer(gdf, layer_name)
            
            # Crop to AOI (special handling for power lines and data centers)
            if layer_name == 'power_lines':
                gdf = self.process_power_lines(gdf, aoi)
            elif layer_name == 'data_centers':
                # Buffer by 10% to capture nearby facilities
                gdf = self.crop_to_aoi(gdf, aoi, layer_name, buffer_percent=10.0)
            else:
                gdf = self.crop_to_aoi(gdf, aoi, layer_name)
            
            # Skip if no features remain after cropping
            if len(gdf) == 0:
                logger.warning(f"No features remain in {layer_name} after cropping to AOI")
                continue
            
            # Validate layer
            qc = self.validate_layer(gdf, layer_name)
            qc_results.append(qc)
            
            # Store processed layer
            processed_layers[layer_name] = gdf
        
        # Save all layers to GeoPackage
        if processed_layers:
            logger.info(f"\nSaving {len(processed_layers)} layers to {self.output_path}")
            for layer_name, gdf in processed_layers.items():
                gdf.to_file(self.output_path, layer=layer_name, driver='GPKG')
                logger.info(f"  [ok]  {layer_name}: {len(gdf)} features")
        else:
            logger.error("No layers to save!")
        
        # Print QC summary
        logger.info("\n" + "="*80)
        logger.info("Quality Control Summary")
        logger.info("="*80)
        for qc in qc_results:
            logger.info(f"{qc['layer']:20s}: {qc['total_features']:4d} features, "
                       f"{qc['geometry_invalid']:2d} invalid geometries")
        
        logger.info("\n" + "="*80)
        logger.info("OSM infrastructure processing complete")
        logger.info("="*80)
        logger.info(f"Output: {self.output_path}")
        logger.info("\nIMPORTANT: Manual QC required:")
        logger.info("  - Verify power line topology (connectivity to substations)")
        logger.info("  - Check voltage attribute completeness")
        logger.info("  - Validate critical infrastructure locations")
        
        return processed_layers


if __name__ == "__main__":
    from pathlib import Path
    
    # Project paths
    PROJECT_ROOT = Path(__file__).parent.parent.parent
    RAW_DATA_DIR = PROJECT_ROOT / "data" / "raw"
    AOI_PATH = PROJECT_ROOT / "data" / "aoi_test.geojson"
    OUTPUT_PATH = PROJECT_ROOT / "data" / "processed" / "osm_infrastructure.gpkg"
    
    # Ensure output directory exists
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    
    # Initialize processor
    processor = OSMInfrastructureProcessor(
        raw_data_dir=RAW_DATA_DIR,
        aoi_path=AOI_PATH,
        output_path=OUTPUT_PATH
    )
    
    # Run processing
    results = processor.process()
    
    # Report
    print("\n" + "="*80)
    print("Processing complete!")
    print("="*80)
    print(f"Processed {len(results)} layers")
    print(f"Output: {OUTPUT_PATH}")
