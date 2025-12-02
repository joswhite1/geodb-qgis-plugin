# -*- coding: utf-8 -*-
"""
High-level data management for GeodbIO plugin.

Uses RESTful API endpoints for model-specific data operations.
"""
from typing import Optional, Callable, Dict, Any, List
from datetime import datetime

from ..api.client import APIClient
from .project_manager import ProjectManager
from .sync_manager import SyncManager
from ..utils.config import Config
from ..utils.logger import PluginLogger
from ..models.schemas import is_raster_model


# Supported models for sync operations
SUPPORTED_MODELS = [
    'DrillCollar',
    'DrillSample',
    'DrillPad',
    'DrillLithology',
    'DrillAlteration',
    'DrillStructure',
    'DrillMineralization',
    'DrillSurvey',
    'DrillPhoto',
    'DrillTrace',
    'LandHolding',
    'PointSample',
    'ProjectFile',  # GeoTIFFs, DEMs, and other raster files
]


class DataManager:
    """
    High-level interface for data pull/push operations.

    Uses RESTful API endpoints per model (e.g., /api/v1/drill-collars/)
    instead of generic /api-data/ endpoint.
    """

    def __init__(
        self,
        config: Config,
        api_client: APIClient,
        project_manager: ProjectManager,
        sync_manager: SyncManager
    ):
        """
        Initialize data manager.

        Args:
            config: Configuration instance
            api_client: API client instance
            project_manager: Project manager instance
            sync_manager: Sync manager instance
        """
        self.config = config
        self.api_client = api_client
        self.project_manager = project_manager
        self.sync_manager = sync_manager
        self.logger = PluginLogger.get_logger()

    def pull_model_data(
        self,
        model_name: str,
        incremental: bool = False,
        merge_assays: bool = True,
        assay_config_id: Optional[int] = None,
        ps_type_id: Optional[int] = None,
        ps_type_name: Optional[str] = None,
        assay_element: Optional[str] = None,
        assay_units: Optional[str] = None,
        progress_callback: Optional[Callable[[int, str], None]] = None
    ) -> Dict[str, Any]:
        """
        Pull data for a specific model from server.

        Uses RESTful endpoint: GET /api/v1/{model-name}/?project_id={id}

        Args:
            model_name: Model name (e.g., 'DrillCollar', 'LandHolding')
            incremental: If True, only pull changes since last sync (not yet implemented on API)
            merge_assays: If True, include merged assay data for drill/point samples
            assay_config_id: Optional AssayRangeConfiguration ID to use for merge settings
            ps_type_id: Optional PointSampleType ID to filter by (PointSample only)
            ps_type_name: Optional PointSampleType name for layer naming
            assay_element: Optional element symbol for layer naming (e.g., 'Au')
            assay_units: Optional units for layer naming (e.g., 'ppb')
            progress_callback: Optional callback(progress_percent, status_message)

        Returns:
            Dictionary with sync results
        """
        self.logger.info(f"Pulling data for model: {model_name}")

        # Check if project is selected
        project = self.project_manager.get_active_project()
        if not project:
            raise ValueError("No project selected")

        # Check permissions
        if not self.project_manager.can_view():
            raise PermissionError(f"No permission to view data")

        # Validate model name
        if model_name not in SUPPORTED_MODELS:
            raise ValueError(f"Unsupported model: {model_name}. Supported: {SUPPORTED_MODELS}")

        # Special handling for raster models (ProjectFile)
        if is_raster_model(model_name):
            return self._pull_raster_model(model_name, project, progress_callback)

        try:
            if progress_callback:
                progress_callback(10, f"Fetching {model_name} data from server...")

            # Determine if this model supports assay merging
            supports_assays = model_name in ['DrillSample', 'PointSample']
            params = {}
            if supports_assays and merge_assays:
                params['merge_assays'] = 'true'
                # Pass assay_config_id to use that config's merge settings (recommended approach)
                if assay_config_id:
                    params['assay_config_id'] = str(assay_config_id)

            # Add sample type filter for PointSample
            if model_name == 'PointSample' and ps_type_id:
                params['ps_type_id'] = str(ps_type_id)

            # Pull data from API using RESTful endpoint
            features = self.api_client.get_all_paginated(
                model_name=model_name,
                project_id=project.id,
                params=params if params else None,
                progress_callback=lambda p: progress_callback(10 + int(p * 0.3), "Downloading...") if progress_callback else None
            )

            if progress_callback:
                progress_callback(40, f"Processing {len(features)} records...")

            # Special handling for DrillTrace - list endpoint has no geometry data
            # We need to fetch each trace individually to get the geometry_wgs84 field
            # The API returns WGS84 coordinates, already transformed from local grid
            if model_name == 'DrillTrace' and features:
                self.logger.info(f"Fetching full trace data for {len(features)} traces...")
                full_features = []
                trace_endpoint = self.config.endpoints.get('drill_traces', '')
                for i, trace in enumerate(features):
                    trace_id = trace.get('id')
                    if trace_id:
                        try:
                            full_trace = self.api_client._make_request(
                                'GET', f"{trace_endpoint}{trace_id}/"
                            )
                            full_features.append(full_trace)
                        except Exception as e:
                            self.logger.warning(f"Failed to fetch trace {trace_id}: {e}")
                            continue
                    if progress_callback:
                        progress = 40 + int((i / len(features)) * 10)
                        progress_callback(progress, f"Loading trace {i+1}/{len(features)}")
                # Convert geometry_wgs84 (WKT) to geometry field for QGIS
                features = self._convert_drill_trace_geometry(full_features)
                self.logger.info(f"Converted {len(features)} traces to LineString Z geometry")

            if progress_callback:
                progress_callback(50, f"Syncing {len(features)} features to QGIS layer...")

            # Build effective model name (include sample type and assay info if provided)
            effective_model_name = model_name
            if model_name == 'PointSample' and ps_type_name:
                # e.g., "PointSample_Soil" or "PointSample_RockChip"
                effective_model_name = f"{model_name}_{ps_type_name.replace(' ', '')}"

            # Add assay element and units to layer name if provided
            # e.g., "PointSample_Soil_Au_ppb" or "DrillSample_Au_ppm"
            if assay_element and assay_units:
                effective_model_name = f"{effective_model_name}_{assay_element}_{assay_units}"

            # Sync to QGIS layer with project name prefix
            # DrillTrace geometry is already in WGS84 from the API (no custom CRS needed)
            result = self.sync_manager.sync_pull_to_layer(
                model_name=effective_model_name,
                features=features,
                progress_callback=lambda p: progress_callback(50 + int(p * 0.4), "Syncing...") if progress_callback else None,
                project_name=project.name
            )

            if progress_callback:
                progress_callback(90, "Updating sync metadata...")

            # Update last sync time
            self.sync_manager.set_last_sync_time(model_name, datetime.now().isoformat())

            # Configure landholding-specific widgets
            if model_name == 'LandHolding':
                layer = result.get('layer')
                if layer and self.project_manager.active_company:
                    self.configure_landholding_widgets(layer, self.project_manager.active_company.id)

            if progress_callback:
                progress_callback(100, "Pull complete")

            self.logger.info(f"Pull complete for {model_name}: {len(features)} records")
            return {
                'pulled': len(features),
                'model': model_name,
                **result
            }

        except Exception as e:
            self.logger.error(f"Pull failed for {model_name}: {e}")
            raise

    def push_model_data(
        self,
        model_name: str,
        progress_callback: Optional[Callable[[int, str], None]] = None
    ) -> Dict[str, Any]:
        """
        Push local changes to server.

        Uses RESTful endpoints:
        - POST /api/v1/{model-name}/ for new records
        - PATCH /api/v1/{model-name}/{id}/ for updates

        Args:
            model_name: Model name
            progress_callback: Optional callback(progress_percent, status_message)

        Returns:
            Dictionary with sync results
        """
        self.logger.info(f"Pushing data for model: {model_name}")

        # Check if project is selected
        project = self.project_manager.get_active_project()
        if not project:
            raise ValueError("No project selected")

        # Check permissions
        if not self.project_manager.can_edit():
            raise PermissionError(f"No permission to edit data")

        # Validate model name
        if model_name not in SUPPORTED_MODELS:
            raise ValueError(f"Unsupported model: {model_name}")

        try:
            if progress_callback:
                progress_callback(10, f"Detecting changes for {model_name}...")

            # Get changed features from layer (only features that differ from server snapshot)
            features, total_checked, skipped = self.sync_manager.get_changed_features(
                model_name=model_name,
                progress_callback=lambda p: progress_callback(10 + int(p * 0.2), "Checking for changes...") if progress_callback else None,
                project_name=project.name if project else None
            )

            if not features:
                self.logger.info(f"No changes to push for {model_name} ({skipped} unchanged)")
                if progress_callback:
                    progress_callback(100, f"No changes to push ({skipped} features unchanged)")
                return {'created': 0, 'updated': 0, 'errors': 0, 'skipped': skipped}

            if progress_callback:
                progress_callback(30, f"Uploading {len(features)} changed features ({skipped} unchanged)...")

            # Push each feature using RESTful API
            created = 0
            updated = 0
            errors = []

            # Get schema for natural key support
            from ..models.schemas import get_schema
            schema = get_schema(model_name)

            for i, feature in enumerate(features):
                try:
                    # Strategy 1: Try using id or _server_id for updates
                    # Records pulled from server have 'id'
                    # New records created locally may have '_server_id' after first push
                    record_id = feature.get('id') or feature.get('_server_id')

                    # Strategy 2: If no ID, try natural key lookup
                    if not record_id and schema and schema.natural_key_fields:
                        natural_key = schema.get_natural_key(feature)
                        if natural_key:
                            existing = self.api_client.find_record_by_natural_key(
                                model_name, natural_key, project_id
                            )
                            if existing:
                                record_id = existing['id']
                                self.logger.info(f"Found existing record by natural key: {natural_key}")

                    if record_id:
                        # Update existing record
                        self.api_client.update_record(
                            model_name=model_name,
                            record_id=record_id,
                            data=feature
                        )
                        updated += 1
                        self.logger.debug(f"Updated feature {record_id}")
                    else:
                        # Create new record
                        result = self.api_client.create_record(
                            model_name=model_name,
                            data=feature
                        )
                        created += 1
                        self.logger.debug(f"Created new feature")

                        # Store server ID in feature for later reference
                        if 'id' in result:
                            feature['_server_id'] = result['id']

                except Exception as e:
                    errors.append({'feature': feature.get('name', 'unknown'), 'error': str(e)})
                    self.logger.error(f"Failed to push feature {feature.get('id', 'unknown')}: {e}")

                if progress_callback:
                    progress = 30 + int((i + 1) / len(features) * 60)
                    progress_callback(progress, f"Uploaded {i + 1}/{len(features)}...")

            if progress_callback:
                progress_callback(92, "Updating local records...")

            # Update local records with sync status
            self.sync_manager.mark_features_synced(
                model_name,
                features,
                project_name=project.name if project else None
            )

            if progress_callback:
                progress_callback(100, "Push complete")

            result = {
                'created': created,
                'updated': updated,
                'errors': len(errors),
                'skipped': skipped,
                'error_details': errors if errors else None
            }

            self.logger.info(f"Push complete for {model_name}: {result}")
            return result

        except Exception as e:
            self.logger.error(f"Push failed for {model_name}: {e}")
            raise

    def configure_landholding_widgets(self, layer, company_id: int):
        """
        Configure field widgets and constraints for landholding layer.

        Args:
            layer: QGIS vector layer for landholdings
            company_id: Active company ID for filtering land status types
        """
        import json

        try:
            # Fetch land status options from API
            types = self.api_client.get_landholding_types(company_id)

            # Create value map: display name → natural key JSON
            value_map = {}
            for lt in types:
                display = lt.get('name', '')
                company_name = lt.get('company', {})
                if isinstance(company_name, dict):
                    company_name = company_name.get('name', '')
                natural_key = json.dumps({'name': lt['name'], 'company': company_name}, sort_keys=True)
                value_map[display] = natural_key

            # Configure land_status as dropdown
            if value_map:
                self.sync_manager.layer_processor.configure_field_widget(
                    layer, 'land_status', 'ValueMap', {'map': value_map}
                )

            # Set read-only fields
            readonly_fields = [
                'id', 'project', 'date_created', 'last_edited',
                'created_by', 'last_edited_by', 'serial_link',
                'current_retain', 'retain_fiscal_year'
            ]
            for field in readonly_fields:
                self.sync_manager.layer_processor.set_field_readonly(layer, field, True)

            # Set field aliases for better UX
            aliases = {
                'name': 'Claim Name',
                'land_status': 'Status Type',
                'area_acres': 'Area (acres)',
                'area_hectares': 'Area (hectares)',
                'current_retain': 'Retain Status',
                'retain_fiscal_year': 'Fiscal Year',
                'date_staked': 'Date Staked',
                'staked_by': 'Staked By',
                'serial_number': 'Serial Number',
                'serial_link': 'Registry Link',
                'dropped': 'Dropped'
            }
            for field, alias in aliases.items():
                self.sync_manager.layer_processor.set_field_alias(layer, field, alias)

            self.logger.info(f"Configured landholding widgets with {len(value_map)} land status types")

        except Exception as e:
            self.logger.error(f"Failed to configure landholding widgets: {e}")

    def _convert_drill_trace_geometry(
        self,
        features: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Convert DrillTrace geometry_wgs84 to geometry field for QGIS.

        The DrillTrace API returns a geometry_wgs84 field containing WKT
        in WGS84 (EPSG:4326). This is already transformed from the local
        grid coordinates stored in trace_data.coords.

        Args:
            features: List of trace feature dictionaries from API

        Returns:
            Features with 'geometry' field populated from geometry_wgs84
        """
        for feature in features:
            # Use pre-transformed WGS84 geometry from API
            wgs84_wkt = feature.get('geometry_wgs84')
            if wgs84_wkt:
                feature['geometry'] = wgs84_wkt
            else:
                # Fallback: try trace_data.coords (legacy - these are local grid coords!)
                # This won't display correctly but at least creates the geometry
                trace_data = feature.get('trace_data', {})
                if trace_data:
                    coords = trace_data.get('coords', [])
                    if coords:
                        coord_strings = [f"{c[0]} {c[1]} {c[2]}" for c in coords]
                        feature['geometry'] = f"LINESTRING Z ({', '.join(coord_strings)})"
                        self.logger.warning(
                            f"Trace {feature.get('collar_name', 'unknown')} missing geometry_wgs84, "
                            "using local grid coords (may display incorrectly)"
                        )

        return features

    def _pull_raster_model(
        self,
        model_name: str,
        project,
        progress_callback: Optional[Callable[[int, str], None]] = None
    ) -> Dict[str, Any]:
        """
        Pull raster model data (ProjectFile) and load as raster layers.

        Unlike vector models, raster models require:
        1. Fetching file metadata from API
        2. Downloading actual raster files from storage
        3. Loading as QGIS raster layers (not vector layers)

        Args:
            model_name: Model name (should be 'ProjectFile')
            project: Active project object
            progress_callback: Optional callback(progress_percent, status_message)

        Returns:
            Dictionary with sync results
        """
        from ..processors.raster_processor import RasterProcessor

        self.logger.info(f"Pulling raster model: {model_name}")

        try:
            if progress_callback:
                progress_callback(5, f"Fetching {model_name} metadata from server...")

            # Fetch project file records from API
            # Filter to only raster files
            params = {'is_raster': 'true'}
            files = self.api_client.get_all_paginated(
                model_name=model_name,
                project_id=project.id,
                params=params,
                progress_callback=lambda p: progress_callback(
                    5 + int(p * 0.15), "Fetching file list..."
                ) if progress_callback else None
            )

            if not files:
                self.logger.info(f"No raster files found for project {project.name}")
                if progress_callback:
                    progress_callback(100, "No raster files found")
                return {
                    'pulled': 0,
                    'model': model_name,
                    'loaded': 0,
                    'skipped': 0,
                    'errors': []
                }

            if progress_callback:
                progress_callback(20, f"Found {len(files)} raster files. Downloading...")

            # Initialize raster processor
            raster_processor = RasterProcessor()

            # Process files - download and load as layers
            result = raster_processor.process_project_files(
                project_files=files,
                project_name=project.name,
                progress_callback=lambda p, s: progress_callback(
                    20 + int(p * 0.75), s
                ) if progress_callback else None
            )

            # Update sync metadata
            self.sync_manager.set_last_sync_time(model_name, datetime.now().isoformat())

            if progress_callback:
                progress_callback(100, f"Loaded {result['loaded']} raster layers")

            self.logger.info(
                f"Raster pull complete: {result['loaded']} loaded, "
                f"{result['skipped']} skipped, {len(result['errors'])} errors"
            )

            return {
                'pulled': len(files),
                'model': model_name,
                'loaded': result['loaded'],
                'skipped': result['skipped'],
                'errors': result['errors'],
                'layers': result['layers']
            }

        except Exception as e:
            self.logger.error(f"Failed to pull raster model {model_name}: {e}")
            raise

    def get_available_models(self) -> List[str]:
        """
        Get list of models user has access to.

        Returns:
            List of model names that can be synced
        """
        if not self.project_manager.can_view():
            return []
        return SUPPORTED_MODELS.copy()

    def get_sync_status(self, model_name: str) -> Dict[str, Any]:
        """
        Get sync status for a model.

        Args:
            model_name: Model name

        Returns:
            Dictionary with sync status information
        """
        # Get active project for layer lookup
        project = self.project_manager.get_active_project()
        project_name = project.name if project else None

        return {
            'model': model_name,
            'last_sync': self.sync_manager.get_last_sync_time(model_name),
            'has_changes': self.sync_manager.has_local_changes(model_name, project_name),
            'layer_exists': self.sync_manager.layer_exists(model_name, project_name)
        }

    def get_model_record_count(self, model_name: str) -> int:
        """
        Get count of records for a model from server.

        Args:
            model_name: Model name

        Returns:
            Number of records
        """
        project = self.project_manager.get_active_project()
        if not project:
            return 0

        try:
            response = self.api_client.get_model_data(
                model_name=model_name,
                project_id=project.id
            )
            return response.get('count', 0)
        except Exception as e:
            self.logger.warning(f"Failed to get record count for {model_name}: {e}")
            return 0

    def pull_all_data(
        self,
        progress_callback: Optional[Callable[[int, str], None]] = None
    ) -> Dict[str, Any]:
        """
        Pull all supported models for the current project.

        Args:
            progress_callback: Optional callback(progress_percent, status_message)

        Returns:
            Dictionary with results for each model
        """
        results = {}
        models = self.get_available_models()
        total_models = len(models)

        for i, model_name in enumerate(models):
            try:
                if progress_callback:
                    base_progress = int((i / total_models) * 100)
                    progress_callback(base_progress, f"Pulling {model_name}...")

                model_result = self.pull_model_data(
                    model_name=model_name,
                    progress_callback=lambda p, s: progress_callback(
                        int(base_progress + (p / total_models)),
                        s
                    ) if progress_callback else None
                )
                results[model_name] = {'success': True, **model_result}

            except Exception as e:
                results[model_name] = {'success': False, 'error': str(e)}
                self.logger.warning(f"Failed to pull {model_name}: {e}")

        if progress_callback:
            progress_callback(100, "All data pulled")

        return results
