# -*- coding: utf-8 -*-
"""
Geometry processing for coordinate conversion and WKT handling.
"""
from typing import List, Optional, Sequence, Tuple
from qgis.core import QgsGeometry, QgsPointXY

from ..api.exceptions import GeometryError
from ..utils.logger import PluginLogger


def closed_ring_from_corners(
    corners: Sequence[QgsPointXY],
    tolerance: float = 0.0,
) -> List[QgsPointXY]:
    """Build a properly-closed polygon ring from an ordered corner list.

    The corner list may arrive OPEN (``[A, B, C, D]``) or already CLOSED
    (``[A, B, C, D, A]``) — the server returns either shape depending on the
    endpoint and the code path (e.g. after an LM-corner rotation), and QGIS
    ``asPolygon()`` gives closed rings while ``[:-1]`` slices give open ones.
    Callers used to unconditionally ``points.append(points[0])``, which turns
    an already-closed ring into ``[A, B, C, D, A, A]`` — a duplicate closing
    vertex that GEOS reports as invalid geometry, so the claim fails to
    create on the server. (Observed in the field: "two extra duplicate
    vertices" on some claims after Number & Rename / corner rotation.)

    This helper is the single safe way to close a ring:
      1. strip ALL trailing vertices coincident with the first (handles the
         doubled-close case, not just a single closing point), then
      2. append the first vertex exactly once.

    Args:
        corners: Ordered ring vertices, open or closed.
        tolerance: Optional distance under which two vertices count as
            coincident (default 0.0 = exact equality). Useful when server
            rounding leaves a closing point a hair off the first corner.

    Returns:
        A closed ring ``[..., first]`` with exactly one closing vertex, or the
        input unchanged (as a list) if fewer than 3 distinct vertices remain.
    """
    pts = list(corners)
    if len(pts) < 3:
        return pts

    def _coincident(a: QgsPointXY, b: QgsPointXY) -> bool:
        if tolerance <= 0.0:
            return a == b
        return a.distance(b) <= tolerance

    # Drop every trailing vertex that coincides with the first — collapses
    # both a single closing point and an accidentally-doubled one.
    while len(pts) > 1 and _coincident(pts[-1], pts[0]):
        pts.pop()

    if len(pts) < 3:
        return pts

    pts.append(pts[0])
    return pts


def dedupe_ring_corners(
    corners: Sequence[QgsPointXY],
    tolerance: float = 1e-6,
) -> List[QgsPointXY]:
    """Drop consecutive coincident vertices from an OPEN corner list.

    Distinct from ``closed_ring_from_corners`` (which only handles a
    trailing/closing duplicate): this removes duplicates ANYWHERE in the ring,
    including in the middle. That is the exact corruption QGIS snapping
    introduces during a copy/move/snap/delete grid-editing loop — two adjacent
    corners of a claim get snapped onto the same point, producing a ring like
    ``[A, A, C, D]`` or ``[A, B, C, C]``: still 4 entries after the closing
    point is stripped, so a naive ``len(corners) < 4`` guard passes, but the
    zero-length edge makes GEOS reject the polygon and the claim silently
    drops when the layer is built server-side. Observed in the field as "two
    duplicate vertices at the top or bottom" on a subset of claims.

    Compares each vertex against the one KEPT before it (not the raw previous),
    so a run of three-or-more coincident points collapses to a single vertex.
    The wrap-around pair (last vs first) is also collapsed, since on an open
    ring an endpoint duplicating the start is still a zero-length edge once the
    ring is closed.

    Args:
        corners: Ordered, OPEN ring vertices (no closing point).
        tolerance: Distance under which two vertices count as coincident.
            Defaults to 1e-6 (native CRS units — metres for UTM claim layers),
            which absorbs snapping/rounding jitter while never merging genuine
            corners of a mineral claim (metres apart at the smallest).

    Returns:
        A new list with consecutive (and wrap-around) duplicates removed.
    """
    pts = list(corners)
    if len(pts) < 2:
        return pts

    def _coincident(a: QgsPointXY, b: QgsPointXY) -> bool:
        if tolerance <= 0.0:
            return a == b
        return a.distance(b) <= tolerance

    deduped: List[QgsPointXY] = [pts[0]]
    for pt in pts[1:]:
        if not _coincident(pt, deduped[-1]):
            deduped.append(pt)

    # Collapse a wrap-around duplicate (last coincident with first).
    if len(deduped) > 1 and _coincident(deduped[-1], deduped[0]):
        deduped.pop()

    return deduped


