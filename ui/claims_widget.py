# -*- coding: utf-8 -*-
"""
Claims tab widget for GeodbIO plugin.

Provides UI for QClaims server-side claims processing:
- License/access status display
- Terms of Service acceptance
- Claims processing (Enterprise/Staff)
- Order submission (Pay-per-claim)
- Document generation
- Layer creation with styling
- GPX export
- Push to server
"""
from typing import Optional, List, Dict, Any
import os
import json
from pathlib import Path

from qgis.PyQt.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QProgressBar, QMessageBox, QFrame, QComboBox,
    QScrollArea, QTableWidget, QTableWidgetItem, QHeaderView,
    QApplication, QSizePolicy, QFileDialog, QSpinBox, QDoubleSpinBox,
    QLineEdit, QFormLayout
)
from qgis.PyQt.QtCore import Qt, pyqtSignal, QUrl
from qgis.PyQt.QtGui import QFont, QColor, QBrush, QDesktopServices
from qgis.PyQt.QtNetwork import QNetworkAccessManager, QNetworkRequest
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsMapLayer, QgsFeature,
    QgsGeometry, QgsField, QgsFields, QgsPointXY,
    QgsCoordinateReferenceSystem, QgsSymbol, QgsSimpleFillSymbolLayer,
    QgsSimpleMarkerSymbolLayer, QgsRendererCategory,
    QgsCategorizedSymbolRenderer, QgsSingleSymbolRenderer,
    QgsMarkerSymbol, QgsFillSymbol,
    QgsPalLayerSettings, QgsVectorLayerSimpleLabeling, QgsTextFormat,
    QgsRuleBasedLabeling
)
from qgis.PyQt.QtCore import QVariant

from ..managers.claims_manager import ClaimsManager
from ..processors.gpx_exporter import GPXExporter
from ..processors.grid_generator import GridGenerator
from ..processors.grid_processor import GridProcessor
from ..processors.corner_alignment import CornerAlignmentProcessor
from ..utils.logger import PluginLogger
from .reference_map_tool import ReferencePointsWidget


