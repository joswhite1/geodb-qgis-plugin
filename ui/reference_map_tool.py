# -*- coding: utf-8 -*-
"""
Reference point map tool for QClaims functionality.

Provides an interactive map tool for adding reference points to mining claims.
Reference points are used by the server to calculate bearing/distance from
the discovery monument, which is included in legal documents.

Ported from QClaims: GenClaimQ/reference_tool.py
"""
from typing import Optional, List, Dict, Any, TYPE_CHECKING

from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QDialogButtonBox, QListWidget, QListWidgetItem,
    QMessageBox, QWidget, QGroupBox
)
from qgis.PyQt.QtGui import QCursor
from qgis.core import (
    QgsPointXY, QgsProject, QgsCoordinateReferenceSystem,
    QgsCoordinateTransform, QgsVectorLayer, QgsFeature,
    QgsGeometry, QgsField, QgsFields, QgsWkbTypes
)
from qgis.PyQt.QtCore import QVariant
from qgis.gui import QgsMapToolEmitPoint, QgsVertexMarker, QgsMapCanvas

if TYPE_CHECKING:
    from ..managers.claims_storage_manager import ClaimsStorageManager

from ..utils.logger import PluginLogger


class ReferenceInputDialog(QDialog):
    """
    Dialog for entering reference point description.

    Shows the clicked coordinates and allows the user to enter a description
    for the reference point (e.g., 'Road junction at Highway 93').
    """

    def __init__(self, x: float, y: float, crs_name: str, parent=None):
        """
        Initialize the reference input dialog.

        Args:
            x: Easting coordinate (UTM)
            y: Northing coordinate (UTM)
            crs_name: Name of the coordinate reference system
            parent: Parent widget
        """
        super().__init__(parent)

        self.setWindowTitle("Add Reference Point")
        self.setMinimumWidth(400)

        layout = QVBoxLayout()

        # Show coordinates
        coord_label = QLabel(
            f"<b>Location:</b> {x:.1f}, {y:.1f}<br>"
            f"<b>CRS:</b> {crs_name}"
        )
        layout.addWidget(coord_label)

        # Input field for description
        layout.addWidget(QLabel("Reference Description:"))
        self.text_input = QLineEdit()
        self.text_input.setPlaceholderText(
            "e.g., 'NE corner of road junction', 'Large pine tree', 'Rock outcrop'"
        )
        layout.addWidget(self.text_input)

        # Help text
        help_label = QLabel(
            "<i>Reference points are used to describe the location of your claims "
            "in legal documents. Choose prominent, permanent features.</i>"
        )
        help_label.setWordWrap(True)
        help_label.setStyleSheet("color: #6b7280; font-size: 11px;")
        layout.addWidget(help_label)

        # Buttons
        button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        self.setLayout(layout)

        # Focus on text input
        self.text_input.setFocus()

    def get_description(self) -> str:
        """Return the entered description."""
        return self.text_input.text().strip()


