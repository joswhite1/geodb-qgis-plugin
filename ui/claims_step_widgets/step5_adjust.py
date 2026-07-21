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
    QGroupBox, QFrame, QScrollArea, QMessageBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QComboBox, QToolButton
)
from qgis.PyQt.QtCore import Qt
from qgis.core import QgsProject, QgsVectorLayer

from .step_base import ClaimsStepBase
from ...processors.claims_layer_generator import ClaimsLayerGenerator
from ...processors.monument_overrides import read_monument_overrides_from_state
from ...utils.logger import PluginLogger
from ...utils.layer_utils import is_layer_valid
from ...utils.compat import (
    Qt_RightArrow, Qt_DownArrow, Qt_PointingHandCursor, QFrame_NoFrame,
    QHeaderView_Stretch, QHeaderView_ResizeToContents, QMessageBox_Warning,
)
from ...utils.theme import T


class ClaimsStep5AdjustWidget(ClaimsStepBase):
    """
    Step 5: Monument Adjustment

    Displays generated layers and allows user manipulation before finalization.
    """

    def get_step_title(self) -> str:
        return "Review & Adjust"

    def get_step_description(self) -> str:
        return (
            "Review and adjust monument positions. You can drag monuments along the "
            "centerline in the map view. For Idaho and New Mexico, use the table below "
            "to change which corner is designated as the LM corner for each claim."
        )

    def __init__(self, state, claims_manager, parent=None, data_manager=None):
        super().__init__(state, claims_manager, parent, data_manager=data_manager)
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
        self._group_name = self.state.claims_group_name
        self._setup_ui()

    def _setup_ui(self):
        """Set up the step UI."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame_NoFrame)

        scroll_content = QWidget()
        layout = QVBoxLayout(scroll_content)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # Header
        layout.addWidget(self._create_header())

        # Status/Info panel
        layout.addWidget(self._create_status_panel())

        # Claims Layer selection (shows current layer and allows manual override)
        layout.addWidget(self._create_claims_layer_group())

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
        frame.setStyleSheet(f"""
            QFrame#statusPanel {{
                background-color: {T.INFO_BG};
                border: 1px solid {T.INFO};
                border-radius: 8px;
                padding: 12px;
            }}
        """)
        layout = QVBoxLayout(frame)
        layout.setSpacing(8)

        self.status_label = QLabel("Layers will be generated when you enter this step.")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet(f"color: {T.INFO_TEXT}; font-weight: bold;")
        layout.addWidget(self.status_label)

        self.status_detail = QLabel("")
        self.status_detail.setWordWrap(True)
        self.status_detail.setStyleSheet(f"color: {T.INFO_TEXT};")
        layout.addWidget(self.status_detail)

        return frame

    def _create_claims_layer_group(self) -> QGroupBox:
        """Create the claims layer selection group."""
        group = QGroupBox("Claims Layer")
        group.setStyleSheet(self._get_group_style())
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        # Info label
        info_label = QLabel(
            "This is the polygon layer used for geometry validation and document generation. "
            "After layers are generated, this should point to 'Lode Claims'."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet(self._get_info_label_style())
        layout.addWidget(info_label)

        # Layer selector row
        layer_layout = QHBoxLayout()
        layer_layout.addWidget(QLabel("Layer:"))

        self.claims_layer_combo = QComboBox()
        self.claims_layer_combo.setMinimumWidth(200)
        self.claims_layer_combo.currentIndexChanged.connect(self._on_claims_layer_changed)
        layer_layout.addWidget(self.claims_layer_combo, 1)

        refresh_btn = QPushButton("Refresh")
        refresh_btn.setMaximumWidth(80)
        refresh_btn.clicked.connect(self._refresh_claims_layers)
        layer_layout.addWidget(refresh_btn)

        layout.addLayout(layer_layout)

        # Layer info label
        self.claims_layer_info_label = QLabel("")
        self.claims_layer_info_label.setStyleSheet(self._get_info_label_style())
        layout.addWidget(self.claims_layer_info_label)

        return group

    def _refresh_claims_layers(self):
        """Refresh the claims layer combo box with polygon layers from the GeoPackage."""
        import os
        current_id = self.claims_layer_combo.currentData()
        # Block signals while rebuilding the combo to prevent _on_claims_layer_changed
        # from overwriting state.claims_layer with the wrong layer during clear/add
        self.claims_layer_combo.blockSignals(True)
        self.claims_layer_combo.clear()

        gpkg_path = self.state.geopackage_path

        # Collect all polygon layers (for logging)
        all_polygon_layers = []

        for layer_id, layer in QgsProject.instance().mapLayers().items():
            if not isinstance(layer, QgsVectorLayer):
                continue
            if layer.geometryType() != 2:  # Polygon only
                continue

            all_polygon_layers.append((layer.name(), layer_id))

            if gpkg_path:
                # Only show layers from the workflow GeoPackage
                source = layer.source()
                gpkg_norm = os.path.normpath(gpkg_path)
                source_path = os.path.normpath(source.split('|')[0])
                if source_path != gpkg_norm:
                    continue

            self.claims_layer_combo.addItem(layer.name(), layer_id)

        # Select the layer from state.claims_layer_id
        target_id = self.state.claims_layer_id or current_id

        # Log for debugging claims layer selection
        self.logger.info(
            f"[Step5] _refresh_claims_layers: target_id={target_id}, "
            f"state.claims_layer_id={self.state.claims_layer_id}, "
            f"combo count={self.claims_layer_combo.count()}"
        )

        found_target = False
        if target_id:
            for i in range(self.claims_layer_combo.count()):
                if self.claims_layer_combo.itemData(i) == target_id:
                    self.claims_layer_combo.setCurrentIndex(i)
                    found_target = True
                    self.logger.info(f"[Step5] Selected layer at index {i}: {self.claims_layer_combo.itemText(i)}")
                    break

        if not found_target and target_id:
            self.logger.warning(
                f"[Step5] Could not find layer with ID {target_id} in combo box. "
                f"Available polygon layers: {all_polygon_layers}"
            )

        self.claims_layer_combo.blockSignals(False)
        self._update_claims_layer_info()

    def _on_claims_layer_changed(self, index: int):
        """Handle claims layer selection change."""
        layer_id = self.claims_layer_combo.currentData()
        if not layer_id:
            return

        layer = QgsProject.instance().mapLayer(layer_id)
        if isinstance(layer, QgsVectorLayer):
            self.state.claims_layer = layer
            self.logger.info(f"[Step5] User changed claims_layer to: {layer.name()} ({layer.id()})")

        self._update_claims_layer_info()

    def _update_claims_layer_info(self):
        """Update the claims layer info label."""
        layer = self.state.claims_layer
        if layer and is_layer_valid(layer):
            self.claims_layer_info_label.setText(f"{layer.featureCount()} features")
        else:
            self.claims_layer_info_label.setText("No layer selected")

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
        self.layers_table.horizontalHeader().setSectionResizeMode(0, QHeaderView_Stretch)
        self.layers_table.horizontalHeader().setSectionResizeMode(1, QHeaderView_ResizeToContents)
        self.layers_table.horizontalHeader().setSectionResizeMode(2, QHeaderView_ResizeToContents)
        self.layers_table.setMaximumHeight(200)
        self.layers_table.setStyleSheet(f"""
            QTableWidget {{
                border: 1px solid {T.BORDER_SUBTLE};
                border-radius: 4px;
            }}
            QHeaderView::section {{
                background-color: {T.SURFACE_SUNKEN};
                padding: 8px;
                border: none;
                border-bottom: 1px solid {T.BORDER_SUBTLE};
                font-weight: bold;
            }}
        """)
        layout.addWidget(self.layers_table)

        return group

    def _create_instructions_panel(self) -> QFrame:
        """Create the instructions panel with state-specific guidance."""
        frame = QFrame()
        frame.setStyleSheet(f"""
            QFrame {{
                background-color: {T.WARNING_BG};
                border: 1px solid {T.WARNING};
                border-radius: 8px;
                padding: 12px;
            }}
        """)
        layout = QVBoxLayout(frame)
        layout.setSpacing(8)

        title = QLabel("How to Adjust Monuments")
        title.setStyleSheet(f"font-weight: bold; color: {T.WARNING_TEXT};")
        layout.addWidget(title)

        self.instructions_label = QLabel()
        self.instructions_label.setWordWrap(True)
        self.instructions_label.setStyleSheet(f"color: {T.WARNING_TEXT};")
        layout.addWidget(self.instructions_label)

        return frame

    def _create_lm_corner_group(self) -> QFrame:
        """
        Create the LM corner adjustment group as a collapsible panel.

        For Idaho/New Mexico: expanded by default (required workflow step)
        For other states: collapsed by default (available for historic claim matching)
        """
        # Main container frame
        container = QFrame()
        container.setObjectName("lmCornerContainer")
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(0, 0, 0, 0)
        container_layout.setSpacing(0)

        # Header with toggle button
        header = QFrame()
        header.setObjectName("lmCornerHeader")
        header.setStyleSheet(f"""
            QFrame#lmCornerHeader {{
                background-color: {T.SURFACE_SUNKEN};
                border: 1px solid {T.BORDER_SUBTLE};
                border-radius: 8px 8px 0 0;
                padding: 8px;
            }}
        """)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 8, 12, 8)

        # Toggle button (arrow indicator)
        self.lm_toggle_btn = QToolButton()
        self.lm_toggle_btn.setArrowType(Qt_RightArrow)
        self.lm_toggle_btn.setCheckable(True)
        self.lm_toggle_btn.setChecked(False)
        self.lm_toggle_btn.setStyleSheet("""
            QToolButton {
                border: none;
                background: transparent;
            }
        """)
        self.lm_toggle_btn.clicked.connect(self._toggle_lm_corner_panel)
        header_layout.addWidget(self.lm_toggle_btn)

        # Title label
        self.lm_title_label = QLabel("LM Corner / Geometry Rotation")
        self.lm_title_label.setStyleSheet(f"font-weight: bold; color: {T.TEXT_PRIMARY};")
        header_layout.addWidget(self.lm_title_label)

        # State hint label (shows when collapsed)
        self.lm_state_hint = QLabel("")
        self.lm_state_hint.setStyleSheet(f"color: {T.TEXT_MUTED}; font-style: italic;")
        header_layout.addWidget(self.lm_state_hint)

        header_layout.addStretch()

        # Make header clickable
        header.mousePressEvent = lambda e: self._toggle_lm_corner_panel()
        header.setCursor(Qt_PointingHandCursor)

        container_layout.addWidget(header)

        # Collapsible content
        self.lm_content = QFrame()
        self.lm_content.setObjectName("lmCornerContent")
        self.lm_content.setStyleSheet(f"""
            QFrame#lmCornerContent {{
                background-color: {T.SURFACE};
                border: 1px solid {T.BORDER_SUBTLE};
                border-top: none;
                border-radius: 0 0 8px 8px;
                padding: 12px;
            }}
        """)
        content_layout = QVBoxLayout(self.lm_content)
        content_layout.setSpacing(8)

        # Info
        info_label = QLabel(
            "For Idaho and New Mexico, you must designate which corner of each claim "
            "is the LM (Location Monument) corner. For other states, you can use this "
            "to rotate claim geometry to match historic claim positions. Select a corner "
            "from the dropdown and click 'Apply' to rotate the claim geometry."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet(self._get_info_label_style())
        content_layout.addWidget(info_label)

        # Claims table with LM corner selector
        self.lm_corner_table = QTableWidget()
        self.lm_corner_table.setColumnCount(3)
        self.lm_corner_table.setHorizontalHeaderLabels(["Claim", "Current LM Corner", "New LM Corner"])
        self.lm_corner_table.horizontalHeader().setSectionResizeMode(0, QHeaderView_Stretch)
        self.lm_corner_table.horizontalHeader().setSectionResizeMode(1, QHeaderView_ResizeToContents)
        self.lm_corner_table.horizontalHeader().setSectionResizeMode(2, QHeaderView_ResizeToContents)
        self.lm_corner_table.setMaximumHeight(200)
        self.lm_corner_table.setStyleSheet(f"""
            QTableWidget {{
                border: 1px solid {T.BORDER_SUBTLE};
                border-radius: 4px;
            }}
            QHeaderView::section {{
                background-color: {T.SURFACE_SUNKEN};
                padding: 8px;
                border: none;
                border-bottom: 1px solid {T.BORDER_SUBTLE};
                font-weight: bold;
            }}
        """)
        content_layout.addWidget(self.lm_corner_table)

        # Apply button
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.apply_lm_btn = QPushButton("Apply LM Corner Changes")
        self.apply_lm_btn.setStyleSheet(f"""
            QPushButton {{
                padding: 8px 16px;
                background-color: {T.SUCCESS};
                color: {T.TEXT_ON_ACCENT};
                border: none;
                border-radius: 4px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {T.SUCCESS_TEXT};
            }}
        """)
        self.apply_lm_btn.clicked.connect(self._on_apply_lm_corners)
        btn_layout.addWidget(self.apply_lm_btn)

        content_layout.addLayout(btn_layout)

        # Start collapsed
        self.lm_content.setVisible(False)

        container_layout.addWidget(self.lm_content)

        return container

    def _toggle_lm_corner_panel(self):
        """Toggle the LM corner panel expanded/collapsed state."""
        is_expanded = self.lm_content.isVisible()
        self.lm_content.setVisible(not is_expanded)
        self.lm_toggle_btn.setArrowType(Qt_DownArrow if not is_expanded else Qt_RightArrow)
        self.lm_toggle_btn.setChecked(not is_expanded)

    def _set_lm_corner_expanded(self, expanded: bool):
        """Set the LM corner panel to expanded or collapsed state."""
        self.lm_content.setVisible(expanded)
        self.lm_toggle_btn.setArrowType(Qt_DownArrow if expanded else Qt_RightArrow)
        self.lm_toggle_btn.setChecked(expanded)

    def _create_action_buttons(self) -> QWidget:
        """Create the action buttons for resetting layers."""
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 16, 0, 0)
        layout.setSpacing(12)

        layout.addStretch()

        # Reset All Layers button (red) - resets to original defaults
        self.reset_btn = QPushButton("Reset All Layers")
        self.reset_btn.setToolTip(
            "WARNING: This will discard ALL your adjustments and\n"
            "regenerate layers from the original claim data."
        )
        self.reset_btn.setStyleSheet(f"""
            QPushButton {{
                padding: 10px 20px;
                background-color: {T.DANGER};
                color: {T.TEXT_ON_ACCENT};
                border: none;
                border-radius: 6px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {T.DANGER_TEXT};
            }}
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
            # Expand the LM corner panel for ID/NM (required workflow step)
            self._set_lm_corner_expanded(True)
            self.lm_state_hint.setText("(required for ID/NM)")
            self.lm_title_label.setText("LM Corner Designation (Idaho/New Mexico)")
        else:
            self.instructions_label.setText(
                "Monument positions can be adjusted:\n\n"
                "1. In the map view, select the 'Monuments' layer and use the vertex tool to drag "
                "monuments along the centerline\n"
                "2. The centerline layer shows the valid range for monument placement\n"
                "3. For Wyoming: sideline monuments are at the center of the long sides\n"
                "4. For Arizona: endline monuments are at the center of the short sides\n\n"
                "Your adjusted monument positions will be used when generating documents in Step 6.\n"
                "Use 'Reset All Layers' only if you want to discard all changes and start over."
            )
            # Collapse the LM corner panel for other states (available but not required)
            self._set_lm_corner_expanded(False)
            self.lm_state_hint.setText("(expand to rotate geometry)")
            self.lm_title_label.setText("LM Corner / Geometry Rotation")

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
                if not is_layer_valid(layer):
                    continue
                row = self.layers_table.rowCount()
                self.layers_table.insertRow(row)

                # Layer name
                name_item = QTableWidgetItem(layer_name)
                name_item.setToolTip(description)
                self.layers_table.setItem(row, 0, name_item)

                # Feature count
                count_item = QTableWidgetItem(str(layer.featureCount()))
                count_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.layers_table.setItem(row, 1, count_item)

                # Status
                status_item = QTableWidgetItem("Ready")
                status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.layers_table.setItem(row, 2, status_item)

    def _update_lm_corner_table(self):
        """Update the LM corner table with claim data."""
        self.lm_corner_table.setRowCount(0)

        claims_layer = self.state.claims_layer
        if not is_layer_valid(claims_layer):
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
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.lm_corner_table.setItem(row, 0, name_item)

            # Current LM corner
            current_item = QTableWidgetItem(f"Corner {lm_corner}")
            current_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            current_item.setFlags(current_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.lm_corner_table.setItem(row, 1, current_item)

            # New LM corner selector
            combo = QComboBox()
            combo.addItem("Corner 1", 1)
            combo.addItem("Corner 2", 2)
            combo.addItem("Corner 3", 3)
            combo.addItem("Corner 4", 4)
            combo.setCurrentIndex(int(lm_corner) - 1)
            self.lm_corner_table.setCellWidget(row, 2, combo)

    def _remove_old_generated_layers(self):
        """Remove previously generated layers from the QGIS project.

        Prevents duplicate layers when navigating back and regenerating.
        """
        if not self.generated_layers:
            return

        project = QgsProject.instance()
        for layer_key, layer in self.generated_layers.items():
            try:
                if layer and is_layer_valid(layer):
                    project.removeMapLayer(layer.id())
                    self.logger.info(f"[CLAIMS] Removed old layer: {layer_key} ({layer.id()})")
            except RuntimeError:
                pass  # Layer's C++ object already deleted

        # Clear state references to old monument layers
        self.state.monuments_layer_id = None
        self.state.sideline_monuments_layer_id = None
        self.state.endline_monuments_layer_id = None

        self.generated_layers = {}
        self._layers_generated = False

    def _generate_layers(self):
        """Generate all supporting layers using server-side calculations."""
        # Remove any old layers first to prevent duplicates
        self._remove_old_generated_layers()

        claims_layer = self.state.claims_layer
        if not claims_layer:
            self.status_label.setText("No claims layer found!")
            self.status_detail.setText("Please go back to Step 2 and create or select a claims layer.")
            return

        # Configure generator
        self.layer_generator.set_monument_inset(self.state.monument_inset_ft)
        self.layer_generator.set_auto_lm_cluster(self.state.auto_lm_cluster)
        self.layer_generator.set_claims_manager(self.claims_manager)

        # Guarantee a GeoPackage backs the generated layers so they persist
        # across a crash / reopen. If the user never created one in Step 1 this
        # auto-creates one in the default GeodbData directory; otherwise the
        # existing path is used unchanged. Without a path the generator falls
        # back to memory layers, which are lost on close — the exact data-loss
        # this guards against (moving LM corners, QGIS crashes, work gone).
        gpkg_path = self.state.ensure_geopackage()
        if gpkg_path:
            self.layer_generator.set_geopackage_path(gpkg_path)
        else:
            self.logger.warning(
                "[CLAIMS] No GeoPackage available; generated layers will be "
                "memory-only and will not survive a crash or reopen."
            )

        # Extract project name from claims layer for layer naming
        project_name = None
        layer_name = claims_layer.name()
        if '[' in layer_name and ']' in layer_name:
            start = layer_name.find('[') + 1
            end = layer_name.find(']')
            project_name = layer_name[start:end]
        self.layer_generator.set_project_name(project_name)
        self._group_name = f"Claims Workflow [{project_name}]" if project_name else "Claims Workflow"
        self._storage_manager.set_claims_group_name(self._group_name)

        # Detect state
        state = self._detect_state(claims_layer)

        # Update status to show we're fetching from server
        self.status_label.setText("Generating layers from server...")
        self.status_detail.setText("Contacting geodb.io API for layer calculations...")
        # Note: Removed QApplication.processEvents() to prevent heap corruption crashes
        # The UI will update after the blocking network request completes

        # Capture user-moved monument positions from existing layers (if any)
        # so the server's regen doesn't clobber them. First-time generation
        # has no monument layer yet → empty dict, no-op on the server.
        monument_overrides = read_monument_overrides_from_state(self.state, self.logger)

        # Generate layers via server API (server-only - no local fallback)
        try:
            self.generated_layers = self.layer_generator.generate_layers_from_server(
                claims_layer,
                state=state,
                monument_overrides=monument_overrides,
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
                group_name=self._group_name
            )

            self._layers_generated = True
            self.status_label.setText(f"Generated {len(self.generated_layers)} layers successfully!")

            # Update state.claims_layer to point to the new Lode Claims layer
            # This ensures subsequent steps (Step 6, validation, etc.) use the
            # generated layer with all QClaims fields, not the original Step 2 layer
            lode_claims = self.generated_layers.get(ClaimsLayerGenerator.LODE_CLAIMS_LAYER)
            if is_layer_valid(lode_claims):
                self.state.claims_layer = lode_claims
                self.logger.info(f"[CLAIMS] Updated claims_layer to Lode Claims: {lode_claims.id()}")
            self.status_detail.setText(
                "Layers have been added to the 'Claims Workflow' group in the Layers panel. "
                "You can now adjust monument positions in the map view."
            )

            # Store monument layer IDs in state for reading in Step 6
            self._store_monument_layer_references()

            # Re-detect state from generated Lode Claims layer (has State field from server)
            lode_claims = self.generated_layers.get(ClaimsLayerGenerator.LODE_CLAIMS_LAYER)
            if is_layer_valid(lode_claims):
                state = self._detect_state(lode_claims)

            # Update UI
            self._update_layers_table()
            self._update_instructions(state)
            self._update_lm_corner_table()
            # Refresh claims layer dropdown to show the new Lode Claims layer
            self._refresh_claims_layers()
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

    def _store_monument_layer_references(self):
        """
        Store monument layer IDs in state for later reading in Step 6.

        This allows Step 6 (Finalize) to read user-adjusted monument positions
        from the QGIS layers and merge them into the document generation request.
        """
        if not self.generated_layers:
            return

        # Store discovery monuments layer
        monuments = self.generated_layers.get(ClaimsLayerGenerator.MONUMENTS_LAYER)
        if monuments and is_layer_valid(monuments):
            self.state.monuments_layer_id = monuments.id()
            self.logger.info(f"[CLAIMS] Stored monuments_layer_id: {monuments.id()}")

        # Store sideline monuments layer (Wyoming)
        sideline = self.generated_layers.get(ClaimsLayerGenerator.SIDELINE_MONUMENTS_LAYER)
        if sideline and is_layer_valid(sideline):
            self.state.sideline_monuments_layer_id = sideline.id()
            self.logger.info(f"[CLAIMS] Stored sideline_monuments_layer_id: {sideline.id()}")

        # Store endline monuments layer (Arizona)
        endline = self.generated_layers.get(ClaimsLayerGenerator.ENDLINE_MONUMENTS_LAYER)
        if endline and is_layer_valid(endline):
            self.state.endline_monuments_layer_id = endline.id()
            self.logger.info(f"[CLAIMS] Stored endline_monuments_layer_id: {endline.id()}")

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
        if is_layer_valid(lode_claims):
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
            # Preserve user-moved monument positions across the rotation.
            monument_overrides = read_monument_overrides_from_state(
                self.state, self.logger
            )
            new_layers = self.layer_generator.update_lm_corners_batch(
                claims_layer,
                changes,  # Dict[claim_name, new_corner]
                monument_overrides=monument_overrides,
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
                    group_name=self._group_name
                )

                # Update state.claims_layer to point to the new Lode Claims layer
                lode_claims = self.generated_layers.get(ClaimsLayerGenerator.LODE_CLAIMS_LAYER)
                if is_layer_valid(lode_claims):
                    self.state.claims_layer = lode_claims
                    self.logger.info(f"[CLAIMS] Updated claims_layer after LM corner update: {lode_claims.id()}")

                # Store monument layer IDs in state for reading in Step 6
                # CRITICAL: This must be called after LM corner rotation because
                # new layers are created with new IDs. Without this, Step 6 cannot
                # find the monuments layer to read user-adjusted positions.
                self._store_monument_layer_references()

                self._update_layers_table()
                self._update_lm_corner_table()
                # Refresh claims layer dropdown
                self._refresh_claims_layers()

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

    def _on_reset_clicked(self):
        """
        Handle reset button click - shows warning and resets all layers to defaults.

        This discards ALL user modifications and regenerates layers from the
        original claim data stored in the source layer.
        """
        # Create a more prominent warning dialog
        msg_box = QMessageBox(self)
        msg_box.setIcon(QMessageBox_Warning)
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
        msg_box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        msg_box.setDefaultButton(QMessageBox.StandardButton.Cancel)

        # Style the Yes button to be red for emphasis
        yes_btn = msg_box.button(QMessageBox.StandardButton.Yes)
        yes_btn.setText("Reset All")
        yes_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {T.DANGER};
                color: {T.TEXT_ON_ACCENT};
                padding: 6px 12px;
                border-radius: 4px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {T.DANGER_TEXT};
            }}
        """)

        reply = msg_box.exec()

        if reply == QMessageBox.StandardButton.Yes:
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
        # If this step is no longer marked complete (user went back and changed
        # something upstream), force regeneration with fresh data
        if not self.state.is_step_complete(5) and self._layers_generated:
            self.logger.info("[CLAIMS] Step 5 invalidated — will regenerate layers")
            self._layers_generated = False
            # Don't clear generated_layers yet — _generate_layers will remove them

        self.load_state()

        # Refresh the claims layer dropdown
        self._refresh_claims_layers()

        # Generate layers if not already done
        if not self._layers_generated:
            self._generate_layers()

    def on_leave(self):
        """Called when leaving step."""
        self.save_state()

    def save_state(self):
        """Save widget state to shared state."""
        # Layer IDs could be stored in state if needed for later steps
        pass

    def load_state(self):
        """Load widget state from shared state."""
        # Check if layers already exist in project
        self._check_existing_layers()

    def _check_existing_layers(self):
        """Check if generated layers from THIS wizard session already exist.

        Uses state monument layer IDs to verify ownership. If the state has
        monument layer IDs (from a previous Step 5 run in this session), we
        check those specific layers. If the state has no monument IDs (fresh
        wizard or reset), we don't adopt layers by name alone — they likely
        belong to a different claim group.
        """
        project = QgsProject.instance()

        # If state tracks specific monument layer IDs, verify those layers exist
        # This means Step 5 was already run in this session
        has_state_refs = (
            self.state.monuments_layer_id
            or self.state.sideline_monuments_layer_id
            or self.state.endline_monuments_layer_id
        )

        if has_state_refs:
            # Verify the tracked layers still exist in the project
            existing = self._find_layers_by_state_ids(project)
            if existing:
                self.generated_layers = existing
                self._layers_generated = True
                self._update_layers_table()
                self._store_monument_layer_references()

                lode_claims = existing.get(ClaimsLayerGenerator.LODE_CLAIMS_LAYER)
                if is_layer_valid(lode_claims):
                    self.state.claims_layer = lode_claims
                    self.logger.info(f"[CLAIMS] Restored claims_layer from session: {lode_claims.id()}")

                claims_layer = lode_claims if is_layer_valid(lode_claims) else self.state.claims_layer
                if is_layer_valid(claims_layer):
                    state = self._detect_state(claims_layer)
                    self._update_instructions(state)
                    self._update_lm_corner_table()

                self._refresh_claims_layers()

                self.status_label.setText(f"Found {len(existing)} existing layers")
                self.status_detail.setText(
                    "Layers from a previous session were found. "
                    "Click 'Reset All Layers' to regenerate them."
                )
                return

        # No valid state references — don't adopt layers by name alone
        # They may belong to a different claim group
        self._layers_generated = False
        self.generated_layers = {}
        self.logger.info("[CLAIMS] No existing session layers found — will generate fresh")

    def _find_layers_by_state_ids(self, project) -> dict:
        """Find generated layers by their state-tracked IDs and sibling names.

        Uses the monument layer IDs stored in state to find layers that belong
        to this wizard session. Also finds sibling layers (Lode Claims, Corner
        Points, etc.) by matching the project name suffix from the found layers.
        """
        existing = {}

        # Map state IDs to their base names
        id_to_name = {}
        if self.state.monuments_layer_id:
            id_to_name[self.state.monuments_layer_id] = ClaimsLayerGenerator.MONUMENTS_LAYER
        if self.state.sideline_monuments_layer_id:
            id_to_name[self.state.sideline_monuments_layer_id] = ClaimsLayerGenerator.SIDELINE_MONUMENTS_LAYER
        if self.state.endline_monuments_layer_id:
            id_to_name[self.state.endline_monuments_layer_id] = ClaimsLayerGenerator.ENDLINE_MONUMENTS_LAYER

        # Find the tracked layers and detect the project suffix
        project_suffix = None
        for layer_id, base_name in id_to_name.items():
            layer = project.mapLayer(layer_id)
            if layer and is_layer_valid(layer):
                existing[base_name] = layer
                # Detect suffix from layer name (e.g., "Monuments [GE2 Lode]" -> " [GE2 Lode]")
                lname = layer.name()
                if '[' in lname:
                    project_suffix = lname[lname.find('[') - 1:]  # includes the space

        # Also find sibling layers (Lode Claims, Corner Points, etc.)
        # Match by exact name or name with same project suffix
        sibling_names = [
            ClaimsLayerGenerator.LODE_CLAIMS_LAYER,
            ClaimsLayerGenerator.CORNER_POINTS_LAYER,
            ClaimsLayerGenerator.LM_CORNERS_LAYER,
            ClaimsLayerGenerator.CENTERLINES_LAYER,
        ]

        for layer in project.mapLayers().values():
            if not is_layer_valid(layer):
                continue
            lname = layer.name()
            for base_name in sibling_names:
                if base_name in existing:
                    continue  # already found
                # Match exact base name or base name + same project suffix
                if lname == base_name or (project_suffix and lname == base_name + project_suffix):
                    existing[base_name] = layer

        return existing
