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
    QgsMarkerSymbol,
    QgsLineSymbol,
    QgsFillSymbol,
    QgsRendererCategory,
    QgsCategorizedSymbolRenderer,
    QgsGraduatedSymbolRenderer,
    QgsRendererRange,
    QgsSimpleMarkerSymbolLayer,
    QgsSimpleLineSymbolLayer,
    QgsSimpleFillSymbolLayer,
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
        qcolor = QColor(color)

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
        import os

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
            self.logger.info(f"Applied SVG camera icon to photo layer")
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