class ReferenceMapTool(QgsMapToolEmitPoint):
    """
    Map tool for clicking to add reference points.

    Extends QgsMapToolEmitPoint to capture map clicks and convert them
    to reference point data. Handles CRS transformation between the
    map canvas CRS and the layer CRS (UTM).

    Signals:
        reference_added: Emitted when a reference point is added (message)
        reference_cancelled: Emitted when user cancels without adding
    """

    reference_added = pyqtSignal(str)
    reference_cancelled = pyqtSignal()

    def __init__(
        self,
        canvas: QgsMapCanvas,
        layer_epsg: int,
        storage_manager: Optional['ClaimsStorageManager'] = None,
        geopackage_path: Optional[str] = None
    ):
        """
        Initialize the reference map tool.

        Args:
            canvas: QGIS map canvas
            layer_epsg: EPSG code for the layer CRS (should be UTM)
            storage_manager: Optional storage manager for persistence
            geopackage_path: Optional path to the GeoPackage for storage
        """
        super().__init__(canvas)

        self.canvas = canvas
        self.layer_epsg = layer_epsg
        self.storage_manager = storage_manager
        self.geopackage_path = geopackage_path
        self.marker: Optional[QgsVertexMarker] = None
        self.logger = PluginLogger.get_logger()

        # Store reference points in memory if no storage manager
        self._reference_points: List[Dict[str, Any]] = []

        # Set crosshair cursor
        self.setCursor(QCursor(Qt.CrossCursor))

    def canvasReleaseEvent(self, event):
        """
        Handle mouse click on map.

        Transforms coordinates to UTM, shows dialog for description,
        and stores the reference point.
        """
        # Get click coordinates in canvas CRS
        click_point = self.toMapCoordinates(event.pos())

        # Get the layer CRS (should be UTM)
        layer_crs = QgsCoordinateReferenceSystem(f'EPSG:{self.layer_epsg}')
        canvas_crs = self.canvas.mapSettings().destinationCrs()

        # Transform coordinates if needed
        if canvas_crs != layer_crs:
            transform = QgsCoordinateTransform(
                canvas_crs, layer_crs, QgsProject.instance()
            )
            transformed_point = transform.transform(click_point)
        else:
            transformed_point = click_point

        x = transformed_point.x()
        y = transformed_point.y()

        # Show temporary marker at click location
        self._show_marker(click_point)

        # Open dialog to get description
        dialog = ReferenceInputDialog(x, y, layer_crs.description())

        if dialog.exec_():
            description = dialog.get_description()

            if description:
                # Add reference point
                try:
                    self._add_reference_point(x, y, description)
                    self.reference_added.emit(
                        f"Added reference point: {description}"
                    )
                except Exception as e:
                    self.logger.error(f"[REFERENCE TOOL] Failed to add point: {e}")
                    self.reference_added.emit(f"Error adding reference: {str(e)}")
            else:
                self.reference_added.emit(
                    "Reference creation cancelled - no description provided"
                )
        else:
            self.reference_cancelled.emit()

        # Remove marker
        self._remove_marker()

    def _add_reference_point(self, x: float, y: float, description: str):
        """
        Add a reference point to storage.

        Args:
            x: Easting coordinate (UTM)
            y: Northing coordinate (UTM)
            description: Reference point description
        """
        reference = {
            'name': description,
            'easting': x,
            'northing': y,
            'epsg': self.layer_epsg
        }

        # Store in memory
        self._reference_points.append(reference)

        # Also persist to storage manager if available
        if self.storage_manager and self.geopackage_path:
            try:
                self.storage_manager.save_reference_point(
                    QgsPointXY(x, y),
                    description,
                    self.layer_epsg,
                    gpkg_path=self.geopackage_path
                )
                self.logger.info(
                    f"[REFERENCE TOOL] Saved reference point to GeoPackage: {description}"
                )
            except Exception as e:
                self.logger.error(
                    f"[REFERENCE TOOL] Failed to persist reference point: {e}"
                )
                raise  # Re-raise so caller knows save failed
        elif self.storage_manager:
            self.logger.warning(
                f"[REFERENCE TOOL] No GeoPackage path set, point only saved to memory"
            )

        self.logger.info(
            f"[REFERENCE TOOL] Added reference: {description} at ({x:.1f}, {y:.1f})"
        )

    def get_reference_points(self) -> List[Dict[str, Any]]:
        """
        Get all reference points added with this tool.

        Returns:
            List of reference point dicts with name, easting, northing, epsg
        """
        return self._reference_points.copy()

    def clear_reference_points(self):
        """Clear all stored reference points."""
        self._reference_points.clear()
        self.logger.info("[REFERENCE TOOL] Cleared reference points")

    def set_layer_epsg(self, epsg: int):
        """
        Update the layer EPSG code.

        Args:
            epsg: New EPSG code for coordinate transformation
        """
        self.layer_epsg = epsg
        self.logger.debug(f"[REFERENCE TOOL] Layer EPSG set to {epsg}")

    def _show_marker(self, point: QgsPointXY):
        """
        Show temporary marker at click location.

        Args:
            point: Map coordinates for marker placement
        """
        if self.marker:
            self.canvas.scene().removeItem(self.marker)

        self.marker = QgsVertexMarker(self.canvas)
        self.marker.setCenter(point)
        self.marker.setColor(Qt.red)
        self.marker.setIconSize(15)
        self.marker.setIconType(QgsVertexMarker.ICON_CROSS)
        self.marker.setPenWidth(3)

    def _remove_marker(self):
        """Remove temporary marker."""
        if self.marker:
            self.canvas.scene().removeItem(self.marker)
            self.marker = None

    def deactivate(self):
        """Clean up when tool is deactivated."""
        self._remove_marker()
        super().deactivate()


