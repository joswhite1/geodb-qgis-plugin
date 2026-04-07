# -*- coding: utf-8 -*-
"""
Legacy Claims Loader

Loads claims data from old QClaims-format GeoPackages (pre-geodb plugin),
builds the layers and state needed by ClaimsMapGenerator, applies styling,
and generates field/filing maps in one shot.

Old QClaims GeoPackage format:
- Metadata table: qclaims_metadata (key/value pairs)
- Dynamic table names: "{prefix} Lode Claims", "{prefix} Initial Claim Layout"
- Fixed table names: corner points, LM Corners, Center Lines, Monuments,
  Endline_Monuments, References, Waypoints
- CRS: UTM (e.g. EPSG:26911)
"""
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

from qgis.core import (
    Qgis,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsFeatureRequest,
    QgsField,
    QgsFields,
    QgsGeometry,
    QgsMapLayer,
    QgsMarkerSymbol,
    QgsPalLayerSettings,
    QgsPointXY,
    QgsProject,
    QgsProperty,
    QgsRectangle,
    QgsSimpleFillSymbolLayer,
    QgsSimpleLineSymbolLayer,
    QgsSimpleMarkerSymbolLayer,
    QgsSingleSymbolRenderer,
    QgsSymbol,
    QgsTextBufferSettings,
    QgsTextFormat,
    QgsVectorLayer,
    QgsVectorLayerSimpleLabeling,
    QgsWkbTypes,
    QgsCategorizedSymbolRenderer,
    QgsRendererCategory,
)
from qgis.PyQt.QtGui import QColor, QFont

logger = logging.getLogger('geodb')

# Map old GPX Symbol values to new waypoint_type values
_SYMBOL_TO_TYPE = {
    'City (Medium)': 'corner',
    'Navaid, Green': 'discovery',
    'Navaid, Blue': 'endline',      # Arizona endline monuments
    'Navaid, White': 'witness',
    'Flag, Red': 'discovery',
    'Flag, Green': 'sideline',
}


def is_legacy_qclaims_geopackage(path: str) -> bool:
    """Check if a GeoPackage is an old QClaims-format file.

    Returns True if the file has a qclaims_metadata table but no
    claims_metadata table (which would indicate the new format).
    """
    try:
        conn = sqlite3.connect(path)
        cursor = conn.cursor()

        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='qclaims_metadata'"
        )
        has_old = cursor.fetchone() is not None

        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='claims_metadata'"
        )
        has_new = cursor.fetchone() is not None

        conn.close()
        return has_old and not has_new
    except Exception:
        return False


