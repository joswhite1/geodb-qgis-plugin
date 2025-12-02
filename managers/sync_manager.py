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
        'created_at', 'updated_at', 'date_created', 'last_edited',
        'created_by', 'updated_by', 'last_edited_by',
        'serial_link', 'current_retain', 'retain_fiscal_year',
        'current_retain_status', 'retain_records', 'documents', 'images',
        'image_urls', 'document_urls',
        'fid'  # QGIS internal feature ID - not part of actual data
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
        timestamps, and natural key objects.
        """
        import ast
        import re

        if value is None or value == '' or str(value) == 'NULL':
            return None

        # Normalize booleans to lowercase JSON strings
        if isinstance(value, bool):
            return 'true' if value else 'false'

        # For geometry, normalize EWKT format
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

        # Round floats to avoid precision issues
        if isinstance(value, float):
            return round(value, 6)

        return value

    def _round_coordinates_in_wkt(self, wkt_string: str) -> str:
        """
        Round all coordinate values in WKT string to 6 decimal places.

        Args:
            wkt_string: WKT geometry string

        Returns:
            WKT string with rounded coordinates
        """
        import re

        def round_match(match):
            """Round a matched number to 6 decimals."""
            num = float(match.group(0))
            return f"{num:.6f}".rstrip('0').rstrip('.')

        # Match floating point numbers (including negative and scientific notation)
        pattern = r'-?\d+\.?\d*(?:[eE][+-]?\d+)?'
        return re.sub(pattern, round_match, wkt_string)

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
        - project: Display name only, read-only
        - land_status: Dropdown with available options
        - retain_records: Read-only display of current retain status
        - image_* and document_*: Read-only URL fields

        Args:
            layer: The LandHolding layer
            features: List of feature data from API
        """
        import json

        # 1. Configure 'project' field - display name only, read-only
        if layer.fields().indexOf('project') >= 0:
            # Set as read-only
            self.layer_processor.set_field_readonly(layer, 'project', readonly=True)

            # Set display expression to show only the name
            # The field contains JSON like: {"name": "Nekash", "company": "Lightning Creek Gold Corp"}
            # We want to display just "Nekash"
            layer.setDisplayExpression("json_extract(\"project\", '$.name')")

            self.logger.info("Configured 'project' field as read-only with name-only display")

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
        for field in layer.fields():
            field_name = field.name()
            if field_name.startswith('image_') or field_name.startswith('document_'):
                # Set as clickable URL instead of plain read-only text
                self.layer_processor.set_field_as_url(layer, field_name)

        self.logger.info("Configured all image and document fields as clickable URLs")

    def _store_snapshot(self, model_name: str, features: List[Dict[str, Any]]):
        """
        Store a snapshot of server data for change detection.

        Args:
            model_name: Model name
            features: List of feature dictionaries from API
        """
        try:
            from qgis.core import QgsMessageLog, Qgis
            QgsMessageLog.logMessage(
                f"Starting snapshot storage for {model_name} with {len(features)} features",
                "GeodbIO", Qgis.Info
            )
            self.logger.info(f"Starting snapshot storage for {model_name} with {len(features)} features")
            snapshot = {}
            for idx, feature in enumerate(features):
                feature_id = feature.get('id')
                if feature_id:
                    try:
                        # DEBUG: Log first 3 features during snapshot creation
                        if idx < 3:
                            import json

                            # Recreate the hash data to see what's being stored
                            hash_data = {}
                            for key, value in feature.items():
                                if key in self.EXCLUDE_FROM_COMPARISON:
                                    continue
                                normalized = self._normalize_value_for_hash(value)
                                hash_data[key] = normalized

                            sorted_json = json.dumps(hash_data, sort_keys=True, default=str)

                            QgsMessageLog.logMessage(
                                f"[SNAPSHOT DEBUG] Feature {feature_id} ({feature.get('name', 'NO_NAME')}):\n"
                                f"  Complete hash data: {sorted_json[:1000]}",
                                "GeodbIO", Qgis.Warning
                            )

                        feature_hash = self._compute_feature_hash(feature)
                        snapshot[feature_id] = feature_hash

                        # DEBUG: Log hash for first 3 features
                        if idx < 3:
                            QgsMessageLog.logMessage(
                                f"[SNAPSHOT DEBUG] Feature {feature_id} hash: {feature_hash}",
                                "GeodbIO", Qgis.Warning
                            )
                    except Exception as e:
                        self.logger.error(f"Failed to hash feature {feature_id}: {e}")
                        QgsMessageLog.logMessage(
                            f"Failed to hash feature {feature_id}: {e}",
                            "GeodbIO", Qgis.Warning
                        )

            self._server_snapshots[model_name] = snapshot

            # Also persist to QGIS project for session recovery
            self._save_snapshot_to_project(model_name, snapshot)

            self.logger.info(f"Stored snapshot for {model_name}: {len(snapshot)} features")
            QgsMessageLog.logMessage(
                f"✓ Stored snapshot for {model_name}: {len(snapshot)} features",
                "GeodbIO", Qgis.Success
            )
        except Exception as e:
            self.logger.error(f"Failed to store snapshot for {model_name}: {e}", exc_info=True)
            QgsMessageLog.logMessage(
                f"Failed to store snapshot for {model_name}: {e}",
                "GeodbIO", Qgis.Critical
            )

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

    def _debug_hash_comparison(
        self,
        model_name: str,
        feature_id: int,
        current_data: Dict[str, Any],
        snapshot: Dict[int, str]
    ):
        """
        Debug helper: Compare field-by-field to identify what changed.

        Args:
            model_name: Model name
            feature_id: Feature ID
            current_data: Current feature data
            snapshot: Snapshot dictionary
        """
        from qgis.core import QgsMessageLog, Qgis
        import json

        if feature_id not in snapshot:
            QgsMessageLog.logMessage(
                f"[HASH DEBUG] Feature {feature_id} is NEW (not in snapshot)",
                "GeodbIO", Qgis.Info
            )
            return

        # Recreate the hash data to see what's being compared
        current_hash_data = {}
        for key, value in current_data.items():
            if key in self.EXCLUDE_FROM_COMPARISON:
                continue
            normalized = self._normalize_value_for_hash(value)
            current_hash_data[key] = normalized

        # Show all fields that will be hashed
        sorted_json = json.dumps(current_hash_data, sort_keys=True, default=str)

        QgsMessageLog.logMessage(
            f"[HASH DEBUG] Feature {feature_id} complete hash data:\n"
            f"{sorted_json[:1000]}",
            "GeodbIO", Qgis.Info
        )

        # Focus on geometry specifically
        if 'geometry' in current_data:
            geom = current_data['geometry']
            normalized_geom = self._normalize_value_for_hash(geom)

            QgsMessageLog.logMessage(
                f"[HASH DEBUG] Feature {feature_id} geometry comparison:\n"
                f"  Raw EWKT: {str(geom)[:300]}\n"
                f"  Normalized: {str(normalized_geom)[:300]}",
                "GeodbIO", Qgis.Info
            )

    def _feature_has_changed(
        self,
        model_name: str,
        feature_id: int,
        current_data: Dict[str, Any]
    ) -> bool:
        """
        Check if a feature has changed compared to the server snapshot.

        Args:
            model_name: Model name
            feature_id: Feature ID
            current_data: Current feature data

        Returns:
            True if the feature has changed or is new
        """
        snapshot = self._get_snapshot(model_name)

        # New feature (not in snapshot)
        if feature_id not in snapshot:
            return True

        # Compare hashes
        current_hash = self._compute_feature_hash(current_data)
        original_hash = snapshot[feature_id]

        return current_hash != original_hash

    def sync_pull_to_layer(
        self,
        model_name: str,
        features: List[Dict[str, Any]],
        progress_callback: Optional[Callable[[int], None]] = None,
        project_name: Optional[str] = None,
        crs: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Sync pulled features to QGIS layer.

        Args:
            model_name: Model name
            features: List of feature dictionaries from API
            progress_callback: Optional progress callback
            project_name: Optional project name to prefix layer name
            crs: Optional CRS EPSG code. If not provided, uses default from config.

        Returns:
            Dictionary with sync results
        """
        self.logger.info(f"Syncing {len(features)} features to layer: {model_name}")

        if not features:
            return {'added': 0, 'updated': 0, 'deleted': 0}

        # Extract schema from first feature
        first_feature = features[0]

        # DEBUG: Log raw API response keys and geometry
        from qgis.core import QgsMessageLog, Qgis
        QgsMessageLog.logMessage(
            f"Raw API first feature keys: {list(first_feature.keys())}",
            "GeodbIO", Qgis.Info
        )
        raw_geom = first_feature.get('geometry')
        if raw_geom:
            geom_preview = str(raw_geom)[:150] if raw_geom else "None"
            QgsMessageLog.logMessage(
                f"Raw API geometry type: {type(raw_geom).__name__}, preview: {geom_preview}",
                "GeodbIO", Qgis.Info
            )
        else:
            QgsMessageLog.logMessage(
                f"Raw API has NO 'geometry' key! Available: {list(first_feature.keys())}",
                "GeodbIO", Qgis.Warning
            )

        # Get geometry type from schema first, fall back to feature data
        geometry_type = self._get_geometry_type_from_schema(model_name)
        if not geometry_type:
            geometry_type = first_feature.get('geometry_type', 'Point')

        # Check if geometries have Z dimension (elevation)
        has_z = self._detect_z_dimension(features)
        if has_z and geometry_type and not geometry_type.endswith('Z'):
            geometry_type = f"{geometry_type}Z"
            QgsMessageLog.logMessage(
                f"Detected Z dimension in geometries, using {geometry_type}",
                "GeodbIO", Qgis.Info
            )

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

        # Since we recreated the layer, no existing features to map
        id_to_feature = {}
        
        # Process features
        added = 0
        updated = 0
        features_to_add = []
        
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

        # Store snapshot of server data for change detection on push
        self._store_snapshot(model_name, features)

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

        # Check if we have a snapshot
        snapshot = self._get_snapshot(model_name)
        if not snapshot:
            self.logger.warning(
                f"No snapshot found for {model_name}. "
                f"Change detection disabled - all features will be pushed. "
                f"Pull data first to create a snapshot."
            )

        self.logger.info(f"Snapshot has {len(snapshot)} entries for {model_name}")

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

            # Check if feature has changed
            feature_id = feature_dict.get('id')
            if feature_id:
                snapshot = self._get_snapshot(model_name)
                has_changed = self._feature_has_changed(model_name, feature_id, feature_dict)

                # DEBUG: Enhanced logging for first 5 features that are detected as "changed"
                if has_changed and idx < 5:
                    from qgis.core import QgsMessageLog, Qgis

                    current_hash = self._compute_feature_hash(feature_dict)
                    original_hash = snapshot.get(feature_id, "NOT_IN_SNAPSHOT")

                    # Get geometry details
                    current_geom = feature_dict.get('geometry', 'NO_GEOMETRY')
                    current_geom_preview = str(current_geom)[:200] if current_geom else "None"

                    # Normalize geometry to see what's being compared
                    normalized_current_geom = self._normalize_value_for_hash(current_geom) if current_geom else None

                    QgsMessageLog.logMessage(
                        f"[CHANGE DETECTION DEBUG] Feature {feature_id} ({feature_dict.get('name', 'NO_NAME')}): DETECTED AS CHANGED\n"
                        f"  Current hash:  {current_hash}\n"
                        f"  Original hash: {original_hash}\n"
                        f"  Hashes match: {current_hash == original_hash}\n"
                        f"  Current EWKT from QGIS: {current_geom_preview}\n"
                        f"  Normalized current geom: {str(normalized_current_geom)[:200]}",
                        "GeodbIO", Qgis.Warning
                    )

                    # Field-by-field comparison to find what changed
                    self._debug_hash_comparison(model_name, feature_id, feature_dict, snapshot)

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

        Args:
            model_name: Model name
            project_name: Optional project name

        Returns:
            QgsVectorLayer or None
        """
        # Try with project prefix first
        if project_name:
            layer_name = self.layer_processor._build_layer_name(model_name, project_name)
            layer = self.layer_processor.find_layer_by_name(layer_name)
            if layer:
                return layer

        # Fallback to unprefixed name for backwards compatibility
        return self.layer_processor.find_layer_by_name(model_name)

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