class ReferencePointsWidget(QWidget):
    """
    Widget for managing reference points in the claims UI.

    Provides:
    - Button to activate reference point map tool
    - List of current reference points
    - Delete functionality
    - Integration with ClaimsWidget
    - Persistent QGIS layer in GeoPackage (or memory layer fallback)
    """

    reference_points_changed = pyqtSignal()

    # Layer name constant
    LAYER_NAME = "Reference Points"

    def __init__(self, parent=None):
        """Initialize the reference points widget."""
        super().__init__(parent)

        self._map_tool: Optional[ReferenceMapTool] = None
        self._reference_points: List[Dict[str, Any]] = []
        self._layer_epsg: int = 4326  # Default, will be updated
        self._layer: Optional[QgsVectorLayer] = None
        self._storage_manager: Optional['ClaimsStorageManager'] = None
        self._geopackage_path: Optional[str] = None
        self._project_name: Optional[str] = None  # For layer naming
        self._features_to_migrate: List[Dict[str, Any]] = []  # For CRS migration
        self.logger = PluginLogger.get_logger()

        self._setup_ui()

    def _setup_ui(self):
        """Set up the widget UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # Instructions
        info_label = QLabel(
            "Reference points help describe claim locations in legal documents. "
            "Click points on the map to add them."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: #6b7280; font-size: 12px;")
        layout.addWidget(info_label)

        # Button row
        button_layout = QHBoxLayout()

        self.add_ref_btn = QPushButton("Add Reference Point")
        self.add_ref_btn.setStyleSheet("""
            QPushButton {
                padding: 8px 16px;
                background-color: #059669;
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #047857;
            }
            QPushButton:checked {
                background-color: #065f46;
                border: 2px solid #10b981;
            }
            QPushButton:disabled {
                background-color: #a7f3d0;
                color: #6b7280;
            }
        """)
        self.add_ref_btn.setCheckable(True)
        self.add_ref_btn.clicked.connect(self._toggle_reference_tool)
        button_layout.addWidget(self.add_ref_btn)

        button_layout.addStretch()

        self.clear_btn = QPushButton("Clear All")
        self.clear_btn.setStyleSheet("""
            QPushButton {
                padding: 6px 12px;
                background-color: #ffffff;
                color: #dc2626;
                border: 1px solid #dc2626;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #fef2f2;
            }
        """)
        self.clear_btn.clicked.connect(self._clear_all)
        self.clear_btn.setEnabled(False)
        button_layout.addWidget(self.clear_btn)

        layout.addLayout(button_layout)

        # Reference points list
        self.ref_list = QListWidget()
        self.ref_list.setMaximumHeight(100)
        self.ref_list.setStyleSheet("""
            QListWidget {
                border: 1px solid #d1d5db;
                border-radius: 4px;
                background-color: #f9fafb;
            }
            QListWidget::item {
                padding: 4px 8px;
            }
            QListWidget::item:selected {
                background-color: #dbeafe;
                color: #1d4ed8;
            }
        """)
        layout.addWidget(self.ref_list)

        # Status label
        self.status_label = QLabel("No reference points added")
        self.status_label.setStyleSheet("color: #6b7280; font-size: 11px;")
        layout.addWidget(self.status_label)

    def _toggle_reference_tool(self, checked: bool):
        """Toggle the reference point map tool on/off."""
        try:
            from qgis.utils import iface
            if not iface or not iface.mapCanvas():
                QMessageBox.warning(
                    self, "No Map",
                    "Please open a map view first."
                )
                self.add_ref_btn.setChecked(False)
                return

            canvas = iface.mapCanvas()

            if checked:
                # Log storage configuration for debugging
                self.logger.debug(
                    f"[REFERENCE WIDGET] Creating map tool with storage_manager={self._storage_manager is not None}, "
                    f"geopackage_path={self._geopackage_path}"
                )

                # Create and activate the map tool
                self._map_tool = ReferenceMapTool(
                    canvas,
                    self._layer_epsg,
                    storage_manager=self._storage_manager,
                    geopackage_path=self._geopackage_path
                )
                self._map_tool.reference_added.connect(self._on_reference_added)
                self._map_tool.reference_cancelled.connect(self._on_reference_cancelled)
                canvas.setMapTool(self._map_tool)
                self.add_ref_btn.setText("Click map to add point...")
                self.status_label.setText("Click on the map to add a reference point")
            else:
                # Deactivate the tool
                if self._map_tool:
                    canvas.unsetMapTool(self._map_tool)
                    self._map_tool = None
                self.add_ref_btn.setText("Add Reference Point")
                self._update_status()

        except Exception as e:
            self.logger.error(f"[REFERENCE WIDGET] Tool toggle failed: {e}")
            self.add_ref_btn.setChecked(False)

    def _on_reference_added(self, message: str):
        """Handle reference point added."""
        if self._map_tool:
            new_points = self._map_tool.get_reference_points()
            # Add only the new point to the layer (last one in the list)
            if len(new_points) > len(self._reference_points):
                new_point = new_points[-1]
                self._add_feature_to_layer(new_point)

                # Log the current state for debugging
                self.logger.info(
                    f"[REFERENCE WIDGET] Point added: {new_point['name']}, "
                    f"total points in list: {len(new_points)}"
                )

                # If using GeoPackage, verify the feature was saved
                if self._layer and self._geopackage_path:
                    self.logger.info(
                        f"[REFERENCE WIDGET] Layer feature count after add: {self._layer.featureCount()}"
                    )

            self._reference_points = new_points
            self._update_list()
            self._update_status()
            self.reference_points_changed.emit()

        # Deactivate tool after adding
        self.add_ref_btn.setChecked(False)
        self._toggle_reference_tool(False)

    def _on_reference_cancelled(self):
        """Handle reference point cancelled."""
        self.add_ref_btn.setChecked(False)
        self._toggle_reference_tool(False)

    def _update_list(self):
        """Update the reference points list widget."""
        self.ref_list.clear()

        for ref in self._reference_points:
            item_text = f"{ref['name']} ({ref['easting']:.0f}, {ref['northing']:.0f})"
            item = QListWidgetItem(item_text)
            self.ref_list.addItem(item)

        self.clear_btn.setEnabled(len(self._reference_points) > 0)

    @property
    def _layer_display_name(self) -> str:
        """Get the display name for the reference points layer."""
        if self._project_name:
            return f"{self.LAYER_NAME} [{self._project_name}]"
        return self.LAYER_NAME

    def _update_status(self):
        """Update the status label."""
        count = len(self._reference_points)
        if count == 0:
            self.status_label.setText("No reference points added")
        elif count == 1:
            self.status_label.setText("1 reference point added")
        else:
            self.status_label.setText(f"{count} reference points added")

    def _get_or_create_layer(self) -> Optional[QgsVectorLayer]:
        """
        Get or create the reference points layer in QGIS project.

        When a GeoPackage is configured, the layer will be stored persistently
        in the GeoPackage. Otherwise, falls back to a memory layer.

        Returns:
            QgsVectorLayer for reference points, or None if creation failed
        """
        # Check if we already have a valid layer reference
        if self._layer and self._layer.isValid():
            # Verify it's still in the project
            if QgsProject.instance().mapLayer(self._layer.id()):
                # Ensure name is correct (might have changed if project_name was set later)
                if self._layer.name() != self._layer_display_name:
                    self._layer.setName(self._layer_display_name)
                    self.logger.info(
                        f"[REFERENCE WIDGET] Updated layer name to '{self._layer_display_name}'"
                    )
                return self._layer

        # If GeoPackage is configured, try to use it
        if self._storage_manager and self._geopackage_path:
            return self._get_or_create_geopackage_layer()

        # Look for existing layer in project by name (memory layer fallback)
        for layer in QgsProject.instance().mapLayers().values():
            if isinstance(layer, QgsVectorLayer) and layer.name() == self.LAYER_NAME:
                self._layer = layer
                return self._layer

        # Create new memory layer (fallback when no GeoPackage)
        return self._create_memory_layer()

    def _get_or_create_geopackage_layer(self) -> Optional[QgsVectorLayer]:
        """
        Get or create the reference points layer in the GeoPackage.

        Returns:
            QgsVectorLayer from GeoPackage, or None if failed
        """
        from ..managers.claims_storage_manager import ClaimsStorageManager

        self.logger.info(
            f"[REFERENCE WIDGET] Getting GeoPackage layer from: {self._geopackage_path}, "
            f"EPSG: {self._layer_epsg}"
        )

        try:
            # Check if layer already exists in project from this GeoPackage
            layer_uri = f"{self._geopackage_path}|layername={ClaimsStorageManager.REFERENCE_POINTS_TABLE}"
            self.logger.debug(f"[REFERENCE WIDGET] Looking for layer with URI: {layer_uri}")

            import os
            # Normalize paths for comparison (handles Windows backslash vs forward slash)
            normalized_gpkg_path = os.path.normpath(self._geopackage_path).lower()

            for layer in QgsProject.instance().mapLayers().values():
                if isinstance(layer, QgsVectorLayer):
                    # Normalize layer source path for comparison
                    layer_source = layer.source()
                    layer_source_path = layer_source.split('|')[0] if '|' in layer_source else layer_source
                    normalized_layer_path = os.path.normpath(layer_source_path).lower()

                    # Check if source matches (handle path normalization)
                    is_match = (
                        normalized_layer_path == normalized_gpkg_path and
                        f"layername={ClaimsStorageManager.REFERENCE_POINTS_TABLE}".lower() in layer_source.lower()
                    )

                    if is_match:
                        # Check if CRS matches expected EPSG
                        layer_epsg = layer.crs().postgisSrid()
                        if layer_epsg != self._layer_epsg:
                            self.logger.warning(
                                f"[REFERENCE WIDGET] Layer CRS mismatch: layer has EPSG:{layer_epsg}, "
                                f"expected EPSG:{self._layer_epsg}. Will recreate layer."
                            )
                            # Remove the mismatched layer from project
                            QgsProject.instance().removeMapLayer(layer.id())
                            # Recreate with correct CRS (handled below)
                            break

                        # Update layer name if it doesn't match expected display name
                        if layer.name() != self._layer_display_name:
                            layer.setName(self._layer_display_name)
                            self.logger.info(
                                f"[REFERENCE WIDGET] Renamed layer to '{self._layer_display_name}'"
                            )
                        self._layer = layer
                        self.logger.info(
                            f"[REFERENCE WIDGET] Found existing layer in project: {layer.name()}"
                        )
                        return self._layer

            # Load existing layer from GeoPackage with proper display name
            # Pass the EPSG so the layer is created with the correct CRS if it doesn't exist
            self.logger.info(
                f"[REFERENCE WIDGET] Layer not in project, loading from GeoPackage..."
            )

            # First check if the GeoPackage table has wrong CRS and fix it
            self._check_and_fix_geopackage_crs()

            layer = self._storage_manager.get_reference_points_layer(
                self._geopackage_path,
                layer_display_name=self._layer_display_name,
                epsg=self._layer_epsg
            )

            if layer and layer.isValid():
                # Apply styling
                self._apply_layer_style(layer)

                # Add to project if not already there
                if not QgsProject.instance().mapLayer(layer.id()):
                    self._add_layer_to_claims_group(layer)
                    self.logger.info(
                        f"[REFERENCE WIDGET] Added layer '{self._layer_display_name}' to project"
                    )

                # Re-add any migrated features (from CRS fix)
                if hasattr(self, '_features_to_migrate') and self._features_to_migrate:
                    self.logger.info(
                        f"[REFERENCE WIDGET] Re-adding {len(self._features_to_migrate)} migrated features"
                    )
                    for feat_data in self._features_to_migrate:
                        try:
                            self._storage_manager.save_reference_point(
                                QgsPointXY(feat_data['easting'], feat_data['northing']),
                                feat_data['name'],
                                feat_data.get('epsg', self._layer_epsg),
                                self._geopackage_path
                            )
                        except Exception as e:
                            self.logger.error(
                                f"[REFERENCE WIDGET] Failed to migrate feature '{feat_data['name']}': {e}"
                            )
                    # Clear the migration list
                    self._features_to_migrate = []
                    # Reload the layer to show migrated features
                    layer.dataProvider().reloadData()
                    layer.triggerRepaint()

                self._layer = layer
                self.logger.info(
                    f"[REFERENCE WIDGET] Loaded reference points layer '{self._layer_display_name}' "
                    f"from GeoPackage with {layer.featureCount()} features"
                )
                return layer

            self.logger.warning(
                "[REFERENCE WIDGET] Could not load reference points from GeoPackage, "
                "falling back to memory layer"
            )
            return self._create_memory_layer()

        except Exception as e:
            self.logger.error(
                f"[REFERENCE WIDGET] Error loading GeoPackage layer: {e}"
            )
            import traceback
            self.logger.error(traceback.format_exc())
            return self._create_memory_layer()

    def _check_and_fix_geopackage_crs(self):
        """
        Check if the reference_points table in GeoPackage has the correct CRS.

        If the table exists with wrong CRS (e.g., EPSG:4326 when it should be UTM),
        this method will:
        1. Load existing features (which have coordinates stored as-is)
        2. Delete the old table
        3. Recreate with correct CRS
        4. Re-add the features (coordinates remain the same, just in correct CRS now)

        This handles the case where the table was created with default EPSG:4326
        but UTM coordinates were stored.
        """
        from ..managers.claims_storage_manager import ClaimsStorageManager
        import sqlite3

        if not self._geopackage_path or not self._layer_epsg:
            return

        try:
            # Load the table directly to check its CRS
            layer_uri = f"{self._geopackage_path}|layername={ClaimsStorageManager.REFERENCE_POINTS_TABLE}"
            test_layer = QgsVectorLayer(layer_uri, "test", "ogr")

            if not test_layer.isValid():
                # Table doesn't exist yet, nothing to fix
                self.logger.debug("[REFERENCE WIDGET] Reference points table doesn't exist yet")
                return

            table_epsg = test_layer.crs().postgisSrid()
            if table_epsg == self._layer_epsg:
                # CRS is correct, nothing to do
                self.logger.debug(
                    f"[REFERENCE WIDGET] Table CRS is correct: EPSG:{table_epsg}"
                )
                return

            self.logger.warning(
                f"[REFERENCE WIDGET] GeoPackage table has EPSG:{table_epsg}, "
                f"expected EPSG:{self._layer_epsg}. Migrating data..."
            )

            # Read existing features (coordinates are stored as-is)
            existing_features = []
            for feature in test_layer.getFeatures():
                geom = feature.geometry()
                if geom and not geom.isNull():
                    pt = geom.asPoint()
                    existing_features.append({
                        'name': feature.attribute('name'),
                        'easting': pt.x(),
                        'northing': pt.y(),
                        'epsg': feature.attribute('epsg'),
                        'created_at': feature.attribute('created_at'),
                    })

            self.logger.info(
                f"[REFERENCE WIDGET] Found {len(existing_features)} features to migrate"
            )

            # Delete the old table
            test_layer = None  # Release the layer
            self._storage_manager.delete_layer(
                ClaimsStorageManager.REFERENCE_POINTS_TABLE,
                self._geopackage_path
            )

            # The table will be recreated with correct CRS when get_reference_points_layer is called
            # But we need to re-add the features after that

            # Store features to re-add after layer is created
            self._features_to_migrate = existing_features

        except Exception as e:
            self.logger.error(f"[REFERENCE WIDGET] Error checking/fixing CRS: {e}")
            import traceback
            self.logger.error(traceback.format_exc())

    def _create_memory_layer(self) -> Optional[QgsVectorLayer]:
        """
        Create a new memory layer for reference points (fallback).

        Returns:
            QgsVectorLayer (memory), or None if creation failed
        """
        try:
            crs = QgsCoordinateReferenceSystem(f'EPSG:{self._layer_epsg}')
            uri = f"Point?crs={crs.authid()}"
            layer = QgsVectorLayer(uri, self.LAYER_NAME, "memory")

            if not layer.isValid():
                self.logger.error("[REFERENCE WIDGET] Failed to create reference points layer")
                return None

            # Add fields
            fields = QgsFields()
            fields.append(QgsField("name", QVariant.String, len=255))
            fields.append(QgsField("easting", QVariant.Double))
            fields.append(QgsField("northing", QVariant.Double))
            fields.append(QgsField("epsg", QVariant.Int))
            layer.dataProvider().addAttributes(fields.toList())
            layer.updateFields()

            # Apply styling - red diamond markers
            self._apply_layer_style(layer)

            # Add to project in Claims Workflow group
            self._add_layer_to_claims_group(layer)
            self._layer = layer

            self.logger.info("[REFERENCE WIDGET] Created reference points memory layer")
            return layer

        except Exception as e:
            self.logger.error(f"[REFERENCE WIDGET] Error creating memory layer: {e}")
            return None

    def _add_layer_to_claims_group(self, layer: QgsVectorLayer):
        """
        Add a layer to the Claims Workflow group in the QGIS project.

        Args:
            layer: The layer to add
        """
        try:
            project = QgsProject.instance()
            root = project.layerTreeRoot()

            # Find or create the Claims Workflow group
            group = root.findGroup("Claims Workflow")
            if not group:
                group = root.insertGroup(0, "Claims Workflow")

            # Add layer to project (without adding to layer tree automatically)
            project.addMapLayer(layer, False)
            # Add to the group
            group.addLayer(layer)

        except Exception as e:
            self.logger.error(f"[REFERENCE WIDGET] Failed to add layer to group: {e}")
            # Fallback to adding without group
            QgsProject.instance().addMapLayer(layer)

    def _apply_layer_style(self, layer: QgsVectorLayer):
        """
        Apply styling to the reference points layer.

        Args:
            layer: The layer to style
        """
        try:
            from qgis.core import (
                QgsSimpleMarkerSymbolLayer, QgsSymbol, QgsSingleSymbolRenderer,
                QgsPalLayerSettings, QgsTextFormat, QgsVectorLayerSimpleLabeling
            )
            from qgis.PyQt.QtGui import QColor, QFont

            # Create red diamond marker
            symbol = QgsSymbol.defaultSymbol(QgsWkbTypes.PointGeometry)
            marker = QgsSimpleMarkerSymbolLayer()
            marker.setShape(QgsSimpleMarkerSymbolLayer.Diamond)
            marker.setSize(8)
            marker.setColor(QColor(220, 38, 38))  # Red
            marker.setStrokeColor(QColor(127, 29, 29))  # Dark red stroke
            marker.setStrokeWidth(0.5)

            symbol.changeSymbolLayer(0, marker)
            renderer = QgsSingleSymbolRenderer(symbol)
            layer.setRenderer(renderer)

            # Add labels showing the name
            label_settings = QgsPalLayerSettings()
            label_settings.fieldName = "name"
            label_settings.enabled = True

            text_format = QgsTextFormat()
            text_format.setFont(QFont("Arial", 9))
            text_format.setColor(QColor(127, 29, 29))
            label_settings.setFormat(text_format)

            labeling = QgsVectorLayerSimpleLabeling(label_settings)
            layer.setLabeling(labeling)
            layer.setLabelsEnabled(True)

            layer.triggerRepaint()

        except Exception as e:
            self.logger.warning(f"[REFERENCE WIDGET] Could not apply layer style: {e}")

    def _add_feature_to_layer(self, ref_point: Dict[str, Any], from_storage: bool = False):
        """
        Add a reference point feature to the QGIS layer.

        Args:
            ref_point: Reference point dict with name, easting, northing, epsg
            from_storage: If True, point was already saved via storage manager
                         (skip adding to layer to avoid duplicates)
        """
        layer = self._get_or_create_layer()
        if not layer:
            return

        # If using GeoPackage and point was already saved by ReferenceMapTool,
        # reload the layer data from the GeoPackage file
        if self._storage_manager and self._geopackage_path and not from_storage:
            # Point should already be saved to GeoPackage by ReferenceMapTool
            # Force a full reload from the GeoPackage file
            provider = layer.dataProvider()

            # Invalidate the provider's cache and reload from disk
            provider.reloadData()
            provider.updateExtents()

            # Also update the layer's extent and repaint
            layer.updateExtents()
            layer.triggerRepaint()

            # Force map canvas refresh
            try:
                from qgis.utils import iface
                if iface and iface.mapCanvas():
                    iface.mapCanvas().refresh()
            except Exception:
                pass

            self.logger.info(
                f"[REFERENCE WIDGET] Reloaded GeoPackage layer after adding '{ref_point['name']}', "
                f"feature count now: {layer.featureCount()}"
            )
            return

        try:
            feature = QgsFeature(layer.fields())
            point = QgsPointXY(ref_point['easting'], ref_point['northing'])
            feature.setGeometry(QgsGeometry.fromPointXY(point))
            feature.setAttribute("name", ref_point['name'])
            feature.setAttribute("easting", ref_point['easting'])
            feature.setAttribute("northing", ref_point['northing'])
            feature.setAttribute("epsg", ref_point.get('epsg', self._layer_epsg))

            layer.dataProvider().addFeature(feature)
            layer.triggerRepaint()

            self.logger.debug(f"[REFERENCE WIDGET] Added feature to layer: {ref_point['name']}")

        except Exception as e:
            self.logger.error(f"[REFERENCE WIDGET] Failed to add feature to layer: {e}")

    def _clear_layer_features(self):
        """Remove all features from the reference points layer."""
        # Use storage manager if available (clears from GeoPackage)
        if self._storage_manager and self._geopackage_path:
            try:
                self._storage_manager.clear_reference_points(self._geopackage_path)
                self.logger.debug(
                    "[REFERENCE WIDGET] Cleared reference points from GeoPackage"
                )
            except Exception as e:
                self.logger.error(
                    f"[REFERENCE WIDGET] Failed to clear GeoPackage features: {e}"
                )

        # Also clear from the layer directly
        if not self._layer or not self._layer.isValid():
            return

        try:
            feature_ids = [f.id() for f in self._layer.getFeatures()]
            if feature_ids:
                self._layer.dataProvider().deleteFeatures(feature_ids)
                self._layer.triggerRepaint()
                self.logger.debug(
                    f"[REFERENCE WIDGET] Cleared {len(feature_ids)} features from layer"
                )

        except Exception as e:
            self.logger.error(f"[REFERENCE WIDGET] Failed to clear layer features: {e}")

    def _sync_layer_with_points(self):
        """
        Synchronize the QGIS layer with the current reference points list.

        If using GeoPackage storage, the layer should already contain the points
        from the GeoPackage - just ensure the layer is loaded and styled.
        For memory layers, clears and re-adds all points.
        """
        if self._storage_manager and self._geopackage_path:
            # For GeoPackage layers, the data is already in the GeoPackage
            # Just ensure the layer is loaded and visible
            layer = self._get_or_create_layer()
            if layer:
                layer.triggerRepaint()
                self.logger.debug(
                    "[REFERENCE WIDGET] Synced GeoPackage layer with "
                    f"{len(self._reference_points)} reference points"
                )
        else:
            # For memory layers, clear and re-add all points
            self._clear_layer_features()
            for ref_point in self._reference_points:
                self._add_feature_to_layer(ref_point, from_storage=True)

    def _clear_all(self):
        """Clear all reference points."""
        reply = QMessageBox.question(
            self,
            "Clear Reference Points",
            "Are you sure you want to remove all reference points?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            self._reference_points.clear()
            if self._map_tool:
                self._map_tool.clear_reference_points()
            self._clear_layer_features()
            self._update_list()
            self._update_status()
            self.reference_points_changed.emit()

    def set_layer_epsg(self, epsg: int):
        """
        Set the EPSG code for coordinate transformation.

        Args:
            epsg: EPSG code (should be UTM zone)
        """
        self._layer_epsg = epsg
        if self._map_tool:
            self._map_tool.set_layer_epsg(epsg)

    def set_storage(
        self,
        storage_manager: 'ClaimsStorageManager',
        geopackage_path: str,
        project_name: Optional[str] = None
    ):
        """
        Set the storage manager and GeoPackage path for persistent storage.

        When set, reference points will be stored in the GeoPackage instead
        of a memory layer. Call this after the GeoPackage is created.
        Also auto-loads the layer into the QGIS project.

        Args:
            storage_manager: ClaimsStorageManager instance
            geopackage_path: Path to the claims GeoPackage
            project_name: Project name for layer naming (e.g., "GE4 Lode Claims")
        """
        self._storage_manager = storage_manager
        self._geopackage_path = geopackage_path
        self._project_name = project_name

        self.logger.info(
            f"[REFERENCE WIDGET] Storage configured: path={geopackage_path}, "
            f"project_name={project_name}"
        )

        # Remove any existing "Reference Points" memory layers from the project
        # This handles orphaned layers from previous sessions
        layers_to_remove = []
        for layer_id, layer in QgsProject.instance().mapLayers().items():
            if isinstance(layer, QgsVectorLayer):
                # Check if it's a memory-based Reference Points layer
                if (layer.name() == self.LAYER_NAME and
                        layer.dataProvider().name() == 'memory'):
                    layers_to_remove.append(layer_id)
                    self.logger.info(
                        f"[REFERENCE WIDGET] Removing old memory layer: {layer.name()}"
                    )

        for layer_id in layers_to_remove:
            QgsProject.instance().removeMapLayer(layer_id)

        # Clear our reference if it was one of the removed layers
        if self._layer and self._layer.id() in layers_to_remove:
            self._layer = None

        # Auto-load the GeoPackage layer into the project
        layer = self._get_or_create_layer()
        if layer:
            # Ensure the layer has the correct display name with project suffix
            if layer.name() != self._layer_display_name:
                layer.setName(self._layer_display_name)
                self.logger.info(
                    f"[REFERENCE WIDGET] Set layer name to '{self._layer_display_name}'"
                )
            self.logger.info(
                f"[REFERENCE WIDGET] Auto-loaded layer '{self._layer_display_name}' "
                f"with {layer.featureCount()} existing features"
            )

    def get_reference_points(self) -> List[Dict[str, Any]]:
        """
        Get all reference points.

        Returns:
            List of reference point dicts for API submission
        """
        return self._reference_points.copy()

    def set_reference_points(self, points: List[Dict[str, Any]]):
        """
        Set reference points (e.g., loaded from storage).

        Args:
            points: List of reference point dicts
        """
        self._reference_points = points.copy()
        self._sync_layer_with_points()
        self._update_list()
        self._update_status()

    def add_point(self, name: str, easting: float, northing: float, epsg: int):
        """
        Add a single reference point programmatically.

        Args:
            name: Description of the reference point
            easting: Easting coordinate
            northing: Northing coordinate
            epsg: EPSG code for the coordinate system
        """
        ref_point = {
            'name': name,
            'easting': easting,
            'northing': northing,
            'epsg': epsg
        }
        self._reference_points.append(ref_point)

        # Save to GeoPackage if using storage manager
        if self._storage_manager and self._geopackage_path:
            try:
                self._storage_manager.save_reference_point(
                    QgsPointXY(easting, northing),
                    name,
                    epsg,
                    self._geopackage_path
                )
                # Just trigger repaint since point is already in GeoPackage
                layer = self._get_or_create_layer()
                if layer:
                    layer.triggerRepaint()
            except Exception as e:
                self.logger.error(
                    f"[REFERENCE WIDGET] Failed to save point to GeoPackage: {e}"
                )
                # Fall back to adding to layer directly
                self._add_feature_to_layer(ref_point, from_storage=True)
        else:
            # Memory layer - add feature directly
            self._add_feature_to_layer(ref_point, from_storage=True)

        self._update_list()
        self._update_status()
        self.reference_points_changed.emit()

    def clear_points(self):
        """Clear all reference points without confirmation dialog."""
        self._reference_points.clear()
        if self._map_tool:
            self._map_tool.clear_reference_points()
        self._clear_layer_features()
        self._update_list()
        self._update_status()
        self.reference_points_changed.emit()
