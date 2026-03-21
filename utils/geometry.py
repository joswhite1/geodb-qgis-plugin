# -*- coding: utf-8 -*-
"""
Shared GeoJSON-to-WKT geometry conversion utilities.

Consolidates duplicate implementations from layer_processor, blm_claims_manager,
plss_streaming_manager, sync_manager, and step1_project_setup.
"""
import json
from typing import Optional


def geojson_to_wkt(geojson_dict: dict) -> str:
    """
    Convert a GeoJSON geometry dict to a WKT string.

    Uses OGR (via osgeo/GDAL) for reliable conversion when available,
    with a manual fallback that handles all standard geometry types.

    Args:
        geojson_dict: GeoJSON geometry dict with 'type' and 'coordinates' keys.

    Returns:
        WKT string, or empty string if conversion fails or input is invalid.
    """
    if not geojson_dict or 'type' not in geojson_dict or 'coordinates' not in geojson_dict:
        return ''

    # ------------------------------------------------------------------
    # Try OGR first (most reliable, handles all edge cases)
    # ------------------------------------------------------------------
    try:
        from osgeo import ogr
        geojson_str = json.dumps(geojson_dict)
        ogr_geom = ogr.CreateGeometryFromJson(geojson_str)
        if ogr_geom:
            wkt = ogr_geom.ExportToWkt()
            ogr_geom = None  # Release OGR geometry
            return wkt
    except Exception:
        pass

    # ------------------------------------------------------------------
    # Manual fallback — handles all standard GeoJSON geometry types
    # ------------------------------------------------------------------
    try:
        geom_type = geojson_dict['type'].upper()
        coords = geojson_dict['coordinates']

        if geom_type == 'POINT':
            if len(coords) >= 3:
                return f"POINT Z ({coords[0]} {coords[1]} {coords[2]})"
            return f"POINT ({coords[0]} {coords[1]})"

        elif geom_type == 'LINESTRING':
            points = ', '.join(f"{p[0]} {p[1]}" for p in coords)
            return f"LINESTRING ({points})"

        elif geom_type == 'POLYGON':
            rings = []
            for ring in coords:
                points = ', '.join(f"{p[0]} {p[1]}" for p in ring)
                rings.append(f"({points})")
            return f"POLYGON ({', '.join(rings)})"

        elif geom_type == 'MULTIPOINT':
            points = ', '.join(f"({p[0]} {p[1]})" for p in coords)
            return f"MULTIPOINT ({points})"

        elif geom_type == 'MULTILINESTRING':
            lines = []
            for line in coords:
                line_str = ', '.join(f"{p[0]} {p[1]}" for p in line)
                lines.append(f"({line_str})")
            return f"MULTILINESTRING ({', '.join(lines)})"

        elif geom_type == 'MULTIPOLYGON':
            polygons = []
            for polygon in coords:
                rings = []
                for ring in polygon:
                    points = ', '.join(f"{p[0]} {p[1]}" for p in ring)
                    rings.append(f"({points})")
                polygons.append(f"({', '.join(rings)})")
            return f"MULTIPOLYGON ({', '.join(polygons)})"

    except Exception:
        pass

    return ''
