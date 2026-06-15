# -*- coding: utf-8 -*-
"""
Step 1: Project Setup

Handles:
- License check and TOS acceptance
- Set project to UTM (auto-detect zone from map center)
- Create or identify GeoPackage for claims storage
- Fill in claimant information
"""
from typing import List
from pathlib import Path
import math

from qgis.PyQt.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QFormLayout, QLineEdit, QFileDialog, QMessageBox,
    QFrame, QScrollArea, QComboBox
)
from qgis.PyQt.QtCore import QTimer
from qgis.core import QgsProject, QgsCoordinateReferenceSystem, QgsRectangle

from .step_base import ClaimsStepBase
from ...utils.compat import QFrame_NoFrame
from ...utils.crs_utils import is_utm_crs, auto_detect_utm
from ...utils.geometry import geojson_to_wkt
from ...utils.theme import T


class ClaimsStep1Widget(ClaimsStepBase):
    """
    Step 1: Project Setup

    This step configures the project CRS, GeoPackage storage, and claimant info.
    """

    def get_step_title(self) -> str:
        return "Project Setup"

    def get_step_description(self) -> str:
        return (
            "Configure your project for US lode mining claims. Set the coordinate system "
            "to UTM, create or select a GeoPackage for claims storage, and enter claimant information."
        )

    def __init__(self, state, claims_manager, parent=None, data_manager=None):
        super().__init__(state, claims_manager, parent, data_manager=data_manager)
        self._refresh_in_progress = False  # Guard against rapid repeated calls
        self._is_destroyed = False  # Flag to prevent crashes after unload
        self._setup_ui()

    def cleanup(self):
        """Clean up resources before deletion to prevent crashes."""
        super().cleanup()  # Sets _is_destroyed = True

    def _setup_ui(self):
        """Set up the step UI."""
        # Main layout with scroll area
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

        # License Status Group
        layout.addWidget(self._create_license_group())

        # UTM Configuration Group
        layout.addWidget(self._create_utm_group())

        # GeoPackage Group
        layout.addWidget(self._create_geopackage_group())

        # Claimant Information Group
        layout.addWidget(self._create_claimant_group())

        layout.addStretch()

        scroll.setWidget(scroll_content)
        main_layout.addWidget(scroll)

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
        self.license_details_label.setStyleSheet(f"color: {T.TEXT_MUTED};")
        layout.addWidget(self.license_details_label)

        # TOS status and button
        tos_layout = QHBoxLayout()

        self.tos_status_label = QLabel("Terms of Service: Unknown")
        self.tos_status_label.setStyleSheet(f"color: {T.TEXT_MUTED};")
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

    def _create_utm_group(self) -> QGroupBox:
        """Create the UTM configuration group."""
        group = QGroupBox("Coordinate Reference System")
        group.setStyleSheet(self._get_group_style())
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        # Info
        info_label = QLabel(
            "Mining claims must be in a UTM (Universal Transverse Mercator) projection. "
            "Click 'Auto-Detect UTM Zone' to determine the correct zone from your map center."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet(self._get_info_label_style())
        layout.addWidget(info_label)

        # Current CRS display
        crs_layout = QHBoxLayout()

        crs_layout.addWidget(QLabel("Current Project CRS:"))

        self.current_crs_label = QLabel("Not set")
        self.current_crs_label.setStyleSheet("font-weight: bold;")
        crs_layout.addWidget(self.current_crs_label)

        crs_layout.addStretch()

        layout.addLayout(crs_layout)

        # UTM status
        self.utm_status_label = QLabel("")
        self.utm_status_label.setStyleSheet(self._get_info_label_style())
        layout.addWidget(self.utm_status_label)

        # Buttons
        btn_layout = QHBoxLayout()

        self.auto_detect_btn = QPushButton("Auto-Detect UTM Zone")
        self.auto_detect_btn.setStyleSheet(self._get_primary_button_style())
        self.auto_detect_btn.clicked.connect(self._auto_detect_utm)
        btn_layout.addWidget(self.auto_detect_btn)

        self.apply_crs_btn = QPushButton("Apply to Project")
        self.apply_crs_btn.setStyleSheet(self._get_success_button_style())
        self.apply_crs_btn.clicked.connect(self._apply_crs)
        self.apply_crs_btn.setEnabled(False)
        btn_layout.addWidget(self.apply_crs_btn)

        btn_layout.addStretch()

        layout.addLayout(btn_layout)

        # Detected zone info
        self.detected_zone_label = QLabel("")
        self.detected_zone_label.setStyleSheet(f"color: {T.SUCCESS}; font-weight: bold;")
        layout.addWidget(self.detected_zone_label)

        return group

    def _create_geopackage_group(self) -> QGroupBox:
        """Create the GeoPackage configuration group."""
        group = QGroupBox("Claims GeoPackage")
        group.setStyleSheet(self._get_group_style())
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        # Info
        info_label = QLabel(
            "Select an existing claims GeoPackage from the server, browse for a "
            "local file, or create a new one. "
            "All claim metadata and layers will be stored in this file."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet(self._get_info_label_style())
        layout.addWidget(info_label)

        # Claim Name Prefix
        prefix_layout = QHBoxLayout()
        prefix_label = QLabel("Claim Name Prefix:")
        prefix_label.setToolTip(
            "Short prefix for claim names (e.g. 'GE' produces claims named GE 1, GE 2, ...).\n"
            "Also used to name the GeoPackage file and layer group."
        )
        prefix_layout.addWidget(prefix_label)

        self.name_prefix_edit = QLineEdit()
        self.name_prefix_edit.setStyleSheet(self._get_input_style())
        self.name_prefix_edit.setPlaceholderText("GE")
        self.name_prefix_edit.setText(self.state.grid_name_prefix or "GE")
        self.name_prefix_edit.setMaximumWidth(100)
        self.name_prefix_edit.setToolTip(
            "Short prefix for claim names (e.g. 'GE' produces claims named GE 1, GE 2, ...)"
        )
        prefix_layout.addWidget(self.name_prefix_edit)
        prefix_layout.addStretch()

        layout.addLayout(prefix_layout)

        # Server GeoPackage dropdown
        server_layout = QHBoxLayout()
        server_layout.addWidget(QLabel("From Server:"))

        self.server_gpkg_combo = QComboBox()
        self.server_gpkg_combo.setPlaceholderText("Select a claims GeoPackage...")
        self.server_gpkg_combo.setStyleSheet(self._get_input_style())
        self.server_gpkg_combo.currentIndexChanged.connect(self._on_server_gpkg_selected)
        server_layout.addWidget(self.server_gpkg_combo, 1)

        self.refresh_gpkg_btn = QPushButton("Refresh")
        self.refresh_gpkg_btn.setStyleSheet(self._get_secondary_button_style())
        self.refresh_gpkg_btn.setToolTip("Refresh the list of claims GeoPackages from the server")
        self.refresh_gpkg_btn.clicked.connect(self._refresh_server_geopackages)
        server_layout.addWidget(self.refresh_gpkg_btn)

        layout.addLayout(server_layout)

        # Current file
        file_layout = QHBoxLayout()

        file_layout.addWidget(QLabel("GeoPackage:"))

        self.gpkg_path_label = QLabel("Not selected")
        self.gpkg_path_label.setStyleSheet(f"color: {T.TEXT_MUTED};")
        file_layout.addWidget(self.gpkg_path_label, 1)

        layout.addLayout(file_layout)

        # Buttons
        btn_layout = QHBoxLayout()

        self.browse_gpkg_btn = QPushButton("Browse Local...")
        self.browse_gpkg_btn.setStyleSheet(self._get_secondary_button_style())
        self.browse_gpkg_btn.setToolTip("Browse for a GeoPackage file on your computer")
        self.browse_gpkg_btn.clicked.connect(self._browse_geopackage)
        btn_layout.addWidget(self.browse_gpkg_btn)

        self.create_gpkg_btn = QPushButton("Create New...")
        self.create_gpkg_btn.setStyleSheet(self._get_primary_button_style())
        self.create_gpkg_btn.clicked.connect(self._create_geopackage)
        btn_layout.addWidget(self.create_gpkg_btn)

        btn_layout.addStretch()

        layout.addLayout(btn_layout)

        # Separator + Load & Generate Maps button on its own line
        legacy_layout = QHBoxLayout()
        self.legacy_btn = QPushButton("Load Claims && Generate Maps...")
        self.legacy_btn.setStyleSheet(self._get_secondary_button_style())
        self.legacy_btn.setToolTip(
            "Load a claims GeoPackage (old QClaims or current plugin format)\n"
            "and generate field maps, filing maps, and state filing maps\n"
            "in one step — without re-running the wizard."
        )
        self.legacy_btn.clicked.connect(self._load_legacy_claims)
        legacy_layout.addWidget(self.legacy_btn)
        legacy_layout.addStretch()
        layout.addLayout(legacy_layout)

        return group

    def _create_claimant_group(self) -> QGroupBox:
        """Create the claimant information group."""
        group = QGroupBox("Claimant Information")
        group.setStyleSheet(self._get_group_style())
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        # Info
        info_label = QLabel(
            "Enter the claimant information that will appear on location notices."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet(self._get_info_label_style())
        layout.addWidget(info_label)

        # Form
        form = QFormLayout()
        form.setSpacing(8)

        self.claimant_name_edit = QLineEdit()
        self.claimant_name_edit.setStyleSheet(self._get_input_style())
        self.claimant_name_edit.setPlaceholderText("John Smith")
        self.claimant_name_edit.textChanged.connect(self.emit_validation_changed)
        form.addRow("Claimant Name:", self.claimant_name_edit)

        self.address1_edit = QLineEdit()
        self.address1_edit.setStyleSheet(self._get_input_style())
        self.address1_edit.setPlaceholderText("123 Main Street")
        self.address1_edit.textChanged.connect(self.emit_validation_changed)
        form.addRow("Address Line 1:", self.address1_edit)

        self.address2_edit = QLineEdit()
        self.address2_edit.setStyleSheet(self._get_input_style())
        self.address2_edit.setPlaceholderText("Suite 100 (optional)")
        form.addRow("Address Line 2:", self.address2_edit)

        self.address3_edit = QLineEdit()
        self.address3_edit.setStyleSheet(self._get_input_style())
        self.address3_edit.setPlaceholderText("City, State ZIP")
        form.addRow("Address Line 3:", self.address3_edit)

        self.district_edit = QLineEdit()
        self.district_edit.setStyleSheet(self._get_input_style())
        self.district_edit.setPlaceholderText("Mining District Name")
        form.addRow("Mining District:", self.district_edit)

        self.monument_type_edit = QLineEdit()
        self.monument_type_edit.setStyleSheet(self._get_input_style())
        self.monument_type_edit.setPlaceholderText("e.g. 2' wooden post")
        form.addRow("Monument Type:", self.monument_type_edit)

        layout.addLayout(form)

        return group

    # =========================================================================
    # License/TOS Methods
    # =========================================================================

    def _refresh_license(self):
        """Refresh license/access status."""
        # Guard against calls after widget is destroyed (from QTimer.singleShot)
        if self._is_destroyed:
            return
        # Guard against rapid repeated calls (can happen during UI events)
        if self._refresh_in_progress:
            return
        self._refresh_in_progress = True

        try:
            self.license_status_label.setText("Checking access...")
            self.license_details_label.setText("")
            self.tos_status_label.setText("Terms of Service: Checking...")
            self.accept_tos_btn.hide()
            # Note: Removed QApplication.processEvents() to prevent heap corruption crashes

            # Check access
            access_info = self.claims_manager.check_access(force_refresh=True)
            self.state.access_info = access_info

            access_type = access_info.get('access_type', 'unknown')
            is_staff = access_info.get('is_staff', False)

            # Update status display
            if is_staff:
                self.license_status_label.setText("Staff Access (Unlimited)")
                self.license_status_label.setStyleSheet(f"font-weight: bold; color: {T.SUCCESS};")
                self.license_details_label.setText("Full QClaims access with no limits.")
            elif access_type.startswith('enterprise'):
                monthly_limit = access_info.get('monthly_limit')
                used = access_info.get('claims_used_this_month', 0)

                self.license_status_label.setText(
                    f"Enterprise: {access_type.replace('enterprise_', '').title()}"
                )
                self.license_status_label.setStyleSheet(f"font-weight: bold; color: {T.ACCENT_TEXT};")

                if monthly_limit:
                    remaining = monthly_limit - used
                    self.license_details_label.setText(
                        f"Claims this month: {used} / {monthly_limit} ({remaining} remaining)"
                    )
                else:
                    self.license_details_label.setText("Unlimited claims")
            elif access_type == 'pay_per_claim':
                self.license_status_label.setText("Pay-Per-Claim")
                self.license_status_label.setStyleSheet(f"font-weight: bold; color: {T.WARNING_TEXT};")

                pricing = access_info.get('pricing', {})
                price_cents = pricing.get('self_service_per_claim_cents', 0)
                price_dollars = price_cents / 100 if price_cents else 0

                if price_dollars > 0:
                    self.license_details_label.setText(
                        f"${price_dollars:.2f} per claim. Payment required before processing."
                    )
                else:
                    self.license_details_label.setText("Pricing not available.")
            else:
                self.license_status_label.setText("No Access")
                self.license_status_label.setStyleSheet(f"font-weight: bold; color: {T.DANGER};")
                self.license_details_label.setText("Unable to determine access level.")

            # Check TOS
            tos_info = self.claims_manager.check_tos(force_refresh=True)
            if tos_info.get('accepted'):
                self.state.tos_accepted = True
                self.tos_status_label.setText(
                    f"Terms of Service: Accepted (v{tos_info.get('accepted_version', '?')})"
                )
                self.tos_status_label.setStyleSheet(f"color: {T.SUCCESS};")
                self.accept_tos_btn.hide()
            else:
                self.state.tos_accepted = False
                self.tos_status_label.setText("Terms of Service: Not Accepted")
                self.tos_status_label.setStyleSheet(f"color: {T.DANGER};")
                self.accept_tos_btn.show()

            # Show/hide staff orders button
            if is_staff:
                self.staff_orders_btn.show()
            else:
                self.staff_orders_btn.hide()

            # Notify the wizard that access level has been determined
            # so it can rebuild steps for pay-per-claim vs enterprise
            self.access_level_changed.emit(access_info)

            self.emit_validation_changed()

        except Exception as e:
            error_msg = str(e)
            if "Not authenticated" in error_msg or "login" in error_msg.lower():
                self.license_status_label.setText("Not Logged In")
                self.license_status_label.setStyleSheet(f"font-weight: bold; color: {T.WARNING_TEXT};")
                self.license_details_label.setText("Please login to check your QClaims access.")
                self.tos_status_label.setText("Terms of Service: Login Required")
                self.tos_status_label.setStyleSheet(f"color: {T.TEXT_MUTED};")
            else:
                self.license_status_label.setText("Error checking access")
                self.license_status_label.setStyleSheet(f"font-weight: bold; color: {T.DANGER};")
                self.license_details_label.setText(error_msg)
                self.emit_status(f"License check failed: {e}", "error")
        finally:
            self._refresh_in_progress = False

    def _accept_tos(self):
        """Show TOS dialog and accept if user agrees."""
        try:
            # Fetch TOS content
            tos_content = self.claims_manager.get_tos_content()

            # Show TOS dialog
            from ..claims_tos_dialog import ClaimsTOSDialog
            dialog = ClaimsTOSDialog(tos_content, self)

            if dialog.exec():
                # User accepted
                result = self.claims_manager.accept_tos()
                self.state.tos_accepted = True
                self.tos_status_label.setText(
                    f"Terms of Service: Accepted (v{result.get('version', '?')})"
                )
                self.tos_status_label.setStyleSheet(f"color: {T.SUCCESS};")
                self.accept_tos_btn.hide()
                self.emit_status("Terms of Service accepted", "success")
                self.emit_validation_changed()

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to accept Terms of Service: {e}")

    def _show_staff_orders(self):
        """Show staff pending orders dialog."""
        try:
            from ..staff_orders_dialog import StaffOrdersDialog

            dialog = StaffOrdersDialog(self.claims_manager, self)
            dialog.order_selected.connect(self._on_staff_order_selected)
            dialog.proposed_claims_selected.connect(self._on_proposed_claims_selected)

            dialog.exec()

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load pending orders: {e}")

    def _on_staff_order_selected(self, order_data: dict):
        """
        Handle staff selecting an order for fulfillment.

        Args:
            order_data: Order dict from staff_pending_orders API
        """
        try:
            # Set fulfillment context in wizard state
            self.state.set_fulfillment_context(order_data)

            # Switch project context to the order's project/company
            # This ensures subsequent API calls use the correct context
            project_id = order_data.get('project_id')
            company_id = order_data.get('company_id')

            if company_id and project_id:
                # Emit signal so main dialog can update project dropdown
                self.project_context_switched.emit(company_id, project_id)

            # Get claim polygons from order
            claim_polygons = order_data.get('claim_polygons', {})
            if not claim_polygons:
                QMessageBox.warning(
                    self,
                    "No Claims Data",
                    "This order does not have claim polygon data."
                )
                return

            # Create a memory layer from the GeoJSON and add to project
            self._load_order_polygons_to_layer(order_data)

            # Update claimant fields from order data if available
            self.load_state()

            self.emit_status(
                f"Loaded order {order_data.get('order_number')} - "
                f"{order_data.get('claim_count', 0)} claims ready for processing",
                "success"
            )

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load order into QGIS: {e}")

    def _on_proposed_claims_selected(self, claims_data: dict):
        """
        Handle staff selecting proposed claims for processing.

        Args:
            claims_data: Dict with project info and claim GeoJSON features
        """
        try:
            # Switch project context to the proposed claims' project/company
            project_id = claims_data.get('project_id')
            company_id = claims_data.get('company_id')
            project_name = claims_data.get('project_name', 'Unknown')

            if company_id and project_id:
                # Emit signal so main dialog can update project dropdown
                self.project_context_switched.emit(company_id, project_id)

            # Get claim features
            claims = claims_data.get('claims', [])
            if not claims:
                QMessageBox.warning(
                    self,
                    "No Claims",
                    "No claims were selected."
                )
                return

            # Clear any previous fulfillment context (proposed claims are not orders)
            self.state.clear_fulfillment_context()

            # Auto-populate claimant info from company address if available
            company_address = claims_data.get('company_address')
            if company_address:
                if company_address.get('claimant_name') and not self.state.claimant_name:
                    self.state.claimant_name = company_address['claimant_name']
                if company_address.get('address_1') and not self.state.address_line1:
                    self.state.address_line1 = company_address['address_1']
                if company_address.get('address_2') and not self.state.address_line2:
                    self.state.address_line2 = company_address['address_2']
                if company_address.get('address_3') and not self.state.address_line3:
                    self.state.address_line3 = company_address['address_3']

            # Auto-populate monument type from state-specific default
            default_monument = claims_data.get('default_monument_type')
            if default_monument and self.state.monument_type == "2' wooden post":
                # Only override the generic default, not user-customized values
                self.state.monument_type = default_monument

            # Create a memory layer from the GeoJSON features
            self._load_proposed_claims_to_layer(claims_data)

            # Reload state to update UI
            self.load_state()

            self.emit_status(
                f"Loaded {len(claims)} proposed claims from {project_name} - ready for processing",
                "success"
            )

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load proposed claims: {e}")

    def _load_proposed_claims_to_layer(self, claims_data: dict):
        """
        Load proposed claims into a QGIS memory layer.

        Args:
            claims_data: Dict with project info and claim GeoJSON features
        """
        from qgis.core import (
            QgsVectorLayer, QgsFeature, QgsGeometry, QgsField, QgsFields
        )
        from ...utils.compat import (
            FieldType_QString, FieldType_Int, FieldType_Double, FieldType_Bool,
        )

        claims = claims_data.get('claims', [])
        project_name = claims_data.get('project_name', 'Proposed')

        if not claims:
            raise ValueError("No claim features found")

        # Create memory layer
        layer = QgsVectorLayer(
            "Polygon?crs=EPSG:4326",
            f"{project_name} - Proposed Claims",
            "memory"
        )
        provider = layer.dataProvider()

        # Add fields
        fields = QgsFields()
        fields.append(QgsField("name", FieldType_QString))
        fields.append(QgsField("claim_type", FieldType_QString))
        fields.append(QgsField("acreage", FieldType_Double))
        fields.append(QgsField("plss_location", FieldType_QString))
        fields.append(QgsField("approved", FieldType_Bool))
        fields.append(QgsField("proposed_claim_id", FieldType_Int))
        provider.addAttributes(fields)
        layer.updateFields()

        # Add features
        for claim in claims:
            feature = QgsFeature()
            props = claim.get('properties', {})
            geom_data = claim.get('geometry')

            if not geom_data:
                continue

            geom = QgsGeometry.fromWkt(geojson_to_wkt(geom_data))
            if not geom or geom.isEmpty():
                continue

            feature.setGeometry(geom)

            # Set attributes
            feature.setAttributes([
                props.get('claim_name', 'Unknown'),
                props.get('claim_type', ''),
                props.get('acreage', 0.0),
                props.get('plss_location', ''),
                props.get('approved', False),
                claim.get('id')
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

    def _load_order_polygons_to_layer(self, order_data: dict):
        """
        Load order claim polygons into a QGIS memory layer.

        Args:
            order_data: Order dict containing claim_polygons GeoJSON
        """
        from qgis.core import (
            QgsVectorLayer, QgsFeature, QgsGeometry, QgsField, QgsFields
        )
        from ...utils.compat import FieldType_QString, FieldType_Int

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
        fields.append(QgsField("name", FieldType_QString))
        fields.append(QgsField("order_id", FieldType_Int))
        fields.append(QgsField("order_type", FieldType_QString))
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

            geom = QgsGeometry.fromWkt(geojson_to_wkt(geom_data))
            if not geom or geom.isEmpty():
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

    # GeoJSON-to-WKT conversion is now in utils.geometry.geojson_to_wkt

    # =========================================================================
    # UTM Methods
    # =========================================================================

    def _update_crs_display(self):
        """Update the current CRS display."""
        project = QgsProject.instance()
        crs = project.crs()

        if crs.isValid():
            self.current_crs_label.setText(f"{crs.authid()} - {crs.description()}")

            # Check if it's a UTM zone
            auth_id = crs.authid()
            if auth_id.startswith('EPSG:') and is_utm_crs(int(auth_id.split(':')[1])):
                self.utm_status_label.setText("Project is in UTM projection")
                self.utm_status_label.setStyleSheet(self._get_success_label_style())
                self.state.project_epsg = crs.postgisSrid()
            else:
                self.utm_status_label.setText("Warning: Project is NOT in UTM projection")
                self.utm_status_label.setStyleSheet(self._get_error_label_style())
        else:
            self.current_crs_label.setText("Not set")
            self.utm_status_label.setText("Please set a UTM projection")
            self.utm_status_label.setStyleSheet(self._get_error_label_style())

    # UTM CRS check is now in utils.crs_utils.is_utm_crs

    def _auto_detect_utm(self):
        """Auto-detect UTM zone from map center."""
        try:
            from qgis.utils import iface
            if not iface or not iface.mapCanvas():
                QMessageBox.warning(self, "No Map", "Please open a map view first.")
                return

            # Get map center
            center = iface.mapCanvas().center()

            # Transform to WGS84 if needed
            canvas_crs = iface.mapCanvas().mapSettings().destinationCrs()
            if canvas_crs.authid() != 'EPSG:4326':
                from qgis.core import QgsCoordinateTransform
                transform = QgsCoordinateTransform(
                    canvas_crs,
                    QgsCoordinateReferenceSystem('EPSG:4326'),
                    QgsProject.instance()
                )
                center = transform.transform(center)

            longitude = center.x()
            latitude = center.y()

            epsg = auto_detect_utm(longitude, latitude, prefer_nad83=True)
            zone = int(math.floor((longitude + 180) / 6)) + 1

            self._detected_epsg = epsg
            self.detected_zone_label.setText(
                f"Detected: UTM Zone {zone} (EPSG:{epsg})"
            )
            self.apply_crs_btn.setEnabled(True)

            self.emit_status(f"Detected UTM Zone {zone}", "info")

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to detect UTM zone: {e}")

    def _apply_crs(self):
        """Apply the detected CRS to the project."""
        if not hasattr(self, '_detected_epsg'):
            return

        try:
            crs = QgsCoordinateReferenceSystem(f'EPSG:{self._detected_epsg}')
            if not crs.isValid():
                raise ValueError(f"Invalid CRS: EPSG:{self._detected_epsg}")

            QgsProject.instance().setCrs(crs)
            self.state.project_epsg = self._detected_epsg

            self._update_crs_display()
            self.emit_status(f"Project CRS set to EPSG:{self._detected_epsg}", "success")
            self.emit_validation_changed()

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to set CRS: {e}")

    # =========================================================================
    # GeoPackage Methods
    # =========================================================================

    def _browse_geopackage(self):
        """Browse for an existing GeoPackage."""
        default_dir = str(Path.home() / "Documents")
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Claims GeoPackage",
            default_dir,
            "GeoPackage Files (*.gpkg);;All Files (*)"
        )

        if path:
            self._load_geopackage(path)

    def _load_legacy_claims(self):
        """Load a claims GeoPackage (old or new format) and generate maps."""
        default_dir = str(Path.home() / "Documents")
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Claims GeoPackage",
            default_dir,
            "GeoPackage Files (*.gpkg);;All Files (*)"
        )
        if not path:
            return

        from ...processors.legacy_claims_loader import (
            is_any_claims_geopackage, ClaimsLoader
        )

        if not is_any_claims_geopackage(path):
            QMessageBox.warning(
                self,
                "Not a Claims GeoPackage",
                "This file does not appear to be a claims GeoPackage.\n\n"
                "Expected a 'qclaims_metadata' table (old QClaims format) "
                "or a 'claims_metadata' table (current plugin format)."
            )
            return

        self.emit_status("Loading claims...", "info")

        try:
            loader = ClaimsLoader(path)
            results = loader.load_and_generate_maps()

            # Update path display
            self.gpkg_path_label.setText(path)

            # Zoom to the loaded layers
            self._zoom_to_loaded_layers(loader)

            # Report results
            map_names = list(results.values())
            self.emit_status(
                f"Generated {len(map_names)} map(s): {', '.join(map_names)}",
                "success"
            )
            QMessageBox.information(
                self,
                "Maps Generated",
                f"Successfully loaded claims and generated {len(map_names)} map layout(s):\n\n"
                + '\n'.join(f"  - {name}" for name in map_names)
                + "\n\nOpen the Print Layout manager (Project > Layouts) to view and export them."
            )
        except Exception as e:
            QMessageBox.critical(
                self,
                "Error",
                f"Failed to load claims:\n\n{e}"
            )
            import traceback
            traceback.print_exc()

    def _zoom_to_loaded_layers(self, loader):
        """Zoom map canvas to the extent of layers loaded by a claims loader."""
        try:
            from qgis.utils import iface
            if not iface or not iface.mapCanvas():
                return

            combined = QgsRectangle()
            for layer in loader._loaded_layers.values():
                if layer.isValid() and not layer.extent().isEmpty():
                    if combined.isEmpty():
                        combined = layer.extent()
                    else:
                        combined.combineExtentWith(layer.extent())

            if not combined.isEmpty():
                combined.scale(1.1)
                iface.mapCanvas().setExtent(combined)
                iface.mapCanvas().refresh()
        except Exception:
            pass

    def _refresh_server_geopackages(self):
        """Fetch claims-linked GeoPackages from the server for the current project."""
        if not self.state.project_id:
            self.emit_status("Select a project first to see server GeoPackages.", "warning")
            return

        # Block the signal while we repopulate to avoid triggering _on_server_gpkg_selected
        self.server_gpkg_combo.blockSignals(True)
        self.server_gpkg_combo.clear()

        try:
            from ...utils.logger import PluginLogger
            logger = PluginLogger.get_logger()

            # Fetch GeoPackage ProjectFiles linked to ClaimPackages
            # With include_deletion_metadata=False, returns a plain list
            records = self.claims_manager.api.get_all_paginated(
                model_name='ProjectFile',
                project_id=self.state.project_id,
                params={
                    'category': 'GP',
                    'linked_to_claim_packages': 'true',
                    'include_claim_package': 'true',
                },
                include_deletion_metadata=False,
            )
            if not records:
                self.server_gpkg_combo.addItem("No claims GeoPackages found", None)
                self.server_gpkg_combo.setEnabled(False)
                self.emit_status("No claims GeoPackages found on server.", "info")
            else:
                self.server_gpkg_combo.setEnabled(True)
                # Add a placeholder first item
                self.server_gpkg_combo.addItem("Select a claims GeoPackage...", None)
                for record in records:
                    cp_info = record.get('claim_package_info') or {}
                    pkg_num = cp_info.get('package_number', '')
                    name = record.get('name', 'Unknown')
                    display = f"{pkg_num} \u2014 {name}" if pkg_num else name
                    self.server_gpkg_combo.addItem(display, record)

                self.emit_status(
                    f"Found {len(records)} claims GeoPackage(s) on server.", "success"
                )
                logger.info(
                    f"[STEP1] Found {len(records)} claims GeoPackage(s) for "
                    f"project {self.state.project_id}"
                )

        except Exception as e:
            self.server_gpkg_combo.addItem("Error loading from server", None)
            self.server_gpkg_combo.setEnabled(False)
            self.emit_status(f"Failed to fetch server GeoPackages: {e}", "error")

        finally:
            self.server_gpkg_combo.blockSignals(False)

    def _on_server_gpkg_selected(self, index: int):
        """Handle selection of a server GeoPackage from the dropdown."""
        if index < 0:
            return

        server_file = self.server_gpkg_combo.itemData(index)
        if not server_file or not isinstance(server_file, dict):
            return

        file_id = server_file.get('id')
        file_url = server_file.get('file_url', '')
        if not file_id or not file_url:
            return

        try:
            from ...utils.logger import PluginLogger
            from ...utils.gpkg_utils import download_geopackage

            logger = PluginLogger.get_logger()

            # Determine cache directory
            from qgis.core import QgsApplication
            cache_dir = Path(QgsApplication.qgisSettingsDirPath()) / 'geodb_cache' / 'geopackages'

            # Resolve base URL for relative file_url values
            base_url = None
            try:
                from urllib.parse import urlparse
                endpoint = self.claims_manager.api.config.base_url
                parsed = urlparse(endpoint)
                base_url = f"{parsed.scheme}://{parsed.netloc}"
            except Exception:
                pass

            self.emit_status("Downloading GeoPackage from server...", "info")

            local_path = download_geopackage(
                file_id=file_id,
                file_url=file_url,
                cache_dir=cache_dir,
                base_url=base_url,
                force_refresh=True,  # Always get latest version
            )

            if not local_path:
                QMessageBox.warning(self, "Download Failed", "Could not download the GeoPackage.")
                return

            # Load the GeoPackage (restores metadata from its claims_metadata table)
            self._load_geopackage(local_path)

            # Restore claim_package_id from the server response if the GeoPackage
            # didn't have it in its metadata (e.g., uploaded by another user)
            cp_info = server_file.get('claim_package_info') or {}
            if cp_info.get('id') and not self.state.claim_package_id:
                self.state.claim_package_id = cp_info['id']
                logger.info(
                    f"[STEP1] Restored claim_package_id={cp_info['id']} "
                    f"from server response"
                )

            self.emit_status(
                f"Loaded GeoPackage from server: {server_file.get('name', '')}",
                "success"
            )

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to download GeoPackage: {e}")

    def _create_geopackage(self):
        """Create a new GeoPackage, using the claim prefix for the default filename."""
        prefix = self.name_prefix_edit.text().strip() or "claims"
        default_name = f"{prefix}_claims.gpkg"
        default_path = str(Path.home() / "Documents" / default_name)
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Create Claims GeoPackage",
            default_path,
            "GeoPackage Files (*.gpkg)"
        )

        if path:
            if not path.endswith('.gpkg'):
                path += '.gpkg'

            try:
                # Save prefix to state before creating the GeoPackage
                self.state.grid_name_prefix = self.name_prefix_edit.text().strip() or "GE"

                # Create empty GeoPackage by initializing state storage
                # IMPORTANT: Clear claim_package_id since this is a fresh GeoPackage
                # Any previous package ID from another GeoPackage is now invalid
                self.state.claim_package_id = None
                self.state.geopackage_path = path
                self.state.save_to_geopackage()

                self.gpkg_path_label.setText(path)
                self.emit_status(f"Created GeoPackage: {Path(path).name}", "success")
                self.emit_validation_changed()

            except Exception as e:
                QMessageBox.critical(self, "Error", f"Failed to create GeoPackage: {e}")

    def _load_geopackage(self, path: str):
        """Load metadata and layers from an existing GeoPackage."""
        try:
            if self.state.load_from_geopackage(path):
                self.gpkg_path_label.setText(path)
                self.load_state()  # Populate form fields
                self.emit_status(f"Loaded GeoPackage: {Path(path).name}", "success")
            else:
                # New GeoPackage without metadata - just set the path
                # Clear claim_package_id since there's no metadata to load from
                self.state.claim_package_id = None
                self.state.geopackage_path = path
                self.gpkg_path_label.setText(path)
                self.emit_status(f"Selected GeoPackage: {Path(path).name}", "info")

            # Load any existing claims layers from the GeoPackage with their saved styles
            self._load_claims_layers_from_geopackage(path)

            self.emit_validation_changed()

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load GeoPackage: {e}")

    def _get_claims_group_name(self) -> str:
        """Build the Claims Workflow group name from the current prefix."""
        prefix = self.name_prefix_edit.text().strip()
        if prefix:
            return f"Claims Workflow [{prefix} Lode Claims]"
        return "Claims Workflow"

    def _load_claims_layers_from_geopackage(self, gpkg_path: str):
        """
        Load all claims layers from a GeoPackage with their saved styles.

        When layers have default styles saved in the GeoPackage (via saveStyleToDatabase),
        QGIS will automatically apply them when loading.

        Args:
            gpkg_path: Path to the GeoPackage file
        """
        try:
            from ...processors.claims_layer_generator import ClaimsLayerGenerator

            # Create a layer generator instance
            generator = ClaimsLayerGenerator(
                claims_storage_manager=None,
                claims_manager=self.claims_manager
            )

            # Load all claims layers from the GeoPackage
            # Use prefix-based group name so layers go into the right group
            loaded_layers = generator.load_layers_from_geopackage(
                gpkg_path,
                group_name=self._get_claims_group_name()
            )

            if loaded_layers:
                layer_names = list(loaded_layers.keys())
                self.emit_status(
                    f"Loaded {len(loaded_layers)} layer(s) from GeoPackage: {', '.join(layer_names)}",
                    "success"
                )

                # Zoom to extent of loaded layers if we have valid extents
                from qgis.utils import iface
                from qgis.core import QgsRectangle
                if iface and iface.mapCanvas():
                    combined_extent = QgsRectangle()
                    for layer in loaded_layers.values():
                        if layer.isValid() and not layer.extent().isEmpty():
                            if combined_extent.isEmpty():
                                combined_extent = layer.extent()
                            else:
                                combined_extent.combineExtentWith(layer.extent())

                    if not combined_extent.isEmpty():
                        # Add 10% buffer around extent
                        combined_extent.scale(1.1)
                        iface.mapCanvas().setExtent(combined_extent)
                        iface.mapCanvas().refresh()

        except ImportError as e:
            # ClaimsLayerGenerator not available - skip layer loading
            self.emit_status(f"Could not load layers: {e}", "warning")
        except Exception as e:
            # Non-fatal - just log the error
            self.emit_status(f"Could not load layers from GeoPackage: {e}", "warning")

    # =========================================================================
    # ClaimsStepBase Implementation
    # =========================================================================

    def validate(self) -> List[str]:
        """Validate the step."""
        errors = []

        # Check UTM
        if not self.state.project_epsg:
            errors.append("Project must be set to a UTM coordinate system")

        # Check claimant info
        if not self.claimant_name_edit.text().strip():
            errors.append("Claimant name is required")

        if not self.address1_edit.text().strip():
            errors.append("At least one address line is required")

        # TOS is recommended but not blocking
        if not self.state.tos_accepted:
            errors.append("Terms of Service must be accepted (click 'View Terms')")

        return errors

    def on_enter(self):
        """Called when step becomes active."""
        self._update_crs_display()
        # Defer API call to avoid nested event loop crash on Windows
        # when called from mouse event handlers (step indicator clicks)
        QTimer.singleShot(0, self._refresh_license)
        self.load_state()
        # Refresh server GeoPackages if a project is selected
        if self.state.project_id:
            QTimer.singleShot(100, self._refresh_server_geopackages)

    def on_leave(self):
        """Called when leaving step."""
        self.save_state()

    def save_state(self):
        """Save widget state to shared state."""
        self.state.grid_name_prefix = self.name_prefix_edit.text().strip() or "GE"
        self.state.claimant_name = self.claimant_name_edit.text().strip()
        self.state.address_line1 = self.address1_edit.text().strip()
        self.state.address_line2 = self.address2_edit.text().strip()
        self.state.address_line3 = self.address3_edit.text().strip()
        self.state.mining_district = self.district_edit.text().strip()
        self.state.monument_type = self.monument_type_edit.text().strip()

        # Save to GeoPackage if we have one
        if self.state.geopackage_path:
            self.state.save_to_geopackage()

    def load_state(self):
        """Load widget state from shared state."""
        self.name_prefix_edit.setText(self.state.grid_name_prefix or "GE")
        self.claimant_name_edit.setText(self.state.claimant_name)
        self.address1_edit.setText(self.state.address_line1)
        self.address2_edit.setText(self.state.address_line2)
        self.address3_edit.setText(self.state.address_line3)
        self.district_edit.setText(self.state.mining_district)
        self.monument_type_edit.setText(self.state.monument_type or "")

        if self.state.geopackage_path:
            self.gpkg_path_label.setText(self.state.geopackage_path)
        else:
            self.gpkg_path_label.setText("Not selected")
