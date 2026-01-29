# -*- coding: utf-8 -*-
"""
Claims layer generator - generates all supporting layers for claims workflow.

Generates the following layers from a claims polygon layer:
- Corner Points: All 4 corners of each claim with labels
- LM Corners: Filtered to only Corner 1 points
- Center Lines: LineString connecting endline midpoints
- Monuments: Discovery monuments inset along centerline
- Sideline Monuments: (Wyoming) midpoints of long sides
- Endline Monuments: (Arizona) midpoints of short sides

This replicates the QClaims "Initialize Claims" functionality.

NOTE: All calculations are performed SERVER-SIDE via the geodb.io API.
The client only receives coordinate data and creates QGIS layers for display.
This protects proprietary algorithms while keeping the plugin open-source.
"""
import json
from typing import Dict, List, Optional, Tuple, Any, TYPE_CHECKING

from qgis.core import (
    QgsProject, QgsVectorLayer, QgsFeature, QgsGeometry,
    QgsPointXY, QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsField, QgsFields, QgsWkbTypes, QgsLineString, QgsPoint,
    QgsSymbol, QgsSingleSymbolRenderer, QgsSimpleMarkerSymbolLayer,
    QgsSimpleLineSymbolLayer, QgsPalLayerSettings, QgsTextFormat,
    QgsVectorLayerSimpleLabeling, QgsMessageLog, Qgis
)
from qgis.PyQt.QtCore import QMetaType
from qgis.PyQt.QtGui import QColor, QFont

from ..utils.logger import PluginLogger


def _qgis_log(message: str, level: Qgis.MessageLevel = Qgis.Info):
    """Log to QGIS Message Log panel for visibility."""
    QgsMessageLog.logMessage(message, 'GeodbIO Claims', level)

if TYPE_CHECKING:
    from ..managers.claims_manager import ClaimsManager


