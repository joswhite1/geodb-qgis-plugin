# -*- coding: utf-8 -*-
"""
Grid processing for claims management: a thin client of the geodb.io API.

- Numbering claims by position (``POST /api/v2/claims/order-claims/``): the
  server's rotation-tolerant row banding is the one numbering rule.
- Validating the grid (``POST /api/v2/claims/qc/``): the SAME quality check
  that gates document generation, so a block that validates here is a block
  the documents step accepts.
- Renaming, Manual_FID bookkeeping and FID resets: plain layer editing, local.

⛔ There is NO local fallback for numbering or validation. The local copies
(strict-sort / snake ordering, rectangularity / area / overlap validation)
were deleted 2026-09-29 (qgis-thin-client), for the reasons in
``processors/server_required.py``. Do not restore them.
"""
from typing import List, Dict, Any, Optional

from qgis.core import (
    QgsVectorLayer, QgsFeature, QgsGeometry, QgsField, QgsWkbTypes
)
from .server_required import require_server
from ..utils.logger import PluginLogger
from ..utils.compat import FieldType_QString, FieldType_Int


class GridProcessor:
    """
    Client of the server's grid processing. Numbering and validation need an
    ``api_client``; without one they refuse (``require_server``). Renaming
    and FID bookkeeping are local layer edits and work offline.
    """

    # Field name for manual FID
    MANUAL_FID_FIELD = 'Manual_FID'

    def __init__(self, api_client=None):
        """
        Initialize the grid processor.

        Args:
            api_client: APIClient instance. Numbering and validation refuse
                       without one; there is no local fallback.
        """
        self.api_client = api_client
        self.logger = PluginLogger.get_logger()

    def set_api_client(self, api_client):
        """Set the API client (the wizard refreshes it on every use)."""
        self.api_client = api_client

    def autopopulate_manual_fid(
        self,
        layer: QgsVectorLayer,
        direction: str = 'left_to_right_top_to_bottom',
        start_number: int = 1
    ) -> int:
        """
        Auto-assign sequential numbers to claims based on spatial position.

        Creates or updates a 'Manual_FID' field with sequential numbers
        based on the claims' geographic positions.

        Args:
            layer: Vector layer with claim polygons
            direction: How to order claims spatially:
                - 'left_to_right_top_to_bottom' (default): West to East, North to South
                - 'top_to_bottom_left_to_right': North to South, West to East
                - 'serpentine': Alternating direction per row
                - 'west_to_east_north_to_south': Same as left_to_right_top_to_bottom
                - 'north_to_south_west_to_east': Same as top_to_bottom_left_to_right
                - 'snake_horizontal': Same as serpentine
            start_number: Starting number for sequence (default 1)

        Returns:
            Number of claims numbered
        """
        if not layer or not layer.isValid():
            raise ValueError("Invalid layer")
        require_server(self.api_client, "Numbering claims")

        # Normalize direction names
        direction_map = {
            'west_to_east_north_to_south': 'left_to_right_top_to_bottom',
            'north_to_south_west_to_east': 'top_to_bottom_left_to_right',
            'snake_horizontal': 'serpentine',
        }
        sort_direction = direction_map.get(direction, direction)

        # Extract claim positions
        claims_data = self._extract_claims_data(layer)

        if not claims_data:
            return 0

        # The server's rotation-tolerant row banding is the one numbering
        # rule. An error SURFACES; there is no simpler local ordering to hide
        # behind (it misnumbered rotated blocks).
        ordered = self._order_claims_server(claims_data, sort_direction)

        # Apply ordering to layer
        return self._apply_ordering(layer, ordered, start_number)

    def _order_claims_server(
        self,
        claims_data: List[Dict[str, Any]],
        sort_direction: str
    ) -> List[Dict[str, Any]]:
        """Order claims using the server's canonical banding rule.

        The server (geodb GridValidator via ``order-claims/``) is the single
        source of truth for book-reading claim ordering — rotation-tolerant
        row banding. This client sends centroids only, with the local list
        index as the join key: layer names can be blank or duplicated before
        renaming, so names cannot key the response mapping.

        Raises on any error, empty, or mismatched response, and the caller
        shows it: a wrong number on a claim is worse than no number.

        Fixed 2026-07-11 (v2.25.0): this path was a silent no-op — it sent
        ``sort_direction`` where the endpoint reads ``direction`` and parsed
        ``ordered_claims`` where the endpoint returns ``claims``, so the
        empty result numbered nothing, no exception fired, and renaming fell
        back to feature-insertion order (the scrambled / right-to-left
        numbering seen in the field).
        """
        # Build API endpoint URL
        endpoint = self.api_client.config.get_claims_url('order-claims/')

        # Prepare data for API — index-as-name join key, centroids only
        api_claims = []
        for i, claim in enumerate(claims_data):
            api_claims.append({
                'name': str(i),
                'centroid': {
                    'easting': claim['centroid'].x(),
                    'northing': claim['centroid'].y()
                }
            })

        # Call server API
        response = self.api_client._make_request('POST', endpoint, data={
            'claims': api_claims,
            'direction': sort_direction
        })

        if 'error' in response:
            raise ValueError(response['error'])

        # Server returns {'claims': [...input claims + 'order'...], 'statistics': ...}
        # in INPUT order — the 'order' field carries the spatial rank.
        returned = response.get('claims') or []
        if len(returned) != len(claims_data):
            raise ValueError(
                f"order-claims returned {len(returned)} claims "
                f"for {len(claims_data)} sent"
            )

        ordered = []
        for item in returned:
            try:
                idx = int(item['name'])
                order = int(item['order'])
            except (KeyError, TypeError, ValueError):
                raise ValueError(f"order-claims response malformed: {item!r}")
            if not 0 <= idx < len(claims_data):
                raise ValueError(f"order-claims returned unknown claim index {idx}")
            ordered.append({**claims_data[idx], 'order': order})

        # _apply_ordering numbers by list position — sort into spatial rank
        ordered.sort(key=lambda c: c['order'])
        return ordered

    def rename_claims(
        self,
        layer: QgsVectorLayer,
        base_name: str,
        name_field: str = 'name',
        start_number: int = 1,
        use_manual_fid: bool = True,
        separator: str = ' '
    ) -> int:
        """
        Rename all claims using a base name and sequential numbers.

        Args:
            layer: Vector layer with claims
            base_name: Base name prefix (e.g., "GE" -> "GE 1", "GE 2")
            name_field: Field name to update (default: 'name')
            start_number: Starting number (default: 1)
            use_manual_fid: Use Manual_FID for numbering (if available)
            separator: Separator between name and number (default space)

        Returns:
            Number of claims renamed
        """
        if not layer or not layer.isValid():
            raise ValueError("Invalid layer")

        # Find field indices
        name_idx = layer.fields().indexOf(name_field)
        if name_idx < 0:
            # Try to add the field
            was_editing = layer.isEditable()
            if not was_editing:
                layer.startEditing()
            layer.addAttribute(QgsField(name_field, FieldType_QString, len=100))
            layer.updateFields()
            name_idx = layer.fields().indexOf(name_field)
            if name_idx < 0:
                if not was_editing:
                    layer.rollBack()
                raise ValueError(f"Could not create '{name_field}' field")
            if not was_editing:
                layer.commitChanges()

        manual_fid_idx = layer.fields().indexOf(self.MANUAL_FID_FIELD)

        # Start editing if not already
        was_editing = layer.isEditable()
        if not was_editing:
            layer.startEditing()

        renamed_count = 0

        # Get features and their ordering
        features_with_order = []
        for feature in layer.getFeatures():
            if use_manual_fid and manual_fid_idx >= 0:
                order = feature.attribute(manual_fid_idx)
                if order is None or order == '':
                    order = feature.id()
            else:
                order = feature.id()

            features_with_order.append((feature.id(), order))

        # Sort by order
        features_with_order.sort(key=lambda x: (x[1] if x[1] is not None else 999999))

        # Rename features
        for i, (fid, _) in enumerate(features_with_order, start_number):
            new_name = f"{base_name}{separator}{i}"
            layer.changeAttributeValue(fid, name_idx, new_name)
            renamed_count += 1

        # Commit changes
        if not was_editing:
            layer.commitChanges()

        # Update spatial index and refresh display
        # This ensures click detection works correctly after attribute changes
        layer.updateExtents()
        layer.triggerRepaint()

        self.logger.info(
            f"[GRID PROCESSOR] Renamed {renamed_count} claims with prefix '{base_name}'"
        )

        return renamed_count

    def validate_grid_geometry(
        self,
        layer: QgsVectorLayer,
        input_epsg: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Check the claim grid before processing.

        Runs the server's claims quality check (``POST /api/v2/claims/qc/``),
        the same check that gates document generation: corner count and
        rectangularity, self-intersection, overlaps and interior gaps,
        corners that should be shared but are not, lode dimension limits,
        and duplicate or skipped claim names.

        Features the plugin cannot send at all (no geometry, or not a single
        polygon) are reported here first, by name, rather than dropped.

        Args:
            layer: Vector layer with claim polygons
            input_epsg: EPSG of the layer's coordinates (defaults to the
                        layer CRS)

        Returns:
            List of ``{'name', 'issue', 'severity', 'code'}`` dicts, errors
            first; empty when the grid passes with no warnings. ``issue`` is a
            whole sentence that already names its claim(s); ``name`` is the
            claim(s) alone. ``severity`` is ``'error'`` or ``'warning'``.
        """
        if not layer or not layer.isValid():
            raise ValueError("Invalid layer")
        require_server(self.api_client, "Validating the claim grid")

        issues = []
        claims = []
        name_idx = layer.fields().indexOf('name')
        for feature in layer.getFeatures():
            name = feature.attribute(name_idx) if name_idx >= 0 else None
            name = name or f"Feature {feature.id()}"
            geom = feature.geometry()
            if geom is None or geom.isNull() or geom.isEmpty():
                issues.append({'name': name, 'issue': f"{name} has no geometry.",
                               'severity': 'error', 'code': 'NO_GEOMETRY'})
                continue
            polygon = geom.asPolygon()
            if (geom.type() != QgsWkbTypes.GeometryType.PolygonGeometry
                    or not polygon):
                issues.append({'name': name,
                               'issue': f"{name} is not a single polygon (it is "
                                        f"multi-part or not a polygon), so it "
                                        f"cannot be checked.",
                               'severity': 'error', 'code': 'NOT_A_POLYGON'})
                continue
            claims.append({
                'name': name,
                'corners': [{'easting': p.x(), 'northing': p.y()}
                            for p in polygon[0][:-1]],
            })

        if claims:
            epsg = input_epsg or layer.crs().postgisSrid() or None
            endpoint = self.api_client.config.get_claims_url('qc/')
            response = self.api_client._make_request('POST', endpoint, data={
                'claims': claims,
                'input_epsg': epsg,
            })
            if 'error' in response:
                raise ValueError(response['error'])
            for finding in response.get('findings', []):
                severity = finding.get('severity')
                if severity not in ('error', 'warning'):
                    continue  # 'info' notes are not issues
                named = finding.get('claims') or []
                issues.append({
                    'name': ', '.join(str(n) for n in named[:3])
                            + (f" (+{len(named) - 3} more)" if len(named) > 3 else ''),
                    'issue': finding.get('message', ''),
                    'severity': severity,
                    'code': finding.get('code', ''),
                })

        issues.sort(key=lambda i: 0 if i['severity'] == 'error' else 1)
        return issues

    def reset_fid_to_match_manual_fid(
        self,
        layer: QgsVectorLayer
    ) -> int:
        """
        Reset the feature FIDs to match the Manual_FID order.

        This is critical for claim document generation - documents are created
        in FID order, so FIDs must match the logical claim numbering.

        For GeoPackage layers, this rewrites the features in Manual_FID order,
        which causes FIDs to be reassigned sequentially.

        For memory layers, the features are reordered in place.

        Args:
            layer: Vector layer with Manual_FID field

        Returns:
            Number of features reordered

        Raises:
            ValueError: If layer is invalid or missing Manual_FID field
        """
        if not layer or not layer.isValid():
            raise ValueError("Invalid layer")

        field_idx = layer.fields().indexOf(self.MANUAL_FID_FIELD)
        if field_idx < 0:
            raise ValueError(
                f"Layer does not have {self.MANUAL_FID_FIELD} field. "
                "Run autopopulate_manual_fid first."
            )

        # Check for GeoPackage layer
        source = layer.source()

        # For GeoPackage layers, we need to rewrite the entire table
        if layer.dataProvider().name() == 'ogr' and '.gpkg' in source:
            return self._reset_fid_geopackage(layer)
        else:
            # For memory layers, reorder in place
            return self._reset_fid_memory(layer)

    def _reset_fid_geopackage(self, layer: QgsVectorLayer) -> int:
        """
        Reset FIDs for a GeoPackage layer to sequential 1, 2, 3... matching Manual_FID order.

        This approach keeps the same layer object in QGIS, avoiding rendering cache
        issues that occur when removing and re-adding layers. It resets SQLite's
        auto-increment sequence so new FIDs start from 1.

        Args:
            layer: GeoPackage vector layer

        Returns:
            Number of features reordered
        """
        import sqlite3

        # Ensure any pending edits are committed before we read
        if layer.isEditable():
            layer.commitChanges()

        # Force data provider to reload from disk to get latest data
        layer.dataProvider().reloadData()
        layer.updateFields()

        # Get all features sorted by Manual_FID
        features = list(layer.getFeatures())
        features.sort(key=lambda f: f.attribute(self.MANUAL_FID_FIELD) or 0)

        self.logger.info(
            f"[GRID PROCESSOR] reset_fid_to_match_manual_fid: found {len(features)} features "
            f"in layer '{layer.name()}'"
        )

        if not features:
            return 0

        # Log feature details for debugging
        for f in features[:5]:  # Log first 5
            self.logger.debug(
                f"[GRID PROCESSOR] Feature FID={f.id()}, Manual_FID={f.attribute(self.MANUAL_FID_FIELD)}, "
                f"geom_valid={f.geometry() is not None and not f.geometry().isNull()}"
            )

        # Deep copy the features with their geometries before we delete them
        # We use WKT to ensure completely independent geometry copies
        fields = layer.fields()
        sorted_features = []
        for feature in features:
            new_feature = QgsFeature(fields)
            orig_geom = feature.geometry()
            if orig_geom and not orig_geom.isNull():
                geom_wkt = orig_geom.asWkt()
                new_geom = QgsGeometry.fromWkt(geom_wkt)
                new_feature.setGeometry(new_geom)
            for field in fields:
                new_feature.setAttribute(field.name(), feature.attribute(field.name()))
            sorted_features.append(new_feature)

        # Parse the layer source to get gpkg path and table name
        source = layer.source()
        if '|layername=' not in source:
            raise ValueError("Cannot determine GeoPackage table name from layer source")

        gpkg_path = source.split('|layername=')[0]
        table_name = source.split('|layername=')[1]

        # Get all feature IDs to delete
        fids_to_delete = [f.id() for f in features]

        # Start editing
        layer.startEditing()

        # Delete all existing features
        layer.deleteFeatures(fids_to_delete)

        # Commit the deletion
        if not layer.commitChanges():
            layer.rollBack()
            raise Exception("Failed to delete features for FID reset")

        self.logger.debug(f"[GRID PROCESSOR] Deleted {len(fids_to_delete)} features")

        # Reset SQLite's auto-increment sequence so FIDs start from 1
        # This must be done after deletion and before adding new features
        try:
            conn = sqlite3.connect(gpkg_path)
            cursor = conn.cursor()
            # Delete the sequence entry for this table - SQLite will restart from 1
            cursor.execute(
                'DELETE FROM sqlite_sequence WHERE name = ?',
                (table_name,)
            )
            conn.commit()
            conn.close()
            self.logger.debug(f"[GRID PROCESSOR] Reset sqlite_sequence for {table_name}")
        except sqlite3.OperationalError as e:
            # sqlite_sequence might not exist if no auto-increment was used
            self.logger.debug(f"[GRID PROCESSOR] Could not reset sqlite_sequence: {e}")

        # Force the data provider to reload after the sequence reset
        layer.dataProvider().reloadData()

        # Now add features back in sorted order - FIDs will be 1, 2, 3, ...
        layer.startEditing()

        # Add features via data provider for better control
        success, added_features = layer.dataProvider().addFeatures(sorted_features)
        if not success:
            layer.rollBack()
            raise Exception("Failed to add features for FID reset")

        # Commit the additions
        if not layer.commitChanges():
            layer.rollBack()
            raise Exception("Failed to commit added features")

        self.logger.info(f"[GRID PROCESSOR] Re-added {len(sorted_features)} features with FIDs 1-{len(sorted_features)}")

        # Force complete refresh
        layer.dataProvider().reloadData()
        layer.updateFields()
        layer.updateExtents()
        layer.triggerRepaint()

        # Refresh the map canvas
        from qgis.utils import iface
        if iface:
            if iface.mapCanvas():
                iface.mapCanvas().clearCache()
                iface.mapCanvas().refresh()
            iface.setActiveLayer(layer)

        self.logger.info(
            f"[GRID PROCESSOR] Reset FIDs for {len(sorted_features)} features "
            f"to 1-{len(sorted_features)} matching {self.MANUAL_FID_FIELD} order"
        )

        return len(sorted_features)

    def _reset_fid_memory(self, layer: QgsVectorLayer) -> int:
        """
        Reset FIDs for a memory layer by recreating it.

        Memory layer FIDs can't be directly changed, but since they're
        ephemeral, we can delete and re-add features.

        Args:
            layer: Memory vector layer

        Returns:
            Number of features reordered
        """
        # Same approach as GeoPackage - delete and re-add in order
        return self._reset_fid_geopackage(layer)

    def reorder_by_manual_fid(
        self,
        layer: QgsVectorLayer,
        geopackage_path: Optional[str] = None
    ) -> QgsVectorLayer:
        """
        Reorder features by Manual FID (creates a new layer).

        Creates a new layer with features ordered by their Manual_FID values.
        If a GeoPackage path is provided, saves to GeoPackage; otherwise creates
        a memory layer.

        NOTE: For most use cases, prefer reset_fid_to_match_manual_fid() which
        modifies the layer in place rather than creating a new one.

        Args:
            layer: Source layer with Manual_FID field
            geopackage_path: Optional path to GeoPackage for persistent storage

        Returns:
            New layer with reordered features
        """
        if not layer or not layer.isValid():
            raise ValueError("Invalid layer")

        field_idx = layer.fields().indexOf(self.MANUAL_FID_FIELD)
        if field_idx < 0:
            raise ValueError(
                f"Layer does not have {self.MANUAL_FID_FIELD} field. "
                "Run autopopulate_manual_fid first."
            )

        # Create new layer with same structure
        crs = layer.crs()
        geom_type = QgsWkbTypes.displayString(layer.wkbType())
        layer_name = f"{layer.name()} (Ordered)"

        # Use GeoPackage if path provided
        if geopackage_path:
            from ..managers.claims_storage_manager import ClaimsStorageManager
            storage_manager = ClaimsStorageManager()
            new_layer = storage_manager.create_or_update_layer(
                table_name=ClaimsStorageManager.INITIAL_LAYOUT_TABLE,
                layer_display_name=layer_name,
                geometry_type=geom_type,
                fields=layer.fields(),
                crs=crs,
                gpkg_path=geopackage_path
            )
        else:
            # Fallback to memory layer
            new_layer = QgsVectorLayer(
                f"{geom_type}?crs={crs.authid()}",
                layer_name,
                "memory"
            )
            new_layer.dataProvider().addAttributes(layer.fields().toList())
            new_layer.updateFields()

        # Get features sorted by Manual FID
        features = list(layer.getFeatures())
        features.sort(key=lambda f: f.attribute(self.MANUAL_FID_FIELD) or 0)

        # Add features in order
        new_features = []
        for feature in features:
            new_feature = QgsFeature(new_layer.fields())
            new_feature.setGeometry(feature.geometry())
            for field in layer.fields():
                new_feature.setAttribute(
                    field.name(),
                    feature.attribute(field.name())
                )
            new_features.append(new_feature)

        new_layer.dataProvider().addFeatures(new_features)
        new_layer.updateExtents()

        storage_type = "GeoPackage" if geopackage_path else "memory"
        self.logger.info(
            f"[GRID PROCESSOR] Created ordered {storage_type} layer with {len(new_features)} features"
        )

        return new_layer

    # =========================================================================
    # Helper methods
    # =========================================================================

    def refresh_layer_spatial_index(self, layer: QgsVectorLayer) -> None:
        """
        Force a complete refresh of the layer's spatial index and display.

        Call this after external edits (like using QGIS's native delete tool)
        to ensure click detection works correctly.

        For GeoPackage layers, this also forces a reload from disk to ensure
        the data provider has the latest data.

        Args:
            layer: The layer to refresh
        """
        if not layer or not layer.isValid():
            return

        # Commit any pending edits
        if layer.isEditable():
            layer.commitChanges()

        # For GeoPackage layers, force reload from disk
        source = layer.source()
        if '.gpkg' in source:
            layer.dataProvider().reloadData()

        # Update fields in case schema changed
        layer.updateFields()

        # Rebuild spatial index by updating extents
        layer.updateExtents()

        # Force visual refresh
        layer.triggerRepaint()

        # Refresh the entire canvas to ensure sync
        from qgis.utils import iface
        if iface and iface.mapCanvas():
            iface.mapCanvas().refresh()

        self.logger.debug(f"[GRID PROCESSOR] Refreshed spatial index for {layer.name()}")


    def _extract_claims_data(
        self,
        layer: QgsVectorLayer,
        include_corners: bool = False
    ) -> List[Dict[str, Any]]:
        """Extract claim data from layer."""
        claims = []
        name_idx = layer.fields().indexOf('name')

        # Log feature count for debugging
        feature_count = layer.featureCount()
        self.logger.debug(
            f"[GRID PROCESSOR] Extracting claims from {layer.name()}, "
            f"feature count: {feature_count}, editable: {layer.isEditable()}"
        )

        for feature in layer.getFeatures():
            fid = feature.id()
            name = feature.attribute(name_idx) if name_idx >= 0 else f"Feature {fid}"
            geom = feature.geometry()

            if geom is None or geom.isNull():
                self.logger.warning(f"[GRID PROCESSOR] Feature {fid} has null geometry, skipping")
                continue

            centroid = geom.centroid().asPoint()
            area = geom.area()

            claim_data = {
                'feature_id': fid,
                'name': name,
                'centroid': centroid,
                'area': area
            }

            if include_corners:
                polygon = geom.asPolygon()
                if polygon:
                    # Exterior ring, excluding closing point
                    claim_data['corners'] = list(polygon[0][:-1])

            claims.append(claim_data)

        self.logger.debug(f"[GRID PROCESSOR] Extracted {len(claims)} claims with valid geometry")
        return claims

    def _apply_ordering(
        self,
        layer: QgsVectorLayer,
        ordered_claims: List[Dict[str, Any]],
        start_number: int
    ) -> int:
        """Apply ordering to layer's Manual_FID field."""
        # Ensure Manual_FID field exists
        self._ensure_manual_fid_field(layer)
        manual_fid_idx = layer.fields().indexOf(self.MANUAL_FID_FIELD)

        was_editing = layer.isEditable()
        if not was_editing:
            layer.startEditing()

        # Apply ordering
        count = 0
        for i, claim in enumerate(ordered_claims):
            fid = claim['feature_id']
            order = start_number + i
            layer.changeAttributeValue(fid, manual_fid_idx, order)
            count += 1

        if not was_editing:
            layer.commitChanges()

        # Update spatial index and refresh display
        # This is critical after modifying features - without this, click detection
        # uses stale geometry locations and features become unclickable
        layer.updateExtents()
        layer.triggerRepaint()

        self.logger.info(
            f"[GRID PROCESSOR] Applied ordering to {count} claims"
        )

        return count

    def _ensure_manual_fid_field(self, layer: QgsVectorLayer):
        """Ensure the Manual_FID field exists on the layer."""
        if layer.fields().indexOf(self.MANUAL_FID_FIELD) < 0:
            was_editing = layer.isEditable()
            if not was_editing:
                layer.startEditing()

            field = QgsField(self.MANUAL_FID_FIELD, FieldType_Int)
            layer.dataProvider().addAttributes([field])
            layer.updateFields()

            if not was_editing:
                layer.commitChanges()

            self.logger.info(
                f"[GRID PROCESSOR] Added {self.MANUAL_FID_FIELD} field to layer"
            )
