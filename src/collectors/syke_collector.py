"""SYKE (Finnish Environment Institute) data collector."""

from typing import Optional
import geopandas as gpd
from owslib.wfs import WebFeatureService

from .base import BaseCollector
from ..config import (
    RAW_DATA_DIR,
    AOI_FILE,
    CRS_WGS84,
    CRS_FINLAND,
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
            if not AOI_FILE.exists():
                self.logger.error(f"AOI file not found: {AOI_FILE}")
                return None

            self.logger.info(f"Using AOI from: {AOI_FILE}")
            aoi_original = gpd.read_file(AOI_FILE)
            if aoi_original.crs != CRS_WGS84:
                aoi_original = aoi_original.to_crs(CRS_WGS84)

            # Buffer the AOI for data collection (avoids edge effects)
            aoi_projected = aoi_original.to_crs(CRS_FINLAND)
            bounds = aoi_projected.total_bounds
            diagonal = ((bounds[2] - bounds[0])**2 + (bounds[3] - bounds[1])**2) ** 0.5
            buffer_distance = diagonal * (buffer_percent / 100.0)
            self.logger.info(f"Buffering AOI by {buffer_percent}% (~{buffer_distance/1000:.1f} km)")

            aoi_buffered = aoi_projected.copy()
            aoi_buffered.geometry = aoi_buffered.geometry.buffer(buffer_distance)
            bbox = aoi_buffered.total_bounds  # in EPSG:3067

            self.logger.info(f"Fetching SYKE flood data (return period: 1:{return_period}) for bbox: {bbox}")

            try:
                wfs = WebFeatureService(url=self.WFS_URL, version='2.0.0')

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

                if gdf.crs is None:
                    gdf = gdf.set_crs(CRS_FINLAND)

                # Convert to WGS84 to match AOI CRS for clipping
                gdf = gdf.to_crs(CRS_WGS84)

                # Clip to the original (unbuffered) AOI polygon
                gdf = gpd.clip(gdf, aoi_original)

                if gdf.empty:
                    self.logger.warning("No flood zones remain after clipping to AOI")
                    return gpd.GeoDataFrame()

                self.logger.info(f"Successfully collected {len(gdf)} flood hazard zones")
                self.logger.info(f"Fields: {list(gdf.columns)}")

                self.save_geodataframe(gdf, f"syke_flood_zones_{return_period}")
                return gdf

            except Exception as e:
                self.logger.error(f"FAILED to access SYKE WFS service: {e}")
                return None

        except Exception as e:
            self.logger.error(f"FAILED to collect SYKE flood data - Unexpected error: {e}")
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
    import sys

    collector = SYKECollector()
    print(f"Collecting SYKE flood hazard zones (1:100a) for AOI: {AOI_FILE}")

    gdf = collector.collect()

    if gdf is not None and not gdf.empty:
        print(f"\nSuccessfully collected {len(gdf)} flood hazard zones")
        print(f"Saved to: {RAW_DATA_DIR / 'syke_flood_zones_100a.gpkg'}")
    elif gdf is not None and gdf.empty:
        print("\nNo flood hazard zones found in AOI (area may not be at flood risk)")
    else:
        print("\nFailed to collect flood hazard data")
        sys.exit(1)