class ClaimsWidget(QWidget):
    """
    Claims tab widget for the main plugin dialog.

    Handles:
    - License status display with pricing info
    - TOS acceptance workflow
    - Claims selection from QGIS layers
    - Processing for Enterprise/Staff users
    - Order submission for Pay-per-claim users
    - Document generation
    - Push to server

    Signals:
        status_message: Emitted when status needs to be shown (message, level)
        claims_processed: Emitted when claims are processed (result dict)
        order_submitted: Emitted when order is submitted (order dict)
    """

    status_message = pyqtSignal(str, str)  # (message, level: 'info'/'warning'/'error')
    claims_processed = pyqtSignal(dict)
    order_submitted = pyqtSignal(dict)

    def __init__(self, claims_manager: ClaimsManager, parent=None):
        """
        Initialize claims widget.

        Args:
            claims_manager: ClaimsManager instance
            parent: Parent widget
        """
        super().__init__(parent)
        self.claims_manager = claims_manager
        self.logger = PluginLogger.get_logger()

        # State
        self._current_project_id: Optional[int] = None
        self._current_company_id: Optional[int] = None
        self._access_info: Optional[Dict[str, Any]] = None
        self._processed_claims: List[Dict[str, Any]] = []
        self._processed_waypoints: List[Dict[str, Any]] = []
        self._generated_documents: List[Dict[str, Any]] = []

        # Staff order fulfillment context
        self._fulfillment_order_id: Optional[int] = None
        self._fulfillment_order_type: Optional[str] = None
        self._fulfillment_order_number: Optional[str] = None
        self._fulfillment_claimant_info: Optional[Dict[str, Any]] = None

        # Created layers (for reference)
        self._claims_layer: Optional[QgsVectorLayer] = None
        self._waypoints_layer: Optional[QgsVectorLayer] = None

        # Network manager for downloads
        self._network_manager = QNetworkAccessManager()

        # Reference points (for bearing/distance calculations)
        self._reference_points: List[Dict[str, Any]] = []

        # Grid and alignment processors - pass API client for server-side processing
        api_client = claims_manager.api if claims_manager else None
        self._grid_processor = GridProcessor(api_client=api_client)
        self._corner_processor = CornerAlignmentProcessor(api_client=api_client)

        # Claims storage manager for GeoPackage persistence
        from ..managers.claims_storage_manager import ClaimsStorageManager
        self._claims_storage_manager = ClaimsStorageManager()

        self._setup_ui()

    def _setup_ui(self):
        """Set up the widget UI."""
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(0, 0, 0, 0)

        # Create scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setSpacing(16)
        scroll_layout.setContentsMargins(16, 16, 16, 16)

        # === License Status Group ===
        self.license_group = self._create_license_group()
        scroll_layout.addWidget(self.license_group)

        # === Grid Generator Group (Local, FREE) ===
        self.grid_group = self._create_grid_generator_group()
        scroll_layout.addWidget(self.grid_group)

        # === Claims Selection Group ===
        self.selection_group = self._create_selection_group()
        scroll_layout.addWidget(self.selection_group)

        # === Reference Points Group ===
        self.reference_group = self._create_reference_points_group()
        scroll_layout.addWidget(self.reference_group)

        # === Grid Tools Group ===
        self.grid_tools_group = self._create_grid_tools_group()
        scroll_layout.addWidget(self.grid_tools_group)

        # === Processing Group ===
        self.processing_group = self._create_processing_group()
        scroll_layout.addWidget(self.processing_group)

        # === Witness Waypoints Group ===
        self.witness_group = self._create_witness_waypoints_group()
        scroll_layout.addWidget(self.witness_group)

        # === Documents Group ===
        self.documents_group = self._create_documents_group()
        scroll_layout.addWidget(self.documents_group)

        # === Push to Server Group ===
        self.push_group = self._create_push_group()
        scroll_layout.addWidget(self.push_group)

        scroll_layout.addStretch()

        scroll.setWidget(scroll_content)
        layout.addWidget(scroll)

        # Initially disable most controls
        self._set_controls_enabled(False)

    @property
    def _geopackage_path(self) -> Optional[str]:
        """
        Get GeoPackage path from QGIS project settings.

        This retrieves the claims GeoPackage path that was set up in Step 1
        of the claims wizard workflow.

        Returns:
            Path to the claims GeoPackage, or None if not configured
        """
        path, _ = QgsProject.instance().readEntry('geodb', 'claims/geopackage_path', '')
        return path if path else None

    def _create_license_group(self) -> QGroupBox:
        """Create the license status group."""
        group = QGroupBox("QClaims Access")
        group.setStyleSheet(self._get_group_style())
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        # Status row
        status_layout = QHBoxLayout()

        self.license_status_label = QLabel("Checking access...")
        self.license_status_label.setStyleSheet("font-weight: bold;")
        status_layout.addWidget(self.license_status_label)

        status_layout.addStretch()

        self.refresh_license_btn = QPushButton("Refresh")
        self.refresh_license_btn.setStyleSheet(self._get_secondary_button_style())
        self.refresh_license_btn.clicked.connect(self._refresh_license)
        self.refresh_license_btn.setMaximumWidth(80)
        status_layout.addWidget(self.refresh_license_btn)

        layout.addLayout(status_layout)

        # Details
        self.license_details_label = QLabel("")
        self.license_details_label.setWordWrap(True)
        self.license_details_label.setStyleSheet("color: #6b7280;")
        layout.addWidget(self.license_details_label)

        # TOS status and button
        tos_layout = QHBoxLayout()

        self.tos_status_label = QLabel("Terms of Service: Unknown")
        self.tos_status_label.setStyleSheet("color: #6b7280;")
        tos_layout.addWidget(self.tos_status_label)

        tos_layout.addStretch()

        self.accept_tos_btn = QPushButton("View Terms")
        self.accept_tos_btn.setStyleSheet(self._get_secondary_button_style())
        self.accept_tos_btn.clicked.connect(self._accept_tos)
        self.accept_tos_btn.hide()
        tos_layout.addWidget(self.accept_tos_btn)

        layout.addLayout(tos_layout)

        # Staff orders button (only visible to staff users)
        self.staff_orders_btn = QPushButton("View Pending Orders (Staff)")
        self.staff_orders_btn.setStyleSheet(self._get_staff_button_style())
        self.staff_orders_btn.clicked.connect(self._show_staff_orders)
        self.staff_orders_btn.hide()  # Hidden by default, shown for staff users
        layout.addWidget(self.staff_orders_btn)

        return group

    def _create_grid_generator_group(self) -> QGroupBox:
        """Create the grid generator group (local, FREE)."""
        group = QGroupBox("Generate Claim Grid (FREE)")
        group.setStyleSheet(self._get_group_style())
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        # Info label
        info_label = QLabel(
            "Generate a grid of claim polygons locally. This is FREE and does not "
            "require server processing. Edit the grid, then select it for processing."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: #6b7280; margin-bottom: 8px;")
        layout.addWidget(info_label)

        # Form layout for grid parameters
        form = QFormLayout()
        form.setSpacing(8)

        # Lode claims: 600 x 1500 ft (only type currently supported)
        self.grid_type_combo = None  # Keep attribute for compatibility

        # Name prefix
        self.grid_name_prefix = QLineEdit("GE")
        self.grid_name_prefix.setMaximumWidth(100)
        self.grid_name_prefix.setStyleSheet("""
            QLineEdit {
                padding: 6px 10px;
                border: 1px solid #d1d5db;
                border-radius: 4px;
            }
        """)
        form.addRow("Name Prefix:", self.grid_name_prefix)

        # Rows and columns
        row_col_layout = QHBoxLayout()

        self.grid_rows_spin = QSpinBox()
        self.grid_rows_spin.setRange(1, 50)
        self.grid_rows_spin.setValue(2)
        self.grid_rows_spin.setMinimumWidth(60)
        row_col_layout.addWidget(QLabel("Rows:"))
        row_col_layout.addWidget(self.grid_rows_spin)

        row_col_layout.addSpacing(20)

        self.grid_cols_spin = QSpinBox()
        self.grid_cols_spin.setRange(1, 50)
        self.grid_cols_spin.setValue(4)
        self.grid_cols_spin.setMinimumWidth(60)
        row_col_layout.addWidget(QLabel("Cols:"))
        row_col_layout.addWidget(self.grid_cols_spin)

        row_col_layout.addStretch()
        form.addRow("Grid Size:", row_col_layout)

        # Azimuth (for lode claims)
        self.grid_azimuth_spin = QDoubleSpinBox()
        self.grid_azimuth_spin.setRange(0, 360)
        self.grid_azimuth_spin.setValue(0)
        self.grid_azimuth_spin.setSuffix("°")
        self.grid_azimuth_spin.setMinimumWidth(80)
        self.azimuth_label = QLabel("Azimuth:")
        form.addRow(self.azimuth_label, self.grid_azimuth_spin)

        layout.addLayout(form)

        # Instructions
        instructions = QLabel(
            "Click on the map to set the NW corner, then click 'Generate Grid'."
        )
        instructions.setWordWrap(True)
        instructions.setStyleSheet("color: #059669; font-style: italic; margin-top: 8px;")
        layout.addWidget(instructions)

        # Button row
        btn_layout = QHBoxLayout()

        self.generate_grid_btn = QPushButton("Generate Grid at Map Center")
        self.generate_grid_btn.setStyleSheet(self._get_success_button_style())
        self.generate_grid_btn.clicked.connect(self._generate_grid)
        self.generate_grid_btn.setToolTip("Generate a claim grid at the current map center")
        btn_layout.addWidget(self.generate_grid_btn)

        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        return group

    def _on_grid_type_changed(self, index: int):
        """Handle grid type change (kept for compatibility, lode only)."""
        # Only lode claims supported - azimuth always visible
        self.grid_azimuth_spin.setVisible(True)
        self.azimuth_label.setVisible(True)

    def _generate_grid(self):
        """Generate a claim grid at the map center."""
        try:
            # Get map center
            canvas = QgsProject.instance().instance()
            if hasattr(canvas, 'mapCanvas'):
                map_canvas = canvas.mapCanvas()
                center = map_canvas.center()
            else:
                # Fallback - use a default or get from iface
                from qgis.utils import iface
                if iface and iface.mapCanvas():
                    center = iface.mapCanvas().center()
                else:
                    QMessageBox.warning(
                        self,
                        "No Map",
                        "Could not determine map center. Please open a map view."
                    )
                    return

            # Get parameters (lode claims only)
            name_prefix = self.grid_name_prefix.text() or "GE"
            rows = self.grid_rows_spin.value()
            cols = self.grid_cols_spin.value()
            azimuth = self.grid_azimuth_spin.value()

            # Generate lode grid - pass API client for server-side processing
            api_client = self.claims_manager.api if self.claims_manager else None
            generator = GridGenerator(api_client=api_client)
            layer = generator.generate_lode_grid(
                center,
                rows,
                cols,
                name_prefix=name_prefix,
                azimuth=azimuth
            )

            # Add to project
            QgsProject.instance().addMapLayer(layer)

            # Update layer combo
            self._update_layer_combo()

            # Select the new layer
            index = self.layer_combo.findText(layer.name())
            if index >= 0:
                self.layer_combo.setCurrentIndex(index)

            QMessageBox.information(
                self,
                "Grid Generated",
                f"Generated {rows * cols} lode claims.\n\n"
                "The claims have been added to the map. You can now:\n"
                "1. Edit the polygons to adjust boundaries\n"
                "2. Select the layer and click 'Process Claims'"
            )

            self.status_message.emit(f"Generated {rows * cols} claim grid", "info")

        except Exception as e:
            self.logger.error(f"[QCLAIMS UI] Grid generation failed: {e}")
            QMessageBox.critical(self, "Error", f"Failed to generate grid: {e}")

    def _create_selection_group(self) -> QGroupBox:
        """Create the claims selection group."""
        group = QGroupBox("Select Claims")
        group.setStyleSheet(self._get_group_style())
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        # Layer selector
        layer_layout = QHBoxLayout()

        layer_label = QLabel("Claims Layer:")
        layer_label.setStyleSheet("font-weight: bold;")
        layer_layout.addWidget(layer_label)

        self.layer_combo = QComboBox()
        self.layer_combo.setMinimumWidth(200)
        self.layer_combo.setStyleSheet(self._get_combo_style())
        self.layer_combo.currentIndexChanged.connect(self._on_layer_changed)
        layer_layout.addWidget(self.layer_combo)

        self.refresh_layers_btn = QPushButton("Refresh")
        self.refresh_layers_btn.setStyleSheet(self._get_secondary_button_style())
        self.refresh_layers_btn.clicked.connect(self._refresh_layers)
        self.refresh_layers_btn.setMaximumWidth(80)
        layer_layout.addWidget(self.refresh_layers_btn)

        layer_layout.addStretch()

        layout.addLayout(layer_layout)

        # Selection info
        self.selection_info_label = QLabel("Select a polygon layer with claim boundaries")
        self.selection_info_label.setStyleSheet("color: #6b7280;")
        layout.addWidget(self.selection_info_label)

        # Use selected features checkbox (simulated with button)
        selection_layout = QHBoxLayout()

        self.use_selected_btn = QPushButton("Use Selected Features Only")
        self.use_selected_btn.setCheckable(True)
        self.use_selected_btn.setStyleSheet(self._get_toggle_button_style())
        selection_layout.addWidget(self.use_selected_btn)

        selection_layout.addStretch()

        layout.addLayout(selection_layout)

        return group

    def _create_reference_points_group(self) -> QGroupBox:
        """Create the reference points group."""
        group = QGroupBox("Reference Points")
        group.setStyleSheet(self._get_group_style())
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        # Use the ReferencePointsWidget
        self.reference_widget = ReferencePointsWidget()
        self.reference_widget.reference_points_changed.connect(
            self._on_reference_points_changed
        )
        layout.addWidget(self.reference_widget)

        return group

    def _create_grid_tools_group(self) -> QGroupBox:
        """Create the grid tools group for advanced grid manipulation."""
        group = QGroupBox("Grid Tools")
        group.setStyleSheet(self._get_group_style())
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        # Info
        info_label = QLabel(
            "Tools for organizing and validating claim grids before processing."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: #6b7280; font-size: 12px;")
        layout.addWidget(info_label)

        # Button row 1: Ordering tools
        order_layout = QHBoxLayout()

        self.auto_number_btn = QPushButton("Auto-Number Claims")
        self.auto_number_btn.setToolTip(
            "Assign sequential numbers to claims based on position (west to east, north to south)"
        )
        self.auto_number_btn.setStyleSheet(self._get_secondary_button_style())
        self.auto_number_btn.clicked.connect(self._auto_number_claims)
        order_layout.addWidget(self.auto_number_btn)

        self.rename_claims_btn = QPushButton("Rename Claims")
        self.rename_claims_btn.setToolTip(
            "Rename all claims using the grid name prefix and auto-assigned numbers"
        )
        self.rename_claims_btn.setStyleSheet(self._get_secondary_button_style())
        self.rename_claims_btn.clicked.connect(self._rename_claims)
        order_layout.addWidget(self.rename_claims_btn)

        order_layout.addStretch()
        layout.addLayout(order_layout)

        # Button row 2: Validation tools
        validate_layout = QHBoxLayout()

        self.validate_grid_btn = QPushButton("Validate Grid")
        self.validate_grid_btn.setToolTip(
            "Check for common issues: non-rectangular claims, wrong corner counts, etc."
        )
        self.validate_grid_btn.setStyleSheet(self._get_secondary_button_style())
        self.validate_grid_btn.clicked.connect(self._validate_grid)
        validate_layout.addWidget(self.validate_grid_btn)

        self.align_corners_btn = QPushButton("Align Corners")
        self.align_corners_btn.setToolTip(
            "Snap nearby corners to shared positions (for proper neighbor detection)"
        )
        self.align_corners_btn.setStyleSheet(self._get_secondary_button_style())
        self.align_corners_btn.clicked.connect(self._align_corners)
        validate_layout.addWidget(self.align_corners_btn)

        validate_layout.addStretch()
        layout.addLayout(validate_layout)

        # LM Corner section (for Idaho/New Mexico)
        lm_frame = QFrame()
        lm_frame.setStyleSheet("""
            QFrame {
                border: 1px solid #e5e7eb;
                border-radius: 4px;
                padding: 8px;
                background-color: #f9fafb;
            }
        """)
        lm_layout = QVBoxLayout(lm_frame)
        lm_layout.setContentsMargins(8, 8, 8, 8)
        lm_layout.setSpacing(4)

        lm_header = QLabel("<b>LM Corner Designation</b> (Idaho/New Mexico only)")
        lm_header.setStyleSheet("font-size: 12px;")
        lm_layout.addWidget(lm_header)

        lm_info = QLabel(
            "In ID and NM, you designate which corner the discovery monument is relative to. "
            "Default is Corner 1 (SW). Change this after processing if needed."
        )
        lm_info.setWordWrap(True)
        lm_info.setStyleSheet("color: #6b7280; font-size: 11px;")
        lm_layout.addWidget(lm_info)

        lm_btn_layout = QHBoxLayout()
        self.update_lm_btn = QPushButton("Update LM Corners")
        self.update_lm_btn.setToolTip(
            "Send updated LM corner designations to server (recalculates monuments)"
        )
        self.update_lm_btn.setStyleSheet(self._get_secondary_button_style())
        self.update_lm_btn.clicked.connect(self._update_lm_corners)
        self.update_lm_btn.setEnabled(False)  # Enable after processing
        lm_btn_layout.addWidget(self.update_lm_btn)
        lm_btn_layout.addStretch()
        lm_layout.addLayout(lm_btn_layout)

        layout.addWidget(lm_frame)

        return group

    def _on_reference_points_changed(self):
        """Handle reference points list change."""
        if hasattr(self, 'reference_widget'):
            self._reference_points = self.reference_widget.get_reference_points()
            self.logger.debug(
                f"[QCLAIMS UI] Reference points updated: {len(self._reference_points)}"
            )

    def _auto_number_claims(self):
        """Auto-number claims based on spatial position."""
        layer = self._get_selected_layer()
        if not layer:
            QMessageBox.warning(
                self, "No Layer",
                "Please select a claims layer first."
            )
            return

        try:
            count = self._grid_processor.autopopulate_manual_fid(layer)
            QMessageBox.information(
                self, "Auto-Number Complete",
                f"Assigned Manual FID to {count} claims.\n\n"
                "Claims are numbered west-to-east, north-to-south."
            )
            self.status_message.emit(f"Auto-numbered {count} claims", "info")
        except Exception as e:
            self.logger.error(f"[QCLAIMS UI] Auto-number failed: {e}")
            QMessageBox.critical(self, "Error", str(e))

    def _rename_claims(self):
        """Rename claims using the grid name prefix."""
        layer = self._get_selected_layer()
        if not layer:
            QMessageBox.warning(
                self, "No Layer",
                "Please select a claims layer first."
            )
            return

        base_name = self.grid_name_prefix.text() or "GE"

        try:
            count = self._grid_processor.rename_claims(
                layer, base_name, use_manual_fid=True
            )
            QMessageBox.information(
                self, "Rename Complete",
                f"Renamed {count} claims using prefix '{base_name}'."
            )
            self.status_message.emit(f"Renamed {count} claims", "info")
        except Exception as e:
            self.logger.error(f"[QCLAIMS UI] Rename failed: {e}")
            QMessageBox.critical(self, "Error", str(e))

    def _validate_grid(self):
        """Validate the claims grid."""
        layer = self._get_selected_layer()
        if not layer:
            QMessageBox.warning(
                self, "No Layer",
                "Please select a claims layer first."
            )
            return

        try:
            issues = self._grid_processor.validate_grid_geometry(layer)

            if not issues:
                QMessageBox.information(
                    self, "Validation Passed",
                    "No issues found. All claims appear to be valid."
                )
                self.status_message.emit("Grid validation passed", "info")
            else:
                # Format issues
                errors = [i for i in issues if i.get('severity') == 'error']
                warnings = [i for i in issues if i.get('severity') == 'warning']

                msg = f"Found {len(errors)} error(s) and {len(warnings)} warning(s):\n\n"

                for issue in issues[:10]:  # Limit to first 10
                    name = issue.get('name', 'Unknown')
                    desc = issue.get('issue', '')
                    severity = issue.get('severity', 'warning').upper()
                    msg += f"[{severity}] {name}: {desc}\n"

                if len(issues) > 10:
                    msg += f"\n... and {len(issues) - 10} more issues."

                QMessageBox.warning(self, "Validation Issues", msg)
                self.status_message.emit(
                    f"Found {len(issues)} validation issues", "warning"
                )

        except Exception as e:
            self.logger.error(f"[QCLAIMS UI] Validation failed: {e}")
            QMessageBox.critical(self, "Error", str(e))

    def _align_corners(self):
        """Align nearby corners to shared positions."""
        layer = self._get_selected_layer()
        if not layer:
            QMessageBox.warning(
                self, "No Layer",
                "Please select a claims layer first."
            )
            return

        reply = QMessageBox.question(
            self,
            "Align Corners",
            "This will modify the layer geometry to align corners that are "
            "within 1 meter of each other.\n\n"
            "This helps ensure adjacent claims share exact corner coordinates.\n\n"
            "Continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply != QMessageBox.Yes:
            return

        try:
            result = self._corner_processor.align_corners(layer, tolerance_m=1.0)

            QMessageBox.information(
                self, "Alignment Complete",
                f"Found {result['clusters_found']} corner clusters.\n"
                f"Moved {result['corners_moved']} corners.\n"
                f"Maximum adjustment: {result['max_adjustment']:.3f}m"
            )
            self.status_message.emit(
                f"Aligned {result['corners_moved']} corners", "info"
            )

            # Refresh layer display
            layer.triggerRepaint()

        except Exception as e:
            self.logger.error(f"[QCLAIMS UI] Corner alignment failed: {e}")
            QMessageBox.critical(self, "Error", str(e))

    def _update_lm_corners(self):
        """Update LM corner designations on server."""
        if not self._processed_claims:
            QMessageBox.warning(
                self, "No Processed Claims",
                "Process claims first before updating LM corners."
            )
            return

        # For now, show info message - full implementation requires server endpoint
        QMessageBox.information(
            self, "LM Corner Update",
            "LM corner updates are sent to the server to recalculate monument positions.\n\n"
            "This feature is used in Idaho and New Mexico where you designate "
            "which corner the discovery monument is relative to.\n\n"
            "Edit the 'LM Corner' field in the claims layer, then click this button."
        )
        self.status_message.emit("LM corner update not yet fully implemented", "warning")

    def _create_processing_group(self) -> QGroupBox:
        """Create the claims processing group."""
        group = QGroupBox("Process Claims")
        group.setStyleSheet(self._get_group_style())
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        # Info text
        self.processing_info_label = QLabel(
            "Server-side processing calculates PLSS descriptions, corner coordinates, "
            "monument locations, and filing deadlines based on state requirements."
        )
        self.processing_info_label.setWordWrap(True)
        self.processing_info_label.setStyleSheet("color: #6b7280;")
        layout.addWidget(self.processing_info_label)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setStyleSheet(self._get_progress_style())
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)

        # Buttons
        button_layout = QHBoxLayout()

        self.process_btn = QPushButton("Process Claims")
        self.process_btn.setStyleSheet(self._get_primary_button_style())
        self.process_btn.clicked.connect(self._process_claims)
        self.process_btn.setEnabled(False)
        button_layout.addWidget(self.process_btn)

        # For pay-per-claim users, show pricing info
        self.pricing_label = QLabel("")
        self.pricing_label.setStyleSheet("color: #2563eb; font-weight: bold;")
        button_layout.addWidget(self.pricing_label)

        button_layout.addStretch()

        layout.addLayout(button_layout)

        # Results table (hidden initially)
        self.results_group = QGroupBox("Processing Results")
        self.results_group.setStyleSheet(self._get_group_style())
        results_layout = QVBoxLayout(self.results_group)

        self.results_table = QTableWidget()
        self.results_table.setColumnCount(4)
        self.results_table.setHorizontalHeaderLabels(["Claim", "State", "PLSS", "Corners"])
        self.results_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.results_table.verticalHeader().setVisible(False)
        self.results_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.results_table.setMaximumHeight(150)
        self.results_table.setStyleSheet(self._get_table_style())
        results_layout.addWidget(self.results_table)

        self.results_group.hide()
        layout.addWidget(self.results_group)

        return group

    def _create_witness_waypoints_group(self) -> QGroupBox:
        """Create the witness waypoints group for adding witness monuments."""
        group = QGroupBox("Witness Waypoints")
        group.setStyleSheet(self._get_group_style())
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        # Info label with detailed instructions
        info_label = QLabel(
            "If any waypoints fall on private land or inaccessible locations, you must "
            "establish WITNESS MONUMENTS. A witness monument is placed at an accessible "
            "location on public land and references the actual corner location."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: #6b7280;")
        layout.addWidget(info_label)

        # Instructions frame
        instructions_frame = QFrame()
        instructions_frame.setStyleSheet("""
            QFrame {
                border: 1px solid #fbbf24;
                border-radius: 4px;
                padding: 8px;
                background-color: #fffbeb;
            }
        """)
        instr_layout = QVBoxLayout(instructions_frame)
        instr_layout.setContentsMargins(8, 8, 8, 8)
        instr_layout.setSpacing(4)

        instr_header = QLabel("<b>How to add witness waypoints:</b>")
        instr_header.setStyleSheet("font-size: 12px; color: #92400e;")
        instr_layout.addWidget(instr_header)

        instr_steps = QLabel(
            "1. Open the Waypoints layer for editing in QGIS\n"
            "2. Add a new point feature where the witness point should be\n"
            "3. Enter only the Name (e.g., 'WIT 1' or 'WP 1')\n"
            "4. Click 'Update Waypoint Table' to auto-fill coordinates and symbol"
        )
        instr_steps.setWordWrap(True)
        instr_steps.setStyleSheet("color: #92400e; font-size: 11px;")
        instr_layout.addWidget(instr_steps)

        layout.addWidget(instructions_frame)

        # Note about naming
        note_label = QLabel(
            "<i>Note: Fill in the Name field for new waypoints before clicking Update.</i>"
        )
        note_label.setWordWrap(True)
        note_label.setStyleSheet("color: #6b7280; font-size: 11px; margin-top: 4px;")
        layout.addWidget(note_label)

        # Button row
        btn_layout = QHBoxLayout()

        self.update_waypoints_btn = QPushButton("Update Waypoint Table")
        self.update_waypoints_btn.setToolTip(
            "Update manually-added waypoint rows with coordinates, symbol (Navaid, White), "
            "and other fields"
        )
        self.update_waypoints_btn.setStyleSheet(self._get_secondary_button_style())
        self.update_waypoints_btn.clicked.connect(self._update_waypoints_table)
        self.update_waypoints_btn.setEnabled(False)
        btn_layout.addWidget(self.update_waypoints_btn)

        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        return group

    def _create_documents_group(self) -> QGroupBox:
        """Create the documents group."""
        group = QGroupBox("Export & Documents")
        group.setStyleSheet(self._get_group_style())
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        # Info
        info_label = QLabel(
            "Generate location notices and export waypoints for field staking."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: #6b7280;")
        layout.addWidget(info_label)

        # Documents row
        docs_layout = QHBoxLayout()

        self.generate_docs_btn = QPushButton("Generate Location Notices")
        self.generate_docs_btn.setStyleSheet(self._get_secondary_button_style())
        self.generate_docs_btn.clicked.connect(self._generate_documents)
        self.generate_docs_btn.setEnabled(False)
        docs_layout.addWidget(self.generate_docs_btn)

        self.download_docs_btn = QPushButton("Download Documents")
        self.download_docs_btn.setStyleSheet(self._get_secondary_button_style())
        self.download_docs_btn.clicked.connect(self._download_documents)
        self.download_docs_btn.setEnabled(False)
        docs_layout.addWidget(self.download_docs_btn)

        docs_layout.addStretch()

        layout.addLayout(docs_layout)

        # GPX Export row
        gpx_layout = QHBoxLayout()

        self.export_gpx_btn = QPushButton("Export GPX for GPS")
        self.export_gpx_btn.setStyleSheet(self._get_success_button_style())
        self.export_gpx_btn.clicked.connect(self._export_gpx)
        self.export_gpx_btn.setEnabled(False)
        self.export_gpx_btn.setToolTip("Export waypoints to GPX format for handheld GPS devices")
        gpx_layout.addWidget(self.export_gpx_btn)

        gpx_layout.addStretch()

        layout.addLayout(gpx_layout)

        return group

    def _create_push_group(self) -> QGroupBox:
        """Create the push to server group."""
        group = QGroupBox("Push to geoDB Server")
        group.setStyleSheet(self._get_group_style())
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        # Info
        info_label = QLabel(
            "Push processed claims as LandHoldings and waypoints as ClaimStakes "
            "to the geoDB server for mobile staking."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: #6b7280;")
        layout.addWidget(info_label)

        # Button
        button_layout = QHBoxLayout()

        self.push_btn = QPushButton("Push to Server")
        self.push_btn.setStyleSheet(self._get_primary_button_style())
        self.push_btn.clicked.connect(self._push_to_server)
        self.push_btn.setEnabled(False)
        button_layout.addWidget(self.push_btn)

        button_layout.addStretch()

        layout.addLayout(button_layout)

        return group

    # =========================================================================
    # Styles
    # =========================================================================

    def _get_group_style(self) -> str:
        """Get group box style."""
        return """
            QGroupBox {
                font-weight: bold;
                border: 1px solid #e5e7eb;
                border-radius: 8px;
                margin-top: 12px;
                padding-top: 16px;
                background-color: white;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 8px;
                color: #374151;
            }
        """

    def _get_primary_button_style(self) -> str:
        """Get primary button style."""
        return """
            QPushButton {
                padding: 10px 20px;
                background-color: #2563eb;
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: bold;
                font-size: 14px;
                min-width: 120px;
            }
            QPushButton:hover {
                background-color: #1d4ed8;
            }
            QPushButton:pressed {
                background-color: #1e40af;
            }
            QPushButton:disabled {
                background-color: #93c5fd;
            }
        """

    def _get_secondary_button_style(self) -> str:
        """Get secondary button style."""
        return """
            QPushButton {
                padding: 8px 16px;
                background-color: #ffffff;
                color: #374151;
                border: 1px solid #d1d5db;
                border-radius: 6px;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #f9fafb;
                border-color: #9ca3af;
            }
            QPushButton:pressed {
                background-color: #f3f4f6;
            }
            QPushButton:disabled {
                background-color: #f3f4f6;
                color: #9ca3af;
            }
        """

    def _get_warning_button_style(self) -> str:
        """Get warning button style."""
        return """
            QPushButton {
                padding: 8px 16px;
                background-color: #f59e0b;
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: bold;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #d97706;
            }
            QPushButton:pressed {
                background-color: #b45309;
            }
        """

    def _get_staff_button_style(self) -> str:
        """Get staff-specific button style (purple)."""
        return """
            QPushButton {
                padding: 10px 16px;
                background-color: #7c3aed;
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: bold;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #6d28d9;
            }
            QPushButton:pressed {
                background-color: #5b21b6;
            }
        """

    def _get_success_button_style(self) -> str:
        """Get success button style (green)."""
        return """
            QPushButton {
                padding: 8px 16px;
                background-color: #059669;
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: bold;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #047857;
            }
            QPushButton:pressed {
                background-color: #065f46;
            }
            QPushButton:disabled {
                background-color: #a7f3d0;
                color: #6b7280;
            }
        """

    def _get_toggle_button_style(self) -> str:
        """Get toggle button style."""
        return """
            QPushButton {
                padding: 8px 16px;
                background-color: #f3f4f6;
                color: #374151;
                border: 1px solid #d1d5db;
                border-radius: 6px;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #e5e7eb;
            }
            QPushButton:checked {
                background-color: #dbeafe;
                border-color: #2563eb;
                color: #1d4ed8;
            }
        """

    def _get_combo_style(self) -> str:
        """Get combo box style."""
        return """
            QComboBox {
                padding: 8px 12px;
                border: 1px solid #d1d5db;
                border-radius: 6px;
                background-color: white;
                font-size: 13px;
            }
            QComboBox:focus {
                border-color: #2563eb;
            }
            QComboBox::drop-down {
                border: none;
                width: 30px;
            }
            QComboBox QAbstractItemView {
                background-color: white;
                border: 1px solid #d1d5db;
                selection-background-color: #2563eb;
                selection-color: white;
            }
            QComboBox QAbstractItemView::item {
                padding: 6px 12px;
                color: #374151;
            }
            QComboBox QAbstractItemView::item:hover {
                background-color: #dbeafe;
                color: #1d4ed8;
            }
        """

    def _get_progress_style(self) -> str:
        """Get progress bar style."""
        return """
            QProgressBar {
                border: 1px solid #d1d5db;
                border-radius: 4px;
                text-align: center;
                height: 20px;
            }
            QProgressBar::chunk {
                background-color: #2563eb;
                border-radius: 3px;
            }
        """

    def _get_table_style(self) -> str:
        """Get table style."""
        return """
            QTableWidget {
                border: 1px solid #e5e7eb;
                border-radius: 4px;
                background-color: white;
                gridline-color: #e5e7eb;
            }
            QHeaderView::section {
                background-color: #f9fafb;
                padding: 8px;
                border: none;
                border-bottom: 1px solid #e5e7eb;
                font-weight: bold;
                color: #374151;
            }
        """

    # =========================================================================
    # Public API
    # =========================================================================

    def set_project(self, project_id: int, company_id: int):
        """
        Set the current project context.

        Args:
            project_id: Project ID
            company_id: Company ID
        """
        self._current_project_id = project_id
        self._current_company_id = company_id
        self._refresh_license()
        self._refresh_layers()
        self._setup_reference_widget_storage()

    def refresh(self):
        """Refresh all data."""
        self._refresh_license()
        self._refresh_layers()
        self._setup_reference_widget_storage()

    # =========================================================================
    # Private Methods
    # =========================================================================

    def _set_controls_enabled(self, enabled: bool):
        """Enable or disable processing controls."""
        self.process_btn.setEnabled(enabled and self._has_valid_selection())
        has_processed = len(self._processed_claims) > 0
        has_waypoints = len(self._processed_waypoints) > 0
        has_documents = len(self._generated_documents) > 0

        self.generate_docs_btn.setEnabled(enabled and has_processed)
        self.download_docs_btn.setEnabled(enabled and has_documents)
        self.export_gpx_btn.setEnabled(enabled and has_waypoints)
        self.push_btn.setEnabled(enabled and has_processed)
        self.update_waypoints_btn.setEnabled(enabled and has_waypoints)

    def _setup_reference_widget_storage(self):
        """
        Configure the reference widget to use GeoPackage storage when available.

        This connects the reference widget to the claims GeoPackage so that
        reference points are stored persistently instead of in memory.
        Also loads any existing reference points from the GeoPackage.
        """
        gpkg_path = self._geopackage_path
        if not gpkg_path:
            self.logger.debug(
                "[QCLAIMS UI] No GeoPackage path configured, "
                "reference points will use memory layer"
            )
            return

        try:
            # Set up the storage manager connection
            self._claims_storage_manager.set_current_geopackage(gpkg_path)

            # Get project name from GeoPackage metadata for layer naming
            metadata = self._claims_storage_manager.load_metadata(gpkg_path)
            project_name = metadata.get('project_name', None)

            # Configure the reference widget to use GeoPackage storage
            if hasattr(self, 'reference_widget'):
                self.reference_widget.set_storage(
                    self._claims_storage_manager,
                    gpkg_path,
                    project_name=project_name
                )

                # Load existing reference points from GeoPackage
                existing_points = self._claims_storage_manager.load_reference_points(gpkg_path)
                if existing_points:
                    self.reference_widget.set_reference_points(existing_points)
                    self._reference_points = existing_points
                    self.logger.info(
                        f"[QCLAIMS UI] Loaded {len(existing_points)} reference points "
                        "from GeoPackage"
                    )

        except FileNotFoundError:
            self.logger.debug(
                f"[QCLAIMS UI] GeoPackage not found at {gpkg_path}, "
                "reference points will use memory layer"
            )
        except Exception as e:
            self.logger.warning(
                f"[QCLAIMS UI] Failed to set up reference point storage: {e}"
            )

    def _has_valid_selection(self) -> bool:
        """Check if a valid layer/selection is available."""
        layer = self._get_selected_layer()
        if not layer:
            return False

        # Check for polygon geometry
        if layer.geometryType() != 2:  # QgsWkbTypes.PolygonGeometry
            return False

        # Check for features
        if self.use_selected_btn.isChecked():
            return layer.selectedFeatureCount() > 0
        return layer.featureCount() > 0

    def _get_selected_layer(self) -> Optional[QgsVectorLayer]:
        """Get the currently selected layer."""
        index = self.layer_combo.currentIndex()
        if index < 0:
            return None

        layer_id = self.layer_combo.currentData()
        if not layer_id:
            return None

        layer = QgsProject.instance().mapLayer(layer_id)
        if isinstance(layer, QgsVectorLayer):
            return layer
        return None

    def _refresh_license(self):
        """Refresh license/access status."""
        self.license_status_label.setText("Checking access...")
        self.license_details_label.setText("")
        self.tos_status_label.setText("Terms of Service: Checking...")
        self.accept_tos_btn.hide()
        QApplication.processEvents()

        try:
            # Check access
            self._access_info = self.claims_manager.check_access(force_refresh=True)

            access_type = self._access_info.get('access_type', 'unknown')
            can_process = self._access_info.get('can_process_immediately', False)
            is_staff = self._access_info.get('is_staff', False)

            # Update status
            if is_staff:
                self.license_status_label.setText("Staff Access (Unlimited)")
                self.license_status_label.setStyleSheet("font-weight: bold; color: #059669;")
                self.license_details_label.setText("Full QClaims access with no limits.")
            elif access_type.startswith('enterprise'):
                monthly_limit = self._access_info.get('monthly_limit')
                used = self._access_info.get('claims_used_this_month', 0)

                self.license_status_label.setText(f"Enterprise: {access_type.replace('enterprise_', '').title()}")
                self.license_status_label.setStyleSheet("font-weight: bold; color: #2563eb;")

                if monthly_limit:
                    remaining = monthly_limit - used
                    self.license_details_label.setText(
                        f"Claims this month: {used} / {monthly_limit} ({remaining} remaining)"
                    )
                else:
                    self.license_details_label.setText("Unlimited claims")
            elif access_type == 'pay_per_claim':
                self.license_status_label.setText("Pay-Per-Claim")
                self.license_status_label.setStyleSheet("font-weight: bold; color: #d97706;")

                pricing = self._access_info.get('pricing', {})
                price_cents = pricing.get('self_service_per_claim_cents', 0)
                price_dollars = price_cents / 100 if price_cents else 0

                if price_dollars > 0:
                    self.license_details_label.setText(
                        f"${price_dollars:.2f} per claim. Payment required before processing."
                    )
                    self.pricing_label.setText(f"${price_dollars:.2f}/claim")
                else:
                    self.license_details_label.setText("Pricing not available.")
            else:
                self.license_status_label.setText("No Access")
                self.license_status_label.setStyleSheet("font-weight: bold; color: #dc2626;")
                self.license_details_label.setText("Unable to determine access level.")

            # Check TOS
            tos_info = self.claims_manager.check_tos(force_refresh=True)
            if tos_info.get('accepted'):
                self.tos_status_label.setText(
                    f"Terms of Service: Accepted (v{tos_info.get('accepted_version', '?')})"
                )
                self.tos_status_label.setStyleSheet("color: #059669;")
                self.accept_tos_btn.hide()
            else:
                self.tos_status_label.setText("Terms of Service: Not Accepted")
                self.tos_status_label.setStyleSheet("color: #dc2626;")
                self.accept_tos_btn.show()

            # Show/hide staff orders button
            if is_staff:
                self.staff_orders_btn.show()
            else:
                self.staff_orders_btn.hide()

            # Enable controls if access and TOS accepted
            self._set_controls_enabled(tos_info.get('accepted', False))

        except Exception as e:
            self.logger.error(f"[QCLAIMS UI] License check failed: {e}")
            self.license_status_label.setText("Error checking access")
            self.license_status_label.setStyleSheet("font-weight: bold; color: #dc2626;")
            # Show error and the URL that was attempted
            url = self.claims_manager._get_claims_endpoint('check-access/')
            self.license_details_label.setText(f"{e}\nURL: {url}")
            self._set_controls_enabled(False)

    def _update_layer_combo(self):
        """Update the layer combo box (alias for _refresh_layers)."""
        self._refresh_layers()

    def _refresh_layers(self):
        """Refresh the layer combo box."""
        self.layer_combo.clear()

        # Find polygon layers
        for layer_id, layer in QgsProject.instance().mapLayers().items():
            if isinstance(layer, QgsVectorLayer):
                if layer.geometryType() == 2:  # Polygon
                    self.layer_combo.addItem(layer.name(), layer_id)

        if self.layer_combo.count() == 0:
            self.selection_info_label.setText("No polygon layers found. Create a layer with claim boundaries.")
        else:
            self._on_layer_changed(self.layer_combo.currentIndex())

    def _on_layer_changed(self, index: int):
        """Handle layer selection change."""
        layer = self._get_selected_layer()
        if not layer:
            self.selection_info_label.setText("Select a polygon layer")
            self.process_btn.setEnabled(False)
            return

        feature_count = layer.featureCount()
        selected_count = layer.selectedFeatureCount()

        self.selection_info_label.setText(
            f"{feature_count} features ({selected_count} selected)"
        )

        # Update process button state
        if self._access_info and self.claims_manager.has_accepted_tos():
            self.process_btn.setEnabled(self._has_valid_selection())

    def _accept_tos(self):
        """Show TOS dialog and accept if user agrees."""
        try:
            # Fetch TOS content
            tos_content = self.claims_manager.get_tos_content()

            # Show TOS dialog
            from .claims_tos_dialog import ClaimsTOSDialog
            dialog = ClaimsTOSDialog(tos_content, self)

            if dialog.exec_():
                # User accepted
                result = self.claims_manager.accept_tos()
                self.tos_status_label.setText(
                    f"Terms of Service: Accepted (v{result.get('version', '?')})"
                )
                self.tos_status_label.setStyleSheet("color: #059669;")
                self.accept_tos_btn.hide()
                self._set_controls_enabled(True)
                self.status_message.emit("Terms of Service accepted", "info")

        except Exception as e:
            self.logger.error(f"[QCLAIMS UI] TOS acceptance failed: {e}")
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to accept Terms of Service: {e}"
            )

    def _show_staff_orders(self):
        """Show staff pending orders dialog."""
        try:
            from .staff_orders_dialog import StaffOrdersDialog

            dialog = StaffOrdersDialog(self.claims_manager, self)
            dialog.order_selected.connect(self._on_staff_order_selected)

            dialog.exec_()

        except Exception as e:
            self.logger.error(f"[QCLAIMS UI] Failed to show staff orders: {e}")
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to load pending orders: {e}"
            )

    def _on_staff_order_selected(self, order_data: dict):
        """
        Handle staff selecting an order for fulfillment.

        Args:
            order_data: Order dict from staff_pending_orders API
        """
        try:
            # Get claim polygons from order
            claim_polygons = order_data.get('claim_polygons', {})
            if not claim_polygons:
                QMessageBox.warning(
                    self,
                    "No Claims Data",
                    "This order does not have claim polygon data."
                )
                return

            # Create a memory layer from the GeoJSON
            self._load_order_polygons_to_layer(order_data)

            # Store order context for later use when generating documents
            self._fulfillment_order_id = order_data.get('id')
            self._fulfillment_order_type = order_data.get('order_type')
            self._fulfillment_order_number = order_data.get('order_number')

            # Pre-populate claimant info if available
            claimant_info = order_data.get('claimant_info')
            if claimant_info:
                # Store for document generation
                self._fulfillment_claimant_info = claimant_info

            self.status_message.emit(
                f"Loaded order {order_data.get('order_number')} - "
                f"{order_data.get('claim_count', 0)} claims ready for processing",
                "info"
            )

        except Exception as e:
            self.logger.error(f"[QCLAIMS UI] Failed to load order: {e}")
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to load order into QGIS: {e}"
            )

    def _load_order_polygons_to_layer(self, order_data: dict):
        """
        Load order claim polygons into a QGIS memory layer.

        Args:
            order_data: Order dict containing claim_polygons GeoJSON
        """
        claim_polygons = order_data.get('claim_polygons', {})
        order_number = order_data.get('order_number', 'Unknown')

        # Handle different formats
        features_data = []
        if isinstance(claim_polygons, dict):
            if claim_polygons.get('type') == 'FeatureCollection':
                features_data = claim_polygons.get('features', [])
            elif claim_polygons.get('type') == 'Feature':
                features_data = [claim_polygons]
            elif 'claims' in claim_polygons:
                # Nested format from some orders
                features_data = claim_polygons.get('claims', [])
        elif isinstance(claim_polygons, list):
            features_data = claim_polygons

        if not features_data:
            raise ValueError("No claim features found in order data")

        # Create memory layer
        layer = QgsVectorLayer(
            "Polygon?crs=EPSG:4326",
            f"Order {order_number} Claims",
            "memory"
        )
        provider = layer.dataProvider()

        # Add fields
        fields = QgsFields()
        fields.append(QgsField("name", QVariant.String))
        fields.append(QgsField("order_id", QVariant.Int))
        fields.append(QgsField("order_type", QVariant.String))
        provider.addAttributes(fields)
        layer.updateFields()

        # Add features
        for i, feat_data in enumerate(features_data):
            feature = QgsFeature()

            # Get geometry
            if isinstance(feat_data, dict):
                geom_data = feat_data.get('geometry', feat_data)
                props = feat_data.get('properties', {})
            else:
                geom_data = feat_data
                props = {}

            geom = QgsGeometry.fromWkt(self._geojson_to_wkt(geom_data))
            if not geom or geom.isEmpty():
                self.logger.warning(f"[QCLAIMS UI] Skipping invalid geometry in order")
                continue

            feature.setGeometry(geom)

            # Set attributes
            name = props.get('name') or props.get('claim_name') or f"Claim {i + 1}"
            feature.setAttributes([
                name,
                order_data.get('id'),
                order_data.get('order_type')
            ])

            provider.addFeature(feature)

        layer.updateExtents()

        # Add to project
        QgsProject.instance().addMapLayer(layer)

        # Zoom to layer
        from qgis.utils import iface
        if iface and iface.mapCanvas():
            iface.mapCanvas().setExtent(layer.extent())
            iface.mapCanvas().refresh()

        # Select the new layer in the combo
        self._refresh_layers()
        for i in range(self.layer_combo.count()):
            if self.layer_combo.itemData(i) == layer.id():
                self.layer_combo.setCurrentIndex(i)
                break

        self.logger.info(
            f"[QCLAIMS UI] Loaded {layer.featureCount()} claims from order {order_number}"
        )

    def _geojson_to_wkt(self, geom_data: dict) -> str:
        """
        Convert GeoJSON geometry to WKT.

        Args:
            geom_data: GeoJSON geometry dict

        Returns:
            WKT string
        """
        geom_type = geom_data.get('type', '').lower()
        coords = geom_data.get('coordinates', [])

        if geom_type == 'polygon':
            rings = []
            for ring in coords:
                points = ', '.join(f"{p[0]} {p[1]}" for p in ring)
                rings.append(f"({points})")
            return f"POLYGON({', '.join(rings)})"

        elif geom_type == 'multipolygon':
            polygons = []
            for polygon in coords:
                rings = []
                for ring in polygon:
                    points = ', '.join(f"{p[0]} {p[1]}" for p in ring)
                    rings.append(f"({points})")
                polygons.append(f"({', '.join(rings)})")
            return f"MULTIPOLYGON({', '.join(polygons)})"

        return ""

    def _process_claims(self):
        """Process selected claims."""
        layer = self._get_selected_layer()
        if not layer:
            QMessageBox.warning(self, "No Layer", "Please select a layer with claim polygons.")
            return

        if not self._current_project_id:
            QMessageBox.warning(self, "No Project", "Please select a project first.")
            return

        # Get features
        if self.use_selected_btn.isChecked():
            features = list(layer.selectedFeatures())
            if not features:
                QMessageBox.warning(self, "No Selection", "Please select features in the layer.")
                return
        else:
            features = list(layer.getFeatures())
            if not features:
                QMessageBox.warning(self, "No Features", "The layer has no features.")
                return

        # Get layer CRS EPSG code first - needed for geometry export
        # IMPORTANT: Use WKT in the layer's native CRS (typically UTM) to preserve
        # exact corner coordinates. Using asJson() would convert to WGS84, causing
        # precision loss that breaks shared corner detection between adjacent claims.
        layer_epsg = None
        if layer.crs().isValid():
            layer_epsg = layer.crs().postgisSrid()

        # Prepare claims data
        claims = []
        for feature in features:
            geom = feature.geometry()
            if geom.isNull() or geom.isEmpty():
                continue

            # Get name from attribute (look for common name fields)
            name = None
            for field_name in ['name', 'Name', 'NAME', 'claim_name', 'CLAIM_NAME']:
                idx = layer.fields().indexOf(field_name)
                if idx >= 0:
                    name = feature.attribute(idx)
                    break

            if not name:
                name = f"Claim {feature.id()}"

            claims.append({
                'name': str(name),
                'geometry': geom.asWkt(),  # WKT in layer's native CRS (UTM)
                'epsg': layer_epsg,
                'claim_type': 'lode'  # Default, could add selector
            })

        if not claims:
            QMessageBox.warning(self, "No Valid Claims", "No valid polygon geometries found.")
            return

        # Get reference points if any
        reference_points = []
        if hasattr(self, 'reference_widget'):
            reference_points = self.reference_widget.get_reference_points()

        # Check if user can process immediately
        can_process_immediately = self._access_info and self._access_info.get('can_process_immediately', False)

        if can_process_immediately:
            self._execute_processing(claims, reference_points, layer_epsg)
        else:
            # Pay-per-claim user - submit order
            self._submit_order(claims)

    def _execute_processing(
        self,
        claims: List[Dict[str, Any]],
        reference_points: Optional[List[Dict[str, Any]]] = None,
        epsg: Optional[int] = None
    ):
        """
        Execute immediate processing (Enterprise/Staff).

        Args:
            claims: List of claim dictionaries with geometry
            reference_points: Optional list of reference points for bearing/distance
            epsg: Optional EPSG code for the layer CRS
        """
        self.progress_bar.show()
        self.progress_bar.setValue(0)
        self.process_btn.setEnabled(False)
        QApplication.processEvents()

        try:
            self.progress_bar.setValue(20)
            self.status_message.emit("Sending claims to server for processing...", "info")
            QApplication.processEvents()

            # Note: reference_points and epsg will be passed when server endpoint supports them
            # For now, just log them
            if reference_points:
                self.logger.info(
                    f"[QCLAIMS UI] Processing with {len(reference_points)} reference points"
                )
            if epsg:
                self.logger.info(f"[QCLAIMS UI] Layer EPSG: {epsg}")

            result = self.claims_manager.process_claims(claims, self._current_project_id)

            self.progress_bar.setValue(50)
            self.status_message.emit("Creating QGIS layers...", "info")
            QApplication.processEvents()

            # Store results
            self._processed_claims = result.get('claims', [])
            self._processed_waypoints = result.get('waypoints', [])

            # Create QGIS layers from results
            self._create_result_layers()

            self.progress_bar.setValue(100)

            # Update UI
            self._display_results(result)

            # Update button states
            self.generate_docs_btn.setEnabled(True)
            self.export_gpx_btn.setEnabled(len(self._processed_waypoints) > 0)
            self.push_btn.setEnabled(True)
            self.update_lm_btn.setEnabled(True)  # Enable LM corner updates

            self.status_message.emit(
                f"Processed {len(self._processed_claims)} claims successfully",
                "info"
            )
            self.claims_processed.emit(result)

        except Exception as e:
            self.logger.error(f"[QCLAIMS UI] Processing failed: {e}")
            QMessageBox.critical(self, "Processing Error", str(e))

        finally:
            self.progress_bar.hide()
            self.process_btn.setEnabled(True)

    def _submit_order(self, claims: List[Dict[str, Any]]):
        """Submit order for pay-per-claim users."""
        # Calculate total
        pricing = self._access_info.get('pricing', {})
        price_cents = pricing.get('self_service_per_claim_cents', 0)
        total_cents = price_cents * len(claims)
        total_dollars = total_cents / 100

        # Confirm with user
        reply = QMessageBox.question(
            self,
            "Submit Order",
            f"Submit order for {len(claims)} claims?\n\n"
            f"Estimated total: ${total_dollars:.2f}\n\n"
            "You will be billed after processing is approved.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply != QMessageBox.Yes:
            return

        self.progress_bar.show()
        self.progress_bar.setValue(0)
        self.process_btn.setEnabled(False)
        QApplication.processEvents()

        try:
            self.progress_bar.setValue(50)

            result = self.claims_manager.submit_order(
                claims=claims,
                project_id=self._current_project_id,
                company_id=self._current_company_id,
                service_type='self_service'
            )

            self.progress_bar.setValue(100)

            # Show result
            status = result.get('status', 'unknown')
            if status == 'approved':
                msg = (
                    f"Order #{result.get('order_id')} submitted and approved!\n\n"
                    f"Total: {result.get('total_display', '?')}\n\n"
                    "Payment will be collected before processing."
                )
            else:
                msg = (
                    f"Order #{result.get('order_id')} submitted for approval.\n\n"
                    f"Total: {result.get('total_display', '?')}\n\n"
                    "Company managers have been notified."
                )

            QMessageBox.information(self, "Order Submitted", msg)
            self.order_submitted.emit(result)
            self.status_message.emit(f"Order #{result.get('order_id')} submitted", "info")

        except Exception as e:
            self.logger.error(f"[QCLAIMS UI] Order submission failed: {e}")
            QMessageBox.critical(self, "Order Error", str(e))

        finally:
            self.progress_bar.hide()
            self.process_btn.setEnabled(True)

    def _display_results(self, result: Dict[str, Any]):
        """Display processing results in the table."""
        claims = result.get('claims', [])

        self.results_table.setRowCount(len(claims))

        for row, claim in enumerate(claims):
            # Name
            name_item = QTableWidgetItem(claim.get('name', ''))
            self.results_table.setItem(row, 0, name_item)

            # State
            state_item = QTableWidgetItem(claim.get('state', ''))
            self.results_table.setItem(row, 1, state_item)

            # PLSS
            plss = claim.get('plss', {})
            plss_desc = plss.get('description', '') if isinstance(plss, dict) else ''
            plss_item = QTableWidgetItem(plss_desc)
            self.results_table.setItem(row, 2, plss_item)

            # Corners
            corners = claim.get('corners', [])
            corners_item = QTableWidgetItem(str(len(corners)))
            corners_item.setTextAlignment(Qt.AlignCenter)
            self.results_table.setItem(row, 3, corners_item)

        self.results_group.show()

    def _generate_documents(self):
        """Generate documents for processed claims."""
        if not self._processed_claims:
            QMessageBox.warning(self, "No Claims", "Process claims first before generating documents.")
            return

        self.progress_bar.show()
        self.progress_bar.setValue(0)
        self.generate_docs_btn.setEnabled(False)
        QApplication.processEvents()

        try:
            self.progress_bar.setValue(30)

            # Build arguments for document generation
            doc_args = {
                'claims': self._processed_claims,
                'document_types': ['location_notice'],
                'project_id': self._current_project_id,
                'save_to_project': True,
                'waypoints': self._processed_waypoints  # For sorted corner certificates
            }

            # Include reference points for bearing/distance tie-in in location notices
            if self._reference_points:
                doc_args['reference_points'] = self._reference_points
                self.logger.info(
                    f"[QCLAIMS UI] Including {len(self._reference_points)} reference points "
                    "for document generation"
                )

            # Include staff fulfillment context if present
            # This links documents to the existing order/ClaimPackage
            if self._fulfillment_order_id and self._fulfillment_order_type:
                doc_args['order_id'] = self._fulfillment_order_id
                doc_args['order_type'] = self._fulfillment_order_type
                self.logger.info(
                    f"[QCLAIMS UI] Generating documents for order fulfillment: "
                    f"{self._fulfillment_order_type} #{self._fulfillment_order_id}"
                )

            # Include claimant info if available
            if self._fulfillment_claimant_info:
                doc_args['claimant_info'] = self._fulfillment_claimant_info

            result = self.claims_manager.generate_documents(**doc_args)

            self.progress_bar.setValue(100)

            documents = result.get('documents', [])
            package_info = result.get('package')

            # Store documents for download
            self._generated_documents = documents

            if documents:
                # Enable download button
                self.download_docs_btn.setEnabled(True)

                doc_list = "\n".join([f"- {d.get('filename', '?')}" for d in documents])

                # Build message
                message = f"Generated {len(documents)} document(s):\n\n{doc_list}"

                # Add fulfillment info if applicable
                if self._fulfillment_order_id:
                    message += f"\n\nDocuments linked to order {self._fulfillment_order_number}"
                    if package_info:
                        message += f"\nPackage: {package_info.get('package_number', 'N/A')}"

                    # Clear fulfillment context after successful generation
                    self._clear_fulfillment_context()

                message += "\n\nUse the 'Download Documents' button to save them."

                QMessageBox.information(
                    self,
                    "Documents Generated",
                    message
                )
            else:
                self.download_docs_btn.setEnabled(False)
                QMessageBox.information(self, "No Documents", "No documents were generated.")

            self.status_message.emit(f"Generated {len(documents)} documents", "info")

        except Exception as e:
            self.logger.error(f"[QCLAIMS UI] Document generation failed: {e}")
            QMessageBox.critical(self, "Error", str(e))

        finally:
            self.progress_bar.hide()
            self.generate_docs_btn.setEnabled(True)

    def _clear_fulfillment_context(self):
        """Clear staff order fulfillment context after successful document generation."""
        self._fulfillment_order_id = None
        self._fulfillment_order_type = None
        self._fulfillment_order_number = None
        self._fulfillment_claimant_info = None

    def _push_to_server(self):
        """Push processed claims to server."""
        if not self._processed_claims:
            QMessageBox.warning(self, "No Claims", "Process claims first before pushing to server.")
            return

        if not self._current_project_id:
            QMessageBox.warning(self, "No Project", "Please select a project first.")
            return

        # Confirm
        reply = QMessageBox.question(
            self,
            "Push to Server",
            f"Push {len(self._processed_claims)} claims as LandHoldings and "
            f"{len(self._processed_waypoints)} waypoints as ClaimStakes to the server?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply != QMessageBox.Yes:
            return

        self.progress_bar.show()
        self.progress_bar.setValue(0)
        self.push_btn.setEnabled(False)
        QApplication.processEvents()

        try:
            self.progress_bar.setValue(30)

            result = self.claims_manager.push_to_server(
                self._processed_claims,
                self._processed_waypoints,
                self._current_project_id
            )

            self.progress_bar.setValue(100)

            # Show result
            lh_summary = result.get('landholdings', {}).get('summary', {})
            st_summary = result.get('stakes', {}).get('summary', {})

            QMessageBox.information(
                self,
                "Push Complete",
                f"LandHoldings: {lh_summary.get('created', 0)} created\n"
                f"ClaimStakes: {st_summary.get('created', 0)} created"
            )

            self.status_message.emit("Claims pushed to server successfully", "info")

        except Exception as e:
            self.logger.error(f"[QCLAIMS UI] Push to server failed: {e}")
            QMessageBox.critical(self, "Error", str(e))

        finally:
            self.progress_bar.hide()
            self.push_btn.setEnabled(True)

    # =========================================================================
    # Layer Creation & Styling
    # =========================================================================

    def _create_result_layers(self):
        """Create QGIS layers from processed claims and waypoints."""
        try:
            using_geopackage = bool(self._geopackage_path)
            storage_info = " (saved to GeoPackage)" if using_geopackage else ""

            # Create claims polygon layer
            if self._processed_claims:
                self._claims_layer = self._create_claims_polygon_layer()
                if self._claims_layer:
                    self._apply_claims_styling(self._claims_layer)
                    # Only add to project if using memory layer (GeoPackage auto-adds)
                    if not using_geopackage:
                        QgsProject.instance().addMapLayer(self._claims_layer)
                    self.status_message.emit(
                        f"Created claims layer with {self._claims_layer.featureCount()} features{storage_info}",
                        "info"
                    )

            # Create waypoints point layer
            if self._processed_waypoints:
                self._waypoints_layer = self._create_waypoints_layer()
                if self._waypoints_layer:
                    self._apply_waypoints_styling(self._waypoints_layer)
                    # Only add to project if using memory layer (GeoPackage auto-adds)
                    if not using_geopackage:
                        QgsProject.instance().addMapLayer(self._waypoints_layer)
                    self.status_message.emit(
                        f"Created waypoints layer with {self._waypoints_layer.featureCount()} features{storage_info}",
                        "info"
                    )

        except Exception as e:
            self.logger.error(f"[QCLAIMS UI] Failed to create layers: {e}")
            self.status_message.emit(f"Warning: Failed to create layers: {e}", "warning")

    def _create_claims_polygon_layer(self) -> Optional[QgsVectorLayer]:
        """Create a polygon layer from processed claims."""
        crs = QgsCoordinateReferenceSystem("EPSG:4326")

        # Define fields
        fields = QgsFields()
        fields.append(QgsField("name", QVariant.String, len=100))
        fields.append(QgsField("state", QVariant.String, len=2))
        fields.append(QgsField("county", QVariant.String, len=100))
        fields.append(QgsField("claim_type", QVariant.String, len=20))
        fields.append(QgsField("plss_description", QVariant.String, len=500))
        fields.append(QgsField("acreage", QVariant.Double))
        fields.append(QgsField("corner_count", QVariant.Int))
        fields.append(QgsField("session_id", QVariant.String, len=50))

        # Use GeoPackage if configured, otherwise memory layer
        from ..managers.claims_storage_manager import ClaimsStorageManager
        if self._geopackage_path:
            layer = self._claims_storage_manager.create_or_update_layer(
                table_name=ClaimsStorageManager.PROCESSED_CLAIMS_TABLE,
                layer_display_name="Processed Claims",
                geometry_type='Polygon',
                fields=fields,
                crs=crs,
                gpkg_path=self._geopackage_path
            )
        else:
            # Fallback to memory layer
            layer = QgsVectorLayer(
                f"Polygon?crs={crs.authid()}",
                "Processed Claims",
                "memory"
            )
            layer.dataProvider().addAttributes(fields.toList())
            layer.updateFields()

        # Add features
        features = []
        for claim in self._processed_claims:
            feature = QgsFeature(layer.fields())

            # Set geometry from the claim
            geom_data = claim.get('geometry')
            if geom_data:
                if isinstance(geom_data, str):
                    geom = QgsGeometry.fromWkt(geom_data)
                elif isinstance(geom_data, dict):
                    geom = QgsGeometry.fromWkt(
                        json.dumps(geom_data) if 'type' in geom_data else None
                    )
                    if geom.isNull():
                        # Try parsing as GeoJSON
                        try:
                            geom = QgsGeometry.fromJson(json.dumps(geom_data))
                        except:
                            pass
                else:
                    geom = None

                if geom and not geom.isNull():
                    feature.setGeometry(geom)

            # Set attributes
            feature.setAttribute("name", claim.get('name', ''))
            feature.setAttribute("state", claim.get('state', ''))
            feature.setAttribute("county", claim.get('county', ''))
            feature.setAttribute("claim_type", claim.get('claim_type', 'lode'))

            plss = claim.get('plss', {})
            if isinstance(plss, dict):
                feature.setAttribute("plss_description", plss.get('description', ''))
            else:
                feature.setAttribute("plss_description", str(plss) if plss else '')

            feature.setAttribute("acreage", claim.get('calculated_acreage', 0))
            feature.setAttribute("corner_count", len(claim.get('corners', [])))
            feature.setAttribute("session_id", claim.get('session_id', ''))

            features.append(feature)

        layer.dataProvider().addFeatures(features)
        layer.updateExtents()

        return layer

    def _create_waypoints_layer(self) -> Optional[QgsVectorLayer]:
        """Create a point layer from processed waypoints."""
        crs = QgsCoordinateReferenceSystem("EPSG:4326")

        # Define fields
        fields = QgsFields()
        fields.append(QgsField("name", QVariant.String, len=100))
        fields.append(QgsField("claim", QVariant.String, len=100))
        fields.append(QgsField("waypoint_type", QVariant.String, len=20))
        fields.append(QgsField("corner_number", QVariant.Int))
        fields.append(QgsField("sequence", QVariant.Int))
        fields.append(QgsField("latitude", QVariant.Double))
        fields.append(QgsField("longitude", QVariant.Double))

        # Use GeoPackage if configured, otherwise memory layer
        from ..managers.claims_storage_manager import ClaimsStorageManager
        if self._geopackage_path:
            layer = self._claims_storage_manager.create_or_update_layer(
                table_name=ClaimsStorageManager.CLAIM_WAYPOINTS_TABLE,
                layer_display_name="Claim Waypoints",
                geometry_type='Point',
                fields=fields,
                crs=crs,
                gpkg_path=self._geopackage_path
            )
        else:
            # Fallback to memory layer
            layer = QgsVectorLayer(
                f"Point?crs={crs.authid()}",
                "Claim Waypoints",
                "memory"
            )
            layer.dataProvider().addAttributes(fields.toList())
            layer.updateFields()

        # Add features
        features = []
        for wpt in self._processed_waypoints:
            feature = QgsFeature(layer.fields())

            # Set geometry
            lat = wpt.get('lat')
            lon = wpt.get('lon')
            if lat is not None and lon is not None:
                point = QgsPointXY(float(lon), float(lat))
                feature.setGeometry(QgsGeometry.fromPointXY(point))

            # Set attributes
            wpt_type = wpt.get('type', 'corner')
            claim_name = wpt.get('claim', '')
            corner_num = wpt.get('corner_number', 0)

            if wpt_type == 'corner':
                name = f"{claim_name} C{corner_num}"
            elif wpt_type == 'discovery':
                name = f"{claim_name} Discovery"
            else:
                name = wpt.get('name', f"{claim_name} WPT")

            feature.setAttribute("name", name)
            feature.setAttribute("claim", claim_name)
            feature.setAttribute("waypoint_type", wpt_type)
            feature.setAttribute("corner_number", corner_num)
            feature.setAttribute("sequence", wpt.get('sequence_number', 0))
            feature.setAttribute("latitude", lat)
            feature.setAttribute("longitude", lon)

            features.append(feature)

        layer.dataProvider().addFeatures(features)
        layer.updateExtents()

        return layer

    def _apply_claims_styling(self, layer: QgsVectorLayer):
        """Apply styling to claims polygon layer."""
        try:
            # Create a simple fill symbol with blue outline and transparent fill
            symbol = QgsFillSymbol.createSimple({
                'color': '91,155,213,50',  # Light blue with transparency
                'outline_color': '#2563eb',  # Blue
                'outline_width': '0.8',
                'outline_style': 'solid'
            })

            renderer = QgsSingleSymbolRenderer(symbol)
            layer.setRenderer(renderer)
            layer.triggerRepaint()

            self.logger.info("[QCLAIMS UI] Applied claims layer styling")

        except Exception as e:
            self.logger.error(f"[QCLAIMS UI] Failed to apply claims styling: {e}")

    def _apply_waypoints_styling(self, layer: QgsVectorLayer):
        """
        Apply categorized styling to waypoints layer by type.

        Styling specifications:
        - Navaid, Green (location_monument): lime green circle, size 1.8mm, label "Location Monument"
        - Navaid, White (witness): white circle, size 1.8mm, label "Witness Point"
        - City (Medium) (corner): black circle, size 1.6mm, label "Corner"
        - Discovery: red star, size 2.5mm (no special label)
        - Sideline/Endline: green diamonds, size 1.8mm
        """
        try:
            # Define categories for waypoint types
            categories = []

            # Corner waypoints - Black circles (City Medium style)
            # Size 1.6mm as specified
            corner_symbol = QgsMarkerSymbol.createSimple({
                'name': 'circle',
                'color': '#000000',  # Black
                'outline_color': '#000000',
                'outline_width': '0.3',
                'size': '1.6',
                'size_unit': 'MM'
            })
            categories.append(QgsRendererCategory('corner', corner_symbol, 'Corner'))

            # Location Monument (LM) - Lime green circles (Navaid, Green style)
            # Size 1.8mm as specified
            location_symbol = QgsMarkerSymbol.createSimple({
                'name': 'circle',
                'color': '#32CD32',  # Lime green
                'outline_color': '#228B22',  # Forest green outline
                'outline_width': '0.3',
                'size': '1.8',
                'size_unit': 'MM'
            })
            categories.append(QgsRendererCategory('location_monument', location_symbol, 'Location Monument'))

            # Discovery monument - Red star (kept for visibility)
            discovery_symbol = QgsMarkerSymbol.createSimple({
                'name': 'star',
                'color': '#dc2626',  # Red
                'outline_color': '#991b1b',
                'outline_width': '0.3',
                'size': '2.5',
                'size_unit': 'MM'
            })
            categories.append(QgsRendererCategory('discovery', discovery_symbol, 'Discovery'))

            # Witness waypoints - White circles (Navaid, White style)
            # Size 1.8mm as specified
            witness_symbol = QgsMarkerSymbol.createSimple({
                'name': 'circle',
                'color': '#FFFFFF',  # White
                'outline_color': '#000000',  # Black outline for visibility
                'outline_width': '0.4',
                'size': '1.8',
                'size_unit': 'MM'
            })
            categories.append(QgsRendererCategory('witness', witness_symbol, 'Witness Point'))

            # Sideline monuments - Green diamonds
            sideline_symbol = QgsMarkerSymbol.createSimple({
                'name': 'diamond',
                'color': '#059669',  # Green
                'outline_color': '#065f46',
                'outline_width': '0.3',
                'size': '1.8',
                'size_unit': 'MM'
            })
            categories.append(QgsRendererCategory('sideline', sideline_symbol, 'Sideline'))

            # Endline monuments - Green diamonds (same as sideline)
            endline_symbol = QgsMarkerSymbol.createSimple({
                'name': 'diamond',
                'color': '#059669',
                'outline_color': '#065f46',
                'outline_width': '0.3',
                'size': '1.8',
                'size_unit': 'MM'
            })
            categories.append(QgsRendererCategory('endline', endline_symbol, 'Endline'))

            # Default/Other - Gray circles
            default_symbol = QgsMarkerSymbol.createSimple({
                'name': 'circle',
                'color': '#6b7280',
                'outline_color': '#374151',
                'outline_width': '0.3',
                'size': '1.5',
                'size_unit': 'MM'
            })
            categories.append(QgsRendererCategory('', default_symbol, 'Other'))

            renderer = QgsCategorizedSymbolRenderer('waypoint_type', categories)
            layer.setRenderer(renderer)

            # Apply rule-based labeling for different waypoint types
            self._apply_waypoint_labels(layer)

            layer.triggerRepaint()

            self.logger.info("[QCLAIMS UI] Applied waypoints layer styling with labels")

        except Exception as e:
            self.logger.error(f"[QCLAIMS UI] Failed to apply waypoints styling: {e}")
            # Fall back to simple styling
            try:
                symbol = QgsMarkerSymbol.createSimple({
                    'name': 'circle',
                    'color': '#2563eb',
                    'size': '2'
                })
                layer.setRenderer(QgsSingleSymbolRenderer(symbol))
                layer.triggerRepaint()
            except:
                pass

    def _apply_waypoint_labels(self, layer: QgsVectorLayer):
        """
        Apply rule-based labels to waypoints layer.

        Labels:
        - Corner (City Medium): "Corner" label, black text
        - Location Monument (Navaid, Green): "Location Monument" label, lime green text
        - Witness Point (Navaid, White): "Witness Point" label, white text with black halo
        """
        try:
            # Create root rule
            root_rule = QgsRuleBasedLabeling.Rule(QgsPalLayerSettings())

            # Corner label rule - black text
            corner_settings = QgsPalLayerSettings()
            corner_settings.fieldName = "'Corner'"
            corner_settings.isExpression = True
            corner_format = QgsTextFormat()
            corner_format.setColor(QColor(0, 0, 0))  # Black
            corner_format.setSize(8)
            corner_settings.setFormat(corner_format)
            corner_rule = QgsRuleBasedLabeling.Rule(corner_settings)
            corner_rule.setFilterExpression("\"waypoint_type\" = 'corner'")
            corner_rule.setDescription("Corner Labels")
            root_rule.appendChild(corner_rule)

            # Location Monument label rule - lime green text
            lm_settings = QgsPalLayerSettings()
            lm_settings.fieldName = "'Location Monument'"
            lm_settings.isExpression = True
            lm_format = QgsTextFormat()
            lm_format.setColor(QColor(50, 205, 50))  # Lime green
            lm_format.setSize(8)
            lm_settings.setFormat(lm_format)
            lm_rule = QgsRuleBasedLabeling.Rule(lm_settings)
            lm_rule.setFilterExpression("\"waypoint_type\" = 'location_monument'")
            lm_rule.setDescription("Location Monument Labels")
            root_rule.appendChild(lm_rule)

            # Witness Point label rule - white text with black buffer/halo
            witness_settings = QgsPalLayerSettings()
            witness_settings.fieldName = "'Witness Point'"
            witness_settings.isExpression = True
            witness_format = QgsTextFormat()
            witness_format.setColor(QColor(255, 255, 255))  # White

            # Add black buffer/halo for visibility
            from qgis.core import QgsTextBufferSettings
            buffer_settings = QgsTextBufferSettings()
            buffer_settings.setEnabled(True)
            buffer_settings.setSize(0.8)
            buffer_settings.setColor(QColor(0, 0, 0))  # Black halo
            witness_format.setBuffer(buffer_settings)

            witness_format.setSize(8)
            witness_settings.setFormat(witness_format)
            witness_rule = QgsRuleBasedLabeling.Rule(witness_settings)
            witness_rule.setFilterExpression("\"waypoint_type\" = 'witness'")
            witness_rule.setDescription("Witness Point Labels")
            root_rule.appendChild(witness_rule)

            # Apply rule-based labeling
            labeling = QgsRuleBasedLabeling(root_rule)
            layer.setLabeling(labeling)
            layer.setLabelsEnabled(True)

            self.logger.info("[QCLAIMS UI] Applied waypoint labels")

        except Exception as e:
            self.logger.error(f"[QCLAIMS UI] Failed to apply waypoint labels: {e}")

    # =========================================================================
    # Document Download
    # =========================================================================

    def _download_documents(self):
        """Download generated documents to local disk."""
        if not self._generated_documents:
            QMessageBox.warning(
                self,
                "No Documents",
                "Please generate documents first."
            )
            return

        # Ask for download directory
        download_dir = QFileDialog.getExistingDirectory(
            self,
            "Select Download Directory",
            str(Path.home() / "Documents"),
            QFileDialog.ShowDirsOnly
        )

        if not download_dir:
            return

        self.progress_bar.show()
        self.progress_bar.setValue(0)
        self.download_docs_btn.setEnabled(False)
        QApplication.processEvents()

        downloaded = 0
        errors = []

        try:
            total = len(self._generated_documents)

            for i, doc in enumerate(self._generated_documents):
                filename = doc.get('filename', f'document_{i}.pdf')
                download_url = doc.get('download_url', '')

                if not download_url:
                    errors.append(f"{filename}: No download URL")
                    continue

                # Download the file
                try:
                    self._download_file(download_url, os.path.join(download_dir, filename))
                    downloaded += 1
                except Exception as e:
                    errors.append(f"{filename}: {e}")

                self.progress_bar.setValue(int((i + 1) / total * 100))
                QApplication.processEvents()

            # Show result
            if errors:
                error_list = "\n".join(errors[:5])
                if len(errors) > 5:
                    error_list += f"\n... and {len(errors) - 5} more"

                QMessageBox.warning(
                    self,
                    "Download Complete with Errors",
                    f"Downloaded {downloaded} of {total} documents.\n\n"
                    f"Errors:\n{error_list}"
                )
            else:
                QMessageBox.information(
                    self,
                    "Download Complete",
                    f"Downloaded {downloaded} documents to:\n{download_dir}"
                )

                # Open the download folder
                QDesktopServices.openUrl(QUrl.fromLocalFile(download_dir))

            self.status_message.emit(f"Downloaded {downloaded} documents", "info")

        except Exception as e:
            self.logger.error(f"[QCLAIMS UI] Document download failed: {e}")
            QMessageBox.critical(self, "Error", str(e))

        finally:
            self.progress_bar.hide()
            self.download_docs_btn.setEnabled(True)

    def _download_file(self, url: str, local_path: str):
        """Download a file from URL to local path."""
        import urllib.request

        # Use urllib for simple download (works with signed S3 URLs)
        try:
            urllib.request.urlretrieve(url, local_path)
        except Exception as e:
            raise Exception(f"Failed to download: {e}")

    # =========================================================================
    # GPX Export
    # =========================================================================

    def _export_gpx(self):
        """Export waypoints to GPX file."""
        if not self._processed_waypoints:
            QMessageBox.warning(
                self,
                "No Waypoints",
                "Process claims first to generate waypoints."
            )
            return

        # Ask for save location
        default_name = "claim_waypoints.gpx"
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save GPX File",
            str(Path.home() / "Documents" / default_name),
            "GPX Files (*.gpx);;All Files (*)"
        )

        if not file_path:
            return

        # Ensure .gpx extension
        if not file_path.lower().endswith('.gpx'):
            file_path += '.gpx'

        self.progress_bar.show()
        self.progress_bar.setValue(0)
        self.export_gpx_btn.setEnabled(False)
        QApplication.processEvents()

        try:
            self.progress_bar.setValue(50)

            # Use GPX exporter
            exporter = GPXExporter()
            success = exporter.export_waypoints(
                self._processed_waypoints,
                file_path,
                creator="geodb.io QClaims",
                include_route=True
            )

            self.progress_bar.setValue(100)

            if success:
                QMessageBox.information(
                    self,
                    "GPX Export Complete",
                    f"Exported {len(self._processed_waypoints)} waypoints to:\n{file_path}\n\n"
                    "Transfer this file to your GPS device for field staking."
                )

                # Offer to open containing folder
                reply = QMessageBox.question(
                    self,
                    "Open Folder",
                    "Open the folder containing the GPX file?",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.Yes
                )
                if reply == QMessageBox.Yes:
                    folder = os.path.dirname(file_path)
                    QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

                self.status_message.emit(f"Exported {len(self._processed_waypoints)} waypoints to GPX", "info")
            else:
                QMessageBox.warning(self, "Export Failed", "Failed to export GPX file.")

        except Exception as e:
            self.logger.error(f"[QCLAIMS UI] GPX export failed: {e}")
            QMessageBox.critical(self, "Error", str(e))

        finally:
            self.progress_bar.hide()
            self.export_gpx_btn.setEnabled(True)

    # =========================================================================
    # Witness Waypoints
    # =========================================================================

    def _update_waypoints_table(self):
        """
        Update manually-added waypoint rows with coordinates, symbol, and other fields.

        This function finds waypoints that have been manually added (have geometry
        but no lat/lon values) and auto-populates:
        - Latitude/Longitude from the geometry
        - Symbol set to 'Navaid, White' for witness points
        - Other metadata fields (date, time, altitude) from existing waypoints
        """
        if not self._waypoints_layer:
            QMessageBox.warning(
                self,
                "No Waypoints Layer",
                "No waypoints layer found. Process claims first to generate waypoints."
            )
            return

        try:
            layer = self._waypoints_layer

            # Make sure the layer is editable
            was_editing = layer.isEditable()
            if not was_editing:
                layer.startEditing()

            # Find waypoints with missing lat/lon (manually added rows)
            rows_to_update = []
            for feature in layer.getFeatures():
                lat = feature.attribute('latitude')
                lon = feature.attribute('longitude')

                # Check if coordinates are missing or zero
                lat_missing = lat is None or lat == 0 or lat == 0.0
                lon_missing = lon is None or lon == 0 or lon == 0.0

                if lat_missing or lon_missing:
                    geom = feature.geometry()
                    if geom and not geom.isNull() and not geom.isEmpty():
                        rows_to_update.append(feature)

            if not rows_to_update:
                QMessageBox.information(
                    self,
                    "No Updates Needed",
                    "No new waypoints found to update. All waypoints already have coordinates."
                )
                return

            # Check that all rows have names
            unnamed = [f for f in rows_to_update if not f.attribute('name')]
            if unnamed:
                QMessageBox.warning(
                    self,
                    "Missing Names",
                    f"{len(unnamed)} waypoint(s) are missing the Name field.\n\n"
                    "Please fill in the Name field (e.g., 'WIT 1') before updating."
                )
                return

            # Get the layer CRS for coordinate transformation
            layer_crs = layer.crs()
            wgs84_crs = QgsCoordinateReferenceSystem("EPSG:4326")

            from qgis.core import QgsCoordinateTransform

            transform = QgsCoordinateTransform(layer_crs, wgs84_crs, QgsProject.instance())

            # Update each row
            updated_count = 0
            for feature in rows_to_update:
                geom = feature.geometry()
                point = geom.asPoint()

                # Transform to WGS84 if needed
                if layer_crs != wgs84_crs:
                    point = transform.transform(point)

                lat = point.y()
                lon = point.x()

                # Update attributes
                fid = feature.id()
                name_idx = layer.fields().indexOf('name')
                lat_idx = layer.fields().indexOf('latitude')
                lon_idx = layer.fields().indexOf('longitude')
                type_idx = layer.fields().indexOf('waypoint_type')

                if lat_idx >= 0:
                    layer.changeAttributeValue(fid, lat_idx, lat)
                if lon_idx >= 0:
                    layer.changeAttributeValue(fid, lon_idx, lon)
                if type_idx >= 0:
                    layer.changeAttributeValue(fid, type_idx, 'witness')

                updated_count += 1

            # Commit changes
            if was_editing:
                # User was editing, leave in edit mode
                pass
            else:
                layer.commitChanges()

            # Update internal waypoints list with the new witness points
            for feature in rows_to_update:
                geom = feature.geometry()
                point = geom.asPoint()

                if layer_crs != wgs84_crs:
                    point = transform.transform(point)

                self._processed_waypoints.append({
                    'name': feature.attribute('name'),
                    'claim': feature.attribute('claim') or '',
                    'type': 'witness',
                    'lat': point.y(),
                    'lon': point.x(),
                    'symbol': 'Navaid, White'
                })

            # Refresh layer display
            layer.triggerRepaint()

            # Re-apply styling to include witness points
            self._apply_waypoints_styling(layer)

            QMessageBox.information(
                self,
                "Update Complete",
                f"Successfully updated {updated_count} waypoint(s) with coordinates.\n\n"
                "Witness waypoints are marked with 'Navaid, White' symbol and will "
                "appear as white circles on the map."
            )

            self.status_message.emit(
                f"Updated {updated_count} witness waypoints", "info"
            )

        except Exception as e:
            self.logger.error(f"[QCLAIMS UI] Update waypoints failed: {e}")
            QMessageBox.critical(self, "Error", f"Failed to update waypoints: {e}")
