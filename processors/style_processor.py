# -*- coding: utf-8 -*-
"""
Style processor for applying AssayRangeConfiguration to QGIS layers.

Handles color-coding of features based on assay values using
configured grade ranges.
"""
from typing import Dict, Any, List, Optional
from qgis.core import (
    QgsVectorLayer,
    QgsSymbol,
    QgsSymbolLayer,
    QgsMarkerSymbol,
    QgsLineSymbol,
    QgsFillSymbol,
    QgsRendererCategory,
    QgsCategorizedSymbolRenderer,
    QgsGraduatedSymbolRenderer,
    QgsRendererRange,
    QgsSimpleMarkerSymbolLayer,
    QgsWkbTypes
)
from qgis.PyQt.QtGui import QColor

from ..utils.logger import PluginLogger


class StyleProcessor:
    """
    Applies AssayRangeConfiguration styling to QGIS layers.

    Creates graduated symbology based on assay value ranges,
    applying colors and sizes from the configuration.
    """

    def __init__(self):
        """Initialize style processor."""
        self.logger = PluginLogger.get_logger()

    def apply_assay_style(
        self,
        layer: QgsVectorLayer,
        config: Dict[str, Any],
        value_field: str = None
    ) -> bool:
        """
        Apply AssayRangeConfiguration styling to a layer.

        Args:
            layer: Target QGIS vector layer
            config: AssayRangeConfiguration dictionary from API
            value_field: Field name containing assay values
                        (defaults to 'assay_{element}')

        Returns:
            True if successful
        """
        if not layer or not layer.isValid():
            self.logger.warning("Invalid layer for styling")
            return False

        element = config.get('element', 'Au')
        ranges = config.get('ranges', [])
        default_color = config.get('default_color', '#CCCCCC')
        units = config.get('units', 'ppm')

        self.logger.info(f"Config element: {element}, ranges count: {len(ranges)}")
        self.logger.info(f"Config keys: {list(config.keys())}")
        if ranges:
            self.logger.info(f"First range: {ranges[0]}")

        # Determine value field name (default: {element}_{units}, e.g., Au_ppm)
        if not value_field:
            value_field = f'{element}_{units}'

        # Check if field exists
        field_index = layer.fields().indexFromName(value_field)
        if field_index < 0:
            # Try alternative field names for backwards compatibility
            alt_fields = [f'assay_{element}', element, f'{element}_value']
            for alt in alt_fields:
                field_index = layer.fields().indexFromName(alt)
                if field_index >= 0:
                    value_field = alt
                    break

            if field_index < 0:
                self.logger.warning(
                    f"Field '{value_field}' not found in layer. "
                    f"Available fields: {[f.name() for f in layer.fields()]}"
                )
                return False

        self.logger.info(
            f"Applying {element} assay style to {layer.name()} "
            f"using field '{value_field}'"
        )

        # Create graduated renderer
        geometry_type = layer.geometryType()

        # Sort ranges by from_value
        sorted_ranges = sorted(ranges, key=lambda r: r.get('from_value', 0))

        # Create renderer ranges
        renderer_ranges = []
        for range_item in sorted_ranges:
            from_val = range_item.get('from_value', 0)
            to_val = range_item.get('to_value', 0)
            color = range_item.get('color', default_color)
            size = range_item.get('size', 2)
            label = range_item.get('label', f"{from_val} - {to_val}")

            # Create symbol based on geometry type
            symbol = self._create_symbol(geometry_type, color, size)

            # Create range
            renderer_range = QgsRendererRange(
                from_val,
                to_val,
                symbol,
                label
            )
            renderer_ranges.append(renderer_range)

        # Create graduated renderer
        renderer = QgsGraduatedSymbolRenderer(value_field, renderer_ranges)

        # Set default symbol for null/out-of-range values
        default_symbol = self._create_symbol(geometry_type, default_color, 2)
        renderer.setSourceSymbol(default_symbol)

        # Apply to layer
        layer.setRenderer(renderer)
        layer.triggerRepaint()

        self.logger.info(
            f"Applied graduated style with {len(renderer_ranges)} ranges"
        )
        return True

    def apply_category_style(
        self,
        layer: QgsVectorLayer,
        config: Dict[str, Any],
        value_field: str = None,
        category_field: str = None
    ) -> bool:
        """
        Apply categorized styling based on range labels.

        This is an alternative approach that categorizes values into
        discrete classes (e.g., "Low", "Medium", "High") rather than
        using continuous ranges.

        Args:
            layer: Target QGIS vector layer
            config: AssayRangeConfiguration dictionary
            value_field: Field with assay values
            category_field: Field to store category (created if needed)

        Returns:
            True if successful
        """
        if not layer or not layer.isValid():
            return False

        element = config.get('element', 'Au')
        ranges = config.get('ranges', [])
        default_color = config.get('default_color', '#CCCCCC')

        if not value_field:
            value_field = f'assay_{element}'

        if not category_field:
            category_field = f'{element}_category'

        # Check if value field exists
        if layer.fields().indexFromName(value_field) < 0:
            self.logger.warning(f"Field '{value_field}' not found")
            return False

        # Sort ranges
        sorted_ranges = sorted(ranges, key=lambda r: r.get('from_value', 0))

        geometry_type = layer.geometryType()
        categories = []

        # Add categories for each range
        for range_item in sorted_ranges:
            label = range_item.get('label', '')
            color = range_item.get('color', default_color)
            size = range_item.get('size', 2)

            if not label:
                from_val = range_item.get('from_value', 0)
                to_val = range_item.get('to_value', 0)
                label = f"{from_val} - {to_val}"

            symbol = self._create_symbol(geometry_type, color, size)
            category = QgsRendererCategory(label, symbol, label)
            categories.append(category)

        # Add default category for null values
        default_symbol = self._create_symbol(geometry_type, default_color, 2)
        null_category = QgsRendererCategory(None, default_symbol, "No Data")
        categories.append(null_category)

        # Create renderer
        renderer = QgsCategorizedSymbolRenderer(category_field, categories)
        layer.setRenderer(renderer)
        layer.triggerRepaint()

        return True

    def _create_symbol(
        self,
        geometry_type: int,
        color: str,
        size: float
    ) -> QgsSymbol:
        """
        Create a symbol for the given geometry type.

        Args:
            geometry_type: QgsWkbTypes geometry type
            color: Hex color code
            size: Symbol size (pixels for 2D)

        Returns:
            QgsSymbol instance
        """
        QColor(color)

        if geometry_type == QgsWkbTypes.PointGeometry:
            symbol = QgsMarkerSymbol.createSimple({
                'name': 'circle',
                'color': color,
                'outline_color': '#000000',
                'outline_width': '0.4',
                'size': str(size)
            })
        elif geometry_type == QgsWkbTypes.LineGeometry:
            symbol = QgsLineSymbol.createSimple({
                'color': color,
                'width': str(size * 0.5)
            })
        elif geometry_type == QgsWkbTypes.PolygonGeometry:
            symbol = QgsFillSymbol.createSimple({
                'color': color,
                'outline_color': '#000000',
                'outline_width': '0.4'
            })
        else:
            # Default to marker
            symbol = QgsMarkerSymbol.createSimple({
                'name': 'circle',
                'color': color,
                'size': str(size)
            })

        return symbol

    # ==================== VECTOR LAYER STYLE-SPEC TRANSLATOR ====================
    # Translates the server's neutral style spec (vector_layers_master.md §3.2,
    # SPEC_VERSION 1) into native QGIS renderers. Lossy-to-nearest by design —
    # exact colors/labels/attributes, nearest match for patterns and widths.

    # Web px → QGIS mm (96 dpi)
    _PX_TO_MM = 0.2646

    # Server pattern flavors → QGIS simple-fill brush styles. Nearest-match
    # inverse of the server's _QGIS_FILL_TO_FLAVOR (vector_style_extraction.py).
    VECTOR_FLAVOR_TO_QGIS_FILL = {
        'diag-stripe': 'f_diagonal',
        'diag-cross': 'diagonal_x',
        'horiz-stripe': 'horizontal',
        'vert-stripe': 'vertical',
        'grid': 'cross',
        'checker': 'dense2',
        'dots-coarse': 'dense3',
        'dots-fine': 'dense6',
    }

    # Server dashArray strings → QGIS line styles (inverse of _QGIS_LINE_TO_DASH)
    VECTOR_DASH_TO_QGIS_LINE = {
        '8 6': 'dash',
        '2 6': 'dot',
        '8 6 2 6': 'dash dot',
        '8 6 2 6 2 6': 'dash dot dot',
    }

    VECTOR_MARKER_SHAPES = ('circle', 'square', 'triangle', 'diamond', 'star')

    def apply_vector_layer_style(
        self,
        layer: QgsVectorLayer,
        layer_summary: Dict[str, Any]
    ) -> bool:
        """
        Apply a pulled vector layer's server style to the QGIS layer.

        frozen single/categorized/graduated specs become native QGIS renderers
        on the source attribute; catalog-mode layers (styled by the geoDB
        lithology/alteration/formation catalogs) categorize on the per-feature
        resolved style JSON (`geodb_style`) with legend labels from
        catalog_legend — the catalog itself is never exposed client-side.

        Returns:
            True if a renderer was applied
        """
        if not layer or not layer.isValid():
            return False

        spec = layer_summary.get('style') or {}
        style_mode = layer_summary.get('style_mode') or 'frozen'

        try:
            if style_mode == 'catalog':
                return self._apply_vector_catalog_style(layer, layer_summary)

            renderer_type = spec.get('renderer', 'single')
            if renderer_type == 'categorized' and spec.get('attribute'):
                return self._apply_vector_categorized_style(layer, spec)
            if renderer_type == 'graduated' and spec.get('attribute'):
                return self._apply_vector_graduated_style(layer, spec)
            return self._apply_vector_single_style(layer, spec)
        except Exception as e:
            self.logger.exception(f"Failed to apply vector layer style: {e}")
            return False

    def _apply_vector_single_style(self, layer, spec: Dict[str, Any]) -> bool:
        symbol = self._vector_symbol_from_spec(layer, spec.get('default') or {})
        from qgis.core import QgsSingleSymbolRenderer
        layer.setRenderer(QgsSingleSymbolRenderer(symbol))
        layer.triggerRepaint()
        return True

    def _apply_vector_categorized_style(self, layer, spec: Dict[str, Any]) -> bool:
        attribute = spec['attribute']
        if layer.fields().indexFromName(attribute) < 0:
            self.logger.warning(
                f"Vector style attribute '{attribute}' not on layer — using single symbol"
            )
            return self._apply_vector_single_style(layer, spec)

        categories = []
        for category in spec.get('categories', []):
            symbol = self._vector_symbol_from_spec(layer, category)
            value = category.get('value')
            label = str(category.get('label') or value or '')
            categories.append(QgsRendererCategory(value, symbol, label))

        # Catch-all for values not in the spec (server falls back to default)
        default_symbol = self._vector_symbol_from_spec(layer, spec.get('default') or {})
        categories.append(QgsRendererCategory(None, default_symbol, 'Other'))

        layer.setRenderer(QgsCategorizedSymbolRenderer(attribute, categories))
        layer.triggerRepaint()
        return True

    def _apply_vector_graduated_style(self, layer, spec: Dict[str, Any]) -> bool:
        attribute = spec['attribute']
        if layer.fields().indexFromName(attribute) < 0:
            self.logger.warning(
                f"Vector style attribute '{attribute}' not on layer — using single symbol"
            )
            return self._apply_vector_single_style(layer, spec)

        ranges = []
        for category in spec.get('categories', []):
            bounds = str(category.get('value', ''))
            if '..' not in bounds:
                continue
            try:
                lower_str, upper_str = bounds.split('..', 1)
                lower, upper = float(lower_str), float(upper_str)
            except ValueError:
                continue
            symbol = self._vector_symbol_from_spec(layer, category)
            label = str(category.get('label') or f"{lower} - {upper}")
            ranges.append(QgsRendererRange(lower, upper, symbol, label))

        if not ranges:
            return self._apply_vector_single_style(layer, spec)

        renderer = QgsGraduatedSymbolRenderer(attribute, ranges)
        default_symbol = self._vector_symbol_from_spec(layer, spec.get('default') or {})
        renderer.setSourceSymbol(default_symbol)
        layer.setRenderer(renderer)
        layer.triggerRepaint()
        return True

    def _apply_vector_catalog_style(self, layer, layer_summary: Dict[str, Any]) -> bool:
        """Categorize on the per-feature resolved style JSON (geodb_style)."""
        import json as _json

        if layer.fields().indexFromName('geodb_style') < 0:
            return self._apply_vector_single_style(layer, layer_summary.get('style') or {})

        legend_by_color = {}
        for entry in layer_summary.get('catalog_legend') or []:
            color = (entry.get('color') or '').lower()
            if color and color not in legend_by_color:
                legend_by_color[color] = entry.get('label')

        distinct_styles = []
        for feature in layer.getFeatures():
            value = feature.attribute('geodb_style')
            if value and value not in distinct_styles:
                distinct_styles.append(value)

        categories = []
        for style_json in distinct_styles:
            try:
                symbol_spec = _json.loads(style_json)
            except (ValueError, TypeError):
                continue
            symbol = self._vector_symbol_from_spec(layer, symbol_spec)
            color = (symbol_spec.get('color') or '').lower()
            label = legend_by_color.get(color) or color or 'Unstyled'
            categories.append(QgsRendererCategory(style_json, symbol, str(label)))

        spec = layer_summary.get('style') or {}
        default_symbol = self._vector_symbol_from_spec(layer, spec.get('default') or {})
        categories.append(QgsRendererCategory(None, default_symbol, 'Uncatalogued'))

        layer.setRenderer(QgsCategorizedSymbolRenderer('geodb_style', categories))
        layer.triggerRepaint()
        return True

    def _vector_symbol_from_spec(self, layer, symbol_spec: Dict[str, Any]) -> QgsSymbol:
        """One spec <symbol> dict → a QGIS symbol matching the layer's geometry."""
        geometry_type = layer.geometryType()
        color = symbol_spec.get('color') or '#808080'
        outline = symbol_spec.get('outline')
        weight = symbol_spec.get('weight')
        dash = symbol_spec.get('dashArray')
        pattern = symbol_spec.get('pattern')
        fill_opacity = symbol_spec.get('fillOpacity')

        if geometry_type == QgsWkbTypes.PointGeometry:
            shape = symbol_spec.get('shape')
            props = {
                'name': shape if shape in self.VECTOR_MARKER_SHAPES else 'circle',
                'color': color,
                'outline_color': outline or '#333333',
            }
            size = symbol_spec.get('size')
            if size:
                props['size'] = str(round(float(size) * self._PX_TO_MM, 2))
            return QgsMarkerSymbol.createSimple(props)

        if geometry_type == QgsWkbTypes.LineGeometry:
            props = {'color': color}
            if weight:
                props['width'] = str(round(float(weight) * self._PX_TO_MM, 2))
            if dash:
                props['line_style'] = self.VECTOR_DASH_TO_QGIS_LINE.get(str(dash), 'dash')
            return QgsLineSymbol.createSimple(props)

        # Polygon (and fallback)
        props = {
            'color': self._vector_fill_color(color, fill_opacity),
            'outline_color': outline or '#333333',
        }
        if weight:
            props['outline_width'] = str(round(float(weight) * self._PX_TO_MM, 2))
        if isinstance(pattern, str) and pattern in self.VECTOR_FLAVOR_TO_QGIS_FILL:
            props['style'] = self.VECTOR_FLAVOR_TO_QGIS_FILL[pattern]
        elif pattern is not None and not isinstance(pattern, str):
            # FGDC pattern id (int) — no client-side atlas; nearest is solid
            self.logger.debug(f"FGDC pattern id {pattern} rendered as solid fill")
        return QgsFillSymbol.createSimple(props)

    @staticmethod
    def _vector_fill_color(hex_color: str, fill_opacity) -> str:
        """'#rrggbb' + opacity 0..1 → QGIS 'r,g,b,a' color string."""
        try:
            q_color = QColor(hex_color)
            alpha = 255
            if fill_opacity is not None:
                alpha = max(0, min(255, int(round(float(fill_opacity) * 255))))
            return f"{q_color.red()},{q_color.green()},{q_color.blue()},{alpha}"
        except Exception:
            return hex_color

    def get_color_for_value(
        self,
        value: Optional[float],
        config: Dict[str, Any]
    ) -> str:
        """
        Get the color for a specific assay value.

        Useful for manual styling or legend generation.

        Args:
            value: Assay value (can be None)
            config: AssayRangeConfiguration dictionary

        Returns:
            Hex color code
        """
        if value is None:
            return config.get('default_color', '#CCCCCC')

        ranges = config.get('ranges', [])
        default_color = config.get('default_color', '#CCCCCC')

        for range_item in ranges:
            from_val = range_item.get('from_value', 0)
            to_val = range_item.get('to_value', 0)

            if from_val <= value < to_val:
                return range_item.get('color', default_color)

        return default_color

    def get_label_for_value(
        self,
        value: Optional[float],
        config: Dict[str, Any]
    ) -> str:
        """
        Get the label for a specific assay value.

        Args:
            value: Assay value (can be None)
            config: AssayRangeConfiguration dictionary

        Returns:
            Range label string
        """
        if value is None:
            return "No Data"

        ranges = config.get('ranges', [])

        for range_item in ranges:
            from_val = range_item.get('from_value', 0)
            to_val = range_item.get('to_value', 0)

            if from_val <= value < to_val:
                label = range_item.get('label', '')
                if label:
                    return label
                return f"{from_val} - {to_val}"

        return "Out of Range"

    def create_legend_items(
        self,
        config: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Create legend items from configuration.

        Returns a list of dictionaries suitable for building
        a custom legend widget.

        Args:
            config: AssayRangeConfiguration dictionary

        Returns:
            List of legend item dicts with 'color', 'label', 'from', 'to'
        """
        ranges = config.get('ranges', [])
        default_color = config.get('default_color', '#CCCCCC')
        units = config.get('units', '')

        items = []

        # Sort by from_value
        sorted_ranges = sorted(ranges, key=lambda r: r.get('from_value', 0))

        for range_item in sorted_ranges:
            from_val = range_item.get('from_value', 0)
            to_val = range_item.get('to_value', 0)
            color = range_item.get('color', default_color)
            label = range_item.get('label', '')

            if not label:
                label = f"{from_val:,.4g} - {to_val:,.4g} {units}"

            items.append({
                'color': color,
                'label': label,
                'from': from_val,
                'to': to_val,
                'size': range_item.get('size', 2)
            })

        # Add default
        items.append({
            'color': default_color,
            'label': 'No Data / Out of Range',
            'from': None,
            'to': None,
            'size': 2
        })

        return items

    def remove_style(self, layer: QgsVectorLayer) -> bool:
        """
        Remove custom styling and reset to default.

        Args:
            layer: Target layer

        Returns:
            True if successful
        """
        if not layer or not layer.isValid():
            return False

        # Reset to single symbol renderer
        symbol = QgsSymbol.defaultSymbol(layer.geometryType())
        from qgis.core import QgsSingleSymbolRenderer
        renderer = QgsSingleSymbolRenderer(symbol)
        layer.setRenderer(renderer)
        layer.triggerRepaint()

        return True

    def apply_simple_gray_style(
        self,
        layer: QgsVectorLayer,
        color: str = '#808080',
        size: float = 3.0
    ) -> bool:
        """
        Apply a simple gray circle style to a layer.

        Used when "None" assay configuration is selected, displaying
        all points as uniform gray circles without graduated coloring.

        Args:
            layer: Target QGIS vector layer
            color: Hex color code (default gray #808080)
            size: Symbol size in mm (default 3.0)

        Returns:
            True if successful
        """
        if not layer or not layer.isValid():
            self.logger.warning("Invalid layer for simple styling")
            return False

        from qgis.core import QgsSingleSymbolRenderer

        geometry_type = layer.geometryType()

        # Create symbol based on geometry type
        symbol = self._create_symbol(geometry_type, color, size)

        # Apply single symbol renderer
        renderer = QgsSingleSymbolRenderer(symbol)
        layer.setRenderer(renderer)
        layer.triggerRepaint()

        self.logger.info(
            f"Applied simple gray style to layer: {layer.name()}"
        )
        return True

    def apply_field_task_style(self, layer: QgsVectorLayer) -> bool:
        """
        Apply status-based categorized styling to a field tasks layer.

        Creates a categorized renderer based on sample status:
        - PL (Planned): Gray hollow circle
        - AS (Assigned): Yellow/orange filled circle
        - CO (Collected): Green filled circle (if present)
        - SK (Skipped): Red X marker

        Args:
            layer: Target QGIS vector layer (FieldTasks layer)

        Returns:
            True if successful
        """
        if not layer or not layer.isValid():
            self.logger.warning("Invalid layer for field task styling")
            return False

        status_field = 'status'
        field_index = layer.fields().indexFromName(status_field)
        if field_index < 0:
            self.logger.warning(
                f"Field '{status_field}' not found in layer. "
                f"Available fields: {[f.name() for f in layer.fields()]}"
            )
            return False

        self.logger.info(f"Applying field task status styling to {layer.name()}")

        # Define status categories with colors and symbols
        # PL = Planned (gray hollow), AS = Assigned (yellow), CO = Collected (green), SK = Skipped (red)
        status_styles = {
            'PL': {
                'color': '#9ca3af',  # Gray
                'outline': '#374151',  # Dark gray
                'label': 'Planned',
                'size': 4,
                'style': 'hollow'
            },
            'AS': {
                'color': '#fbbf24',  # Yellow/Amber
                'outline': '#d97706',  # Dark amber
                'label': 'Assigned',
                'size': 5,
                'style': 'filled'
            },
            'CO': {
                'color': '#10b981',  # Green
                'outline': '#059669',  # Dark green
                'label': 'Collected',
                'size': 5,
                'style': 'filled'
            },
            'SK': {
                'color': '#ef4444',  # Red
                'outline': '#b91c1c',  # Dark red
                'label': 'Skipped',
                'size': 4,
                'style': 'cross'
            }
        }

        categories = []

        for status_code, style in status_styles.items():
            # Create marker symbol
            symbol = QgsMarkerSymbol()
            symbol.deleteSymbolLayer(0)  # Remove default

            simple_layer = QgsSimpleMarkerSymbolLayer()

            if style['style'] == 'hollow':
                simple_layer.setShape(QgsSimpleMarkerSymbolLayer.Circle)
                simple_layer.setColor(QColor('transparent'))
                simple_layer.setStrokeColor(QColor(style['outline']))
                simple_layer.setStrokeWidth(1.0)
            elif style['style'] == 'cross':
                simple_layer.setShape(QgsSimpleMarkerSymbolLayer.Cross2)
                simple_layer.setColor(QColor(style['color']))
                simple_layer.setStrokeColor(QColor(style['outline']))
                simple_layer.setStrokeWidth(1.0)
            else:  # filled
                simple_layer.setShape(QgsSimpleMarkerSymbolLayer.Circle)
                simple_layer.setColor(QColor(style['color']))
                simple_layer.setStrokeColor(QColor(style['outline']))
                simple_layer.setStrokeWidth(0.5)

            simple_layer.setSize(style['size'])
            symbol.appendSymbolLayer(simple_layer)

            category = QgsRendererCategory(status_code, symbol, style['label'])
            categories.append(category)

        # Add default category for unknown/null status values
        default_symbol = QgsMarkerSymbol.createSimple({
            'name': 'circle',
            'color': '#d1d5db',
            'outline_color': '#9ca3af',
            'outline_width': '0.5',
            'size': '3'
        })
        null_category = QgsRendererCategory(None, default_symbol, "Unknown")
        categories.append(null_category)

        # Create and apply categorized renderer
        renderer = QgsCategorizedSymbolRenderer(status_field, categories)
        layer.setRenderer(renderer)
        layer.triggerRepaint()

        self.logger.info(
            f"Applied categorized status style with {len(categories)} categories"
        )
        return True

    def apply_photo_style(self, layer: QgsVectorLayer) -> bool:
        """
        Apply camera icon symbology to a Photo layer.

        Uses a camera/photo marker symbol to indicate photo locations.

        Args:
            layer: Target QGIS vector layer (Photo layer)

        Returns:
            True if successful
        """
        if not layer or not layer.isValid():
            self.logger.warning("Invalid layer for photo styling")
            return False

        from qgis.core import QgsSingleSymbolRenderer, QgsSvgMarkerSymbolLayer

        # Create a camera marker symbol
        # QGIS has built-in SVG markers including camera icons
        symbol = QgsMarkerSymbol()
        symbol.deleteSymbolLayer(0)  # Remove default circle

        # Try to use SVG camera icon from QGIS resources
        # Falls back to a distinctive marker if SVG not available
        svg_paths = [
            # QGIS default SVG paths (cross-platform)
            'gpsicons/camera.svg',
            'entertainment/camera.svg',
            'symbol/camera.svg',
        ]

        svg_layer = None
        for svg_path in svg_paths:
            try:
                test_layer = QgsSvgMarkerSymbolLayer(svg_path)
                if test_layer.isValid() or test_layer.path():
                    svg_layer = test_layer
                    svg_layer.setSize(6)
                    svg_layer.setFillColor(QColor('#3498db'))  # Blue fill
                    svg_layer.setStrokeColor(QColor('#2c3e50'))  # Dark outline
                    svg_layer.setStrokeWidth(0.5)
                    break
            except Exception:
                continue

        if svg_layer:
            symbol.appendSymbolLayer(svg_layer)
            self.logger.info("Applied SVG camera icon to photo layer")
        else:
            # Fallback: use a distinctive star/cross marker
            simple_layer = QgsSimpleMarkerSymbolLayer()
            simple_layer.setShape(QgsSimpleMarkerSymbolLayer.Star)
            simple_layer.setSize(5)
            simple_layer.setColor(QColor('#3498db'))  # Blue
            simple_layer.setStrokeColor(QColor('#2c3e50'))
            simple_layer.setStrokeWidth(0.5)
            symbol.appendSymbolLayer(simple_layer)
            self.logger.info("Applied star marker to photo layer (SVG not available)")

        # Apply single symbol renderer
        renderer = QgsSingleSymbolRenderer(symbol)
        layer.setRenderer(renderer)
        layer.triggerRepaint()

        self.logger.info(f"Applied photo style to layer: {layer.name()}")
        return True

    def apply_fieldnote_style(self, layer: QgsVectorLayer) -> bool:
        """
        Apply notebook-style symbology to a FieldNote layer.

        Uses a simple circle marker with a distinct color for field notes.

        Args:
            layer: Target QGIS vector layer (FieldNote layer)

        Returns:
            True if successful
        """
        if not layer or not layer.isValid():
            self.logger.warning("Invalid layer for field note styling")
            return False

        from qgis.core import QgsSingleSymbolRenderer

        # Create a notebook/note marker symbol
        symbol = QgsMarkerSymbol.createSimple({
            'name': 'circle',
            'color': '#3498db',  # Blue
            'outline_color': '#2980b9',  # Darker blue outline
            'outline_width': '0.5',
            'size': '4'
        })

        # Apply single symbol renderer
        renderer = QgsSingleSymbolRenderer(symbol)
        layer.setRenderer(renderer)
        layer.triggerRepaint()

        self.logger.info(f"Applied field note style to layer: {layer.name()}")
        return True

    def apply_fieldnote_photo_style(self, layer: QgsVectorLayer) -> bool:
        """
        Apply camera icon symbology to a FieldNotePhoto layer.

        Uses a camera/photo marker and adds a map action to view photos on click.

        Args:
            layer: Target QGIS vector layer (FieldNotePhoto layer)

        Returns:
            True if successful
        """
        if not layer or not layer.isValid():
            self.logger.warning("Invalid layer for field note photo styling")
            return False

        from qgis.core import QgsSingleSymbolRenderer, QgsSvgMarkerSymbolLayer

        # Create a camera marker symbol
        symbol = QgsMarkerSymbol()
        symbol.deleteSymbolLayer(0)  # Remove default circle

        # Try to use SVG camera icon from QGIS resources
        svg_paths = [
            'gpsicons/camera.svg',
            'entertainment/camera.svg',
            'symbol/camera.svg',
        ]

        svg_layer = None
        for svg_path in svg_paths:
            try:
                test_layer = QgsSvgMarkerSymbolLayer(svg_path)
                if test_layer.isValid() or test_layer.path():
                    svg_layer = test_layer
                    svg_layer.setSize(5)
                    svg_layer.setFillColor(QColor('#e74c3c'))  # Red fill
                    svg_layer.setStrokeColor(QColor('#c0392b'))  # Dark red outline
                    svg_layer.setStrokeWidth(0.5)
                    break
            except Exception:
                continue

        if svg_layer:
            symbol.appendSymbolLayer(svg_layer)
            self.logger.info("Applied SVG camera icon to field note photo layer")
        else:
            # Fallback: use a square marker (represents photo)
            simple_layer = QgsSimpleMarkerSymbolLayer()
            simple_layer.setShape(QgsSimpleMarkerSymbolLayer.Square)
            simple_layer.setSize(4)
            simple_layer.setColor(QColor('#e74c3c'))  # Red
            simple_layer.setStrokeColor(QColor('#c0392b'))
            simple_layer.setStrokeWidth(0.5)
            symbol.appendSymbolLayer(simple_layer)
            self.logger.info("Applied square marker to field note photo layer (SVG not available)")

        # Apply single symbol renderer
        renderer = QgsSingleSymbolRenderer(symbol)
        layer.setRenderer(renderer)

        # Add map action to open photo in browser when clicked
        self._add_photo_view_action(layer)

        layer.triggerRepaint()

        self.logger.info(f"Applied field note photo style to layer: {layer.name()}")
        return True

    def _add_photo_view_action(self, layer: QgsVectorLayer):
        """
        Add a map action to open photo URL when feature is clicked.

        Uses QGIS's built-in action system for click-to-view functionality.

        Args:
            layer: Vector layer to add action to
        """
        from qgis.core import QgsAction

        # Check if action already exists
        actions = layer.actions()
        for action in actions.actions():
            if action.name() == 'View Full Photo':
                return  # Already has the action

        # Create action to open photo in browser
        # Uses QGIS expression to get the image_url field value
        action = QgsAction(
            QgsAction.OpenUrl,  # Action type: open URL in browser
            'View Full Photo',  # Action name
            '[% "image_url" %]',  # Expression for URL (field value)
            '',  # Icon path (empty = default)
            False,  # Capture output
            '',  # Notification message
            {'Feature', 'Canvas'}  # Action scopes: feature identify and canvas
        )
        actions.addAction(action)

        # Set as default action for feature identification
        layer.setDefaultValueDefinition(
            layer.fields().indexFromName('image_url'),
            layer.defaultValueDefinition(layer.fields().indexFromName('image_url'))
        )

        self.logger.info(f"Added photo view action to layer: {layer.name()}")

    # =========================================================================
    # STRUCTURE STYLING (FGDC-standard geological symbols)
    # =========================================================================

    # Mapping of feature_type codes to SVG symbol filenames
    STRUCTURE_SYMBOLS = {
        'BD': 'bedding_strike_dip.svg',
        'FT': 'fault_strike_dip.svg',
        'FL': 'foliation_strike_dip.svg',
        'LN': 'lineation_arrow.svg',
        'SZ': 'shear_zone.svg',
        'VN': 'vein_strike_dip.svg',
        'JT': 'joint_strike_dip.svg',
        'OT': 'default_point.svg',
    }

    # Feature types that use 'trend' for rotation (linear features)
    # All others use 'strike'
    LINEAR_FEATURE_TYPES = ['LN']

    def apply_structure_style(self, layer: QgsVectorLayer) -> bool:
        """
        Apply FGDC-standard geological structure symbology.

        Creates a rule-based renderer that:
        - Selects SVG symbol based on feature_type field
        - Rotates symbol based on strike (planar) or trend (linear) field
        - Adds dip/plunge labels offset from symbol

        Args:
            layer: Target QGIS vector layer (Structure layer)

        Returns:
            True if successful
        """
        if not layer or not layer.isValid():
            self.logger.warning("Invalid layer for structure styling")
            return False

        from qgis.core import (
            QgsRuleBasedRenderer,
            QgsSvgMarkerSymbolLayer,
            QgsProperty
        )
        import os

        # Check for required fields
        feature_type_idx = layer.fields().indexFromName('feature_type')
        self.logger.info(f"Structure layer fields: {[f.name() for f in layer.fields()]}")
        self.logger.info(f"feature_type field index: {feature_type_idx}")

        if feature_type_idx < 0:
            self.logger.warning(
                f"Field 'feature_type' not found in layer. "
                f"Available fields: {[f.name() for f in layer.fields()]}"
            )
            # Fall back to simple style
            return self.apply_simple_gray_style(layer)

        # Get plugin symbols directory
        plugin_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        symbols_dir = os.path.join(plugin_dir, 'symbols', 'structures')

        self.logger.info(f"Plugin dir: {plugin_dir}")
        self.logger.info(f"Symbols dir: {symbols_dir}")
        self.logger.info(f"Symbols dir exists: {os.path.exists(symbols_dir)}")

        if not os.path.exists(symbols_dir):
            self.logger.warning(f"Symbols directory not found: {symbols_dir}")
            return self._apply_fallback_structure_style(layer)

        self.logger.info(f"Applying FGDC structure styling to {layer.name()}")

        # Create root rule
        root_rule = QgsRuleBasedRenderer.Rule(None)

        # Create a rule for each feature type
        for feature_type, svg_file in self.STRUCTURE_SYMBOLS.items():
            svg_path = os.path.join(symbols_dir, svg_file)

            if not os.path.exists(svg_path):
                self.logger.warning(f"SVG file not found: {svg_path}")
                continue

            # Create SVG marker symbol
            symbol = QgsMarkerSymbol()
            symbol.deleteSymbolLayer(0)

            svg_layer = QgsSvgMarkerSymbolLayer(svg_path)
            svg_layer.setSize(6)  # Base size in mm
            svg_layer.setFillColor(QColor('#000000'))
            svg_layer.setStrokeColor(QColor('#000000'))
            svg_layer.setStrokeWidth(0.2)

            # Set data-defined rotation based on feature type
            if feature_type in self.LINEAR_FEATURE_TYPES:
                # Linear features rotate by trend
                rotation_expr = 'coalesce("trend", 0)'
            else:
                # Planar features rotate by strike
                rotation_expr = 'coalesce("strike", 0)'

            # QGIS rotates clockwise from North, our symbols are at 0 degrees
            # Strike is measured clockwise from North, so direct mapping works
            svg_layer.setDataDefinedProperty(
                QgsSymbolLayer.PropertyAngle,
                QgsProperty.fromExpression(rotation_expr)
            )

            symbol.appendSymbolLayer(svg_layer)

            # Create rule with filter
            rule = QgsRuleBasedRenderer.Rule(symbol)
            rule.setFilterExpression(f'"feature_type" = \'{feature_type}\'')
            rule.setLabel(self._get_structure_type_label(feature_type))
            root_rule.appendChild(rule)

        # Add default rule for unknown types
        default_svg = os.path.join(symbols_dir, 'default_point.svg')
        if os.path.exists(default_svg):
            default_symbol = QgsMarkerSymbol()
            default_symbol.deleteSymbolLayer(0)
            default_svg_layer = QgsSvgMarkerSymbolLayer(default_svg)
            default_svg_layer.setSize(5)
            default_symbol.appendSymbolLayer(default_svg_layer)
        else:
            default_symbol = QgsMarkerSymbol.createSimple({
                'name': 'circle',
                'color': '#666666',
                'outline_color': '#000000',
                'outline_width': '0.5',
                'size': '4'
            })

        default_rule = QgsRuleBasedRenderer.Rule(default_symbol)
        default_rule.setFilterExpression('ELSE')  # Catch-all rule for unmatched features
        default_rule.setLabel('Other')
        root_rule.appendChild(default_rule)

        # Create and apply the renderer
        renderer = QgsRuleBasedRenderer(root_rule)
        layer.setRenderer(renderer)

        # Add dip/plunge labels
        self._add_structure_labels(layer)

        layer.triggerRepaint()

        self.logger.info(
            f"Applied FGDC structure style with {len(self.STRUCTURE_SYMBOLS)} symbol types"
        )
        return True

    def _get_structure_type_label(self, feature_type: str) -> str:
        """Get human-readable label for structure type code."""
        labels = {
            'BD': 'Bedding',
            'FT': 'Fault',
            'FL': 'Foliation',
            'LN': 'Lineation',
            'SZ': 'Shear Zone',
            'VN': 'Vein',
            'JT': 'Joint',
            'OT': 'Other',
        }
        return labels.get(feature_type, feature_type)

    def _add_structure_labels(self, layer: QgsVectorLayer):
        """
        Add dip/plunge labels positioned relative to the structure symbol.

        Labels are offset in the dip direction (perpendicular to strike).

        Args:
            layer: Vector layer to add labels to
        """
        from qgis.core import (
            QgsPalLayerSettings,
            QgsVectorLayerSimpleLabeling,
            QgsTextFormat,
            QgsProperty,
            Qgis
        )
        from qgis.PyQt.QtGui import QFont

        # Check if dip field exists
        dip_idx = layer.fields().indexFromName('dip')
        plunge_idx = layer.fields().indexFromName('plunge')

        if dip_idx < 0 and plunge_idx < 0:
            self.logger.info("No dip or plunge fields found, skipping labels")
            return

        # Create label settings
        settings = QgsPalLayerSettings()

        # Use expression to show dip for planar, plunge for linear features
        # Format as integer (e.g., "45" not "45.0")
        label_expr = '''
            CASE
                WHEN "feature_type" = 'LN' THEN
                    to_string(round(coalesce("plunge", 0)))
                ELSE
                    to_string(round(coalesce("dip", 0)))
            END
        '''
        settings.fieldName = label_expr
        settings.isExpression = True

        # Text format
        text_format = QgsTextFormat()
        font = QFont('Arial', 8)
        font.setBold(True)
        text_format.setFont(font)
        text_format.setSize(8)
        text_format.setColor(QColor('#000000'))
        settings.setFormat(text_format)

        # Position label at the tip of the dip tick (in dip direction)
        # Use dip_direction if available, otherwise strike + 90 (right-hand rule)
        settings.placement = Qgis.LabelPlacement.OverPoint

        # Calculate offset in dip direction to place label at end of tick
        # The SVG symbol is 6mm with tick extending ~3mm from center (half of symbol)
        # Place label just beyond the tick end
        offset_distance = 4.5  # mm - at the end of the dip tick

        # Use dip_direction directly if available, otherwise calculate from strike
        # X offset = sin(dip_direction) * distance  (East is positive X)
        # Y offset = -cos(dip_direction) * distance (North is negative Y in screen coords)
        dip_dir_expr = 'coalesce("dip_direction", coalesce("strike", 0) + 90)'
        x_offset_expr = f'sin(radians({dip_dir_expr})) * {offset_distance}'
        y_offset_expr = f'-cos(radians({dip_dir_expr})) * {offset_distance}'

        # Set data-defined X and Y offsets separately
        props = settings.dataDefinedProperties()
        props.setProperty(
            QgsPalLayerSettings.Property.OffsetXY,
            QgsProperty.fromExpression(f'array({x_offset_expr}, {y_offset_expr})')
        )
        settings.setDataDefinedProperties(props)

        # Set offset units to millimeters to match symbol size
        settings.offsetUnits = Qgis.RenderUnit.Millimeters

        # Enable labeling
        settings.enabled = True

        # Apply labeling
        labeling = QgsVectorLayerSimpleLabeling(settings)
        layer.setLabeling(labeling)
        layer.setLabelsEnabled(True)

        self.logger.info("Added dip/plunge labels to structure layer")

    def _apply_fallback_structure_style(self, layer: QgsVectorLayer) -> bool:
        """
        Apply a simple categorized style when SVG symbols are not available.

        Uses basic shapes with different colors for each structure type.

        Args:
            layer: Target vector layer

        Returns:
            True if successful
        """
        self.logger.info("Applying fallback structure style (no SVG symbols)")

        # Define colors for each structure type
        structure_colors = {
            'BD': {'color': '#2ecc71', 'label': 'Bedding'},      # Green
            'FT': {'color': '#e74c3c', 'label': 'Fault'},        # Red
            'FL': {'color': '#9b59b6', 'label': 'Foliation'},    # Purple
            'LN': {'color': '#3498db', 'label': 'Lineation'},    # Blue
            'SZ': {'color': '#e67e22', 'label': 'Shear Zone'},   # Orange
            'VN': {'color': '#f1c40f', 'label': 'Vein'},         # Yellow
            'JT': {'color': '#1abc9c', 'label': 'Joint'},        # Teal
            'OT': {'color': '#95a5a6', 'label': 'Other'},        # Gray
        }

        categories = []

        for feature_type, style in structure_colors.items():
            symbol = QgsMarkerSymbol.createSimple({
                'name': 'triangle',
                'color': style['color'],
                'outline_color': '#000000',
                'outline_width': '0.5',
                'size': '4',
                'angle': '0'  # Will be overridden by data-defined
            })

            # Add data-defined rotation
            from qgis.core import QgsProperty
            symbol.setDataDefinedAngle(
                QgsProperty.fromExpression('coalesce("strike", 0)')
            )

            category = QgsRendererCategory(feature_type, symbol, style['label'])
            categories.append(category)

        # Default category
        default_symbol = QgsMarkerSymbol.createSimple({
            'name': 'circle',
            'color': '#cccccc',
            'outline_color': '#000000',
            'outline_width': '0.5',
            'size': '3'
        })
        null_category = QgsRendererCategory(None, default_symbol, 'Unknown')
        categories.append(null_category)

        # Apply categorized renderer
        renderer = QgsCategorizedSymbolRenderer('feature_type', categories)
        layer.setRenderer(renderer)

        # Add labels
        self._add_structure_labels(layer)

        layer.triggerRepaint()

        return True