class LegacyClaimsLoader:
    """Load old QClaims GeoPackages and generate maps from them.

    Usage::

        loader = LegacyClaimsLoader(gpkg_path)
        results = loader.load_and_generate_maps()
        # results is a dict like {'field_map': 'layout_name', ...}
    """

    def __init__(self, gpkg_path: str):
        self.gpkg_path = gpkg_path
        self.metadata: Dict[str, str] = {}
        self.prefix: str = ""
        self.epsg: int = 0
        self.crs: Optional[QgsCoordinateReferenceSystem] = None
        self._loaded_layers: Dict[str, QgsVectorLayer] = {}

    # =========================================================================
    # PUBLIC API
    # =========================================================================

    def load_and_generate_maps(self) -> Dict[str, str]:
        """One-shot: load legacy data, build layers, generate all maps.

        Returns:
            Dict mapping map type to layout name (same as
            ClaimsMapGenerator.generate_all_maps).
        """
        self._read_metadata()
        self._detect_crs()
        self._load_layers()
        self._apply_all_styles()
        state = self._build_wizard_state()

        from .claims_map_generator import ClaimsMapGenerator
        generator = ClaimsMapGenerator(state)
        return generator.generate_all_maps()

    def load_layers_only(self) -> Dict[str, QgsVectorLayer]:
        """Load layers into QGIS with correct names and styles (no map gen).

        Returns:
            Dict mapping display name to QgsVectorLayer.
        """
        self._read_metadata()
        self._detect_crs()
        self._load_layers()
        self._apply_all_styles()
        return dict(self._loaded_layers)

    # =========================================================================
    # METADATA
    # =========================================================================

    def _read_metadata(self):
        """Read qclaims_metadata key-value table."""
        conn = sqlite3.connect(self.gpkg_path)
        cursor = conn.cursor()

        cursor.execute("SELECT key, value FROM qclaims_metadata")
        self.metadata = dict(cursor.fetchall())
        conn.close()

        self.prefix = self.metadata.get('base_name', '')
        if not self.prefix:
            # Try to extract from GeoPackage filename
            self.prefix = Path(self.gpkg_path).stem.split(' ')[0]

        logger.info(
            f"[LEGACY] Read metadata: prefix={self.prefix}, "
            f"claimant={self.metadata.get('claimant_name', '?')}"
        )

    def _detect_crs(self):
        """Detect CRS from the GeoPackage geometry columns."""
        conn = sqlite3.connect(self.gpkg_path)
        cursor = conn.cursor()

        # Get SRS from the main claims table
        claims_table = f"{self.prefix} Lode Claims"
        cursor.execute(
            "SELECT srs_id FROM gpkg_geometry_columns WHERE table_name=?",
            (claims_table,)
        )
        row = cursor.fetchone()
        if row:
            self.epsg = row[0]
        else:
            # Fallback: pick the first non-4326 SRS
            cursor.execute(
                "SELECT DISTINCT srs_id FROM gpkg_geometry_columns WHERE srs_id != 4326"
            )
            row = cursor.fetchone()
            if row:
                self.epsg = row[0]

        conn.close()

        if self.epsg:
            self.crs = QgsCoordinateReferenceSystem(f"EPSG:{self.epsg}")
            logger.info(f"[LEGACY] Detected CRS: EPSG:{self.epsg}")

    # =========================================================================
    # LAYER LOADING
    # =========================================================================

    def _load_layers(self):
        """Load all layers from the GeoPackage with correct display names."""
        from ..utils.layer_utils import get_or_create_claims_group

        project = QgsProject.instance()
        root = project.layerTreeRoot()
        group_name = f"Claims Workflow [{self.prefix} Lode Claims]"
        group = get_or_create_claims_group(group_name, root)

        # Map: (old table name, new display name)
        table_map = [
            (f"{self.prefix} Lode Claims", "Lode Claims"),
            ("corner points", "Corner Points"),
            ("LM Corners", "LM Corners"),
            ("Center Lines", "Center Lines"),
            ("Monuments", "Monuments"),
            ("Endline_Monuments", "Endline Monuments"),
        ]

        for table_name, display_name in table_map:
            layer_uri = f"{self.gpkg_path}|layername={table_name}"
            layer = QgsVectorLayer(layer_uri, display_name, "ogr")
            if layer.isValid() and layer.featureCount() > 0:
                project.addMapLayer(layer, False)
                group.insertLayer(0, layer)
                self._loaded_layers[display_name] = layer
                logger.info(
                    f"[LEGACY] Loaded '{display_name}' from '{table_name}' "
                    f"({layer.featureCount()} features)"
                )

        # Waypoints need special handling: create a memory layer with the
        # waypoint_type field that the map generator expects.
        self._create_waypoints_layer(group)

    def _create_waypoints_layer(self, group):
        """Build a Claims Waypoints memory layer from the old Waypoints table.

        The old format stores waypoint type in the Symbol field. The new map
        generator expects a waypoint_type field. We create a memory layer
        with both fields populated.
        """
        source_uri = f"{self.gpkg_path}|layername=Waypoints"
        source = QgsVectorLayer(source_uri, "_tmp_waypoints", "ogr")
        if not source.isValid() or source.featureCount() == 0:
            logger.warning("[LEGACY] Waypoints table not found or empty")
            return

        # Build memory layer with correct fields
        display_name = "Claims Waypoints"
        crs_str = self.crs.authid() if self.crs else "EPSG:4326"

        mem_layer = QgsVectorLayer(
            f"Point?crs={crs_str}",
            display_name,
            "memory"
        )
        fields = QgsFields()
        fields.append(QgsField("No", _qvariant_type('Int')))
        fields.append(QgsField("Name", _qvariant_type('QString')))
        fields.append(QgsField("Latitude", _qvariant_type('Double')))
        fields.append(QgsField("Longitude", _qvariant_type('Double')))
        fields.append(QgsField("Altitude", _qvariant_type('Double')))
        fields.append(QgsField("Symbol", _qvariant_type('QString')))
        fields.append(QgsField("Date", _qvariant_type('QString')))
        fields.append(QgsField("Time", _qvariant_type('QString')))
        fields.append(QgsField("waypoint_type", _qvariant_type('QString')))
        fields.append(QgsField("claim", _qvariant_type('QString')))

        mem_layer.dataProvider().addAttributes(fields.toList())
        mem_layer.updateFields()

        features = []
        for feat in source.getFeatures():
            new_feat = QgsFeature()
            new_feat.setFields(mem_layer.fields())
            new_feat.setGeometry(feat.geometry())

            no_val = feat['No']
            new_feat['No'] = int(no_val) if no_val else 0
            new_feat['Name'] = feat['Name'] or ''
            new_feat['Latitude'] = feat['Latitude'] or 0
            new_feat['Longitude'] = feat['Longitude'] or 0
            new_feat['Altitude'] = feat['Altitude'] or 0
            new_feat['Symbol'] = feat['Symbol'] or ''
            new_feat['Date'] = feat['Date'] or ''
            new_feat['Time'] = feat['Time'] or ''

            # Map Symbol → waypoint_type
            symbol = feat['Symbol'] or ''
            new_feat['waypoint_type'] = _SYMBOL_TO_TYPE.get(symbol, 'corner')

            # Try to associate with a claim from the Name field
            name = feat['Name'] or ''
            new_feat['claim'] = ''  # Old format doesn't store claim association

            features.append(new_feat)

        mem_layer.dataProvider().addFeatures(features)
        mem_layer.updateExtents()

        QgsProject.instance().addMapLayer(mem_layer, False)
        group.insertLayer(0, mem_layer)
        self._loaded_layers["Claims Waypoints"] = mem_layer
        logger.info(
            f"[LEGACY] Created Claims Waypoints layer with "
            f"{len(features)} waypoints"
        )

    # =========================================================================
    # STYLING
    # =========================================================================

    def _apply_all_styles(self):
        """Apply appropriate styling to each loaded layer."""
        style_map = {
            'Lode Claims': self._style_lode_claims,
            'Corner Points': self._style_corner_points,
            'LM Corners': self._style_lm_corners,
            'Center Lines': self._style_centerlines,
            'Monuments': self._style_monuments,
            'Endline Monuments': self._style_endline_monuments,
            'Claims Waypoints': self._style_waypoints,
        }
        for name, style_fn in style_map.items():
            layer = self._loaded_layers.get(name)
            if layer:
                try:
                    style_fn(layer)
                except Exception as e:
                    logger.warning(f"[LEGACY] Could not style '{name}': {e}")

    def _style_lode_claims(self, layer: QgsVectorLayer):
        """Light blue fill with steel blue outline, claim name labels."""
        symbol = QgsSymbol.defaultSymbol(QgsWkbTypes.PolygonGeometry)
        fill = QgsSimpleFillSymbolLayer()
        fill.setColor(QColor(173, 216, 230, 100))
        fill.setStrokeColor(QColor(70, 130, 180))
        fill.setStrokeWidth(0.5)
        symbol.changeSymbolLayer(0, fill)
        layer.setRenderer(QgsSingleSymbolRenderer(symbol))

        label_settings = QgsPalLayerSettings()
        label_settings.fieldName = 'Name'
        label_settings.enabled = True
        text_format = QgsTextFormat()
        text_format.setFont(QFont("Arial", 9, QFont.Bold))
        text_format.setColor(QColor(25, 25, 112))
        text_format.setSize(9)
        buf = QgsTextBufferSettings()
        buf.setEnabled(True)
        buf.setSize(1.0)
        buf.setColor(QColor(255, 255, 255))
        text_format.setBuffer(buf)
        label_settings.setFormat(text_format)
        label_settings.placement = Qgis.LabelPlacement.OverPoint
        layer.setLabeling(QgsVectorLayerSimpleLabeling(label_settings))
        layer.setLabelsEnabled(True)
        layer.triggerRepaint()

    def _style_corner_points(self, layer: QgsVectorLayer):
        """Black circles with corner number labels."""
        symbol = QgsSymbol.defaultSymbol(QgsWkbTypes.PointGeometry)
        marker = QgsSimpleMarkerSymbolLayer()
        marker.setShape(QgsSimpleMarkerSymbolLayer.Circle)
        marker.setSize(2.5)
        marker.setColor(QColor(0, 0, 0))
        marker.setStrokeColor(QColor(0, 0, 0))
        symbol.changeSymbolLayer(0, marker)
        layer.setRenderer(QgsSingleSymbolRenderer(symbol))

        label_settings = QgsPalLayerSettings()
        label_settings.fieldName = 'Corner #'
        label_settings.enabled = True
        text_format = QgsTextFormat()
        text_format.setFont(QFont("Arial", 7, QFont.Bold))
        text_format.setColor(QColor(0, 0, 0))
        buf = QgsTextBufferSettings()
        buf.setEnabled(True)
        buf.setSize(1.0)
        buf.setColor(QColor(255, 255, 255))
        text_format.setBuffer(buf)
        label_settings.setFormat(text_format)
        layer.setLabeling(QgsVectorLayerSimpleLabeling(label_settings))
        layer.setLabelsEnabled(True)
        layer.triggerRepaint()

    def _style_lm_corners(self, layer: QgsVectorLayer):
        """Green circles for LM corners."""
        symbol = QgsSymbol.defaultSymbol(QgsWkbTypes.PointGeometry)
        marker = QgsSimpleMarkerSymbolLayer()
        marker.setShape(QgsSimpleMarkerSymbolLayer.Circle)
        marker.setSize(3.0)
        marker.setColor(QColor(34, 139, 34))
        marker.setStrokeColor(QColor(0, 100, 0))
        symbol.changeSymbolLayer(0, marker)
        layer.setRenderer(QgsSingleSymbolRenderer(symbol))
        layer.triggerRepaint()

    def _style_centerlines(self, layer: QgsVectorLayer):
        """Dashed red lines."""
        symbol = QgsSymbol.defaultSymbol(QgsWkbTypes.LineGeometry)
        line = QgsSimpleLineSymbolLayer()
        line.setColor(QColor(220, 20, 60))
        line.setWidth(0.4)
        line.setPenStyle(2)  # DashLine
        symbol.changeSymbolLayer(0, line)
        layer.setRenderer(QgsSingleSymbolRenderer(symbol))
        layer.triggerRepaint()

    def _style_monuments(self, layer: QgsVectorLayer):
        """Green triangles with name labels."""
        symbol = QgsSymbol.defaultSymbol(QgsWkbTypes.PointGeometry)
        marker = QgsSimpleMarkerSymbolLayer()
        marker.setShape(QgsSimpleMarkerSymbolLayer.Triangle)
        marker.setSize(3.0)
        marker.setColor(QColor(50, 205, 50))
        marker.setStrokeColor(QColor(34, 139, 34))
        symbol.changeSymbolLayer(0, marker)
        layer.setRenderer(QgsSingleSymbolRenderer(symbol))

        label_settings = QgsPalLayerSettings()
        label_settings.fieldName = 'Name'
        label_settings.enabled = True
        text_format = QgsTextFormat()
        text_format.setFont(QFont("Arial", 7))
        text_format.setColor(QColor(0, 100, 0))
        buf = QgsTextBufferSettings()
        buf.setEnabled(True)
        buf.setSize(0.8)
        buf.setColor(QColor(255, 255, 255))
        text_format.setBuffer(buf)
        label_settings.setFormat(text_format)
        layer.setLabeling(QgsVectorLayerSimpleLabeling(label_settings))
        layer.setLabelsEnabled(True)
        layer.triggerRepaint()

    def _style_endline_monuments(self, layer: QgsVectorLayer):
        """Blue squares for endline monuments."""
        symbol = QgsSymbol.defaultSymbol(QgsWkbTypes.PointGeometry)
        marker = QgsSimpleMarkerSymbolLayer()
        marker.setShape(QgsSimpleMarkerSymbolLayer.Square)
        marker.setSize(2.5)
        marker.setColor(QColor(59, 130, 246))
        marker.setStrokeColor(QColor(29, 78, 216))
        symbol.changeSymbolLayer(0, marker)
        layer.setRenderer(QgsSingleSymbolRenderer(symbol))
        layer.triggerRepaint()

    def _style_waypoints(self, layer: QgsVectorLayer):
        """Categorized styling by waypoint_type, matching the new plugin."""
        categories = []

        corner_sym = QgsMarkerSymbol.createSimple({
            'name': 'circle', 'color': '#000000',
            'outline_color': '#000000', 'outline_width': '0.3',
            'size': '1.6', 'size_unit': 'MM',
        })
        categories.append(QgsRendererCategory('corner', corner_sym, 'Corner'))

        discovery_sym = QgsMarkerSymbol.createSimple({
            'name': 'circle', 'color': '#32CD32',
            'outline_color': '#228B22', 'outline_width': '0.3',
            'size': '1.8', 'size_unit': 'MM',
        })
        categories.append(QgsRendererCategory('discovery', discovery_sym, 'Location Monument'))

        endline_sym = QgsMarkerSymbol.createSimple({
            'name': 'circle', 'color': '#3b82f6',
            'outline_color': '#1d4ed8', 'outline_width': '0.3',
            'size': '1.8', 'size_unit': 'MM',
        })
        categories.append(QgsRendererCategory('endline', endline_sym, 'Endline Monument'))

        sideline_sym = QgsMarkerSymbol.createSimple({
            'name': 'circle', 'color': '#3b82f6',
            'outline_color': '#1d4ed8', 'outline_width': '0.3',
            'size': '1.8', 'size_unit': 'MM',
        })
        categories.append(QgsRendererCategory('sideline', sideline_sym, 'Sideline Monument'))

        witness_sym = QgsMarkerSymbol.createSimple({
            'name': 'circle', 'color': '#FFFFFF',
            'outline_color': '#000000', 'outline_width': '0.4',
            'size': '1.8', 'size_unit': 'MM',
        })
        categories.append(QgsRendererCategory('witness', witness_sym, 'Witness'))

        default_sym = QgsMarkerSymbol.createSimple({
            'name': 'circle', 'color': '#6b7280',
            'outline_color': '#374151', 'outline_width': '0.3',
            'size': '1.5', 'size_unit': 'MM',
        })
        categories.append(QgsRendererCategory('', default_sym, 'Other'))

        renderer = QgsCategorizedSymbolRenderer('waypoint_type', categories)
        layer.setRenderer(renderer)

        # Labeling
        label_settings = QgsPalLayerSettings()
        label_settings.fieldName = 'Name'
        label_settings.enabled = True
        label_settings.placement = QgsPalLayerSettings.OverPoint
        text_format = QgsTextFormat()
        text_format.setFont(QFont("Arial", 7, QFont.Bold))
        text_format.setColor(QColor(0, 0, 0))
        buf = QgsTextBufferSettings()
        buf.setEnabled(True)
        buf.setSize(1.5)
        buf.setColor(QColor(255, 255, 255))
        text_format.setBuffer(buf)
        label_settings.setFormat(text_format)
        layer.setLabeling(QgsVectorLayerSimpleLabeling(label_settings))
        layer.setLabelsEnabled(True)
        layer.triggerRepaint()

    # =========================================================================
    # STATE BUILDING
    # =========================================================================

    def _build_wizard_state(self):
        """Build a ClaimsWizardState populated from legacy metadata + features.

        The map generator reads state.processed_claims, state.reference_points,
        state.grid_name_prefix, state.claimant_name, state.project_epsg,
        state.monument_type, etc.
        """
        from ..ui.claims_wizard_state import ClaimsWizardState

        state = ClaimsWizardState()
        state.geopackage_path = self.gpkg_path
        state.grid_name_prefix = self.prefix
        state.claimant_name = self.metadata.get('claimant_name', '')
        state.address_line1 = self.metadata.get('address_1', '')
        state.address_line2 = (
            f"{self.metadata.get('city', '')}, "
            f"{self.metadata.get('state', '')} "
            f"{self.metadata.get('zip_code', '')}"
        ).strip(', ')
        state.monument_inset_ft = float(
            self.metadata.get('monument_inset_ft', '25.0')
        )
        state.monument_type = "2' wooden post"
        state.project_epsg = self.epsg if self.epsg else None

        # Build processed_claims from the claims layer features
        state.processed_claims = self._build_processed_claims()

        # Build reference_points from the References table
        state.reference_points = self._build_reference_points()

        # Mark as fully processed so map generator doesn't complain
        state.completed_steps = [1, 2, 3, 4, 5, 6]

        return state

    def _build_processed_claims(self) -> List[Dict[str, Any]]:
        """Build the processed_claims list from the old claims layer.

        The map generator needs each claim dict to have: name, state, county,
        corners (with easting/northing), lm_corner, plss, and optionally
        endline_monuments.
        """
        claims_layer = self._loaded_layers.get('Lode Claims')
        corners_layer = self._loaded_layers.get('Corner Points')
        endline_layer = self._loaded_layers.get('Endline Monuments')
        monuments_layer = self._loaded_layers.get('Monuments')

        if not claims_layer:
            logger.warning("[LEGACY] No Lode Claims layer found")
            return []

        # Index corner points by claim name
        corners_by_claim: Dict[str, List[Dict]] = {}
        if corners_layer:
            for feat in corners_layer.getFeatures():
                claim = feat['Claim']
                geom = feat.geometry()
                pt = geom.asPoint() if geom else None
                if claim and pt:
                    corners_by_claim.setdefault(claim, []).append({
                        'corner_number': feat['Corner #'],
                        'easting': pt.x(),
                        'northing': pt.y(),
                    })

        # Sort corners by corner number within each claim
        for claim_name in corners_by_claim:
            corners_by_claim[claim_name].sort(
                key=lambda c: c.get('corner_number', 0)
            )

        # Index endline monuments by claim name
        endlines_by_claim: Dict[str, List[Dict]] = {}
        if endline_layer:
            for feat in endline_layer.getFeatures():
                claim = feat['Claim']
                geom = feat.geometry()
                pt = geom.asPoint() if geom else None
                if claim and pt:
                    endlines_by_claim.setdefault(claim, []).append({
                        'easting': pt.x(),
                        'northing': pt.y(),
                        'name': feat['Name'] if 'Name' in feat.fields().names() else '',
                    })

        # Index discovery monuments by claim name
        monuments_by_claim: Dict[str, Dict] = {}
        if monuments_layer:
            for feat in monuments_layer.getFeatures():
                claim = feat['Claim']
                geom = feat.geometry()
                pt = geom.asPoint() if geom else None
                if claim and pt:
                    monuments_by_claim[claim] = {
                        'easting': pt.x(),
                        'northing': pt.y(),
                    }

        # Build processed claims
        claims = []
        for feat in claims_layer.getFeatures():
            name = feat['Name']
            corners = corners_by_claim.get(name, [])

            # Parse QSecs into PLSS dict
            qsecs = feat['QSecs'] or ''
            meridian = feat['Meridian'] or ''
            plss = self._parse_qsecs_to_plss(qsecs, meridian)

            claim_dict = {
                'name': name,
                'state': feat['State'] or '',
                'county': feat['County'] or '',
                'lm_corner': feat['LM Corner'] if feat['LM Corner'] else 1,
                'corners': corners,
                'plss': plss,
                'endline_monuments': endlines_by_claim.get(name, []),
                'discovery_monument': monuments_by_claim.get(name),
                'dimensions': feat['Dimensions'] or '',
                'lode_azimuth': feat['Lode_Azimuth'],
            }
            claims.append(claim_dict)

        logger.info(f"[LEGACY] Built {len(claims)} processed claims")
        return claims

    def _build_reference_points(self) -> List[Dict[str, Any]]:
        """Build reference_points from the old References table."""
        ref_uri = f"{self.gpkg_path}|layername=References"
        ref_layer = QgsVectorLayer(ref_uri, "_tmp_refs", "ogr")
        if not ref_layer.isValid() or ref_layer.featureCount() == 0:
            return []

        points = []
        for feat in ref_layer.getFeatures():
            geom = feat.geometry()
            pt = geom.asPoint() if geom else None
            if pt:
                points.append({
                    'name': feat['Name'] or 'Survey Monument',
                    'easting': pt.x(),
                    'northing': pt.y(),
                })

        logger.info(f"[LEGACY] Found {len(points)} reference point(s)")
        return points

    def _parse_qsecs_to_plss(self, qsecs: str, meridian: str) -> Dict[str, str]:
        """Parse QSecs string like 'SE Quarter of SEC 04, T008N, R001E; ...'
        into a PLSS dict with section, township, range.

        Takes the first quarter-section entry.
        """
        if not qsecs:
            return {}

        # Take first entry (before semicolon)
        first = qsecs.split(';')[0].strip()
        if not first:
            return {}

        plss = {}

        # Extract section
        import re
        sec_match = re.search(r'SEC\s+(\d+)', first, re.IGNORECASE)
        if sec_match:
            plss['section'] = sec_match.group(1).lstrip('0') or '0'

        # Extract township
        twp_match = re.search(r'(T\d+[NS])', first, re.IGNORECASE)
        if twp_match:
            plss['township'] = twp_match.group(1)

        # Extract range
        rng_match = re.search(r'(R\d+[EW])', first, re.IGNORECASE)
        if rng_match:
            plss['range'] = rng_match.group(1)

        if meridian:
            plss['meridian'] = meridian

        return plss


# =========================================================================
# HELPERS
# =========================================================================

def _qvariant_type(type_name: str):
    """Get QVariant type for QgsField, compatible across Qt versions."""
    try:
        from qgis.PyQt.QtCore import QVariant
        type_map = {
            'Int': QVariant.Int,
            'Double': QVariant.Double,
            'QString': QVariant.String,
        }
        return type_map.get(type_name, QVariant.String)
    except Exception:
        # Fallback for newer Qt where QVariant types moved
        from qgis.PyQt.QtCore import QMetaType
        type_map = {
            'Int': QMetaType.Type.Int,
            'Double': QMetaType.Type.Double,
            'QString': QMetaType.Type.QString,
        }
        return type_map.get(type_name, QMetaType.Type.QString)
