# -*- coding: utf-8 -*-
"""
Step 5: Monument Adjustment

Allows users to manipulate monument positions and LM corners before finalization.

For most states:
- User can drag monuments along the centerline
- Centerline layer is displayed for reference

For Idaho/New Mexico:
- User can edit the LM Corner field to designate which corner is Corner 1
- Corner points layer shows all corners
- LM Corners layer highlights the designated LM corner
"""
from typing import List, Optional

from qgis.PyQt.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QFormLayout, QFrame, QScrollArea, QMessageBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QComboBox
)
from qgis.PyQt.QtCore import Qt
from qgis.core import QgsProject, QgsVectorLayer

try:
    from qgis.PyQt import sip
except ImportError:
    import sip

from .step_base import ClaimsStepBase
from ...processors.claims_layer_generator import ClaimsLayerGenerator
from ...utils.logger import PluginLogger


def _is_layer_valid(layer: QgsVectorLayer) -> bool:
    """
    Check if a QgsVectorLayer reference is still valid.

    In PyQGIS, when a layer is deleted from QGIS, the Python object
    still exists but the underlying C++ object is gone. Accessing
    such an object raises RuntimeError.

    Args:
        layer: The layer to check

    Returns:
        True if the layer is valid and can be used, False otherwise
    """
    if layer is None:
        return False
    try:
        # sip.isdeleted() checks if the C++ object has been deleted
        if sip.isdeleted(layer):
            return False
        # Also verify the layer is still valid by accessing a property
        _ = layer.isValid()
        return True
    except (RuntimeError, ReferenceError):
        return False


