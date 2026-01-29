# -*- coding: utf-8 -*-
"""
Corner alignment tools for claims management - API client wrapper.

Corner alignment logic is performed server-side. This module provides
a QGIS-friendly interface that calls the server API and applies
results to QGIS layers.

The server-side implementation handles:
- Corner clustering and snapping
- Edge matching for neighbor detection
- Corner statistics calculation

Provides tools for aligning claim corners that should be shared between
adjacent claims. This is important for proper neighbor detection and
waypoint deduplication.
"""
from typing import List, Dict, Any, Optional, Tuple, Set
import math
from collections import defaultdict

from qgis.core import (
    QgsVectorLayer, QgsFeature, QgsGeometry, QgsPointXY,
    QgsField, QgsFields, QgsCoordinateReferenceSystem,
    QgsProject
)
from qgis.PyQt.QtCore import QMetaType

from ..utils.logger import PluginLogger


class CornerAlignmentProcessor:
    """
    Client-side wrapper for server-side corner alignment.

    All corner alignment logic is on the server. This class:
    1. Extracts corner data from QGIS layers
    2. Calls the server API for alignment calculations
    3. Applies the results back to QGIS layers

    For offline use, falls back to local algorithms.

    Adjacent claims should share exact corner coordinates. Small
    digitization errors can cause corners to be slightly misaligned,
    which breaks neighbor detection and causes duplicate waypoints.
    """

    def __init__(self, api_client=None):
        """
        Initialize the corner alignment processor.

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

    def identify_misaligned_corners(
        self,
        layer: QgsVectorLayer,
        tolerance_m: float = 1.0
    ) -> QgsVectorLayer:
        """
        Create point layer showing potentially misaligned corners.

        Identifies corners that are close to (but not exactly at)
        other corners. These represent potential alignment issues.

        Args:
            layer: Vector layer with claim polygons
            tolerance_m: Maximum distance (meters) to consider corners
                        as potentially the same point

        Returns:
            Point layer with misaligned corner locations and diagnostics
        """
        if not layer or not layer.isValid():
            raise ValueError("Invalid layer")

        # Extract all corners
        corners = self._extract_all_corners(layer)

        if not corners:
            # Return empty layer
            return self._create_corner_analysis_layer(layer.crs(), [])

        # Try server-side analysis first
        if self.api_client and not self._offline_mode:
            try:
                misaligned = self._identify_misaligned_server(corners, tolerance_m)
            except Exception as e:
                self.logger.warning(
                    f"[CORNER ALIGNMENT] Server-side analysis failed, falling back to local: {e}"
                )
                misaligned = self._find_misaligned_local(corners, tolerance_m)
        else:
            misaligned = self._find_misaligned_local(corners, tolerance_m)

        # Create analysis layer
        result_layer = self._create_corner_analysis_layer(layer.crs(), misaligned)

        self.logger.info(
            f"[CORNER ALIGNMENT] Found {len(misaligned)} potentially misaligned corners"
        )

        return result_layer

    def _identify_misaligned_server(
        self,
        corners: List[Dict[str, Any]],
        tolerance_m: float
    ) -> List[Dict[str, Any]]:
        """Identify misaligned corners using server API."""
        # Build claims data for API
        claims_data = self._corners_to_claims_data(corners)

        # Build API endpoint URL
        endpoint = self._get_claims_endpoint('analyze-corners/')

        # Call server API
        response = self.api_client._make_request('POST', endpoint, data={
            'claims': claims_data,
            'tolerance_m': tolerance_m
        })

        if 'error' in response:
            raise ValueError(response['error'])

        # Convert response to misaligned format
        misaligned = []
        for item in response.get('misaligned_corners', []):
            # Find matching corners from our data
            misaligned.append({
                'point': QgsPointXY(
                    item['position']['easting'],
                    item['position']['northing']
                ),
                'distance': item['distance_m'],
                'feature1_id': 0,  # Server doesn't know QGIS feature IDs
                'feature1_name': item['claim1'],
                'corner1_num': item['corner1_num'],
                'feature2_id': 0,
                'feature2_name': item['claim2'],
                'corner2_num': item['corner2_num'],
            })

        return misaligned

    def align_corners(
        self,
        layer: QgsVectorLayer,
        tolerance_m: float = 1.0
    ) -> Dict[str, Any]:
        """
        Snap corners within tolerance to shared position.

        Modifies the layer in place to align corners that are
        within the tolerance distance. Uses the centroid of
        clustered corners as the alignment point.

        Args:
            layer: Vector layer with claim polygons
            tolerance_m: Maximum distance (meters) to snap corners

        Returns:
            Dictionary with:
                - clusters_found: Number of corner clusters aligned
                - corners_moved: Total number of corners adjusted
                - max_adjustment: Maximum distance a corner was moved
        """
        if not layer or not layer.isValid():
            raise ValueError("Invalid layer")

        # Extract all corners with feature references
        corners = self._extract_all_corners(layer)

        if not corners:
            return {'clusters_found': 0, 'corners_moved': 0, 'max_adjustment': 0}

        # Try server-side alignment first
        if self.api_client and not self._offline_mode:
            try:
                result = self._align_corners_server(layer, corners, tolerance_m)
                return result
            except Exception as e:
                self.logger.warning(
                    f"[CORNER ALIGNMENT] Server-side alignment failed, falling back to local: {e}"
                )

        # Fallback to local alignment
        return self._align_corners_local(layer, corners, tolerance_m)

    def _align_corners_server(
        self,
        layer: QgsVectorLayer,
        corners: List[Dict[str, Any]],
        tolerance_m: float
    ) -> Dict[str, Any]:
        """Align corners using server API."""
        # Build claims data for API
        claims_data = self._corners_to_claims_data(corners)

        # Build API endpoint URL
        endpoint = self._get_claims_endpoint('align-corners/')

        # Call server API
        response = self.api_client._make_request('POST', endpoint, data={
            'claims': claims_data,
            'tolerance_m': tolerance_m
        })

        if 'error' in response:
            raise ValueError(response['error'])

        # Apply the aligned corners back to the layer
        aligned_claims = response.get('claims', [])
        statistics = response.get('statistics', {})

        # Map claim names to feature IDs
        name_to_fid = {}
        name_idx = layer.fields().indexOf('name')
        for feature in layer.getFeatures():
            if name_idx >= 0:
                name = feature.attribute(name_idx)
                name_to_fid[name] = feature.id()

        # Start editing
        was_editing = layer.isEditable()
        if not was_editing:
            layer.startEditing()

        # Apply aligned corners
        for aligned_claim in aligned_claims:
            claim_name = aligned_claim.get('name')
            fid = name_to_fid.get(claim_name)
            if fid is None:
                continue

            aligned_corners = aligned_claim.get('corners', [])
            if not aligned_corners:
                continue

            # Update geometry
            feature = layer.getFeature(fid)
            if feature.geometry() is None or feature.geometry().isNull():
                continue

            # Build new polygon from aligned corners
            points = [
                QgsPointXY(c['easting'], c['northing'])
                for c in aligned_corners
            ]
            points.append(points[0])  # Close polygon

            new_geom = QgsGeometry.fromPolygonXY([points])
            layer.changeGeometry(fid, new_geom)

        # Commit changes
        if not was_editing:
            layer.commitChanges()

        self.logger.info(
            f"[CORNER ALIGNMENT] Server aligned {statistics.get('clusters_found', 0)} clusters, "
            f"moved {statistics.get('corners_moved', 0)} corners, "
            f"max adjustment {statistics.get('max_adjustment', 0):.3f}m"
        )

        return statistics

    def find_shared_edges(
        self,
        layer: QgsVectorLayer,
        tolerance_m: float = 1.0
    ) -> Dict[str, Any]:
        """
        Find edges that are shared between adjacent claims.

        This helps identify neighbor relationships and verify
        that adjacent claims properly share boundaries.

        Args:
            layer: Vector layer with claim polygons
            tolerance_m: Tolerance for edge matching

        Returns:
            Dictionary with:
                - shared_edges: List of shared edge info
                - isolated_claims: List of claims with no shared edges
                - neighbor_map: Dict mapping feature IDs to their neighbors
        """
        if not layer or not layer.isValid():
            raise ValueError("Invalid layer")

        # Extract corners for building claims data
        corners = self._extract_all_corners(layer)

        # Try server-side analysis first
        if self.api_client and not self._offline_mode:
            try:
                return self._find_shared_edges_server(corners, tolerance_m)
            except Exception as e:
                self.logger.warning(
                    f"[CORNER ALIGNMENT] Server-side edge analysis failed, falling back to local: {e}"
                )

        # Fallback to local analysis
        return self._find_shared_edges_local(layer, tolerance_m)

    def _find_shared_edges_server(
        self,
        corners: List[Dict[str, Any]],
        tolerance_m: float
    ) -> Dict[str, Any]:
        """Find shared edges using server API."""
        # Build claims data for API
        claims_data = self._corners_to_claims_data(corners)

        # Build API endpoint URL
        endpoint = self._get_claims_endpoint('analyze-corners/')

        # Call server API
        response = self.api_client._make_request('POST', endpoint, data={
            'claims': claims_data,
            'tolerance_m': tolerance_m
        })

        if 'error' in response:
            raise ValueError(response['error'])

        return {
            'shared_edges': response.get('shared_edges', []),
            'isolated_claims': response.get('isolated_claims', []),
            'neighbor_count': response.get('neighbor_count', {})
        }

    def get_corner_statistics(
        self,
        layer: QgsVectorLayer,
        tolerance_m: float = 1.0
    ) -> Dict[str, Any]:
        """
        Get statistics about corner sharing in the layer.

        Args:
            layer: Vector layer with claim polygons
            tolerance_m: Tolerance for considering corners as shared

        Returns:
            Dictionary with corner sharing statistics
        """
        corners = self._extract_all_corners(layer)

        if not corners:
            return {'total_corners': 0}

        # Count how many corners are at each unique position
        position_counts = defaultdict(list)

        for corner in corners:
            # Round to tolerance for grouping
            key = (
                round(corner['point'].x() / tolerance_m) * tolerance_m,
                round(corner['point'].y() / tolerance_m) * tolerance_m
            )
            position_counts[key].append(corner)

        # Calculate statistics
        unique_positions = len(position_counts)
        shared_positions = sum(
            1 for corners in position_counts.values()
            if len(corners) > 1
        )
        max_sharing = max(
            len(corners) for corners in position_counts.values()
        )

        return {
            'total_corners': len(corners),
            'unique_positions': unique_positions,
            'shared_positions': shared_positions,
            'unshared_positions': unique_positions - shared_positions,
            'max_corners_at_one_point': max_sharing,
            'average_sharing': len(corners) / unique_positions if unique_positions else 0
        }

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

    def _corners_to_claims_data(
        self,
        corners: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Convert extracted corners to claims data format for API."""
        # Group corners by feature/claim name
        claims_dict = {}
        for corner in corners:
            name = corner['feature_name']
            if name not in claims_dict:
                claims_dict[name] = {
                    'name': name,
                    'corners': []
                }
            claims_dict[name]['corners'].append({
                'easting': corner['point'].x(),
                'northing': corner['point'].y()
            })

        return list(claims_dict.values())

    def _extract_all_corners(
        self,
        layer: QgsVectorLayer
    ) -> List[Dict[str, Any]]:
        """
        Extract all corners from all features in the layer.

        Args:
            layer: Vector layer with polygons

        Returns:
            List of corner dictionaries with point, feature info, corner number
        """
        corners = []
        name_idx = layer.fields().indexOf('name')

        for feature in layer.getFeatures():
            fid = feature.id()
            name = feature.attribute(name_idx) if name_idx >= 0 else f"Feature {fid}"
            geom = feature.geometry()

            if geom is None or geom.isNull():
                continue

            # Get polygon corners
            polygon = geom.asPolygon()
            if not polygon:
                continue

            # Exterior ring (exclude closing point)
            exterior = polygon[0][:-1]

            for i, point in enumerate(exterior):
                corners.append({
                    'point': point,
                    'feature_id': fid,
                    'feature_name': name,
                    'corner_num': i + 1
                })

        return corners

    def _create_corner_analysis_layer(
        self,
        crs: QgsCoordinateReferenceSystem,
        misaligned: List[Dict[str, Any]]
    ) -> QgsVectorLayer:
        """
        Create a point layer for corner analysis results.

        Args:
            crs: Coordinate reference system
            misaligned: List of misaligned corner info

        Returns:
            Point layer with analysis results
        """
        layer = QgsVectorLayer(
            f"Point?crs={crs.authid()}",
            "Misaligned Corners",
            "memory"
        )

        # Define fields
        fields = QgsFields()
        fields.append(QgsField("distance_m", QMetaType.Type.Double))
        fields.append(QgsField("feature1_name", QMetaType.Type.QString, len=100))
        fields.append(QgsField("corner1_num", QMetaType.Type.Int))
        fields.append(QgsField("feature2_name", QMetaType.Type.QString, len=100))
        fields.append(QgsField("corner2_num", QMetaType.Type.Int))
        fields.append(QgsField("description", QMetaType.Type.QString, len=255))

        layer.dataProvider().addAttributes(fields)
        layer.updateFields()

        # Add features
        features = []
        for item in misaligned:
            feature = QgsFeature(layer.fields())
            feature.setGeometry(QgsGeometry.fromPointXY(item['point']))
            feature.setAttribute("distance_m", round(item['distance'], 3))
            feature.setAttribute("feature1_name", item['feature1_name'])
            feature.setAttribute("corner1_num", item['corner1_num'])
            feature.setAttribute("feature2_name", item['feature2_name'])
            feature.setAttribute("corner2_num", item['corner2_num'])
            feature.setAttribute(
                "description",
                f"{item['feature1_name']} C{item['corner1_num']} is {item['distance']:.3f}m "
                f"from {item['feature2_name']} C{item['corner2_num']}"
            )
            features.append(feature)

        layer.dataProvider().addFeatures(features)
        layer.updateExtents()

        return layer

    # =========================================================================
    # Local fallback methods (for offline mode)
    # =========================================================================

    def _find_misaligned_local(
        self,
        corners: List[Dict[str, Any]],
        tolerance_m: float
    ) -> List[Dict[str, Any]]:
        """Find potentially misaligned corners locally."""
        misaligned = []

        for i, corner1 in enumerate(corners):
            for corner2 in corners[i + 1:]:
                distance = self._calculate_distance(
                    corner1['point'], corner2['point']
                )

                # If very close (< tolerance) but not exact (> 0.001m)
                if 0.001 < distance <= tolerance_m:
                    misaligned.append({
                        'point': corner1['point'],
                        'distance': distance,
                        'feature1_id': corner1['feature_id'],
                        'feature1_name': corner1['feature_name'],
                        'corner1_num': corner1['corner_num'],
                        'feature2_id': corner2['feature_id'],
                        'feature2_name': corner2['feature_name'],
                        'corner2_num': corner2['corner_num'],
                    })

        return misaligned

    def _align_corners_local(
        self,
        layer: QgsVectorLayer,
        corners: List[Dict[str, Any]],
        tolerance_m: float
    ) -> Dict[str, Any]:
        """Align corners locally (fallback for offline mode)."""
        # Find clusters of nearby corners
        clusters = self._find_corner_clusters(corners, tolerance_m)

        if not clusters:
            return {'clusters_found': 0, 'corners_moved': 0, 'max_adjustment': 0}

        # Start editing
        was_editing = layer.isEditable()
        if not was_editing:
            layer.startEditing()

        corners_moved = 0
        max_adjustment = 0

        # Process each cluster
        for cluster in clusters:
            if len(cluster) < 2:
                continue

            # Calculate cluster centroid
            avg_x = sum(c['point'].x() for c in cluster) / len(cluster)
            avg_y = sum(c['point'].y() for c in cluster) / len(cluster)
            centroid = QgsPointXY(avg_x, avg_y)

            # Update each feature in the cluster
            for corner_info in cluster:
                adjustment = self._calculate_distance(corner_info['point'], centroid)

                if adjustment > 0.0001:  # Only move if significant
                    self._move_corner(
                        layer,
                        corner_info['feature_id'],
                        corner_info['corner_num'] - 1,  # 0-indexed
                        centroid
                    )
                    corners_moved += 1
                    max_adjustment = max(max_adjustment, adjustment)

        # Commit changes
        if not was_editing:
            layer.commitChanges()

        self.logger.info(
            f"[CORNER ALIGNMENT] Aligned {len(clusters)} clusters (local), "
            f"moved {corners_moved} corners, max adjustment {max_adjustment:.3f}m"
        )

        return {
            'clusters_found': len(clusters),
            'corners_moved': corners_moved,
            'max_adjustment': round(max_adjustment, 4)
        }

    def _find_corner_clusters(
        self,
        corners: List[Dict[str, Any]],
        tolerance_m: float
    ) -> List[List[Dict[str, Any]]]:
        """
        Find clusters of corners that should be aligned.

        Uses a simple proximity-based clustering algorithm.
        """
        if not corners:
            return []

        # Track which corners have been assigned to a cluster
        assigned: Set[int] = set()
        clusters = []

        for i, corner1 in enumerate(corners):
            if i in assigned:
                continue

            # Start new cluster
            cluster = [corner1]
            assigned.add(i)

            # Find all corners within tolerance
            for j, corner2 in enumerate(corners):
                if j in assigned:
                    continue

                distance = self._calculate_distance(
                    corner1['point'], corner2['point']
                )

                if distance <= tolerance_m:
                    # Check it's not the same feature
                    if corner1['feature_id'] != corner2['feature_id']:
                        cluster.append(corner2)
                        assigned.add(j)

            # Only keep clusters with multiple corners
            if len(cluster) > 1:
                clusters.append(cluster)

        return clusters

    def _move_corner(
        self,
        layer: QgsVectorLayer,
        feature_id: int,
        corner_index: int,
        new_position: QgsPointXY
    ):
        """
        Move a specific corner of a feature to a new position.

        Args:
            layer: Vector layer
            feature_id: Feature ID
            corner_index: Index of the corner in the polygon (0-based)
            new_position: New position for the corner
        """
        feature = layer.getFeature(feature_id)
        geom = feature.geometry()

        if geom is None or geom.isNull():
            return

        polygon = geom.asPolygon()
        if not polygon:
            return

        # Modify the exterior ring
        exterior = list(polygon[0])

        # Update the corner
        if 0 <= corner_index < len(exterior) - 1:
            exterior[corner_index] = new_position

            # Also update closing point if it's corner 0
            if corner_index == 0:
                exterior[-1] = new_position

        # Reconstruct polygon
        new_polygon = [exterior]
        if len(polygon) > 1:
            # Preserve any interior rings
            new_polygon.extend(polygon[1:])

        new_geom = QgsGeometry.fromPolygonXY(new_polygon)
        layer.changeGeometry(feature_id, new_geom)

    def _find_shared_edges_local(
        self,
        layer: QgsVectorLayer,
        tolerance_m: float
    ) -> Dict[str, Any]:
        """Find shared edges locally (fallback for offline mode)."""
        name_idx = layer.fields().indexOf('name')

        # Extract all edges
        edges_by_feature = {}

        for feature in layer.getFeatures():
            fid = feature.id()
            name = feature.attribute(name_idx) if name_idx >= 0 else f"Feature {fid}"
            geom = feature.geometry()

            if geom is None or geom.isNull():
                continue

            polygon = geom.asPolygon()
            if not polygon:
                continue

            exterior = polygon[0][:-1]  # Exclude closing point
            edges = []

            for i in range(len(exterior)):
                p1 = exterior[i]
                p2 = exterior[(i + 1) % len(exterior)]
                edges.append({
                    'p1': p1,
                    'p2': p2,
                    'midpoint': QgsPointXY(
                        (p1.x() + p2.x()) / 2,
                        (p1.y() + p2.y()) / 2
                    ),
                    'length': self._calculate_distance(p1, p2),
                    'edge_num': i + 1
                })

            edges_by_feature[fid] = {
                'name': name,
                'edges': edges
            }

        # Find shared edges
        shared_edges = []
        neighbor_map = defaultdict(set)

        feature_ids = list(edges_by_feature.keys())

        for i, fid1 in enumerate(feature_ids):
            for fid2 in feature_ids[i + 1:]:
                for edge1 in edges_by_feature[fid1]['edges']:
                    for edge2 in edges_by_feature[fid2]['edges']:
                        # Check if edges match (same endpoints, possibly reversed)
                        if self._edges_match(edge1, edge2, tolerance_m):
                            shared_edges.append({
                                'feature1_id': fid1,
                                'feature1_name': edges_by_feature[fid1]['name'],
                                'edge1_num': edge1['edge_num'],
                                'feature2_id': fid2,
                                'feature2_name': edges_by_feature[fid2]['name'],
                                'edge2_num': edge2['edge_num'],
                                'length': edge1['length']
                            })
                            neighbor_map[fid1].add(fid2)
                            neighbor_map[fid2].add(fid1)

        # Find isolated claims (no neighbors)
        isolated = [
            {
                'feature_id': fid,
                'feature_name': edges_by_feature[fid]['name']
            }
            for fid in feature_ids
            if fid not in neighbor_map
        ]

        return {
            'shared_edges': shared_edges,
            'isolated_claims': isolated,
            'neighbor_map': dict(neighbor_map)
        }

    def _edges_match(
        self,
        edge1: Dict[str, Any],
        edge2: Dict[str, Any],
        tolerance_m: float
    ) -> bool:
        """
        Check if two edges represent the same boundary.

        Args:
            edge1: First edge dictionary
            edge2: Second edge dictionary
            tolerance_m: Tolerance for endpoint matching

        Returns:
            True if edges match (possibly reversed)
        """
        # Check endpoints match (either direction)
        d1_1 = self._calculate_distance(edge1['p1'], edge2['p1'])
        d1_2 = self._calculate_distance(edge1['p2'], edge2['p2'])
        d2_1 = self._calculate_distance(edge1['p1'], edge2['p2'])
        d2_2 = self._calculate_distance(edge1['p2'], edge2['p1'])

        # Same direction
        if d1_1 <= tolerance_m and d1_2 <= tolerance_m:
            return True

        # Reversed direction
        if d2_1 <= tolerance_m and d2_2 <= tolerance_m:
            return True

        return False

    def _calculate_distance(self, p1: QgsPointXY, p2: QgsPointXY) -> float:
        """
        Calculate distance between two points.

        Args:
            p1: First point
            p2: Second point

        Returns:
            Distance in map units (assumed meters for UTM)
        """
        return math.sqrt((p2.x() - p1.x())**2 + (p2.y() - p1.y())**2)
