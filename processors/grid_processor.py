# -*- coding: utf-8 -*-
"""
Grid processing utilities for claims management - API client wrapper.

Grid processing logic is performed server-side. This module provides
a QGIS-friendly interface that calls the server API and applies
results to QGIS layers.

The server-side implementation handles:
- Grid validation
- Claim ordering/numbering algorithms
- Name assignment

Provides utilities for working with claim grids:
- Auto-numbering claims by spatial position
- Renaming claims with prefixes
- Validating grid geometry

These operations help prepare a claim grid for processing.
"""
from typing import List, Dict, Any, Optional, Tuple
from collections import defaultdict
import math

from qgis.core import (
    QgsVectorLayer, QgsFeature, QgsGeometry, QgsPointXY,
    QgsField, QgsFields, QgsProject, QgsWkbTypes
)
from qgis.PyQt.QtCore import QVariant

from ..utils.logger import PluginLogger


class GridProcessor:
    """
    Client-side wrapper for server-side grid processing.

    All complex grid processing logic is on the server. This class:
    1. Extracts grid data from QGIS layers
    2. Calls the server API for processing
    3. Applies the results back to QGIS layers

    For offline use, falls back to local algorithms.
    """

    # Field name for manual FID
    MANUAL_FID_FIELD = 'Manual_FID'

    def __init__(self, api_client=None):
        """
        Initialize the grid processor.

        Args:
            api_client: Optional APIClient instance. If not provided,
                       falls back to local processing for offline use.
        """
        self.api_client = api_client
        self.logger = PluginLogger.get_logger()
        self._offline_mode = api_client is None

    def set_api_client(self, api_client):
        """Set the API client for server-side processing."""
        self.api_client = api_client
        self._offline_mode = api_client is None

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
                - 'snake_horizontal': Same as serpentine
            start_number: Starting number for sequence (default 1)

        Returns:
            Number of claims numbered
        """
        if not layer or not layer.isValid():
            raise ValueError("Invalid layer")

        # Normalize direction names
        direction_map = {
            'west_to_east_north_to_south': 'left_to_right_top_to_bottom',
            'north_to_south_west_to_east': 'top_to_bottom_left_to_right',
            'snake_horizontal': 'serpentine',
            'snake_vertical': 'serpentine_vertical',
        }
        sort_direction = direction_map.get(direction, direction)

        # Extract claim positions
        claims_data = self._extract_claims_data(layer)

        if not claims_data:
            return 0

        # Try server-side ordering first
        if self.api_client and not self._offline_mode:
            try:
                ordered = self._order_claims_server(claims_data, sort_direction)
            except Exception as e:
                self.logger.warning(
                    f"[GRID PROCESSOR] Server-side ordering failed, falling back to local: {e}"
                )
                ordered = self._order_claims_local(claims_data, sort_direction)
        else:
            ordered = self._order_claims_local(claims_data, sort_direction)

        # Apply ordering to layer
        return self._apply_ordering(layer, ordered, start_number)

    def _order_claims_server(
        self,
        claims_data: List[Dict[str, Any]],
        sort_direction: str
    ) -> List[Dict[str, Any]]:
        """Order claims using server API."""
        # Build API endpoint URL
        endpoint = self._get_claims_endpoint('order-claims/')

        # Prepare data for API
        api_claims = []
        for claim in claims_data:
            api_claims.append({
                'name': claim['name'],
                'centroid': {
                    'easting': claim['centroid'].x(),
                    'northing': claim['centroid'].y()
                }
            })

        # Call server API
        response = self.api_client._make_request('POST', endpoint, data={
            'claims': api_claims,
            'sort_direction': sort_direction
        })

        if 'error' in response:
            raise ValueError(response['error'])

        # Convert response back to our format
        ordered = []
        name_to_data = {c['name']: c for c in claims_data}

        for item in response.get('ordered_claims', []):
            name = item['name']
            if name in name_to_data:
                ordered.append({
                    **name_to_data[name],
                    'order': item['order']
                })

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
            layer.addAttribute(QgsField(name_field, QVariant.String, len=100))
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

        self.logger.info(
            f"[GRID PROCESSOR] Renamed {renamed_count} claims with prefix '{base_name}'"
        )

        return renamed_count

    def validate_grid_geometry(
        self,
        layer: QgsVectorLayer,
        expected_corners: int = 4
    ) -> List[Dict[str, Any]]:
        """
        Validate claim geometries for common issues.

        Checks for:
        - Non-polygon geometries
        - Wrong number of corners
        - Self-intersecting polygons
        - Invalid geometries
        - Overlapping claims
        - Non-rectangular shapes

        Args:
            layer: Vector layer with claim polygons
            expected_corners: Expected number of corners (default 4 for rectangular claims)

        Returns:
            List of validation issues, empty if all valid
        """
        if not layer or not layer.isValid():
            raise ValueError("Invalid layer")

        claims_data = self._extract_claims_data(layer, include_corners=True)

        if not claims_data:
            return []

        # Try server-side validation first
        if self.api_client and not self._offline_mode:
            try:
                return self._validate_grid_server(claims_data, expected_corners)
            except Exception as e:
                self.logger.warning(
                    f"[GRID PROCESSOR] Server-side validation failed, falling back to local: {e}"
                )

        # Fallback to local validation
        return self._validate_grid_local(layer, expected_corners)

    def _validate_grid_server(
        self,
        claims_data: List[Dict[str, Any]],
        expected_corners: int
    ) -> List[Dict[str, Any]]:
        """Validate grid using server API."""
        # Build API endpoint URL
        endpoint = self._get_claims_endpoint('validate-grid/')

        # Prepare data for API
        api_claims = []
        for claim in claims_data:
            api_claims.append({
                'name': claim['name'],
                'corners': [
                    {'easting': c.x(), 'northing': c.y()}
                    for c in claim.get('corners', [])
                ]
            })

        # Call server API
        response = self.api_client._make_request('POST', endpoint, data={
            'claims': api_claims,
            'expected_corners': expected_corners
        })

        if 'error' in response:
            raise ValueError(response['error'])

        # Return validation issues
        return response.get('issues', [])

    def calculate_grid_statistics(self, layer: QgsVectorLayer) -> Dict[str, Any]:
        """
        Calculate statistics about the claim grid.

        Args:
            layer: Vector layer with claim polygons

        Returns:
            Dictionary with grid statistics
        """
        if not layer or not layer.isValid():
            return {'feature_count': 0}

        claims_data = self._extract_claims_data(layer, include_corners=True)

        if not claims_data:
            return {'feature_count': 0}

        # Calculate areas and centroids
        areas = []
        centroids = []

        for claim in claims_data:
            areas.append(claim.get('area', 0))
            centroids.append(claim['centroid'])

        # Calculate statistics
        total_area = sum(areas)
        min_area = min(areas) if areas else 0
        max_area = max(areas) if areas else 0
        corner_counts = defaultdict(int)

        for claim in claims_data:
            num_corners = len(claim.get('corners', []))
            corner_counts[num_corners] += 1

        # Estimate rows and columns
        x_values = [c.x() for c in centroids]
        y_values = [c.y() for c in centroids]

        x_clusters = self._count_clusters(x_values)
        y_clusters = self._count_clusters(y_values)

        extent = layer.extent()

        return {
            'feature_count': len(claims_data),
            'estimated_rows': y_clusters,
            'estimated_cols': x_clusters,
            'total_area': total_area,
            'min_area': min_area,
            'max_area': max_area,
            'avg_area': total_area / len(claims_data) if claims_data else 0,
            'corner_count_distribution': dict(corner_counts),
            'all_rectangular': len(corner_counts) == 1 and 4 in corner_counts,
            'bounds': (
                extent.xMinimum(),
                extent.yMinimum(),
                extent.xMaximum(),
                extent.yMaximum()
            ),
            'average_width': (extent.xMaximum() - extent.xMinimum()) / max(x_clusters, 1),
            'average_height': (extent.yMaximum() - extent.yMinimum()) / max(y_clusters, 1),
        }

    def detect_grid_pattern(
        self,
        layer: QgsVectorLayer
    ) -> Dict[str, Any]:
        """
        Attempt to detect the grid pattern (rows, columns, orientation).

        Useful for understanding how claims are arranged and validating
        that they form a proper grid.

        Args:
            layer: Vector layer with claim polygons

        Returns:
            Dictionary with detected pattern info
        """
        claims_data = self._extract_claims_data(layer, include_corners=True)

        if len(claims_data) < 2:
            return {
                'pattern_detected': False,
                'reason': 'Not enough claims to detect pattern'
            }

        # Get all centroids
        centroids = [c['centroid'] for c in claims_data]

        # Find unique X and Y coordinates (with tolerance)
        tolerance = 10  # meters
        unique_x = self._cluster_values([c.x() for c in centroids], tolerance)
        unique_y = self._cluster_values([c.y() for c in centroids], tolerance)

        # Estimate rows and columns
        estimated_cols = len(unique_x)
        estimated_rows = len(unique_y)

        # Check if it forms a complete grid
        expected_count = estimated_rows * estimated_cols
        is_complete = len(claims_data) == expected_count

        # Estimate orientation (angle of first edge)
        orientation = self._estimate_orientation(claims_data)

        return {
            'pattern_detected': True,
            'estimated_rows': estimated_rows,
            'estimated_cols': estimated_cols,
            'actual_count': len(claims_data),
            'expected_count': expected_count,
            'is_complete_grid': is_complete,
            'missing_claims': expected_count - len(claims_data) if not is_complete else 0,
            'estimated_orientation': orientation
        }

    def reorder_by_manual_fid(
        self,
        layer: QgsVectorLayer,
        geopackage_path: Optional[str] = None
    ) -> QgsVectorLayer:
        """
        Reorder features by Manual FID.

        Creates a new layer with features ordered by their Manual_FID values.
        If a GeoPackage path is provided, saves to GeoPackage; otherwise creates
        a memory layer.

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

    def _get_claims_endpoint(self, path: str) -> str:
        """Build full URL for claims endpoint."""
        base = self.api_client.config.base_url
        # Ensure we use v2 API for claims endpoints
        if '/v1' in base:
            base = base.replace('/v1', '/api/v2')
        elif '/api/v2' not in base:
            base = base.rstrip('/') + '/api/v2' if not base.endswith('/api/v2') else base
        return f"{base}/claims/{path}"

    def _extract_claims_data(
        self,
        layer: QgsVectorLayer,
        include_corners: bool = False
    ) -> List[Dict[str, Any]]:
        """Extract claim data from layer."""
        claims = []
        name_idx = layer.fields().indexOf('name')

        for feature in layer.getFeatures():
            fid = feature.id()
            name = feature.attribute(name_idx) if name_idx >= 0 else f"Feature {fid}"
            geom = feature.geometry()

            if geom is None or geom.isNull():
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

            field = QgsField(self.MANUAL_FID_FIELD, QVariant.Int)
            layer.dataProvider().addAttributes([field])
            layer.updateFields()

            if not was_editing:
                layer.commitChanges()

            self.logger.info(
                f"[GRID PROCESSOR] Added {self.MANUAL_FID_FIELD} field to layer"
            )

    def _cluster_values(
        self,
        values: List[float],
        tolerance: float
    ) -> List[float]:
        """Cluster similar values together."""
        if not values:
            return []

        sorted_vals = sorted(values)
        clusters = [[sorted_vals[0]]]

        for val in sorted_vals[1:]:
            if abs(val - clusters[-1][-1]) <= tolerance:
                clusters[-1].append(val)
            else:
                clusters.append([val])

        # Return cluster centroids
        return [sum(c) / len(c) for c in clusters]

    def _count_clusters(
        self,
        values: List[float],
        tolerance: Optional[float] = None
    ) -> int:
        """Count distinct clusters in a list of values."""
        if not values:
            return 0

        sorted_values = sorted(values)

        if tolerance is None:
            # Auto-calculate tolerance as 10% of average spacing
            if len(sorted_values) > 1:
                diffs = [
                    sorted_values[i + 1] - sorted_values[i]
                    for i in range(len(sorted_values) - 1)
                ]
                tolerance = sum(diffs) / len(diffs) * 0.3
            else:
                tolerance = 1.0

        clusters = 1
        last_value = sorted_values[0]

        for value in sorted_values[1:]:
            if value - last_value > tolerance:
                clusters += 1
            last_value = value

        return clusters

    def _estimate_orientation(
        self,
        claims_data: List[Dict[str, Any]]
    ) -> float:
        """Estimate grid orientation from claim data."""
        if not claims_data or 'corners' not in claims_data[0]:
            return 0.0

        # Use first claim's first edge
        corners = claims_data[0].get('corners', [])
        if len(corners) < 2:
            return 0.0

        p1 = corners[0]
        p2 = corners[1]

        dx = p2.x() - p1.x()
        dy = p2.y() - p1.y()

        angle = math.degrees(math.atan2(dx, dy))  # Angle from north

        # Normalize to 0-90 range (we don't care about direction)
        while angle < 0:
            angle += 180
        while angle >= 180:
            angle -= 180
        if angle > 90:
            angle = 180 - angle

        return round(angle, 1)

    # =========================================================================
    # Local fallback methods (for offline mode)
    # =========================================================================

    def _order_claims_local(
        self,
        claims_data: List[Dict[str, Any]],
        sort_direction: str
    ) -> List[Dict[str, Any]]:
        """Order claims locally (fallback for offline mode)."""
        if sort_direction == 'left_to_right_top_to_bottom':
            # Primary: Y descending (north first), Secondary: X ascending (west first)
            sorted_claims = sorted(
                claims_data,
                key=lambda c: (-c['centroid'].y(), c['centroid'].x())
            )
        elif sort_direction == 'top_to_bottom_left_to_right':
            # Primary: X ascending, Secondary: Y descending
            sorted_claims = sorted(
                claims_data,
                key=lambda c: (c['centroid'].x(), -c['centroid'].y())
            )
        elif sort_direction in ('serpentine', 'snake_horizontal'):
            # Group by rows, alternate direction
            sorted_claims = self._snake_sort(claims_data, horizontal=True)
        elif sort_direction == 'serpentine_vertical':
            # Group by columns, alternate direction
            sorted_claims = self._snake_sort(claims_data, horizontal=False)
        else:
            # Default: west to east, north to south
            sorted_claims = sorted(
                claims_data,
                key=lambda c: (-c['centroid'].y(), c['centroid'].x())
            )

        # Add order numbers
        for i, claim in enumerate(sorted_claims):
            claim['order'] = i + 1

        return sorted_claims

    def _snake_sort(
        self,
        claims_data: List[Dict[str, Any]],
        horizontal: bool = True
    ) -> List[Dict[str, Any]]:
        """Sort claims in a snake pattern."""
        tolerance = self._estimate_row_spacing(claims_data) * 0.3

        if horizontal:
            # Group by Y coordinate (rows)
            groups = self._group_by_coordinate(
                claims_data,
                key_func=lambda c: c['centroid'].y(),
                tolerance=tolerance
            )
            # Sort groups by Y descending (north first)
            sorted_groups = sorted(groups.items(), key=lambda x: -x[0])
        else:
            # Group by X coordinate (columns)
            groups = self._group_by_coordinate(
                claims_data,
                key_func=lambda c: c['centroid'].x(),
                tolerance=tolerance
            )
            # Sort groups by X ascending (west first)
            sorted_groups = sorted(groups.items(), key=lambda x: x[0])

        result = []
        reverse = False

        for _, group in sorted_groups:
            if horizontal:
                # Sort within row by X
                sorted_group = sorted(
                    group,
                    key=lambda c: c['centroid'].x(),
                    reverse=reverse
                )
            else:
                # Sort within column by Y
                sorted_group = sorted(
                    group,
                    key=lambda c: c['centroid'].y(),
                    reverse=not reverse  # Y is inverted (north = higher)
                )

            result.extend(sorted_group)
            reverse = not reverse  # Alternate direction

        return result

    def _estimate_row_spacing(self, claims_data: List[Dict[str, Any]]) -> float:
        """Estimate the vertical spacing between rows."""
        if len(claims_data) < 2:
            return 100.0  # Default fallback

        y_values = sorted(set(c['centroid'].y() for c in claims_data), reverse=True)

        if len(y_values) < 2:
            return 100.0

        # Calculate differences between consecutive Y values
        diffs = [y_values[i] - y_values[i + 1] for i in range(len(y_values) - 1)]

        # Filter out very small differences (within same row)
        significant_diffs = [d for d in diffs if d > 10]

        if significant_diffs:
            return sum(significant_diffs) / len(significant_diffs)

        return 100.0

    def _group_by_coordinate(
        self,
        claims_data: List[Dict[str, Any]],
        key_func,
        tolerance: float
    ) -> Dict[float, List[Dict[str, Any]]]:
        """Group claims by a coordinate with tolerance."""
        # Get all coordinate values
        coords = [key_func(c) for c in claims_data]

        # Cluster them
        cluster_centers = self._cluster_values(coords, tolerance)

        # Assign claims to clusters
        groups = defaultdict(list)
        for claim in claims_data:
            coord = key_func(claim)
            # Find nearest cluster
            nearest = min(cluster_centers, key=lambda x: abs(x - coord))
            groups[nearest].append(claim)

        return groups

    def _validate_grid_local(
        self,
        layer: QgsVectorLayer,
        expected_corners: int
    ) -> List[Dict[str, Any]]:
        """Validate grid locally (fallback for offline mode)."""
        issues = []
        name_idx = layer.fields().indexOf('name')
        geometries = []

        # Calculate expected area range (for detecting outliers)
        areas = []
        for feature in layer.getFeatures():
            geom = feature.geometry()
            if geom and not geom.isNull():
                areas.append(geom.area())

        if areas:
            avg_area = sum(areas) / len(areas)
            min_expected = avg_area * 0.5
            max_expected = avg_area * 1.5
        else:
            min_expected = 0
            max_expected = float('inf')

        for feature in layer.getFeatures():
            fid = feature.id()
            name = feature.attribute(name_idx) if name_idx >= 0 else f"Feature {fid}"
            geom = feature.geometry()

            if geom is None or geom.isNull():
                issues.append({
                    'feature_id': fid,
                    'name': name,
                    'issue': 'Null or empty geometry',
                    'severity': 'error'
                })
                continue

            # Check if polygon
            if geom.type() != QgsWkbTypes.PolygonGeometry:
                issues.append({
                    'feature_id': fid,
                    'name': name,
                    'issue': f'Not a polygon (type: {geom.type()})',
                    'severity': 'error'
                })
                continue

            # Check validity
            if not geom.isGeosValid():
                issues.append({
                    'feature_id': fid,
                    'name': name,
                    'issue': 'Invalid geometry (self-intersecting or other issue)',
                    'severity': 'error'
                })

            # Check corner count
            polygon = geom.asPolygon()
            if polygon:
                num_corners = len(polygon[0]) - 1  # Exclude closing point
                if num_corners != expected_corners:
                    issues.append({
                        'feature_id': fid,
                        'name': name,
                        'issue': f'Expected {expected_corners} corners, found {num_corners}',
                        'severity': 'warning'
                    })

                # Check for rectangular shape
                if num_corners == 4:
                    is_rect, rect_error = self._check_rectangular(polygon[0][:-1])
                    if not is_rect:
                        issues.append({
                            'feature_id': fid,
                            'name': name,
                            'issue': rect_error,
                            'severity': 'warning'
                        })

            # Check area
            area = geom.area()
            if area < min_expected:
                issues.append({
                    'feature_id': fid,
                    'name': name,
                    'issue': f'Area ({area:.0f}) is much smaller than average',
                    'severity': 'warning'
                })
            elif area > max_expected:
                issues.append({
                    'feature_id': fid,
                    'name': name,
                    'issue': f'Area ({area:.0f}) is much larger than average',
                    'severity': 'warning'
                })

            geometries.append((fid, name, geom))

        # Check for overlaps
        for i, (fid1, name1, geom1) in enumerate(geometries):
            for fid2, name2, geom2 in geometries[i + 1:]:
                if geom1.overlaps(geom2):
                    issues.append({
                        'feature_id': fid1,
                        'name': name1,
                        'issue': f'Overlaps with {name2}',
                        'severity': 'warning'
                    })

        return issues

    def _check_rectangular(
        self,
        corners: List[QgsPointXY],
        tolerance: float = 0.05
    ) -> Tuple[bool, Optional[str]]:
        """Check if a polygon is approximately rectangular."""
        if len(corners) != 4:
            return False, f"Expected 4 corners, got {len(corners)}"

        # Calculate side lengths
        sides = []
        for i in range(4):
            p1 = corners[i]
            p2 = corners[(i + 1) % 4]
            length = math.sqrt((p2.x() - p1.x())**2 + (p2.y() - p1.y())**2)
            sides.append(length)

        # Check opposite sides are roughly equal
        diff1 = abs(sides[0] - sides[2]) / max(sides[0], sides[2], 0.001)
        diff2 = abs(sides[1] - sides[3]) / max(sides[1], sides[3], 0.001)

        if diff1 > tolerance:
            return False, f"Opposite sides differ by {diff1*100:.1f}% (sides 1 and 3)"

        if diff2 > tolerance:
            return False, f"Opposite sides differ by {diff2*100:.1f}% (sides 2 and 4)"

        return True, None
