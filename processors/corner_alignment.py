# -*- coding: utf-8 -*-
"""
Corner alignment for claims management: a thin client of the geodb.io API.

Adjacent claims should share exact corner coordinates; small digitising
errors leave corners slightly apart, which breaks neighbour detection and
duplicates waypoints. The server finds the corner clusters and snaps them
(``POST /api/v2/claims/align-corners/``). This module only reads the
corners off the QGIS layer, sends them, and writes the aligned corners back.

⛔ There is NO local fallback. The local clustering / shared-edge copy was
deleted 2026-09-29 (qgis-thin-client), for the reasons in
``processors/server_required.py``: it fired on any server error, and it was
the server's algorithm published in a public repository. Do not restore it.
"""
from typing import List, Dict, Any

from qgis.core import QgsVectorLayer, QgsGeometry, QgsPointXY

from .geometry_processor import closed_ring_from_corners
from .server_required import require_server
from ..utils.logger import PluginLogger


class CornerAlignmentProcessor:
    """Client of the server's corner alignment. Needs an ``api_client``;
    without one every call refuses (``require_server``)."""

    def __init__(self, api_client=None):
        self.api_client = api_client
        self.logger = PluginLogger.get_logger()

    def set_api_client(self, api_client):
        """Set the API client (the wizard refreshes it on every use)."""
        self.api_client = api_client

    def align_corners(
        self,
        layer: QgsVectorLayer,
        tolerance_m: float = 1.0
    ) -> Dict[str, Any]:
        """
        Snap corners within ``tolerance_m`` of each other to one shared
        position, modifying ``layer`` in place.

        Returns the server's statistics: ``clusters_found``,
        ``corners_moved``, ``max_adjustment`` (metres).
        """
        if not layer or not layer.isValid():
            raise ValueError("Invalid layer")
        require_server(self.api_client, "Aligning corners")

        fids, claims_data = self._extract_claims_corners(layer)
        if not claims_data:
            return {'clusters_found': 0, 'corners_moved': 0, 'max_adjustment': 0}

        endpoint = self.api_client.config.get_claims_url('align-corners/')
        # Async-capable so 700+ claim blocks don't trip the proxy timeout.
        response = self.api_client.post_async_capable(
            endpoint,
            {'claims': claims_data, 'tolerance_m': tolerance_m},
            progress_title="Aligning corners",
        )
        if 'error' in response:
            raise ValueError(response['error'])

        self._apply_aligned_corners(layer, fids, response.get('claims', []))

        statistics = response.get('statistics', {})
        self.logger.info(
            f"[CORNER ALIGNMENT] Server aligned {statistics.get('clusters_found', 0)} clusters, "
            f"moved {statistics.get('corners_moved', 0)} corners, "
            f"max adjustment {statistics.get('max_adjustment', 0):.3f}m"
        )
        return statistics

    # =========================================================================
    # Layer I/O
    # =========================================================================

    def _extract_claims_corners(self, layer: QgsVectorLayer):
        """``(fids, claims)``: one ``{'name', 'corners': [...]}`` per polygon
        feature, exterior ring without its closing vertex.

        ⭐ The name sent is the LIST INDEX, not the claim name: before
        numbering, names can be blank or duplicated, and the server treats
        the name as the claim's identity (two claims called "GE 1" would be
        one claim with eight corners). Same join key as ordering
        (``grid_processor._order_claims_server``).
        """
        fids, claims = [], []
        for feature in layer.getFeatures():
            geom = feature.geometry()
            if geom is None or geom.isNull():
                continue
            polygon = geom.asPolygon()
            if not polygon:
                continue
            claims.append({
                'name': str(len(claims)),
                'corners': [{'easting': p.x(), 'northing': p.y()}
                            for p in polygon[0][:-1]],
            })
            fids.append(feature.id())
        return fids, claims

    def _apply_aligned_corners(
        self,
        layer: QgsVectorLayer,
        fids: List[int],
        aligned_claims: List[Dict[str, Any]]
    ) -> None:
        """Write the server's aligned corners back, joined by list index.
        Validates the whole response BEFORE editing, so a malformed answer
        changes nothing."""
        updates = []
        for aligned_claim in aligned_claims:
            try:
                idx = int(aligned_claim['name'])
            except (KeyError, TypeError, ValueError):
                raise ValueError(f"align-corners response malformed: {aligned_claim!r}")
            if not 0 <= idx < len(fids):
                raise ValueError(f"align-corners returned unknown claim index {idx}")
            corners = aligned_claim.get('corners') or []
            if corners:
                updates.append((fids[idx], corners))

        was_editing = layer.isEditable()
        if not was_editing:
            layer.startEditing()

        for fid, corners in updates:
            # Close the ring safely: the server may return corners already
            # closed, and blindly appending points[0] would duplicate it.
            points = closed_ring_from_corners([
                QgsPointXY(c['easting'], c['northing']) for c in corners
            ])
            layer.changeGeometry(fid, QgsGeometry.fromPolygonXY([points]))

        if not was_editing:
            layer.commitChanges()
