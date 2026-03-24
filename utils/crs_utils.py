# -*- coding: utf-8 -*-
"""
Shared CRS / UTM utilities.

Consolidates duplicate implementations from step1_project_setup,
step2_claim_layout, and claims_order_widget.
"""
import math
from typing import Optional

from qgis.core import (
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsProject,
    QgsRectangle,
)


def extent_to_wgs84(extent: 'QgsRectangle', map_crs: 'QgsCoordinateReferenceSystem') -> Optional['QgsRectangle']:
    """Transform a canvas extent to WGS84, clamped to valid bounds.

    Handles the common case where a local CRS (e.g. UTM) has a canvas extent
    that extends beyond the CRS's valid area — for instance when a wide-extent
    layer like a national basemap is loaded.

    Returns the WGS84 extent, or None if the transform fails entirely.
    """
    if map_crs.authid() == 'EPSG:4326':
        return QgsRectangle(extent)

    wgs84 = QgsCoordinateReferenceSystem('EPSG:4326')
    transform = QgsCoordinateTransform(map_crs, wgs84, QgsProject.instance())

    # Clamp the source extent to the CRS's valid area of use first
    crs_bounds = map_crs.bounds()  # Returns QgsRectangle in WGS84 degrees
    if crs_bounds and not crs_bounds.isEmpty():
        # Transform CRS bounds into the map CRS to clamp the extent
        reverse = QgsCoordinateTransform(wgs84, map_crs, QgsProject.instance())
        try:
            crs_bounds_in_map = reverse.transformBoundingBox(crs_bounds)
            extent = extent.intersect(crs_bounds_in_map)
            if extent.isEmpty():
                return None
        except Exception:
            pass  # If reverse transform fails, try forward anyway

    try:
        result = transform.transformBoundingBox(extent)
    except Exception:
        return None

    # Clamp to valid WGS84 bounds as a safety net
    result = result.intersect(QgsRectangle(-180, -90, 180, 90))
    if result.isEmpty():
        return None

    return result


def is_utm_crs(epsg: int) -> bool:
    """
    Check whether an EPSG code corresponds to a UTM zone.

    Covers three standard ranges:
      - NAD83 UTM zones:  26901 - 26923
      - WGS84 UTM North:  32601 - 32660
      - WGS84 UTM South:  32701 - 32760

    Args:
        epsg: Integer EPSG code.

    Returns:
        True if the code is a UTM zone.
    """
    if 26901 <= epsg <= 26923:
        return True
    if 32601 <= epsg <= 32660:
        return True
    if 32701 <= epsg <= 32760:
        return True
    return False


def auto_detect_utm(longitude: float, latitude: float,
                    prefer_nad83: bool = True) -> int:
    """
    Return the EPSG code for the UTM zone covering a given lon/lat.

    Args:
        longitude: Longitude in decimal degrees (-180 to 180).
        latitude:  Latitude in decimal degrees (-90 to 90).
        prefer_nad83: When True (default) and the point is in the northern
            hemisphere within NAD83 zone range (1-23), return the NAD83 code
            (269xx) instead of WGS84 (326xx).  NAD83 is more accurate for
            US/Canada mining operations.

    Returns:
        Integer EPSG code for the appropriate UTM zone.
    """
    zone = int(math.floor((longitude + 180) / 6)) + 1

    if latitude >= 0:  # Northern hemisphere
        if prefer_nad83 and 1 <= zone <= 23:
            return 26900 + zone
        return 32600 + zone
    else:  # Southern hemisphere
        return 32700 + zone