def sanitize_polygon_geometry(
    geometry: QgsGeometry,
    epsilon: float = 1e-6,
) -> QgsGeometry:
    """Remove duplicate/degenerate vertices from a polygon before it is sent.

    Last line of defence at the push boundary: whatever built the ring, a
    doubled closing vertex (or two coincident corners collapsed by corner
    alignment) makes GEOS report the ring invalid and the server rejects the
    claim on create. ``removeDuplicateNodes`` collapses coincident vertices
    within ``epsilon``; if the result is still not GEOS-valid we fall back to
    ``makeValid`` so a claim is repaired rather than silently dropped.

    Args:
        geometry: The polygon geometry to clean.
        epsilon: Distance under which adjacent vertices are treated as
            duplicates (native CRS units — metres for the UTM claim layers).

    Returns:
        A cleaned copy. Never returns None; on any failure the input is
        returned unchanged so callers can still push (the server also
        validates).
    """
    if geometry is None or geometry.isNull() or geometry.isEmpty():
        return geometry

    try:
        cleaned = QgsGeometry(geometry)  # work on a copy
        # epsilon>0 with useZValues=False collapses vertices closer than
        # epsilon — this is what removes the doubled closing vertex.
        cleaned.removeDuplicateNodes(epsilon=epsilon, useZValues=False)
        if cleaned.isGeosValid():
            return cleaned
        repaired = cleaned.makeValid()
        if repaired and not repaired.isNull() and repaired.isGeosValid():
            return repaired
        return cleaned
    except Exception:
        return geometry


class GeometryProcessor:
    """
    Handles geometry conversion between QGIS and API formats.
    API uses WKT format with 6 decimal precision.
    """

    COORDINATE_PRECISION = 6

    def __init__(self):
        """Initialize geometry processor."""
        self.logger = PluginLogger.get_logger()

    def qgs_to_wkt(self, geometry: QgsGeometry, precision: int = COORDINATE_PRECISION) -> str:
        """
        Convert QGIS geometry to WKT with specified precision.

        Args:
            geometry: QgsGeometry object
            precision: Decimal places for coordinates (default: 6)

        Returns:
            WKT string
        """
        if geometry is None or geometry.isNull():
            return ''

        try:
            wkt = geometry.asWkt(precision)
            return wkt
        except Exception as e:
            self.logger.error(f"Failed to convert geometry to WKT: {e}")
            raise GeometryError(f"Failed to convert geometry to WKT: {e}")

    def qgs_to_ewkt(
        self,
        geometry: QgsGeometry,
        srid: int = 4326,
        precision: int = COORDINATE_PRECISION
    ) -> str:
        """
        Convert QGIS geometry to EWKT (Extended WKT) with SRID prefix.

        Args:
            geometry: QgsGeometry object
            srid: Spatial Reference ID (default: 4326 for WGS84)
            precision: Decimal places for coordinates (default: 6)

        Returns:
            EWKT string in format "SRID=4326;MULTIPOLYGON(...)"
        """
        if geometry is None or geometry.isNull():
            return ''

        try:
            wkt = geometry.asWkt(precision)
            return f"SRID={srid};{wkt}"
        except Exception as e:
            self.logger.error(f"Failed to convert geometry to EWKT: {e}")
            raise GeometryError(f"Failed to convert geometry to EWKT: {e}")

    def wkt_to_qgs(self, wkt: str) -> Optional[QgsGeometry]:
        """
        Convert WKT to QGIS geometry.

        Args:
            wkt: WKT string

        Returns:
            QgsGeometry object or None if empty
        """
        if not wkt or wkt.strip() == '':
            return None

        try:
            geometry = QgsGeometry.fromWkt(wkt)

            if geometry.isNull():
                raise GeometryError("Invalid WKT string")

            return geometry
        except Exception as e:
            self.logger.error(f"Failed to parse WKT: {e}")
            raise GeometryError(f"Failed to parse WKT: {e}")

    def round_coordinates(
        self,
        geometry: QgsGeometry,
        precision: int = COORDINATE_PRECISION
    ) -> QgsGeometry:
        """
        Round geometry coordinates to specified precision.

        Args:
            geometry: QgsGeometry object
            precision: Decimal places

        Returns:
            New QgsGeometry with rounded coordinates
        """
        if geometry is None or geometry.isNull():
            return geometry

        # Convert to WKT with precision and back
        wkt = self.qgs_to_wkt(geometry, precision)
        return self.wkt_to_qgs(wkt)

    def geometries_equal(
        self,
        geom1: QgsGeometry,
        geom2: QgsGeometry,
        precision: int = COORDINATE_PRECISION
    ) -> bool:
        """
        Compare two geometries with coordinate precision tolerance.

        Args:
            geom1: First geometry
            geom2: Second geometry
            precision: Comparison precision

        Returns:
            True if geometries are equal within precision
        """
        if geom1 is None and geom2 is None:
            return True

        if geom1 is None or geom2 is None:
            return False

        # Compare WKT representations with same precision
        wkt1 = self.qgs_to_wkt(geom1, precision)
        wkt2 = self.qgs_to_wkt(geom2, precision)

        return wkt1 == wkt2

    def get_centroid(self, geometry: QgsGeometry) -> Optional[Tuple[float, float]]:
        """
        Get geometry centroid coordinates.

        Args:
            geometry: QgsGeometry object

        Returns:
            Tuple of (lon, lat) or None
        """
        if geometry is None or geometry.isNull():
            return None

        try:
            centroid = geometry.centroid()
            point = centroid.asPoint()
            return (point.x(), point.y())
        except Exception as e:
            self.logger.error(f"Failed to get centroid: {e}")
            return None
