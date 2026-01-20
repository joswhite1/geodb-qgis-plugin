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
    'Photo',  # Field photos with GPS coordinates
    'ProjectFile',  # GeoTIFFs, DEMs, and other raster files
    'FieldNote',  # Field notes with attached photos
    'Structure',  # Surface geological structures (bedding, faults, etc.)
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
        status_filter: Optional[str] = None,
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
            status_filter: Optional status filter (e.g., 'CO' for collected only, 'PL,AS' for planned/assigned)
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

        # Special handling for FieldNote (creates two layers: notes + photos)
        if model_name == 'FieldNote':
            return self._pull_fieldnote_model(project, progress_callback)

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

            # Add status filter for PointSample
            # When pulling with assay config (merge_assays=true), default to collected only
            # This excludes planned/assigned samples from assay-colored layers
            if model_name == 'PointSample':
                if status_filter:
                    params['status'] = status_filter
                elif merge_assays and assay_config_id:
                    # Default to collected samples only for assay visualization
                    params['status'] = 'CO'

            # Filter Photos to only include those with GPS coordinates
            # Photos without geometry are typically linked to drill collars
            # and should be accessed through those models instead
            if model_name == 'Photo':
                params['has_geometry'] = 'true'

            # Add deleted_since parameter for incremental deletion sync
            # This tells the server to only return records deleted since our last sync
            last_sync_timestamp = self.sync_manager.get_sync_timestamp(model_name)
            if last_sync_timestamp and incremental:
                params['deleted_since'] = last_sync_timestamp

            # Pull data from API using RESTful endpoint
            # Response includes: results, deleted_ids, sync_timestamp, deleted_since_applied
            api_response = self.api_client.get_all_paginated(
                model_name=model_name,
                project_id=project.id,
                params=params if params else None,
                progress_callback=lambda p: progress_callback(10 + int(p * 0.3), "Downloading...") if progress_callback else None
            )

            # Extract results and deletion sync metadata
            features = api_response.get('results', [])
            deleted_ids = api_response.get('deleted_ids', [])
            sync_timestamp = api_response.get('sync_timestamp')

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
                project_name=project.name,
                company_name=project.company_name,
                api_client=self.api_client,
                project_id=project.id,
                crs_metadata=self._get_coordinate_system_metadata()
            )

            if progress_callback:
                progress_callback(90, "Updating sync metadata...")

            # Process deleted records from server
            # The API returns deleted_ids for records that were soft-deleted on the server
            # We need to remove these from our local layer
            deleted_count = 0
            conflicts = []
            skipped_ids = []

            if deleted_ids:
                self.logger.info(f"Processing {len(deleted_ids)} deleted records for {model_name}")
                layer = result.get('layer')
                if layer:
                    # Check for conflicts: local changes vs server deletion
                    # A conflict occurs when user has modified a record locally,
                    # but that record was deleted by another user on the server
                    conflicts = self.sync_manager.detect_deletion_conflicts(
                        layer=layer,
                        server_ids=deleted_ids,
                        model_name=model_name
                    )

                    if conflicts:
                        # Log conflicts - these records will NOT be deleted
                        # The user's local changes are preserved
                        skipped_ids = [c['server_id'] for c in conflicts]
                        conflict_names = [c['name'] for c in conflicts]
                        self.logger.warning(
                            f"Deletion conflict detected: {len(conflicts)} records have local changes "
                            f"but were deleted on server. Preserving local copies: {conflict_names}"
                        )

                    # Remove deleted features, skipping conflicting ones
                    deleted_count = self.sync_manager.remove_features_by_server_id(
                        layer=layer,
                        server_ids=deleted_ids,
                        skip_ids=skipped_ids
                    )
                    self.logger.info(f"Removed {deleted_count} deleted features from layer")

                    # For drill-related parent models, handle orphan cleanup
                    # When a DrillCollar is deleted, its child records should also be removed
                    # But skip orphan cleanup for collars that had conflicts
                    if model_name == 'DrillCollar' and deleted_count > 0:
                        # Only cleanup orphans for collars that were actually deleted
                        deleted_collar_ids = [
                            id for id in deleted_ids if id not in skipped_ids
                        ]
                        if deleted_collar_ids:
                            self._cleanup_orphaned_drill_children(
                                deleted_collar_ids=deleted_collar_ids,
                                project_name=project.name
                            )

            # Update sync timestamp for next incremental sync
            # Use the server-provided sync_timestamp if available
            if sync_timestamp:
                self.sync_manager.set_sync_timestamp(model_name, sync_timestamp)
            else:
                self.sync_manager.set_last_sync_time(model_name, datetime.now().isoformat())

            # Configure landholding-specific widgets
            if model_name == 'LandHolding':
                layer = result.get('layer')
                if layer and self.project_manager.active_company:
                    self.configure_landholding_widgets(layer, self.project_manager.active_company.id)

            if progress_callback:
                progress_callback(100, "Pull complete")

            self.logger.info(
                f"Pull complete for {model_name}: {len(features)} records pulled, "
                f"{deleted_count} deleted, {len(conflicts)} conflicts preserved"
            )
            return {
                'pulled': len(features),
                'deleted': deleted_count,
                'conflicts': conflicts,
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

            # Push each feature using upsert (server handles create vs update)
            created = 0
            updated = 0
            errors = []

            # Get schema for filtering push data (including custom fields if available)
            from ..models.schemas import get_extended_schema
            schema = get_extended_schema(self.api_client, model_name, project.id)

            # Get layer metadata once (for project and CRS info)
            layer = self.sync_manager._find_layer(model_name, project.name if project else None)
            layer_metadata = None
            if layer:
                layer_metadata = self.sync_manager.layer_processor.get_layer_project_metadata(layer)

            for i, feature in enumerate(features):
                try:
                    # Filter feature data to only include fields accepted by API
                    push_data = feature
                    if schema:
                        push_data = schema.filter_for_push(feature)

                    # Ensure project natural key is set
                    # The server uses this to look up the record for upsert
                    project_value = push_data.get('project')
                    needs_project = (
                        not project_value or
                        project_value is None or
                        project_value == '' or
                        project_value == 'NULL'
                    )

                    if needs_project:
                        if layer_metadata and layer_metadata.get('project_natural_key'):
                            # Use metadata stored in layer (most reliable)
                            push_data['project'] = layer_metadata['project_natural_key']
                        elif project:
                            # Fallback to active project
                            push_data['project'] = {
                                'name': project.name,
                                'company': project.company_name
                            }

                    # Ensure coordinate_system_metadata is set
                    if not push_data.get('coordinate_system_metadata'):
                        if layer_metadata and layer_metadata.get('crs_metadata'):
                            push_data['coordinate_system_metadata'] = layer_metadata['crs_metadata']
                        else:
                            push_data['coordinate_system_metadata'] = self._get_coordinate_system_metadata()

                    # Handle model-specific required fields
                    self._populate_required_fields(model_name, push_data, is_new_feature=True)

                    # Use upsert - server determines create vs update by natural key
                    result = self.api_client.upsert_record(
                        model_name=model_name,
                        data=push_data
                    )

                    # Track create vs update based on server response
                    if result.get('_status') == 'created':
                        created += 1
                        self.logger.info(f"Created {model_name}: {feature.get('name')}")
                    else:
                        # Default to updated if status not specified
                        updated += 1
                        self.logger.info(f"Updated {model_name}: {feature.get('name')}")

                except Exception as e:
                    errors.append({'feature': feature.get('name', 'unknown'), 'error': str(e)})
                    self.logger.error(f"Failed to push {model_name} '{feature.get('name', 'unknown')}': {e}")

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

    def delete_record(
        self,
        model_name: str,
        record_id: int,
        progress_callback: Optional[Callable[[int, str], None]] = None
    ) -> Dict[str, Any]:
        """
        Delete a record on the server (soft delete).

        This sends a DELETE request to the server, which will soft-delete the record
        (set mark_deleted=True). The record will be removed from local layers during
        the next sync when its ID appears in deleted_ids.

        Args:
            model_name: Model name (e.g., 'DrillCollar', 'LandHolding')
            record_id: Server ID of the record to delete
            progress_callback: Optional callback(progress_percent, status_message)

        Returns:
            Dictionary with deletion result
        """
        self.logger.info(f"Deleting {model_name} record {record_id}")

        # Check if project is selected
        project = self.project_manager.get_active_project()
        if not project:
            raise ValueError("No project selected")

        # Check permissions
        if not self.project_manager.can_edit():
            raise PermissionError("No permission to edit data")

        # Validate model name
        if model_name not in SUPPORTED_MODELS:
            raise ValueError(f"Unsupported model: {model_name}")

        try:
            if progress_callback:
                progress_callback(10, f"Deleting {model_name} record...")

            # Send DELETE request to server
            self.api_client.delete_record(model_name=model_name, record_id=record_id)

            if progress_callback:
                progress_callback(50, "Record deleted on server")

            # Remove from local layer
            layer = self.sync_manager._find_layer(model_name, project.name)
            if layer:
                removed = self.sync_manager.remove_features_by_server_id(
                    layer=layer,
                    server_ids=[record_id]
                )
                self.logger.info(f"Removed {removed} feature(s) from local layer")

            if progress_callback:
                progress_callback(100, "Delete complete")

            self.logger.info(f"Delete complete for {model_name} record {record_id}")
            return {
                'deleted': True,
                'model': model_name,
                'record_id': record_id
            }

        except Exception as e:
            self.logger.error(f"Delete failed for {model_name} record {record_id}: {e}")
            raise

    def queue_deletion(
        self,
        model_name: str,
        record_id: int,
        record_name: Optional[str] = None
    ) -> bool:
        """
        Queue a record for deletion during the next sync.

        Use this for offline scenarios or when you want to batch deletions.
        The queued deletions will be processed during push_model_data.

        Args:
            model_name: Model name
            record_id: Server ID of the record to delete
            record_name: Optional human-readable name for logging

        Returns:
            True if successfully queued
        """
        return self.sync_manager.queue_deletion(model_name, record_id, record_name)

    def process_deletion_queue(
        self,
        model_name: Optional[str] = None,
        progress_callback: Optional[Callable[[int, str], None]] = None
    ) -> Dict[str, Any]:
        """
        Process all queued deletions, sending them to the server.

        Args:
            model_name: Optional filter to process only one model's deletions
            progress_callback: Optional callback(progress_percent, status_message)

        Returns:
            Dictionary with results: deleted count, errors, etc.
        """
        self.logger.info(f"Processing deletion queue for {model_name or 'all models'}")

        # Check permissions
        if not self.project_manager.can_edit():
            raise PermissionError("No permission to edit data")

        # Get queued deletions
        queue = self.sync_manager.get_deletion_queue(model_name)
        if not queue:
            self.logger.info("No deletions in queue")
            return {'deleted': 0, 'errors': 0, 'error_details': []}

        deleted = 0
        errors = []

        for i, item in enumerate(queue):
            item_model = item['model_name']
            item_id = item['record_id']
            item_name = item.get('record_name', f'{item_model}:{item_id}')

            try:
                if progress_callback:
                    progress = int((i / len(queue)) * 100)
                    progress_callback(progress, f"Deleting {item_name}...")

                self.api_client.delete_record(model_name=item_model, record_id=item_id)
                deleted += 1

                # Remove from queue after successful delete
                self.sync_manager.remove_from_deletion_queue(item_model, item_id)

                self.logger.info(f"Deleted {item_name} from server")

            except Exception as e:
                error_msg = str(e)
                errors.append({'model': item_model, 'id': item_id, 'error': error_msg})
                self.logger.error(f"Failed to delete {item_name}: {error_msg}")

                # If record not found (404), remove from queue anyway
                if '404' in error_msg or 'not found' in error_msg.lower():
                    self.sync_manager.remove_from_deletion_queue(item_model, item_id)

        if progress_callback:
            progress_callback(100, f"Deleted {deleted} records")

        return {
            'deleted': deleted,
            'errors': len(errors),
            'error_details': errors if errors else None
        }

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

        IMPORTANT: trace_data.coords contains LOCAL GRID coordinates (e.g., UTM),
        NOT WGS84 lat/lon. Using them directly would result in wildly incorrect
        geometries (e.g., longitude=500000 degrees). We must only use the
        pre-transformed geometry_wgs84 from the API.

        Args:
            features: List of trace feature dictionaries from API

        Returns:
            Features with 'geometry' field populated from geometry_wgs84
        """
        valid_features = []
        skipped_count = 0

        for feature in features:
            # Use pre-transformed WGS84 geometry from API
            wgs84_wkt = feature.get('geometry_wgs84')
            if wgs84_wkt:
                feature['geometry'] = wgs84_wkt
                valid_features.append(feature)
            else:
                # Cannot use trace_data.coords - they are LOCAL GRID coordinates
                # (e.g., UTM eastings/northings like 500000, 5400000) and would
                # display incorrectly if interpreted as WGS84 lat/lon.
                # Skip traces that don't have WGS84 geometry.
                collar_name = feature.get('collar_name', 'unknown')
                needs_recalc = feature.get('needs_recalculation', False)
                if needs_recalc:
                    self.logger.warning(
                        f"Trace '{collar_name}' needs recalculation on server - skipping"
                    )
                else:
                    self.logger.warning(
                        f"Trace '{collar_name}' missing geometry_wgs84 - skipping. "
                        "Trace may need recalculation on the server."
                    )
                skipped_count += 1

        if skipped_count > 0:
            self.logger.info(
                f"Skipped {skipped_count} traces without WGS84 geometry. "
                "These traces may need recalculation on the geodb.io server."
            )

        return valid_features

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

        Now also handles georeferenced sketches from the mobile app:
        - GL category files with georeferencing data are pulled and displayed
        - World files are generated from georeferencing bounds

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
            # We need both:
            # 1. Traditional rasters (is_raster=true)
            # 2. Georeferenced sketches (GL category with georeferencing field)
            # The API doesn't support OR queries easily, so we fetch all and filter client-side
            # Actually, we fetch all files and let the raster_processor filter based on
            # is_raster=true OR (category='GL' AND georeferencing is not null)

            # Fetch all project files for this project
            # Use include_deletion_metadata=False for raster pulls since we just need the list
            response = self.api_client.get_all_paginated(
                model_name=model_name,
                project_id=project.id,
                params={},  # No filter - get all files
                progress_callback=lambda p: progress_callback(
                    5 + int(p * 0.15), "Fetching file list..."
                ) if progress_callback else None,
                include_deletion_metadata=False
            )

            # Handle both dict (with 'results' key) and list responses for backwards compatibility
            if isinstance(response, dict):
                files = response.get('results', [])
            else:
                files = response if response else []

            if not files:
                self.logger.info(f"No project files found for project {project.name}")
                if progress_callback:
                    progress_callback(100, "No project files found")
                return {
                    'pulled': 0,
                    'model': model_name,
                    'loaded': 0,
                    'skipped': 0,
                    'errors': []
                }

            # Filter to loadable files (rasters + georeferenced sketches + tiled rasters)
            # This is done in process_project_files, but we can pre-filter here for accurate counting
            loadable_files = [
                f for f in files
                if (f.get('is_raster', False) or
                    f.get('georeferencing') or
                    (f.get('tiles_available', False) and f.get('tiles_status') == 'completed'))
            ]

            # Debug: Log all files and their georeferencing/tile status
            for f in files:
                self.logger.info(
                    f"  File '{f.get('name')}' (id={f.get('id')}): "
                    f"is_raster={f.get('is_raster')}, category={f.get('category')}, "
                    f"georeferencing={f.get('georeferencing')}, "
                    f"tiles_available={f.get('tiles_available')}, tiles_status={f.get('tiles_status')}"
                )

            self.logger.info(
                f"Found {len(files)} total files, {len(loadable_files)} are loadable "
                f"(rasters, georeferenced sketches, or XYZ tile layers)"
            )

            if not loadable_files:
                self.logger.info(f"No loadable files found for project {project.name}")
                if progress_callback:
                    progress_callback(100, "No raster or sketch files found")
                return {
                    'pulled': 0,
                    'model': model_name,
                    'loaded': 0,
                    'skipped': len(files),
                    'errors': []
                }

            if progress_callback:
                progress_callback(20, f"Found {len(loadable_files)} loadable files. Downloading...")

            # Initialize raster processor with base URL for local development
            # (handles relative URLs from local server)
            base_url = self.config.get('api.base_url') if self.config.get('api.use_local', False) else None
            raster_processor = RasterProcessor(base_url=base_url)

            # Process files - download and load as layers
            # Pass all files; the processor will skip non-loadable ones
            # Also pass auth token for XYZ tile layer authentication
            result = raster_processor.process_project_files(
                project_files=files,
                project_name=project.name,
                progress_callback=lambda p, s: progress_callback(
                    20 + int(p * 0.75), s
                ) if progress_callback else None,
                auth_token=self.api_client.token
            )

            # Update sync metadata
            self.sync_manager.set_last_sync_time(model_name, datetime.now().isoformat())

            # Get counts (tiled may not be present in older results)
            loaded_count = result.get('loaded', 0)
            tiled_count = result.get('tiled', 0)
            total_loaded = loaded_count + tiled_count

            if progress_callback:
                if tiled_count > 0:
                    progress_callback(100, f"Loaded {loaded_count} rasters + {tiled_count} XYZ tile layers")
                else:
                    progress_callback(100, f"Loaded {total_loaded} raster layers")

            self.logger.info(
                f"Raster pull complete: {loaded_count} downloaded, {tiled_count} tiled, "
                f"{result['skipped']} skipped, {len(result['errors'])} errors"
            )

            return {
                'pulled': len(loadable_files),
                'model': model_name,
                'loaded': loaded_count,
                'tiled': tiled_count,
                'skipped': result['skipped'],
                'errors': result['errors'],
                'layers': result['layers']
            }

        except Exception as e:
            self.logger.error(f"Failed to pull raster model {model_name}: {e}")
            raise

    def _pull_fieldnote_model(
        self,
        project,
        progress_callback: Optional[Callable[[int, str], None]] = None
    ) -> Dict[str, Any]:
        """
        Pull field notes and create two layers:
        1. FieldNotes - main note layer
        2. FieldNotePhotos - photo layer with click-to-view

        The photos layer shows photos at their GPS coordinates (which may differ
        from the field note location) and supports click-to-view functionality.

        Args:
            project: Active project object
            progress_callback: Optional callback(progress_percent, status_message)

        Returns:
            Dictionary with sync results including both layers
        """
        self.logger.info("Pulling FieldNote model with two-layer architecture")

        try:
            if progress_callback:
                progress_callback(5, "Fetching field notes from server...")

            # Pull data from API
            features = self.api_client.get_all_paginated(
                model_name='FieldNote',
                project_id=project.id,
                params={},
                progress_callback=lambda p: progress_callback(
                    5 + int(p * 0.25), "Downloading field notes..."
                ) if progress_callback else None
            )

            if not features:
                self.logger.info(f"No field notes found for project {project.name}")
                if progress_callback:
                    progress_callback(100, "No field notes found")
                return {
                    'pulled': 0,
                    'model': 'FieldNote',
                    'notes_layer': None,
                    'photos_layer': None,
                    'photo_count': 0
                }

            if progress_callback:
                progress_callback(30, f"Processing {len(features)} field notes...")

            # Separate notes and photos data
            notes_features = []
            photos_features = []

            for note in features:
                # Count photos for this note
                images = note.get('images', [])
                note['photo_count'] = len(images)

                # Add to notes layer
                notes_features.append(note)

                # Extract photos and add to photos layer
                for photo in images:
                    # Use photo's coordinates if available, else note's
                    photo_lat = photo.get('latitude') or note.get('latitude')
                    photo_lon = photo.get('longitude') or note.get('longitude')
                    photo_elev = photo.get('elevation') or note.get('elevation')

                    photo_feature = {
                        'id': f"{note.get('id')}_{photo.get('id')}",  # Composite ID
                        'photo_id': photo.get('id'),
                        'fieldnote_id': note.get('id'),
                        'fieldnote_uuid': note.get('uuid'),
                        'fieldnote_name': note.get('name'),
                        'latitude': photo_lat,
                        'longitude': photo_lon,
                        'elevation': photo_elev,
                        'image_url': photo.get('image_url'),
                        'thumbnail_url': photo.get('thumbnail_url'),
                        'original_filename': photo.get('original_filename', ''),
                        'description': photo.get('description', ''),
                    }
                    photos_features.append(photo_feature)

            self.logger.info(
                f"Processed {len(notes_features)} notes with {len(photos_features)} photos"
            )

            if progress_callback:
                progress_callback(40, f"Creating field notes layer ({len(notes_features)} notes)...")

            # Create the main notes layer
            notes_result = self.sync_manager.sync_pull_to_layer(
                model_name='FieldNote',
                features=notes_features,
                progress_callback=lambda p: progress_callback(
                    40 + int(p * 0.25), "Creating notes layer..."
                ) if progress_callback else None,
                project_name=project.name,
                company_name=project.company_name,
                api_client=self.api_client,
                project_id=project.id,
                crs_metadata=self._get_coordinate_system_metadata()
            )

            notes_layer = notes_result.get('layer')

            # Create the photos layer if there are any photos
            photos_layer = None
            if photos_features:
                if progress_callback:
                    progress_callback(70, f"Creating photos layer ({len(photos_features)} photos)...")

                photos_result = self.sync_manager.sync_pull_to_layer(
                    model_name='FieldNotePhoto',
                    features=photos_features,
                    progress_callback=lambda p: progress_callback(
                        70 + int(p * 0.25), "Creating photos layer..."
                    ) if progress_callback else None,
                    project_name=project.name,
                    company_name=project.company_name,
                    api_client=self.api_client,
                    project_id=project.id,
                    crs_metadata=self._get_coordinate_system_metadata()
                )
                photos_layer = photos_result.get('layer')

            # Update sync metadata
            self.sync_manager.set_last_sync_time('FieldNote', datetime.now().isoformat())

            if progress_callback:
                progress_callback(100, f"Loaded {len(notes_features)} notes with {len(photos_features)} photos")

            self.logger.info(
                f"FieldNote pull complete: {len(notes_features)} notes, {len(photos_features)} photos"
            )

            return {
                'pulled': len(notes_features),
                'model': 'FieldNote',
                'notes_layer': notes_layer,
                'photos_layer': photos_layer,
                'photo_count': len(photos_features),
                'added': notes_result.get('added', 0),
                'updated': notes_result.get('updated', 0),
                'layer': notes_layer  # For compatibility with standard result format
            }

        except Exception as e:
            self.logger.error(f"Failed to pull FieldNote model: {e}")
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

    def _get_coordinate_system_metadata(self) -> dict:
        """
        Get coordinate system metadata from the active project.

        Returns:
            Dict with CRS metadata for the CoordinateSystemValidatorMixin
        """
        project = self.project_manager.get_active_project()
        if not project:
            # Return default WGS84 metadata when no project
            return {
                'crs_epsg': 4326,
                'origin_x': 0.0,
                'origin_y': 0.0,
                'rotation_degrees': 0.0
            }

        # Build metadata from project's local_grid settings
        # API requires: origin_x, origin_y, crs_epsg, rotation_degrees
        metadata = {
            'crs_epsg': 4326,  # Default to WGS84
            'origin_x': 0.0,
            'origin_y': 0.0,
            'rotation_degrees': 0.0
        }

        if hasattr(project, 'crs') and project.crs:
            try:
                metadata['crs_epsg'] = int(project.crs)
            except (ValueError, TypeError):
                pass

        if hasattr(project, 'local_grid') and project.local_grid:
            lg = project.local_grid
            if lg.get('origin_x') is not None:
                metadata['origin_x'] = lg['origin_x']
            if lg.get('origin_y') is not None:
                metadata['origin_y'] = lg['origin_y']
            if lg.get('rotation_degrees') is not None:
                metadata['rotation_degrees'] = lg['rotation_degrees']
            if lg.get('epsg') is not None:
                metadata['crs_epsg'] = lg['epsg']

        if hasattr(project, 'proj4_string') and project.proj4_string:
            metadata['proj4_string'] = project.proj4_string

        return metadata

    def _populate_required_fields(
        self, model_name: str, push_data: dict, is_new_feature: bool = True
    ) -> None:
        """
        Populate required fields for a model before pushing to API.

        This handles model-specific required fields that may be NULL when creating
        new features in QGIS. Each model type has different requirements:

        - LandHolding: 'dropped' cannot be NULL (default False)
        - DrillPad: 'status' defaults to 'planned' if not set
        - DrillCollar: coordinates validated together (handled by API)
        - PointSample: coordinates validated together (handled by API)

        For NEW features, we also clean up NULL values for optional fields to
        avoid validation errors. For UPDATES, we preserve all values (including
        NULL) to allow unsetting fields via PATCH.

        Args:
            model_name: Name of the model being pushed
            push_data: Data dictionary to be modified in-place
            is_new_feature: True if creating a new record, False if updating
        """
        def is_null_or_empty(value):
            """Check if a value is effectively NULL/empty."""
            return value is None or value == '' or str(value) == 'NULL'

        # LandHolding: 'dropped' field cannot be NULL
        if model_name == 'LandHolding':
            if is_null_or_empty(push_data.get('dropped')):
                push_data['dropped'] = False

        # DrillPad: 'status' defaults to 'planned' if not set
        elif model_name == 'DrillPad':
            if is_null_or_empty(push_data.get('status')):
                push_data['status'] = 'planned'

        # For NEW features only: Remove NULL values for optional fields
        # This keeps the payload clean and avoids some validation edge cases
        # For UPDATES, we preserve all values to allow explicit NULL via PATCH
        if is_new_feature:
            keys_to_remove = []
            for key, value in push_data.items():
                # Skip required fields - let API validate these
                if key in ('name', 'project', 'bhid'):
                    continue
                # Skip ID field
                if key == 'id':
                    continue
                # Skip geometry - even if NULL, needed for some models
                if key == 'geometry':
                    continue
                # Remove NULL optional fields to keep payload clean
                if is_null_or_empty(value):
                    keys_to_remove.append(key)

            for key in keys_to_remove:
                del push_data[key]

        # For Point-based models (PointSample, DrillCollar), extract lat/lon from geometry
        # The API expects latitude, longitude, elevation, epsg - not geometry field
        if model_name in ('PointSample', 'DrillCollar'):
            self._extract_coordinates_from_geometry(push_data)

    def _extract_coordinates_from_geometry(self, push_data: dict) -> None:
        """
        Extract latitude, longitude, elevation, and EPSG from EWKT geometry.

        The API expects coordinates as separate fields (latitude, longitude, elevation, epsg)
        rather than a geometry field. This method parses EWKT format and populates those fields.

        EWKT format examples:
        - SRID=4326;Point Z (-116.219452 48.566896 1453.6)
        - SRID=4326;Point (-116.219452 48.566896)
        - Point Z (-116.219452 48.566896 1453.6)

        Args:
            push_data: Data dictionary to be modified in-place
        """
        import re

        geometry = push_data.get('geometry')
        if not geometry or not isinstance(geometry, str):
            return

        # Parse SRID if present: SRID=4326;Point...
        srid = None
        geom_part = geometry
        if geometry.upper().startswith('SRID='):
            match = re.match(r'SRID=(\d+);(.+)', geometry, re.IGNORECASE)
            if match:
                srid = int(match.group(1))
                geom_part = match.group(2)

        # Parse Point coordinates: Point Z (lon lat elev) or Point (lon lat)
        # Note: WKT uses (X Y Z) order which is (longitude latitude elevation)
        point_match = re.match(
            r'Point\s*Z?\s*\(\s*([+-]?\d+\.?\d*)\s+([+-]?\d+\.?\d*)\s*([+-]?\d+\.?\d*)?\s*\)',
            geom_part,
            re.IGNORECASE
        )

        if point_match:
            lon = float(point_match.group(1))
            lat = float(point_match.group(2))
            elev = float(point_match.group(3)) if point_match.group(3) else None

            # Update push_data with extracted coordinates
            push_data['longitude'] = lon
            push_data['latitude'] = lat
            if elev is not None:
                push_data['elevation'] = elev
            if srid is not None:
                push_data['epsg'] = srid

            # Remove geometry field - API doesn't accept it for writes
            del push_data['geometry']

            self.logger.debug(
                f"Extracted coordinates from geometry: "
                f"lat={lat}, lon={lon}, elev={elev}, epsg={srid}"
            )

    def _cleanup_orphaned_drill_children(
        self,
        deleted_collar_ids: List[int],
        project_name: str
    ) -> None:
        """
        Remove orphaned child records when their parent DrillCollar is deleted.

        When a DrillCollar is soft-deleted on the server, we should also remove
        all its child records from local layers (lithology, alteration, samples, etc.)
        to prevent "ghost" data from appearing without their parent.

        Args:
            deleted_collar_ids: List of deleted DrillCollar server IDs
            project_name: Project name for finding layers
        """
        # Child models that reference DrillCollar via collar_id or collar field
        DRILL_CHILD_MODELS = [
            'DrillLithology',
            'DrillAlteration',
            'DrillMineralization',
            'DrillStructure',
            'DrillSample',
            'DrillSurvey',
            'DrillPhoto',
            'DrillTrace',
        ]

        if not deleted_collar_ids:
            return

        self.logger.info(
            f"Cleaning up orphaned drill children for {len(deleted_collar_ids)} deleted collars"
        )

        total_removed = 0
        for child_model in DRILL_CHILD_MODELS:
            # Find the layer for this child model
            layer = self.sync_manager._find_layer(child_model, project_name)
            if not layer:
                continue

            # Find features that reference the deleted collar IDs
            # Child models typically have a 'collar' or 'collar_id' field
            collar_field = None
            for field_name in ['collar', 'collar_id']:
                field_idx = layer.fields().indexOf(field_name)
                if field_idx >= 0:
                    collar_field = field_name
                    break

            if not collar_field:
                self.logger.debug(f"No collar field found in {child_model} layer")
                continue

            # Build expression to find orphaned features
            # The collar field might contain an ID (int) or a natural key (dict/string)
            # For simplicity, we'll iterate and check manually
            features_to_delete = []
            for feature in layer.getFeatures():
                collar_value = feature[collar_field]
                # Handle both integer ID and potential dict/string representations
                if collar_value in deleted_collar_ids:
                    features_to_delete.append(feature.id())
                elif isinstance(collar_value, str):
                    # Try to parse as integer
                    try:
                        if int(collar_value) in deleted_collar_ids:
                            features_to_delete.append(feature.id())
                    except (ValueError, TypeError):
                        pass

            if features_to_delete:
                layer.startEditing()
                layer.deleteFeatures(features_to_delete)
                layer.commitChanges()
                total_removed += len(features_to_delete)
                self.logger.info(
                    f"Removed {len(features_to_delete)} orphaned {child_model} records"
                )

        self.logger.info(f"Total orphaned drill children removed: {total_removed}")

    # =========================================================================
    # FIELD WORK PLANNING METHODS
    # =========================================================================

    def pull_field_tasks(
        self,
        ps_type_id: Optional[int] = None,
        ps_type_name: Optional[str] = None,
        progress_callback: Optional[Callable[[int, str], None]] = None
    ) -> Dict[str, Any]:
        """
        Pull planned and assigned PointSamples as field tasks.

        This creates a separate layer for field tasks, styled by status.
        Unlike regular PointSample pulls (which get collected samples for assay viz),
        this specifically pulls samples that are:
        - PL (Planned) - ready to be assigned
        - AS (Assigned) - assigned to field workers

        Args:
            ps_type_id: Optional PointSampleType ID to filter by
            ps_type_name: Optional PointSampleType name for layer naming
            progress_callback: Optional callback(progress_percent, status_message)

        Returns:
            Dictionary with sync results
        """
        self.logger.info("Pulling field tasks (planned/assigned samples)")

        # Check if project is selected
        project = self.project_manager.get_active_project()
        if not project:
            raise ValueError("No project selected")

        # Check permissions
        if not self.project_manager.can_view():
            raise PermissionError("No permission to view data")

        try:
            if progress_callback:
                progress_callback(10, "Fetching field tasks from server...")

            # Build params for field tasks (planned and assigned only)
            # Server expects 'status' param with comma-separated values
            params = {
                'status': 'PL,AS',  # Only planned and assigned
            }

            # Add sample type filter if specified
            if ps_type_id:
                params['ps_type_id'] = str(ps_type_id)

            # Pull data from API
            api_response = self.api_client.get_all_paginated(
                model_name='PointSample',
                project_id=project.id,
                params=params,
                progress_callback=lambda p: progress_callback(10 + int(p * 0.3), "Downloading...") if progress_callback else None
            )

            # Extract results (ignore deleted_ids for field tasks - they're filtered by status)
            features = api_response.get('results', [])

            if progress_callback:
                progress_callback(40, f"Processing {len(features)} field tasks...")

            # Build layer name for field tasks
            # e.g., "ProjectName_FieldTasks" or "ProjectName_FieldTasks_Soil"
            effective_model_name = "FieldTasks"
            if ps_type_name:
                effective_model_name = f"FieldTasks_{ps_type_name.replace(' ', '')}"

            if progress_callback:
                progress_callback(50, f"Syncing {len(features)} field tasks to QGIS layer...")

            # Sync to QGIS layer with project name prefix
            result = self.sync_manager.sync_pull_to_layer(
                model_name=effective_model_name,
                features=features,
                progress_callback=lambda p: progress_callback(50 + int(p * 0.4), "Syncing...") if progress_callback else None,
                project_name=project.name,
                company_name=project.company_name,
                api_client=self.api_client,
                project_id=project.id,
                crs_metadata=self._get_coordinate_system_metadata(),
                base_schema_name='PointSample'  # Use PointSample schema for field mapping
            )

            if progress_callback:
                progress_callback(95, "Field tasks layer created")

            self.logger.info(f"Field tasks pull complete: {len(features)} records")
            return {
                'pulled': len(features),
                'model': 'FieldTasks',
                **result
            }

        except Exception as e:
            self.logger.error(f"Pull field tasks failed: {e}")
            raise

    def push_planned_samples(
        self,
        source_layer,
        prefix: str,
        start_number: int,
        padding: int,
        sample_type: str,
        progress_callback: Optional[Callable[[int, str], None]] = None
    ) -> Dict[str, Any]:
        """
        Push points from any QGIS layer as planned PointSample records.

        This creates "Planned" samples that can be assigned to field workers
        via the geodb.io dashboard. The samples have:
        - status = 'PL' (Planned)
        - sequence_number = generated ID (e.g., SS-001)
        - target_latitude/longitude = point coordinates (where to go)
        - name = NULL (lab sample ID not yet known)
        - latitude/longitude = NULL (actual coords set in field)

        Args:
            source_layer: Any point layer in QGIS (QgsVectorLayer)
            prefix: Sequence number prefix (e.g., "SS-")
            start_number: Starting sequence number (e.g., 1)
            padding: Zero-padding width (e.g., 3 for "001")
            sample_type: Sample type code (SL, RK, OC, etc.)
            progress_callback: Optional callback(progress_percent, status_message)

        Returns:
            Dict with 'created', 'errors', 'error_details'
        """
        from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject

        self.logger.info(f"Pushing planned samples from layer: {source_layer.name()}")

        # Validate inputs
        project = self.project_manager.get_active_project()
        if not project:
            raise ValueError("No project selected")

        if not self.project_manager.can_edit():
            raise PermissionError("No permission to edit data in this project")

        feature_count = source_layer.featureCount()
        if feature_count == 0:
            raise ValueError("Source layer has no features")

        try:
            if progress_callback:
                progress_callback(5, "Preparing coordinate transformation...")

            # Set up coordinate transform to WGS84 if needed
            source_crs = source_layer.crs()
            wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
            transform = None
            if source_crs != wgs84:
                transform = QgsCoordinateTransform(
                    source_crs, wgs84, QgsProject.instance()
                )
                self.logger.info(f"Will transform from {source_crs.authid()} to WGS84")

            # Get CRS metadata for tracking
            crs_metadata = self._get_coordinate_system_metadata()
            if source_crs.isValid():
                try:
                    crs_metadata['crs_epsg'] = int(source_crs.authid().split(':')[1])
                except (ValueError, IndexError):
                    pass

            # Build project natural key
            # IMPORTANT: Both name and company are required for the API's natural key lookup
            if not project.name:
                raise ValueError("Project name is missing")
            if not project.company_name:
                raise ValueError(
                    f"Project company_name is missing for project '{project.name}'. "
                    f"Project object: id={project.id}, company_id={project.company_id}"
                )

            project_nk = {
                'name': project.name,
                'company': project.company_name
            }
            self.logger.info(
                f"Project natural key: name='{project.name}', company='{project.company_name}'"
            )

            if progress_callback:
                progress_callback(10, f"Processing {feature_count} features...")

            # Build all sample records first
            samples_to_push = []
            prep_errors = []

            for i, feature in enumerate(source_layer.getFeatures()):
                try:
                    # Generate sequence number
                    seq_num = f"{prefix}{str(start_number + i).zfill(padding)}"

                    # Get geometry and transform to WGS84
                    geom = feature.geometry()
                    if geom.isEmpty():
                        raise ValueError("Feature has empty geometry")

                    point = geom.asPoint()
                    if transform:
                        point = transform.transform(point)

                    # Get elevation if available (Z coordinate)
                    elevation = None
                    if geom.constGet().is3D():
                        elevation = geom.constGet().z()

                    # Build sample data for API
                    # For planned samples, we send WGS84 coordinates (already transformed).
                    # Setting epsg=4326 tells the API these are WGS84 coordinates, which
                    # bypasses the CoordinateSystemValidatorMixin check (designed for
                    # round-trip data protection, not new externally-created data).
                    sample_data = {
                        'project': project_nk,
                        'status': 'PL',  # Planned
                        'sequence_number': seq_num,
                        'sample_type': sample_type,
                        # Target coordinates (where to go) - already in WGS84
                        'target_latitude': point.y(),
                        'target_longitude': point.x(),
                        'target_epsg': 4326,  # Always WGS84 after transform
                        # Setting epsg=4326 bypasses coordinate_system_metadata validation
                        # (see CoordinateSystemValidatorMixin._is_wgs84_coordinates)
                        'epsg': 4326,
                    }

                    if elevation is not None:
                        sample_data['target_elevation'] = elevation

                    samples_to_push.append(sample_data)

                except Exception as e:
                    seq_num = f"{prefix}{str(start_number + i).zfill(padding)}"
                    prep_errors.append({
                        'sequence_number': seq_num,
                        'error': str(e)
                    })
                    self.logger.error(f"Failed to prepare sample {seq_num}: {e}")

                # Progress update during preparation
                if progress_callback and (i + 1) % 100 == 0:
                    progress = 10 + int((i + 1) / feature_count * 20)
                    progress_callback(progress, f"Prepared {i + 1} of {feature_count} samples...")

            if progress_callback:
                progress_callback(30, f"Pushing {len(samples_to_push)} samples to server...")

            # Push in batches using bulk endpoint (much faster than individual requests)
            # Batch size of 500 balances between request size and server processing
            BATCH_SIZE = 500
            total_created = 0
            total_updated = 0
            api_errors = []

            for batch_start in range(0, len(samples_to_push), BATCH_SIZE):
                batch_end = min(batch_start + BATCH_SIZE, len(samples_to_push))
                batch = samples_to_push[batch_start:batch_end]

                try:
                    result = self.api_client.bulk_upsert_records(
                        model_name='PointSample',
                        records=batch
                    )

                    summary = result.get('summary', {})
                    total_created += summary.get('created', 0)
                    total_updated += summary.get('updated', 0)

                    # Collect any errors from this batch
                    batch_errors = result.get('errors', [])
                    if batch_errors:
                        api_errors.extend(batch_errors)

                    self.logger.info(
                        f"Batch {batch_start}-{batch_end}: "
                        f"created={summary.get('created', 0)}, "
                        f"updated={summary.get('updated', 0)}, "
                        f"errors={summary.get('errors', 0)}"
                    )

                except Exception as e:
                    # If bulk request fails entirely, record error for all items in batch
                    self.logger.error(f"Bulk push failed for batch {batch_start}-{batch_end}: {e}")
                    for item in batch:
                        api_errors.append({
                            'sequence_number': item.get('sequence_number'),
                            'error': str(e)
                        })

                # Progress update
                if progress_callback:
                    progress = 30 + int(batch_end / len(samples_to_push) * 65)
                    progress_callback(
                        progress,
                        f"Pushed {batch_end} of {len(samples_to_push)} samples..."
                    )

            if progress_callback:
                progress_callback(100, "Push complete")

            # Combine preparation errors and API errors
            all_errors = prep_errors + api_errors

            result = {
                'created': total_created,
                'updated': total_updated,
                'errors': len(all_errors),
                'error_details': all_errors if all_errors else None
            }

            self.logger.info(f"Planned samples push complete: {result}")
            return result

        except Exception as e:
            self.logger.error(f"Push planned samples failed: {e}")
            raise