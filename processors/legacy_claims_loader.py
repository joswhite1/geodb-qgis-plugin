# -*- coding: utf-8 -*-
"""
Claims Loader

Loads claims data from GeoPackages and generates field/filing maps in one
shot — without re-running the wizard steps.  Supports two formats:

1. **Old QClaims format** (pre-geodb plugin):
   - Metadata table: ``qclaims_metadata`` (key/value pairs)
   - Dynamic table names: "{prefix} Lode Claims", etc.
   - Fixed table names: corner points, LM Corners, Center Lines, Monuments,
     Endline_Monuments, References, Waypoints

2. **New geodb-plugin format** (current plugin):
   - Metadata table: ``claims_metadata`` (key/value pairs)
   - Fixed table names: lode_claims, corner_points, lm_corners, center_lines,
     monuments, endline_monuments, reference_points, claim_waypoints

A single :class:`ClaimsLoader` class handles both formats; the differences
live in a :class:`ClaimsFormat` spec object that describes the metadata key
names, table names, and format-specific quirks.
"""
import json
import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from qgis.core import (
    Qgis,
    QgsCategorizedSymbolRenderer,
    QgsCoordinateReferenceSystem,
    QgsFeature,
    QgsField,
    QgsFields,
    QgsMarkerSymbol,
    QgsPalLayerSettings,
    QgsProject,
    QgsRendererCategory,
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
)
from qgis.PyQt.QtGui import QColor, QFont
from ..utils.compat import QFont_Bold

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


# =========================================================================
# FORMAT DETECTION
# =========================================================================

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


def is_new_format_claims_geopackage(path: str) -> bool:
    """Check if a GeoPackage is a new geodb-plugin-format claims file."""
    try:
        conn = sqlite3.connect(path)
        cursor = conn.cursor()

        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='claims_metadata'"
        )
        has_metadata = cursor.fetchone() is not None

        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='lode_claims'"
        )
        has_claims = cursor.fetchone() is not None

        conn.close()
        return has_metadata and has_claims
    except Exception:
        return False


def is_any_claims_geopackage(path: str) -> bool:
    """Check if a GeoPackage is either an old QClaims or new geodb claims file."""
    return is_legacy_qclaims_geopackage(path) or is_new_format_claims_geopackage(path)


# =========================================================================
# FORMAT SPECIFICATION
# =========================================================================

@dataclass
class ClaimsFormat:
    """Describes a GeoPackage claims format.

    Each format (legacy QClaims vs new geodb-plugin) uses different metadata
    table/key names, layer table names, and storage conventions. This object
    captures those differences so a single loader can handle both.
    """

    name: str  # human-readable tag for logs, e.g. "LEGACY" or "CLAIMS"

    # Metadata table
    metadata_table: str
    metadata_keys: Dict[str, str]  # logical name -> actual key in this format

    # Layer tables — list of (table_name_or_factory, display_name) pairs.
    # If the entry is a callable, it's called with the prefix to get the
    # actual table name (legacy uses "{prefix} Lode Claims").
    claims_layers: List[tuple]

    # Waypoints handling
    waypoints_table: str
    waypoints_needs_conversion: bool  # True for legacy (Symbol → waypoint_type)

    # References handling
    references_mode: str  # "table" (legacy) or "metadata_json" (new)
    references_table: str  # only used if references_mode == "table"

    # Whether the claims layer has QSecs/Meridian fields for PLSS parsing
    has_plss_fields: bool

    # Main claims table name (for CRS detection) — may be a factory
    claims_table_for_crs: Any  # str or callable(prefix) -> str


def _legacy_claims_table(prefix: str) -> str:
    return f"{prefix} Lode Claims"


LEGACY_FORMAT = ClaimsFormat(
    name='LEGACY',
    metadata_table='qclaims_metadata',
    metadata_keys={
        'prefix': 'base_name',
        'claimant_name': 'claimant_name',
        'address_1': 'address_1',
        'city': 'city',
        'state': 'state',
        'zip': 'zip_code',
        'mining_district': 'mining_district',
        'monument_type': 'monument_type',
        'monument_inset': 'monument_inset_ft',
    },
    claims_layers=[
        (_legacy_claims_table, 'Lode Claims'),
        ('corner points', 'Corner Points'),
        ('LM Corners', 'LM Corners'),
        ('Center Lines', 'Center Lines'),
        ('Monuments', 'Monuments'),
        ('Endline_Monuments', 'Endline Monuments'),
    ],
    waypoints_table='Waypoints',
    waypoints_needs_conversion=True,
    references_mode='table',
    references_table='References',
    has_plss_fields=True,
    claims_table_for_crs=_legacy_claims_table,
)


