# -*- coding: utf-8 -*-
"""
Claims Order Widget for pay-per-claim users.

A simplified single-page widget for submitting claim orders with payment.
This is shown to pay-per-claim users instead of the full Claims Wizard.
"""
from typing import Optional, Dict, Any, List

from qgis.PyQt.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QMessageBox, QFrame, QComboBox,
    QScrollArea, QSpinBox, QDoubleSpinBox,
    QLineEdit, QFormLayout, QCheckBox
)
from qgis.PyQt.QtCore import Qt, pyqtSignal, QUrl
from qgis.PyQt.QtGui import QFont, QDesktopServices
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsPointXY,
    QgsCoordinateReferenceSystem, QgsCoordinateTransform
)

from ..managers.claims_manager import ClaimsManager
from ..processors.grid_generator import GridGenerator
from ..utils.logger import PluginLogger


class ClaimsOrderWidget(QWidget):
    """
    Simplified claims order widget for pay-per-claim users.

    Provides a single-page interface for:
    - Entering claimant information
    - Generating/positioning claim grids
    - Reviewing pricing
    - Submitting orders with Stripe payment

    Signals:
        status_message: Emitted when status needs to be shown (message, level)
        order_submitted: Emitted when order is submitted (order dict)
    """

    status_message = pyqtSignal(str, str)  # (message, level: 'info'/'warning'/'error')
    order_submitted = pyqtSignal(dict)

    def __init__(self, claims_manager: ClaimsManager, parent=None):
        """
        Initialize claims order widget.

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
        self._pricing: Dict[str, Any] = {}
        self._claims_layer: Optional[QgsVectorLayer] = None

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

        # === QClaims Access Group ===
        self.access_group = self._create_access_group()
        scroll_layout.addWidget(self.access_group)

        # === Claimant Information Group ===
        self.claimant_group = self._create_claimant_group()
        scroll_layout.addWidget(self.claimant_group)

        # === Coordinate System Group ===
        self.crs_group = self._create_crs_group()
        scroll_layout.addWidget(self.crs_group)

        # === Claim Layout Group ===
        self.layout_group = self._create_layout_group()
        scroll_layout.addWidget(self.layout_group)

        # === Submit Order Group ===
        self.submit_group = self._create_submit_group()
        scroll_layout.addWidget(self.submit_group)

        scroll_layout.addStretch()

        scroll.setWidget(scroll_content)
        layout.addWidget(scroll)

        # Initially disable submit until requirements met
        self._update_submit_state()

    # =========================================================================
    # Group Creation
    # =========================================================================

    def _create_access_group(self) -> QGroupBox:
        """Create the QClaims access status group."""
        group = QGroupBox("QClaims Access")
        group.setStyleSheet(self._get_group_style())
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        # Status row
        status_layout = QHBoxLayout()

        self.access_status_label = QLabel("Pay-Per-Claim")
        self.access_status_label.setStyleSheet(
            "font-weight: bold; color: #2563eb;"
        )
        status_layout.addWidget(self.access_status_label)

        status_layout.addStretch()

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setStyleSheet(self._get_secondary_button_style())
        self.refresh_btn.clicked.connect(self._refresh_access)
        self.refresh_btn.setMaximumWidth(80)
        status_layout.addWidget(self.refresh_btn)

        layout.addLayout(status_layout)

        # Pricing info
        self.pricing_label = QLabel("Checking pricing...")
        self.pricing_label.setStyleSheet("color: #6b7280;")
        layout.addWidget(self.pricing_label)

        return group

    def _create_claimant_group(self) -> QGroupBox:
        """Create the claimant information group."""
        group = QGroupBox("Claimant Information")
        group.setStyleSheet(self._get_group_style())
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        # Info label
        info_label = QLabel(
            "Enter the claimant details for your location notices. "
            "This information will appear on all generated documents."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: #6b7280; margin-bottom: 8px;")
        layout.addWidget(info_label)

        # Form layout
        form = QFormLayout()
        form.setSpacing(8)

        # Claimant name (required)
        self.claimant_name_edit = QLineEdit()
        self.claimant_name_edit.setPlaceholderText("Required")
        self.claimant_name_edit.setStyleSheet(self._get_input_style())
        self.claimant_name_edit.textChanged.connect(self._update_submit_state)
        form.addRow("Claimant Name*:", self.claimant_name_edit)

        # Address line 1 (required)
        self.address_1_edit = QLineEdit()
        self.address_1_edit.setPlaceholderText("Required")
        self.address_1_edit.setStyleSheet(self._get_input_style())
        self.address_1_edit.textChanged.connect(self._update_submit_state)
        form.addRow("Address Line 1*:", self.address_1_edit)

        # Address line 2
        self.address_2_edit = QLineEdit()
        self.address_2_edit.setPlaceholderText("City, State ZIP")
        self.address_2_edit.setStyleSheet(self._get_input_style())
        form.addRow("Address Line 2:", self.address_2_edit)

        # Address line 3
        self.address_3_edit = QLineEdit()
        self.address_3_edit.setStyleSheet(self._get_input_style())
        form.addRow("Address Line 3:", self.address_3_edit)

        # Mining district
        self.district_edit = QLineEdit()
        self.district_edit.setPlaceholderText("e.g., Elko Mining District")
        self.district_edit.setStyleSheet(self._get_input_style())
        form.addRow("Mining District:", self.district_edit)

        # Monument type
        self.monument_type_edit = QLineEdit()
        self.monument_type_edit.setText("2' wooden post")
        self.monument_type_edit.setStyleSheet(self._get_input_style())
        form.addRow("Monument Type:", self.monument_type_edit)

        layout.addLayout(form)

        return group

    def _create_crs_group(self) -> QGroupBox:
        """Create the coordinate system group."""
        group = QGroupBox("Coordinate System")
        group.setStyleSheet(self._get_group_style())
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        # Current CRS display
        crs_layout = QHBoxLayout()

        self.crs_label = QLabel("Current CRS: Not set")
        self.crs_label.setStyleSheet("font-weight: bold;")
        crs_layout.addWidget(self.crs_label)

        crs_layout.addStretch()

        self.auto_detect_btn = QPushButton("Auto-Detect UTM Zone")
        self.auto_detect_btn.setStyleSheet(self._get_secondary_button_style())
        self.auto_detect_btn.clicked.connect(self._auto_detect_utm)
        crs_layout.addWidget(self.auto_detect_btn)

        layout.addLayout(crs_layout)

        # UTM zone info
        self.utm_info_label = QLabel("")
        self.utm_info_label.setStyleSheet("color: #6b7280;")
        self.utm_info_label.setWordWrap(True)
        layout.addWidget(self.utm_info_label)

        return group

    def _create_layout_group(self) -> QGroupBox:
        """Create the claim layout group."""
        group = QGroupBox("Claim Layout")
        group.setStyleSheet(self._get_group_style())
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        # Info label
        info_label = QLabel(
            "Generate a grid of claim polygons or select an existing layer. "
            "You can edit the grid positions before submitting."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: #6b7280; margin-bottom: 8px;")
        layout.addWidget(info_label)

        # Grid generator controls
        form = QFormLayout()
        form.setSpacing(8)

        # Name prefix
        self.name_prefix_edit = QLineEdit("GE")
        self.name_prefix_edit.setMaximumWidth(100)
        self.name_prefix_edit.setStyleSheet(self._get_input_style())
        form.addRow("Name Prefix:", self.name_prefix_edit)

        # Rows and columns
        row_col_layout = QHBoxLayout()

        self.rows_spin = QSpinBox()
        self.rows_spin.setRange(1, 50)
        self.rows_spin.setValue(2)
        self.rows_spin.setMinimumWidth(60)
        row_col_layout.addWidget(QLabel("Rows:"))
        row_col_layout.addWidget(self.rows_spin)

        row_col_layout.addSpacing(20)

        self.cols_spin = QSpinBox()
        self.cols_spin.setRange(1, 50)
        self.cols_spin.setValue(4)
        self.cols_spin.setMinimumWidth(60)
        row_col_layout.addWidget(QLabel("Cols:"))
        row_col_layout.addWidget(self.cols_spin)

        row_col_layout.addStretch()
        form.addRow("Grid Size:", row_col_layout)

        # Azimuth
        self.azimuth_spin = QDoubleSpinBox()
        self.azimuth_spin.setRange(0, 360)
        self.azimuth_spin.setValue(0)
        self.azimuth_spin.setSuffix("°")
        self.azimuth_spin.setMinimumWidth(80)
        form.addRow("Azimuth:", self.azimuth_spin)

        layout.addLayout(form)

        # Generate button
        gen_layout = QHBoxLayout()

        self.generate_btn = QPushButton("Generate Grid at Map Center")
        self.generate_btn.setStyleSheet(self._get_success_button_style())
        self.generate_btn.clicked.connect(self._generate_grid)
        gen_layout.addWidget(self.generate_btn)

        gen_layout.addStretch()
        layout.addLayout(gen_layout)

        # Separator
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("background-color: #e5e7eb; margin: 8px 0;")
        layout.addWidget(line)

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

        # Grid tools row
        tools_layout = QHBoxLayout()

        self.auto_number_btn = QPushButton("Auto-Number")
        self.auto_number_btn.setToolTip("Assign sequential numbers based on position")
        self.auto_number_btn.setStyleSheet(self._get_secondary_button_style())
        self.auto_number_btn.clicked.connect(self._auto_number_claims)
        tools_layout.addWidget(self.auto_number_btn)

        self.rename_btn = QPushButton("Rename Claims")
        self.rename_btn.setToolTip("Rename claims with a new prefix")
        self.rename_btn.setStyleSheet(self._get_secondary_button_style())
        self.rename_btn.clicked.connect(self._rename_claims)
        tools_layout.addWidget(self.rename_btn)

        tools_layout.addStretch()
        layout.addLayout(tools_layout)

        # Claim count display
        self.claim_count_label = QLabel("Claims: 0")
        self.claim_count_label.setStyleSheet("font-weight: bold; color: #059669;")
        layout.addWidget(self.claim_count_label)

        return group

    def _create_submit_group(self) -> QGroupBox:
        """Create the submit order group."""
        group = QGroupBox("Submit Order")
        group.setStyleSheet(self._get_group_style())
        layout = QVBoxLayout(group)
        layout.setSpacing(12)

        # Pricing summary
        pricing_frame = QFrame()
        pricing_frame.setStyleSheet("""
            QFrame {
                background-color: #f3f4f6;
                border: 1px solid #e5e7eb;
                border-radius: 8px;
                padding: 12px;
            }
        """)
        pricing_layout = QVBoxLayout(pricing_frame)
        pricing_layout.setSpacing(4)

        self.summary_claims_label = QLabel("Claims: 0")
        self.summary_claims_label.setStyleSheet("font-size: 14px;")
        pricing_layout.addWidget(self.summary_claims_label)

        self.summary_price_label = QLabel("Price per claim: $0.00")
        self.summary_price_label.setStyleSheet("font-size: 14px;")
        pricing_layout.addWidget(self.summary_price_label)

        self.summary_total_label = QLabel("Total: $0.00")
        self.summary_total_label.setStyleSheet(
            "font-size: 18px; font-weight: bold; color: #059669;"
        )
        pricing_layout.addWidget(self.summary_total_label)

        layout.addWidget(pricing_frame)

        # Disclaimer checkbox
        self.disclaimer_checkbox = QCheckBox(
            "I understand that by submitting this order, I am requesting claim "
            "processing services. Payment is required to complete the order. "
            "Claims will be processed by geodb.io staff after payment."
        )
        self.disclaimer_checkbox.setWordWrap(True)
        self.disclaimer_checkbox.setStyleSheet("""
            QCheckBox {
                font-size: 12px;
                color: #374151;
                padding: 8px;
            }
            QCheckBox::indicator {
                width: 20px;
                height: 20px;
            }
        """)
        self.disclaimer_checkbox.stateChanged.connect(self._update_submit_state)
        layout.addWidget(self.disclaimer_checkbox)

        # Submit button
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.submit_btn = QPushButton("Submit & Pay")
        self.submit_btn.setStyleSheet(self._get_primary_button_style())
        self.submit_btn.clicked.connect(self._submit_order)
        self.submit_btn.setEnabled(False)
        self.submit_btn.setMinimumWidth(150)
        btn_layout.addWidget(self.submit_btn)

        layout.addLayout(btn_layout)

        return group

    # =========================================================================
    # Public Methods
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
        self._refresh_access()
        self._refresh_layers()
        self._update_crs_display()

    def refresh(self):
        """Refresh the widget state."""
        self._refresh_access()
        self._refresh_layers()
        self._update_crs_display()
        self._update_pricing_summary()

    # =========================================================================
    # Event Handlers
    # =========================================================================

    def _refresh_access(self):
        """Refresh QClaims access information."""
        try:
            self._access_info = self.claims_manager.check_access(force_refresh=True)
            self._pricing = self._access_info.get('pricing', {})

            # Update UI
            access_type = self._access_info.get('access_type', 'unknown')
            self.access_status_label.setText(f"Access: {access_type.replace('_', ' ').title()}")

            # Show pricing
            price_cents = self._pricing.get('self_service_per_claim_cents', 0)
            price_dollars = price_cents / 100
            self.pricing_label.setText(f"Pricing: ${price_dollars:.2f} per claim")

            self._update_pricing_summary()

        except Exception as e:
            self.logger.error(f"[CLAIMS ORDER] Failed to refresh access: {e}")
            self.access_status_label.setText("Access: Error")
            self.pricing_label.setText("Could not load pricing")

    def _refresh_layers(self):
        """Refresh the layers combo box."""
        self.layer_combo.clear()
        self.layer_combo.addItem("-- Select a layer --", None)

        for layer in QgsProject.instance().mapLayers().values():
            if isinstance(layer, QgsVectorLayer) and layer.geometryType() == 2:  # Polygon
                self.layer_combo.addItem(layer.name(), layer.id())

    def _on_layer_changed(self, index: int):
        """Handle layer selection change."""
        layer_id = self.layer_combo.currentData()

        if layer_id:
            self._claims_layer = QgsProject.instance().mapLayer(layer_id)
            if self._claims_layer:
                count = self._claims_layer.featureCount()
                self.claim_count_label.setText(f"Claims: {count}")
        else:
            self._claims_layer = None
            self.claim_count_label.setText("Claims: 0")

        self._update_pricing_summary()
        self._update_submit_state()

    def _update_crs_display(self):
        """Update the CRS display."""
        project_crs = QgsProject.instance().crs()
        if project_crs.isValid():
            self.crs_label.setText(f"Current CRS: {project_crs.authid()}")

            # Check if it's a UTM zone
            desc = project_crs.description()
            if 'UTM' in desc:
                self.utm_info_label.setText(f"UTM zone detected: {desc}")
                self.utm_info_label.setStyleSheet("color: #059669;")
            else:
                self.utm_info_label.setText(
                    "Note: Claims processing requires UTM coordinates. "
                    "Click 'Auto-Detect UTM Zone' to set the appropriate CRS."
                )
                self.utm_info_label.setStyleSheet("color: #f59e0b;")
        else:
            self.crs_label.setText("Current CRS: Not set")
            self.utm_info_label.setText("")

    def _auto_detect_utm(self):
        """Auto-detect and set UTM zone based on map center."""
        try:
            from qgis.utils import iface
            if not iface or not iface.mapCanvas():
                QMessageBox.warning(
                    self, "No Map", "Could not access map canvas."
                )
                return

            # Get map center
            center = iface.mapCanvas().center()
            current_crs = QgsProject.instance().crs()

            # Transform to WGS84 to get longitude
            wgs84 = QgsCoordinateReferenceSystem("EPSG:4326")
            transform = QgsCoordinateTransform(current_crs, wgs84, QgsProject.instance())
            wgs84_center = transform.transform(center)

            lon = wgs84_center.x()
            lat = wgs84_center.y()

            # Calculate UTM zone
            zone = int((lon + 180) / 6) + 1

            # Determine if northern or southern hemisphere
            if lat >= 0:
                epsg = 32600 + zone  # Northern hemisphere
                hemisphere = "N"
            else:
                epsg = 32700 + zone  # Southern hemisphere
                hemisphere = "S"

            # Set project CRS
            utm_crs = QgsCoordinateReferenceSystem(f"EPSG:{epsg}")
            QgsProject.instance().setCrs(utm_crs)

            self._update_crs_display()
            self.status_message.emit(
                f"Set CRS to UTM Zone {zone}{hemisphere} (EPSG:{epsg})", "info"
            )

        except Exception as e:
            self.logger.error(f"[CLAIMS ORDER] UTM auto-detect failed: {e}")
            QMessageBox.critical(self, "Error", f"Failed to detect UTM zone: {e}")

    def _generate_grid(self):
        """Generate a claim grid at the map center."""
        try:
            from qgis.utils import iface
            if not iface or not iface.mapCanvas():
                QMessageBox.warning(
                    self, "No Map", "Could not determine map center."
                )
                return

            center = iface.mapCanvas().center()

            # Get parameters
            name_prefix = self.name_prefix_edit.text() or "GE"
            rows = self.rows_spin.value()
            cols = self.cols_spin.value()
            azimuth = self.azimuth_spin.value()

            # Generate grid
            api_client = self.claims_manager.api if self.claims_manager else None
            generator = GridGenerator(api_client=api_client)
            layer = generator.generate_lode_grid(
                center, rows, cols,
                name_prefix=name_prefix,
                azimuth=azimuth
            )

            # Add to project
            QgsProject.instance().addMapLayer(layer)

            # Refresh and select the new layer
            self._refresh_layers()
            index = self.layer_combo.findText(layer.name())
            if index >= 0:
                self.layer_combo.setCurrentIndex(index)

            QMessageBox.information(
                self,
                "Grid Generated",
                f"Generated {rows * cols} lode claims.\n\n"
                "You can edit the polygons to adjust boundaries before submitting."
            )

            self.status_message.emit(f"Generated {rows * cols} claim grid", "info")

        except Exception as e:
            self.logger.error(f"[CLAIMS ORDER] Grid generation failed: {e}")
            QMessageBox.critical(self, "Error", f"Failed to generate grid: {e}")

    def _auto_number_claims(self):
        """Auto-number claims based on position."""
        if not self._claims_layer:
            QMessageBox.warning(self, "No Layer", "Please select a claims layer first.")
            return

        try:
            from ..processors.grid_processor import GridProcessor
            processor = GridProcessor()
            processor.auto_number_claims(self._claims_layer, self.name_prefix_edit.text())
            self._claims_layer.triggerRepaint()
            self.status_message.emit("Claims auto-numbered", "info")
        except Exception as e:
            self.logger.error(f"[CLAIMS ORDER] Auto-number failed: {e}")
            QMessageBox.critical(self, "Error", f"Failed to auto-number claims: {e}")

    def _rename_claims(self):
        """Rename claims with a new prefix."""
        if not self._claims_layer:
            QMessageBox.warning(self, "No Layer", "Please select a claims layer first.")
            return

        from qgis.PyQt.QtWidgets import QInputDialog
        new_prefix, ok = QInputDialog.getText(
            self, "Rename Claims",
            "Enter new name prefix:",
            text=self.name_prefix_edit.text()
        )

        if ok and new_prefix:
            try:
                from ..processors.grid_processor import GridProcessor
                processor = GridProcessor()
                processor.rename_claims(self._claims_layer, new_prefix)
                self._claims_layer.triggerRepaint()
                self.name_prefix_edit.setText(new_prefix)
                self.status_message.emit(f"Claims renamed with prefix: {new_prefix}", "info")
            except Exception as e:
                self.logger.error(f"[CLAIMS ORDER] Rename failed: {e}")
                QMessageBox.critical(self, "Error", f"Failed to rename claims: {e}")

    def _update_pricing_summary(self):
        """Update the pricing summary display."""
        claim_count = 0
        if self._claims_layer:
            claim_count = self._claims_layer.featureCount()

        price_cents = self._pricing.get('self_service_per_claim_cents', 0)
        price_per_claim = price_cents / 100
        total = claim_count * price_per_claim

        self.summary_claims_label.setText(f"Claims: {claim_count}")
        self.summary_price_label.setText(f"Price per claim: ${price_per_claim:.2f}")
        self.summary_total_label.setText(f"Total: ${total:.2f}")

    def _update_submit_state(self):
        """Update the submit button state based on form validity."""
        can_submit = True
        reasons = []

        # Check required fields
        if not self.claimant_name_edit.text().strip():
            can_submit = False
            reasons.append("Claimant name required")

        if not self.address_1_edit.text().strip():
            can_submit = False
            reasons.append("Address required")

        # Check claims layer
        if not self._claims_layer or self._claims_layer.featureCount() == 0:
            can_submit = False
            reasons.append("Select a layer with claims")

        # Check disclaimer
        if not self.disclaimer_checkbox.isChecked():
            can_submit = False
            reasons.append("Accept disclaimer")

        # Check project context
        if not self._current_project_id or not self._current_company_id:
            can_submit = False
            reasons.append("Select a project")

        self.submit_btn.setEnabled(can_submit)

        if not can_submit and reasons:
            self.submit_btn.setToolTip("Missing: " + ", ".join(reasons))
        else:
            self.submit_btn.setToolTip("Submit order and proceed to payment")

    def _submit_order(self):
        """Submit the claim order."""
        if not self._claims_layer:
            QMessageBox.warning(self, "No Claims", "Please select a claims layer.")
            return

        try:
            # Check TOS acceptance first
            tos_status = self.claims_manager.check_tos()
            if not tos_status.get('accepted'):
                # Show TOS dialog
                from .claims_tos_dialog import ClaimsTOSDialog
                tos_content = self.claims_manager.get_tos_content()
                dialog = ClaimsTOSDialog(tos_content, self)
                if dialog.exec_() != dialog.Accepted:
                    return

                # Accept TOS
                self.claims_manager.accept_tos()

            # Collect claimant info
            claimant_info = {
                'claimant_name': self.claimant_name_edit.text().strip(),
                'address_1': self.address_1_edit.text().strip(),
                'address_2': self.address_2_edit.text().strip(),
                'address_3': self.address_3_edit.text().strip(),
                'district': self.district_edit.text().strip(),
                'monument_type': self.monument_type_edit.text().strip(),
            }

            # Collect claims from layer
            claims = []
            for feature in self._claims_layer.getFeatures():
                geom = feature.geometry()
                name = feature.attribute('name') or f"Claim {feature.id()}"

                claims.append({
                    'name': name,
                    'geometry': geom.asJson(),
                })

            if not claims:
                QMessageBox.warning(self, "No Claims", "The selected layer has no features.")
                return

            # Submit order
            self.status_message.emit("Submitting order...", "info")

            result = self.claims_manager.submit_order(
                claims=claims,
                project_id=self._current_project_id,
                company_id=self._current_company_id,
                service_type='self_service',
                claimant_info=claimant_info
            )

            order_id = result.get('order_id')
            status = result.get('status')

            if status == 'approved' and result.get('payment_url'):
                # Open payment URL in browser
                QDesktopServices.openUrl(QUrl(result['payment_url']))
                QMessageBox.information(
                    self,
                    "Order Submitted",
                    f"Order #{order_id} has been submitted.\n\n"
                    "A browser window has been opened for payment. "
                    "After payment, your claims will be processed by our team "
                    "and you'll receive an email when complete."
                )
            else:
                # Create checkout session
                checkout = self.claims_manager.create_order_checkout(order_id)
                if checkout.get('checkout_url'):
                    QDesktopServices.openUrl(QUrl(checkout['checkout_url']))
                    QMessageBox.information(
                        self,
                        "Order Submitted",
                        f"Order #{order_id} has been submitted.\n\n"
                        "A browser window has been opened for payment."
                    )

            self.order_submitted.emit(result)
            self.status_message.emit(f"Order #{order_id} submitted", "info")

        except Exception as e:
            self.logger.error(f"[CLAIMS ORDER] Submit failed: {e}")
            QMessageBox.critical(self, "Error", f"Failed to submit order: {e}")
            self.status_message.emit(f"Order submission failed: {e}", "error")

    # =========================================================================
    # Styles
    # =========================================================================

    def _get_group_style(self) -> str:
        """Get group box style."""
        return """
            QGroupBox {
                font-weight: bold;
                font-size: 14px;
                color: #1f2937;
                border: 1px solid #e5e7eb;
                border-radius: 8px;
                margin-top: 16px;
                padding-top: 16px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 12px;
                padding: 0 8px;
                background-color: white;
            }
        """

    def _get_input_style(self) -> str:
        """Get input field style."""
        return """
            QLineEdit {
                padding: 6px 10px;
                border: 1px solid #d1d5db;
                border-radius: 4px;
                background-color: white;
            }
            QLineEdit:focus {
                border-color: #2563eb;
            }
        """

    def _get_combo_style(self) -> str:
        """Get combo box style."""
        return """
            QComboBox {
                padding: 6px 10px;
                border: 1px solid #d1d5db;
                border-radius: 4px;
                background-color: white;
            }
            QComboBox:focus {
                border-color: #2563eb;
            }
            QComboBox::drop-down {
                border: none;
                width: 24px;
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
                padding: 6px 12px;
                background-color: #ffffff;
                color: #374151;
                border: 1px solid #d1d5db;
                border-radius: 4px;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #f9fafb;
                border-color: #9ca3af;
            }
            QPushButton:pressed {
                background-color: #f3f4f6;
            }
        """

    def _get_success_button_style(self) -> str:
        """Get success button style."""
        return """
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
            QPushButton:pressed {
                background-color: #065f46;
            }
        """
