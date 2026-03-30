# -*- coding: utf-8 -*-
"""
Claims Map Generator

Generates QGIS print layouts for claims field maps and filing maps
from bundled composer templates. Integrates with the claims wizard
to auto-populate labels, control layer visibility, and set proper scale.

Map types:
- Field Map: Waypoints + claims + topo, legible scale for navigation
- Filing Map: Claims + PLSS + topo, no waypoints, for county recording
- State Filing Map (AZ/NV): State-specific requirements (scale, bearings, etc.)
"""
import math
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any, TYPE_CHECKING

from qgis.core import (
    Qgis,
    QgsProject,
    QgsRasterLayer,
    QgsVectorLayer,
    QgsMapLayer,
    QgsPrintLayout,
    QgsLayoutItemMap,
    QgsLayoutItemLabel,
    QgsLayoutItemPicture,
    QgsLayoutItemScaleBar,
    QgsLayoutItemLegend,
    QgsLayoutPoint,
    QgsLayoutSize,
    QgsUnitTypes,
    QgsRectangle,
    QgsCoordinateReferenceSystem,
    QgsCoordinateTransform,
    QgsFeature,
    QgsSymbol,
    QgsSingleSymbolRenderer,
    QgsSimpleLineSymbolLayer,
    QgsPalLayerSettings,
    QgsVectorLayerSimpleLabeling,
    QgsTextFormat,
    QgsTextBufferSettings,
    QgsApplication,
    QgsReadWriteContext,
)
from qgis.PyQt.QtCore import Qt, QRectF
from qgis.PyQt.QtGui import QColor, QFont
from qgis.PyQt.QtXml import QDomDocument

if TYPE_CHECKING:
    from ..ui.claims_wizard_state import ClaimsWizardState

logger = logging.getLogger('geodb')

# Meters to feet conversion
METERS_TO_FEET = 3.28084

# "Nice" scale denominators for rounding
NICE_SCALES = [
    1000, 1200, 1500, 2000, 2400, 2500, 3000, 4000, 4800,
    5000, 6000, 8000, 10000, 12000, 15000, 20000, 24000,
    25000, 30000, 50000, 100000,
]

# USGS Topo basemap configuration (matches basemaps_widget.py)
USGS_TOPO_CONFIG = {
    'name': 'USGS Topo',
    'url': 'https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}',
    'zmin': 0,
    'zmax': 16,
}

# PLSS endpoints (matches basemaps_widget.py)
PLSS_ENDPOINTS = {
    'sections': '/services/api/plss/sections/',
    'townships': '/services/api/plss/townships/',
}


