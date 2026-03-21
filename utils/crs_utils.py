# -*- coding: utf-8 -*-
"""
Shared CRS / UTM utilities.

Consolidates duplicate implementations from step1_project_setup,
step2_claim_layout, and claims_order_widget.
"""
import math


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