class ClaimsStep5AdjustWidget(ClaimsStepBase):
    """
    Step 5: Monument Adjustment

    Displays generated layers and allows user manipulation before finalization.
    """

    def get_step_title(self) -> str:
        return "Monument Adjustment"

    def get_step_description(self) -> str:
        return (
            "Review and adjust monument positions. You can drag monuments along the "
            "centerline in the map view. For Idaho and New Mexico, use the table below "
            "to change which corner is designated as the LM corner for each claim."
        )

    def __init__(self, state, claims_manager, parent=None):
        super().__init__(state, claims_manager, parent)
        self.logger = PluginLogger.get_logger()
        # Create storage manager for GeoPackage persistence
        from ...managers.claims_storage_manager import ClaimsStorageManager
        self._storage_manager = ClaimsStorageManager()
        # Pass both claims_manager and storage_manager to layer generator
        self.layer_generator = ClaimsLayerGenerator(
            claims_storage_manager=self._storage_manager,
            claims_manager=claims_manager
        )
        self.generated_layers = {}
        self._layers_generated = False
        self._setup_ui()

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

        # Status/Info panel
        layout.addWidget(self._create_status_panel())

        # Generated layers info
        layout.addWidget(self._create_layers_info())

        # State-specific instructions
        layout.addWidget(self._create_instructions_panel())

        # LM Corner adjustment table (for ID/NM)
        self.lm_corner_group = self._create_lm_corner_group()
        layout.addWidget(self.lm_corner_group)

        # Action buttons (Apply Changes and Reset)
        layout.addWidget(self._create_action_buttons())

        layout.addStretch()

        scroll.setWidget(scroll_content)
        main_layout.addWidget(scroll)

    def _create_status_panel(self) -> QFrame:
        """Create the status panel showing layer generation status."""
        frame = QFrame()
        frame.setObjectName("statusPanel")
        frame.setStyleSheet("""
            QFrame#statusPanel {
                background-color: #f0f9ff;
                border: 1px solid #0284c7;
                border-radius: 8px;
                padding: 12px;
            }
        """)
        layout = QVBoxLayout(frame)
        layout.setSpacing(8)

        self.status_label = QLabel("Layers will be generated when you enter this step.")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("color: #0369a1; font-weight: bold;")
        layout.addWidget(self.status_label)

        self.status_detail = QLabel("")
        self.status_detail.setWordWrap(True)
        self.status_detail.setStyleSheet("color: #0369a1;")
        layout.addWidget(self.status_detail)

        return frame

    def _create_layers_info(self) -> QGroupBox:
        """Create the generated layers information panel."""
        group = QGroupBox("Generated Layers")
        group.setStyleSheet(self._get_group_style())
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        # Layer list
        self.layers_table = QTableWidget()
        self.layers_table.setColumnCount(3)
        self.layers_table.setHorizontalHeaderLabels(["Layer", "Features", "Status"])
        self.layers_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.layers_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.layers_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.layers_table.setMaximumHeight(200)
        self.layers_table.setStyleSheet("""
            QTableWidget {
                border: 1px solid #e5e7eb;
                border-radius: 4px;
            }
            QHeaderView::section {
                background-color: #f3f4f6;
                padding: 8px;
                border: none;
                border-bottom: 1px solid #e5e7eb;
                font-weight: bold;
            }
        """)
        layout.addWidget(self.layers_table)

        return group

    def _create_instructions_panel(self) -> QFrame:
        """Create the instructions panel with state-specific guidance."""
        frame = QFrame()
        frame.setStyleSheet("""
            QFrame {
                background-color: #fefce8;
                border: 1px solid #fde047;
                border-radius: 8px;
                padding: 12px;
            }
        """)
        layout = QVBoxLayout(frame)
        layout.setSpacing(8)

        title = QLabel("How to Adjust Monuments")
        title.setStyleSheet("font-weight: bold; color: #854d0e;")
        layout.addWidget(title)

        self.instructions_label = QLabel()
        self.instructions_label.setWordWrap(True)
        self.instructions_label.setStyleSheet("color: #854d0e;")
        layout.addWidget(self.instructions_label)

        return frame

    def _create_lm_corner_group(self) -> QGroupBox:
        """Create the LM corner adjustment group (for Idaho/New Mexico)."""
        group = QGroupBox("LM Corner Designation (Idaho/New Mexico)")
        group.setStyleSheet(self._get_group_style())
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        # Info
        info_label = QLabel(
            "For Idaho and New Mexico, you can designate which corner of each claim "
            "is the LM (Location Monument) corner. Select a corner from the dropdown "
            "and click 'Apply' to rotate the claim geometry."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet(self._get_info_label_style())
        layout.addWidget(info_label)

        # Claims table with LM corner selector
        self.lm_corner_table = QTableWidget()
        self.lm_corner_table.setColumnCount(3)
        self.lm_corner_table.setHorizontalHeaderLabels(["Claim", "Current LM Corner", "New LM Corner"])
        self.lm_corner_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.lm_corner_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.lm_corner_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.lm_corner_table.setMaximumHeight(200)
        self.lm_corner_table.setStyleSheet("""
            QTableWidget {
                border: 1px solid #e5e7eb;
                border-radius: 4px;
            }
            QHeaderView::section {
                background-color: #f3f4f6;
                padding: 8px;
                border: none;
                border-bottom: 1px solid #e5e7eb;
                font-weight: bold;
            }
        """)
        layout.addWidget(self.lm_corner_table)

        # Apply button
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.apply_lm_btn = QPushButton("Apply LM Corner Changes")
        self.apply_lm_btn.setStyleSheet("""
            QPushButton {
                padding: 8px 16px;
                background-color: #059669;
                color: white;
                border: none;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #047857;
            }
        """)
        self.apply_lm_btn.clicked.connect(self._on_apply_lm_corners)
        btn_layout.addWidget(self.apply_lm_btn)

        layout.addLayout(btn_layout)

        return group

    def _create_action_buttons(self) -> QWidget:
        """Create the action buttons for applying changes and resetting layers."""
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 16, 0, 0)
        layout.setSpacing(12)

        layout.addStretch()

        # Apply Changes button (blue) - recalculates based on user modifications
        self.apply_changes_btn = QPushButton("Apply Changes")
        self.apply_changes_btn.setToolTip(
            "Recalculate all layers based on your current modifications.\n"
            "This preserves your LM corner selections and monument adjustments."
        )
        self.apply_changes_btn.setStyleSheet("""
            QPushButton {
                padding: 10px 20px;
                background-color: #2563eb;
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #1d4ed8;
            }
        """)
        self.apply_changes_btn.clicked.connect(self._on_apply_changes_clicked)
        layout.addWidget(self.apply_changes_btn)

        # Reset All Layers button (red) - resets to original defaults
        self.reset_btn = QPushButton("Reset All Layers")
        self.reset_btn.setToolTip(
            "WARNING: This will discard ALL your adjustments and\n"
            "regenerate layers from the original claim data."
        )
        self.reset_btn.setStyleSheet("""
            QPushButton {
                padding: 10px 20px;
                background-color: #dc2626;
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #b91c1c;
            }
        """)
        self.reset_btn.clicked.connect(self._on_reset_clicked)
        layout.addWidget(self.reset_btn)

        return container

    def _update_instructions(self, state: Optional[str] = None):
        """Update instructions based on detected state."""
        # Normalize state to uppercase for comparison
        normalized_state = state.upper().strip() if state else None
        if normalized_state in ['ID', 'NM']:
            self.instructions_label.setText(
                "Idaho and New Mexico use the 'Monument-as-Corner' system:\n\n"
                "1. The discovery monument IS one of the numbered corners (Corner 1)\n"
                "2. Use the table below to change which corner is designated as the LM corner\n"
                "3. Click 'Apply Changes' to recalculate layers with your modifications\n"
                "4. The claim polygon geometry will be rotated so the new LM corner becomes Corner 1\n\n"
                "Tip: Choose the corner that is most clearly on open, unclaimed federal land."
            )
            self.lm_corner_group.setVisible(True)
        else:
            self.instructions_label.setText(
                "Monument positions can be adjusted:\n\n"
                "1. In the map view, select the 'Monuments' layer and use the vertex tool to drag "
                "monuments along the centerline\n"
                "2. The centerline layer shows the valid range for monument placement\n"
                "3. For Wyoming: sideline monuments are at the center of the long sides\n"
                "4. For Arizona: endline monuments are at the center of the short sides\n\n"
                "After adjusting, click 'Apply Changes' to recalculate dependent layers.\n"
                "Use 'Reset All Layers' only if you want to discard all changes and start over."
            )
            self.lm_corner_group.setVisible(False)

    def _update_layers_table(self):
        """Update the layers table with generated layer info."""
        self.layers_table.setRowCount(0)

        layer_order = [
            (ClaimsLayerGenerator.LODE_CLAIMS_LAYER, "Main claims with QClaims fields"),
            (ClaimsLayerGenerator.CORNER_POINTS_LAYER, "All claim corner points"),
            (ClaimsLayerGenerator.LM_CORNERS_LAYER, "LM corners only (Corner 1)"),
            (ClaimsLayerGenerator.CENTERLINES_LAYER, "Monument placement reference"),
            (ClaimsLayerGenerator.MONUMENTS_LAYER, "Discovery monuments"),
            (ClaimsLayerGenerator.SIDELINE_MONUMENTS_LAYER, "Wyoming sideline monuments"),
            (ClaimsLayerGenerator.ENDLINE_MONUMENTS_LAYER, "Arizona endline monuments"),
        ]

        for layer_name, description in layer_order:
            if layer_name in self.generated_layers:
                layer = self.generated_layers[layer_name]
                # Skip if the layer has been deleted from QGIS
                if not _is_layer_valid(layer):
                    continue
                row = self.layers_table.rowCount()
                self.layers_table.insertRow(row)

                # Layer name
                name_item = QTableWidgetItem(layer_name)
                name_item.setToolTip(description)
                self.layers_table.setItem(row, 0, name_item)

                # Feature count
                count_item = QTableWidgetItem(str(layer.featureCount()))
                count_item.setTextAlignment(Qt.AlignCenter)
                self.layers_table.setItem(row, 1, count_item)

                # Status
                status_item = QTableWidgetItem("Ready")
                status_item.setTextAlignment(Qt.AlignCenter)
                self.layers_table.setItem(row, 2, status_item)

    def _update_lm_corner_table(self):
        """Update the LM corner table with claim data."""
        self.lm_corner_table.setRowCount(0)

        claims_layer = self.state.claims_layer
        if not _is_layer_valid(claims_layer):
            return

        for feature in claims_layer.getFeatures():
            name = feature.attribute('name') or feature.attribute('Name') or ""

            # Get current LM corner
            lm_corner = 1
            if claims_layer.fields().indexOf('LM Corner') >= 0:
                lm_corner = feature.attribute('LM Corner') or 1
            elif claims_layer.fields().indexOf('lm_corner') >= 0:
                lm_corner = feature.attribute('lm_corner') or 1

            row = self.lm_corner_table.rowCount()
            self.lm_corner_table.insertRow(row)

            # Claim name
            name_item = QTableWidgetItem(name)
            name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
            self.lm_corner_table.setItem(row, 0, name_item)

            # Current LM corner
            current_item = QTableWidgetItem(f"Corner {lm_corner}")
            current_item.setTextAlignment(Qt.AlignCenter)
            current_item.setFlags(current_item.flags() & ~Qt.ItemIsEditable)
            self.lm_corner_table.setItem(row, 1, current_item)

            # New LM corner selector
            combo = QComboBox()
            combo.addItem("Corner 1", 1)
            combo.addItem("Corner 2", 2)
            combo.addItem("Corner 3", 3)
            combo.addItem("Corner 4", 4)
            combo.setCurrentIndex(int(lm_corner) - 1)
            self.lm_corner_table.setCellWidget(row, 2, combo)

    def _generate_layers(self):
        """Generate all supporting layers using server-side calculations."""
        claims_layer = self.state.claims_layer
        if not claims_layer:
            self.status_label.setText("No claims layer found!")
            self.status_detail.setText("Please go back to Step 2 and create or select a claims layer.")
            return

        # Configure generator
        self.layer_generator.set_monument_inset(self.state.monument_inset_ft)
        self.layer_generator.set_claims_manager(self.claims_manager)

        if self.state.geopackage_path:
            self.layer_generator.set_geopackage_path(self.state.geopackage_path)

        # Extract project name from claims layer for layer naming
        project_name = None
        layer_name = claims_layer.name()
        if '[' in layer_name and ']' in layer_name:
            start = layer_name.find('[') + 1
            end = layer_name.find(']')
            project_name = layer_name[start:end]
        self.layer_generator.set_project_name(project_name)

        # Detect state
        state = self._detect_state(claims_layer)

        # Update status to show we're fetching from server
        self.status_label.setText("Generating layers from server...")
        self.status_detail.setText("Contacting geodb.io API for layer calculations...")
        # Note: Removed QApplication.processEvents() to prevent heap corruption crashes
        # The UI will update after the blocking network request completes

        # Generate layers via server API (server-only - no local fallback)
        try:
            self.generated_layers = self.layer_generator.generate_layers_from_server(
                claims_layer,
                state=state
            )
        except Exception as e:
            self.status_label.setText("Failed to generate layers!")
            self.status_detail.setText(
                f"Server connection required. Error: {e}\n\n"
                "Please check your internet connection and ensure you are logged in."
            )
            return

        if self.generated_layers:
            # Add layers to project
            self.layer_generator.add_layers_to_project(
                self.generated_layers,
                group_name="Claims Workflow"
            )

            self._layers_generated = True
            self.status_label.setText(f"Generated {len(self.generated_layers)} layers successfully!")
            self.status_detail.setText(
                "Layers have been added to the 'Claims Workflow' group in the Layers panel. "
                "You can now adjust monument positions in the map view."
            )

            # Re-detect state from generated Lode Claims layer (has State field from server)
            lode_claims = self.generated_layers.get(ClaimsLayerGenerator.LODE_CLAIMS_LAYER)
            if _is_layer_valid(lode_claims):
                state = self._detect_state(lode_claims)

            # Update UI
            self._update_layers_table()
            self._update_instructions(state)
            self._update_lm_corner_table()
        else:
            self.status_label.setText("Failed to generate layers!")
            self.status_detail.setText("Check that your claims layer has valid polygon geometries.")

    def _detect_state(self, claims_layer: QgsVectorLayer) -> Optional[str]:
        """Detect state from claims layer."""
        for feature in claims_layer.getFeatures():
            state = None
            if claims_layer.fields().indexOf('state') >= 0:
                state = feature.attribute('state')
            elif claims_layer.fields().indexOf('State') >= 0:
                state = feature.attribute('State')
            if state:
                return state
        return None

    def _on_apply_lm_corners(self):
        """Apply LM corner changes via server API."""
        claims_layer = self.state.claims_layer
        if not claims_layer:
            return

        # Collect changes from GUI widget
        changes = {}
        for row in range(self.lm_corner_table.rowCount()):
            name_item = self.lm_corner_table.item(row, 0)
            combo = self.lm_corner_table.cellWidget(row, 2)

            if not name_item or not combo:
                continue

            claim_name = name_item.text()
            new_corner = combo.currentData()

            # Check if this is a change from current displayed value
            current_item = self.lm_corner_table.item(row, 1)
            current_corner = int(current_item.text().replace("Corner ", ""))

            if new_corner != current_corner:
                changes[claim_name] = new_corner

        # Also check the Lode Claims layer for any claims with LM Corner != 1
        # This handles cases where the user edited the layer directly
        lode_claims = self.generated_layers.get(ClaimsLayerGenerator.LODE_CLAIMS_LAYER)
        if _is_layer_valid(lode_claims):
            # Find LM Corner field
            lm_corner_idx = lode_claims.fields().indexOf('LM Corner')
            if lm_corner_idx < 0:
                lm_corner_idx = lode_claims.fields().indexOf('lm_corner')

            if lm_corner_idx >= 0:
                for feature in lode_claims.getFeatures():
                    name = feature.attribute('Name') or feature.attribute('name') or ""
                    lm_corner = feature.attribute(lm_corner_idx)
                    try:
                        lm_corner_val = int(lm_corner) if lm_corner else 1
                    except (ValueError, TypeError):
                        lm_corner_val = 1

                    # If LM corner is not 1 and not already in changes, add it
                    if lm_corner_val != 1 and name not in changes:
                        changes[name] = lm_corner_val

        if not changes:
            QMessageBox.information(
                self,
                "No Changes",
                "No LM corner changes were detected."
            )
            return

        # Update status
        self.status_label.setText("Applying LM corner changes...")
        self.status_detail.setText("Sending changes to server and regenerating layers...")
        # Note: Removed QApplication.processEvents() to prevent heap corruption crashes

        # Update LM corners via server API using BATCH method (single API call)
        # This is much more efficient than calling one at a time
        changes_applied = False
        try:
            self.logger.info(f"[CLAIMS DEBUG] Batch updating {len(changes)} LM corners: {changes}")
            new_layers = self.layer_generator.update_lm_corners_batch(
                claims_layer,
                changes  # Dict[claim_name, new_corner]
            )
            if new_layers:
                self.logger.info(f"[CLAIMS DEBUG] Got {len(new_layers)} layers from batch update")
                self.generated_layers = new_layers
                changes_applied = True
            else:
                self.logger.warning("[CLAIMS DEBUG] No layers returned from batch update")
        except Exception as e:
            self.logger.error(f"[CLAIMS DEBUG] Exception during LM corner update: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            self.status_label.setText("Failed to apply LM corner changes")
            self.status_detail.setText(
                f"Server connection required. Error: {e}\n\n"
                "Please check your internet connection and ensure you are logged in."
            )
            QMessageBox.warning(
                self,
                "Update Failed",
                f"Failed to apply LM corner changes: {e}\n\n"
                "Server connection is required for this operation."
            )
            return

        if changes_applied:
            # Add updated layers to project
            if self.generated_layers:
                self.layer_generator.add_layers_to_project(
                    self.generated_layers,
                    group_name="Claims Workflow"
                )
                self._update_layers_table()
                self._update_lm_corner_table()

            self.status_label.setText(f"Applied {len(changes)} LM corner change(s)")
            self.status_detail.setText(
                "Layers have been updated. The claim geometries have been rotated "
                "so the new LM corners are now Corner 1."
            )

            QMessageBox.information(
                self,
                "LM Corners Updated",
                f"Applied {len(changes)} LM corner change(s).\n\n"
                "The claim geometries have been rotated and layers regenerated."
            )
        else:
            self.status_label.setText("Failed to apply LM corner changes")
            self.status_detail.setText("Check your network connection and try again.")
            QMessageBox.warning(
                self,
                "Update Failed",
                "Failed to apply LM corner changes. Please try again."
            )

    def _on_apply_changes_clicked(self):
        """
        Apply user changes by recalculating layers based on current modifications.

        This reads the current LM corner values from the claims layer and
        regenerates all derived layers (corners, centerlines, monuments) while
        preserving the user's adjustments.
        """
        claims_layer = self.state.claims_layer
        if not claims_layer:
            QMessageBox.warning(
                self,
                "No Claims Layer",
                "No claims layer found. Please go back to Step 2."
            )
            return

        # Update status
        self.status_label.setText("Applying changes...")
        self.status_detail.setText("Recalculating layers based on your modifications...")
        # Note: Removed QApplication.processEvents() to prevent heap corruption crashes

        # Configure generator
        self.layer_generator.set_monument_inset(self.state.monument_inset_ft)
        self.layer_generator.set_claims_manager(self.claims_manager)

        if self.state.geopackage_path:
            self.layer_generator.set_geopackage_path(self.state.geopackage_path)

        # Extract project name from claims layer for layer naming
        project_name = None
        layer_name = claims_layer.name()
        if '[' in layer_name and ']' in layer_name:
            start = layer_name.find('[') + 1
            end = layer_name.find(']')
            project_name = layer_name[start:end]
        self.layer_generator.set_project_name(project_name)

        # Detect state
        state = self._detect_state(claims_layer)

        # Generate layers via server API using CURRENT claim geometries
        # This preserves any rotations/changes the user has made
        try:
            self.generated_layers = self.layer_generator.generate_layers_from_server(
                claims_layer,
                state=state
            )
        except Exception as e:
            self.status_label.setText("Failed to apply changes!")
            self.status_detail.setText(
                f"Server connection required. Error: {e}\n\n"
                "Please check your internet connection and ensure you are logged in."
            )
            QMessageBox.warning(
                self,
                "Apply Changes Failed",
                f"Failed to recalculate layers: {e}\n\n"
                "Server connection is required for this operation."
            )
            return

        if self.generated_layers:
            # Add layers to project (replaces existing)
            self.layer_generator.add_layers_to_project(
                self.generated_layers,
                group_name="Claims Workflow"
            )

            self._layers_generated = True
            self.status_label.setText(f"Applied changes - {len(self.generated_layers)} layers updated!")
            self.status_detail.setText(
                "Layers have been recalculated based on your current modifications."
            )

            # Re-detect state from generated Lode Claims layer (has State field from server)
            lode_claims = self.generated_layers.get(ClaimsLayerGenerator.LODE_CLAIMS_LAYER)
            if _is_layer_valid(lode_claims):
                state = self._detect_state(lode_claims)

            # Update UI
            self._update_layers_table()
            self._update_instructions(state)
            self._update_lm_corner_table()
        else:
            self.status_label.setText("Failed to apply changes!")
            self.status_detail.setText("Check that your claims layer has valid polygon geometries.")

    def _on_reset_clicked(self):
        """
        Handle reset button click - shows warning and resets all layers to defaults.

        This discards ALL user modifications and regenerates layers from the
        original claim data stored in the source layer.
        """
        # Create a more prominent warning dialog
        msg_box = QMessageBox(self)
        msg_box.setIcon(QMessageBox.Warning)
        msg_box.setWindowTitle("Reset All Layers?")
        msg_box.setText("Are you sure you want to reset all layers?")
        msg_box.setInformativeText(
            "This will DISCARD all your adjustments including:\n\n"
            "  • LM corner designations\n"
            "  • Monument position changes\n"
            "  • Any other manual modifications\n\n"
            "All layers will be regenerated from the original claim data.\n\n"
            "This action cannot be undone."
        )
        msg_box.setStandardButtons(QMessageBox.Yes | QMessageBox.Cancel)
        msg_box.setDefaultButton(QMessageBox.Cancel)

        # Style the Yes button to be red for emphasis
        yes_btn = msg_box.button(QMessageBox.Yes)
        yes_btn.setText("Reset All")
        yes_btn.setStyleSheet("""
            QPushButton {
                background-color: #dc2626;
                color: white;
                padding: 6px 12px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #b91c1c;
            }
        """)

        reply = msg_box.exec_()

        if reply == QMessageBox.Yes:
            # Reset LM corners to 1 before regenerating
            self._reset_lm_corners_to_default()
            self._generate_layers()

    def _reset_lm_corners_to_default(self):
        """Reset all LM corner values to 1 (default) in the claims layer."""
        claims_layer = self.state.claims_layer
        if not claims_layer:
            return

        # Find the LM Corner field
        lm_corner_idx = claims_layer.fields().indexOf('LM Corner')
        if lm_corner_idx < 0:
            lm_corner_idx = claims_layer.fields().indexOf('lm_corner')

        if lm_corner_idx < 0:
            return  # No LM Corner field exists

        # Reset all LM corners to 1
        claims_layer.startEditing()
        for feature in claims_layer.getFeatures():
            claims_layer.changeAttributeValue(feature.id(), lm_corner_idx, 1)
        claims_layer.commitChanges()

    # =========================================================================
    # ClaimsStepBase Implementation
    # =========================================================================

    def validate(self) -> List[str]:
        """Validate the step."""
        errors = []

        if not self._layers_generated:
            errors.append("Layers must be generated before proceeding")

        if not self.generated_layers:
            errors.append("No layers were generated")

        return errors

    def on_enter(self):
        """Called when step becomes active."""
        self.load_state()

        # Generate layers if not already done
        if not self._layers_generated:
            self._generate_layers()

    def on_leave(self):
        """Called when leaving step."""
        self.save_state()

    def save_state(self):
        """Save widget state to shared state."""
        # Store layer IDs in state for reference (only for valid layers)
        if self.generated_layers:
            layer_ids = {
                name: layer.id()
                for name, layer in self.generated_layers.items()
                if _is_layer_valid(layer)
            }
            # Could store in state if needed for later steps

    def load_state(self):
        """Load widget state from shared state."""
        # Check if layers already exist in project
        self._check_existing_layers()

    def _check_existing_layers(self):
        """Check if generated layers already exist in the project."""
        project = QgsProject.instance()
        existing = {}

        layer_names = [
            ClaimsLayerGenerator.LODE_CLAIMS_LAYER,
            ClaimsLayerGenerator.CORNER_POINTS_LAYER,
            ClaimsLayerGenerator.LM_CORNERS_LAYER,
            ClaimsLayerGenerator.CENTERLINES_LAYER,
            ClaimsLayerGenerator.MONUMENTS_LAYER,
            ClaimsLayerGenerator.SIDELINE_MONUMENTS_LAYER,
            ClaimsLayerGenerator.ENDLINE_MONUMENTS_LAYER,
        ]

        for layer in project.mapLayers().values():
            if layer.name() in layer_names:
                existing[layer.name()] = layer

        if existing:
            self.generated_layers = existing
            self._layers_generated = True
            self._update_layers_table()

            # Detect state from generated Lode Claims layer (has State field from server)
            # Fall back to original claims layer if Lode Claims not available
            lode_claims = existing.get(ClaimsLayerGenerator.LODE_CLAIMS_LAYER)
            claims_layer = lode_claims if _is_layer_valid(lode_claims) else self.state.claims_layer
            if _is_layer_valid(claims_layer):
                state = self._detect_state(claims_layer)
                self._update_instructions(state)
                self._update_lm_corner_table()

            self.status_label.setText(f"Found {len(existing)} existing layers")
            self.status_detail.setText(
                "Layers from a previous session were found. "
                "Click 'Regenerate All Layers' to reset them."
            )
