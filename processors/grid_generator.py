# -*- coding: utf-8 -*-
"""
Mining claim grid generator - API client wrapper.

Grid generation is performed server-side. This module provides
a QGIS-friendly interface that calls the server API and converts
results to QGIS layers.

The server-side implementation handles:
- Grid calculation with rotation matrix
- Claim dimension calculations (600x1500 ft for lode, variable for placer)
- Corner coordinate generation
- Polygon geometry creation
"""
from typing import List, Dict, Any, Optional
from enum import Enum

from qgis.core import (
    QgsProject, QgsVectorLayer, QgsFeature, QgsGeometry,
    QgsPointXY, QgsCoordinateReferenceSystem, QgsField, QgsFields
)
from qgis.PyQt.QtCore import QMetaType

from ..utils.logger import PluginLogger


class ClaimType(Enum):
    """Mining claim types."""
    LODE = 'lode'
    PLACER = 'placer'


class GridGenerator:
    """
    Client-side wrapper for server-side grid generation.

    All grid generation logic is on the server. This class:
    1. Calls the server API with grid parameters
    2. Converts the response to a QGIS layer (GeoPackage or memory)

    The server handles:
    - Standard lode claim dimensions (600 ft wide x 1500 ft long)
    - Placer claim dimensions (typically 20 acres)
    - Rotation matrix calculations for azimuth
    - Corner coordinate generation
    """

    def __init__(self, api_client=None, claims_storage_manager=None):
        """
        Initialize grid generator.

        Args:
            api_client: Optional APIClient instance. If not provided,
                       falls back to local generation for offline use.
            claims_storage_manager: Optional ClaimsStorageManager for
                       GeoPackage persistence. If provided with a GeoPackage
                       path, layers are saved to the GeoPackage instead of
                       being created as temporary memory layers.
        """
        self.api_client = api_client
        self.claims_storage_manager = claims_storage_manager
        self.logger = PluginLogger.get_logger()
        self._offline_mode = api_client is None
        self._geopackage_path: Optional[str] = None

    def set_geopackage_path(self, gpkg_path: Optional[str]):
        """
        Configure GeoPackage path for persistent layer storage.

        When a GeoPackage path is set, generated grid layers will be saved
        to the GeoPackage instead of being created as temporary memory layers.

        Args:
            gpkg_path: Path to the claims GeoPackage, or None for memory layers
        """
        self._geopackage_path = gpkg_path
        if gpkg_path:
            self.logger.info(f"[GRID] Using GeoPackage storage: {gpkg_path}")
        else:
            self.logger.info("[GRID] Using memory layer storage")

    def set_api_client(self, api_client):
        """Set the API client for server-side generation."""
        self.api_client = api_client
        self._offline_mode = api_client is None

    def generate_lode_grid(
        self,
        start_point: QgsPointXY,
        rows: int,
        cols: int,
        name_prefix: str = "GE",
        azimuth: float = 0.0,
        project_crs: Optional[QgsCoordinateReferenceSystem] = None
    ) -> QgsVectorLayer:
        """
        Generate a grid of lode claims via server API.

        Standard lode claim: 600 ft wide x 1500 ft long (max).

        Args:
            start_point: Northwest corner of the grid (in project CRS)
            rows: Number of rows (east-west)
            cols: Number of columns (north-south)
            name_prefix: Prefix for claim names (e.g., "GE" -> "GE 1", "GE 2")
            azimuth: Rotation angle in degrees clockwise from north
            project_crs: Project CRS (defaults to current QGIS project)

        Returns:
            QgsVectorLayer with claim polygons
        """
        self.logger.info(
            f"[GRID] Generating {rows}x{cols} lode grid at {start_point.x():.6f}, "
            f"{start_point.y():.6f} with azimuth {azimuth}"
        )

        # Get CRS
        if project_crs is None:
            project_crs = QgsProject.instance().crs()

        # Get EPSG code
        epsg = self._get_epsg_code(project_crs)

        # Try server-side generation first
        if self.api_client and not self._offline_mode:
            try:
                return self._generate_lode_grid_server(
                    start_point, rows, cols, name_prefix, azimuth, epsg, project_crs
                )
            except Exception as e:
                self.logger.warning(
                    f"[GRID] Server-side generation failed, falling back to local: {e}"
                )

        # Fallback to local generation (offline mode)
        return self._generate_lode_grid_local(
            start_point, rows, cols, name_prefix, azimuth, project_crs
        )

    def _generate_lode_grid_server(
        self,
        start_point: QgsPointXY,
        rows: int,
        cols: int,
        name_prefix: str,
        azimuth: float,
        epsg: int,
        project_crs: QgsCoordinateReferenceSystem
    ) -> QgsVectorLayer:
        """Generate lode grid using server API."""
        # Build API endpoint URL
        endpoint = self._get_claims_endpoint('generate-grid/')

        # Call server API
        response = self.api_client._make_request('POST', endpoint, data={
            'start_easting': start_point.x(),
            'start_northing': start_point.y(),
            'rows': rows,
            'cols': cols,
            'claim_type': 'lode',
            'name_prefix': name_prefix,
            'azimuth': azimuth,
            'epsg': epsg
        })

        if 'error' in response:
            raise ValueError(response['error'])

        # Convert to QGIS layer
        return self._response_to_layer(response, project_crs)

    def generate_placer_grid(
        self,
        start_point: QgsPointXY,
        rows: int,
        cols: int,
        claim_size_acres: float = 20.0,
        name_prefix: str = "PL",
        project_crs: Optional[QgsCoordinateReferenceSystem] = None
    ) -> QgsVectorLayer:
        """
        Generate a grid of placer claims via server API.

        Placer claims are typically 20 acres (legal subdivision).

        Args:
            start_point: Northwest corner of the grid
            rows: Number of rows
            cols: Number of columns
            claim_size_acres: Size of each claim in acres (default 20)
            name_prefix: Prefix for claim names
            project_crs: Project CRS

        Returns:
            QgsVectorLayer with claim polygons
        """
        self.logger.info(
            f"[GRID] Generating {rows}x{cols} placer grid at {start_point.x():.6f}, "
            f"{start_point.y():.6f}, {claim_size_acres} acres each"
        )

        # Get CRS
        if project_crs is None:
            project_crs = QgsProject.instance().crs()

        # Get EPSG code
        epsg = self._get_epsg_code(project_crs)

        # Try server-side generation first
        if self.api_client and not self._offline_mode:
            try:
                return self._generate_placer_grid_server(
                    start_point, rows, cols, claim_size_acres, name_prefix, epsg, project_crs
                )
            except Exception as e:
                self.logger.warning(
                    f"[GRID] Server-side generation failed, falling back to local: {e}"
                )

        # Fallback to local generation (offline mode)
        return self._generate_placer_grid_local(
            start_point, rows, cols, claim_size_acres, name_prefix, project_crs
        )

    def _generate_placer_grid_server(
        self,
        start_point: QgsPointXY,
        rows: int,
        cols: int,
        claim_size_acres: float,
        name_prefix: str,
        epsg: int,
        project_crs: QgsCoordinateReferenceSystem
    ) -> QgsVectorLayer:
        """Generate placer grid using server API."""
        # Build API endpoint URL
        endpoint = self._get_claims_endpoint('generate-grid/')

        # Call server API
        response = self.api_client._make_request('POST', endpoint, data={
            'start_easting': start_point.x(),
            'start_northing': start_point.y(),
            'rows': rows,
            'cols': cols,
            'claim_type': 'placer',
            'name_prefix': name_prefix,
            'claim_size_acres': claim_size_acres,
            'epsg': epsg
        })

        if 'error' in response:
            raise ValueError(response['error'])

        # Convert to QGIS layer
        return self._response_to_layer(response, project_crs)

    def _response_to_layer(
        self,
        response: dict,
        crs: QgsCoordinateReferenceSystem
    ) -> QgsVectorLayer:
        """Convert server response to QGIS layer."""
        claim_type = response.get('claim_type', 'lode')
        claims = response.get('claims', [])

        # Create layer with descriptive name
        if claims:
            first_name = claims[0].get('name', 'Claims').split()[0]
        else:
            first_name = 'Claims'

        layer_name = f"Initial Layout [{first_name} {claim_type.title()} Claims]"

        # Define fields
        fields = QgsFields()
        fields.append(QgsField("name", QMetaType.Type.QString, len=100))
        fields.append(QgsField("claim_type", QMetaType.Type.QString, len=20))
        fields.append(QgsField("status", QMetaType.Type.QString, len=20))
        fields.append(QgsField("order", QMetaType.Type.Int))
        fields.append(QgsField("notes", QMetaType.Type.QString, len=500))

        # Use GeoPackage if configured, otherwise memory layer
        if self._geopackage_path and self.claims_storage_manager:
            from ..managers.claims_storage_manager import ClaimsStorageManager
            layer = self.claims_storage_manager.create_or_update_layer(
                table_name=ClaimsStorageManager.INITIAL_LAYOUT_TABLE,
                layer_display_name=layer_name,
                geometry_type='Polygon',
                fields=fields,
                crs=crs,
                gpkg_path=self._geopackage_path
            )
        else:
            # Fallback to memory layer
            layer = QgsVectorLayer(f"Polygon?crs={crs.authid()}", layer_name, "memory")
            layer.dataProvider().addAttributes(fields.toList())
            layer.updateFields()

        # Add features
        features = []
        for claim in claims:
            feature = QgsFeature(layer.fields())

            # Create geometry from corners
            corners = claim.get('corners', [])
            if corners:
                points = [QgsPointXY(c['easting'], c['northing']) for c in corners]
                points.append(points[0])  # Close polygon
                feature.setGeometry(QgsGeometry.fromPolygonXY([points]))

            feature.setAttribute("name", claim.get('name', ''))
            feature.setAttribute("claim_type", claim.get('claim_type', claim_type))
            feature.setAttribute("status", "planned")
            feature.setAttribute("order", claim.get('order', 0))
            feature.setAttribute("notes", "")

            features.append(feature)

        layer.dataProvider().addFeatures(features)
        layer.updateExtents()

        storage_type = "GeoPackage" if self._geopackage_path else "memory"
        self.logger.info(f"[GRID] Created {storage_type} layer with {len(features)} claims from server")

        return layer

    def _get_claims_endpoint(self, path: str) -> str:
        """Build full URL for claims endpoint."""
        base = self.api_client.config.base_url
        # Ensure we use v2 API for claims endpoints
        if '/v1' in base:
            base = base.replace('/v1', '/api/v2')
        elif '/api/v2' not in base:
            base = base.rstrip('/') + '/api/v2' if not base.endswith('/api/v2') else base
        return f"{base}/claims/{path}"

    def _get_epsg_code(self, crs: QgsCoordinateReferenceSystem) -> int:
        """Extract EPSG code from CRS."""
        auth_id = crs.authid()
        if ':' in auth_id:
            try:
                return int(auth_id.split(':')[1])
            except (ValueError, IndexError):
                pass
        return 4326  # Default to WGS84

    # =========================================================================
    # Local fallback methods (for offline mode)
    # =========================================================================

    # Standard claim dimensions in feet
    LODE_WIDTH_FT = 600
    LODE_LENGTH_FT = 1500
    FEET_TO_METERS = 0.3048

    def _generate_lode_grid_local(
        self,
        start_point: QgsPointXY,
        rows: int,
        cols: int,
        name_prefix: str,
        azimuth: float,
        project_crs: QgsCoordinateReferenceSystem
    ) -> QgsVectorLayer:
        """
        Local fallback for lode grid generation (offline mode).

        Note: This is a simplified implementation for offline use only.
        For production use, the server-side implementation is preferred.
        """
        import math

        # Convert dimensions to meters
        width_m = self.LODE_WIDTH_FT * self.FEET_TO_METERS
        length_m = self.LODE_LENGTH_FT * self.FEET_TO_METERS

        # Create layer
        layer_name = f"Initial Layout [{name_prefix} Lode Claims]"
        layer = self._create_claims_layer(layer_name, project_crs)

        # Generate claims
        claim_num = 1
        for row in range(rows):
            for col in range(cols):
                # Calculate position
                local_x = col * width_m
                local_y = -row * length_m  # Negative because we go south

                # Apply rotation
                if azimuth != 0:
                    rotated_x, rotated_y = self._rotate_point(local_x, local_y, azimuth)
                else:
                    rotated_x, rotated_y = local_x, local_y

                # Create claim polygon
                claim_origin = QgsPointXY(
                    start_point.x() + rotated_x,
                    start_point.y() + rotated_y
                )

                polygon = self._create_claim_polygon(
                    claim_origin, width_m, length_m, azimuth
                )

                # Create feature
                name = f"{name_prefix} {claim_num}"
                feature = self._create_claim_feature(
                    layer.fields(), name, polygon, ClaimType.LODE.value
                )

                layer.dataProvider().addFeature(feature)
                claim_num += 1

        layer.updateExtents()
        self.logger.info(f"[GRID] Generated {claim_num - 1} lode claims (local)")

        return layer

    def _generate_placer_grid_local(
        self,
        start_point: QgsPointXY,
        rows: int,
        cols: int,
        claim_size_acres: float,
        name_prefix: str,
        project_crs: QgsCoordinateReferenceSystem
    ) -> QgsVectorLayer:
        """
        Local fallback for placer grid generation (offline mode).

        Note: This is a simplified implementation for offline use only.
        For production use, the server-side implementation is preferred.
        """
        import math

        # Calculate dimensions from acres (1 acre = 4046.86 sq m)
        area_sq_m = claim_size_acres * 4046.86
        side_m = math.sqrt(area_sq_m)

        # Create layer
        layer_name = f"Initial Layout [{name_prefix} Placer Claims]"
        layer = self._create_claims_layer(layer_name, project_crs)

        # Generate claims
        claim_num = 1
        for row in range(rows):
            for col in range(cols):
                x = start_point.x() + col * side_m
                y = start_point.y() - row * side_m

                claim_origin = QgsPointXY(x, y)
                polygon = self._create_claim_polygon(claim_origin, side_m, side_m, 0.0)

                name = f"{name_prefix} {claim_num}"
                feature = self._create_claim_feature(
                    layer.fields(), name, polygon, ClaimType.PLACER.value
                )

                layer.dataProvider().addFeature(feature)
                claim_num += 1

        layer.updateExtents()
        self.logger.info(f"[GRID] Generated {claim_num - 1} placer claims (local)")

        return layer

    def _create_claims_layer(
        self,
        name: str,
        crs: QgsCoordinateReferenceSystem
    ) -> QgsVectorLayer:
        """Create a layer for claims (GeoPackage or memory)."""
        # Define fields
        fields = QgsFields()
        fields.append(QgsField("name", QMetaType.Type.QString, len=100))
        fields.append(QgsField("claim_type", QMetaType.Type.QString, len=20))
        fields.append(QgsField("status", QMetaType.Type.QString, len=20))
        fields.append(QgsField("notes", QMetaType.Type.QString, len=500))

        # Use GeoPackage if configured, otherwise memory layer
        if self._geopackage_path and self.claims_storage_manager:
            from ..managers.claims_storage_manager import ClaimsStorageManager
            layer = self.claims_storage_manager.create_or_update_layer(
                table_name=ClaimsStorageManager.INITIAL_LAYOUT_TABLE,
                layer_display_name=name,
                geometry_type='Polygon',
                fields=fields,
                crs=crs,
                gpkg_path=self._geopackage_path
            )
        else:
            # Fallback to memory layer
            layer = QgsVectorLayer(f"Polygon?crs={crs.authid()}", name, "memory")
            layer.dataProvider().addAttributes(fields.toList())
            layer.updateFields()

        return layer

    def _create_claim_polygon(
        self,
        origin: QgsPointXY,
        width: float,
        length: float,
        azimuth: float
    ) -> QgsGeometry:
        """Create a claim polygon."""
        # Create corners in local coordinates (origin at NW)
        corners_local = [
            (0, 0),              # NW
            (width, 0),          # NE
            (width, -length),    # SE
            (0, -length),        # SW
            (0, 0),              # NW (close polygon)
        ]

        corners = []
        for local_x, local_y in corners_local:
            if azimuth != 0:
                rotated_x, rotated_y = self._rotate_point(local_x, local_y, azimuth)
            else:
                rotated_x, rotated_y = local_x, local_y

            corners.append(QgsPointXY(
                origin.x() + rotated_x,
                origin.y() + rotated_y
            ))

        return QgsGeometry.fromPolygonXY([corners])

    def _create_claim_feature(
        self,
        fields: QgsFields,
        name: str,
        geometry: QgsGeometry,
        claim_type: str
    ) -> QgsFeature:
        """Create a feature for a claim."""
        feature = QgsFeature(fields)
        feature.setGeometry(geometry)
        feature.setAttribute("name", name)
        feature.setAttribute("claim_type", claim_type)
        feature.setAttribute("status", "planned")
        feature.setAttribute("notes", "")
        return feature

    def _rotate_point(self, x: float, y: float, angle_degrees: float):
        """Rotate a point around the origin."""
        import math
        angle_rad = -math.radians(angle_degrees)
        cos_a = math.cos(angle_rad)
        sin_a = math.sin(angle_rad)
        new_x = x * cos_a - y * sin_a
        new_y = x * sin_a + y * cos_a
        return new_x, new_y

    def add_layer_to_project(
        self,
        layer: QgsVectorLayer,
        add_to_group: Optional[str] = None
    ) -> bool:
        """
        Add the generated layer to the QGIS project.

        Args:
            layer: Layer to add
            add_to_group: Optional group name to add layer to

        Returns:
            True if successful
        """
        try:
            QgsProject.instance().addMapLayer(layer, not add_to_group)

            if add_to_group:
                root = QgsProject.instance().layerTreeRoot()
                group = root.findGroup(add_to_group)
                if not group:
                    group = root.addGroup(add_to_group)
                group.addLayer(layer)

            self.logger.info(f"[GRID] Added layer '{layer.name()}' to project")
            return True

        except Exception as e:
            self.logger.error(f"[GRID] Failed to add layer: {e}")
            return False


