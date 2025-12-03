# -*- coding: utf-8 -*-
"""
Low-level synchronization between API and QGIS layers.
"""
import json
import hashlib
from typing import Dict, Any, List, Optional, Callable, Tuple
from datetime import datetime
from qgis.core import QgsProject, QgsField
from qgis.PyQt.QtCore import QVariant

from ..processors.geometry_processor import GeometryProcessor
from ..processors.field_processor import FieldProcessor
from ..processors.layer_processor import LayerProcessor
from ..models.schemas import get_schema, ModelSchema, FieldType, GeometryType
from ..utils.config import Config
from ..utils.logger import PluginLogger


class SyncManager:
    """
    Manages low-level synchronization between API data and QGIS layers.
    Handles change tracking, conflict resolution, and bidirectional sync.
    """

    # QGIS project variable section for sync metadata
    SYNC_VAR_SECTION = "geodb_sync"

    # Fields to exclude from change comparison (read-only server fields)
    EXCLUDE_FROM_COMPARISON = {
        # Timestamp/audit fields
        'created_at', 'updated_at', 'date_created', 'last_edited',
        'created_by', 'updated_by', 'last_edited_by',
        # LandHolding-specific fields
        'serial_link', 'current_retain', 'retain_fiscal_year',
        'current_retain_status', 'retain_records', 'documents', 'images',
        'image_urls', 'document_urls',
        # QGIS internal
        'fid',
        # Computed/display fields from API that aren't editable
        'status_display', 'natural_key', 'coordinate_system_metadata',
        'hole_count', 'total_meters_drilled', 'mark_deleted',
        'date_marked_deleted',
        # Project reference (complex nested object, not editable)
        'project',
        # DrillCollar/PointSample multi-format coordinate fields (computed by API, not stored)
        # Original input units (computed from EPSG)
        'xy_units',
        # Project CRS coordinates (transformed)
        'crs_easting', 'crs_northing', 'crs_elevation', 'crs_epsg',
        'crs_xy_units', 'crs_elevation_units',
        # Proj4 local grid coordinates (transformed for Blender)
        'proj4_easting', 'proj4_northing', 'proj4_elevation', 'proj4_string',
        'proj4_xy_units', 'proj4_elevation_units',
        # DrillSample xyz coordinates (computed from drill trace)
        'xyz_from', 'xyz_to', 'xyz_from_wgs84', 'xyz_to_wgs84',
        # DrillLithology/DrillAlteration/DrillMineralization/DrillStructure xyz fields
        'xyz_at',
        # DrillPad-specific fields (handled specially for geometry generation)
        'location', 'polygon',
        # Assay data (complex nested object with element values)
        'assay',
        # Note: Merged assay element fields (Au_ppm, Cu_ppb, etc.) are excluded
        # dynamically in _compute_feature_hash() by checking for _ppm/_ppb/_pct/_%
    }

    def __init__(self, config: Config):
        """
        Initialize sync manager.

        Args:
            config: Configuration instance
        """
        self.config = config
        self.logger = PluginLogger.get_logger()

        # Initialize processors
        self.geometry_processor = GeometryProcessor()
        self.field_processor = FieldProcessor()
        self.layer_processor = LayerProcessor(config)

        # Track sync metadata
        self.sync_metadata: Dict[str, Dict[str, Any]] = {}

        # In-memory cache of server snapshots (keyed by model_name -> id -> hash)
        self._server_snapshots: Dict[str, Dict[int, str]] = {}

    def _compute_feature_hash(self, feature_data: Dict[str, Any]) -> str:
        """
        Compute a hash of feature data for change detection.

        Only includes fields that are relevant for comparison (excludes
        read-only server fields that change on every save).

        Args:
            feature_data: Feature dictionary

        Returns:
            MD5 hash string
        """
        # Create a normalized dict for hashing
        hash_data = {}

        for key, value in feature_data.items():
            # Skip excluded fields
            if key in self.EXCLUDE_FROM_COMPARISON:
                continue

            # Skip dynamic image/document columns (read-only display fields)
            if key.startswith('image_') or key.startswith('document_'):
                continue

            # Skip merged assay element fields (read-only, format: Element_units)
            # Units: ppm, ppb, pct (percent), opt (ounces per ton)
            # e.g., Au_ppm, Cu_ppb, Fe_pct, Ag_opt
            if key.endswith(('_ppm', '_ppb', '_pct', '_opt')):
                continue

            # Normalize the value for consistent hashing
            normalized = self._normalize_value_for_hash(value)
            hash_data[key] = normalized

        # Sort keys for consistent ordering
        sorted_json = json.dumps(hash_data, sort_keys=True, default=str)
        return hashlib.md5(sorted_json.encode('utf-8')).hexdigest()

    def _normalize_value_for_hash(self, value: Any) -> Any:
        """
        Normalize a value for consistent hashing.

        Handles special cases like geometry strings, None vs NULL, booleans,
        timestamps, QVariant types, and natural key objects.
        """
        import ast
        import re
        from qgis.PyQt.QtCore import QVariant

        # Handle QVariant from QGIS - convert to Python type first
        if isinstance(value, QVariant):
            if value.isNull():
                return None
            # Convert to Python type
            value = value.value() if hasattr(value, 'value') else value

        if value is None or value == '' or str(value) == 'NULL':
            return None

        # Normalize booleans to lowercase JSON strings
        if isinstance(value, bool):
            return 'true' if value else 'false'

        # Handle GeoJSON geometry dicts - convert to WKT for consistent hashing
        # API returns geometry as GeoJSON: {"type": "MultiPolygon", "coordinates": [...]}
        # QGIS returns geometry as EWKT: "SRID=4326;MULTIPOLYGON(...)"
        # We normalize both to uppercase WKT for comparison
        if isinstance(value, dict) and 'type' in value and 'coordinates' in value:
            wkt = self._geojson_dict_to_wkt(value)
            if wkt:
                # Round and normalize the WKT
                wkt = self._round_coordinates_in_wkt(wkt)
                wkt = re.sub(r'\(\s+', '(', wkt)
                wkt = re.sub(r'\s+\)', ')', wkt)
                wkt = re.sub(r'\s+', ' ', wkt)
                return wkt.upper().strip()
            # If conversion fails, fall through to dict handling below

        # For geometry strings (WKT/EWKT), normalize to uppercase WKT
        if isinstance(value, str) and (
            value.upper().startswith('SRID=') or
            value.upper().startswith(('POINT', 'LINESTRING', 'POLYGON', 'MULTI'))
        ):
            # Strip SRID prefix if present
            if value.upper().startswith('SRID='):
                semicolon_idx = value.find(';')
                if semicolon_idx != -1:
                    value = value[semicolon_idx + 1:].strip()

            # Round coordinates to 6 decimal places
            value = self._round_coordinates_in_wkt(value)

            # Normalize whitespace and case
            value = re.sub(r'\(\s+', '(', value)  # Remove space after (
            value = re.sub(r'\s+\)', ')', value)  # Remove space before )
            value = re.sub(r'\s+', ' ', value)    # Normalize multiple spaces
            return value.upper().strip()

        # Normalize date/datetime values
        # Handle QDate objects from QGIS
        if hasattr(value, 'toString') and hasattr(value, 'year'):
            # QDate or QDateTime object
            if hasattr(value, 'time'):
                # QDateTime
                return value.toString('yyyy-MM-dd HH:mm:ss')
            else:
                # QDate
                return value.toString('yyyy-MM-dd')

        # Normalize ISO datetime strings
        if isinstance(value, str) and ('T' in value and ('Z' in value or '+' in value or value.count(':') >= 2)):
            try:
                # Try parsing as ISO datetime
                dt_str = value.replace('Z', '+00:00')
                # Simple fallback if dateutil not available
                try:
                    from dateutil import parser as date_parser
                    dt = date_parser.isoparse(dt_str)
                except ImportError:
                    # Fallback to datetime.fromisoformat (Python 3.7+)
                    dt = datetime.fromisoformat(dt_str)
                # Return in consistent format (strip microseconds)
                return dt.strftime('%Y-%m-%d %H:%M:%S')
            except:
                pass

        # Normalize date-only strings (YYYY-MM-DD)
        if isinstance(value, str) and len(value) == 10 and value.count('-') == 2:
            try:
                # Validate it's a valid date
                parts = value.split('-')
                if len(parts) == 3 and all(p.isdigit() for p in parts):
                    year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
                    if 1 <= month <= 12 and 1 <= day <= 31 and year >= 1900:
                        # Return normalized date string
                        return f"{year:04d}-{month:02d}-{day:02d}"
            except:
                pass

        # Try to parse string representations of dicts/lists back to objects
        if isinstance(value, str) and (value.startswith('{') or value.startswith('[')):
            try:
                # Try JSON first
                parsed = json.loads(value)
                value = parsed
            except json.JSONDecodeError:
                try:
                    # Try Python literal_eval for {'key': 'value'} format
                    parsed = ast.literal_eval(value)
                    if isinstance(parsed, (dict, list)):
                        value = parsed
                except (ValueError, SyntaxError):
                    pass

        # Convert dicts/lists to sorted JSON strings
        if isinstance(value, (dict, list)):
            return json.dumps(value, sort_keys=True, default=str)

        # Normalize numeric types for consistent comparison
        # Handle integers (could be stored as int or string)
        if isinstance(value, int) and not isinstance(value, bool):
            return value  # Keep as int

        # Round floats to avoid precision issues
        if isinstance(value, float):
            return round(value, 6)

        # Try to convert string representations of numbers
        if isinstance(value, str):
            stripped = value.strip()
            # Try integer conversion
            try:
                if stripped.isdigit() or (stripped.startswith('-') and stripped[1:].isdigit()):
                    return int(stripped)
            except (ValueError, IndexError):
                pass
            # Try float conversion (but only if it looks like a float)
            if '.' in stripped:
                try:
                    float_val = float(stripped)
                    return round(float_val, 6)
                except ValueError:
                    pass

        return value

    def _round_coordinates_in_wkt(self, wkt_string: str) -> str:
        """
        Round all coordinate values in WKT string to 6 decimal places.

        Args:
            wkt_string: WKT geometry string

        Returns:
            WKT string with rounded coordinates (always 6 decimal places)
        """
        import re

        def round_match(match):
            """Round a matched number to exactly 6 decimals for consistency."""
            num = float(match.group(0))
            # Always use exactly 6 decimal places for consistent comparison
            return f"{num:.6f}"

        # Match floating point numbers (including negative and scientific notation)
        pattern = r'-?\d+\.?\d*(?:[eE][+-]?\d+)?'
        return re.sub(pattern, round_match, wkt_string)

    def _geojson_dict_to_wkt(self, geojson: Dict[str, Any]) -> Optional[str]:
        """
        Convert a GeoJSON geometry dict to WKT string.

        Uses OGR for reliable conversion, with fallback to manual conversion.

        Args:
            geojson: GeoJSON geometry dict with 'type' and 'coordinates'

        Returns:
            WKT string or None if conversion fails
        """
        if not geojson or 'type' not in geojson or 'coordinates' not in geojson:
            return None

        try:
            # Try OGR first (most reliable)
            from osgeo import ogr
            geojson_str = json.dumps(geojson)
            ogr_geom = ogr.CreateGeometryFromJson(geojson_str)
            if ogr_geom:
                wkt = ogr_geom.ExportToWkt()
                ogr_geom = None  # Release OGR geometry
                return wkt
        except Exception:
            pass

        # Fallback: manual conversion for common geometry types
        try:
            geom_type = geojson['type'].upper()
            coords = geojson['coordinates']

            if geom_type == 'POINT':
                if len(coords) >= 3:
                    return f"POINT Z ({coords[0]} {coords[1]} {coords[2]})"
                return f"POINT ({coords[0]} {coords[1]})"

            elif geom_type == 'MULTIPOLYGON':
                polygons = []
                for polygon in coords:
                    rings = []
                    for ring in polygon:
                        points = ', '.join(f"{p[0]} {p[1]}" for p in ring)
                        rings.append(f"({points})")
                    polygons.append(f"({', '.join(rings)})")
                return f"MULTIPOLYGON ({', '.join(polygons)})"

            elif geom_type == 'POLYGON':
                rings = []
                for ring in coords:
                    points = ', '.join(f"{p[0]} {p[1]}" for p in ring)
                    rings.append(f"({points})")
                return f"POLYGON ({', '.join(rings)})"

            elif geom_type == 'LINESTRING':
                points = ', '.join(f"{p[0]} {p[1]}" for p in coords)
                return f"LINESTRING ({points})"

            elif geom_type == 'MULTIPOINT':
                points = ', '.join(f"({p[0]} {p[1]})" for p in coords)
                return f"MULTIPOINT ({points})"

        except Exception as e:
            self.logger.debug(f"Manual GeoJSON to WKT conversion failed: {e}")

        return None

    def _convert_geometry_to_ewkt(self, geom_value: Any, epsg_code: int) -> Optional[str]:
        """
        Convert geometry from various formats to EWKT string.

        This method handles:
        - GeoJSON dict from API
        - WKT/EWKT strings
        - Already parsed EWKT strings (passed through)

        Args:
            geom_value: Geometry in any supported format
            epsg_code: EPSG code to use for SRID prefix

        Returns:
            EWKT string (e.g., "SRID=4326;MULTIPOLYGON(...)") or None if conversion fails
        """
        if not geom_value:
            return None

        # If it's already an EWKT string, return as-is
        if isinstance(geom_value, str):
            if geom_value.upper().startswith('SRID='):
                return geom_value
            # If it's WKT without SRID, add the SRID prefix
            if geom_value.upper().startswith(('POINT', 'LINESTRING', 'POLYGON', 'MULTI')):
                return f"SRID={epsg_code};{geom_value}"

        # If it's a GeoJSON dict, convert to WKT first then add SRID
        if isinstance(geom_value, dict) and 'type' in geom_value and 'coordinates' in geom_value:
            wkt = self._geojson_dict_to_wkt(geom_value)
            if wkt:
                return f"SRID={epsg_code};{wkt}"

        return None

    def _get_max_document_counts(self, features: List[Dict]) -> Dict[str, int]:
        """
        Scan features to find max number of images and documents.

        Args:
            features: List of feature dictionaries from API

        Returns:
            Dict with 'images' and 'documents' counts
        """
        max_images = 0
        max_docs = 0
        for feature in features:
            images = feature.get('images', [])
            docs = feature.get('documents', [])
            max_images = max(max_images, len(images))
            max_docs = max(max_docs, len(docs))
        return {'images': max_images, 'documents': max_docs}

    def _collect_all_assay_elements(self, features: List[Dict]) -> set:
        """
        Scan all features to collect unique assay element/unit combinations.

        This ensures we create fields for all elements even if the first
        feature has null assay data.

        Args:
            features: List of feature dictionaries from API

        Returns:
            Set of (element, units) tuples (e.g., {('Au', 'ppm'), ('Cu', 'ppm')})
        """
        elements = set()
        for feature in features:
            assay_data = feature.get('assay')
            if isinstance(assay_data, dict) and assay_data.get('merged'):
                for elem in assay_data.get('elements', []):
                    element_symbol = elem.get('element', '')
                    units = elem.get('units', 'ppm')
                    if element_symbol:
                        elements.add((element_symbol, units))
        return elements

    def _build_drillsample_geometry(self, feature_data: Dict) -> Optional[str]:
        """
        Build LineStringZ WKT geometry from xyz coordinates.

        DrillSample API returns desurveyed 3D coordinates for sample intervals:
        - xyz_from_wgs84 / xyz_to_wgs84: [lon, lat, elevation] in WGS84 (required for QGIS)
        - xyz_from / xyz_to: [x, y, z] in local grid (for Blender - NOT used here)

        Only uses WGS84 coordinates. Local grid coordinates would display incorrectly
        in QGIS (meters interpreted as degrees = massive spans).

        Args:
            feature_data: Feature dictionary from API

        Returns:
            WKT LineStringZ string or None if WGS84 coordinates not available
        """
        # Only use WGS84 coordinates - local grid coords display incorrectly in QGIS
        xyz_from = feature_data.get('xyz_from_wgs84')
        xyz_to = feature_data.get('xyz_to_wgs84')

        if not xyz_from or not xyz_to:
            # Log diagnostic info
            sample_name = feature_data.get('name', 'unknown')
            local_from = feature_data.get('xyz_from')
            local_to = feature_data.get('xyz_to')
            self.logger.warning(
                f"DrillSample '{sample_name}' missing WGS84 coords: "
                f"xyz_from_wgs84={xyz_from}, xyz_to_wgs84={xyz_to}, "
                f"has local xyz_from={local_from is not None}, xyz_to={local_to is not None}"
            )
            return None

        try:
            # WGS84 coordinates are [lon, lat, elevation]
            lon1, lat1, z1 = xyz_from[0], xyz_from[1], xyz_from[2]
            lon2, lat2, z2 = xyz_to[0], xyz_to[1], xyz_to[2]

            # Sanity check - WGS84 lon should be -180 to 180, lat -90 to 90
            if not (-180 <= lon1 <= 180 and -90 <= lat1 <= 90):
                self.logger.warning(
                    f"DrillSample '{feature_data.get('name')}' has invalid WGS84 coords: "
                    f"lon={lon1}, lat={lat1} - may be local grid in wrong field"
                )
                return None

            # Build WKT LineStringZ
            wkt = f"LINESTRING Z ({lon1} {lat1} {z1}, {lon2} {lat2} {z2})"
            return wkt

        except (IndexError, TypeError) as e:
            self.logger.warning(f"Failed to build DrillSample geometry: {e}")
            return None

    def _create_dynamic_image_document_fields(self, max_images: int, max_docs: int) -> List[Dict]:
        """
        Create field definitions for image_1, image_2, ..., document_1, ...

        Args:
            max_images: Maximum number of images across all features
            max_docs: Maximum number of documents across all features

        Returns:
            List of field definition dicts
        """
        fields = []
        for i in range(1, max_images + 1):
            fields.append({
                'name': f'image_{i}',
                'type': 'string',
                'length': 500,
                'readonly': True,
                'description': f'Image {i} URL'
            })
        for i in range(1, max_docs + 1):
            fields.append({
                'name': f'document_{i}',
                'type': 'string',
                'length': 500,
                'readonly': True,
                'description': f'Document {i} URL'
            })
        return fields

    def _extract_image_document_attributes(
        self,
        feature_data: Dict,
        max_images: int,
        max_docs: int
    ) -> Dict:
        """
        Extract image/document URLs into individual columns.

        Args:
            feature_data: Feature dictionary from API
            max_images: Number of image columns to create
            max_docs: Number of document columns to create

        Returns:
            Dict with image_1, image_2, document_1, etc. keys
        """
        attrs = {}

        images = feature_data.get('images', [])
        for i in range(max_images):
            if i < len(images):
                # Extract full URL from image object
                # FlexibleReferenceField returns 'url', not 'image_url'
                img = images[i]
                url = img.get('url', '')

                # DEBUG: Log what we're getting from the API
                if not url:
                    self.logger.warning(f"Empty URL for image {i+1} in feature {feature_data.get('id', 'unknown')}")
                    self.logger.debug(f"Image object keys: {list(img.keys())}")
                else:
                    # Log first 100 chars of URL
                    self.logger.info(f"image_{i+1} URL: {url[:100]}{'...' if len(url) > 100 else ''}")

                attrs[f'image_{i+1}'] = url
            else:
                attrs[f'image_{i+1}'] = None

        docs = feature_data.get('documents', [])
        for i in range(max_docs):
            if i < len(docs):
                # FlexibleReferenceField returns 'url', not 'document_url'
                doc = docs[i]
                url = doc.get('url', '')

                # DEBUG: Log what we're getting from the API
                if not url:
                    self.logger.warning(f"Empty URL for document {i+1} in feature {feature_data.get('id', 'unknown')}")
                    self.logger.debug(f"Document object keys: {list(doc.keys())}")
                else:
                    # Log first 100 chars of URL
                    self.logger.info(f"document_{i+1} URL: {url[:100]}{'...' if len(url) > 100 else ''}")

                attrs[f'document_{i+1}'] = url
            else:
                attrs[f'document_{i+1}'] = None

        return attrs

    def _extract_flattened_assay_attributes(self, assay_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract flattened assay element values from merged assay data.

        Converts nested assay structure:
            {'elements': [{'element': 'Au', 'value': 0.5, 'units': 'ppm'}, ...], 'merged': True}
        Into flat attributes:
            {'Au_ppm': 0.5, 'Cu_ppm': 1.2, ...}

        Args:
            assay_data: Merged assay dictionary from API

        Returns:
            Dict with {element}_{units} keys and float values
        """
        attrs = {}

        elements = assay_data.get('elements', [])
        for elem in elements:
            element_symbol = elem.get('element', '')
            units = elem.get('units', 'ppm')
            value = elem.get('value')

            if element_symbol and value is not None:
                attrs[f'{element_symbol}_{units}'] = float(value)

        return attrs

    def _configure_landholding_widgets(self, layer, features: List[Dict[str, Any]]):
        """
        Configure special widgets for LandHolding fields.

        Sets up:
        - project: Hidden (auto-set from active project context during sync)
        - land_status: Dropdown with available options
        - retain_records: Read-only display of current retain status
        - image_* and document_*: Read-only URL fields
        - Hidden: Non-essential fields for new feature form

        Args:
            layer: The LandHolding layer
            features: List of feature data from API
        """
        import json
        from qgis.core import QgsEditorWidgetSetup

        # Fields to show in the new feature form
        # Note: 'project' is NOT shown - it's auto-set from active project during sync
        # Only 'name' is required by API; other fields are optional but useful
        # Geometry is drawn by user (polygon), not entered as coordinates
        visible_fields = {
            'name', 'serial_number', 'claim_type', 'land_status',
            'county', 'state', 'date_staked', 'comments'
        }

        # Hidden fields (auto-populated):
        # - project: from active project context
        # - geometry: drawn by user on map

        # 2. Configure 'land_status' field - dropdown with available options
        if layer.fields().indexOf('land_status') >= 0:
            # Extract unique land_status options from features
            land_status_options = set()
            for feature in features:
                land_status = feature.get('land_status')
                if land_status and isinstance(land_status, dict):
                    name = land_status.get('name')
                    if name:
                        land_status_options.add(name)

            if land_status_options:
                # Create a value map for the dropdown
                # Map display name to the JSON format the API expects
                value_map = []
                company_cache = {}  # Cache company name for each land_status

                # Build the value map and cache
                for feature in features:
                    land_status = feature.get('land_status')
                    if land_status and isinstance(land_status, dict):
                        name = land_status.get('name')
                        company = land_status.get('company')
                        if name and company and name not in company_cache:
                            company_cache[name] = company
                            # Store as JSON string that can be sent back to API
                            json_value = json.dumps({'name': name, 'company': company})
                            value_map.append({name: json_value})

                # Configure as ValueMap (dropdown)
                self.layer_processor.configure_field_widget(
                    layer, 'land_status', 'ValueMap',
                    {'map': value_map}
                )
                self.logger.info(f"Configured 'land_status' dropdown with {len(value_map)} options")

        # 3. Configure 'retain_records' field - read-only, display current status
        if layer.fields().indexOf('retain_records') >= 0:
            self.layer_processor.set_field_readonly(layer, 'retain_records', readonly=True)
            self.logger.info("Configured 'retain_records' as read-only")

        # 4. Configure 'current_retain_status' - read-only
        if layer.fields().indexOf('current_retain_status') >= 0:
            self.layer_processor.set_field_readonly(layer, 'current_retain_status', readonly=True)

        # 5. Configure image and document fields as clickable URLs
        # and hide non-visible fields
        for field in layer.fields():
            field_name = field.name()
            if field_name.startswith('image_') or field_name.startswith('document_'):
                # Set as clickable URL instead of plain read-only text
                self.layer_processor.set_field_as_url(layer, field_name)
            elif field_name not in visible_fields:
                # Hide non-essential fields (includes 'project')
                field_idx = layer.fields().indexOf(field_name)
                if field_idx >= 0:
                    layer.setEditorWidgetSetup(
                        field_idx,
                        QgsEditorWidgetSetup('Hidden', {})
                    )

        self.logger.info(
            f"Configured LandHolding form: showing {len(visible_fields)} fields, "
            f"hiding {layer.fields().count() - len(visible_fields)} fields"
        )

    def _configure_drillcollar_widgets(
        self,
        layer,
        features: List[Dict[str, Any]],
        api_client=None,
        project_id: Optional[int] = None
    ):
        """
        Configure special widgets for DrillCollar fields.

        Sets up:
        - project: Hidden (auto-set from active project context during sync)
        - pad: Dropdown populated from DrillPads in the project
        - hole_type: Dropdown (DD, RC, DC, PC, SN, TR, OT)
        - hole_status: Dropdown (CP, AB, PL, IP)
        - hole_size: Dropdown (AQ, BQ, NQ, NQ2, HQ, HQ3, PQ, OT)
        - length_units: Dropdown (M, FT)
        - Hidden: All other fields except essential ones for new feature form

        Args:
            layer: The DrillCollar layer
            features: List of feature data from API
            api_client: Optional APIClient for fetching drill pads
            project_id: Optional project ID for fetching drill pads
        """
        from qgis.core import QgsEditorWidgetSetup

        # Fields to show in the new feature form (minimal set for creating new collars)
        # Note: 'project' is NOT shown - it's auto-set from active project during sync
        # latitude/longitude are derived from geometry, elevation must be entered manually
        visible_fields = {
            'name', 'pad', 'hole_type', 'hole_status', 'hole_size',
            'total_depth', 'length_units', 'azimuth', 'dip', 'elevation'
        }

        # Hidden fields (auto-populated):
        # - project: from active project context
        # - latitude/longitude: extracted from geometry on push

        # 2. Configure 'hole_type' field - dropdown
        # Values from geodb.io API: drill_hole_types in drill_models.py
        if layer.fields().indexOf('hole_type') >= 0:
            hole_type_options = [
                {'Diamond Core': 'DD'},
                {'Reverse Circulation': 'RC'},
                {'Direct Circulation': 'DC'},
                {'Percussion': 'PC'},
                {'Sonic': 'SN'},
                {'Trench': 'TR'},
                {'Other': 'OT'},
            ]
            self.layer_processor.configure_field_widget(
                layer, 'hole_type', 'ValueMap',
                {'map': hole_type_options}
            )
            self.logger.info("Configured 'hole_type' dropdown")

        # 3. Configure 'hole_status' field - dropdown
        # Values from geodb.io API: drill_hole_status in drill_models.py
        if layer.fields().indexOf('hole_status') >= 0:
            hole_status_options = [
                {'Completed': 'CP'},
                {'Abandoned': 'AB'},
                {'Planned': 'PL'},
                {'In Progress': 'IP'},
            ]
            self.layer_processor.configure_field_widget(
                layer, 'hole_status', 'ValueMap',
                {'map': hole_status_options}
            )
            self.logger.info("Configured 'hole_status' dropdown")

        # 4. Configure 'hole_size' field - dropdown
        # Values from geodb.io API: drill_hole_size in model_variables.py
        if layer.fields().indexOf('hole_size') >= 0:
            hole_size_options = [
                {'AQ': 'AQ'},
                {'BQ': 'BQ'},
                {'NQ': 'NQ'},
                {'NQ2': 'NQ2'},
                {'HQ': 'HQ'},
                {'HQ3': 'HQ3'},
                {'PQ': 'PQ'},
                {'Other': 'OT'},
            ]
            self.layer_processor.configure_field_widget(
                layer, 'hole_size', 'ValueMap',
                {'map': hole_size_options}
            )
            self.logger.info("Configured 'hole_size' dropdown")

        # 5. Configure 'length_units' field - dropdown
        if layer.fields().indexOf('length_units') >= 0:
            units_options = [
                {'Meters': 'M'},
                {'Feet': 'FT'},
            ]
            self.layer_processor.configure_field_widget(
                layer, 'length_units', 'ValueMap',
                {'map': units_options}
            )
            self.logger.info("Configured 'length_units' dropdown")

        # 6. Configure 'pad' field - dropdown populated from DrillPads in project
        if layer.fields().indexOf('pad') >= 0:
            pad_options = [{'(No Pad)': ''}]  # Allow unassigned
            # Try to fetch pads from API
            if api_client and project_id:
                try:
                    drill_pads = api_client.get_drill_pads(project_id)
                    for pad in drill_pads:
                        pad_name = pad.get('name', '')
                        pad_id = pad.get('id', '')
                        if pad_name and pad_id:
                            pad_options.append({pad_name: str(pad_id)})
                    self.logger.info(f"Fetched {len(drill_pads)} drill pads from API")
                except Exception as e:
                    self.logger.warning(f"Failed to fetch drill pads from API: {e}")
            # Fall back to extracting from features if API fetch failed
            if len(pad_options) <= 1:
                pad_options.extend(self._extract_unique_values(features, 'pad'))
            if len(pad_options) > 1:
                self.layer_processor.configure_field_widget(
                    layer, 'pad', 'ValueMap',
                    {'map': pad_options}
                )
                self.logger.info(f"Configured 'pad' dropdown with {len(pad_options)} options")

        # 7. Hide all fields that are not in the visible_fields list
        for field in layer.fields():
            field_name = field.name()
            if field_name not in visible_fields:
                field_idx = layer.fields().indexOf(field_name)
                if field_idx >= 0:
                    layer.setEditorWidgetSetup(
                        field_idx,
                        QgsEditorWidgetSetup('Hidden', {})
                    )

        self.logger.info(
            f"Configured DrillCollar form: showing {len(visible_fields)} fields, "
            f"hiding {layer.fields().count() - len(visible_fields)} fields"
        )

    def _configure_drillpad_widgets(self, layer, features: List[Dict[str, Any]]):
        """
        Configure special widgets for DrillPad fields.

        Sets up:
        - project: Hidden (auto-set from active project context during sync)
        - status: Dropdown (planned, built, historic)
        - access_type: Text input for access method
        - Hidden: Non-essential fields for new feature form

        Args:
            layer: The DrillPad layer
            features: List of feature data from API
        """
        from qgis.core import QgsEditorWidgetSetup

        # Fields to show in the new feature form
        # Note: 'project' is NOT shown - it's auto-set from active project during sync
        # Geometry (polygon) is drawn by user on map
        visible_fields = {
            'name', 'status', 'permit_number', 'constructed_date',
            'access_type', 'disturbance_area', 'notes'
        }

        # 1. Configure 'status' field - dropdown
        if layer.fields().indexOf('status') >= 0:
            status_options = [
                {'Planned': 'planned'},
                {'Built': 'built'},
                {'Historic': 'historic'},
            ]
            self.layer_processor.configure_field_widget(
                layer, 'status', 'ValueMap',
                {'map': status_options}
            )
            self.logger.info("Configured 'status' dropdown for DrillPad")

        # 2. Configure read-only computed fields
        readonly_fields = ['hole_count', 'total_meters_drilled']
        for field_name in readonly_fields:
            if layer.fields().indexOf(field_name) >= 0:
                self.layer_processor.set_field_readonly(layer, field_name, True)

        # 3. Hide all fields that are not in the visible_fields list
        for field in layer.fields():
            field_name = field.name()
            if field_name not in visible_fields:
                field_idx = layer.fields().indexOf(field_name)
                if field_idx >= 0:
                    layer.setEditorWidgetSetup(
                        field_idx,
                        QgsEditorWidgetSetup('Hidden', {})
                    )

        self.logger.info(
            f"Configured DrillPad form: showing {len(visible_fields)} fields, "
            f"hiding {layer.fields().count() - len(visible_fields)} fields"
        )

    def _configure_pointsample_widgets(
        self,
        layer,
        features: List[Dict[str, Any]],
        point_sample_types: Optional[List] = None,
        api_client=None,
        project_id: Optional[int] = None
    ):
        """
        Configure special widgets for PointSample fields.

        Sets up:
        - id: Hidden (server-assigned)
        - project: Hidden (auto-set from active project context during sync)
        - sample_type: Dropdown (SL, RK, OC, DP, ST, BG, OT)
        - ps_type: Dropdown from company-specific PointSampleType
        - lithology: Dropdown from API (all project lithology types)
        - alteration: Dropdown from API (all project alteration types)
        - Hidden: Non-essential fields for new feature form

        Args:
            layer: The PointSample layer
            features: List of feature data from API
            point_sample_types: List of PointSampleType from UserContext
            api_client: Optional APIClient for fetching lithology/alteration types
            project_id: Optional project ID for fetching types from API
        """
        from qgis.core import QgsEditorWidgetSetup

        # Fields to show in the new feature form
        # Note: 'id' and 'project' are NOT shown - id is server-assigned,
        # project is auto-set from active project during sync
        # latitude/longitude are derived from geometry, elevation must be entered manually
        visible_fields = {
            'name', 'sample_type', 'ps_type',
            'lithology', 'alteration', 'elevation', 'date_collected', 'collected_by'
        }

        # Hidden fields (auto-populated):
        # - id: server-assigned on create
        # - project: from active project context
        # - latitude/longitude: extracted from geometry on push

        # 3. Configure 'sample_type' field - dropdown with human-readable labels
        # Values from geodb.io API: pointsample_types in model_variables.py
        if layer.fields().indexOf('sample_type') >= 0:
            sample_type_options = [
                {'Soil': 'SL'},
                {'Rock Chip': 'RK'},
                {'Outcrop': 'OC'},
                {'Dump': 'DP'},
                {'Stream Sediment': 'ST'},
                {'BLEG': 'BG'},
                {'Other': 'OT'},
            ]
            self.layer_processor.configure_field_widget(
                layer, 'sample_type', 'ValueMap',
                {'map': sample_type_options}
            )
            self.logger.info("Configured 'sample_type' dropdown")

        # 4. Configure 'ps_type' field - dropdown from company-specific types
        if layer.fields().indexOf('ps_type') >= 0 and point_sample_types:
            ps_type_options = [
                {pst.name: pst.id} for pst in point_sample_types
            ]
            if ps_type_options:
                self.layer_processor.configure_field_widget(
                    layer, 'ps_type', 'ValueMap',
                    {'map': ps_type_options}
                )
                self.logger.info(f"Configured 'ps_type' dropdown with {len(ps_type_options)} options")

        # 5. Configure 'lithology' field - dropdown from API or features
        if layer.fields().indexOf('lithology') >= 0:
            lithology_options = []
            # Try to fetch from API first (includes all project types)
            self.logger.debug(f"Lithology lookup: api_client={api_client is not None}, project_id={project_id}")
            if api_client and project_id:
                try:
                    lithology_types = api_client.get_lithologies(project_id)
                    lithology_options = [
                        {lt.get('name', ''): str(lt.get('id', ''))}
                        for lt in lithology_types
                        if lt.get('name')
                    ]
                    self.logger.info(f"Fetched {len(lithology_options)} lithology types from API")
                except Exception as e:
                    self.logger.warning(f"Failed to fetch lithology types from API: {e}")
            # Fall back to extracting from features
            if not lithology_options:
                lithology_options = self._extract_unique_values(features, 'lithology')
            if lithology_options:
                self.layer_processor.configure_field_widget(
                    layer, 'lithology', 'ValueMap',
                    {'map': lithology_options}
                )
                self.logger.info(f"Configured 'lithology' dropdown with {len(lithology_options)} options")

        # 6. Configure 'alteration' field - dropdown from API or features
        if layer.fields().indexOf('alteration') >= 0:
            alteration_options = []
            # Try to fetch from API first (includes all project types)
            if api_client and project_id:
                try:
                    alteration_types = api_client.get_alterations(project_id)
                    alteration_options = [
                        {at.get('name', ''): str(at.get('id', ''))}
                        for at in alteration_types
                        if at.get('name')
                    ]
                    self.logger.info(f"Fetched {len(alteration_options)} alteration types from API")
                except Exception as e:
                    self.logger.warning(f"Failed to fetch alteration types from API: {e}")
            # Fall back to extracting from features
            if not alteration_options:
                alteration_options = self._extract_unique_values(features, 'alteration')
            if alteration_options:
                self.layer_processor.configure_field_widget(
                    layer, 'alteration', 'ValueMap',
                    {'map': alteration_options}
                )
                self.logger.info(f"Configured 'alteration' dropdown with {len(alteration_options)} options")

        # 7. Hide all fields that are not in the visible_fields list
        for field in layer.fields():
            field_name = field.name()
            if field_name not in visible_fields:
                field_idx = layer.fields().indexOf(field_name)
                if field_idx >= 0:
                    layer.setEditorWidgetSetup(
                        field_idx,
                        QgsEditorWidgetSetup('Hidden', {})
                    )

        self.logger.info(
            f"Configured PointSample form: showing {len(visible_fields)} fields, "
            f"hiding {layer.fields().count() - len(visible_fields)} fields"
        )

    def _extract_unique_values(
        self,
        features: List[Dict[str, Any]],
        field_name: str
    ) -> List[Dict[str, str]]:
        """
        Extract unique values from features for dropdown configuration.

        Handles both string values and dict values (FK representations).

        Args:
            features: List of feature dictionaries
            field_name: Field to extract values from

        Returns:
            List of {display_name: value} dicts for ValueMap widget
        """
        unique_values = {}
        for feature in features:
            value = feature.get(field_name)
            if value is None:
                continue
            if isinstance(value, dict):
                # FK representation: {'id': 1, 'name': 'Granite'}
                name = value.get('name', '')
                val_id = value.get('id', '')
                if name and name not in unique_values:
                    unique_values[name] = str(val_id) if val_id else name
            elif isinstance(value, str) and value:
                if value not in unique_values:
                    unique_values[value] = value

        return [{name: val} for name, val in sorted(unique_values.items())]

    def _configure_photo_widgets(self, layer):
        """
        Configure Photo layer with camera icon symbology and image popup action.

        Sets up:
        - Camera icon marker symbology
        - Map action to open image in browser when feature is clicked
        - HTML maptip showing thumbnail preview on hover
        - Read-only field configuration

        Args:
            layer: The Photo layer
        """
        from qgis.core import QgsEditorWidgetSetup, QgsAction
        from ..processors.style_processor import StyleProcessor

        # 1. Apply camera icon symbology
        style_processor = StyleProcessor()
        style_processor.apply_photo_style(layer)
        self.logger.info("Applied camera icon symbology to Photo layer")

        # 2. Configure map action to open image URL in browser
        # This creates a clickable action that opens the full image
        action_manager = layer.actions()

        # Clear any existing "View Photo" actions to avoid duplicates
        for action in action_manager.actions():
            if action.name() == 'View Photo':
                action_manager.removeAction(action.id())

        # Create action to open image_url in default browser
        # Works on Windows, macOS, and Linux
        open_image_action = QgsAction(
            QgsAction.ActionType.OpenUrl,  # Opens URL in default browser
            'View Photo',
            '[% "image_url" %]',  # Expression to get the URL
            '',  # Icon path (empty = default)
            False,  # Capture output
            'View full-resolution photo in browser',
            {'Field': 'image_url'},  # Action scope
            ''  # Notification message
        )
        action_manager.addAction(open_image_action)
        self.logger.info("Added 'View Photo' map action")

        # 3. Configure HTML maptip to show thumbnail on hover
        # This creates a tooltip that displays the thumbnail image
        maptip_html = '''
<div style="max-width: 400px; text-align: center;">
    <img src="[% "thumbnail_url" %]" style="max-width: 100%; max-height: 300px; border-radius: 4px;">
    <p style="margin: 8px 0 4px; font-weight: bold;">[% "original_filename" %]</p>
    <p style="margin: 0; color: #666; font-size: 0.9em;">[% "category_display" %]</p>
    [% IF "description" IS NOT NULL AND "description" != '' %]
    <p style="margin: 4px 0 0; font-style: italic;">[% "description" %]</p>
    [% END %]
    <p style="margin: 4px 0 0; color: #888; font-size: 0.8em;">Click to view full image</p>
</div>
'''
        layer.setMapTipTemplate(maptip_html)
        self.logger.info("Configured photo thumbnail maptip")

        # 4. Set default map layer action (triggered on identify/click)
        # Make "View Photo" the default action for this layer
        layer.setCustomProperty('default_action', 'View Photo')

        # 5. Configure read-only fields
        readonly_fields = [
            'id', 'original_filename', 'category_display', 'file_size',
            'image_url', 'thumbnail_url', 'length_units',
            'date_created', 'last_edited', 'created_by', 'last_edited_by'
        ]
        for field_name in readonly_fields:
            self.layer_processor.set_field_readonly(layer, field_name, True)

        # 6. Configure URL fields as external resources (clickable links in form)
        self.layer_processor.set_field_as_url(layer, 'image_url')
        self.layer_processor.set_field_as_url(layer, 'thumbnail_url')

        # 7. Set field aliases for better display
        aliases = {
            'original_filename': 'Filename',
            'category': 'Category',
            'category_display': 'Category Name',
            'description': 'Description',
            'latitude': 'Latitude',
            'longitude': 'Longitude',
            'elevation': 'Elevation (m)',
            'file_size': 'File Size',
            'image_url': 'Image URL',
            'thumbnail_url': 'Thumbnail URL',
            'date_created': 'Date Created',
            'last_edited': 'Last Edited',
            'created_by': 'Created By',
            'last_edited_by': 'Last Edited By',
        }
        for field, alias in aliases.items():
            self.layer_processor.set_field_alias(layer, field, alias)

        # 8. Configure category dropdown
        if layer.fields().indexOf('category') >= 0:
            category_options = [
                {'Drill Core': 'DRL'},
                {'Map': 'MAP'},
                {'Field': 'FLD'},
                {'Aerial': 'AER'},
                {'Geology': 'GEO'},
                {'Sample': 'SMP'},
                {'Equipment': 'EQP'},
                {'Other': 'OTH'},
            ]
            self.layer_processor.configure_field_widget(
                layer, 'category', 'ValueMap',
                {'map': category_options}
            )

        self.logger.info(
            f"Configured Photo layer with camera icon, maptip, and image popup action"
        )

    def _normalize_drillpad_geometry(self, feature: Dict[str, Any]) -> Dict[str, Any]:
        """
        Normalize DrillPad feature geometry for consistent hashing.

        When DrillPad has no polygon geometry but has a location point,
        we generate a 30m square polygon (same as layer_processor does).
        This ensures the snapshot hash matches what gets stored in QGIS.

        Args:
            feature: Feature dict from API

        Returns:
            Modified feature dict with normalized geometry
        """
        import math

        # Check if geometry is empty but location exists
        geometry = feature.get('geometry')
        location = feature.get('location')

        if geometry or not location:
            return feature  # No normalization needed

        # Parse location - could be dict or JSON string
        if isinstance(location, str):
            try:
                import json
                location = json.loads(location)
            except (json.JSONDecodeError, TypeError):
                return feature

        if not isinstance(location, dict):
            return feature

        # Extract point coordinates
        if location.get('type') != 'Point':
            return feature

        coords = location.get('coordinates', [])
        if len(coords) < 2:
            return feature

        lon, lat = coords[0], coords[1]

        # Create 30m square polygon (same logic as layer_processor._point_to_square_polygon)
        meters_per_degree_lat = 111320
        meters_per_degree_lon = 111320 * math.cos(math.radians(lat))
        size_meters = 30

        half_size_lat = (size_meters / 2) / meters_per_degree_lat
        half_size_lon = (size_meters / 2) / meters_per_degree_lon

        # Use 6 decimal precision for consistency with layer_processor
        min_lon = round(lon - half_size_lon, 6)
        max_lon = round(lon + half_size_lon, 6)
        min_lat = round(lat - half_size_lat, 6)
        max_lat = round(lat + half_size_lat, 6)

        # Generate WKT with 6 decimal precision (same as layer_processor)
        wkt = (
            f"POLYGON (("
            f"{min_lon:.6f} {min_lat:.6f}, "
            f"{max_lon:.6f} {min_lat:.6f}, "
            f"{max_lon:.6f} {max_lat:.6f}, "
            f"{min_lon:.6f} {max_lat:.6f}, "
            f"{min_lon:.6f} {min_lat:.6f}))"
        )

        # Return modified feature with generated geometry and cleared location
        normalized = feature.copy()
        normalized['geometry'] = wkt
        normalized['location'] = None
        # polygon field should also be null for generated polygons
        if 'polygon' in normalized:
            normalized['polygon'] = None

        return normalized

    def _store_snapshot(self, model_name: str, features: List[Dict[str, Any]]):
        """
        Store a snapshot of server data for change detection.

        DEPRECATED: Use _store_snapshot_from_layer instead for accurate hashing.
        This method is kept for backwards compatibility but should not be used
        for new code.

        Args:
            model_name: Model name
            features: List of feature dictionaries from API
        """
        try:
            self.logger.info(f"Storing snapshot for {model_name} with {len(features)} features")
            snapshot = {}
            for feature in features:
                feature_id = feature.get('id')
                if feature_id:
                    try:
                        # Normalize DrillPad geometry (convert location point to 30m polygon)
                        # This ensures snapshot matches what gets displayed in QGIS
                        if model_name == 'DrillPad':
                            feature = self._normalize_drillpad_geometry(feature)

                        feature_hash = self._compute_feature_hash(feature)
                        snapshot[feature_id] = feature_hash
                    except Exception as e:
                        self.logger.error(f"Failed to hash feature {feature_id}: {e}")

            self._server_snapshots[model_name] = snapshot

            # Also persist to QGIS project for session recovery
            self._save_snapshot_to_project(model_name, snapshot)

            self.logger.info(f"Stored snapshot for {model_name}: {len(snapshot)} features")
        except Exception as e:
            self.logger.error(f"Failed to store snapshot for {model_name}: {e}", exc_info=True)

    def _store_snapshot_from_layer(
        self,
        model_name: str,
        layer,
        project_name: Optional[str] = None
    ):
        """
        Store a snapshot by reading features directly from the QGIS layer.

        This is more accurate than storing pre-computed data because it captures
        exactly what QGIS stored, including any type conversions or formatting
        changes made by QGIS.

        Args:
            model_name: Model name
            layer: QGIS vector layer to read from
            project_name: Optional project name for layer lookup
        """
        try:
            self.logger.info(f"Building snapshot from layer for {model_name}")
            snapshot = {}

            # Get all features from layer
            all_features = self.layer_processor.get_all_features(layer)

            # Get layer CRS for EPSG
            layer_crs = layer.crs()
            epsg_code = layer_crs.postgisSrid() if layer_crs.isValid() else 4326

            for feature in all_features:
                # Build feature dict the same way get_changed_features does
                feature_dict = {}

                # Add attributes
                for field in layer.fields():
                    field_name = field.name()

                    # Skip dynamic image/document columns (read-only display fields)
                    if field_name.startswith('image_') or field_name.startswith('document_'):
                        continue

                    value = feature.attribute(field_name)
                    feature_dict[field_name] = value

                # Add EPSG to feature
                feature_dict['epsg'] = epsg_code

                # Add geometry in EWKT format (same as get_changed_features)
                geometry = feature.geometry()
                if geometry and not geometry.isNull():
                    ewkt = self.geometry_processor.qgs_to_ewkt(geometry, srid=epsg_code)
                    feature_dict['geometry'] = ewkt

                # For DrillPad: Normalize to match get_changed_features behavior
                if model_name == 'DrillPad':
                    feature_dict['location'] = None
                    if 'polygon' not in feature_dict:
                        feature_dict['polygon'] = None

                # Get feature ID and compute hash
                feature_id = feature_dict.get('id')
                if feature_id:
                    try:
                        feature_hash = self._compute_feature_hash(feature_dict)
                        snapshot[feature_id] = feature_hash
                    except Exception as e:
                        self.logger.error(f"Failed to hash feature {feature_id}: {e}")

            self._server_snapshots[model_name] = snapshot

            # Also persist to QGIS project for session recovery
            self._save_snapshot_to_project(model_name, snapshot)

            self.logger.info(f"Stored snapshot from layer for {model_name}: {len(snapshot)} features")
        except Exception as e:
            self.logger.error(f"Failed to store snapshot from layer for {model_name}: {e}", exc_info=True)

    def _save_snapshot_to_project(self, model_name: str, snapshot: Dict[int, str]):
        """Save snapshot to QGIS project variables."""
        qgs_project = QgsProject.instance()
        snapshot_json = json.dumps(snapshot)
        qgs_project.writeEntry(
            self.SYNC_VAR_SECTION,
            f"{model_name}_snapshot",
            snapshot_json
        )

    def _load_snapshot_from_project(self, model_name: str) -> Dict[int, str]:
        """Load snapshot from QGIS project variables."""
        qgs_project = QgsProject.instance()
        snapshot_json = qgs_project.readEntry(
            self.SYNC_VAR_SECTION,
            f"{model_name}_snapshot",
            ""
        )[0]

        if snapshot_json:
            try:
                # Keys come back as strings, convert to int
                raw = json.loads(snapshot_json)
                return {int(k): v for k, v in raw.items()}
            except (json.JSONDecodeError, ValueError):
                pass

        return {}

    def _get_snapshot(self, model_name: str) -> Dict[int, str]:
        """
        Get the snapshot for a model, loading from project if needed.
        """
        if model_name not in self._server_snapshots:
            self._server_snapshots[model_name] = self._load_snapshot_from_project(model_name)

        return self._server_snapshots.get(model_name, {})

    def _feature_has_changed(
        self,
        model_name: str,
        feature_id: int,
        current_data: Dict[str, Any],
        debug: bool = False
    ) -> bool:
        """
        Check if a feature has changed compared to the server snapshot.

        Args:
            model_name: Model name
            feature_id: Feature ID
            current_data: Current feature data
            debug: If True, log detailed diff information

        Returns:
            True if the feature has changed or is new
        """
        snapshot = self._get_snapshot(model_name)

        # New feature (not in snapshot)
        if feature_id not in snapshot:
            if debug:
                self.logger.info(f"Feature {feature_id} is NEW (not in snapshot)")
            return True

        # Compare hashes
        current_hash = self._compute_feature_hash(current_data)
        original_hash = snapshot[feature_id]

        if current_hash != original_hash and debug:
            self.logger.info(f"Feature {feature_id} CHANGED: hash {original_hash[:8]}... -> {current_hash[:8]}...")
            # Log field differences for debugging
            self._log_hash_diff(feature_id, current_data, model_name)

        return current_hash != original_hash

    def _log_hash_diff(self, feature_id: int, current_data: Dict[str, Any], model_name: str):
        """
        Log detailed field differences for debugging change detection issues.

        This helps identify which fields are causing hash mismatches.
        """
        self.logger.info(f"=== Hash diff debug for feature {feature_id} ({model_name}) ===")

        # Build normalized hash data for current feature
        hash_data = {}
        raw_types = {}
        for key, value in current_data.items():
            if key in self.EXCLUDE_FROM_COMPARISON:
                continue
            if key.startswith('image_') or key.startswith('document_'):
                continue
            if key.endswith(('_ppm', '_ppb', '_pct', '_opt')):
                continue
            raw_types[key] = type(value).__name__
            normalized = self._normalize_value_for_hash(value)
            hash_data[key] = normalized

        # Log all fields being hashed with their raw types
        self.logger.info("Current data (from QGIS layer):")
        for key in sorted(hash_data.keys()):
            value = hash_data[key]
            raw_type = raw_types.get(key, 'unknown')
            if key == 'geometry':
                # Truncate geometry for readability
                geom_preview = str(value)[:80] + '...' if len(str(value)) > 80 else str(value)
                self.logger.info(f"  {key} [{raw_type}]: {geom_preview}")
            else:
                self.logger.info(f"  {key} [{raw_type}]: {value!r}")

    def sync_pull_to_layer(
        self,
        model_name: str,
        features: List[Dict[str, Any]],
        progress_callback: Optional[Callable[[int], None]] = None,
        project_name: Optional[str] = None,
        company_name: Optional[str] = None,
        crs: Optional[str] = None,
        api_client=None,
        project_id: Optional[int] = None,
        crs_metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Sync pulled features to QGIS layer.

        Args:
            model_name: Model name
            features: List of feature dictionaries from API
            progress_callback: Optional progress callback
            project_name: Optional project name to prefix layer name
            company_name: Optional company name for project natural key
            crs: Optional CRS EPSG code. If not provided, uses default from config.
            api_client: Optional APIClient for fetching lookup table data
            project_id: Optional project ID for fetching lookup table data
            crs_metadata: Optional CRS metadata for coordinate_system_metadata field

        Returns:
            Dictionary with sync results
        """
        self.logger.info(f"Syncing {len(features)} features to layer: {model_name}")

        # Handle empty results - create layer from schema if model supports push
        if not features:
            return self._create_empty_layer_from_schema(
                model_name=model_name,
                project_name=project_name,
                company_name=company_name,
                crs=crs,
                api_client=api_client,
                project_id=project_id,
                crs_metadata=crs_metadata
            )

        # Extract schema from first feature
        first_feature = features[0]

        # Get geometry type from schema first, fall back to feature data
        geometry_type = self._get_geometry_type_from_schema(model_name)
        if not geometry_type:
            geometry_type = first_feature.get('geometry_type', 'Point')

        # Check if geometries have Z dimension (elevation)
        has_z = self._detect_z_dimension(features)
        # DrillSample always has Z dimension (geometry built from xyz_from/xyz_to coordinates)
        if model_name.startswith('DrillSample'):
            has_z = True
        if has_z and geometry_type and not geometry_type.endswith('Z'):
            geometry_type = f"{geometry_type}Z"

        self.logger.info(f"Using geometry type: {geometry_type} for {model_name}")

        # Create field definitions
        field_definitions = self._extract_field_definitions(first_feature)

        # Scan all features for additional assay elements (in case first feature has null assay)
        all_assay_elements = self._collect_all_assay_elements(features)
        if all_assay_elements:
            # Add any missing assay element fields (e.g., Au_ppm, Cu_ppm)
            existing_field_names = {fd['name'] for fd in field_definitions}
            for element, units in all_assay_elements:
                field_name = f'{element}_{units}'
                if field_name not in existing_field_names:
                    field_definitions.append({
                        'name': field_name,
                        'type': 'decimal',
                        'length': 0
                    })
            self.logger.info(f"Assay element fields: {[f'{e}_{u}' for e, u in all_assay_elements]}")

        # For LandHolding, add dynamic image/document fields
        counts = {'images': 0, 'documents': 0}
        if model_name == 'LandHolding':
            counts = self._get_max_document_counts(features)
            dynamic_fields = self._create_dynamic_image_document_fields(
                counts['images'], counts['documents']
            )
            field_definitions.extend(dynamic_fields)
            self.logger.info(
                f"Adding {counts['images']} image and {counts['documents']} document columns"
            )

        qgs_fields = self.field_processor.create_qgs_fields(field_definitions)

        # Log custom CRS if provided
        if crs:
            self.logger.info(f"Using custom CRS for {model_name}: {crs[:60]}...")

        # Always recreate the layer to ensure schema is correct
        # (field lengths, types, etc. may have changed)
        # Pass custom CRS if provided (e.g., DrillTrace uses project's local grid CRS)
        layer = self.layer_processor.remove_and_recreate_layer(
            model_name=model_name,
            geometry_type=geometry_type,
            fields=qgs_fields,
            crs=crs,
            project_name=project_name
        )

        # Store project metadata on layer for reliable push operations
        # This ensures new features get correct project even if user switches active project
        if project_name and company_name:
            self.layer_processor.set_layer_project_metadata(
                layer,
                project_name=project_name,
                company_name=company_name,
                crs_metadata=crs_metadata
            )

        # Since we recreated the layer, no existing features to map
        id_to_feature = {}

        # Process features
        added = 0
        updated = 0
        features_to_add = []

        # Collect processed attributes for snapshot (so hash matches what we read back)
        processed_features_for_snapshot = []

        for idx, feature_data in enumerate(features):
            if progress_callback:
                progress = int((idx / len(features)) * 100)
                progress_callback(progress)
            
            server_id = feature_data.get('id')

            # Extract attributes
            attributes = self.field_processor.extract_attributes(
                feature_data,
                field_definitions
            )

            # Flatten merged assay data into {element}_{units} fields (e.g., Au_ppm)
            assay_data = feature_data.get('assay')
            if isinstance(assay_data, dict) and assay_data.get('merged'):
                assay_attrs = self._extract_flattened_assay_attributes(assay_data)
                attributes.update(assay_attrs)

            # For LandHolding, extract image/document attributes
            if model_name == 'LandHolding':
                img_doc_attrs = self._extract_image_document_attributes(
                    feature_data, counts['images'], counts['documents']
                )
                attributes.update(img_doc_attrs)

            # Add geometry - pass through whatever format the API returns
            geom_data = feature_data.get('geometry')
            if geom_data:
                attributes['geometry'] = geom_data
                # Log first geometry for debugging
                if idx == 0:
                    self.logger.info(f"Geometry format sample: {type(geom_data).__name__}")

            # Preserve 'location' field for models like DrillPad that may have
            # a point location even when polygon geometry is null
            # This allows layer_processor to create a fallback polygon from the point
            location_data = feature_data.get('location')
            if location_data:
                attributes['location'] = location_data
                if idx == 0 and not geom_data:
                    self.logger.info(f"Using location field as geometry fallback: {type(location_data).__name__}")

            if not geom_data and not location_data and model_name.startswith('DrillSample'):
                # DrillSample has no geometry field - build LineStringZ from xyz_from/xyz_to
                # Note: model_name may include suffix like "_Au_ppm" for assay visualization
                line_geom = self._build_drillsample_geometry(feature_data)
                if line_geom:
                    attributes['geometry'] = line_geom
                    if idx == 0:
                        self.logger.info(f"Built LineStringZ geometry from xyz_from/xyz_to")
                elif idx == 0:
                    # Log why geometry couldn't be built (first feature only)
                    has_xyz_from = 'xyz_from_wgs84' in feature_data or 'xyz_from' in feature_data
                    has_xyz_to = 'xyz_to_wgs84' in feature_data or 'xyz_to' in feature_data
                    self.logger.warning(
                        f"DrillSample geometry not built: xyz_from present={has_xyz_from}, "
                        f"xyz_to present={has_xyz_to}. Available keys: {list(feature_data.keys())[:15]}"
                    )
            
            # Collect processed attributes for snapshot computation
            # This ensures the hash matches what we'll read back from QGIS
            if server_id:
                # Build snapshot data matching what get_changed_features will see
                snapshot_data = dict(attributes)
                # Ensure id is included (it's needed for lookup)
                snapshot_data['id'] = server_id
                # Add epsg field since get_changed_features adds it from layer CRS
                layer_crs = layer.crs() if layer else None
                if layer_crs and layer_crs.isValid():
                    epsg_code = layer_crs.postgisSrid()
                else:
                    epsg_code = 4326
                snapshot_data['epsg'] = epsg_code

                # Convert geometry to EWKT format to match what get_changed_features() returns
                # get_changed_features() reads from QGIS layer and converts to EWKT
                # So we need to store the snapshot in the same format for hash comparison
                geom_value = snapshot_data.get('geometry')
                if geom_value:
                    ewkt_geom = self._convert_geometry_to_ewkt(geom_value, epsg_code)
                    if ewkt_geom:
                        snapshot_data['geometry'] = ewkt_geom

                # Model-specific normalization to match get_changed_features() behavior
                # DrillPad: location and polygon are consumed to create geometry
                if model_name == 'DrillPad':
                    snapshot_data['location'] = None
                    if 'polygon' not in snapshot_data:
                        snapshot_data['polygon'] = None

                processed_features_for_snapshot.append(snapshot_data)

            # Check if feature exists locally
            if server_id and server_id in id_to_feature:
                # Update existing feature
                local_feature = id_to_feature[server_id]
                self.layer_processor.update_feature(
                    layer,
                    local_feature.id(),
                    attributes
                )
                updated += 1
            else:
                # Add new feature
                features_to_add.append(attributes)
                added += 1
        
        # Batch add new features
        if features_to_add:
            self.layer_processor.add_features(layer, features_to_add)

        # Configure field widgets for LandHolding
        if model_name == 'LandHolding':
            self._configure_landholding_widgets(layer, features)

        # Configure field widgets for DrillCollar
        if model_name == 'DrillCollar':
            self._configure_drillcollar_widgets(
                layer, features,
                api_client=api_client,
                project_id=project_id
            )

        # Configure field widgets for DrillPad
        if model_name == 'DrillPad':
            self._configure_drillpad_widgets(layer, features)

        # Configure field widgets for PointSample
        # Note: point_sample_types should be passed from caller if available
        if model_name == 'PointSample' or model_name.startswith('PointSample_'):
            self._configure_pointsample_widgets(
                layer, features,
                api_client=api_client,
                project_id=project_id
            )

        # Configure Photo layer with camera icon and image popup
        if model_name == 'Photo':
            self._configure_photo_widgets(layer)

        # Store snapshot by reading data back from the QGIS layer.
        # This ensures the hash matches exactly what get_changed_features() will
        # compute, including any type conversions or formatting changes QGIS makes.
        self._store_snapshot_from_layer(model_name, layer, project_name)

        result = {
            'added': added,
            'updated': updated,
            'deleted': 0,
            'total': len(features),
            'layer': layer
        }

        # Add image/document counts for LandHolding
        if model_name == 'LandHolding':
            result['image_columns'] = counts['images']
            result['document_columns'] = counts['documents']

        self.logger.info(f"Sync complete: {result}")
        return result
    
    def get_changed_features(
        self,
        model_name: str,
        progress_callback: Optional[Callable[[int], None]] = None,
        project_name: Optional[str] = None
    ) -> Tuple[List[Dict[str, Any]], int, int]:
        """
        Get features that have been modified locally.

        Only returns features that have actually changed compared to the
        server snapshot stored during pull.

        Args:
            model_name: Model name
            progress_callback: Optional progress callback
            project_name: Optional project name for layer lookup

        Returns:
            Tuple of (changed_features, total_checked, skipped_unchanged)
        """
        self.logger.info(f"Getting changed features for: {model_name}")

        layer = self._find_layer(model_name, project_name)
        if not layer:
            layer_name = self.layer_processor._build_layer_name(model_name, project_name)
            self.logger.warning(f"Layer not found: {layer_name}")
            return [], 0, 0

        changed_features = []
        skipped_unchanged = 0

        # Get all features
        all_features = self.layer_processor.get_all_features(layer)
        total_count = len(all_features)

        # Determine snapshot key - use actual layer name for PointSample/DrillSample
        # since they may have suffixes like _Soil_Au_ppb
        snapshot_key = model_name
        if model_name in ('PointSample', 'DrillSample') and layer:
            # Extract the model portion from layer name (after project prefix)
            layer_name = layer.name()
            if project_name and layer_name.startswith(f"{project_name}_"):
                snapshot_key = layer_name[len(project_name) + 1:]  # Remove "ProjectName_" prefix
                self.logger.info(f"Using snapshot key from layer name: {snapshot_key}")

        # Check if we have a snapshot
        snapshot = self._get_snapshot(snapshot_key)
        if not snapshot:
            self.logger.warning(
                f"No snapshot found for {snapshot_key}. "
                f"Change detection disabled - all features will be pushed. "
                f"Pull data first to create a snapshot."
            )

        self.logger.info(f"Snapshot has {len(snapshot)} entries for {snapshot_key}")

        # Extract field definitions (needed for type conversion)
        field_definitions = self._get_field_definitions_from_layer(layer)

        for idx, feature in enumerate(all_features):
            if progress_callback:
                progress = int((idx / total_count) * 100)
                progress_callback(progress)

            # Convert feature to API format
            feature_dict = {}

            # Add attributes
            for field in layer.fields():
                field_name = field.name()

                # Skip dynamic image/document columns (read-only display fields)
                if field_name.startswith('image_') or field_name.startswith('document_'):
                    continue

                value = feature.attribute(field_name)
                feature_dict[field_name] = value

            # Get layer CRS EPSG code
            layer_crs = layer.crs()
            epsg_code = layer_crs.postgisSrid() if layer_crs.isValid() else 4326

            # Add EPSG to feature
            feature_dict['epsg'] = epsg_code

            # Add geometry in EWKT format (API expects SRID prefix)
            geometry = feature.geometry()
            if geometry and not geometry.isNull():
                ewkt = self.geometry_processor.qgs_to_ewkt(geometry, srid=epsg_code)
                feature_dict['geometry'] = ewkt

            # For DrillPad: Normalize to match snapshot format
            # API has location (point), polygon, and geometry fields
            # When we generate a 30m polygon from location, we need to set:
            # - location = null (consumed to create geometry)
            # - polygon = null (not used for generated polygons)
            if model_name == 'DrillPad':
                feature_dict['location'] = None
                # Only set polygon=null if it's not already in the feature
                if 'polygon' not in feature_dict:
                    feature_dict['polygon'] = None

            # Check if feature has changed
            feature_id = feature_dict.get('id')
            if feature_id:
                # Enable debug for first feature to diagnose hash mismatches
                debug_this_feature = (idx == 0 and len(snapshot) > 0)
                # Use snapshot_key (e.g., PointSample_Soil_Au_ppb) not model_name (PointSample)
                has_changed = self._feature_has_changed(
                    snapshot_key, feature_id, feature_dict, debug=debug_this_feature
                )

                if not has_changed:
                    skipped_unchanged += 1
                    continue

            # Prepare for push (convert types, remove read-only fields)
            prepared = self.field_processor.prepare_for_push(
                feature_dict,
                field_definitions
            )

            # Add geometry back
            if 'geometry' in feature_dict:
                prepared['geometry'] = feature_dict['geometry']

            changed_features.append(prepared)

        self.logger.info(
            f"Change detection for {model_name}: "
            f"{len(changed_features)} changed, {skipped_unchanged} unchanged out of {total_count}"
        )
        return changed_features, total_count, skipped_unchanged
    
    def sync_push_response(
        self,
        model_name: str,
        response: Dict[str, Any],
        project_name: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Process push response and update local records.

        Args:
            model_name: Model name
            response: API response from push
            project_name: Optional project name for layer lookup

        Returns:
            Summary of updates
        """
        self.logger.info(f"Processing push response for: {model_name}")

        # Extract results - handle both paginated (dict) and non-paginated (list) responses
        if isinstance(response, list):
            results = response
        else:
            results = response.get('results', [])

        layer = self._find_layer(model_name, project_name)
        if not layer:
            self.logger.warning(f"Layer not found: {model_name}")
            return {'updated': 0, 'errors': 0}
        
        updated = 0
        errors = 0
        
        # Update features with server IDs
        for result in results:
            if result.get('success'):
                # Feature was successfully saved on server
                server_id = result.get('id')
                # TODO: Update local feature with server ID if it was new
                updated += 1
            else:
                errors += 1
                self.logger.error(f"Feature push failed: {result.get('error')}")
        
        return {
            'updated': updated,
            'errors': errors,
            'total': len(results)
        }
    
    def has_local_changes(self, model_name: str, project_name: Optional[str] = None) -> bool:
        """
        Check if layer has unsaved changes.

        Args:
            model_name: Model name
            project_name: Optional project name for layer lookup

        Returns:
            True if there are local changes
        """
        layer = self._find_layer(model_name, project_name)
        if not layer:
            return False

        # Check if layer is modified
        return layer.isModified()

    def layer_exists(self, model_name: str, project_name: Optional[str] = None) -> bool:
        """
        Check if layer exists.

        Args:
            model_name: Model name
            project_name: Optional project name for layer lookup

        Returns:
            True if layer exists
        """
        layer = self._find_layer(model_name, project_name)
        return layer is not None
    
    def get_last_sync_time(self, model_name: str) -> Optional[str]:
        """
        Get last sync time for model.
        
        Args:
            model_name: Model name
            
        Returns:
            ISO timestamp string or None
        """
        qgs_project = QgsProject.instance()
        
        value = qgs_project.readEntry(
            self.SYNC_VAR_SECTION,
            f"{model_name}_last_sync",
            ""
        )[0]
        
        return value if value else None
    
    def set_last_sync_time(self, model_name: str, timestamp: str):
        """
        Set last sync time for model.
        
        Args:
            model_name: Model name
            timestamp: ISO timestamp string
        """
        qgs_project = QgsProject.instance()
        
        qgs_project.writeEntry(
            self.SYNC_VAR_SECTION,
            f"{model_name}_last_sync",
            timestamp
        )
        
        self.logger.debug(f"Set last sync time for {model_name}: {timestamp}")
    
    def _extract_field_definitions(self, feature: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Extract field definitions from feature data.

        Args:
            feature: Feature dictionary

        Returns:
            List of field definition dictionaries
        """
        field_definitions = []

        # Skip special fields
        skip_fields = {'geometry', 'geometry_type'}

        # Fields that typically contain longer text/JSON data
        long_text_fields = {
            'documents', 'notes', 'description', 'comments', 'metadata',
            'geojson', 'wkt', 'properties', 'attributes', 'data'
        }

        for field_name, value in feature.items():
            if field_name in skip_fields:
                continue

            # Special handling for merged assay data - flatten to {element}_{units} fields
            if field_name == 'assay' and isinstance(value, dict) and value.get('merged'):
                # Add flattened assay element fields (e.g., Au_ppm, Cu_ppm)
                elements = value.get('elements', [])
                for elem in elements:
                    element_symbol = elem.get('element', '')
                    units = elem.get('units', 'ppm')
                    if element_symbol:
                        field_definitions.append({
                            'name': f'{element_symbol}_{units}',
                            'type': 'decimal',
                            'length': 0  # No length for decimal
                        })
                # Also keep the original assay field as JSON for reference
                field_definitions.append({
                    'name': field_name,
                    'type': 'string',
                    'length': -1
                })
                continue

            # Infer type from value
            field_type = 'string'
            field_length = 255  # Default length

            if isinstance(value, bool):
                # Check bool before int since bool is subclass of int
                field_type = 'boolean'
            elif isinstance(value, int):
                field_type = 'integer'
            elif isinstance(value, float):
                field_type = 'decimal'
            elif isinstance(value, str):
                # Use longer length for known long-text fields or long values
                if field_name.lower() in long_text_fields or len(value) > 255:
                    field_length = -1  # Unlimited length in QGIS
                elif len(value) > 100:
                    field_length = 1024  # Medium-length fields
            elif isinstance(value, (list, dict)):
                # JSON-like data needs unlimited length
                field_type = 'string'
                field_length = -1

            field_definitions.append({
                'name': field_name,
                'type': field_type,
                'length': field_length
            })

        return field_definitions

    def _create_empty_layer_from_schema(
        self,
        model_name: str,
        project_name: Optional[str] = None,
        company_name: Optional[str] = None,
        crs: Optional[str] = None,
        api_client=None,
        project_id: Optional[int] = None,
        crs_metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Create an empty layer with proper schema when no features are pulled.

        This allows users to create new features locally and push them to the server,
        even when the server has no existing data for this model.

        Only creates layers for models that support push operations.

        Args:
            model_name: Model name
            project_name: Optional project name to prefix layer name
            company_name: Optional company name for project natural key
            crs: Optional CRS EPSG code
            api_client: Optional APIClient for widget configuration
            project_id: Optional project ID for widget configuration
            crs_metadata: Optional CRS metadata for coordinate_system_metadata field

        Returns:
            Dictionary with sync results (all zeros for empty layer)
        """
        from qgis.core import QgsMessageLog, Qgis

        # Get schema for this model
        schema = get_schema(model_name)
        if not schema:
            self.logger.warning(f"No schema found for {model_name}, skipping empty layer creation")
            return {'added': 0, 'updated': 0, 'deleted': 0}

        # Only create empty layers for models that support push
        if not schema.supports_push:
            self.logger.info(
                f"Model {model_name} does not support push, skipping empty layer creation"
            )
            QgsMessageLog.logMessage(
                f"No data found for {model_name} (read-only model)",
                "GeodbIO", Qgis.Info
            )
            return {'added': 0, 'updated': 0, 'deleted': 0}

        self.logger.info(f"Creating empty layer for {model_name} from schema (supports push)")
        QgsMessageLog.logMessage(
            f"No data found for {model_name} - creating empty layer for new features",
            "GeodbIO", Qgis.Info
        )

        # Get geometry type from schema
        geometry_type = self._get_geometry_type_from_schema(model_name)
        if not geometry_type:
            self.logger.warning(f"No geometry type for {model_name}, skipping")
            return {'added': 0, 'updated': 0, 'deleted': 0}

        # Get field definitions from schema
        field_definitions = self._get_field_definitions_from_schema(model_name)
        if not field_definitions:
            self.logger.warning(f"No field definitions for {model_name}, skipping")
            return {'added': 0, 'updated': 0, 'deleted': 0}

        # Create QgsFields
        qgs_fields = self.field_processor.create_qgs_fields(field_definitions)

        # Create the empty layer
        layer = self.layer_processor.remove_and_recreate_layer(
            model_name=model_name,
            geometry_type=geometry_type,
            fields=qgs_fields,
            crs=crs,
            project_name=project_name
        )

        if not layer:
            self.logger.error(f"Failed to create empty layer for {model_name}")
            return {'added': 0, 'updated': 0, 'deleted': 0}

        # Store project metadata on layer for reliable push operations
        # This ensures new features get correct project even if user switches active project
        if project_name and company_name:
            self.layer_processor.set_layer_project_metadata(
                layer,
                project_name=project_name,
                company_name=company_name,
                crs_metadata=crs_metadata
            )

        # Configure widgets based on model type (same as populated layers)
        if model_name == 'LandHolding':
            self._configure_landholding_widgets(layer, [])

        if model_name == 'DrillCollar':
            self._configure_drillcollar_widgets(
                layer, [],
                api_client=api_client,
                project_id=project_id
            )

        if model_name == 'DrillPad':
            self._configure_drillpad_widgets(layer, [])

        if model_name == 'PointSample' or model_name.startswith('PointSample_'):
            self._configure_pointsample_widgets(
                layer, [],
                api_client=api_client,
                project_id=project_id
            )

        # Store empty snapshot (no features from server)
        self._store_snapshot(model_name, [])

        self.logger.info(f"Created empty {model_name} layer ready for new features")
        QgsMessageLog.logMessage(
            f"✓ Created empty {model_name} layer - digitize new features and push to server",
            "GeodbIO", Qgis.Success
        )

        return {
            'added': 0,
            'updated': 0,
            'deleted': 0,
            'total': 0,
            'empty_layer_created': True
        }

    def _get_field_definitions_from_schema(self, model_name: str) -> List[Dict[str, Any]]:
        """
        Get field definitions from model schema.

        Args:
            model_name: Model name

        Returns:
            List of field definition dictionaries
        """
        schema = get_schema(model_name)
        if not schema:
            self.logger.warning(f"No schema found for model: {model_name}")
            return []

        field_definitions = []

        # Map schema field types to sync field types
        type_map = {
            FieldType.STRING: 'string',
            FieldType.INTEGER: 'integer',
            FieldType.DOUBLE: 'decimal',
            FieldType.BOOLEAN: 'boolean',
            FieldType.DATE: 'string',  # Store as string for now
            FieldType.DATETIME: 'string',
        }

        for field_schema in schema.fields:
            field_definitions.append({
                'name': field_schema.name,
                'type': type_map.get(field_schema.field_type, 'string'),
                'length': field_schema.length,
                'readonly': field_schema.readonly,
            })

        return field_definitions

    def _get_geometry_type_from_schema(self, model_name: str) -> str:
        """
        Get geometry type from model schema.

        Args:
            model_name: Model name (may include suffix like PointSample_Soil)

        Returns:
            Geometry type string (Point, Polygon, etc.)
        """
        # Extract base model name (e.g., "PointSample_Soil" -> "PointSample")
        base_model_name = model_name.split('_')[0] if '_' in model_name else model_name

        schema = get_schema(base_model_name)
        if not schema:
            return 'Point'  # Default

        if schema.geometry_type == GeometryType.NONE:
            return None

        return schema.geometry_type.value

    def _detect_z_dimension(self, features: List[Dict[str, Any]]) -> bool:
        """
        Detect if any feature geometries contain Z (elevation) dimension.

        Checks the first few features for Z coordinates in their geometry strings.

        Args:
            features: List of feature dictionaries from API

        Returns:
            True if Z dimension detected
        """
        # Check first 5 features (or all if fewer)
        check_count = min(5, len(features))

        for feature in features[:check_count]:
            geom_data = feature.get('geometry')
            if not geom_data:
                continue

            # If geometry is a string (WKT/EWKT format)
            if isinstance(geom_data, str):
                geom_upper = geom_data.upper()
                # Look for "POINT Z", "LINESTRING Z", "POLYGON Z", etc.
                # or check for 3 coordinate values in WKT
                if ' Z ' in geom_upper or geom_upper.startswith('SRID=') and ' Z' in geom_upper:
                    return True

        return False

    def _find_layer(self, model_name: str, project_name: Optional[str] = None):
        """
        Find layer by model name, with support for project-prefixed names.

        Handles PointSample layers that have suffixes like _Soil_Au_ppb
        by searching for layers that start with the expected prefix.

        Args:
            model_name: Model name
            project_name: Optional project name

        Returns:
            QgsVectorLayer or None
        """
        from qgis.core import QgsProject

        # Try exact match with project prefix first
        if project_name:
            layer_name = self.layer_processor._build_layer_name(model_name, project_name)
            layer = self.layer_processor.find_layer_by_name(layer_name)
            if layer:
                return layer

            # For PointSample/DrillSample, try prefix match to find layers
            # with suffixes like _Soil_Au_ppb
            if model_name in ('PointSample', 'DrillSample'):
                prefix = f"{project_name}_{model_name}"
                for lyr in QgsProject.instance().mapLayers().values():
                    if hasattr(lyr, 'name') and lyr.name().startswith(prefix):
                        self.logger.info(f"Found layer by prefix match: {lyr.name()}")
                        return lyr

        # Fallback to unprefixed name for backwards compatibility
        layer = self.layer_processor.find_layer_by_name(model_name)
        if layer:
            return layer

        # Also try prefix match without project name for PointSample/DrillSample
        if model_name in ('PointSample', 'DrillSample'):
            for lyr in QgsProject.instance().mapLayers().values():
                if hasattr(lyr, 'name') and lyr.name().startswith(model_name):
                    self.logger.info(f"Found layer by prefix match: {lyr.name()}")
                    return lyr

        return None

    def _get_field_definitions_from_layer(self, layer) -> List[Dict[str, Any]]:
        """Get field definitions from existing layer."""
        field_definitions = []

        for field in layer.fields():
            field_name = field.name()

            # Map QGIS type back to API type
            qgs_type = field.type()
            if qgs_type == 2:  # Int
                field_type = 'integer'
            elif qgs_type == 6:  # Double
                field_type = 'decimal'
            elif qgs_type == 1:  # Bool
                field_type = 'boolean'
            else:
                field_type = 'string'

            field_definitions.append({
                'name': field_name,
                'type': field_type,
                'length': field.length()
            })

        return field_definitions

    def mark_features_synced(
        self,
        model_name: str,
        features: List[Dict[str, Any]],
        project_name: Optional[str] = None
    ) -> int:
        """
        Mark features as synced after successful push.

        Updates local features with server-assigned IDs and clears
        any dirty/modified flags.

        Args:
            model_name: Model name
            features: List of feature dictionaries with server IDs
            project_name: Optional project name for layer lookup

        Returns:
            Number of features marked as synced
        """
        self.logger.info(f"Marking {len(features)} features as synced for: {model_name}")

        layer = self._find_layer(model_name, project_name)
        if not layer:
            self.logger.warning(f"Layer not found: {model_name}")
            return 0

        synced_count = 0

        layer.startEditing()

        for feature_data in features:
            server_id = feature_data.get('_server_id') or feature_data.get('id')
            local_name = feature_data.get('name')

            if not server_id:
                continue

            # Find feature by name if no local ID mapping
            for local_feature in layer.getFeatures():
                feature_name = local_feature.attribute('name')
                feature_id_attr = local_feature.attribute('id')

                # Match by name if ID not set, or update existing
                if feature_name == local_name:
                    # Update the server ID
                    id_field_idx = layer.fields().indexFromName('id')
                    if id_field_idx >= 0 and (not feature_id_attr or feature_id_attr != server_id):
                        layer.changeAttributeValue(local_feature.id(), id_field_idx, server_id)
                        synced_count += 1
                    break

        layer.commitChanges()

        # Update last sync time
        self.set_last_sync_time(model_name, datetime.now().isoformat())

        # Update snapshot to reflect current layer state after sync
        # This ensures subsequent pushes correctly detect changes
        self._store_snapshot_from_layer(model_name, layer, project_name)

        self.logger.info(f"Marked {synced_count} features as synced")
        return synced_count

    def get_feature_count(self, model_name: str, project_name: Optional[str] = None) -> int:
        """
        Get number of features in a layer.

        Args:
            model_name: Model name
            project_name: Optional project name for layer lookup

        Returns:
            Feature count or 0 if layer doesn't exist
        """
        layer = self._find_layer(model_name, project_name)
        if not layer:
            return 0
        return layer.featureCount()

    def clear_layer(self, model_name: str, project_name: Optional[str] = None) -> bool:
        """
        Clear all features from a layer.

        Args:
            model_name: Model name
            project_name: Optional project name for layer lookup

        Returns:
            True if successful
        """
        layer = self._find_layer(model_name, project_name)
        if not layer:
            return False

        layer.startEditing()

        # Delete all features
        feature_ids = [f.id() for f in layer.getFeatures()]
        layer.deleteFeatures(feature_ids)

        layer.commitChanges()
        layer.triggerRepaint()

        self.logger.info(f"Cleared {len(feature_ids)} features from {model_name}")
        return True