NEW_FORMAT = ClaimsFormat(
    name='CLAIMS',
    metadata_table='claims_metadata',
    metadata_keys={
        'prefix': 'grid_name_prefix',
        'claimant_name': 'claimant_name',
        'address_line1': 'address_line1',
        'address_line2': 'address_line2',
        'address_line3': 'address_line3',
        'mining_district': 'mining_district',
        'monument_type': 'monument_type',
        'monument_inset': 'monument_inset_ft',
        'grid_rows': 'grid_rows',
        'grid_cols': 'grid_cols',
        'grid_azimuth': 'grid_azimuth',
        'lm_corner': 'lm_corner',
        'reference_points_json': 'reference_points',
        'claim_package_id': 'claim_package_id',
    },
    claims_layers=[
        ('lode_claims', 'Lode Claims'),
        ('corner_points', 'Corner Points'),
        ('lm_corners', 'LM Corners'),
        ('center_lines', 'Center Lines'),
        ('monuments', 'Monuments'),
        ('endline_monuments', 'Endline Monuments'),
    ],
    waypoints_table='claim_waypoints',
    waypoints_needs_conversion=False,
    references_mode='metadata_json',
    references_table='',
    has_plss_fields=False,
    claims_table_for_crs='lode_claims',
)


# =========================================================================
# UNIFIED CLAIMS LOADER
# =========================================================================

