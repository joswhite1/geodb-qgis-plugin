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
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsSymbol,
    QgsSingleSymbolRenderer,
    QgsSimpleLineSymbolLayer,
    QgsSimpleMarkerSymbolLayer,
    QgsPalLayerSettings,
    QgsVectorLayerSimpleLabeling,
    QgsTextFormat,
    QgsTextBufferSettings,
    QgsApplication,
    QgsReadWriteContext,
    QgsFeatureRequest,
    QgsProperty,
)
from qgis.PyQt.QtCore import Qt
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

        # Calculate extents
        # Field map: just claims
        field_extent = self._calculate_extent(layers['claims'])
        # Filing maps: include reference point so tie line is visible
        filing_extent = self._calculate_extent(
            layers['claims'], include_reference=True
        )

        # 1. Field Map (auto-detect orientation)
        results['field_map'] = self._create_field_map(layers, field_extent)

        # 2. Filing Map
        results['filing_map'] = self._create_filing_map(layers, filing_extent)

        # 3. State Filing Map (AZ or NV only)
        state_code = self._get_claims_state()
        if state_code == 'AZ':
            results['state_filing_map'] = self._create_state_filing_map(
                state_code, layers, filing_extent
            )
        elif state_code == 'NV':
            # NV requires TWO copies; generate both size options
            # 24"x36" (ARCH D landscape) for mylar prints
            results['state_filing_map'] = self._create_state_filing_map(
                state_code, layers, filing_extent
            )
            # 8.5"x14" (legal) for photocopy-quality filing
            results['state_filing_map_legal'] = self._create_nv_filing_map(
                layers, filing_extent, template='legal'
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
        # Use the filtered waypoints layer: corner/witness names labeled,
        # LM/monument symbols shown without labels (LM name = claim name)
        map_layers = self._build_layer_list(
            layers,
            include_waypoints=True,
            use_waypoints_corners_only=True,
            include_dimensions=True,
        )

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
        self._configure_scale_bars(layout, use_feet=True)
        self._configure_legend(layout, map_layers)

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
        # Includes: corner labels, dimensions, tie line, reference point, monuments
        map_layers = self._build_layer_list(
            layers,
            include_waypoints=False,
            include_centerlines=False,
            include_corners=False,
            include_dimensions=True,
            include_tie_line=True,
            include_corner_labels=True,
            include_ref_point=True,
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
        self._configure_scale_bars(layout, use_feet=True)
        self._remove_legend(layout)

        # Add filing-specific text labels programmatically
        self._add_filing_text_labels(layout, map_item)

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
        # Include corner labels, dimensions, tie line, reference point
        map_layers = self._build_layer_list(
            layers,
            include_waypoints=False,
            include_centerlines=False,
            include_monuments=True,
            include_endline_monuments=True,
            include_corners=False,
            include_dimensions=True,
            include_tie_line=True,
            include_corner_labels=True,
            include_ref_point=True,
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
        self._remove_legend(layout)

        # AZ keeps corner labels on the main map (bearings text references
        # them by number) so no need for an inset diagram.

        QgsProject.instance().layoutManager().addLayout(layout)
        logger.info(f"[CLAIMS MAP] Created AZ filing map: {layout_name}")
        return layout_name

    def _create_nv_filing_map(
        self,
        layers: Dict[str, Optional[QgsMapLayer]],
        extent: QgsRectangle,
        template: str = 'large',
    ) -> str:
        """
        Create Nevada state filing map.

        NV requirements (NRS 517.040):
        - Scale >= 500 ft/inch (~1:6,000)
        - Size 8.5"x14" or 24"x36" landscape (ARCH D)
        - Monument positions/numbers
        - Courses/distances to public land survey corner
        - Township/range, quarter section/section

        Uses an inset diagram for corner numbering instead of labeling
        every corner on the main map (all claims have the same layout).

        Args:
            template: 'large' for 36"x24" ARCH D, 'legal' for 8.5"x14"
        """
        prefix = self.state.grid_name_prefix or "Claims"
        size_label = "Legal" if template == 'legal' else "36x24"
        layout_name = self._get_unique_layout_name(
            f"{prefix} Lode Claims - NV Filing Map ({size_label})"
        )

        template_file = (
            'nv_state_filing_map_legal.qpt' if template == 'legal'
            else 'nv_state_filing_map.qpt'
        )
        layout = self._load_template(template_file, layout_name)

        # NV filing map: claims + PLSS, no waypoints, no individual corner labels
        # Corner numbering is shown via an inset diagram instead
        map_layers = self._build_layer_list(
            layers,
            include_waypoints=False,
            include_centerlines=False,
            include_monuments=True,
            include_dimensions=True,
            include_tie_line=True,
            include_corner_labels=False,
            include_ref_point=True,
        )

        map_item = self._find_map_item(layout)

        # NV fixed scale: 1:6,000
        self._configure_map_item(map_item, map_layers, extent, fixed_scale=6000)

        # Add a coordinate grid to the NV map (template lacks one)
        self._add_nv_map_grid(map_item)

        county = self._get_county()
        state_name = 'Nevada'

        # Title line: "PREFIX Lode Claims, Claimant, County, Nevada"
        title_text = (
            f"{prefix} Lode Claims\n{self.state.claimant_name or 'Claimant'}\n"
            f"{county}, {state_name}"
        )

        # Reference text using surveyor's notation
        nv_ref = self._generate_nv_reference_text()

        # Match and replace template labels
        self._populate_labels_startswith(layout, 'BC Lode Claims', title_text)
        self._populate_labels_startswith(
            layout, 'Each claim is', 'Each claim is 1,500\' x 600\''
        )

        # Update reference label text (template has rotation=0 now)
        if nv_ref:
            self._update_nv_reference_label(layout, nv_ref)

        # Scale text
        self._populate_labels_startswith(layout, 'Scale:', f'Scale: 1:6,000 (500\'/inch)')

        self._fix_logo_paths(layout)
        self._remove_legend(layout)

        # Add claim inset diagram showing corner numbers and dimensions
        self._add_claim_inset(layout)

        QgsProject.instance().layoutManager().addLayout(layout)
        logger.info(f"[CLAIMS MAP] Created NV filing map: {layout_name}")
        return layout_name

    def _update_nv_reference_label(self, layout: QgsPrintLayout, new_text: str):
        """
        Find and update the reference label in the NV template.

        The template has a label rotated 19 degrees with sample reference
        data. We reset the rotation to 0 and update the text to match
        the current claim's reference point.
        """
        for item in layout.items():
            if not isinstance(item, QgsLayoutItemLabel):
                continue
            text = item.text()
            # Match the old-style reference text (contains "feet west" or
            # "feet east" or "survey monument") or any rotated label
            if ('feet west' in text or 'feet east' in text or
                    'survey monument' in text.lower() or
                    'corner of Sec' in text):
                item.setText(new_text)
                item.setItemRotation(0)  # Reset rotation
                return

        # If no existing label matched, the template may have changed.
        # Don't create a new one — the reference text is also in the
        # reference tie line label on the map itself.

    def _add_nv_map_grid(self, map_item: QgsLayoutItemMap):
        """
        Add a coordinate grid to the NV map item.

        The NV template lacks a grid. This adds a cross-style grid
        with coordinate annotations matching the project CRS.
        """
        try:
            from qgis.core import QgsLayoutItemMapGrid

            grid = QgsLayoutItemMapGrid('UTM Grid', map_item)

            # Set CRS
            if self.state.project_epsg:
                crs = QgsCoordinateReferenceSystem(f"EPSG:{self.state.project_epsg}")
                grid.setCrs(crs)

            # Calculate interval from extent (target ~4 lines)
            extent = map_item.extent()
            map_span = min(extent.width(), extent.height())
            nice_intervals = [100, 200, 250, 500, 1000, 1500, 2000, 2500, 5000]
            target_interval = map_span / 4
            chosen_interval = nice_intervals[0]
            for interval in nice_intervals:
                if interval >= target_interval:
                    chosen_interval = interval
                    break

            grid.setIntervalX(chosen_interval)
            grid.setIntervalY(chosen_interval)

            # Cross style
            grid.setStyle(QgsLayoutItemMapGrid.Cross)
            grid.setCrossLength(3.0)

            # Enable annotations
            grid.setAnnotationEnabled(True)
            grid.setAnnotationFont(QFont("Arial", 8))
            grid.setAnnotationPrecision(0)

            # Annotations outside on all sides (NV has full-page map)
            grid.setAnnotationPosition(
                QgsLayoutItemMapGrid.OutsideMapFrame,
                QgsLayoutItemMapGrid.Left,
            )
            grid.setAnnotationPosition(
                QgsLayoutItemMapGrid.OutsideMapFrame,
                QgsLayoutItemMapGrid.Right,
            )
            grid.setAnnotationPosition(
                QgsLayoutItemMapGrid.OutsideMapFrame,
                QgsLayoutItemMapGrid.Top,
            )
            grid.setAnnotationPosition(
                QgsLayoutItemMapGrid.OutsideMapFrame,
                QgsLayoutItemMapGrid.Bottom,
            )

            grid.setEnabled(True)
            map_item.grids().addGrid(grid)

            logger.info(
                f"[CLAIMS MAP] Added NV map grid with {chosen_interval}m interval"
            )

        except Exception as e:
            logger.warning(f"[CLAIMS MAP] Could not add NV map grid: {e}")

    def _add_claim_inset(self, layout: QgsPrintLayout):
        """
        Add a schematic inset diagram showing a single representative claim
        with corner numbers (C1-C4) and dimensions (1,500' x 600').

        This replaces labeling every corner on the main map, since all
        claims in a block share the same corner layout.  The inset is
        drawn as text labels on a white rectangle in the lower-left of
        the map area.
        """
        try:
            from qgis.core import QgsLayoutItemShape

            # Determine the first claim's corner ordering to get the
            # correct corner-number arrangement
            lm_corner = 1
            if self.state.processed_claims:
                lm_corner = self.state.processed_claims[0].get('lm_corner', 1)

            # Position: lower-left of the map area, above the title block.
            # Adapt to the actual page size (works for both 36x24 and legal).
            page = layout.pageCollection().page(0)
            page_h = page.pageSize().height()
            map_item = self._find_map_item(layout)
            map_bottom = map_item.pagePos().y() + map_item.sizeWithUnits().height()

            inset_w = 100.0
            inset_h = 75.0
            inset_x = map_item.pagePos().x() + 5.0
            inset_y = map_bottom - inset_h - 5.0

            # White background box with border
            bg = QgsLayoutItemShape(layout)
            bg.setShapeType(QgsLayoutItemShape.Rectangle)
            bg.attemptMove(
                QgsLayoutPoint(inset_x, inset_y, QgsUnitTypes.LayoutMillimeters)
            )
            bg.attemptResize(
                QgsLayoutSize(inset_w, inset_h, QgsUnitTypes.LayoutMillimeters)
            )
            # White fill with black border
            symbol = QgsSymbol.defaultSymbol(2)  # Polygon
            symbol.setColor(QColor(255, 255, 255, 230))
            symbol.symbolLayer(0).setStrokeColor(QColor('#000000'))
            symbol.symbolLayer(0).setStrokeWidth(0.5)
            bg.setSymbol(symbol)
            layout.addLayoutItem(bg)

            # Title for the inset
            title = self._create_text_label(
                layout, "Typical Claim Layout",
                inset_x + 5, inset_y + 3, inset_w - 10, 8,
                font_size=9, bold=True,
            )
            title.setHAlign(Qt.AlignmentFlag.AlignHCenter)
            layout.addLayoutItem(title)

            # Corner labels positioned around a virtual rectangle
            # The claim rectangle occupies the center of the inset
            rect_x = inset_x + 15
            rect_y = inset_y + 15
            rect_w = 70  # Proportional to 1500'
            rect_h = 40  # Proportional to 600'

            # Draw the claim rectangle outline
            claim_rect = QgsLayoutItemShape(layout)
            claim_rect.setShapeType(QgsLayoutItemShape.Rectangle)
            claim_rect.attemptMove(
                QgsLayoutPoint(rect_x, rect_y, QgsUnitTypes.LayoutMillimeters)
            )
            claim_rect.attemptResize(
                QgsLayoutSize(rect_w, rect_h, QgsUnitTypes.LayoutMillimeters)
            )
            rect_sym = QgsSymbol.defaultSymbol(2)
            rect_sym.setColor(QColor(255, 255, 255, 0))  # Transparent fill
            rect_sym.symbolLayer(0).setStrokeColor(QColor('#333333'))
            rect_sym.symbolLayer(0).setStrokeWidth(0.4)
            claim_rect.setSymbol(rect_sym)
            layout.addLayoutItem(claim_rect)

            # Corner positions (x, y offsets from rect origin)
            # Standard lode claim: C1=SE, C2=SW, C3=NW, C4=NE
            corner_positions = {
                1: (rect_x + rect_w - 2, rect_y + rect_h - 1),   # SE / bottom-right
                2: (rect_x - 8, rect_y + rect_h - 1),              # SW / bottom-left
                3: (rect_x - 8, rect_y - 1),                       # NW / top-left
                4: (rect_x + rect_w - 2, rect_y - 1),              # NE / top-right
            }

            for corner_num, (cx, cy) in corner_positions.items():
                lm_text = " (LM)" if corner_num == lm_corner else ""
                label = self._create_text_label(
                    layout, f"C{corner_num}{lm_text}",
                    cx, cy, 18, 6, font_size=7, bold=True,
                )
                layout.addLayoutItem(label)

            # Dimension labels along the sides
            # Top/bottom: 1,500'
            top_dim = self._create_text_label(
                layout, "1,500'",
                rect_x + rect_w / 2 - 10, rect_y - 7, 20, 6,
                font_size=7,
            )
            top_dim.setHAlign(Qt.AlignmentFlag.AlignHCenter)
            layout.addLayoutItem(top_dim)

            # Left/right: 600'
            side_dim = self._create_text_label(
                layout, "600'",
                rect_x - 13, rect_y + rect_h / 2 - 3, 12, 6,
                font_size=7,
            )
            side_dim.setHAlign(Qt.AlignmentFlag.AlignHCenter)
            layout.addLayoutItem(side_dim)

            # Discovery monument marker (center of claim)
            disc_label = self._create_text_label(
                layout, "Discovery\nMonument",
                rect_x + rect_w / 2 - 12, rect_y + rect_h / 2 - 5, 24, 12,
                font_size=6,
            )
            disc_label.setHAlign(Qt.AlignmentFlag.AlignHCenter)
            disc_label.setVAlign(Qt.AlignmentFlag.AlignVCenter)
            layout.addLayoutItem(disc_label)

            logger.info("[CLAIMS MAP] Added claim inset diagram")

        except Exception as e:
            logger.warning(f"[CLAIMS MAP] Could not add claim inset: {e}")

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

    def _configure_scale_bars(self, layout: QgsPrintLayout, use_feet: bool = True):
        """
        Configure scale bars in the layout to use feet (US mining standard).

        The bundled templates may have scale bars in meters. This converts
        them to feet for US mining claim maps.
        """
        if not use_feet:
            return

        for item in layout.items():
            if isinstance(item, QgsLayoutItemScaleBar):
                # QgsUnitTypes.DistanceFeet = 0 in some QGIS versions
                # Use the enum directly
                try:
                    item.setUnits(Qgis.DistanceUnit.Feet)
                except AttributeError:
                    # Older QGIS versions
                    item.setUnits(QgsUnitTypes.DistanceFeet)

                # Set reasonable number of segments for feet
                item.setNumberOfSegments(4)
                item.setUnitsPerSegment(500)  # 500 ft per segment
                item.setUnitLabel('ft')

    def _configure_legend(
        self, layout: QgsPrintLayout, map_layers: List[QgsMapLayer],
    ):
        """
        Populate the legend item with the layers shown in this map.

        Filters out basemap layers (USGS Topo, PLSS) to keep the legend
        focused on claims-related symbology.
        """
        legend_item = None
        for item in layout.items():
            if isinstance(item, QgsLayoutItemLegend):
                legend_item = item
                break

        if not legend_item:
            return

        # Link legend to the map item
        map_item = self._find_map_item(layout)
        if map_item:
            legend_item.setLinkedMap(map_item)

        # Build a custom layer tree with only claims-related layers
        # Exclude basemap and annotation layers from legend
        basemap_names = {
            'USGS Topo', 'PLSS Sections', 'PLSS Townships',
            'Claim Dimensions', 'Reference Tie', 'Corner Labels', 'Reference Point',
        }
        legend_model = legend_item.model()
        root_group = legend_model.rootGroup()
        root_group.removeAllChildren()

        for layer in map_layers:
            if layer.name() in basemap_names:
                continue
            root_group.addLayer(layer)

        # Disable auto-update so it stays locked to our layer list
        legend_item.setAutoUpdateModel(False)

        legend_item.adjustBoxSize()

    def _remove_legend(self, layout: QgsPrintLayout):
        """Remove or hide the legend item from a layout (for filing maps)."""
        for item in layout.items():
            if isinstance(item, QgsLayoutItemLegend):
                layout.removeLayoutItem(item)
                return

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
        self,
        claims_layer: QgsVectorLayer,
        buffer_pct: float = 0.15,
        include_reference: bool = False,
    ) -> QgsRectangle:
        """Calculate bounding extent of claims with buffer.

        If include_reference is True, expands the extent to also encompass
        the reference point so the tie line is fully visible.
        """
        extent = claims_layer.extent()

        # Optionally include the reference point in the extent
        if include_reference and self.state.reference_points:
            ref = self.state.reference_points[0]
            ref_e = ref.get('easting', 0)
            ref_n = ref.get('northing', 0)
            if ref_e and ref_n:
                extent.combineExtentWith(QgsRectangle(ref_e, ref_n, ref_e, ref_n))

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

            # Place bottom and right annotations inside the map frame
            # so they don't extend into the title block area.
            # Top and left stay outside (they have room).
            from qgis.core import QgsLayoutItemMapGrid
            grid.setAnnotationPosition(
                QgsLayoutItemMapGrid.InsideMapFrame,
                QgsLayoutItemMapGrid.Bottom,
            )
            grid.setAnnotationPosition(
                QgsLayoutItemMapGrid.InsideMapFrame,
                QgsLayoutItemMapGrid.Right,
            )
            grid.setAnnotationPosition(
                QgsLayoutItemMapGrid.OutsideMapFrame,
                QgsLayoutItemMapGrid.Top,
            )
            grid.setAnnotationPosition(
                QgsLayoutItemMapGrid.OutsideMapFrame,
                QgsLayoutItemMapGrid.Left,
            )

            # Disable grid frame on bottom and right to prevent the
            # zebra/tick frame from overlapping the title block.
            grid.setFrameSideFlags(
                QgsLayoutItemMapGrid.FrameLeft
                | QgsLayoutItemMapGrid.FrameTop
            )

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
        """Gather all relevant layers organized by role.

        Includes both existing project layers and dynamically-created
        annotation layers (dimensions, tie lines, corner labels, reference point).
        """
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

        # Clean up any leftover annotation layers from previous generation
        self._cleanup_annotation_layers()

        # Create dynamic annotation layers
        dimension_layer = self._create_dimension_layer()
        tie_layer = self._create_reference_tie_layer()
        corner_label_layer = self._create_corner_label_layer()
        ref_point_layer = self._create_reference_point_layer()

        waypoints_layer = find_layer('Claims Waypoints')
        # Create a filtered copy that only labels corner/witness waypoints
        # (LM names duplicate claim names, so we show LM symbols without labels)
        waypoints_corners_only = self._create_waypoints_corners_only(waypoints_layer)

        return {
            'topo': find_layer('USGS Topo'),
            'claims': find_layer('Lode Claims'),
            'corners': find_layer('Corner Points'),
            'lm_corners': find_layer('LM Corners'),
            'centerlines': find_layer('Center Lines'),
            'monuments': find_layer('Monuments'),
            'waypoints': waypoints_layer,
            'waypoints_corners_only': waypoints_corners_only,
            'plss_sections': find_layer('PLSS Sections'),
            'plss_townships': find_layer('PLSS Townships'),
            'sideline_monuments': find_layer('Sideline Monuments'),
            'endline_monuments': find_layer('Endline Monuments'),
            # Dynamic annotation layers
            'dimensions': dimension_layer,
            'tie_line': tie_layer,
            'corner_labels': corner_label_layer,
            'ref_point': ref_point_layer,
        }

    def _find_claims_layer(self) -> Optional[QgsVectorLayer]:
        """Find the claims layer by name or ID."""
        # Try by ID first
        if self.state.claims_layer_id:
            layer = QgsProject.instance().mapLayer(self.state.claims_layer_id)
            if isinstance(layer, QgsVectorLayer) and layer.isValid():
                return layer

        # Try by name (without full _collect_layers to avoid creating annotation layers)
        project = QgsProject.instance()
        prefix = self.state.grid_name_prefix or ""
        suffix = f" [{prefix} Lode Claims]" if prefix else ""

        for name in [f"Lode Claims{suffix}", "Lode Claims"]:
            layers = project.mapLayersByName(name)
            if layers and isinstance(layers[0], QgsVectorLayer):
                return layers[0]

        return None

    # States where the LM is just a corner designation (no separate discovery
    # monument), so LM points should NOT be shown on the map.
    _NO_LM_DISPLAY_STATES = {'ID', 'NM'}

    def _build_layer_list(
        self,
        layers: Dict[str, Optional[QgsMapLayer]],
        include_waypoints: bool = True,
        include_centerlines: bool = True,
        include_monuments: bool = True,
        include_endline_monuments: bool = False,
        include_sideline_monuments: bool = False,
        include_corners: bool = True,
        include_dimensions: bool = False,
        include_tie_line: bool = False,
        include_corner_labels: bool = False,
        include_ref_point: bool = False,
        use_waypoints_corners_only: bool = False,
    ) -> List[QgsMapLayer]:
        """
        Build ordered layer list for a map item.

        Args:
            use_waypoints_corners_only: If True, use the filtered waypoints
                layer that only labels corner/witness types (LM symbols shown
                without labels since LM name = claim name).

        Layer order (top to bottom):
        1. Reference tie line + reference point (if included)
        2. Dimension annotations (if included)
        3. Corner labels (if included)
        4. Waypoints (if included)
        5. Monuments / LM Corners
        6. Corner Points (if included)
        7. Center Lines
        8. Claims polygons
        9. PLSS Sections
        10. PLSS Townships
        11. USGS Topo (bottom)
        """
        result = []
        state_code = self._get_claims_state()
        show_lm = state_code not in self._NO_LM_DISPLAY_STATES

        # Annotation layers on top so labels aren't obscured
        if include_ref_point and layers.get('ref_point'):
            result.append(layers['ref_point'])

        if include_tie_line and layers.get('tie_line'):
            result.append(layers['tie_line'])

        if include_dimensions and layers.get('dimensions'):
            result.append(layers['dimensions'])

        if include_corner_labels and layers.get('corner_labels'):
            result.append(layers['corner_labels'])

        if include_waypoints:
            # Use filtered copy (corner/witness labels only) or full waypoints
            if use_waypoints_corners_only and layers.get('waypoints_corners_only'):
                result.append(layers['waypoints_corners_only'])
            elif layers.get('waypoints'):
                result.append(layers['waypoints'])

        if include_monuments and layers.get('monuments'):
            result.append(layers['monuments'])

        if include_endline_monuments and layers.get('endline_monuments'):
            result.append(layers['endline_monuments'])

        if include_sideline_monuments and layers.get('sideline_monuments'):
            result.append(layers['sideline_monuments'])

        # LM Corners: show symbol only for states with separate LMs
        if include_corners and show_lm and layers.get('lm_corners'):
            result.append(layers['lm_corners'])

        if include_corners and layers.get('corners'):
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
    # PROGRAMMATIC LAYOUT LABELS
    # =========================================================================

    def _add_filing_text_labels(self, layout: QgsPrintLayout, map_item: QgsLayoutItemMap):
        """
        Add filing-specific text labels below the map on generic filing maps.

        Adds: bearings/distances, monument description, and reference tie text
        in a compact info block below the map area.
        """
        # Get the bottom of the map item to position text below it
        map_bottom = map_item.pagePos().y() + map_item.sizeWithUnits().height()
        page_width = layout.pageCollection().page(0).pageSize().width()
        left_margin = 8.0  # mm
        text_width = page_width - (left_margin * 2)

        y_pos = map_bottom + 2  # small gap below map

        # Bearings and distances
        bearings_text = self._generate_az_bearings_text()
        if bearings_text:
            label = self._create_text_label(
                layout, bearings_text, left_margin, y_pos, text_width, 20,
                font_size=7,
            )
            layout.addLayoutItem(label)
            y_pos += 22

        # Monument description
        monument_text = self._generate_monument_text()
        if monument_text:
            label = self._create_text_label(
                layout, monument_text, left_margin, y_pos, text_width, 8,
                font_size=7,
            )
            layout.addLayoutItem(label)
            y_pos += 10

        # Reference tie
        reference_text = self._generate_reference_text()
        if reference_text:
            label = self._create_text_label(
                layout, reference_text, left_margin, y_pos, text_width, 8,
                font_size=7,
            )
            layout.addLayoutItem(label)

    def _create_text_label(
        self,
        layout: QgsPrintLayout,
        text: str,
        x: float,
        y: float,
        width: float,
        height: float,
        font_size: int = 8,
        bold: bool = False,
    ) -> QgsLayoutItemLabel:
        """Create a positioned text label in a print layout."""
        label = QgsLayoutItemLabel(layout)
        label.setText(text)

        font = QFont("Arial", font_size)
        font.setBold(bold)
        label.setFont(font)

        label.attemptMove(
            QgsLayoutPoint(x, y, QgsUnitTypes.LayoutMillimeters)
        )
        label.attemptResize(
            QgsLayoutSize(width, height, QgsUnitTypes.LayoutMillimeters)
        )
        label.setVAlign(Qt.AlignmentFlag.AlignTop)
        label.setHAlign(Qt.AlignmentFlag.AlignLeft)

        return label

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
        Generate bearings and distances text for filing maps using
        surveyor's notation.

        Produces text like:
        "CM 1: Beginning at Corner No. 1, thence N 90°00' W, 1,500.00 ft
        to Corner No. 2; thence N 00°00' E, 600.00 ft to Corner No. 3; ..."
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
                bearing = self._calc_bearing(e1, n1, e2, n2)
                surveyors = self._bearing_to_surveyors(bearing)
                segments.append((round(distance_ft, 2), surveyors))

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

            # Build claim name list
            if len(names) <= 3:
                name_str = ', '.join(names)
            else:
                name_str = f"{names[0]} to {names[-1]}"

            # Build description using surveyor's notation
            desc = (
                f"{name_str}: Beginning at Corner No. 1, thence {segments[0][1]}, "
                f"{segments[0][0]:,.2f} ft to Corner No. 2; thence {segments[1][1]}, "
                f"{segments[1][0]:,.2f} ft to Corner No. 3; thence {segments[2][1]}, "
                f"{segments[2][0]:,.2f} ft to Corner No. 4; thence {segments[3][1]}, "
                f"{segments[3][0]:,.2f} ft to Corner No. 1, the point of beginning."
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
        """Generate reference/tie text with bearing and distance from
        reference point to Corner No. 1 of the first claim."""
        if not self.state.reference_points:
            return "Reference: [no reference point specified - add in Step 3]"

        ref = self.state.reference_points[0]
        ref_name = ref.get('name', 'survey monument')

        # Calculate bearing and distance from reference to Corner 1
        tie_text = ""
        if self.state.processed_claims:
            first_claim = self.state.processed_claims[0]
            corners = first_claim.get('corners', [])
            ref_easting = ref.get('easting', 0)
            ref_northing = ref.get('northing', 0)
            if corners and ref_easting and ref_northing:
                corner = corners[0]
                corner_e = corner.get('easting', 0)
                corner_n = corner.get('northing', 0)
                bearing = self._calc_bearing(ref_easting, ref_northing, corner_e, corner_n)
                dist_ft = self._calc_distance_ft(ref_easting, ref_northing, corner_e, corner_n)
                surveyors = self._bearing_to_surveyors(bearing)
                tie_text = f" Corner No. 1 bears {surveyors}, {dist_ft:,.2f} ft from"

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

        if tie_text:
            if plss_str:
                return f"Reference:{tie_text} the {ref_name}, {plss_str}"
            return f"Reference:{tie_text} the {ref_name}"
        if plss_str:
            return f"Reference: the permanent survey monument located at the {ref_name}, {plss_str}"
        return f"Reference: the permanent survey monument located at the {ref_name}"

    def _generate_nv_reference_text(self) -> str:
        """
        Generate NV reference text showing bearing and distance
        from a reference survey monument to Corner No. 1 of the
        first claim, using surveyor's bearing notation.
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

        # Bearing and distance from reference point TO Corner 1
        bearing = self._calc_bearing(ref_easting, ref_northing, corner_e, corner_n)
        distance_ft = self._calc_distance_ft(ref_easting, ref_northing, corner_e, corner_n)
        surveyors = self._bearing_to_surveyors(bearing)

        ref_name = ref.get('name', 'the permanent survey monument')

        return (
            f"Corner No. 1 of {first_claim.get('name', 'Claim 1')} bears "
            f"{surveyors}, {distance_ft:,.2f} ft from {ref_name}"
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

        Uses 8-point compass for field map text where cardinal is appropriate.
        """
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
            (360.1, 'north'),
        ]

        for threshold, direction in directions:
            if bearing < threshold:
                return direction

        return 'north'

    def _bearing_to_surveyors(self, bearing: float) -> str:
        """
        Convert azimuth bearing (degrees from north, 0-360) to surveyor's
        bearing notation.

        Surveyor's bearings are measured from N or S toward E or W,
        never exceeding 90 degrees. Examples:
        - 0° → N 00°00' E (due north)
        - 45° → N 45°00' E
        - 90° → N 90°00' E (due east)
        - 135° → S 45°00' E
        - 180° → S 00°00' E (due south)
        - 225° → S 45°00' W
        - 270° → N 90°00' W (due west)
        - 315° → N 45°00' W
        """
        bearing = bearing % 360

        if bearing <= 90:
            # NE quadrant (0-90, including due east)
            ns = 'N'
            ew = 'E'
            angle = bearing
        elif bearing <= 180:
            # SE quadrant (90-180, including due south)
            ns = 'S'
            ew = 'E'
            angle = 180 - bearing
        elif bearing < 270:
            # SW quadrant (180-270, excluding due west)
            ns = 'S'
            ew = 'W'
            angle = bearing - 180
        else:
            # NW quadrant
            ns = 'N'
            ew = 'W'
            angle = 360 - bearing

        degrees = int(angle)
        minutes = int(round((angle - degrees) * 60))
        if minutes == 60:
            degrees += 1
            minutes = 0

        return f"{ns} {degrees:02d}\u00b0{minutes:02d}' {ew}"

    # =========================================================================
    # MAP ANNOTATION LAYERS
    # =========================================================================

    def _create_dimension_layer(self) -> Optional[QgsVectorLayer]:
        """
        Create a memory layer with line segments along each claim edge,
        labeled with the distance in feet and surveyor's bearing.

        Each feature is a 2-point line along one edge of a claim polygon.
        The label shows: "1,500.00' N 90°00' W" (distance + bearing).
        """
        claims = self.state.processed_claims
        if not claims:
            return None

        epsg = self.state.project_epsg or 4326
        uri = f"LineString?crs=EPSG:{epsg}&field=label:string&field=distance_ft:double&field=bearing:string&field=claim:string"
        layer = QgsVectorLayer(uri, "Claim Dimensions", "memory")
        if not layer.isValid():
            logger.warning("[CLAIMS MAP] Failed to create dimension layer")
            return None

        layer.startEditing()

        for claim in claims:
            corners = claim.get('corners', [])
            if len(corners) < 4:
                continue
            claim_name = claim.get('name', '')

            for i in range(4):
                c1 = corners[i]
                c2 = corners[(i + 1) % 4]

                e1 = c1.get('easting', 0)
                n1 = c1.get('northing', 0)
                e2 = c2.get('easting', 0)
                n2 = c2.get('northing', 0)

                dist_ft = self._calc_distance_ft(e1, n1, e2, n2)
                bearing = self._calc_bearing(e1, n1, e2, n2)
                surveyors = self._bearing_to_surveyors(bearing)

                label = f"{dist_ft:,.0f}'"

                feat = QgsFeature(layer.fields())
                feat.setGeometry(QgsGeometry.fromPolylineXY([
                    QgsPointXY(e1, n1),
                    QgsPointXY(e2, n2),
                ]))
                feat.setAttribute('label', label)
                feat.setAttribute('distance_ft', round(dist_ft, 2))
                feat.setAttribute('bearing', surveyors)
                feat.setAttribute('claim', claim_name)
                layer.addFeature(feat)

        layer.commitChanges()

        # Style: invisible line (dimensions are shown via labels only)
        symbol = QgsSymbol.defaultSymbol(layer.geometryType())
        symbol.setOpacity(0)
        renderer = QgsSingleSymbolRenderer(symbol)
        layer.setRenderer(renderer)

        # Label: centered along the line, showing distance
        label_settings = QgsPalLayerSettings()
        label_settings.fieldName = '"label"'
        label_settings.isExpression = True
        label_settings.placement = Qgis.LabelPlacement.Line

        text_format = QgsTextFormat()
        font = QFont("Arial", 8)
        font.setBold(True)
        text_format.setFont(font)
        text_format.setColor(QColor('#1a1a1a'))

        buffer_settings = QgsTextBufferSettings()
        buffer_settings.setEnabled(True)
        buffer_settings.setSize(1.5)
        buffer_settings.setColor(QColor('#FFFFFF'))
        text_format.setBuffer(buffer_settings)

        label_settings.setFormat(text_format)
        labeling = QgsVectorLayerSimpleLabeling(label_settings)
        layer.setLabeling(labeling)
        layer.setLabelsEnabled(True)

        # Don't add to project layer tree — used only in print layouts
        QgsProject.instance().addMapLayer(layer, False)
        logger.info(
            f"[CLAIMS MAP] Created dimension layer with {layer.featureCount()} segments"
        )
        return layer

    def _create_reference_tie_layer(self) -> Optional[QgsVectorLayer]:
        """
        Create a memory layer with a dashed line from the reference point
        to Corner No. 1 of the first claim, labeled with bearing and distance.

        This is the standard "tie line" shown on mining claim filing maps
        connecting the claim to a known survey monument.
        """
        if not self.state.reference_points or not self.state.processed_claims:
            return None

        ref = self.state.reference_points[0]
        ref_easting = ref.get('easting', 0)
        ref_northing = ref.get('northing', 0)

        if not ref_easting and not ref_northing:
            return None

        first_claim = self.state.processed_claims[0]
        corners = first_claim.get('corners', [])
        if not corners:
            return None

        corner = corners[0]
        corner_e = corner.get('easting', 0)
        corner_n = corner.get('northing', 0)

        bearing = self._calc_bearing(ref_easting, ref_northing, corner_e, corner_n)
        dist_ft = self._calc_distance_ft(ref_easting, ref_northing, corner_e, corner_n)
        surveyors = self._bearing_to_surveyors(bearing)

        epsg = self.state.project_epsg or 4326
        uri = f"LineString?crs=EPSG:{epsg}&field=label:string&field=distance_ft:double&field=bearing:string"
        layer = QgsVectorLayer(uri, "Reference Tie", "memory")
        if not layer.isValid():
            return None

        layer.startEditing()

        feat = QgsFeature(layer.fields())
        feat.setGeometry(QgsGeometry.fromPolylineXY([
            QgsPointXY(ref_easting, ref_northing),
            QgsPointXY(corner_e, corner_n),
        ]))
        label = f"{surveyors}  {dist_ft:,.2f}'"
        feat.setAttribute('label', label)
        feat.setAttribute('distance_ft', round(dist_ft, 2))
        feat.setAttribute('bearing', surveyors)
        layer.addFeature(feat)

        layer.commitChanges()

        # Style: dashed red line
        symbol = QgsSymbol.defaultSymbol(layer.geometryType())
        symbol.deleteSymbolLayer(0)

        line_sym = QgsSimpleLineSymbolLayer()
        line_sym.setColor(QColor('#CC0000'))
        line_sym.setWidth(0.5)
        line_sym.setPenStyle(Qt.PenStyle.DashLine)
        symbol.appendSymbolLayer(line_sym)

        renderer = QgsSingleSymbolRenderer(symbol)
        layer.setRenderer(renderer)

        # Label: centered along the line
        label_settings = QgsPalLayerSettings()
        label_settings.fieldName = '"label"'
        label_settings.isExpression = True
        label_settings.placement = Qgis.LabelPlacement.Line

        text_format = QgsTextFormat()
        font = QFont("Arial", 8)
        font.setBold(True)
        font.setItalic(True)
        text_format.setFont(font)
        text_format.setColor(QColor('#CC0000'))

        buffer_settings = QgsTextBufferSettings()
        buffer_settings.setEnabled(True)
        buffer_settings.setSize(1.5)
        buffer_settings.setColor(QColor('#FFFFFF'))
        text_format.setBuffer(buffer_settings)

        label_settings.setFormat(text_format)
        labeling = QgsVectorLayerSimpleLabeling(label_settings)
        layer.setLabeling(labeling)
        layer.setLabelsEnabled(True)

        # Don't add to layer tree
        QgsProject.instance().addMapLayer(layer, False)
        logger.info(
            f"[CLAIMS MAP] Created reference tie layer: {surveyors}, {dist_ft:,.2f}'"
        )
        return layer

    def _create_corner_label_layer(self) -> Optional[QgsVectorLayer]:
        """
        Create a memory point layer with corner labels (C1, C2, C3, C4)
        for each claim, positioned at each corner.

        Used on filing maps where corner identification is required.
        """
        claims = self.state.processed_claims
        if not claims:
            return None

        epsg = self.state.project_epsg or 4326
        uri = f"Point?crs=EPSG:{epsg}&field=label:string&field=claim:string&field=corner_num:integer"
        layer = QgsVectorLayer(uri, "Corner Labels", "memory")
        if not layer.isValid():
            return None

        layer.startEditing()

        for claim in claims:
            corners = claim.get('corners', [])
            claim_name = claim.get('name', '')
            lm_corner = claim.get('lm_corner', 1)

            for corner in corners:
                e = corner.get('easting', 0)
                n = corner.get('northing', 0)
                num = corner.get('corner_number', 0)

                # Label format: "C1" or "C1 (LM)" for the location monument corner
                label = f"C{num}"
                if num == lm_corner:
                    label = f"C{num} (LM)"

                feat = QgsFeature(layer.fields())
                feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(e, n)))
                feat.setAttribute('label', label)
                feat.setAttribute('claim', claim_name)
                feat.setAttribute('corner_num', num)
                layer.addFeature(feat)

        layer.commitChanges()

        # Style: small black circle
        symbol = QgsSymbol.defaultSymbol(layer.geometryType())
        symbol.deleteSymbolLayer(0)
        marker = QgsSimpleMarkerSymbolLayer()
        marker.setColor(QColor('#000000'))
        marker.setSize(2.0)
        marker.setStrokeColor(QColor('#FFFFFF'))
        marker.setStrokeWidth(0.3)
        symbol.appendSymbolLayer(marker)
        renderer = QgsSingleSymbolRenderer(symbol)
        layer.setRenderer(renderer)

        # Label: offset above-right with white buffer
        label_settings = QgsPalLayerSettings()
        label_settings.fieldName = '"label"'
        label_settings.isExpression = True
        label_settings.placement = Qgis.LabelPlacement.OverPoint

        text_format = QgsTextFormat()
        font = QFont("Arial", 8)
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

        QgsProject.instance().addMapLayer(layer, False)
        logger.info(
            f"[CLAIMS MAP] Created corner label layer with {layer.featureCount()} points"
        )
        return layer

    def _create_reference_point_layer(self) -> Optional[QgsVectorLayer]:
        """
        Create a memory point layer with the reference/survey monument
        point, styled with a distinctive symbol and labeled.
        """
        if not self.state.reference_points:
            return None

        ref = self.state.reference_points[0]
        ref_easting = ref.get('easting', 0)
        ref_northing = ref.get('northing', 0)

        if not ref_easting and not ref_northing:
            return None

        epsg = self.state.project_epsg or 4326
        uri = f"Point?crs=EPSG:{epsg}&field=label:string"
        layer = QgsVectorLayer(uri, "Reference Point", "memory")
        if not layer.isValid():
            return None

        layer.startEditing()

        feat = QgsFeature(layer.fields())
        feat.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(ref_easting, ref_northing)))
        ref_name = ref.get('name', 'Survey Monument')
        feat.setAttribute('label', ref_name)
        layer.addFeature(feat)

        layer.commitChanges()

        # Style: red triangle
        symbol = QgsSymbol.defaultSymbol(layer.geometryType())
        symbol.deleteSymbolLayer(0)
        marker = QgsSimpleMarkerSymbolLayer()
        marker.setShape(Qgis.MarkerShape.Triangle)
        marker.setColor(QColor('#CC0000'))
        marker.setSize(3.5)
        marker.setStrokeColor(QColor('#000000'))
        marker.setStrokeWidth(0.4)
        symbol.appendSymbolLayer(marker)
        renderer = QgsSingleSymbolRenderer(symbol)
        layer.setRenderer(renderer)

        # Label
        label_settings = QgsPalLayerSettings()
        label_settings.fieldName = '"label"'
        label_settings.isExpression = True
        label_settings.placement = Qgis.LabelPlacement.OverPoint

        text_format = QgsTextFormat()
        font = QFont("Arial", 7)
        font.setItalic(True)
        text_format.setFont(font)
        text_format.setColor(QColor('#CC0000'))

        buffer_settings = QgsTextBufferSettings()
        buffer_settings.setEnabled(True)
        buffer_settings.setSize(1.5)
        buffer_settings.setColor(QColor('#FFFFFF'))
        text_format.setBuffer(buffer_settings)

        label_settings.setFormat(text_format)
        labeling = QgsVectorLayerSimpleLabeling(label_settings)
        layer.setLabeling(labeling)
        layer.setLabelsEnabled(True)

        QgsProject.instance().addMapLayer(layer, False)
        logger.info("[CLAIMS MAP] Created reference point layer")
        return layer

    def _create_waypoints_corners_only(
        self, source_layer: Optional[QgsVectorLayer]
    ) -> Optional[QgsVectorLayer]:
        """
        Create a filtered copy of the waypoints layer that only labels
        corner and witness waypoints.

        LM/discovery/sideline/endline symbols are included but unlabeled,
        since their names duplicate the claim name.  This avoids cluttering
        the map while still showing all monument positions.
        """
        if not source_layer or not isinstance(source_layer, QgsVectorLayer):
            return None

        try:
            # Clone the layer as a memory layer
            clone = source_layer.materialize(
                QgsFeatureRequest()
            )
            if not clone or not clone.isValid():
                return None

            clone.setName("Waypoints (corners labeled)")

            # Copy the renderer from the source layer
            clone.setRenderer(source_layer.renderer().clone())

            # Apply labeling only for corner and witness types
            label_settings = QgsPalLayerSettings()
            # Only label corner and witness types using an expression filter
            label_settings.fieldName = '"Name"'
            label_settings.isExpression = True
            label_settings.placement = Qgis.LabelPlacement.OverPoint

            # Use data-defined show label to filter by waypoint_type
            show_prop = QgsProperty.fromExpression(
                "\"waypoint_type\" IN ('corner', 'witness')"
            )
            label_settings.dataDefinedProperties().setProperty(
                QgsPalLayerSettings.Property.Show, show_prop
            )

            text_format = QgsTextFormat()
            font = QFont("Arial", 8)
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
            clone.setLabeling(labeling)
            clone.setLabelsEnabled(True)

            QgsProject.instance().addMapLayer(clone, False)
            logger.info(
                "[CLAIMS MAP] Created filtered waypoints layer "
                "(corner/witness labels only)"
            )
            return clone

        except Exception as e:
            logger.warning(
                f"[CLAIMS MAP] Could not create filtered waypoints layer: {e}"
            )
            return None

    def _cleanup_annotation_layers(self):
        """Remove temporary annotation layers from the project."""
        project = QgsProject.instance()
        for name in [
            'Claim Dimensions', 'Reference Tie', 'Corner Labels',
            'Reference Point', 'Waypoints (corners labeled)',
        ]:
            for layer in project.mapLayersByName(name):
                project.removeMapLayer(layer.id())

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
