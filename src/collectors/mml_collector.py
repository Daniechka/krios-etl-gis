"""MML (Finnish Land Survey) data collector."""

import os
import time
from typing import Optional
import requests
import geopandas as gpd
from owslib.wfs import WebFeatureService

from .base import BaseCollector
from ..config import (
    RAW_DATA_DIR,
    AOI_CENTER_LAT,
    AOI_CENTER_LON,
    AOI_RADIUS_KM,
    CRS_WGS84,
    CRS_FINLAND,
    MIN_PARCEL_SIZE_HA
)


class MMLCollector(BaseCollector):
    """Collector for MML cadastral/land parcel data and DEM."""
    
    # MML INSPIRE WFS endpoint for cadastral parcels (alternative)
    INSPIRE_WFS_URL = "https://inspire-wfs.maanmittauslaitos.fi/inspire-wfs/cp/ows"
    
    # MML OGC API Features endpoint for cadastral data (primary)
    OGC_API_URL = "https://avoin-paikkatieto.maanmittauslaitos.fi/kiinteisto-avoin/simple-features/v3"
    
    # MML OGC API Processes endpoint for DEM tiles
    DEM_API_URL = "https://avoin-paikkatieto.maanmittauslaitos.fi/tiedostopalvelu/ogcproc/v1"
    
    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize MML collector.
        
        Args:
            api_key: Optional API key for WCS/download services.
                    If not provided, reads from MML_API_KEY environment variable.
        """
        super().__init__(RAW_DATA_DIR)
        self.api_key = api_key or os.getenv('MML_API_KEY')
        
        if self.api_key:
            self.logger.info("MML API key found")
        else:
            self.logger.warning("No MML API key - WCS/download services will not work")
    
    def collect_parcels(self, buffer_percent: float = 15.0) -> Optional[gpd.GeoDataFrame]:
        """
        Collect land parcels from MML using OGC API Features.
        
        Args:
            buffer_percent: Percentage to buffer AOI for data collection (default: 15%)
        
        Returns:
            GeoDataFrame with land parcels
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
                
                # Ensure WGS84 for bbox query
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
                
                # Convert back to WGS84 for API query
                aoi_wgs84 = aoi_buffered.to_crs(CRS_WGS84)
            
            bbox = aoi_wgs84.total_bounds
            
            # Format: minx,miny,maxx,maxy (WGS84 lon,lat format)
            bbox_str = f"{bbox[0]},{bbox[1]},{bbox[2]},{bbox[3]}"
            
            self.logger.info(f"Fetching MML parcels via OGC API Features")
            self.logger.info(f"BBOX (WGS84): {bbox_str}")
            
            # Use PalstanSijaintitiedot (Parcels) collection
            collection = "PalstanSijaintitiedot"
            url = f"{self.OGC_API_URL}/collections/{collection}/items"
            
            params = {
                'bbox': bbox_str,
                # Don't specify crs - let API use default (WGS84)
                'limit': 10000  # Max items per request
            }
            
            # Add API key as query parameter
            if self.api_key:
                params['api-key'] = self.api_key
            
            self.logger.info(f"Requesting: {url}")
            response = requests.get(url, params=params, timeout=120)
            response.raise_for_status()
            
            # Check response content
            self.logger.info(f"Response size: {len(response.content)} bytes")
            
            # Save response to temporary file for debugging
            temp_file = self.output_dir / "temp_parcels.geojson"
            with open(temp_file, 'wb') as f:
                f.write(response.content)
            
            # Try to parse as JSON first to see what we got
            try:
                import json
                data = json.loads(response.content)
                feature_count = len(data.get('features', []))
                self.logger.info(f"Received {feature_count} features from API")
                
                if feature_count == 0:
                    self.logger.warning(f"Empty result - bbox might be wrong or no parcels in area")
                    self.logger.warning(f"Response keys: {list(data.keys())}")
                    if 'numberReturned' in data:
                        self.logger.warning(f"numberReturned: {data['numberReturned']}")
                    if 'links' in data:
                        self.logger.warning(f"Links: {[l.get('rel') for l in data.get('links', [])]}")
            except Exception as e:
                self.logger.warning(f"Could not parse response: {e}")
            
            # Read with geopandas
            gdf = gpd.read_file(temp_file)
            temp_file.unlink()
            
            if gdf.empty:
                self.logger.error("No parcels found in AOI - check bbox coordinates")
                return None
            
            # Ensure EPSG:3067
            if gdf.crs != CRS_FINLAND:
                gdf = gdf.to_crs(CRS_FINLAND)
            
            # Calculate area in hectares
            gdf['area_ha'] = gdf.geometry.area / 10000
            
            # Filter by minimum size
            initial_count = len(gdf)
            gdf = gdf[gdf['area_ha'] >= MIN_PARCEL_SIZE_HA]
            
            self.logger.info(f"Collected {len(gdf)} parcels ≥{MIN_PARCEL_SIZE_HA} ha (filtered from {initial_count} total)")
            self.save_geodataframe(gdf, "mml_parcels")
            return gdf
                
        except requests.exceptions.RequestException as e:
            self.logger.error(f"OGC API request failed: {e}")
            return None
        except Exception as e:
            self.logger.error(f"Failed to collect parcel data: {e}")
            return None
    
    def collect_dem(self) -> Optional[str]:
        """
        Collect DEM elevation data (10m resolution) via OGC API Processes.
        
        NOTE: This method is NOT currently working. Multiple request combinations
        have been attempted without success. DEM collection is currently done manually.
        
        See DATA_COLLECTION_NOTES.md for rationale:
        - DEM is very static (infrequent updates)
        - Production systems typically download full DEM once and index locally
        - Manual download + tile index approach is sufficient
        - Automation can be revisited if needed, but not blocking analysis
        
        This method is kept for reference and potential future automation.
        """
        if not self.api_key:
            self.logger.error("DEM collection requires MML API key")
            return None
        
        try:
            # Create AOI and BBox in EPSG:3067
            aoi_wgs84 = self.create_aoi_circle(AOI_CENTER_LAT, AOI_CENTER_LON, AOI_RADIUS_KM, crs=CRS_WGS84)
            aoi_finland = aoi_wgs84.to_crs(CRS_FINLAND)
            bbox = aoi_finland.total_bounds
            
            # --- CRITICAL FIX 1: PROCESS ID ---
            # Since you want to use coordinates (bbox), you MUST use the bbox process.
            # 'korkeusmalli_10m_karttalehti' ONLY accepts map sheet names, not coordinates.
            PROCESS_ID = "korkeusmalli_10m_bbox" 
            BASE_URL = "https://avoin-paikkatieto.maanmittauslaitos.fi/tiedostopalvelu/ogcproc/v1"

            # The endpoint to START a job
            job_url = f"{BASE_URL}/processes/{PROCESS_ID}/jobs?api-key={self.api_key}"

            # --- CRITICAL FIX 2: PAYLOAD STRUCTURE ---
            # MML's BBox processes usually expect 'boundingBoxInput' and 'fileFormatInput'
            payload = {
                "inputs": {
                    "boundingBoxInput": {
                        "bbox": [bbox[0], bbox[1], bbox[2], bbox[3]],
                        "crs": "http://www.opengis.net/def/crs/EPSG/0/3067"
                    },
                    "fileFormatInput": "TIFF"
                }
            }

            self.logger.info(f"Submitting DEM job for process: {PROCESS_ID}")
            response = requests.post(job_url, json=payload, timeout=30)
            
            # Handle 404/Auth errors before accessing job_id
            if response.status_code != 201 and response.status_code != 200:
                self.logger.error(f"Server returned {response.status_code}: {response.text}")
                return None

            job_id = response.json()['id']
            self.logger.info(f"Job created! ID: {job_id}")
            
            status_url = f"{BASE_URL}/jobs/{job_id}?api-key={self.api_key}"

            # 3. Poll for Completion
            max_retries = 30
            attempt = 0
            while attempt < max_retries:
                status_resp = requests.get(status_url, timeout=10).json()
                status = status_resp.get('status')
                
                if status == 'successful':
                    self.logger.info("Job successful!")
                    break
                elif status == 'failed':
                    self.logger.error(f"Job failed on server: {status_resp}")
                    return None
                
                self.logger.info(f"Status: {status}... waiting")
                time.sleep(3)
                attempt += 1

            # 4. Get results
            results_url = f"{BASE_URL}/jobs/{job_id}/results?api-key={self.api_key}"
            results_response = requests.get(results_url, timeout=30)
            results_response.raise_for_status()
            results_data = results_response.json()
            
            # --- CRITICAL FIX 3: DOWNLOAD LINK EXTRACTION ---
            # The result structure is often nested. Let's find the TIFF link robustly.
            download_url = None
            # Look for the TIFF value in the results dictionary
            for key, value in results_data.items():
                if isinstance(value, dict) and value.get('format') == 'TIFF':
                    download_url = value.get('path')
                elif key == 'fileOutput' and isinstance(value, dict): # Common variant
                    download_url = value.get('path')

            if not download_url:
                # Fallback: some versions put it in a list called 'links'
                for link in results_data.get('links', []):
                    if 'geotiff' in link.get('href', '').lower():
                        download_url = link['href']

            if not download_url:
                self.logger.error(f"Could not find download path in results: {results_data}")
                return None
            
            # Final Download
            # Ensure API key is attached if the link is relative or restricted
            if "api-key=" not in download_url:
                separator = "&" if "?" in download_url else "?"
                download_url = f"{download_url}{separator}api-key={self.api_key}"

            self.logger.info(f"Downloading final file...")
            dem_response = requests.get(download_url, timeout=300)
            dem_response.raise_for_status()
            
            output_path = self.output_dir / "mml_dem_10m.tif"
            with open(output_path, 'wb') as f:
                f.write(dem_response.content)
            
            return str(output_path)
                
        except Exception as e:
            self.logger.error(f"Failed to collect DEM: {str(e)}")
            return None



    def collect(self) -> dict:
        """
        Collect all MML datasets.
        
        Returns:
            Dictionary with collected datasets
        """
        self.logger.info("Starting MML data collection...")
        
        # Collect parcels (working)
        parcels = self.collect_parcels()
        
        # DEM collection is not working - log and skip
        self.logger.warning("DEM collection skipped - automated collection not working")
        self.logger.info("Use manual DEM download workflow (see DATA_COLLECTION_NOTES.md)")
        
        return {
            'parcels': parcels,
            'dem': None  # DEM requires manual download
        }


if __name__ == "__main__":
    """Run MML collector when executed as module."""
    import sys
    
    collector = MMLCollector()
    
    # Check if API key is available
    if not collector.api_key:
        print("ERROR: MML_API_KEY environment variable not set")
        print("Get your API key from: https://omatili.maanmittauslaitos.fi")
        print("Then set it: export MML_API_KEY=your_key_here")
        sys.exit(1)
    
    # Run collection
    results = collector.collect()
    
    # Report results
    if results['parcels'] is not None:
        print(f"\n Successfully collected {len(results['parcels'])} parcels")
        print(f"   Saved to: {RAW_DATA_DIR / 'mml_parcels.geojson'}")
    else:
        print("\n Failed to collect parcels")
    
    print("\nNext step: Process parcels for analysis")
    print("  python3 src/processors/mml_parcel_processor.py")
