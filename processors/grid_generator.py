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

⛔ There is NO local fallback. It was deleted 2026-09-14 (claims-petra P6):
it was a second copy of the claim rectangle — with its own truncated
4046.86 acre constant — and it fired on ANY server exception, not only
offline, so a transient 500 silently produced client-drawn claims. Offline
now refuses with a message the user can act on.
"""
from typing import Optional
from enum import Enum

from qgis.core import (
    QgsProject, QgsVectorLayer, QgsFeature, QgsGeometry,
    QgsPointXY, QgsCoordinateReferenceSystem, QgsField, QgsFields
)
from .geometry_processor import closed_ring_from_corners
from .server_required import require_server
from ..utils.logger import PluginLogger
from ..utils.compat import FieldType_QString, FieldType_Int, FieldType_Double


class ClaimType(Enum):
    """Mining claim types."""
    LODE = 'lode'
    PLACER = 'placer'


class GridGenerator:
    """
    Client-side wrapper for server-side grid generation.

    All grid generation logic is on the server — genuinely all of it, since
    2026-09-14: the local offline fallback was deleted rather than left to
    drift from the server's published geometry contract. This class:
    1. Calls the server API with grid parameters
    2. Converts the response to a QGIS layer (GeoPackage or memory)
    3. Without an api_client, REFUSES with a readable message
       (``server_required.require_server``)

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
            api_client: APIClient instance. Without one the generator
                       REFUSES — there is no local fallback; claim geometry
                       comes from the server's one contract.
            claims_storage_manager: Optional ClaimsStorageManager for
                       GeoPackage persistence. If provided with a GeoPackage
                       path, layers are saved to the GeoPackage instead of
                       being created as temporary memory layers.
        """
        self.api_client = api_client
        self.claims_storage_manager = claims_storage_manager
        self.logger = PluginLogger.get_logger()
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

        # The server is the ONE geometry. There is no local fallback: a
        # server error must SURFACE, not quietly become client-drawn claims.
        require_server(self.api_client, "Generating a lode claim grid")
        return self._generate_lode_grid_server(
            start_point, rows, cols, name_prefix, azimuth, epsg, project_crs
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
        endpoint = self.api_client.config.get_claims_url('generate-grid/')

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

        # Same rule as the lode path: the server is the ONE geometry.
        require_server(self.api_client, "Generating a placer claim grid")
        return self._generate_placer_grid_server(
            start_point, rows, cols, claim_size_acres, name_prefix, epsg, project_crs
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
        endpoint = self.api_client.config.get_claims_url('generate-grid/')

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
        fields.append(QgsField("name", FieldType_QString, len=100))
        fields.append(QgsField("claim_type", FieldType_QString, len=20))
        fields.append(QgsField("status", FieldType_QString, len=20))
        fields.append(QgsField("order", FieldType_Int))
        fields.append(QgsField("notes", FieldType_QString, len=500))

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
                # Close safely — corners may already be closed; blindly
                # appending points[0] would duplicate the closing vertex.
                points = closed_ring_from_corners(points)
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

    def _get_epsg_code(self, crs: QgsCoordinateReferenceSystem) -> int:
        """The EPSG code the server will read the origin's easting/northing in.

        REFUSES when the CRS carries no EPSG authority id (a custom / user
        CRS, or an empty project CRS). The old `return 4326` fallback would
        have sent WGS84 DEGREES into a field the server reads as METRES in
        that EPSG frame — a block placed at (lon, lat) metres from the zone
        origin, i.e. in the ocean. Unreachable today behind the wizard's
        `is_utm_crs` gate; closed by refusing rather than defaulting
        (2026-09-15, plan `claim_layout_projected_frame_and_frame_door` R5).
        """
        auth_id = crs.authid() if crs is not None else ''
        if ':' in auth_id:
            try:
                return int(auth_id.split(':')[1])
            except (ValueError, IndexError):
                pass
        label = auth_id or (crs.description() if crs is not None else '') or '(none)'
        raise ValueError(
            f"The project CRS {label!r} "
            "has no EPSG code. Claim grids need a projected CRS with an EPSG "
            "authority id (a UTM zone, e.g. EPSG:26911) — set the project CRS "
            "and try again."
        )

    # =========================================================================
    # ⛔ THE OFFLINE LOCAL FALLBACK WAS DELETED (claims-petra P6, 2026-09-14)
    # =========================================================================
    #
    # ``_generate_lode_grid_local`` / ``_generate_placer_grid_local`` and their
    # four helpers (``_create_claims_layer``, ``_create_claim_polygon``,
    # ``_create_claim_feature``, ``_rotate_point``) drew claim rectangles HERE,
    # in the browser-side plugin, from its own copy of the 600 x 1500 ft
    # dimensions and its own rotation. That made the plugin the FIFTH copy of a
    # geometry the server now publishes as ONE contract
    # (``services/claims/grid_generator.geometry_contract()``), and it carried a
    # truncated ``4046.86`` sq-m-per-acre constant of its own.
    #
    # Two reasons it had to go, and the second is the one that mattered:
    #
    # 1. **Drift arrives in ONE commit, not gradually.** The first placer,
    #    state-variant or AK-MTRSC claim moves the server's rule and this copy
    #    keeps silently drawing 600 x 1500. The failure shape is a customer
    #    signing a document that describes ground they did not see.
    # 2. **It was not only an OFFLINE path.** The dispatch caught ANY exception
    #    from the server call — a 500, a timeout, an auth failure — logged a
    #    warning, and drew local geometry instead. A user with a working
    #    connection and a transient server error got client-drawn claims and no
    #    indication anything had changed. That is silent divergence from the
    #    geometry of record.
    #
    # Offline now REFUSES, loudly (``server_required.require_server``, shared
    # with ordering / validation / corner alignment since 2026-09-29). Both callers
    # already wrap generation in try/except and show the message in a dialog.
    # ⛔ Do not restore a local generator "just for offline". If offline grid
    # drawing is ever wanted, it must consume the SAME published contract, not
    # a second set of constants.

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
