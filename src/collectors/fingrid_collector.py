"""Fingrid (Finnish TSO) data collector for grid capacity.

CURRENT STATUS: NOT FUNCTIONAL - Data collected manually via browser DevTools.

This collector is a placeholder for future automated collection from Fingrid's
grid capacity map portal. The API endpoint requires authentication/session
management that has not been successfully reverse-engineered yet.

For now, data is manually extracted from:
https://karttapalaute.fingrid.fi/?setlanguage=en&?link=3opMB

See DATA_COLLECTION_NOTES.md for manual extraction process and field definitions.
"""

from typing import Optional
import geopandas as gpd

from .base import BaseCollector
from ..config import RAW_DATA_DIR


class FingridCollector(BaseCollector):
    """
    Placeholder collector for Fingrid grid capacity data.
    
    NOTE: Automated collection is not yet implemented. The Fingrid map portal
    (https://karttapalaute.fingrid.fi) uses a protected API endpoint that requires:
    - Session cookies from the main page
    - Specific layer IDs and parameters
    - Potentially additional authentication tokens
    
    Current workflow:
    1. Manual data extraction via browser DevTools (see DATA_COLLECTION_NOTES.md)
    2. Data saved to: data/raw/fingrid_capacity_headroom.geojson
    3. Processing via: src/processors/fingrid_processor.py
    
    Future work:
    - Reverse-engineer the API authentication mechanism
    - Implement automated scraping with proper session management
    - OR obtain access to Fingrid's official API (if available)
    """
    
    def __init__(self):
        super().__init__(RAW_DATA_DIR)
    
    def collect(self) -> Optional[gpd.GeoDataFrame]:
        """
        Placeholder method for automated collection.
        
        Returns:
            None - automated collection not implemented
        """
        self.logger.warning("Automated Fingrid data collection not yet implemented")
        self.logger.info("Data must be collected manually via browser DevTools")
        self.logger.info("See DATA_COLLECTION_NOTES.md for extraction instructions")
        self.logger.info("Raw data location: data/raw/fingrid_capacity_headroom.geojson")
        return None