class ClaimsLayerGenerator:
    """
    Generates all supporting layers for the claims workflow.

    Takes a claims polygon layer and generates:
    - Corner points for labeling
    - LM corners (Corner 1 only)
    - Centerlines for monument placement reference
    - Discovery monuments
    - State-specific monuments (sideline/endline)
    """

    # Standard lode claim dimensions
    LODE_WIDTH_FT = 600
    LODE_LENGTH_FT = 1500
    FEET_TO_METERS = 0.3048

    # Layer names
    LODE_CLAIMS_LAYER = "Lode Claims"  # Main claims polygon layer with QClaims fields
    CORNER_POINTS_LAYER = "Corner Points"
    LM_CORNERS_LAYER = "LM Corners"
    CENTERLINES_LAYER = "Center Lines"
    MONUMENTS_LAYER = "Monuments"
    SIDELINE_MONUMENTS_LAYER = "Sideline Monuments"
    ENDLINE_MONUMENTS_LAYER = "Endline Monuments"

    def __init__(self, claims_storage_manager=None, claims_manager: 'ClaimsManager' = None):
        """
        Initialize the layer generator.

        Args:
            claims_storage_manager: Optional ClaimsStorageManager for GeoPackage persistence
            claims_manager: Optional ClaimsManager for server API calls
        """
        self.storage_manager = claims_storage_manager
        self.claims_manager = claims_manager
        self.logger = PluginLogger.get_logger()
        self._geopackage_path: Optional[str] = None
        self._project_name: Optional[str] = None  # For layer naming suffix

        # Configuration
        self.monument_inset_ft = 25.0  # Default 25 feet
        self.buffer_distance = -40  # For inset corner labels (meters)

    def set_claims_manager(self, claims_manager: 'ClaimsManager'):
        """Set the claims manager for server API calls."""
        self.claims_manager = claims_manager

    def set_geopackage_path(self, gpkg_path: Optional[str]):
        """Set the GeoPackage path for persistent storage."""
        self._geopackage_path = gpkg_path

    def set_project_name(self, project_name: Optional[str]):
        """
        Set the project name for layer naming.

        When set, layers will be named like "Corner Points [Project Name]".

        Args:
            project_name: Project name to append to layer names (e.g., "GE2 Lode Claims")
        """
        self._project_name = project_name

    def _get_display_name(self, base_name: str) -> str:
        """
        Get the display name for a layer, including project suffix if set.

        Args:
            base_name: Base layer name (e.g., "Corner Points")

        Returns:
            Display name with optional project suffix (e.g., "Corner Points [GE2 Lode Claims]")
        """
        if self._project_name:
            return f"{base_name} [{self._project_name}]"
        return base_name

    def set_monument_inset(self, inset_ft: float):
        """
        Set the monument inset distance in feet.

        Args:
            inset_ft: Monument inset distance from centerline endpoint in feet
        """
        self.monument_inset_ft = inset_ft

    def generate_layers_from_server(
        self,
        claims_layer: QgsVectorLayer,
        state: Optional[str] = None
    ) -> Dict[str, QgsVectorLayer]:
        """
        Generate preview layers using server-side calculations.

        This method sends claim geometries to the server API, which performs
        all proprietary calculations, and returns layer data that is then
        rendered as QGIS layers.

        IMPORTANT: Geometries are sent as WKT with explicit EPSG to preserve
        UTM coordinate precision. GeoJSON conventionally expects WGS84 lon/lat
        which causes coordinate interpretation issues with projected CRS.

        Args:
            claims_layer: QgsVectorLayer with claim polygons
            state: Optional state code for monument type determination

        Returns:
            Dict mapping layer names to QgsVectorLayer objects

        Raises:
            RuntimeError: If claims_manager is not set or server call fails
        """
        if not self.claims_manager:
            raise RuntimeError("Claims manager not configured. Server connection required.")

        self.logger.info(f"[CLAIMS] Generating layers from server for {claims_layer.featureCount()} claims")

        crs = claims_layer.crs()
        epsg = int(crs.authid().split(':')[1]) if ':' in crs.authid() else 4326

        # Extract claims data and convert to server format
        claims_data = self._extract_claims_data(claims_layer)
        if not claims_data:
            self.logger.warning("[CLAIMS] No claims found in layer")
            return {}

        # Convert to server request format
        # CRITICAL: Use WKT format with explicit EPSG to preserve UTM coordinates
        # GeoJSON conventionally expects WGS84 lon/lat, which causes precision loss
        server_claims = []
        for claim in claims_data:
            # Get the original geometry as WKT with 8 decimal place precision
            # This preserves sub-micrometer precision for UTM coordinates while avoiding
            # floating-point noise from full 17 decimal places. Using 8 decimals ensures
            # that snapped/shared corners between adjacent claims remain exactly aligned.
            geometry_wkt = claim['geometry'].asWkt(8)

            server_claims.append({
                'name': claim['name'],
                'geometry_wkt': geometry_wkt,  # WKT with 8 decimal precision
                'epsg': epsg,  # Explicit EPSG for coordinate interpretation
                'lm_corner': claim.get('lm_corner', 1),
                'notes': claim.get('notes', '')  # Per-claim notes for location notices
            })

        # Call server API
        response = self.claims_manager.get_preview_layers(
            claims=server_claims,
            epsg=epsg,
            monument_inset_ft=self.monument_inset_ft,
            state=state
        )

        if not response or 'layers' not in response:
            raise RuntimeError("Server returned empty response")

        # Create layers from server response
        layers = self._create_layers_from_server_response(response, crs)
        self.logger.info(f"[CLAIMS] Generated {len(layers)} layers from server")
        return layers

    def _create_layers_from_server_response(
        self,
        response: Dict,
        crs: QgsCoordinateReferenceSystem
    ) -> Dict[str, QgsVectorLayer]:
        """
        Create QGIS layers from server API response.

        Args:
            response: Server response with layers data
            crs: Coordinate reference system for the layers

        Returns:
            Dict mapping layer names to QgsVectorLayer objects
        """
        self.logger.info(f"[CLAIMS DEBUG] _create_layers_from_server_response called, CRS: {crs.authid()}")

        layers = {}
        layer_data = response.get('layers', {})

        self.logger.info(f"[CLAIMS DEBUG] layer_data keys: {list(layer_data.keys())}")
        for key, data in layer_data.items():
            count = len(data) if isinstance(data, list) else 'not a list'
            self.logger.info(f"[CLAIMS DEBUG]   {key}: {count} items")

        # ================================================================
        # Create Lode Claims layer - main polygon layer with all QClaims fields
        # This is the primary layer for ID/NM corner adjustment workflow
        # ================================================================
        lode_claims = layer_data.get('lode_claims', [])
        if lode_claims:
            self.logger.info(f"[CLAIMS DEBUG] Creating Lode Claims layer with {len(lode_claims)} claims")
            try:
                layer = self._create_polygon_layer(
                    self.LODE_CLAIMS_LAYER,
                    crs,
                    [
                        ("FID", QMetaType.Type.Int),
                        ("Name", QMetaType.Type.QString),
                        ("LM Corner", QMetaType.Type.Int),
                        ("Manual FID", QMetaType.Type.Int),
                        ("Notes", QMetaType.Type.QString),
                        ("Lode_Azimuth", QMetaType.Type.Double),
                        ("Dimensions", QMetaType.Type.QString),
                        ("Corner 1", QMetaType.Type.QString),
                        ("State", QMetaType.Type.QString),
                        ("County", QMetaType.Type.QString),
                    ]
                )
                if layer:
                    features = []
                    for claim_data in lode_claims:
                        feature = QgsFeature(layer.fields())

                        # Build polygon from corners (UTM coordinates)
                        corners = claim_data.get('corners', [])
                        self.logger.debug(f"[CLAIMS DEBUG] Claim '{claim_data.get('name')}' has {len(corners)} corners")
                        if len(corners) >= 4:
                            # Validate corner data before creating geometry
                            valid_corners = True
                            for i, c in enumerate(corners):
                                if c.get('easting') is None or c.get('northing') is None:
                                    self.logger.error(f"[CLAIMS DEBUG] Corner {i} has None coordinates: {c}")
                                    valid_corners = False
                                    break

                            if valid_corners:
                                points = [
                                    QgsPointXY(c['easting'], c['northing'])
                                    for c in corners
                                ]
                                # Close the ring
                                points.append(points[0])
                                feature.setGeometry(QgsGeometry.fromPolygonXY([points]))
                            else:
                                self.logger.error(f"[CLAIMS DEBUG] Skipping geometry for '{claim_data.get('name')}' due to invalid corners")

                        # Set attributes matching QClaims field structure
                        feature.setAttribute("FID", claim_data.get('fid', 0))
                        feature.setAttribute("Name", claim_data.get('name', ''))
                        feature.setAttribute("LM Corner", claim_data.get('lm_corner', 1))
                        feature.setAttribute("Manual FID", claim_data.get('manual_fid', 0))
                        feature.setAttribute("Notes", claim_data.get('notes') or '')
                        feature.setAttribute("Lode_Azimuth", claim_data.get('lode_azimuth', 0.0))
                        feature.setAttribute("Dimensions", claim_data.get('dimensions', ''))
                        feature.setAttribute("Corner 1", claim_data.get('corner_1', ''))
                        feature.setAttribute("State", claim_data.get('state', ''))
                        feature.setAttribute("County", claim_data.get('county', ''))
                        features.append(feature)

                    layer.dataProvider().addFeatures(features)
                    layer.updateExtents()
                    layers[self.LODE_CLAIMS_LAYER] = layer
                    self.logger.info(f"[CLAIMS DEBUG] Created Lode Claims layer with {len(features)} features")
            except Exception as e:
                self.logger.error(f"[CLAIMS DEBUG] Failed to create Lode Claims layer: {e}")
                import traceback
                self.logger.error(traceback.format_exc())

        # Create corner points layer
        corner_points = layer_data.get('corner_points', [])
        if corner_points:
            self.logger.info(f"[CLAIMS DEBUG] Creating Corner Points layer with {len(corner_points)} points")
            try:
                layer = self._create_point_layer(
                    self.CORNER_POINTS_LAYER,
                    crs,
                    [
                        ("Corner #", QMetaType.Type.Int),
                        ("Claim", QMetaType.Type.QString),
                        ("Easting", QMetaType.Type.Double),
                        ("Northing", QMetaType.Type.Double),
                    ]
                )
                if layer:
                    features = []
                    for pt in corner_points:
                        # Validate coordinates
                        easting = pt.get('easting')
                        northing = pt.get('northing')
                        if easting is None or northing is None:
                            self.logger.error(f"[CLAIMS DEBUG] Corner point has None coordinates: {pt}")
                            continue
                        feature = QgsFeature(layer.fields())
                        feature.setGeometry(QgsGeometry.fromPointXY(
                            QgsPointXY(easting, northing)
                        ))
                        feature.setAttribute("Corner #", pt.get('corner_number', 0))
                        feature.setAttribute("Claim", pt.get('claim_name', ''))
                        feature.setAttribute("Easting", easting)
                        feature.setAttribute("Northing", northing)
                        features.append(feature)
                    layer.dataProvider().addFeatures(features)
                    layer.updateExtents()
                    layers[self.CORNER_POINTS_LAYER] = layer
                    self.logger.info(f"[CLAIMS DEBUG] Created Corner Points layer with {len(features)} features")
            except Exception as e:
                self.logger.error(f"[CLAIMS DEBUG] Failed to create Corner Points layer: {e}")
                import traceback
                self.logger.error(traceback.format_exc())

        # Create LM corners layer - filter Corner Points for Corner #1 (the LM corner)
        # This ensures LM corners are at EXACTLY the same position as Corner Points,
        # providing a visual indicator overlay (green dot on top of black dot)
        lm_corner_points = [pt for pt in corner_points if pt.get('corner_number') == 1]
        if lm_corner_points:
            self.logger.info(f"[CLAIMS DEBUG] Creating LM Corners layer with {len(lm_corner_points)} points (filtered from Corner Points)")
            try:
                layer = self._create_point_layer(
                    self.LM_CORNERS_LAYER,
                    crs,
                    [
                        ("Corner #", QMetaType.Type.Int),
                        ("Claim", QMetaType.Type.QString),
                        ("Easting", QMetaType.Type.Double),
                        ("Northing", QMetaType.Type.Double),
                    ]
                )
                if layer:
                    features = []
                    for pt in lm_corner_points:
                        # Use the exact same coordinates as Corner Points
                        easting = pt.get('easting')
                        northing = pt.get('northing')
                        if easting is None or northing is None:
                            self.logger.error(f"[CLAIMS DEBUG] LM corner point has None coordinates: {pt}")
                            continue
                        feature = QgsFeature(layer.fields())
                        feature.setGeometry(QgsGeometry.fromPointXY(
                            QgsPointXY(easting, northing)
                        ))
                        feature.setAttribute("Corner #", 1)
                        feature.setAttribute("Claim", pt.get('claim_name', ''))
                        feature.setAttribute("Easting", easting)
                        feature.setAttribute("Northing", northing)
                        features.append(feature)
                    layer.dataProvider().addFeatures(features)
                    layer.updateExtents()
                    layers[self.LM_CORNERS_LAYER] = layer
                    self.logger.info(f"[CLAIMS DEBUG] Created LM Corners layer with {len(features)} features")
            except Exception as e:
                self.logger.error(f"[CLAIMS DEBUG] Failed to create LM Corners layer: {e}")
                import traceback
                self.logger.error(traceback.format_exc())

        # Create centerlines layer
        centerlines = layer_data.get('centerlines', [])
        if centerlines:
            self.logger.info(f"[CLAIMS DEBUG] Creating Centerlines layer with {len(centerlines)} lines")
            try:
                layer = self._create_line_layer(
                    self.CENTERLINES_LAYER,
                    crs,
                    [("Name", QMetaType.Type.QString)]
                )
                if layer:
                    features = []
                    for cl in centerlines:
                        # Validate coordinates
                        start_e = cl.get('start_easting')
                        start_n = cl.get('start_northing')
                        end_e = cl.get('end_easting')
                        end_n = cl.get('end_northing')
                        if None in (start_e, start_n, end_e, end_n):
                            self.logger.error(f"[CLAIMS DEBUG] Centerline has None coordinates: {cl}")
                            continue
                        line = QgsGeometry.fromPolylineXY([
                            QgsPointXY(start_e, start_n),
                            QgsPointXY(end_e, end_n)
                        ])
                        feature = QgsFeature(layer.fields())
                        feature.setGeometry(line)
                        feature.setAttribute("Name", cl.get('claim_name', ''))
                        features.append(feature)
                    layer.dataProvider().addFeatures(features)
                    layer.updateExtents()
                    layers[self.CENTERLINES_LAYER] = layer
                    self.logger.info(f"[CLAIMS DEBUG] Created Centerlines layer with {len(features)} features")
            except Exception as e:
                self.logger.error(f"[CLAIMS DEBUG] Failed to create Centerlines layer: {e}")
                import traceback
                self.logger.error(traceback.format_exc())

        # Create monuments layer
        monuments = layer_data.get('monuments', [])
        if monuments:
            self.logger.info(f"[CLAIMS DEBUG] Creating Monuments layer with {len(monuments)} points")
            try:
                layer = self._create_point_layer(
                    self.MONUMENTS_LAYER,
                    crs,
                    [
                        ("Claim", QMetaType.Type.QString),
                        ("Name", QMetaType.Type.QString),
                        ("Easting", QMetaType.Type.Double),
                        ("Northing", QMetaType.Type.Double),
                    ]
                )
                if layer:
                    features = []
                    for i, mon in enumerate(monuments):
                        easting = mon.get('easting')
                        northing = mon.get('northing')
                        if easting is None or northing is None:
                            self.logger.error(f"[CLAIMS DEBUG] Monument has None coordinates: {mon}")
                            continue
                        feature = QgsFeature(layer.fields())
                        feature.setGeometry(QgsGeometry.fromPointXY(
                            QgsPointXY(easting, northing)
                        ))
                        feature.setAttribute("Claim", mon.get('claim_name', ''))
                        feature.setAttribute("Name", f"LM {i+1}")
                        feature.setAttribute("Easting", easting)
                        feature.setAttribute("Northing", northing)
                        features.append(feature)
                    layer.dataProvider().addFeatures(features)
                    layer.updateExtents()
                    layers[self.MONUMENTS_LAYER] = layer
                    self.logger.info(f"[CLAIMS DEBUG] Created Monuments layer with {len(features)} features")
            except Exception as e:
                self.logger.error(f"[CLAIMS DEBUG] Failed to create Monuments layer: {e}")
                import traceback
                self.logger.error(traceback.format_exc())

        # Create sideline monuments layer
        sideline_monuments = layer_data.get('sideline_monuments', [])
        if sideline_monuments:
            self.logger.info(f"[CLAIMS DEBUG] Creating Sideline Monuments layer with {len(sideline_monuments)} points")
            try:
                layer = self._create_point_layer(
                    self.SIDELINE_MONUMENTS_LAYER,
                    crs,
                    [
                        ("Claim", QMetaType.Type.QString),
                        ("Name", QMetaType.Type.QString),
                        ("Easting", QMetaType.Type.Double),
                        ("Northing", QMetaType.Type.Double),
                    ]
                )
                if layer:
                    features = []
                    for mon in sideline_monuments:
                        easting = mon.get('easting')
                        northing = mon.get('northing')
                        if easting is None or northing is None:
                            self.logger.error(f"[CLAIMS DEBUG] Sideline monument has None coordinates: {mon}")
                            continue
                        feature = QgsFeature(layer.fields())
                        feature.setGeometry(QgsGeometry.fromPointXY(
                            QgsPointXY(easting, northing)
                        ))
                        feature.setAttribute("Claim", mon.get('claim_name', ''))
                        feature.setAttribute("Name", mon.get('name', ''))
                        feature.setAttribute("Easting", easting)
                        feature.setAttribute("Northing", northing)
                        features.append(feature)
                    layer.dataProvider().addFeatures(features)
                    layer.updateExtents()
                    layers[self.SIDELINE_MONUMENTS_LAYER] = layer
                    self.logger.info(f"[CLAIMS DEBUG] Created Sideline Monuments layer with {len(features)} features")
            except Exception as e:
                self.logger.error(f"[CLAIMS DEBUG] Failed to create Sideline Monuments layer: {e}")
                import traceback
                self.logger.error(traceback.format_exc())

        # Create endline monuments layer
        endline_monuments = layer_data.get('endline_monuments', [])
        if endline_monuments:
            self.logger.info(f"[CLAIMS DEBUG] Creating Endline Monuments layer with {len(endline_monuments)} points")
            try:
                layer = self._create_point_layer(
                    self.ENDLINE_MONUMENTS_LAYER,
                    crs,
                    [
                        ("Claim", QMetaType.Type.QString),
                        ("Name", QMetaType.Type.QString),
                        ("Easting", QMetaType.Type.Double),
                        ("Northing", QMetaType.Type.Double),
                    ]
                )
                if layer:
                    features = []
                    for mon in endline_monuments:
                        easting = mon.get('easting')
                        northing = mon.get('northing')
                        if easting is None or northing is None:
                            self.logger.error(f"[CLAIMS DEBUG] Endline monument has None coordinates: {mon}")
                            continue
                        feature = QgsFeature(layer.fields())
                        feature.setGeometry(QgsGeometry.fromPointXY(
                            QgsPointXY(easting, northing)
                        ))
                        feature.setAttribute("Claim", mon.get('claim_name', ''))
                        feature.setAttribute("Name", mon.get('name', ''))
                        feature.setAttribute("Easting", easting)
                        feature.setAttribute("Northing", northing)
                        features.append(feature)
                    layer.dataProvider().addFeatures(features)
                    layer.updateExtents()
                    layers[self.ENDLINE_MONUMENTS_LAYER] = layer
                    self.logger.info(f"[CLAIMS DEBUG] Created Endline Monuments layer with {len(features)} features")
            except Exception as e:
                self.logger.error(f"[CLAIMS DEBUG] Failed to create Endline Monuments layer: {e}")
                import traceback
                self.logger.error(traceback.format_exc())

        self.logger.info(f"[CLAIMS DEBUG] _create_layers_from_server_response finished, created layers: {list(layers.keys())}")
        return layers

    def update_lm_corner_from_server(
        self,
        claims_layer: QgsVectorLayer,
        claim_name: str,
        new_lm_corner: int
    ) -> Dict[str, QgsVectorLayer]:
        """
        Update LM corner via server and return refreshed layers.

        This sends the LM corner change to the server, which rotates the
        claim geometry and recalculates all monuments/layers.

        The server returns the rotated geometry in UTM coordinates, which is
        used to update the source claims layer. This matches QClaims behavior
        where changing the LM Corner rotates the claim polygon geometry so that
        the new corner becomes Corner 1.

        IMPORTANT: Geometries are sent as WKT with explicit EPSG to preserve
        UTM coordinate precision. This prevents coordinate transformation errors.

        Args:
            claims_layer: The claims layer (source layer to update)
            claim_name: Name of the claim to update
            new_lm_corner: New LM corner (1-4)

        Returns:
            Updated layers dict

        Raises:
            RuntimeError: If claims_manager is not set or server call fails
        """
        if not self.claims_manager:
            raise RuntimeError("Claims manager not configured. Server connection required.")

        self.logger.info(f"[CLAIMS DEBUG] update_lm_corner_from_server called for '{claim_name}' -> corner {new_lm_corner}")

        crs = claims_layer.crs()
        epsg = int(crs.authid().split(':')[1]) if ':' in crs.authid() else 4326
        self.logger.info(f"[CLAIMS DEBUG] Layer CRS: {crs.authid()}, EPSG: {epsg}")

        # Extract claims and update the specific claim's lm_corner
        # CRITICAL: Use WKT format with explicit EPSG to preserve UTM coordinates
        claims_data = self._extract_claims_data(claims_layer)
        self.logger.info(f"[CLAIMS DEBUG] Extracted {len(claims_data)} claims from layer")

        server_claims = []
        for claim in claims_data:
            # Get the original geometry as WKT with 8 decimal place precision
            # This preserves sub-micrometer precision for UTM coordinates while avoiding
            # floating-point noise from full 17 decimal places. Using 8 decimals ensures
            # that snapped/shared corners between adjacent claims remain exactly aligned.
            geometry_wkt = claim['geometry'].asWkt(8)

            lm_corner = new_lm_corner if claim['name'] == claim_name else claim.get('lm_corner', 1)

            server_claims.append({
                'name': claim['name'],
                'geometry_wkt': geometry_wkt,  # WKT with 8 decimal precision
                'epsg': epsg,  # Explicit EPSG for coordinate interpretation
                'lm_corner': lm_corner,
                'notes': claim.get('notes', '')  # Per-claim notes for location notices
            })
            self.logger.debug(f"[CLAIMS DEBUG] Claim '{claim['name']}': lm_corner={lm_corner}, wkt_len={len(geometry_wkt)}")

        self.logger.info(f"[CLAIMS DEBUG] Sending {len(server_claims)} claims to server, monument_inset_ft={self.monument_inset_ft}")

        try:
            response = self.claims_manager.update_lm_corner_with_layers(
                claims=server_claims,
                epsg=epsg,
                monument_inset_ft=self.monument_inset_ft
            )

            # Debug: Log response structure
            if response:
                self.logger.info(f"[CLAIMS DEBUG] Server response keys: {list(response.keys())}")
                if 'layers' in response:
                    layers_keys = list(response['layers'].keys()) if isinstance(response['layers'], dict) else 'not a dict'
                    self.logger.info(f"[CLAIMS DEBUG] Response layers keys: {layers_keys}")
                    for layer_name, layer_data in response.get('layers', {}).items():
                        count = len(layer_data) if isinstance(layer_data, list) else 'not a list'
                        self.logger.info(f"[CLAIMS DEBUG]   - {layer_name}: {count} features")
                if 'claims' in response:
                    self.logger.info(f"[CLAIMS DEBUG] Response claims count: {len(response.get('claims', []))}")
                    for claim_resp in response.get('claims', []):
                        has_utm = 'rotated_geometry_utm' in claim_resp
                        has_wgs84 = 'rotated_geometry' in claim_resp
                        self.logger.info(f"[CLAIMS DEBUG]   - {claim_resp.get('name')}: has_utm={has_utm}, has_wgs84={has_wgs84}")
                if 'error' in response:
                    self.logger.error(f"[CLAIMS DEBUG] Server returned error: {response['error']}")
            else:
                self.logger.warning("[CLAIMS DEBUG] Server returned None/empty response")

            if not response or 'layers' not in response:
                self.logger.warning("[CLAIMS] Server returned empty response or missing 'layers' key")
                return {}

            # Update the local claims layer geometry with rotated version
            # Use UTM coordinates (rotated_geometry_utm) if available, as the
            # source layer is typically in a projected CRS (UTM)
            self.logger.info(f"[CLAIMS DEBUG] Looking for rotated geometry for '{claim_name}'...")
            for claim_resp in response.get('claims', []):
                if claim_resp['name'] == claim_name:
                    # Prefer UTM geometry for projected layers
                    rotated_geom_utm = claim_resp.get('rotated_geometry_utm')
                    if rotated_geom_utm:
                        self.logger.info(f"[CLAIMS DEBUG] Found rotated_geometry_utm for '{claim_name}', updating local layer")
                        self._update_claim_geometry_utm(claims_layer, claim_name, rotated_geom_utm)
                    else:
                        # Fallback to WGS84 geometry (for unprojected layers)
                        rotated_geom = claim_resp.get('rotated_geometry')
                        if rotated_geom:
                            self.logger.info(f"[CLAIMS DEBUG] Using WGS84 fallback for '{claim_name}'")
                            self._update_claim_geometry(claims_layer, claim_name, rotated_geom, crs)
                        else:
                            self.logger.warning(f"[CLAIMS DEBUG] No rotated geometry found for '{claim_name}'!")

            self.logger.info("[CLAIMS DEBUG] Creating layers from server response...")
            result_layers = self._create_layers_from_server_response(response, crs)
            self.logger.info(f"[CLAIMS DEBUG] Created {len(result_layers)} layers: {list(result_layers.keys())}")
            return result_layers

        except Exception as e:
            self.logger.error(f"[CLAIMS] Server LM corner update failed: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return {}

    def update_lm_corners_batch(
        self,
        claims_layer: QgsVectorLayer,
        lm_corner_changes: Dict[str, int]
    ) -> Dict[str, QgsVectorLayer]:
        """
        Update multiple LM corners in a single server call.

        This is more efficient than calling update_lm_corner_from_server multiple
        times, as it batches all changes into a single API request.

        Args:
            claims_layer: The claims layer (source layer to update)
            lm_corner_changes: Dict mapping claim_name -> new_lm_corner value

        Returns:
            Updated layers dict

        Raises:
            RuntimeError: If claims_manager is not set or server call fails
        """
        if not self.claims_manager:
            raise RuntimeError("Claims manager not configured. Server connection required.")

        if not lm_corner_changes:
            self.logger.warning("[CLAIMS] No LM corner changes to apply")
            return {}

        self.logger.info(f"[CLAIMS DEBUG] update_lm_corners_batch called with {len(lm_corner_changes)} changes: {lm_corner_changes}")

        crs = claims_layer.crs()
        epsg = int(crs.authid().split(':')[1]) if ':' in crs.authid() else 4326
        self.logger.info(f"[CLAIMS DEBUG] Layer CRS: {crs.authid()}, EPSG: {epsg}")

        # Extract claims and apply ALL lm_corner changes at once
        claims_data = self._extract_claims_data(claims_layer)
        self.logger.info(f"[CLAIMS DEBUG] Extracted {len(claims_data)} claims from layer")

        server_claims = []
        for claim in claims_data:
            geometry_wkt = claim['geometry'].asWkt(8)

            # Apply the new lm_corner if this claim has a change, otherwise keep existing
            if claim['name'] in lm_corner_changes:
                lm_corner = lm_corner_changes[claim['name']]
                self.logger.info(f"[CLAIMS DEBUG] Applying lm_corner={lm_corner} to '{claim['name']}'")
            else:
                lm_corner = claim.get('lm_corner', 1)

            server_claims.append({
                'name': claim['name'],
                'geometry_wkt': geometry_wkt,
                'epsg': epsg,
                'lm_corner': lm_corner,
                'notes': claim.get('notes', '')  # Per-claim notes for location notices
            })

        self.logger.info(f"[CLAIMS DEBUG] Sending {len(server_claims)} claims to server (batch), monument_inset_ft={self.monument_inset_ft}")

        try:
            response = self.claims_manager.update_lm_corner_with_layers(
                claims=server_claims,
                epsg=epsg,
                monument_inset_ft=self.monument_inset_ft
            )

            if not response or 'layers' not in response:
                self.logger.warning("[CLAIMS] Server returned empty response or missing 'layers' key")
                return {}

            # Update ALL claim geometries that were changed
            for claim_name in lm_corner_changes.keys():
                self.logger.info(f"[CLAIMS DEBUG] Looking for rotated geometry for '{claim_name}'...")
                for claim_resp in response.get('claims', []):
                    if claim_resp['name'] == claim_name:
                        rotated_geom_utm = claim_resp.get('rotated_geometry_utm')
                        if rotated_geom_utm:
                            self.logger.info(f"[CLAIMS DEBUG] Found rotated_geometry_utm for '{claim_name}', updating local layer")
                            self._update_claim_geometry_utm(claims_layer, claim_name, rotated_geom_utm)
                        else:
                            rotated_geom = claim_resp.get('rotated_geometry')
                            if rotated_geom:
                                self.logger.info(f"[CLAIMS DEBUG] Using WGS84 fallback for '{claim_name}'")
                                self._update_claim_geometry(claims_layer, claim_name, rotated_geom, crs)
                        break

            self.logger.info("[CLAIMS DEBUG] Creating layers from server response (batch)...")
            result_layers = self._create_layers_from_server_response(response, crs)
            self.logger.info(f"[CLAIMS DEBUG] Created {len(result_layers)} layers: {list(result_layers.keys())}")
            return result_layers

        except Exception as e:
            self.logger.error(f"[CLAIMS] Server batch LM corner update failed: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return {}

    def _update_claim_geometry(
        self,
        claims_layer: QgsVectorLayer,
        claim_name: str,
        geojson_geom: Dict,
        crs: QgsCoordinateReferenceSystem
    ):
        """
        Update a claim's geometry from GeoJSON (WGS84 coordinates).

        WARNING: This is a FALLBACK method used when the server doesn't return
        UTM coordinates (rotated_geometry_utm). Using this method introduces
        coordinate transformation which can cause slight misalignment.

        The preferred path is to use _update_claim_geometry_utm() with UTM
        coordinates directly from the server.

        Args:
            claims_layer: The claims layer to update
            claim_name: Name of the claim
            geojson_geom: GeoJSON geometry dict with lon/lat coordinates (WGS84)
            crs: Target CRS (the layer's coordinate reference system)
        """
        self.logger.warning(
            f"[CLAIMS] Using WGS84 fallback for {claim_name} - this may cause "
            "slight coordinate misalignment. Server should return rotated_geometry_utm."
        )
        try:
            # Find the feature
            for feature in claims_layer.getFeatures():
                name = feature.attribute('name') or feature.attribute('Name') or ""
                if name != claim_name:
                    continue

                # Convert GeoJSON to QgsGeometry
                # GeoJSON uses lon/lat (WGS84), we need to transform if CRS is projected
                coords = geojson_geom.get('coordinates', [[]])[0]
                if not coords:
                    return

                # Build polygon points in WGS84 first
                wgs84_points = [QgsPointXY(c[0], c[1]) for c in coords]
                wgs84_geom = QgsGeometry.fromPolygonXY([wgs84_points])

                # Transform from WGS84 to the layer's CRS if needed
                wgs84_crs = QgsCoordinateReferenceSystem('EPSG:4326')
                if crs != wgs84_crs and crs.isValid():
                    # Create coordinate transform: WGS84 -> Layer CRS
                    transform = QgsCoordinateTransform(
                        wgs84_crs,
                        crs,
                        QgsProject.instance()
                    )
                    # Transform the geometry in place
                    wgs84_geom.transform(transform)
                    self.logger.info(
                        f"[CLAIMS] Transformed geometry from WGS84 to {crs.authid()} for {claim_name}"
                    )

                # Update the feature
                claims_layer.startEditing()
                claims_layer.changeGeometry(feature.id(), wgs84_geom)

                # Set LM Corner to 1 (it's now rotated)
                lm_corner_idx = claims_layer.fields().indexOf('LM Corner')
                if lm_corner_idx < 0:
                    lm_corner_idx = claims_layer.fields().indexOf('lm_corner')
                if lm_corner_idx >= 0:
                    claims_layer.changeAttributeValue(feature.id(), lm_corner_idx, 1)

                claims_layer.commitChanges()
                self.logger.info(f"[CLAIMS] Updated geometry for {claim_name}")
                return

        except Exception as e:
            self.logger.error(f"[CLAIMS] Failed to update claim geometry: {e}")
            import traceback
            self.logger.error(traceback.format_exc())

    def _update_claim_geometry_utm(
        self,
        claims_layer: QgsVectorLayer,
        claim_name: str,
        geojson_geom: Dict
    ):
        """
        Update a claim's geometry from UTM coordinates.

        This method updates the source claims layer with the rotated geometry
        returned from the server. The geometry coordinates are in UTM
        (easting/northing), matching the typical CRS of claims layers.

        This implements the same behavior as QClaims update_lm_corner():
        when the LM Corner is changed, the polygon geometry is rotated so
        that the new corner becomes Corner 1 (first vertex).

        Args:
            claims_layer: The claims layer to update (source layer)
            claim_name: Name of the claim to update
            geojson_geom: GeoJSON-style geometry dict with UTM coordinates
                          (easting/northing in the 'coordinates' array)
        """
        self.logger.info(f"[CLAIMS DEBUG] _update_claim_geometry_utm called for '{claim_name}'")
        self.logger.info(f"[CLAIMS DEBUG] geojson_geom type: {type(geojson_geom)}, keys: {list(geojson_geom.keys()) if isinstance(geojson_geom, dict) else 'not a dict'}")

        try:
            # Find the feature by name
            feature_found = False
            for feature in claims_layer.getFeatures():
                name = feature.attribute('name') or feature.attribute('Name') or ""
                if name != claim_name:
                    continue

                feature_found = True
                self.logger.info(f"[CLAIMS DEBUG] Found feature for '{claim_name}', feature id: {feature.id()}")

                # Extract UTM coordinates from the geometry
                coords = geojson_geom.get('coordinates', [[]])[0]
                self.logger.info(f"[CLAIMS DEBUG] Extracted {len(coords) if coords else 0} coordinates from geojson_geom")

                if not coords:
                    self.logger.warning(f"[CLAIMS] No coordinates in rotated geometry for {claim_name}")
                    return

                # Log first and last coordinate for verification
                self.logger.info(f"[CLAIMS DEBUG] First coord: {coords[0]}, Last coord: {coords[-1]}")

                # Remove the closing point if present (QgsGeometry handles ring closure)
                if len(coords) > 4 and coords[0] == coords[-1]:
                    coords = coords[:-1]
                    self.logger.info(f"[CLAIMS DEBUG] Removed closing point, now {len(coords)} coords")

                # Build polygon points from UTM coordinates
                # coords format: [[easting, northing], [easting, northing], ...]
                points = [QgsPointXY(c[0], c[1]) for c in coords]

                # Close the ring for the polygon
                if points[0] != points[-1]:
                    points.append(points[0])

                new_geom = QgsGeometry.fromPolygonXY([points])

                if new_geom.isEmpty():
                    self.logger.warning(f"[CLAIMS] Created empty geometry for {claim_name}")
                    return

                self.logger.info(f"[CLAIMS DEBUG] Created new geometry, WKT length: {len(new_geom.asWkt())}")

                # Update the feature geometry and LM Corner field
                claims_layer.startEditing()
                change_result = claims_layer.changeGeometry(feature.id(), new_geom)
                self.logger.info(f"[CLAIMS DEBUG] changeGeometry result: {change_result}")

                # Set LM Corner to 1 (it's now rotated, so Corner 1 is the LM corner)
                lm_corner_idx = claims_layer.fields().indexOf('LM Corner')
                if lm_corner_idx < 0:
                    lm_corner_idx = claims_layer.fields().indexOf('lm_corner')
                if lm_corner_idx >= 0:
                    attr_result = claims_layer.changeAttributeValue(feature.id(), lm_corner_idx, 1)
                    self.logger.info(f"[CLAIMS DEBUG] changeAttributeValue result: {attr_result}")

                commit_result = claims_layer.commitChanges()
                self.logger.info(f"[CLAIMS DEBUG] commitChanges result: {commit_result}")
                if not commit_result:
                    self.logger.error(f"[CLAIMS DEBUG] Commit errors: {claims_layer.commitErrors()}")

                self.logger.info(
                    f"[CLAIMS] Updated geometry for {claim_name} with rotated coordinates "
                    f"({len(coords)} corners)"
                )
                return

            if not feature_found:
                self.logger.warning(f"[CLAIMS DEBUG] Feature '{claim_name}' NOT FOUND in claims layer!")

        except Exception as e:
            self.logger.error(f"[CLAIMS] Failed to update claim geometry (UTM): {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            # Try to rollback if we were editing
            if claims_layer.isEditable():
                claims_layer.rollBack()

    def _extract_claims_data(self, claims_layer: QgsVectorLayer) -> List[Dict[str, Any]]:
        """
        Extract claim data from the layer.

        Returns list of dicts with:
            - name: claim name
            - corners: list of (x, y) tuples for 4 corners
            - lm_corner: which corner is the LM corner (1-4, default 1)
            - geometry: original QgsGeometry
        """
        claims_data = []

        for feature in claims_layer.getFeatures():
            geom = feature.geometry()
            if geom.isEmpty() or geom.type() != QgsWkbTypes.PolygonGeometry:
                continue

            # Get polygon coordinates
            if geom.isMultipart():
                polygon = geom.asMultiPolygon()[0][0]  # First polygon, outer ring
            else:
                polygon = geom.asPolygon()[0]  # Outer ring

            # Get 4 corners (excluding closing point)
            corners = [(pt.x(), pt.y()) for pt in polygon[:-1]]

            if len(corners) < 4:
                self.logger.warning(f"[CLAIMS] Claim has {len(corners)} corners, expected 4")
                continue

            # Get claim name
            name = ""
            if claims_layer.fields().indexOf('name') >= 0:
                name = feature.attribute('name') or ""
            elif claims_layer.fields().indexOf('Name') >= 0:
                name = feature.attribute('Name') or ""

            # Get LM corner (default to 1)
            lm_corner = 1
            if claims_layer.fields().indexOf('LM Corner') >= 0:
                lm_corner = feature.attribute('LM Corner') or 1
            elif claims_layer.fields().indexOf('lm_corner') >= 0:
                lm_corner = feature.attribute('lm_corner') or 1

            # Get state if available
            state = None
            if claims_layer.fields().indexOf('state') >= 0:
                state = feature.attribute('state')
            elif claims_layer.fields().indexOf('State') >= 0:
                state = feature.attribute('State')

            # Get notes if available (for location notices)
            notes = None
            for field_name in ['notes', 'Notes', 'NOTES']:
                idx = claims_layer.fields().indexOf(field_name)
                if idx >= 0:
                    notes = feature.attribute(idx)
                    break

            claims_data.append({
                'name': name,
                'corners': corners[:4],  # Only first 4 corners
                'lm_corner': int(lm_corner),
                'geometry': geom,
                'state': state,
                'feature_id': feature.id(),
                'notes': str(notes) if notes else ''  # Per-claim notes for location notices
            })

        return claims_data

    def _detect_state(self, claims_data: List[Dict]) -> Optional[str]:
        """Detect state from claims data."""
        for claim in claims_data:
            if claim.get('state'):
                return claim['state']
        return None

    # =========================================================================
    # Layer Creation Helpers
    # =========================================================================

    def _create_point_layer(
        self,
        name: str,
        crs: QgsCoordinateReferenceSystem,
        fields: List[Tuple[str, QMetaType.Type]]
    ) -> Optional[QgsVectorLayer]:
        """Create a point layer with the given fields."""
        qgs_fields = QgsFields()
        for field_name, field_type in fields:
            qgs_fields.append(QgsField(field_name, field_type))

        # Get display name with project suffix
        display_name = self._get_display_name(name)

        # Use GeoPackage if configured
        if self._geopackage_path and self.storage_manager:
            table_name = name.lower().replace(' ', '_')
            layer = self.storage_manager.create_or_update_layer(
                table_name=table_name,
                layer_display_name=display_name,
                geometry_type='Point',
                fields=qgs_fields,
                crs=crs,
                gpkg_path=self._geopackage_path,
                add_to_project=False  # We add to project later via add_layers_to_project
            )
        else:
            # Memory layer fallback
            layer = QgsVectorLayer(f"Point?crs={crs.authid()}", display_name, "memory")
            layer.dataProvider().addAttributes(qgs_fields.toList())
            layer.updateFields()

        return layer

    def _create_line_layer(
        self,
        name: str,
        crs: QgsCoordinateReferenceSystem,
        fields: List[Tuple[str, QMetaType.Type]]
    ) -> Optional[QgsVectorLayer]:
        """Create a line layer with the given fields."""
        qgs_fields = QgsFields()
        for field_name, field_type in fields:
            qgs_fields.append(QgsField(field_name, field_type))

        # Get display name with project suffix
        display_name = self._get_display_name(name)

        # Use GeoPackage if configured
        if self._geopackage_path and self.storage_manager:
            table_name = name.lower().replace(' ', '_')
            layer = self.storage_manager.create_or_update_layer(
                table_name=table_name,
                layer_display_name=display_name,
                geometry_type='LineString',
                fields=qgs_fields,
                crs=crs,
                gpkg_path=self._geopackage_path,
                add_to_project=False  # We add to project later via add_layers_to_project
            )
        else:
            # Memory layer fallback
            layer = QgsVectorLayer(f"LineString?crs={crs.authid()}", display_name, "memory")
            layer.dataProvider().addAttributes(qgs_fields.toList())
            layer.updateFields()

        return layer

    def _create_polygon_layer(
        self,
        name: str,
        crs: QgsCoordinateReferenceSystem,
        fields: List[Tuple[str, QMetaType.Type]]
    ) -> Optional[QgsVectorLayer]:
        """
        Create a polygon layer with the given fields.

        This is used for the Lode Claims layer which contains the main
        claim polygons with all QClaims-compatible fields.
        """
        qgs_fields = QgsFields()
        for field_name, field_type in fields:
            qgs_fields.append(QgsField(field_name, field_type))

        # Get display name with project suffix
        display_name = self._get_display_name(name)

        # Use GeoPackage if configured
        if self._geopackage_path and self.storage_manager:
            table_name = name.lower().replace(' ', '_')
            layer = self.storage_manager.create_or_update_layer(
                table_name=table_name,
                layer_display_name=display_name,
                geometry_type='Polygon',
                fields=qgs_fields,
                crs=crs,
                gpkg_path=self._geopackage_path,
                add_to_project=False  # We add to project later via add_layers_to_project
            )
        else:
            # Memory layer fallback
            layer = QgsVectorLayer(f"Polygon?crs={crs.authid()}", display_name, "memory")
            layer.dataProvider().addAttributes(qgs_fields.toList())
            layer.updateFields()

        return layer

    # =========================================================================
    # Layer Styling
    # =========================================================================

    def _apply_corner_points_style(self, layer: QgsVectorLayer):
        """
        Apply styling to Corner Points layer.

        Black circles, size 1.6mm, labeled with corner number.
        """
        try:
            from qgis.core import QgsTextBufferSettings, Qgis

            # Create black circle marker
            symbol = QgsSymbol.defaultSymbol(QgsWkbTypes.PointGeometry)
            marker = QgsSimpleMarkerSymbolLayer()
            marker.setShape(QgsSimpleMarkerSymbolLayer.Circle)
            marker.setSize(1.6)  # Size in mm
            marker.setColor(QColor(0, 0, 0))  # Black fill
            marker.setStrokeColor(QColor(0, 0, 0))  # Black stroke
            marker.setStrokeWidth(0.2)

            symbol.changeSymbolLayer(0, marker)
            renderer = QgsSingleSymbolRenderer(symbol)
            layer.setRenderer(renderer)

            # Add labels showing the corner number
            label_settings = QgsPalLayerSettings()
            label_settings.fieldName = 'Corner #'  # Field name without quotes
            label_settings.enabled = True

            # Text format
            text_format = QgsTextFormat()
            text_format.setFont(QFont("Arial", 8, QFont.Bold))
            text_format.setColor(QColor(0, 0, 0))  # Black text
            text_format.setSize(8)

            # Add white buffer/halo for readability
            buffer_settings = QgsTextBufferSettings()
            buffer_settings.setEnabled(True)
            buffer_settings.setSize(1.0)
            buffer_settings.setColor(QColor(255, 255, 255))
            text_format.setBuffer(buffer_settings)

            label_settings.setFormat(text_format)

            # Position label offset from point (top-right)
            label_settings.xOffset = 1.5
            label_settings.yOffset = -1.5

            # Set placement to around point
            label_settings.placement = Qgis.LabelPlacement.AroundPoint

            labeling = QgsVectorLayerSimpleLabeling(label_settings)
            layer.setLabeling(labeling)
            layer.setLabelsEnabled(True)

            layer.triggerRepaint()
            self.logger.info("[CLAIMS] Applied Corner Points styling with labels")

        except Exception as e:
            self.logger.warning(f"[CLAIMS] Could not apply Corner Points style: {e}")
            import traceback
            self.logger.warning(traceback.format_exc())

    def _apply_lm_corners_style(self, layer: QgsVectorLayer):
        """
        Apply styling to LM Corners layer.

        Lime green dot, size 1.6mm, no label.
        """
        try:
            # Create lime green circle marker
            symbol = QgsSymbol.defaultSymbol(QgsWkbTypes.PointGeometry)
            marker = QgsSimpleMarkerSymbolLayer()
            marker.setShape(QgsSimpleMarkerSymbolLayer.Circle)
            marker.setSize(1.6)  # Size in mm
            marker.setColor(QColor(0, 255, 0))  # Lime green fill
            marker.setStrokeColor(QColor(0, 200, 0))  # Slightly darker green stroke
            marker.setStrokeWidth(0.2)

            symbol.changeSymbolLayer(0, marker)
            renderer = QgsSingleSymbolRenderer(symbol)
            layer.setRenderer(renderer)

            # No labels for LM corners
            layer.setLabelsEnabled(False)

            layer.triggerRepaint()
            self.logger.info("[CLAIMS] Applied LM Corners styling")

        except Exception as e:
            self.logger.warning(f"[CLAIMS] Could not apply LM Corners style: {e}")

    def _apply_centerlines_style(self, layer: QgsVectorLayer):
        """
        Apply styling to Centerlines layer.

        Dashed gray line for reference.
        """
        try:
            symbol = QgsSymbol.defaultSymbol(QgsWkbTypes.LineGeometry)
            line = QgsSimpleLineSymbolLayer()
            line.setColor(QColor(128, 128, 128))  # Gray
            line.setWidth(0.3)
            line.setUseCustomDashPattern(True)
            line.setCustomDashVector([3.0, 2.0])  # Dashed pattern

            symbol.changeSymbolLayer(0, line)
            renderer = QgsSingleSymbolRenderer(symbol)
            layer.setRenderer(renderer)

            layer.triggerRepaint()
            self.logger.info("[CLAIMS] Applied Centerlines styling")

        except Exception as e:
            self.logger.warning(f"[CLAIMS] Could not apply Centerlines style: {e}")

    def _apply_monuments_style(self, layer: QgsVectorLayer):
        """
        Apply styling to Monuments layer.

        Green triangle marker for discovery monuments.
        """
        try:
            symbol = QgsSymbol.defaultSymbol(QgsWkbTypes.PointGeometry)
            marker = QgsSimpleMarkerSymbolLayer()
            marker.setShape(QgsSimpleMarkerSymbolLayer.Triangle)
            marker.setSize(2.5)
            marker.setColor(QColor(34, 139, 34))  # Forest green
            marker.setStrokeColor(QColor(0, 100, 0))  # Dark green stroke
            marker.setStrokeWidth(0.3)

            symbol.changeSymbolLayer(0, marker)
            renderer = QgsSingleSymbolRenderer(symbol)
            layer.setRenderer(renderer)

            # Add labels
            label_settings = QgsPalLayerSettings()
            label_settings.fieldName = '"Name"'
            label_settings.enabled = True

            text_format = QgsTextFormat()
            text_format.setFont(QFont("Arial", 8))
            text_format.setColor(QColor(0, 100, 0))
            label_settings.setFormat(text_format)

            labeling = QgsVectorLayerSimpleLabeling(label_settings)
            layer.setLabeling(labeling)
            layer.setLabelsEnabled(True)

            layer.triggerRepaint()
            self.logger.info("[CLAIMS] Applied Monuments styling")

        except Exception as e:
            self.logger.warning(f"[CLAIMS] Could not apply Monuments style: {e}")

    def _apply_sideline_monuments_style(self, layer: QgsVectorLayer):
        """Apply styling to Sideline Monuments layer (Wyoming)."""
        try:
            symbol = QgsSymbol.defaultSymbol(QgsWkbTypes.PointGeometry)
            marker = QgsSimpleMarkerSymbolLayer()
            marker.setShape(QgsSimpleMarkerSymbolLayer.Square)
            marker.setSize(2.0)
            marker.setColor(QColor(65, 105, 225))  # Royal blue
            marker.setStrokeColor(QColor(0, 0, 139))  # Dark blue stroke
            marker.setStrokeWidth(0.3)

            symbol.changeSymbolLayer(0, marker)
            renderer = QgsSingleSymbolRenderer(symbol)
            layer.setRenderer(renderer)

            layer.triggerRepaint()
            self.logger.info("[CLAIMS] Applied Sideline Monuments styling")

        except Exception as e:
            self.logger.warning(f"[CLAIMS] Could not apply Sideline Monuments style: {e}")

    def _apply_endline_monuments_style(self, layer: QgsVectorLayer):
        """Apply styling to Endline Monuments layer (Arizona)."""
        try:
            symbol = QgsSymbol.defaultSymbol(QgsWkbTypes.PointGeometry)
            marker = QgsSimpleMarkerSymbolLayer()
            marker.setShape(QgsSimpleMarkerSymbolLayer.Diamond)
            marker.setSize(2.0)
            marker.setColor(QColor(255, 140, 0))  # Dark orange
            marker.setStrokeColor(QColor(255, 69, 0))  # Red-orange stroke
            marker.setStrokeWidth(0.3)

            symbol.changeSymbolLayer(0, marker)
            renderer = QgsSingleSymbolRenderer(symbol)
            layer.setRenderer(renderer)

            layer.triggerRepaint()
            self.logger.info("[CLAIMS] Applied Endline Monuments styling")

        except Exception as e:
            self.logger.warning(f"[CLAIMS] Could not apply Endline Monuments style: {e}")

    def _apply_lode_claims_style(self, layer: QgsVectorLayer):
        """
        Apply styling to Lode Claims layer.

        Light blue fill with darker blue outline, matching QClaims styling.
        Labels show claim name.
        """
        try:
            from qgis.core import (
                QgsSimpleFillSymbolLayer, QgsTextBufferSettings, Qgis
            )

            # Create light blue fill with darker blue outline
            symbol = QgsSymbol.defaultSymbol(QgsWkbTypes.PolygonGeometry)
            fill = QgsSimpleFillSymbolLayer()
            fill.setColor(QColor(173, 216, 230, 100))  # Light blue with transparency
            fill.setStrokeColor(QColor(70, 130, 180))  # Steel blue outline
            fill.setStrokeWidth(0.5)

            symbol.changeSymbolLayer(0, fill)
            renderer = QgsSingleSymbolRenderer(symbol)
            layer.setRenderer(renderer)

            # Add labels showing claim name
            label_settings = QgsPalLayerSettings()
            label_settings.fieldName = 'Name'  # Field name without quotes
            label_settings.enabled = True

            # Text format
            text_format = QgsTextFormat()
            text_format.setFont(QFont("Arial", 9, QFont.Bold))
            text_format.setColor(QColor(25, 25, 112))  # Midnight blue
            text_format.setSize(9)

            # Add white buffer/halo for readability
            buffer_settings = QgsTextBufferSettings()
            buffer_settings.setEnabled(True)
            buffer_settings.setSize(1.0)
            buffer_settings.setColor(QColor(255, 255, 255))
            text_format.setBuffer(buffer_settings)

            label_settings.setFormat(text_format)

            # Center label in polygon
            label_settings.placement = Qgis.LabelPlacement.OverPoint

            labeling = QgsVectorLayerSimpleLabeling(label_settings)
            layer.setLabeling(labeling)
            layer.setLabelsEnabled(True)

            layer.triggerRepaint()
            self.logger.info("[CLAIMS] Applied Lode Claims styling")

        except Exception as e:
            self.logger.warning(f"[CLAIMS] Could not apply Lode Claims style: {e}")
            import traceback
            self.logger.warning(traceback.format_exc())

    def _remove_existing_layers(
        self,
        project: QgsProject,
        new_layers: Dict[str, QgsVectorLayer]
    ) -> int:
        """
        Remove existing layers from the project that match the names of new layers.

        This prevents duplicate layers when regenerating. Layers are matched by
        their display name (which may include a project suffix like "[Project Name]").

        Args:
            project: The QgsProject instance
            new_layers: Dict of layer names to new QgsVectorLayer objects

        Returns:
            Number of layers removed
        """
        removed_count = 0

        # Get the display names of layers we're about to add
        new_layer_names = set()
        for base_name in new_layers.keys():
            # Add both the base name and the display name (with project suffix)
            new_layer_names.add(base_name)
            new_layer_names.add(self._get_display_name(base_name))

        # Find and remove existing layers with matching names
        layers_to_remove = []
        for layer_id, layer in project.mapLayers().items():
            if layer.name() in new_layer_names:
                layers_to_remove.append(layer_id)

        # Remove the layers
        for layer_id in layers_to_remove:
            project.removeMapLayer(layer_id)
            removed_count += 1

        if removed_count > 0:
            self.logger.info(f"[CLAIMS] Removed {removed_count} existing layer(s) before regenerating")

        return removed_count

    def add_layers_to_project(
        self,
        layers: Dict[str, QgsVectorLayer],
        group_name: str = "Claims Layers"
    ) -> bool:
        """
        Add generated layers to the QGIS project with styling.

        Layers are added in a specific order so that LM Corners draws on top
        of Corner Points (higher in the layer list = drawn on top).

        If layers with the same name already exist in the project, they are
        removed first to avoid duplicates.

        Args:
            layers: Dict of layer name to QgsVectorLayer
            group_name: Name of the layer group to create

        Returns:
            True if successful
        """
        try:
            project = QgsProject.instance()
            root = project.layerTreeRoot()

            # Create or find group
            group = root.findGroup(group_name)
            if not group:
                group = root.insertGroup(0, group_name)

            # Add layers in specific order (bottom to top in the layer tree)
            # Later items in this list will be higher in the tree (drawn on top)
            layer_order = [
                self.LODE_CLAIMS_LAYER,        # Bottom - main claims polygons
                self.CORNER_POINTS_LAYER,
                self.CENTERLINES_LAYER,
                self.ENDLINE_MONUMENTS_LAYER,
                self.SIDELINE_MONUMENTS_LAYER,
                self.MONUMENTS_LAYER,
                self.LM_CORNERS_LAYER,         # Top - drawn last (on top)
            ]

            # Remove existing layers with matching names before adding new ones
            # This prevents duplicate layers when regenerating
            self._remove_existing_layers(project, layers)

            for layer_name in layer_order:
                if layer_name in layers:
                    layer = layers[layer_name]

                    # Apply styling before adding to project
                    if layer_name == self.LODE_CLAIMS_LAYER:
                        self._apply_lode_claims_style(layer)
                    elif layer_name == self.CORNER_POINTS_LAYER:
                        self._apply_corner_points_style(layer)
                    elif layer_name == self.LM_CORNERS_LAYER:
                        self._apply_lm_corners_style(layer)
                    elif layer_name == self.CENTERLINES_LAYER:
                        self._apply_centerlines_style(layer)
                    elif layer_name == self.MONUMENTS_LAYER:
                        self._apply_monuments_style(layer)
                    elif layer_name == self.SIDELINE_MONUMENTS_LAYER:
                        self._apply_sideline_monuments_style(layer)
                    elif layer_name == self.ENDLINE_MONUMENTS_LAYER:
                        self._apply_endline_monuments_style(layer)

                    # Save the style as default in the GeoPackage (if applicable)
                    # This allows the style to be automatically loaded next time
                    # TEMPORARILY DISABLED - investigating crash
                    # TODO: Re-enable once crash is resolved
                    # try:
                    #     self._save_style_to_geopackage(layer)
                    # except Exception as style_err:
                    #     self.logger.warning(f"[CLAIMS] Could not save style for '{layer_name}': {style_err}")

                    # Add to project at top of group (index 0)
                    # This means later layers in our loop go to the top
                    project.addMapLayer(layer, False)
                    group.insertLayer(0, layer)

            self.logger.info(f"[CLAIMS] Added {len(layers)} styled layers to project group '{group_name}'")
            return True

        except Exception as e:
            self.logger.error(f"[CLAIMS] Failed to add layers: {e}")
            return False

    def _save_style_to_geopackage(self, layer: QgsVectorLayer) -> bool:
        """
        Save the layer's current style as the default style in the GeoPackage.

        When a layer is loaded from a GeoPackage that has a default style,
        QGIS automatically applies the style. This makes reopening the
        GeoPackage seamless - all styling is preserved.

        Args:
            layer: The layer whose style should be saved

        Returns:
            True if style was saved successfully
        """
        if not layer or not layer.isValid():
            return False

        # Check if the layer is from a GeoPackage
        source = layer.source()
        if '|layername=' not in source:
            self.logger.debug(
                f"[CLAIMS] Layer '{layer.name()}' is not from a GeoPackage, skipping style save"
            )
            return False

        try:
            # Save the style as the default style in the GeoPackage
            # Parameters: name, description, useAsDefault, uiFileContent
            result = layer.saveStyleToDatabase(
                '',  # Empty name = default style
                'QClaims default style',
                True,  # Use as default
                ''  # No UI file
            )

            # saveStyleToDatabase returns a tuple (success_message, success_bool) in QGIS 3.x
            if isinstance(result, tuple):
                message, success = result
                if success:
                    self.logger.debug(f"[CLAIMS] Saved style for '{layer.name()}' to GeoPackage")
                    return True
                else:
                    self.logger.warning(
                        f"[CLAIMS] Failed to save style for '{layer.name()}': {message}"
                    )
                    return False
            else:
                # Older API or different return type
                self.logger.debug(f"[CLAIMS] Style save result for '{layer.name()}': {result}")
                return True

        except Exception as e:
            self.logger.warning(f"[CLAIMS] Could not save style to GeoPackage: {e}")
            return False

    def load_layers_from_geopackage(
        self,
        gpkg_path: str,
        group_name: str = "Claims Layers"
    ) -> Dict[str, QgsVectorLayer]:
        """
        Load all claims layers from a GeoPackage with their saved styles.

        This method loads all the standard claims layers (Lode Claims, Corner Points,
        LM Corners, etc.) from a GeoPackage and adds them to the project. If the
        layers have default styles saved in the GeoPackage, they will be applied
        automatically by QGIS.

        Args:
            gpkg_path: Path to the GeoPackage file
            group_name: Name of the layer group to create/use

        Returns:
            Dict mapping layer names to loaded QgsVectorLayer objects
        """
        import os
        if not os.path.exists(gpkg_path):
            self.logger.error(f"[CLAIMS] GeoPackage not found: {gpkg_path}")
            return {}

        # Map of table names to display names
        layer_tables = {
            'lode_claims': self.LODE_CLAIMS_LAYER,
            'corner_points': self.CORNER_POINTS_LAYER,
            'lm_corners': self.LM_CORNERS_LAYER,
            'center_lines': self.CENTERLINES_LAYER,
            'monuments': self.MONUMENTS_LAYER,
            'sideline_monuments': self.SIDELINE_MONUMENTS_LAYER,
            'endline_monuments': self.ENDLINE_MONUMENTS_LAYER,
        }

        # Also check for ClaimsStorageManager table names
        if self.storage_manager:
            layer_tables.update({
                self.storage_manager.CORNER_POINTS_TABLE: self.CORNER_POINTS_LAYER,
                self.storage_manager.LM_CORNERS_TABLE: self.LM_CORNERS_LAYER,
                self.storage_manager.CENTERLINES_TABLE: self.CENTERLINES_LAYER,
                self.storage_manager.MONUMENTS_TABLE: self.MONUMENTS_LAYER,
                self.storage_manager.SIDELINE_MONUMENTS_TABLE: self.SIDELINE_MONUMENTS_LAYER,
                self.storage_manager.ENDLINE_MONUMENTS_TABLE: self.ENDLINE_MONUMENTS_LAYER,
            })

        loaded_layers = {}
        project = QgsProject.instance()
        root = project.layerTreeRoot()

        # Create or find group
        group = root.findGroup(group_name)
        if not group:
            group = root.insertGroup(0, group_name)

        # Try to load each layer
        for table_name, display_name in layer_tables.items():
            layer_uri = f"{gpkg_path}|layername={table_name}"

            # Check if layer is already in project
            existing = self._find_layer_by_source(project, layer_uri)
            if existing:
                loaded_layers[display_name] = existing
                self.logger.debug(f"[CLAIMS] Layer '{display_name}' already in project")
                continue

            # Try to load the layer
            layer = QgsVectorLayer(layer_uri, display_name, "ogr")
            if layer.isValid() and layer.featureCount() > 0:
                # Layer loaded successfully - style will be auto-applied from GeoPackage
                project.addMapLayer(layer, False)
                group.insertLayer(0, layer)
                loaded_layers[display_name] = layer
                self.logger.info(f"[CLAIMS] Loaded '{display_name}' from GeoPackage with {layer.featureCount()} features")
            else:
                # Layer doesn't exist or is empty
                self.logger.debug(f"[CLAIMS] Layer '{table_name}' not found or empty in GeoPackage")

        self.logger.info(f"[CLAIMS] Loaded {len(loaded_layers)} layers from GeoPackage")
        return loaded_layers

    def _find_layer_by_source(
        self,
        project: QgsProject,
        source: str
    ) -> Optional[QgsVectorLayer]:
        """Find a layer in the project by its data source."""
        import os
        for layer in project.mapLayers().values():
            if isinstance(layer, QgsVectorLayer):
                # Normalize paths for comparison
                layer_source = os.path.normpath(layer.source())
                check_source = os.path.normpath(source)
                if layer_source == check_source:
                    return layer
        return None
