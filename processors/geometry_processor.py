# -*- coding: utf-8 -*-
"""
Geometry processing for coordinate conversion and WKT handling.
"""
from typing import Optional, Tuple
from qgis.core import QgsGeometry, QgsPointXY, QgsCoordinateReferenceSystem

from ..api.exceptions import GeometryError
from ..utils.logger import PluginLogger


class GeometryProcessor:
    """
    Handles geometry conversion between QGIS and API formats.
    API uses WKT format with 6 decimal precision.
    """
    
    COORDINATE_PRECISION = 6
    
    def __init__(self):
        """Initialize geometry processor."""
        self.logger = PluginLogger.get_logger()
    
    def qgs_to_wkt(self, geometry: QgsGeometry, precision: int = COORDINATE_PRECISION) -> str:
        """
        Convert QGIS geometry to WKT with specified precision.

        Args:
            geometry: QgsGeometry object
            precision: Decimal places for coordinates (default: 6)

        Returns:
            WKT string
        """
        if geometry is None or geometry.isNull():
            return ''

        try:
            wkt = geometry.asWkt(precision)
            return wkt
        except Exception as e:
            self.logger.error(f"Failed to convert geometry to WKT: {e}")
            raise GeometryError(f"Failed to convert geometry to WKT: {e}")

    def qgs_to_ewkt(
        self,
        geometry: QgsGeometry,
        srid: int = 4326,
        precision: int = COORDINATE_PRECISION
    ) -> str:
        """
        Convert QGIS geometry to EWKT (Extended WKT) with SRID prefix.

        Args:
            geometry: QgsGeometry object
            srid: Spatial Reference ID (default: 4326 for WGS84)
            precision: Decimal places for coordinates (default: 6)

        Returns:
            EWKT string in format "SRID=4326;MULTIPOLYGON(...)"
        """
        if geometry is None or geometry.isNull():
            return ''

        try:
            wkt = geometry.asWkt(precision)
            return f"SRID={srid};{wkt}"
        except Exception as e:
            self.logger.error(f"Failed to convert geometry to EWKT: {e}")
            raise GeometryError(f"Failed to convert geometry to EWKT: {e}")
    
    def wkt_to_qgs(self, wkt: str) -> Optional[QgsGeometry]:
        """
        Convert WKT to QGIS geometry.
        
        Args:
            wkt: WKT string
            
        Returns:
            QgsGeometry object or None if empty
        """
        if not wkt or wkt.strip() == '':
            return None
        
        try:
            geometry = QgsGeometry.fromWkt(wkt)
            
            if geometry.isNull():
                raise GeometryError("Invalid WKT string")
            
            return geometry
        except Exception as e:
            self.logger.error(f"Failed to parse WKT: {e}")
            raise GeometryError(f"Failed to parse WKT: {e}")
    
    def round_coordinates(
        self,
        geometry: QgsGeometry,
        precision: int = COORDINATE_PRECISION
    ) -> QgsGeometry:
        """
        Round geometry coordinates to specified precision.
        
        Args:
            geometry: QgsGeometry object
            precision: Decimal places
            
        Returns:
            New QgsGeometry with rounded coordinates
        """
        if geometry is None or geometry.isNull():
            return geometry
        
        # Convert to WKT with precision and back
        wkt = self.qgs_to_wkt(geometry, precision)
        return self.wkt_to_qgs(wkt)
    
    def geometries_equal(
        self,
        geom1: QgsGeometry,
        geom2: QgsGeometry,
        precision: int = COORDINATE_PRECISION
    ) -> bool:
        """
        Compare two geometries with coordinate precision tolerance.
        
        Args:
            geom1: First geometry
            geom2: Second geometry
            precision: Comparison precision
            
        Returns:
            True if geometries are equal within precision
        """
        if geom1 is None and geom2 is None:
            return True
        
        if geom1 is None or geom2 is None:
            return False
        
        # Compare WKT representations with same precision
        wkt1 = self.qgs_to_wkt(geom1, precision)
        wkt2 = self.qgs_to_wkt(geom2, precision)
        
        return wkt1 == wkt2
    
    def get_centroid(self, geometry: QgsGeometry) -> Optional[Tuple[float, float]]:
        """
        Get geometry centroid coordinates.
        
        Args:
            geometry: QgsGeometry object
            
        Returns:
            Tuple of (lon, lat) or None
        """
        if geometry is None or geometry.isNull():
            return None
        
        try:
            centroid = geometry.centroid()
            point = centroid.asPoint()
            return (point.x(), point.y())
        except Exception as e:
            self.logger.error(f"Failed to get centroid: {e}")
            return None