# -*- coding: utf-8 -*-
"""
Grid Move Tool

A QgsMapTool for dragging the entire claim grid to a new position.
"""
from typing import Optional

from qgis.PyQt.QtCore import pyqtSignal, Qt
from qgis.PyQt.QtGui import QColor, QCursor
from qgis.PyQt.QtWidgets import QApplication
from qgis.core import (
    QgsVectorLayer, QgsPointXY, QgsGeometry,
    QgsFeature, QgsWkbTypes
)
from qgis.gui import (
    QgsMapTool, QgsMapCanvas, QgsRubberBand,
    QgsMapToolEmitPoint
)


class GridMoveTool(QgsMapToolEmitPoint):
    """
    Map tool for dragging an entire claim grid to a new position.

    Click and drag to move all features in the layer by the same offset.

    Signals:
        grid_moved: (float, float) - Emitted when grid is moved with (offset_x, offset_y)
    """

    grid_moved = pyqtSignal(float, float)

    def __init__(self, canvas: QgsMapCanvas, layer: QgsVectorLayer):
        """
        Initialize the grid move tool.

        Args:
            canvas: QGIS map canvas
            layer: Vector layer containing the grid to move
        """
        super().__init__(canvas)
        self.canvas = canvas
        self.layer = layer

        # State
        self._dragging = False
        self._start_point: Optional[QgsPointXY] = None
        self._rubber_band: Optional[QgsRubberBand] = None

        # Set cursor
        self.setCursor(QCursor(Qt.SizeAllCursor))

    def canvasPressEvent(self, event):
        """Handle mouse press - start dragging."""
        if event.button() != Qt.LeftButton:
            return

        self._start_point = self.toMapCoordinates(event.pos())
        self._dragging = True

        # Create rubber band for preview
        self._create_rubber_band()

    def canvasMoveEvent(self, event):
        """Handle mouse move - update preview."""
        if not self._dragging or not self._start_point:
            return

        current_point = self.toMapCoordinates(event.pos())

        # Calculate offset
        offset_x = current_point.x() - self._start_point.x()
        offset_y = current_point.y() - self._start_point.y()

        # Update rubber band preview
        self._update_rubber_band(offset_x, offset_y)

    def canvasReleaseEvent(self, event):
        """Handle mouse release - apply the move."""
        if not self._dragging or not self._start_point:
            return

        if event.button() != Qt.LeftButton:
            return

        end_point = self.toMapCoordinates(event.pos())

        # Calculate final offset
        offset_x = end_point.x() - self._start_point.x()
        offset_y = end_point.y() - self._start_point.y()

        # Clear rubber band
        self._clear_rubber_band()

        # Apply the move if significant
        if abs(offset_x) > 0.01 or abs(offset_y) > 0.01:
            self._apply_move(offset_x, offset_y)
            self.grid_moved.emit(offset_x, offset_y)

        # Reset state
        self._dragging = False
        self._start_point = None

    def keyPressEvent(self, event):
        """Handle key press - Escape cancels."""
        if event.key() == Qt.Key_Escape:
            self._clear_rubber_band()
            self._dragging = False
            self._start_point = None

    def deactivate(self):
        """Clean up when tool is deactivated."""
        self._clear_rubber_band()
        super().deactivate()

    def _create_rubber_band(self):
        """Create rubber band for move preview."""
        self._clear_rubber_band()

        # Create a polygon rubber band
        self._rubber_band = QgsRubberBand(self.canvas, QgsWkbTypes.PolygonGeometry)
        self._rubber_band.setColor(QColor(0, 128, 255, 100))
        self._rubber_band.setFillColor(QColor(0, 128, 255, 50))
        self._rubber_band.setWidth(2)

        # Add all geometries from the layer
        for feature in self.layer.getFeatures():
            geom = feature.geometry()
            if not geom.isNull():
                self._rubber_band.addGeometry(geom, self.layer)

    def _update_rubber_band(self, offset_x: float, offset_y: float):
        """Update rubber band position based on offset."""
        if not self._rubber_band:
            return

        # Clear and recreate with offset
        self._rubber_band.reset(QgsWkbTypes.PolygonGeometry)

        for feature in self.layer.getFeatures():
            geom = feature.geometry()
            if not geom.isNull():
                # Create translated geometry
                translated_geom = QgsGeometry(geom)
                translated_geom.translate(offset_x, offset_y)
                self._rubber_band.addGeometry(translated_geom, self.layer)

    def _clear_rubber_band(self):
        """Clear the rubber band."""
        if self._rubber_band:
            self.canvas.scene().removeItem(self._rubber_band)
            self._rubber_band = None

    def _apply_move(self, offset_x: float, offset_y: float):
        """
        Apply the move offset to all features in the layer.

        Args:
            offset_x: X offset in map units
            offset_y: Y offset in map units
        """
        if not self.layer.isEditable():
            self.layer.startEditing()

        try:
            # Translate all features
            for feature in self.layer.getFeatures():
                geom = feature.geometry()
                if geom.isNull():
                    continue

                # Translate the geometry
                geom.translate(offset_x, offset_y)

                # Update the feature geometry
                self.layer.changeGeometry(feature.id(), geom)

            # Commit changes
            self.layer.commitChanges()

            # Refresh the canvas
            self.canvas.refresh()

        except Exception as e:
            # Rollback on error
            self.layer.rollBack()
            raise e


class GridRotateTool(QgsMapToolEmitPoint):
    """
    Map tool for rotating an entire claim grid around a center point.

    This is a placeholder for future implementation.
    """

    grid_rotated = pyqtSignal(float)  # angle in degrees

    def __init__(self, canvas: QgsMapCanvas, layer: QgsVectorLayer):
        super().__init__(canvas)
        self.canvas = canvas
        self.layer = layer
        self.setCursor(QCursor(Qt.CrossCursor))

    # TODO: Implement rotation functionality