def generate_claim_grid(
    start_lat: float,
    start_lon: float,
    rows: int,
    cols: int,
    claim_type: str = 'lode',
    name_prefix: str = 'GE',
    azimuth: float = 0.0,
    claim_size_acres: float = 20.0,
    api_client=None
) -> QgsVectorLayer:
    """
    Convenience function to generate a claim grid.

    Args:
        start_lat: Starting latitude (NW corner)
        start_lon: Starting longitude (NW corner)
        rows: Number of rows
        cols: Number of columns
        claim_type: 'lode' or 'placer'
        name_prefix: Prefix for claim names
        azimuth: Rotation angle (lode claims only)
        claim_size_acres: Claim size (placer claims only)
        api_client: Optional APIClient for server-side generation

    Returns:
        QgsVectorLayer with generated claims
    """
    generator = GridGenerator(api_client=api_client)
    start_point = QgsPointXY(start_lon, start_lat)
    project_crs = QgsCoordinateReferenceSystem("EPSG:4326")

    if claim_type.lower() == 'placer':
        return generator.generate_placer_grid(
            start_point, rows, cols,
            claim_size_acres=claim_size_acres,
            name_prefix=name_prefix,
            project_crs=project_crs
        )
    else:
        return generator.generate_lode_grid(
            start_point, rows, cols,
            name_prefix=name_prefix,
            azimuth=azimuth,
            project_crs=project_crs
        )
