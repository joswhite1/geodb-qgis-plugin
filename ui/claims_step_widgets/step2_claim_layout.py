# -*- coding: utf-8 -*-
"""
Step 2: Claim Layout

Handles:
- Create claim grid (rows, cols, base name, azimuth)
- Move claim layout (drag tool)
- Renumber claims (auto-number)
- Rename claim layout
"""
from typing import List, Optional

from qgis.PyQt.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QFormLayout, QLineEdit, QSpinBox, QDoubleSpinBox,
    QComboBox, QMessageBox, QFrame, QScrollArea
)
from qgis.PyQt.QtCore import Qt
from qgis.core import QgsProject, QgsVectorLayer

from .step_base import ClaimsStepBase


class ClaimsStep2Widget(ClaimsStepBase):
    """
    Step 2: Claim Layout

    Create, adjust, and organize the claim grid.
    """

    def get_step_title(self) -> str:
        return "Claim Layout"

    def get_step_description(self) -> str:
        return (
            "Create a grid of mining claim polygons, or select an existing layer. "
            "Use the tools to number and name your claims."
        )

    def __init__(self, state, claims_manager, parent=None):
        super().__init__(state, claims_manager, parent)
        self._grid_generator = None
        self._grid_processor = None
        self._setup_ui()

    def _get_grid_generator(self):
        """Lazy-load grid generator with GeoPackage support."""
        if self._grid_generator is None:
            from ...processors.grid_generator import GridGenerator
            from ...managers.claims_storage_manager import ClaimsStorageManager

            storage_manager = ClaimsStorageManager()
            self._grid_generator = GridGenerator(
                claims_storage_manager=storage_manager
            )

        # Update GeoPackage path from state
        if self.state.geopackage_path:
            self._grid_generator.set_geopackage_path(self.state.geopackage_path)
        else:
            self._grid_generator.set_geopackage_path(None)

        return self._grid_generator

    def _get_grid_processor(self):
        """Lazy-load grid processor."""
        if self._grid_processor is None:
            from ...processors.grid_processor import GridProcessor
            self._grid_processor = GridProcessor()
        return self._grid_processor

    def _setup_ui(self):
        """Set up the step UI."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        scroll_content = QWidget()
        layout = QVBoxLayout(scroll_content)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # Header
        layout.addWidget(self._create_header())

        # Grid Generator Group
        layout.addWidget(self._create_grid_generator_group())

        # Layer Selection Group
        layout.addWidget(self._create_layer_selection_group())

        # Grid Tools Group
        layout.addWidget(self._create_grid_tools_group())

        layout.addStretch()

        scroll.setWidget(scroll_content)
        main_layout.addWidget(scroll)

    def _create_grid_generator_group(self) -> QGroupBox:
        """Create the grid generator group."""
        group = QGroupBox("Generate Claim Grid")
        group.setStyleSheet(self._get_group_style())
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        # Info
        info_label = QLabel(
            "Generate a rectangular grid of US lode mining claims (600' x 1500'). "
            "The grid will be created at the current map center."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet(self._get_info_label_style())
        layout.addWidget(info_label)

        # Form
        form = QFormLayout()
        form.setSpacing(8)

        # Name prefix
        self.name_prefix_edit = QLineEdit()
        self.name_prefix_edit.setStyleSheet(self._get_input_style())
        self.name_prefix_edit.setPlaceholderText("GE")
        self.name_prefix_edit.setText("GE")
        self.name_prefix_edit.setMaximumWidth(100)
        form.addRow("Name Prefix:", self.name_prefix_edit)

        # Rows and columns
        row_col_layout = QHBoxLayout()

        row_col_layout.addWidget(QLabel("Rows:"))
        self.rows_spin = QSpinBox()
        self.rows_spin.setRange(1, 50)
        self.rows_spin.setValue(2)
        self.rows_spin.setMinimumWidth(60)
        self.rows_spin.setStyleSheet(self._get_input_style())
        row_col_layout.addWidget(self.rows_spin)

        row_col_layout.addSpacing(20)

        row_col_layout.addWidget(QLabel("Columns:"))
        self.cols_spin = QSpinBox()
        self.cols_spin.setRange(1, 50)
        self.cols_spin.setValue(4)
        self.cols_spin.setMinimumWidth(60)
        self.cols_spin.setStyleSheet(self._get_input_style())
        row_col_layout.addWidget(self.cols_spin)

        row_col_layout.addStretch()
        form.addRow("Grid Size:", row_col_layout)

        # Azimuth
        self.azimuth_spin = QDoubleSpinBox()
        self.azimuth_spin.setRange(0, 360)
        self.azimuth_spin.setValue(0)
        self.azimuth_spin.setSuffix("°")
        self.azimuth_spin.setMinimumWidth(100)
        self.azimuth_spin.setStyleSheet(self._get_input_style())
        form.addRow("Long Axis Azimuth:", self.azimuth_spin)

        layout.addLayout(form)

        # Generate button
        btn_layout = QHBoxLayout()

        self.generate_btn = QPushButton("Generate Grid at Map Center")
        self.generate_btn.setStyleSheet(self._get_primary_button_style())
        self.generate_btn.clicked.connect(self._generate_grid)
        btn_layout.addWidget(self.generate_btn)

        btn_layout.addStretch()

        layout.addLayout(btn_layout)

        return group

    def _create_layer_selection_group(self) -> QGroupBox:
        """Create the layer selection group."""
        group = QGroupBox("Claims Layer")
        group.setStyleSheet(self._get_group_style())
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        # Info
        info_label = QLabel(
            "Select the polygon layer containing your claim boundaries. "
            "You can use a generated grid or an existing layer."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet(self._get_info_label_style())
        layout.addWidget(info_label)

        # Layer selector
        layer_layout = QHBoxLayout()

        layer_layout.addWidget(QLabel("Layer:"))

        self.layer_combo = QComboBox()
        self.layer_combo.setMinimumWidth(200)
        self.layer_combo.setStyleSheet(self._get_combo_style())
        self.layer_combo.currentIndexChanged.connect(self._on_layer_changed)
        layer_layout.addWidget(self.layer_combo, 1)

        self.refresh_layers_btn = QPushButton("Refresh")
        self.refresh_layers_btn.setStyleSheet(self._get_secondary_button_style())
        self.refresh_layers_btn.clicked.connect(self._refresh_layers)
        self.refresh_layers_btn.setMaximumWidth(80)
        layer_layout.addWidget(self.refresh_layers_btn)

        layout.addLayout(layer_layout)

        # Layer info
        self.layer_info_label = QLabel("")
        self.layer_info_label.setStyleSheet(self._get_info_label_style())
        layout.addWidget(self.layer_info_label)

        return group

    def _create_grid_tools_group(self) -> QGroupBox:
        """Create the grid tools group."""
        group = QGroupBox("Grid Tools")
        group.setStyleSheet(self._get_group_style())
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        # Info
        info_label = QLabel(
            "Use these tools to organize your claims before processing."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet(self._get_info_label_style())
        layout.addWidget(info_label)

        # Move tool row
        move_layout = QHBoxLayout()

        self.move_grid_btn = QPushButton("Move Grid")
        self.move_grid_btn.setToolTip("Activate map tool to drag the entire grid to a new position")
        self.move_grid_btn.setStyleSheet(self._get_secondary_button_style())
        self.move_grid_btn.clicked.connect(self._activate_move_tool)
        move_layout.addWidget(self.move_grid_btn)

        move_layout.addStretch()

        layout.addLayout(move_layout)

        # Numbering row
        number_layout = QHBoxLayout()

        self.auto_number_btn = QPushButton("Auto-Number Claims")
        self.auto_number_btn.setToolTip(
            "Assign sequential numbers based on position (west to east, north to south)"
        )
        self.auto_number_btn.setStyleSheet(self._get_secondary_button_style())
        self.auto_number_btn.clicked.connect(self._auto_number_claims)
        number_layout.addWidget(self.auto_number_btn)

        self.rename_btn = QPushButton("Rename Claims")
        self.rename_btn.setToolTip("Rename all claims using the name prefix and auto-assigned numbers")
        self.rename_btn.setStyleSheet(self._get_secondary_button_style())
        self.rename_btn.clicked.connect(self._rename_claims)
        number_layout.addWidget(self.rename_btn)

        number_layout.addStretch()

        layout.addLayout(number_layout)

        # Status label
        self.tools_status_label = QLabel("")
        self.tools_status_label.setStyleSheet(self._get_info_label_style())
        layout.addWidget(self.tools_status_label)

        return group

    # =========================================================================
    # Grid Generation
    # =========================================================================

    def _generate_grid(self):
        """Generate a claim grid at the map center."""
        try:
            from qgis.utils import iface
            if not iface or not iface.mapCanvas():
                QMessageBox.warning(self, "No Map", "Please open a map view first.")
                return

            center = iface.mapCanvas().center()
            name_prefix = self.name_prefix_edit.text().strip() or "GE"
            rows = self.rows_spin.value()
            cols = self.cols_spin.value()
            azimuth = self.azimuth_spin.value()

            # Generate grid (automatically adds to project via storage manager if GeoPackage)
            generator = self._get_grid_generator()
            layer = generator.generate_lode_grid(
                center,
                rows,
                cols,
                name_prefix=name_prefix,
                azimuth=azimuth
            )

            # Add to project only if it's a memory layer (not already added by storage manager)
            if not self.state.geopackage_path:
                self._add_layer_to_claims_group(layer)

            # Refresh layer list and select new layer
            self._refresh_layers()

            index = self.layer_combo.findText(layer.name())
            if index >= 0:
                self.layer_combo.setCurrentIndex(index)

            # Store the layer in state
            self.state.claims_layer = layer

            storage_info = " (saved to GeoPackage)" if self.state.geopackage_path else ""
            QMessageBox.information(
                self,
                "Grid Generated",
                f"Generated {rows * cols} lode claims{storage_info}.\n\n"
                "You can now:\n"
                "1. Use 'Move Grid' to reposition the claims\n"
                "2. Edit polygons in QGIS to adjust boundaries\n"
                "3. Use 'Auto-Number' and 'Rename' to organize"
            )

            self.emit_status(f"Generated {rows * cols} claim grid{storage_info}", "success")
            self.emit_validation_changed()

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to generate grid: {e}")
            self.emit_status(f"Grid generation failed: {e}", "error")

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
            from ...utils.logger import PluginLogger
            logger = PluginLogger.get_logger()
            logger.error(f"[Step2] Failed to add layer to group: {e}")
            # Fallback to adding without group
            QgsProject.instance().addMapLayer(layer)

    # =========================================================================
    # Layer Selection
    # =========================================================================

    def _refresh_layers(self):
        """Refresh the layer combo box."""
        current_id = self.layer_combo.currentData()
        self.layer_combo.clear()

        # Find polygon layers
        for layer_id, layer in QgsProject.instance().mapLayers().items():
            if isinstance(layer, QgsVectorLayer):
                if layer.geometryType() == 2:  # Polygon
                    self.layer_combo.addItem(layer.name(), layer_id)

        # Restore selection if possible
        if current_id:
            for i in range(self.layer_combo.count()):
                if self.layer_combo.itemData(i) == current_id:
                    self.layer_combo.setCurrentIndex(i)
                    break

        if self.layer_combo.count() == 0:
            self.layer_info_label.setText("No polygon layers found. Generate a grid above.")
        else:
            self._on_layer_changed(self.layer_combo.currentIndex())

    def _on_layer_changed(self, index: int):
        """Handle layer selection change."""
        layer = self._get_selected_layer()
        if not layer:
            self.layer_info_label.setText("Select a layer")
            self._set_tools_enabled(False)
            self.emit_validation_changed()
            return

        feature_count = layer.featureCount()
        self.layer_info_label.setText(f"{feature_count} features")

        # Store in state
        self.state.claims_layer = layer

        # Enable tools
        self._set_tools_enabled(feature_count > 0)
        self.emit_validation_changed()

    def _get_selected_layer(self) -> Optional[QgsVectorLayer]:
        """Get the currently selected layer."""
        layer_id = self.layer_combo.currentData()
        if not layer_id:
            return None

        layer = QgsProject.instance().mapLayer(layer_id)
        if isinstance(layer, QgsVectorLayer):
            return layer
        return None

    def _set_tools_enabled(self, enabled: bool):
        """Enable or disable grid tools."""
        self.move_grid_btn.setEnabled(enabled)
        self.auto_number_btn.setEnabled(enabled)
        self.rename_btn.setEnabled(enabled)

    # =========================================================================
    # Grid Tools
    # =========================================================================

    def _activate_move_tool(self):
        """Activate the grid move map tool."""
        layer = self._get_selected_layer()
        if not layer:
            QMessageBox.warning(self, "No Layer", "Please select a claims layer first.")
            return

        try:
            from qgis.utils import iface
            from ..grid_move_tool import GridMoveTool

            # Create and activate move tool
            canvas = iface.mapCanvas()
            self._move_tool = GridMoveTool(canvas, layer)
            self._move_tool.grid_moved.connect(self._on_grid_moved)
            canvas.setMapTool(self._move_tool)

            self.tools_status_label.setText("Click and drag on the map to move the grid")
            self.emit_status("Grid move tool activated - click and drag to move", "info")

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to activate move tool: {e}")

    def _on_grid_moved(self, offset_x: float, offset_y: float):
        """Handle grid moved event."""
        self.tools_status_label.setText(f"Grid moved by ({offset_x:.1f}, {offset_y:.1f})")
        self.emit_status("Grid moved successfully", "success")

        # Refresh layer to show changes
        layer = self._get_selected_layer()
        if layer:
            layer.triggerRepaint()

    def _auto_number_claims(self):
        """Auto-number claims based on spatial position."""
        layer = self._get_selected_layer()
        if not layer:
            QMessageBox.warning(self, "No Layer", "Please select a claims layer first.")
            return

        try:
            processor = self._get_grid_processor()
            count = processor.autopopulate_manual_fid(layer)

            QMessageBox.information(
                self,
                "Auto-Number Complete",
                f"Assigned Manual FID to {count} claims.\n\n"
                "Claims are numbered west-to-east, north-to-south.\n\n"
                "Click 'Rename Claims' to apply names and reset FIDs to match."
            )

            self.emit_status(f"Auto-numbered {count} claims", "success")

        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _rename_claims(self):
        """Rename claims using the name prefix and reset FIDs to match."""
        layer = self._get_selected_layer()
        if not layer:
            QMessageBox.warning(self, "No Layer", "Please select a claims layer first.")
            return

        base_name = self.name_prefix_edit.text().strip() or "GE"

        # Store layer name BEFORE any operations that might invalidate the layer
        original_layer_name = layer.name()

        try:
            processor = self._get_grid_processor()
            count = processor.rename_claims(layer, base_name, use_manual_fid=True)

            # Reset FIDs to match Manual_FID order
            # This is critical - claim documents are generated in FID order,
            # so FIDs must match the logical claim numbering
            # NOTE: This removes and re-adds the layer, so our layer reference becomes invalid
            processor.reset_fid_to_match_manual_fid(layer)

            # Refresh the layer list since the layer was removed and re-added
            self._refresh_layers()

            # Try to select the layer with the same name (use stored name, not layer.name())
            for i in range(self.layer_combo.count()):
                if self.layer_combo.itemText(i) == original_layer_name:
                    self.layer_combo.setCurrentIndex(i)
                    break

            # Refresh the canvas
            from qgis.utils import iface
            if iface and iface.mapCanvas():
                iface.mapCanvas().refresh()

            QMessageBox.information(
                self,
                "Rename Complete",
                f"Renamed {count} claims using prefix '{base_name}'.\n\n"
                "FIDs have been reset to match the claim numbering order."
            )

            self.emit_status(f"Renamed {count} claims and reset FIDs", "success")

        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    # =========================================================================
    # ClaimsStepBase Implementation
    # =========================================================================

    def validate(self) -> List[str]:
        """Validate the step."""
        errors = []

        layer = self._get_selected_layer()
        if not layer:
            errors.append("A claims layer must be selected")
        elif layer.featureCount() == 0:
            errors.append("Claims layer must have at least one feature")

        return errors

    def on_enter(self):
        """Called when step becomes active."""
        self._refresh_layers()
        self.load_state()

    def on_leave(self):
        """Called when leaving step."""
        self.save_state()

        # Deactivate move tool if active
        try:
            from qgis.utils import iface
            if hasattr(self, '_move_tool'):
                iface.mapCanvas().unsetMapTool(self._move_tool)
        except Exception:
            pass

    def save_state(self):
        """Save widget state to shared state."""
        self.state.grid_name_prefix = self.name_prefix_edit.text().strip()
        self.state.grid_rows = self.rows_spin.value()
        self.state.grid_cols = self.cols_spin.value()
        self.state.grid_azimuth = self.azimuth_spin.value()

        layer = self._get_selected_layer()
        if layer:
            self.state.claims_layer = layer

    def load_state(self):
        """Load widget state from shared state."""
        self.name_prefix_edit.setText(self.state.grid_name_prefix or "GE")
        self.rows_spin.setValue(self.state.grid_rows)
        self.cols_spin.setValue(self.state.grid_cols)
        self.azimuth_spin.setValue(self.state.grid_azimuth)

        # Try to select the saved layer
        if self.state.claims_layer_id:
            for i in range(self.layer_combo.count()):
                if self.layer_combo.itemData(i) == self.state.claims_layer_id:
                    self.layer_combo.setCurrentIndex(i)
                    break