class ClaimsMapGenerator:
    """
    Generate QGIS print layouts for claims field maps and filing maps.

    Uses bundled .qpt templates, populates labels from wizard state,
    locks layer visibility per layout, and handles state-specific
    requirements for AZ and NV.
    """

    def __init__(self, state: 'ClaimsWizardState'):
        self.state = state
        self._plugin_dir = Path(__file__).resolve().parent.parent
        self._templates_dir = self._plugin_dir / 'resources' / 'templates'
        self._logo_path = self._plugin_dir / 'resources' / 'images' / 'logo.png'

    # =========================================================================
    # PUBLIC API
    # =========================================================================

    def generate_all_maps(self) -> Dict[str, str]:
        """
        Generate all applicable print layout maps.

        Returns:
            Dict mapping map type to layout name, e.g.:
            {
                'field_map': 'CM Lode Claims - Field Map',
                'filing_map': 'CM Lode Claims - Filing Map',
                'state_filing_map': 'CM Lode Claims - AZ Filing Map',
            }
        """
        if not self.state.processed_claims:
            raise ValueError("No processed claims available. Complete Step 6 first.")

        # Ensure required basemaps exist in the project
        self._ensure_basemaps()

        # Collect all relevant layers by role
        layers = self._collect_layers()

        # Validate minimum required layers
        if not layers.get('claims'):
            raise ValueError(
                "Lode Claims layer not found. "
                "Make sure claims have been processed in Step 5."
            )

        results = {}

        # Calculate claims extent (shared across all maps)
        extent = self._calculate_extent(layers['claims'])

        # 1. Field Map (auto-detect orientation)
        results['field_map'] = self._create_field_map(layers, extent)

        # 2. Filing Map
        results['filing_map'] = self._create_filing_map(layers, extent)

        # 3. State Filing Map (AZ or NV only)
        state_code = self._get_claims_state()
        if state_code in ('AZ', 'NV'):
            results['state_filing_map'] = self._create_state_filing_map(
                state_code, layers, extent
            )

        return results

    # =========================================================================
    # MAP CREATION
    # =========================================================================

    def _create_field_map(
        self, layers: Dict[str, Optional[QgsMapLayer]], extent: QgsRectangle
    ) -> str:
        """Create a field map with waypoints, claims, topo."""
        prefix = self.state.grid_name_prefix or "Claims"

        # Auto-detect orientation: landscape if wider than tall
        if extent.width() > extent.height():
            template_file = 'field_map_landscape.qpt'
        else:
            template_file = 'field_map_portrait.qpt'

        layout_name = self._get_unique_layout_name(
            f"{prefix} Lode Claims - Field Map"
        )
        layout = self._load_template(template_file, layout_name)

        # Build layer list: field map shows everything including waypoints
        map_layers = self._build_layer_list(layers, include_waypoints=True)

        # Configure map item
        map_item = self._find_map_item(layout)
        self._configure_map_item(map_item, map_layers, extent)

        # Populate labels
        label_values = {
            'Client': self.state.claimant_name or 'Client',
            'Map Name': f"{prefix} Lode Claims - Field Map",
            'Project Name': f"{prefix} Lode Claims",
            'County Name': self._get_county_state(),
        }
        crs_label = self._get_crs_label()
        if crs_label:
            label_values['CRS: NAD83 / UTM Zone 11N'] = f"CRS: {crs_label}"

        self._populate_labels(layout, label_values)
        self._fix_logo_paths(layout)

        # Register layout
        QgsProject.instance().layoutManager().addLayout(layout)
        logger.info(f"[CLAIMS MAP] Created field map: {layout_name}")
        return layout_name

    def _create_filing_map(
        self, layers: Dict[str, Optional[QgsMapLayer]], extent: QgsRectangle
    ) -> str:
        """Create a filing map without waypoints, with PLSS."""
        prefix = self.state.grid_name_prefix or "Claims"

        # Auto-detect orientation
        if extent.width() > extent.height():
            template_file = 'filing_map_landscape.qpt'
        else:
            template_file = 'filing_map_portrait.qpt'

        layout_name = self._get_unique_layout_name(
            f"{prefix} Lode Claims - Filing Map"
        )
        layout = self._load_template(template_file, layout_name)

        # Filing map: no waypoints, no centerlines
        map_layers = self._build_layer_list(
            layers, include_waypoints=False, include_centerlines=False
        )

        map_item = self._find_map_item(layout)
        self._configure_map_item(map_item, map_layers, extent)

        label_values = {
            'Client': self.state.claimant_name or 'Client',
            'Map Name': f"{prefix} Lode Claims - Filing Map",
            'Project Name': f"{prefix} Lode Claims",
            'County Name': self._get_county_state(),
        }
        crs_label = self._get_crs_label()
        if crs_label:
            label_values['CRS: NAD83 / UTM Zone 11N'] = f"CRS: {crs_label}"

        self._populate_labels(layout, label_values)
        self._fix_logo_paths(layout)

        QgsProject.instance().layoutManager().addLayout(layout)
        logger.info(f"[CLAIMS MAP] Created filing map: {layout_name}")
        return layout_name

    def _create_state_filing_map(
        self,
        state_code: str,
        layers: Dict[str, Optional[QgsMapLayer]],
        extent: QgsRectangle,
    ) -> str:
        """Create a state-specific filing map for AZ or NV."""
        if state_code == 'AZ':
            return self._create_az_filing_map(layers, extent)
        elif state_code == 'NV':
            return self._create_nv_filing_map(layers, extent)
        else:
            raise ValueError(f"No state-specific template for {state_code}")

    def _create_az_filing_map(
        self, layers: Dict[str, Optional[QgsMapLayer]], extent: QgsRectangle
    ) -> str:
        """
        Create Arizona state filing map.

        AZ requirements (ARS 27-203):
        - Scale <= 1":2000' (1:24,000)
        - Claim name, location date, Lode/Placer type
        - North arrow, PLSS description
        - Monument types, bearing/distance between corners
        - Tie to survey monument
        """
        prefix = self.state.grid_name_prefix or "Claims"
        layout_name = self._get_unique_layout_name(
            f"{prefix} Lode Claims - AZ Filing Map"
        )
        layout = self._load_template('az_state_filing_map.qpt', layout_name)

        # AZ filing map: include monuments + endline monuments, no waypoints
        map_layers = self._build_layer_list(
            layers,
            include_waypoints=False,
            include_centerlines=False,
            include_monuments=True,
            include_endline_monuments=True,
        )

        map_item = self._find_map_item(layout)

        # AZ max scale: 1:24,000
        self._configure_map_item(
            map_item, map_layers, extent, max_scale=24000
        )

        actual_scale = map_item.scale()
        scale_ft = int(round(actual_scale / 12))  # Convert to feet per inch
        scale_text = f'scale 1":{scale_ft:,}\' (1:{int(actual_scale):,})'

        county_state = self._get_county_state()

        label_values = {
            'Gold Express Mines, Inc': self.state.claimant_name or 'Client',
            'Copper Butte LODE claims': f"{prefix} LODE Claims",
            'Pinal County, AZ': county_state,
            'County Filing Map': 'County Filing Map',
        }

        # Scale text
        label_values['scale 1":2000\' (1:24,000)'] = scale_text

        # CRS
        crs_label = self._get_crs_label()
        if crs_label:
            label_values['CRS: NAD83 / UTM Zone 11N'] = f"CRS: {crs_label}"

        # Bearings and distances (auto-generated from corners)
        bearings_text = self._generate_az_bearings_text()
        if bearings_text:
            self._populate_labels_startswith(layout, 'Bearings and distances', bearings_text)

        # Monument description
        monument_text = self._generate_monument_text()
        if monument_text:
            self._populate_labels_startswith(layout, 'Corners are all', monument_text)

        # Reference point / PLSS tie
        reference_text = self._generate_reference_text()
        if reference_text:
            self._populate_labels_startswith(layout, 'Reference:', reference_text)

        self._populate_labels(layout, label_values)
        self._fix_logo_paths(layout)

        QgsProject.instance().layoutManager().addLayout(layout)
        logger.info(f"[CLAIMS MAP] Created AZ filing map: {layout_name}")
        return layout_name

    def _create_nv_filing_map(
        self, layers: Dict[str, Optional[QgsMapLayer]], extent: QgsRectangle
    ) -> str:
        """
        Create Nevada state filing map.

        NV requirements (NRS 517.040):
        - Scale >= 500 ft/inch (~1:6,000)
        - Size 8.5"x14" or 24"x36" (using ARCH D 24x36)
        - Monument positions/numbers
        - Courses/distances to public land survey corner
        - Township/range, quarter section/section
        """
        prefix = self.state.grid_name_prefix or "Claims"
        layout_name = self._get_unique_layout_name(
            f"{prefix} Lode Claims - NV Filing Map"
        )
        layout = self._load_template('nv_state_filing_map.qpt', layout_name)

        # NV filing map: claims + corners + PLSS, no waypoints
        map_layers = self._build_layer_list(
            layers,
            include_waypoints=False,
            include_centerlines=False,
            include_monuments=False,
        )

        map_item = self._find_map_item(layout)

        # NV fixed scale: 1:6,000
        self._configure_map_item(map_item, map_layers, extent, fixed_scale=6000)

        county_state = self._get_county_state()
        county = self._get_county()
        state_name = 'Nevada'

        # Title line: "PREFIX Lode Claims, Claimant, County, Nevada"
        title_text = (
            f"{prefix} Lode Claims, {self.state.claimant_name or 'Claimant'}, "
            f"{county}, {state_name}"
        )

        # Reference text
        nv_ref = self._generate_nv_reference_text()

        label_values = {}

        # Match the NV template labels by their content
        self._populate_labels_startswith(
            layout, 'BC Lode Claims', title_text
        )
        self._populate_labels_startswith(
            layout, 'Each claim is', 'Each claim is 1,500\' x 600\''
        )
        if nv_ref:
            self._populate_labels_contains(layout, 'feet west', nv_ref)

        self._populate_labels(layout, label_values)
        self._fix_logo_paths(layout)

        QgsProject.instance().layoutManager().addLayout(layout)
        logger.info(f"[CLAIMS MAP] Created NV filing map: {layout_name}")
        return layout_name

    # =========================================================================
    # BASEMAP MANAGEMENT
    # =========================================================================

    def _ensure_basemaps(self):
        """Ensure USGS Topo and PLSS layers exist in the project."""
        project = QgsProject.instance()

        # USGS Topo
        if not project.mapLayersByName('USGS Topo'):
            self._add_usgs_topo()

        # PLSS - use claims extent for bbox
        claims_layer = self._find_claims_layer()
        if claims_layer:
            claims_extent = claims_layer.extent()
            claims_crs = claims_layer.crs()

            if not project.mapLayersByName('PLSS Sections'):
                self._add_plss_layer('sections', claims_extent, claims_crs)

            if not project.mapLayersByName('PLSS Townships'):
                self._add_plss_layer('townships', claims_extent, claims_crs)

    def _add_usgs_topo(self):
        """Add USGS Topo XYZ tile layer to the project."""
        try:
            url = USGS_TOPO_CONFIG['url']
            zmin = USGS_TOPO_CONFIG['zmin']
            zmax = USGS_TOPO_CONFIG['zmax']

            uri = f"type=xyz&url={url}&zmin={zmin}&zmax={zmax}"
            layer = QgsRasterLayer(uri, 'USGS Topo', 'wms')

            if not layer.isValid():
                logger.warning("[CLAIMS MAP] Failed to create USGS Topo layer")
                return

            QgsProject.instance().addMapLayer(layer, False)

            # Add to Base Layers group
            root = QgsProject.instance().layerTreeRoot()
            base_group = root.findGroup("Base Layers")
            if not base_group:
                base_group = root.addGroup("Base Layers")
                clone = base_group.clone()
                root.insertChildNode(-1, clone)
                root.removeChildNode(base_group)
                base_group = root.findGroup("Base Layers")

            base_group.addLayer(layer)
            logger.info("[CLAIMS MAP] Added USGS Topo basemap")

        except Exception as e:
            logger.warning(f"[CLAIMS MAP] Could not add USGS Topo: {e}")

    def _add_plss_layer(
        self,
        layer_type: str,
        claims_extent: QgsRectangle,
        claims_crs: QgsCoordinateReferenceSystem,
    ):
        """
        Add a PLSS layer from geodb.io for the claims area.

        Uses the claims extent (not canvas extent) to load the correct area.
        """
        try:
            from ..utils.crs_utils import extent_to_wgs84
            from ..utils.config import Config

            # Add buffer to claims extent (50% to show surrounding context)
            buffered = QgsRectangle(claims_extent)
            dx = buffered.width() * 0.5
            dy = buffered.height() * 0.5
            buffered.setXMinimum(buffered.xMinimum() - dx)
            buffered.setXMaximum(buffered.xMaximum() + dx)
            buffered.setYMinimum(buffered.yMinimum() - dy)
            buffered.setYMaximum(buffered.yMaximum() + dy)

            # Transform to WGS84 for API
            wgs84_extent = extent_to_wgs84(buffered, claims_crs)
            if wgs84_extent is None:
                logger.warning(
                    f"[CLAIMS MAP] Could not transform extent to WGS84 for PLSS {layer_type}"
                )
                return

            bbox = (
                f"{wgs84_extent.xMinimum()},{wgs84_extent.yMinimum()},"
                f"{wgs84_extent.xMaximum()},{wgs84_extent.yMaximum()}"
            )

            # Use config for base URL (respects local dev mode)
            config = Config()
            base_url = config.services_base_url.rstrip('/')
            # services_base_url already ends with /services/api
            # but PLSS endpoints start with /services/api/
            # So we need to reconstruct properly
            if config.get('api.use_local', False):
                api_base = "http://localhost:8000"
            else:
                api_base = "https://geodb.io"

            endpoint = PLSS_ENDPOINTS[layer_type]
            url = f"{api_base}{endpoint}?bbox={bbox}&simplified=true"

            layer_name = "PLSS Sections" if layer_type == 'sections' else "PLSS Townships"

            # Load via OGR
            temp_layer = QgsVectorLayer(url, f"temp_{layer_name}", 'ogr')
            QgsApplication.processEvents()

            if not temp_layer.isValid():
                logger.warning(
                    f"[CLAIMS MAP] Could not load PLSS {layer_type} from geodb.io"
                )
                return

            # Copy to memory layer for performance
            layer = self._ogr_to_memory_layer(temp_layer, layer_name)
            if not layer:
                layer = temp_layer  # Fallback

            # Apply styling
            self._apply_plss_style(layer, layer_type)
            self._apply_plss_labels(layer, layer_type)

            # Add to project in Base Layers group
            QgsProject.instance().addMapLayer(layer, False)

            root = QgsProject.instance().layerTreeRoot()
            base_group = root.findGroup("Base Layers")
            if not base_group:
                base_group = root.addGroup("Base Layers")
                clone = base_group.clone()
                root.insertChildNode(-1, clone)
                root.removeChildNode(base_group)
                base_group = root.findGroup("Base Layers")

            base_group.addLayer(layer)
            logger.info(
                f"[CLAIMS MAP] Added PLSS {layer_type} with "
                f"{layer.featureCount()} features"
            )

        except Exception as e:
            logger.warning(f"[CLAIMS MAP] Could not add PLSS {layer_type}: {e}")

    def _ogr_to_memory_layer(
        self, source_layer: QgsVectorLayer, layer_name: str
    ) -> Optional[QgsVectorLayer]:
        """Copy an OGR layer to an in-memory layer with spatial index."""
        try:
            geom_type = source_layer.geometryType()
            geom_map = {0: "Point", 1: "LineString", 2: "Polygon"}
            geom_str = geom_map.get(geom_type, "Polygon")

            fields = source_layer.fields()
            field_defs = []
            for field in fields:
                ft = field.typeName().lower()
                if 'int' in ft:
                    field_defs.append(f"field={field.name()}:integer")
                elif any(t in ft for t in ('real', 'double', 'float')):
                    field_defs.append(f"field={field.name()}:double")
                else:
                    field_defs.append(f"field={field.name()}:string")

            mem_uri = f"{geom_str}?crs=EPSG:4326&{'&'.join(field_defs)}"
            mem_layer = QgsVectorLayer(mem_uri, layer_name, 'memory')

            if not mem_layer.isValid():
                return None

            mem_layer.startEditing()
            features = []
            for feat in source_layer.getFeatures():
                new_feat = QgsFeature(mem_layer.fields())
                new_feat.setGeometry(feat.geometry())
                for field in fields:
                    try:
                        new_feat.setAttribute(field.name(), feat.attribute(field.name()))
                    except Exception:
                        pass
                features.append(new_feat)

            mem_layer.addFeatures(features)
            mem_layer.commitChanges()
            mem_layer.dataProvider().createSpatialIndex()
            return mem_layer

        except Exception as e:
            logger.warning(f"[CLAIMS MAP] Memory layer copy failed: {e}")
            return None

    def _apply_plss_style(self, layer: QgsVectorLayer, layer_type: str):
        """Apply black line styling to PLSS layer."""
        symbol = QgsSymbol.defaultSymbol(layer.geometryType())
        symbol.deleteSymbolLayer(0)

        line_layer = QgsSimpleLineSymbolLayer()
        line_layer.setColor(QColor('#000000'))
        line_layer.setWidth(1.5)

        if layer_type == 'townships':
            line_layer.setPenStyle(Qt.PenStyle.DashLine)

        symbol.appendSymbolLayer(line_layer)
        renderer = QgsSingleSymbolRenderer(symbol)
        layer.setRenderer(renderer)

    def _apply_plss_labels(self, layer: QgsVectorLayer, layer_type: str):
        """Apply labeling to PLSS layer."""
        label_settings = QgsPalLayerSettings()
        label_settings.placement = Qgis.LabelPlacement.OverPoint

        if layer_type == 'sections':
            label_settings.fieldName = (
                "'Sec ' || \"section\" || '\\n' || \"township\" || '\\n' || \"range\""
            )
            label_settings.isExpression = True
            label_settings.scaleVisibility = True
            label_settings.minimumScale = 100000
            label_settings.maximumScale = 0
        else:
            label_settings.fieldName = "\"township\" || ' ' || \"range\""
            label_settings.isExpression = True
            label_settings.scaleVisibility = True
            label_settings.minimumScale = 500000
            label_settings.maximumScale = 0

        text_format = QgsTextFormat()
        font = QFont("Arial", 9)
        font.setBold(True)
        text_format.setFont(font)
        text_format.setColor(QColor('#000000'))

        buffer_settings = QgsTextBufferSettings()
        buffer_settings.setEnabled(True)
        buffer_settings.setSize(1.5)
        buffer_settings.setColor(QColor('#FFFFFF'))
        text_format.setBuffer(buffer_settings)

        label_settings.setFormat(text_format)
        labeling = QgsVectorLayerSimpleLabeling(label_settings)
        layer.setLabeling(labeling)
        layer.setLabelsEnabled(True)

    # =========================================================================
    # TEMPLATE LOADING
    # =========================================================================

    def _load_template(self, template_filename: str, layout_name: str) -> QgsPrintLayout:
        """Load a .qpt template file and return a QgsPrintLayout."""
        template_path = self._templates_dir / template_filename
        if not template_path.exists():
            raise FileNotFoundError(
                f"Template not found: {template_path}\n"
                "Ensure the plugin's resources/templates/ directory is intact."
            )

        project = QgsProject.instance()
        layout = QgsPrintLayout(project)
        layout.initializeDefaults()

        # Read template XML
        with open(template_path, 'r', encoding='utf-8') as f:
            template_content = f.read()

        doc = QDomDocument()
        doc.setContent(template_content)

        context = QgsReadWriteContext()
        items, ok = layout.loadFromTemplate(doc, context)
        if not ok:
            raise RuntimeError(
                f"Failed to load template: {template_filename}"
            )

        layout.setName(layout_name)
        return layout

    def _fix_logo_paths(self, layout: QgsPrintLayout):
        """Fix logo image paths in layout to point to bundled logo."""
        logo_path = str(self._logo_path).replace('\\', '/')

        for item in layout.items():
            if isinstance(item, QgsLayoutItemPicture):
                current_path = item.picturePath()
                # Skip north arrows
                if 'arrow' in current_path.lower() or 'north' in current_path.lower():
                    continue
                # Replace any other image (logo) with bundled logo
                if current_path and self._logo_path.exists():
                    item.setPicturePath(logo_path)

    # =========================================================================
    # MAP ITEM CONFIGURATION
    # =========================================================================

    def _find_map_item(self, layout: QgsPrintLayout) -> QgsLayoutItemMap:
        """Find the map item in a layout."""
        for item in layout.items():
            if isinstance(item, QgsLayoutItemMap):
                return item
        raise ValueError("No map item found in template layout")

    def _configure_map_item(
        self,
        map_item: QgsLayoutItemMap,
        layer_list: List[QgsMapLayer],
        extent: QgsRectangle,
        max_scale: Optional[float] = None,
        min_scale: Optional[float] = None,
        fixed_scale: Optional[float] = None,
    ):
        """
        Configure a map item with locked layers, extent, and scale.

        Args:
            map_item: The layout map item to configure
            layer_list: Layers to show in this map
            extent: Claims extent (with buffer)
            max_scale: Maximum scale denominator (e.g., 24000 for AZ)
            min_scale: Minimum scale denominator
            fixed_scale: Exact scale to use (overrides auto-calculation)
        """
        # Lock layers for this specific map item
        map_item.setKeepLayerSet(True)
        map_item.setLayers(layer_list)
        map_item.setKeepLayerStyles(True)

        # Set CRS to match claims layer
        crs = None
        if self.state.project_epsg:
            crs = QgsCoordinateReferenceSystem(f"EPSG:{self.state.project_epsg}")
            map_item.setCrs(crs)

        # Set extent
        map_item.setExtent(extent)

        if fixed_scale:
            map_item.setScale(fixed_scale)
        else:
            # Auto-calculate and round to nice scale
            natural_scale = map_item.scale()
            nice_scale = self._round_to_nice_scale(natural_scale)

            if max_scale and nice_scale > max_scale:
                nice_scale = max_scale
                logger.warning(
                    f"[CLAIMS MAP] Claims extent exceeds max scale 1:{max_scale}. "
                    "Some claims may not be fully visible."
                )
            if min_scale and nice_scale < min_scale:
                nice_scale = min_scale

            map_item.setScale(nice_scale)

        # Update grid CRS and interval to match the map
        self._update_map_grid(map_item, crs)

    def _calculate_extent(
        self, claims_layer: QgsVectorLayer, buffer_pct: float = 0.15
    ) -> QgsRectangle:
        """Calculate bounding extent of claims with buffer."""
        extent = claims_layer.extent()
        dx = extent.width() * buffer_pct
        dy = extent.height() * buffer_pct

        # Ensure minimum buffer for very small extents
        min_buffer = 100  # meters (for UTM)
        dx = max(dx, min_buffer)
        dy = max(dy, min_buffer)

        return QgsRectangle(
            extent.xMinimum() - dx,
            extent.yMinimum() - dy,
            extent.xMaximum() + dx,
            extent.yMaximum() + dy,
        )

    def _round_to_nice_scale(self, scale: float) -> float:
        """Round scale up to the nearest 'nice' denominator."""
        for s in NICE_SCALES:
            if s >= scale:
                return float(s)
        return float(NICE_SCALES[-1])

    def _update_map_grid(
        self,
        map_item: QgsLayoutItemMap,
        crs: Optional[QgsCoordinateReferenceSystem] = None,
    ):
        """
        Update the map grid CRS and interval to match the map item.

        The templates have grids hardcoded to EPSG:26911 with 1500m intervals.
        This updates them to use the project CRS and calculates an appropriate
        grid interval based on the map's visible extent.
        """
        try:
            grids = map_item.grids()
            if grids.size() == 0:
                return

            grid = grids.grid(0)

            # Update grid CRS to match map CRS
            if crs and crs.isValid():
                grid.setCrs(crs)

            # Calculate appropriate grid interval from the map extent
            extent = map_item.extent()
            # Use the smaller dimension to determine interval
            map_span = min(extent.width(), extent.height())

            # Choose interval to get 3-6 grid lines across the map
            # "Nice" intervals in meters for UTM
            nice_intervals = [
                100, 200, 250, 500, 1000, 1500, 2000, 2500,
                5000, 10000, 20000, 50000,
            ]

            target_lines = 4
            target_interval = map_span / target_lines

            chosen_interval = nice_intervals[0]
            for interval in nice_intervals:
                if interval >= target_interval:
                    chosen_interval = interval
                    break

            grid.setIntervalX(chosen_interval)
            grid.setIntervalY(chosen_interval)

            logger.info(
                f"[CLAIMS MAP] Grid updated: CRS={crs.authid() if crs else 'default'}, "
                f"interval={chosen_interval}m"
            )

        except Exception as e:
            logger.warning(f"[CLAIMS MAP] Could not update map grid: {e}")

    # =========================================================================
    # LAYER COLLECTION
    # =========================================================================

    def _collect_layers(self) -> Dict[str, Optional[QgsMapLayer]]:
        """Gather all relevant layers organized by role."""
        project = QgsProject.instance()
        prefix = self.state.grid_name_prefix or ""
        suffix = f" [{prefix} Lode Claims]" if prefix else ""

        def find_layer(base_name: str) -> Optional[QgsMapLayer]:
            # Try with project suffix first, then without
            for name in [f"{base_name}{suffix}", base_name]:
                layers = project.mapLayersByName(name)
                if layers:
                    return layers[0]
            return None

        return {
            'topo': find_layer('USGS Topo'),
            'claims': find_layer('Lode Claims'),
            'corners': find_layer('Corner Points'),
            'lm_corners': find_layer('LM Corners'),
            'centerlines': find_layer('Center Lines'),
            'monuments': find_layer('Monuments'),
            'waypoints': find_layer('Claims Waypoints'),
            'plss_sections': find_layer('PLSS Sections'),
            'plss_townships': find_layer('PLSS Townships'),
            'sideline_monuments': find_layer('Sideline Monuments'),
            'endline_monuments': find_layer('Endline Monuments'),
        }

    def _find_claims_layer(self) -> Optional[QgsVectorLayer]:
        """Find the claims layer by name or ID."""
        # Try by ID first
        if self.state.claims_layer_id:
            layer = QgsProject.instance().mapLayer(self.state.claims_layer_id)
            if isinstance(layer, QgsVectorLayer) and layer.isValid():
                return layer

        # Try by name
        layers = self._collect_layers()
        claims = layers.get('claims')
        if isinstance(claims, QgsVectorLayer):
            return claims
        return None

    def _build_layer_list(
        self,
        layers: Dict[str, Optional[QgsMapLayer]],
        include_waypoints: bool = True,
        include_centerlines: bool = True,
        include_monuments: bool = True,
        include_endline_monuments: bool = False,
        include_sideline_monuments: bool = False,
    ) -> List[QgsMapLayer]:
        """
        Build ordered layer list for a map item.

        Layer order (top to bottom):
        1. Waypoints (if included)
        2. Monuments
        3. Corner Points / LM Corners
        4. Center Lines
        5. Claims polygons
        6. PLSS Sections
        7. PLSS Townships
        8. USGS Topo (bottom)
        """
        result = []

        if include_waypoints and layers.get('waypoints'):
            result.append(layers['waypoints'])

        if include_monuments and layers.get('monuments'):
            result.append(layers['monuments'])

        if include_endline_monuments and layers.get('endline_monuments'):
            result.append(layers['endline_monuments'])

        if include_sideline_monuments and layers.get('sideline_monuments'):
            result.append(layers['sideline_monuments'])

        if layers.get('lm_corners'):
            result.append(layers['lm_corners'])

        if layers.get('corners'):
            result.append(layers['corners'])

        if include_centerlines and layers.get('centerlines'):
            result.append(layers['centerlines'])

        if layers.get('claims'):
            result.append(layers['claims'])

        if layers.get('plss_sections'):
            result.append(layers['plss_sections'])

        if layers.get('plss_townships'):
            result.append(layers['plss_townships'])

        if layers.get('topo'):
            result.append(layers['topo'])

        return result

    # =========================================================================
    # LABEL POPULATION
    # =========================================================================

    def _populate_labels(self, layout: QgsPrintLayout, label_values: Dict[str, str]):
        """
        Populate label items by matching their current text exactly.

        Args:
            layout: The print layout
            label_values: Dict mapping current label text -> new text
        """
        for item in layout.items():
            if not isinstance(item, QgsLayoutItemLabel):
                continue
            current_text = item.text().strip()
            for match_text, new_text in label_values.items():
                if current_text == match_text.strip():
                    item.setText(new_text)
                    break

    def _populate_labels_startswith(
        self, layout: QgsPrintLayout, prefix: str, new_text: str
    ):
        """Replace label text where current text starts with the given prefix."""
        for item in layout.items():
            if not isinstance(item, QgsLayoutItemLabel):
                continue
            if item.text().strip().startswith(prefix):
                item.setText(new_text)
                return  # Only match first occurrence

    def _populate_labels_contains(
        self, layout: QgsPrintLayout, substring: str, new_text: str
    ):
        """Replace label text where current text contains the given substring."""
        for item in layout.items():
            if not isinstance(item, QgsLayoutItemLabel):
                continue
            if substring in item.text():
                item.setText(new_text)
                return

    # =========================================================================
    # DATA EXTRACTION HELPERS
    # =========================================================================

    def _get_claims_state(self) -> str:
        """Get state code from first processed claim."""
        if not self.state.processed_claims:
            return ''
        return self.state.processed_claims[0].get('state', '') or ''

    def _get_county(self) -> str:
        """Get county name from first processed claim."""
        if not self.state.processed_claims:
            return ''
        return self.state.processed_claims[0].get('county', '') or ''

    def _get_county_state(self) -> str:
        """Get 'County, ST' string from first processed claim."""
        county = self._get_county()
        state = self._get_claims_state()
        if county and state:
            return f"{county}, {state}"
        return county or state or 'County'

    def _get_crs_label(self) -> str:
        """Get human-readable CRS label from project EPSG."""
        if not self.state.project_epsg:
            return ''
        try:
            crs = QgsCoordinateReferenceSystem(f"EPSG:{self.state.project_epsg}")
            if crs.isValid():
                return crs.description()
        except Exception:
            pass
        return f"EPSG:{self.state.project_epsg}"

    # =========================================================================
    # STATE-SPECIFIC TEXT GENERATION
    # =========================================================================

    def _generate_az_bearings_text(self) -> str:
        """
        Generate bearings and distances text for AZ filing map.

        Produces text like:
        "CM 1: From corner 1, go 1500' west to corner 2, then 600' north
        to corner 3, then 1500' east to corner 4, then 600' south back
        to corner 1."
        """
        claims = self.state.processed_claims
        if not claims:
            return ""

        # Group claims by their geometry pattern to avoid repetition
        lines = []
        pattern_groups = {}  # pattern_key -> list of claim names

        for claim in claims:
            corners = claim.get('corners', [])
            if len(corners) < 4:
                continue

            # Calculate segments
            segments = []
            for i in range(4):
                c1 = corners[i]
                c2 = corners[(i + 1) % 4]

                e1 = c1.get('easting', 0)
                n1 = c1.get('northing', 0)
                e2 = c2.get('easting', 0)
                n2 = c2.get('northing', 0)

                distance_ft = self._calc_distance_ft(e1, n1, e2, n2)
                cardinal = self._bearing_to_cardinal(
                    self._calc_bearing(e1, n1, e2, n2)
                )
                segments.append((int(round(distance_ft)), cardinal))

            # Create pattern key for grouping identical geometries
            pattern_key = tuple(segments)
            name = claim.get('name', 'Unknown')

            if pattern_key not in pattern_groups:
                pattern_groups[pattern_key] = {
                    'names': [],
                    'segments': segments,
                    'corners': corners,
                }
            pattern_groups[pattern_key]['names'].append(name)

        for pattern_key, group in pattern_groups.items():
            segments = group['segments']
            names = group['names']

            # Determine which corner is corner 1 (by cardinal direction)
            c1_direction = segments[0][1]  # Direction from C1 to C2

            # Build claim name list
            if len(names) <= 3:
                name_str = ', '.join(names)
            else:
                name_str = f"{names[0]} to {names[-1]}"

            # Build description
            desc = (
                f"{name_str}: From corner 1, go {segments[0][0]:,}' {segments[0][1]} "
                f"to corner 2, then {segments[1][0]:,}' {segments[1][1]} to corner 3, "
                f"then {segments[2][0]:,}' {segments[2][1]} to corner 4, then "
                f"{segments[3][0]:,}' {segments[3][1]} back to corner 1."
            )
            lines.append(desc)

        header = "Bearings and distances between corners:\n\n"
        return header + "\n\n".join(lines)

    def _generate_monument_text(self) -> str:
        """Generate monument type description from wizard state."""
        monument = self.state.monument_type or "2' wooden post"
        return (
            f"Corners are all {monument} marked with distance and direction "
            f"to adjacent corners. Location Monuments are {monument} marked "
            f"with signed location notice."
        )

    def _generate_reference_text(self) -> str:
        """Generate PLSS reference/tie text from reference points."""
        if not self.state.reference_points:
            return "Reference: [no reference point specified - add in Step 3]"

        ref = self.state.reference_points[0]
        ref_name = ref.get('name', 'survey monument')

        # Try to get PLSS info from first claim
        plss_parts = []
        if self.state.processed_claims:
            plss = self.state.processed_claims[0].get('plss') or {}
            if isinstance(plss, str):
                plss_parts.append(plss)
            else:
                section = plss.get('section', '')
                township = plss.get('township', '')
                range_val = plss.get('range', '')
                if section:
                    plss_parts.append(f"Section {section}")
                if township:
                    plss_parts.append(township)
                if range_val:
                    plss_parts.append(range_val)

        plss_str = ', '.join(plss_parts) if plss_parts else ''
        if plss_str:
            return f"Reference: the permanent survey monument located at the {ref_name}, {plss_str}"
        return f"Reference: the permanent survey monument located at the {ref_name}"

    def _generate_nv_reference_text(self) -> str:
        """
        Generate NV reference text showing distance/direction
        from claim corner to reference point.
        """
        if not self.state.reference_points or not self.state.processed_claims:
            return ""

        ref = self.state.reference_points[0]
        ref_easting = ref.get('easting', 0)
        ref_northing = ref.get('northing', 0)

        if not ref_easting and not ref_northing:
            return ""

        # Use corner 1 of first claim
        first_claim = self.state.processed_claims[0]
        corners = first_claim.get('corners', [])
        if not corners:
            return ""

        corner = corners[0]
        corner_e = corner.get('easting', 0)
        corner_n = corner.get('northing', 0)

        de = corner_e - ref_easting
        dn = corner_n - ref_northing

        ew_dir = "east" if de > 0 else "west"
        ns_dir = "north" if dn > 0 else "south"

        ew_ft = abs(de) * METERS_TO_FEET
        ns_ft = abs(dn) * METERS_TO_FEET

        ref_name = ref.get('name', 'the permanent survey monument')

        return (
            f"{ew_ft:,.0f} feet {ew_dir}, and {ns_ft:,.0f} feet {ns_dir} of "
            f"{ref_name}"
        )

    # =========================================================================
    # GEOMETRY HELPERS
    # =========================================================================

    def _calc_bearing(self, e1: float, n1: float, e2: float, n2: float) -> float:
        """Calculate bearing in degrees from north (0-360)."""
        de = e2 - e1
        dn = n2 - n1
        bearing = math.degrees(math.atan2(de, dn))
        if bearing < 0:
            bearing += 360
        return bearing

    def _calc_distance_ft(
        self, e1: float, n1: float, e2: float, n2: float
    ) -> float:
        """Calculate distance in feet between two UTM points."""
        de = e2 - e1
        dn = n2 - n1
        dist_m = math.sqrt(de * de + dn * dn)
        return dist_m * METERS_TO_FEET

    def _bearing_to_cardinal(self, bearing: float) -> str:
        """
        Convert bearing (degrees from north) to cardinal direction string.

        Uses 8-point compass: N, NE, E, SE, S, SW, W, NW.
        """
        # Normalize to 0-360
        bearing = bearing % 360

        directions = [
            (22.5, 'north'),
            (67.5, 'northeast'),
            (112.5, 'east'),
            (157.5, 'southeast'),
            (202.5, 'south'),
            (247.5, 'southwest'),
            (292.5, 'west'),
            (337.5, 'northwest'),
            (360.1, 'north'),  # Wrap around
        ]

        for threshold, direction in directions:
            if bearing < threshold:
                return direction

        return 'north'

    # =========================================================================
    # LAYOUT MANAGEMENT HELPERS
    # =========================================================================

    def _get_unique_layout_name(self, base_name: str) -> str:
        """
        Get a unique layout name, removing existing layout if present.

        This ensures re-generating maps replaces the old ones.
        """
        manager = QgsProject.instance().layoutManager()
        existing = manager.layoutByName(base_name)
        if existing:
            manager.removeLayout(existing)
        return base_name