class ClaimsLoader:
    """Load a claims GeoPackage (either format) and generate maps.

    Usage::

        loader = ClaimsLoader(gpkg_path)  # auto-detects format
        results = loader.load_and_generate_maps()
        # results is a dict like {'field_map': 'layout_name', ...}

    You can also pass an explicit :class:`ClaimsFormat` if auto-detection
    isn't appropriate for the caller.
    """

    def __init__(self, gpkg_path: str, fmt: Optional[ClaimsFormat] = None):
        self.gpkg_path = gpkg_path
        self.fmt = fmt or self._detect_format(gpkg_path)
        self.metadata: Dict[str, str] = {}
        self.prefix: str = ""
        self.epsg: int = 0
        self.crs: Optional[QgsCoordinateReferenceSystem] = None
        self._loaded_layers: Dict[str, QgsVectorLayer] = {}

    @staticmethod
    def _detect_format(path: str) -> ClaimsFormat:
        if is_legacy_qclaims_geopackage(path):
            return LEGACY_FORMAT
        if is_new_format_claims_geopackage(path):
            return NEW_FORMAT
        raise ValueError(
            "Not a recognised claims GeoPackage (expected qclaims_metadata "
            "or claims_metadata table)."
        )

    # =========================================================================
    # PUBLIC API
    # =========================================================================

    def load_and_generate_maps(self) -> Dict[str, str]:
        """One-shot: load data, build layers, generate all maps.

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

    def _meta_key(self, logical_name: str) -> str:
        """Look up the format-specific metadata key for a logical name."""
        return self.fmt.metadata_keys.get(logical_name, logical_name)

    def _meta_get(self, logical_name: str, default: str = '') -> str:
        """Get a metadata value by its logical (format-agnostic) name."""
        return self.metadata.get(self._meta_key(logical_name), default)

    def _read_metadata(self):
        """Read the metadata key-value table."""
        conn = sqlite3.connect(self.gpkg_path)
        cursor = conn.cursor()

        cursor.execute(f"SELECT key, value FROM {self.fmt.metadata_table}")
        self.metadata = dict(cursor.fetchall())
        conn.close()

        self.prefix = self._meta_get('prefix')
        if not self.prefix:
            # Fall back to GeoPackage filename
            self.prefix = Path(self.gpkg_path).stem.split(' ')[0]

        logger.info(
            f"[{self.fmt.name}] Read metadata: prefix={self.prefix}, "
            f"claimant={self._meta_get('claimant_name', '?')}"
        )

    def _detect_crs(self):
        """Detect CRS from the GeoPackage geometry columns."""
        conn = sqlite3.connect(self.gpkg_path)
        cursor = conn.cursor()

        # Resolve the main claims table name for this format
        table_spec = self.fmt.claims_table_for_crs
        if callable(table_spec):
            claims_table = table_spec(self.prefix)
        else:
            claims_table = table_spec

        cursor.execute(
            "SELECT srs_id FROM gpkg_geometry_columns WHERE table_name=?",
            (claims_table,)
        )
        row = cursor.fetchone()
        if row:
            self.epsg = row[0]
        else:
            # Fallback: first non-4326 SRS in the file
            cursor.execute(
                "SELECT DISTINCT srs_id FROM gpkg_geometry_columns WHERE srs_id != 4326"
            )
            row = cursor.fetchone()
            if row:
                self.epsg = row[0]

        conn.close()

        if self.epsg:
            self.crs = QgsCoordinateReferenceSystem(f"EPSG:{self.epsg}")
            logger.info(f"[{self.fmt.name}] Detected CRS: EPSG:{self.epsg}")

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

        for table_spec, display_name in self.fmt.claims_layers:
            table_name = table_spec(self.prefix) if callable(table_spec) else table_spec
            layer_uri = f"{self.gpkg_path}|layername={table_name}"
            layer = QgsVectorLayer(layer_uri, display_name, "ogr")
            if layer.isValid() and layer.featureCount() > 0:
                project.addMapLayer(layer, False)
                group.insertLayer(0, layer)
                self._loaded_layers[display_name] = layer
                logger.info(
                    f"[{self.fmt.name}] Loaded '{display_name}' from '{table_name}' "
                    f"({layer.featureCount()} features)"
                )

        # Waypoints: either load directly or convert from the old Symbol-based format
        if self.fmt.waypoints_needs_conversion:
            self._create_waypoints_layer_from_symbols(group)
        else:
            self._load_waypoints_layer_direct(group)

        # Reference points: load the persistent OGR layer so the ref point
        # survives in the Layers panel and on the print template after a
        # "Lock Styles For Layers" toggle. Always attempted regardless of
        # whether any reference points were entered — the table may be
        # empty on older GeoPackages, in which case this is a no-op.
        self._load_reference_points_layer(group)

    def _load_waypoints_layer_direct(self, group):
        """Load the waypoints table directly (new format).

        The new format already stores ``waypoint_type`` and ``claim`` fields,
        so no conversion is needed.
        """
        source_uri = f"{self.gpkg_path}|layername={self.fmt.waypoints_table}"
        layer = QgsVectorLayer(source_uri, "Claims Waypoints", "ogr")
        if not layer.isValid() or layer.featureCount() == 0:
            logger.warning(
                f"[{self.fmt.name}] {self.fmt.waypoints_table} table not found or empty"
            )
            return

        QgsProject.instance().addMapLayer(layer, False)
        group.insertLayer(0, layer)
        self._loaded_layers["Claims Waypoints"] = layer
        logger.info(
            f"[{self.fmt.name}] Loaded Claims Waypoints ({layer.featureCount()} features)"
        )

    def _create_waypoints_layer_from_symbols(self, group):
        """Build a Claims Waypoints memory layer from the old Waypoints table.

        The old format stores waypoint type in the Symbol field. The map
        generator expects a ``waypoint_type`` field. We create a memory layer
        with both fields populated.
        """
        source_uri = f"{self.gpkg_path}|layername={self.fmt.waypoints_table}"
        source = QgsVectorLayer(source_uri, "_tmp_waypoints", "ogr")
        if not source.isValid() or source.featureCount() == 0:
            logger.warning(f"[{self.fmt.name}] Waypoints table not found or empty")
            return

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

            symbol = feat['Symbol'] or ''
            new_feat['waypoint_type'] = _SYMBOL_TO_TYPE.get(symbol, 'corner')
            new_feat['claim'] = ''  # Old format doesn't store claim association

            features.append(new_feat)

        mem_layer.dataProvider().addFeatures(features)
        mem_layer.updateExtents()

        QgsProject.instance().addMapLayer(mem_layer, False)
        group.insertLayer(0, mem_layer)
        self._loaded_layers["Claims Waypoints"] = mem_layer
        logger.info(
            f"[{self.fmt.name}] Created Claims Waypoints layer with "
            f"{len(features)} waypoints"
        )

    def _load_reference_points_layer(self, group):
        """Load the persistent reference points layer from the GeoPackage.

        New format: ``reference_points`` OGR table. Legacy QClaims: the
        ``References`` OGR table. In both cases the geometry is a Point
        layer — we add it to the Claims Workflow group and style it so it
        matches the symbology used on generated filing maps.
        """
        if self.fmt.references_mode == 'metadata_json':
            table_name = 'reference_points'
        else:
            table_name = self.fmt.references_table

        if not table_name:
            return

        source_uri = f"{self.gpkg_path}|layername={table_name}"
        layer = QgsVectorLayer(source_uri, "Reference Points", "ogr")
        if not layer.isValid() or layer.featureCount() == 0:
            # Quietly skip — legitimate case when no ref points were
            # entered, or an older GeoPackage predates the table.
            return

        QgsProject.instance().addMapLayer(layer, False)
        group.insertLayer(0, layer)
        self._loaded_layers["Reference Points"] = layer
        logger.info(
            f"[{self.fmt.name}] Loaded Reference Points ({layer.featureCount()} features)"
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
            'Reference Points': self._style_reference_points,
        }
        for name, style_fn in style_map.items():
            layer = self._loaded_layers.get(name)
            if layer:
                try:
                    style_fn(layer)
                except Exception as e:
                    logger.warning(f"[{self.fmt.name}] Could not style '{name}': {e}")

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
        text_format.setFont(QFont("Arial", 9, QFont_Bold))
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
        text_format.setFont(QFont("Arial", 7, QFont_Bold))
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

    def _style_reference_points(self, layer: QgsVectorLayer):
        """Red triangle + italic red label, matching filing-map symbology.

        Legacy GeoPackages store the label in 'Description' (References
        table); the new format uses 'name'. Fall back to the first string
        field if neither is present.
        """
        symbol = QgsSymbol.defaultSymbol(QgsWkbTypes.PointGeometry)
        marker = QgsSimpleMarkerSymbolLayer()
        marker.setShape(QgsSimpleMarkerSymbolLayer.Triangle)
        marker.setSize(3.5)
        marker.setColor(QColor(204, 0, 0))
        marker.setStrokeColor(QColor(0, 0, 0))
        marker.setStrokeWidth(0.4)
        symbol.changeSymbolLayer(0, marker)
        layer.setRenderer(QgsSingleSymbolRenderer(symbol))

        field_names = [f.name() for f in layer.fields()]
        label_field = None
        for candidate in ('name', 'Description', 'label', 'Name'):
            if candidate in field_names:
                label_field = candidate
                break
        if label_field is None and field_names:
            label_field = field_names[0]

        if label_field:
            label_settings = QgsPalLayerSettings()
            label_settings.fieldName = label_field
            label_settings.enabled = True
            label_settings.placement = Qgis.LabelPlacement.OverPoint

            text_format = QgsTextFormat()
            italic_font = QFont("Arial", 7)
            italic_font.setItalic(True)
            text_format.setFont(italic_font)
            text_format.setColor(QColor(204, 0, 0))

            buf = QgsTextBufferSettings()
            buf.setEnabled(True)
            buf.setSize(1.5)
            buf.setColor(QColor(255, 255, 255))
            text_format.setBuffer(buf)

            label_settings.setFormat(text_format)
            layer.setLabeling(QgsVectorLayerSimpleLabeling(label_settings))
            layer.setLabelsEnabled(True)

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

        label_settings = QgsPalLayerSettings()
        label_settings.fieldName = 'Name'
        label_settings.enabled = True
        label_settings.placement = QgsPalLayerSettings.OverPoint
        text_format = QgsTextFormat()
        text_format.setFont(QFont("Arial", 7, QFont_Bold))
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
        """Build a ClaimsWizardState populated from metadata and features."""
        from ..ui.claims_wizard_state import ClaimsWizardState

        state = ClaimsWizardState()
        state.geopackage_path = self.gpkg_path
        state.grid_name_prefix = self.prefix
        state.claimant_name = self._meta_get('claimant_name')

        # Address fields — legacy stores city/state/zip separately, new format
        # stores pre-formatted address_line1/2/3.
        if self.fmt is LEGACY_FORMAT:
            state.address_line1 = self._meta_get('address_1')
            state.address_line2 = (
                f"{self._meta_get('city')}, "
                f"{self._meta_get('state')} "
                f"{self._meta_get('zip')}"
            ).strip(', ')
        else:
            state.address_line1 = (
                self._meta_get('address_line1')
                or self.metadata.get('claimant_address', '')
            )
            state.address_line2 = (
                self._meta_get('address_line2')
                or self.metadata.get('claimant_city', '')
            )
            state.address_line3 = (
                self._meta_get('address_line3')
                or self.metadata.get('claimant_state', '')
            )

        state.mining_district = self._meta_get('mining_district')
        state.monument_type = self._meta_get('monument_type') or "2' wooden post"
        state.monument_inset_ft = float(self._meta_get('monument_inset') or '25.0')
        state.project_epsg = self.epsg if self.epsg else None

        # New-format extras
        if self.fmt is NEW_FORMAT:
            state.grid_rows = int(self._meta_get('grid_rows') or '2')
            state.grid_cols = int(self._meta_get('grid_cols') or '4')
            state.grid_azimuth = float(self._meta_get('grid_azimuth') or '0.0')
            state.lm_corner = int(self._meta_get('lm_corner') or '1')

            pkg_id = self._meta_get('claim_package_id')
            state.claim_package_id = int(pkg_id) if pkg_id else None

        state.processed_claims = self._build_processed_claims()
        state.reference_points = self._build_reference_points()
        state.completed_steps = [1, 2, 3, 4, 5, 6]

        return state

    def _build_processed_claims(self) -> List[Dict[str, Any]]:
        """Build the processed_claims list from the claims layer features.

        Corners are extracted from the polygon vertices of each claim (the
        authoritative geometry). The corner_points table is not used because
        its coordinates may differ from the polygon.
        """
        claims_layer = self._loaded_layers.get('Lode Claims')
        endline_layer = self._loaded_layers.get('Endline Monuments')
        monuments_layer = self._loaded_layers.get('Monuments')

        if not claims_layer:
            logger.warning(f"[{self.fmt.name}] No Lode Claims layer found")
            return []

        # Extract corners from polygon vertices
        corners_by_claim: Dict[str, List[Dict]] = {}
        for feat in claims_layer.getFeatures():
            name = feat['Name']
            geom = feat.geometry()
            if not geom or geom.isEmpty():
                continue
            polygon = geom.asPolygon()
            if not polygon:
                continue
            # Exterior ring; last point duplicates first, so skip it
            ring = polygon[0]
            corners = []
            for i, pt in enumerate(ring[:-1]):
                corners.append({
                    'corner_number': i + 1,
                    'easting': pt.x(),
                    'northing': pt.y(),
                })
            corners_by_claim[name] = corners

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

        # Build processed claims list
        claims = []
        for feat in claims_layer.getFeatures():
            name = feat['Name']
            corners = corners_by_claim.get(name, [])

            # Parse PLSS info if this format has QSecs/Meridian fields
            plss: Dict[str, str] = {}
            if self.fmt.has_plss_fields:
                field_names = feat.fields().names()
                qsecs = feat['QSecs'] if 'QSecs' in field_names else ''
                meridian = feat['Meridian'] if 'Meridian' in field_names else ''
                plss = self._parse_qsecs_to_plss(qsecs or '', meridian or '')

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

        logger.info(f"[{self.fmt.name}] Built {len(claims)} processed claims")
        return claims

    def _build_reference_points(self) -> List[Dict[str, Any]]:
        """Build reference_points from wherever this format stores them."""
        if self.fmt.references_mode == 'metadata_json':
            ref_json = self.metadata.get(
                self._meta_key('reference_points_json'), '[]'
            )
            try:
                points = json.loads(ref_json)
            except (ValueError, TypeError):
                points = []
            logger.info(f"[{self.fmt.name}] Found {len(points)} reference point(s)")
            return points

        # references_mode == 'table' — read from a point layer
        ref_uri = f"{self.gpkg_path}|layername={self.fmt.references_table}"
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

        logger.info(f"[{self.fmt.name}] Found {len(points)} reference point(s)")
        return points

    def _parse_qsecs_to_plss(self, qsecs: str, meridian: str) -> Dict[str, str]:
        """Parse a QSecs string into a PLSS dict.

        Takes the first quarter-section entry from a string like
        'SE Quarter of SEC 04, T008N, R001E; ...'.
        """
        if not qsecs:
            return {}

        first = qsecs.split(';')[0].strip()
        if not first:
            return {}

        plss: Dict[str, str] = {}

        import re
        sec_match = re.search(r'SEC\s+(\d+)', first, re.IGNORECASE)
        if sec_match:
            plss['section'] = sec_match.group(1).lstrip('0') or '0'

        twp_match = re.search(r'(T\d+[NS])', first, re.IGNORECASE)
        if twp_match:
            plss['township'] = twp_match.group(1)

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
