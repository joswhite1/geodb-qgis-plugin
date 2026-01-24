# -*- coding: utf-8 -*-
"""
Step 6: Finalize Documents

Handles:
- Validate grid geometry
- Align corners
- Process claims (server-side)
- Generate documents (server-side)
"""
from typing import List, Dict, Any, Optional

from qgis.PyQt.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QProgressBar, QTableWidget, QTableWidgetItem,
    QHeaderView, QFrame, QScrollArea, QMessageBox, QApplication
)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtCore import QUrl
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsFeature, QgsGeometry,
    QgsField, QgsFields, QgsPointXY, QgsCoordinateReferenceSystem,
    QgsCoordinateTransform, QgsMarkerSymbol, QgsCategorizedSymbolRenderer,
    QgsRendererCategory
)
from qgis.PyQt.QtCore import QVariant

from .step_base import ClaimsStepBase


class ClaimsStep6Widget(ClaimsStepBase):
    """
    Step 6: Finalize Documents

    Validate, process, and generate documents for claims.
    """

    def get_step_title(self) -> str:
        return "Finalize Documents"

    def get_step_description(self) -> str:
        return (
            "Validate your claim grid, align corners for precise boundaries, then process "
            "claims on the server to calculate PLSS descriptions, corners, and monuments. "
            "Finally, generate location notice documents."
        )

    def __init__(self, state, claims_manager, parent=None):
        super().__init__(state, claims_manager, parent)
        self._corner_processor = None
        self._grid_processor = None
        # Get logger for this module
        from ...utils.logger import PluginLogger
        self.logger = PluginLogger.get_logger()
        self._setup_ui()

    def _get_corner_processor(self):
        """Lazy-load corner alignment processor."""
        if self._corner_processor is None:
            from ...processors.corner_alignment import CornerAlignmentProcessor
            self._corner_processor = CornerAlignmentProcessor()
        return self._corner_processor

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

        # Validation Group
        layout.addWidget(self._create_validation_group())

        # Processing Group
        layout.addWidget(self._create_processing_group())

        # Results Group
        layout.addWidget(self._create_results_group())

        # Documents Group
        layout.addWidget(self._create_documents_group())

        layout.addStretch()

        scroll.setWidget(scroll_content)
        main_layout.addWidget(scroll)

    def _create_validation_group(self) -> QGroupBox:
        """Create the validation group."""
        group = QGroupBox("Validate & Prepare")
        group.setStyleSheet(self._get_group_style())
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        # Info
        info_label = QLabel(
            "Before processing, validate your grid to check for issues and align "
            "corners to ensure adjacent claims share precise boundary coordinates."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet(self._get_info_label_style())
        layout.addWidget(info_label)

        # Buttons
        btn_layout = QHBoxLayout()

        self.validate_btn = QPushButton("Validate Grid")
        self.validate_btn.setToolTip("Check for geometry issues")
        self.validate_btn.setStyleSheet(self._get_secondary_button_style())
        self.validate_btn.clicked.connect(self._validate_grid)
        btn_layout.addWidget(self.validate_btn)

        self.align_btn = QPushButton("Align Corners")
        self.align_btn.setToolTip("Snap nearby corners to shared positions")
        self.align_btn.setStyleSheet(self._get_secondary_button_style())
        self.align_btn.clicked.connect(self._align_corners)
        btn_layout.addWidget(self.align_btn)

        btn_layout.addStretch()

        layout.addLayout(btn_layout)

        # Status label
        self.validation_status_label = QLabel("")
        self.validation_status_label.setStyleSheet(self._get_info_label_style())
        layout.addWidget(self.validation_status_label)

        return group

    def _create_processing_group(self) -> QGroupBox:
        """Create the processing group."""
        group = QGroupBox("Process Claims")
        group.setStyleSheet(self._get_group_style())
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        # Info
        self.processing_info_label = QLabel(
            "Send claims to the server for processing. The server will calculate "
            "PLSS descriptions, corner coordinates, monument positions, and filing deadlines."
        )
        self.processing_info_label.setWordWrap(True)
        self.processing_info_label.setStyleSheet(self._get_info_label_style())
        layout.addWidget(self.processing_info_label)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setStyleSheet("""
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
        """)
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)

        # Buttons
        btn_layout = QHBoxLayout()

        self.process_btn = QPushButton("Process Claims")
        self.process_btn.setStyleSheet(self._get_primary_button_style())
        self.process_btn.clicked.connect(self._process_claims)
        btn_layout.addWidget(self.process_btn)

        # Pricing label (for pay-per-claim users)
        self.pricing_label = QLabel("")
        self.pricing_label.setStyleSheet("color: #2563eb; font-weight: bold;")
        btn_layout.addWidget(self.pricing_label)

        btn_layout.addStretch()

        layout.addLayout(btn_layout)

        return group

    def _create_results_group(self) -> QGroupBox:
        """Create the results display group."""
        group = QGroupBox("Processing Results")
        group.setStyleSheet(self._get_group_style())
        layout = QVBoxLayout(group)

        self.results_table = QTableWidget()
        self.results_table.setColumnCount(4)
        self.results_table.setHorizontalHeaderLabels(["Claim", "State", "PLSS", "Corners"])
        self.results_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.results_table.verticalHeader().setVisible(False)
        self.results_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.results_table.setMaximumHeight(200)
        self.results_table.setStyleSheet("""
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
        """)
        layout.addWidget(self.results_table)

        group.hide()
        self.results_group = group
        return group

    def _create_documents_group(self) -> QGroupBox:
        """Create the documents group."""
        group = QGroupBox("Documents")
        group.setStyleSheet(self._get_group_style())
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        # Info
        info_label = QLabel(
            "Generate location notice documents for filing. Documents are created "
            "on the server and bundled into a Claim Package for easy download."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet(self._get_info_label_style())
        layout.addWidget(info_label)

        # Buttons
        btn_layout = QHBoxLayout()

        self.generate_docs_btn = QPushButton("Generate Location Notices")
        self.generate_docs_btn.setStyleSheet(self._get_secondary_button_style())
        self.generate_docs_btn.clicked.connect(self._generate_documents)
        self.generate_docs_btn.setEnabled(False)
        btn_layout.addWidget(self.generate_docs_btn)

        btn_layout.addStretch()

        layout.addLayout(btn_layout)

        # Documents status
        self.docs_status_label = QLabel("")
        self.docs_status_label.setStyleSheet(self._get_info_label_style())
        layout.addWidget(self.docs_status_label)

        return group

    # =========================================================================
    # Validation Methods
    # =========================================================================

    def _validate_grid(self):
        """Validate the claims grid."""
        layer = self.state.claims_layer
        if not layer:
            QMessageBox.warning(self, "No Layer", "Please select a claims layer first.")
            return

        try:
            processor = self._get_grid_processor()
            issues = processor.validate_grid_geometry(layer)

            if not issues:
                self.validation_status_label.setText("Validation passed - no issues found")
                self.validation_status_label.setStyleSheet(self._get_success_label_style())
                self.emit_status("Grid validation passed", "success")
            else:
                errors = [i for i in issues if i.get('severity') == 'error']
                warnings = [i for i in issues if i.get('severity') == 'warning']

                msg = f"Found {len(errors)} error(s) and {len(warnings)} warning(s):\n\n"
                for issue in issues[:10]:
                    name = issue.get('name', 'Unknown')
                    desc = issue.get('issue', '')
                    severity = issue.get('severity', 'warning').upper()
                    msg += f"[{severity}] {name}: {desc}\n"

                if len(issues) > 10:
                    msg += f"\n... and {len(issues) - 10} more issues."

                QMessageBox.warning(self, "Validation Issues", msg)
                self.validation_status_label.setText(f"Found {len(issues)} issues")
                self.validation_status_label.setStyleSheet(self._get_error_label_style())
                self.emit_status(f"Found {len(issues)} validation issues", "warning")

        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def _align_corners(self):
        """Align nearby corners to shared positions."""
        layer = self.state.claims_layer
        if not layer:
            QMessageBox.warning(self, "No Layer", "Please select a claims layer first.")
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
            processor = self._get_corner_processor()
            result = processor.align_corners(layer, tolerance_m=1.0)

            self.validation_status_label.setText(
                f"Aligned {result['corners_moved']} corners in {result['clusters_found']} clusters"
            )
            self.validation_status_label.setStyleSheet(self._get_success_label_style())

            QMessageBox.information(
                self,
                "Alignment Complete",
                f"Found {result['clusters_found']} corner clusters.\n"
                f"Moved {result['corners_moved']} corners.\n"
                f"Maximum adjustment: {result['max_adjustment']:.3f}m"
            )

            self.emit_status(f"Aligned {result['corners_moved']} corners", "success")
            layer.triggerRepaint()

        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    # =========================================================================
    # Processing Methods
    # =========================================================================

    def _process_claims(self):
        """Process claims on the server."""
        layer = self.state.claims_layer
        if not layer:
            QMessageBox.warning(self, "No Layer", "Please select a claims layer first.")
            return

        if not self.state.project_id:
            QMessageBox.warning(self, "No Project", "Please select a project first.")
            return

        # Get features
        features = list(layer.getFeatures())
        if not features:
            QMessageBox.warning(self, "No Features", "The layer has no features.")
            return

        # Prepare claims data
        # IMPORTANT: Use WKT in the layer's native CRS (typically UTM) to preserve
        # exact corner coordinates. Using asJson() would convert to WGS84, causing
        # precision loss that breaks shared corner detection between adjacent claims.
        layer_epsg = layer.crs().postgisSrid()
        claims = []
        for feature in features:
            geom = feature.geometry()
            if geom.isNull() or geom.isEmpty():
                continue

            # Get name from attribute
            name = None
            for field_name in ['name', 'Name', 'NAME', 'claim_name', 'CLAIM_NAME']:
                idx = layer.fields().indexOf(field_name)
                if idx >= 0:
                    name = feature.attribute(idx)
                    break

            if not name:
                name = f"Claim {feature.id()}"

            # Get notes from attribute (for location notices)
            notes = None
            for field_name in ['notes', 'Notes', 'NOTES']:
                idx = layer.fields().indexOf(field_name)
                if idx >= 0:
                    notes = feature.attribute(idx)
                    break

            claims.append({
                'name': str(name),
                'geometry': geom.asWkt(),  # WKT in layer's native CRS (UTM)
                'epsg': layer_epsg,
                'claim_type': 'lode',
                'notes': str(notes) if notes else ''  # Per-claim notes for location notices
            })

        if not claims:
            QMessageBox.warning(self, "No Valid Claims", "No valid polygon geometries found.")
            return

        # Check access level
        can_process = self.state.access_info and self.state.access_info.get('can_process_immediately', False)

        if can_process:
            self._execute_processing(claims)
        else:
            self._submit_order(claims)

    def _execute_processing(self, claims: List[Dict[str, Any]]):
        """Execute immediate processing (Enterprise/Staff)."""
        self.progress_bar.show()
        self.progress_bar.setValue(0)
        self.process_btn.setEnabled(False)
        # Note: Removed QApplication.processEvents() to prevent heap corruption crashes

        try:
            self.progress_bar.setValue(20)
            self.emit_status("Sending claims to server for processing...", "info")
            # Note: Removed QApplication.processEvents() to prevent heap corruption crashes

            result = self.claims_manager.process_claims(claims, self.state.project_id)

            self.progress_bar.setValue(50)
            self.emit_status("Creating QGIS layers...", "info")
            # Note: Removed QApplication.processEvents() to prevent heap corruption crashes

            # Store results
            self.state.processed_claims = result.get('claims', [])
            self.state.processed_waypoints = result.get('waypoints', [])

            # Create QGIS layers from results
            self._create_result_layers()

            self.progress_bar.setValue(100)

            # Update UI
            self._display_results(result)

            # Enable document generation
            self.generate_docs_btn.setEnabled(True)

            self.emit_status(
                f"Processed {len(self.state.processed_claims)} claims successfully",
                "success"
            )
            self.emit_validation_changed()

        except Exception as e:
            QMessageBox.critical(self, "Processing Error", str(e))
            self.emit_status(f"Processing failed: {e}", "error")

        finally:
            self.progress_bar.hide()
            self.process_btn.setEnabled(True)

    def _submit_order(self, claims: List[Dict[str, Any]]):
        """Submit order for pay-per-claim users."""
        pricing = self.state.access_info.get('pricing', {})
        price_cents = pricing.get('self_service_per_claim_cents', 0)
        total_cents = price_cents * len(claims)
        total_dollars = total_cents / 100

        reply = QMessageBox.question(
            self,
            "Submit Order",
            f"Submit order for {len(claims)} claims?\n\n"
            f"Estimated total: ${total_dollars:.2f}\n\n"
            "A browser window will open for payment after submission.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply != QMessageBox.Yes:
            return

        self.progress_bar.show()
        self.progress_bar.setValue(25)
        self.process_btn.setEnabled(False)
        # Note: Removed QApplication.processEvents() to prevent heap corruption crashes

        try:
            # Step 1: Submit the order
            result = self.claims_manager.submit_order(
                claims=claims,
                project_id=self.state.project_id,
                company_id=self.state.company_id,
                service_type='self_service'
            )

            self.progress_bar.setValue(50)
            # Note: Removed QApplication.processEvents() to prevent heap corruption crashes

            order_id = result.get('order_id')
            status = result.get('status', 'unknown')
            total_display = result.get('total_display', f'${total_dollars:.2f}')

            # Step 2: If order is approved (or auto-approved), create checkout and open browser
            if status == 'approved':
                self.progress_bar.setValue(75)
                # Note: Removed QApplication.processEvents() to prevent heap corruption crashes

                try:
                    # Get checkout URL from server
                    checkout_result = self.claims_manager.create_order_checkout(order_id)
                    checkout_url = checkout_result.get('checkout_url')

                    if checkout_url:
                        self.progress_bar.setValue(100)

                        # Open browser for payment
                        QDesktopServices.openUrl(QUrl(checkout_url))

                        msg = (
                            f"Order #{order_id} submitted!\n\n"
                            f"Total: {total_display}\n"
                            f"Claims: {len(claims)}\n\n"
                            "A browser window has opened for payment.\n\n"
                            "After completing payment, your claims will be processed "
                            "and added to your project. You can close this message "
                            "and continue working in QGIS."
                        )
                        QMessageBox.information(self, "Complete Payment in Browser", msg)
                        self.emit_status(f"Order #{order_id} - payment window opened", "success")
                    else:
                        # No checkout URL returned - shouldn't happen
                        msg = (
                            f"Order #{order_id} submitted and approved!\n\n"
                            f"Total: {total_display}\n\n"
                            "Please log in to geodb.io to complete payment."
                        )
                        QMessageBox.information(self, "Order Submitted", msg)
                        self.emit_status(f"Order #{order_id} submitted", "success")

                except Exception as checkout_error:
                    # Checkout creation failed - order still exists
                    self.logger.warning(f"Checkout creation failed: {checkout_error}")
                    msg = (
                        f"Order #{order_id} submitted and approved!\n\n"
                        f"Total: {total_display}\n\n"
                        "Could not open payment window automatically.\n"
                        "Please log in to geodb.io to complete payment."
                    )
                    QMessageBox.warning(self, "Payment Window Error", msg)
                    self.emit_status(f"Order #{order_id} submitted (manual payment needed)", "warning")

            elif status == 'pending_approval':
                # Order requires manager approval before payment
                self.progress_bar.setValue(100)
                approvers = result.get('approvers', [])
                approvers_text = ', '.join(approvers[:3]) if approvers else 'company managers'
                if len(approvers) > 3:
                    approvers_text += f' (+{len(approvers) - 3} more)'

                msg = (
                    f"Order #{order_id} submitted for approval.\n\n"
                    f"Total: {total_display}\n"
                    f"Claims: {len(claims)}\n\n"
                    f"Approval required from: {approvers_text}\n\n"
                    "You will receive an email when approved. "
                    "You can then complete payment to process your claims."
                )
                QMessageBox.information(self, "Approval Required", msg)
                self.emit_status(f"Order #{order_id} pending approval", "info")

            else:
                # Unknown status
                self.progress_bar.setValue(100)
                msg = (
                    f"Order #{order_id} submitted.\n\n"
                    f"Status: {status}\n"
                    f"Total: {total_display}"
                )
                QMessageBox.information(self, "Order Submitted", msg)
                self.emit_status(f"Order #{order_id} submitted ({status})", "info")

        except Exception as e:
            self.logger.error(f"Order submission failed: {e}")
            QMessageBox.critical(self, "Order Error", str(e))
            self.emit_status(f"Order failed: {e}", "error")

        finally:
            self.progress_bar.hide()
            self.process_btn.setEnabled(True)

    def _display_results(self, result: Dict[str, Any]):
        """Display processing results in the table."""
        claims = result.get('claims', [])

        self.results_table.setRowCount(len(claims))

        for row, claim in enumerate(claims):
            name_item = QTableWidgetItem(claim.get('name', ''))
            self.results_table.setItem(row, 0, name_item)

            state_item = QTableWidgetItem(claim.get('state', ''))
            self.results_table.setItem(row, 1, state_item)

            plss = claim.get('plss', {})
            plss_desc = plss.get('description', '') if isinstance(plss, dict) else ''
            plss_item = QTableWidgetItem(plss_desc)
            self.results_table.setItem(row, 2, plss_item)

            corners = claim.get('corners', [])
            corners_item = QTableWidgetItem(str(len(corners)))
            corners_item.setTextAlignment(Qt.AlignCenter)
            self.results_table.setItem(row, 3, corners_item)

        self.results_group.show()

    def _create_result_layers(self):
        """Create QGIS layers from processed results."""
        # This is simplified - the full implementation would create
        # properly styled layers with all the processed data
        if not self.state.processed_waypoints:
            return

        try:
            # Create waypoints layer
            layer_crs = self.state.claims_layer.crs() if self.state.claims_layer else QgsCoordinateReferenceSystem('EPSG:4326')

            # Extract project name from claims layer name for consistent naming
            # e.g., "Lode Claims [G56E Lode Claims]" -> "G56E Lode Claims"
            project_name = None
            if self.state.claims_layer:
                layer_name = self.state.claims_layer.name()
                if '[' in layer_name and ']' in layer_name:
                    start = layer_name.index('[') + 1
                    end = layer_name.index(']')
                    project_name = layer_name[start:end]

            # Build display name with project suffix
            display_name = "Claims Waypoints"
            if project_name:
                display_name = f"Claims Waypoints [{project_name}]"

            # Define fields to match QClaims Waypoints layer format
            # This enables GPX export compatibility and proper field navigation
            fields = QgsFields()
            fields.append(QgsField("No", QVariant.Int))  # Sequential number (1, 2, 3...)
            fields.append(QgsField("Name", QVariant.String))  # "WP 1", "LM 3", "SL 2", etc.
            fields.append(QgsField("Latitude", QVariant.Double))  # WGS84 latitude
            fields.append(QgsField("Longitude", QVariant.Double))  # WGS84 longitude
            fields.append(QgsField("Altitude", QVariant.Double))  # Always 0
            fields.append(QgsField("Symbol", QVariant.String))  # GPX symbol: "City (Medium)", "Navaid, Green"
            fields.append(QgsField("Date", QVariant.String))  # Generation date
            fields.append(QgsField("Time", QVariant.String))  # "00:00:00"
            fields.append(QgsField("waypoint_type", QVariant.String))  # For styling: corner, discovery, etc.
            fields.append(QgsField("claim", QVariant.String))  # Associated claim name(s)

            # Use GeoPackage if configured, otherwise memory layer
            using_geopackage = bool(self.state.geopackage_path)
            if using_geopackage:
                from ...managers.claims_storage_manager import ClaimsStorageManager
                storage_manager = ClaimsStorageManager()
                waypoints_layer = storage_manager.create_or_update_layer(
                    table_name=ClaimsStorageManager.CLAIM_WAYPOINTS_TABLE,
                    layer_display_name=display_name,
                    geometry_type='Point',
                    fields=fields,
                    crs=layer_crs,
                    gpkg_path=self.state.geopackage_path
                )
            else:
                # Fallback to memory layer
                waypoints_layer = QgsVectorLayer(
                    f"Point?crs={layer_crs.authid()}",
                    display_name,
                    "memory"
                )
                waypoints_layer.dataProvider().addAttributes(fields.toList())
                waypoints_layer.updateFields()

            provider = waypoints_layer.dataProvider()

            # Get current date for waypoint records
            from datetime import datetime
            current_date = datetime.now()
            date_str = f"{current_date.month}/{current_date.day}/{current_date.year}"

            # Add features - populate all QClaims-compatible fields
            for idx, wp in enumerate(self.state.processed_waypoints, start=1):
                feature = QgsFeature()
                feature.setFields(waypoints_layer.fields())

                # Get coordinates - prefer UTM (easting/northing), fallback to lat/lon
                easting = wp.get('easting') or wp.get('longitude', 0)
                northing = wp.get('northing') or wp.get('latitude', 0)

                feature.setGeometry(QgsGeometry.fromPointXY(QgsPointXY(easting, northing)))

                # QClaims-compatible fields
                feature['No'] = idx  # Sequential number in sorted order
                feature['Name'] = wp.get('sequence_number', f"WP {idx}")  # "WP 1", "LM 3", etc.
                feature['Latitude'] = wp.get('lat', 0)  # WGS84 latitude
                feature['Longitude'] = wp.get('lon', 0)  # WGS84 longitude
                feature['Altitude'] = 0  # Always 0 for mining claims
                feature['Symbol'] = wp.get('symbol', 'City (Medium)')  # GPX symbol
                feature['Date'] = date_str
                feature['Time'] = '00:00:00'

                # Additional fields for QGIS styling and reference
                feature['waypoint_type'] = wp.get('type', 'corner')
                # Handle both single claim and multiple claims (shared corners)
                claims = wp.get('claims', [])
                if claims:
                    feature['claim'] = ', '.join(claims)
                else:
                    feature['claim'] = wp.get('claim', '')

                provider.addFeature(feature)

            # Apply categorized styling for waypoint types
            self._apply_waypoints_styling(waypoints_layer)

            # Only add to project if using memory layer (GeoPackage auto-adds)
            if not using_geopackage:
                QgsProject.instance().addMapLayer(waypoints_layer)

            storage_info = " (saved to GeoPackage)" if using_geopackage else ""
            self.emit_status(f"Created waypoints layer with {len(self.state.processed_waypoints)} points{storage_info}", "info")

        except Exception as e:
            self.emit_status(f"Warning: Could not create waypoints layer: {e}", "warning")

    def _apply_waypoints_styling(self, layer: QgsVectorLayer):
        """
        Apply categorized styling to Claims Waypoints layer.

        Styling matches QClaims GPS symbol conventions:
        - corner (WP): City (Medium) - black circles, 1.6mm
        - discovery (LM): Navaid, Green - lime green circles, 1.8mm
        - sideline (SL): Navaid, Blue - blue circles, 1.8mm (Wyoming)
        - endline (EL): Navaid, Blue - blue circles, 1.8mm (Arizona)
        - witness: Navaid, White - white circles with black outline, 1.8mm
        """
        try:
            categories = []

            # Corner waypoints (WP) - Black circles (City Medium style)
            corner_symbol = QgsMarkerSymbol.createSimple({
                'name': 'circle',
                'color': '#000000',  # Black
                'outline_color': '#000000',
                'outline_width': '0.3',
                'size': '1.6',
                'size_unit': 'MM'
            })
            categories.append(QgsRendererCategory('corner', corner_symbol, 'Corner'))

            # Discovery Monument (LM) - Lime green circles (Navaid, Green style)
            discovery_symbol = QgsMarkerSymbol.createSimple({
                'name': 'circle',
                'color': '#32CD32',  # Lime green
                'outline_color': '#228B22',  # Forest green outline
                'outline_width': '0.3',
                'size': '1.8',
                'size_unit': 'MM'
            })
            categories.append(QgsRendererCategory('discovery', discovery_symbol, 'Location Monument'))

            # Sideline Monument (SL) - Blue circles (Navaid, Blue style) - Wyoming
            sideline_symbol = QgsMarkerSymbol.createSimple({
                'name': 'circle',
                'color': '#3b82f6',  # Blue
                'outline_color': '#1d4ed8',  # Darker blue outline
                'outline_width': '0.3',
                'size': '1.8',
                'size_unit': 'MM'
            })
            categories.append(QgsRendererCategory('sideline', sideline_symbol, 'Sideline Monument'))

            # Endline Monument (EL) - Blue circles (Navaid, Blue style) - Arizona
            endline_symbol = QgsMarkerSymbol.createSimple({
                'name': 'circle',
                'color': '#3b82f6',  # Blue
                'outline_color': '#1d4ed8',  # Darker blue outline
                'outline_width': '0.3',
                'size': '1.8',
                'size_unit': 'MM'
            })
            categories.append(QgsRendererCategory('endline', endline_symbol, 'Endline Monument'))

            # Witness waypoints - White circles (Navaid, White style)
            witness_symbol = QgsMarkerSymbol.createSimple({
                'name': 'circle',
                'color': '#FFFFFF',  # White
                'outline_color': '#000000',  # Black outline for visibility
                'outline_width': '0.4',
                'size': '1.8',
                'size_unit': 'MM'
            })
            categories.append(QgsRendererCategory('witness', witness_symbol, 'Witness'))

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

            # Enable labeling with waypoint Name (WP 1, LM 3, etc.)
            self._apply_waypoints_labeling(layer)

            layer.triggerRepaint()

        except Exception as e:
            # Fall back to simple styling on error
            try:
                symbol = QgsMarkerSymbol.createSimple({
                    'name': 'circle',
                    'color': '#2563eb',
                    'size': '2'
                })
                from qgis.core import QgsSingleSymbolRenderer
                layer.setRenderer(QgsSingleSymbolRenderer(symbol))
                layer.triggerRepaint()
            except:
                pass

    def _apply_waypoints_labeling(self, layer: QgsVectorLayer):
        """
        Apply labeling to waypoints layer showing the Name field (WP 1, LM 3, etc.).

        This matches QClaims behavior where waypoint numbers are displayed on the map
        for easy field navigation reference.
        """
        try:
            from qgis.core import (
                QgsPalLayerSettings, QgsVectorLayerSimpleLabeling,
                QgsTextFormat, QgsTextBufferSettings
            )
            from qgis.PyQt.QtGui import QFont, QColor

            # Configure label settings
            label_settings = QgsPalLayerSettings()
            label_settings.fieldName = 'Name'
            label_settings.placement = QgsPalLayerSettings.OverPoint

            # Text format
            text_format = QgsTextFormat()
            font = QFont('Arial', 8)
            font.setBold(True)
            text_format.setFont(font)
            text_format.setSize(8)

            # White buffer/halo for readability
            buffer_settings = QgsTextBufferSettings()
            buffer_settings.setEnabled(True)
            buffer_settings.setSize(1)
            buffer_settings.setColor(QColor(255, 255, 255))
            text_format.setBuffer(buffer_settings)

            label_settings.setFormat(text_format)

            # Apply labeling
            labeling = QgsVectorLayerSimpleLabeling(label_settings)
            layer.setLabeling(labeling)
            layer.setLabelsEnabled(True)

        except Exception as e:
            # Labeling is optional - don't fail if it doesn't work
            pass

    # =========================================================================
    # Document Methods
    # =========================================================================

    def _generate_documents(self):
        """Generate location notice documents."""
        if not self.state.processed_claims:
            QMessageBox.warning(self, "No Claims", "Process claims first before generating documents.")
            return

        if not self.state.project_id:
            QMessageBox.warning(self, "No Project", "Please select a project first.")
            return

        self.progress_bar.show()
        self.progress_bar.setValue(0)
        self.generate_docs_btn.setEnabled(False)
        # Note: Removed QApplication.processEvents() to prevent heap corruption crashes

        try:
            self.progress_bar.setValue(30)

            # Build claimant_info from state for location notices
            claimant_info = {
                'claimant_name': self.state.claimant_name or '',
                'address_1': self.state.address_line1 or '',
                'address_2': self.state.address_line2 or '',
                'address_3': self.state.address_line3 or '',
                'district': self.state.mining_district or '',
                'monument_type': self.state.monument_type or '',
                'claim_type': 'lode',  # Default to lode claims
                # Agent info - left blank, filled in by person who stakes the claim
                'agent_name': '',
                'agent_address_1': '',
                'agent_address_2': '',
                'agent_address_3': '',
            }

            result = self.claims_manager.generate_documents(
                self.state.processed_claims,
                waypoints=self.state.processed_waypoints,  # Required for sorted corner certificates
                document_types=['location_notices', 'corner_certificates'],
                project_id=self.state.project_id,
                save_to_project=True,  # Save documents to project for permanent access
                claimant_info=claimant_info,
                claim_prefix=self.state.grid_name_prefix,  # Prefix filenames with claim name prefix
                order_id=self.state.fulfillment_order_id,  # Link to existing order/package if in fulfillment mode
                order_type=self.state.fulfillment_order_type,  # 'claim_purchase' or 'claim_order'
                reference_points=self.state.reference_points  # For bearing/distance tie-in
            )

            self.progress_bar.setValue(100)

            documents = result.get('documents', [])
            self.state.generated_documents = documents

            # Extract document IDs for linking later
            doc_ids = [d.get('document_id') for d in documents if d.get('document_id')]
            self.state.generated_document_ids = doc_ids

            # Store package info for download in Step 7
            self.state.package_info = result.get('package')

            # Extract and store claim_package_id for use when pushing claims
            # This ensures claims are linked to the same package as documents
            if self.state.package_info:
                self.state.claim_package_id = self.state.package_info.get('id')
                self.logger.info(
                    f"[QCLAIMS] Stored claim_package_id={self.state.claim_package_id} "
                    f"for linking during push"
                )

            if documents:
                package_info = self.state.package_info
                if package_info:
                    pkg_num = package_info.get('package_number', '')
                    self.docs_status_label.setText(
                        f"{len(documents)} document(s) generated - Package {pkg_num}"
                    )
                else:
                    self.docs_status_label.setText(f"{len(documents)} document(s) generated")
                self.docs_status_label.setStyleSheet(self._get_success_label_style())

                doc_list = "\n".join([f"- {d.get('filename', '?')}" for d in documents])
                saved_msg = (
                    "\n\nDocuments have been saved to your project. "
                    "You can download them in the Export step."
                )
                QMessageBox.information(
                    self,
                    "Documents Generated",
                    f"Generated {len(documents)} document(s):\n\n{doc_list}{saved_msg}"
                )
            else:
                self.docs_status_label.setText("No documents generated")

            self.emit_status(f"Generated {len(documents)} documents", "success")

        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

        finally:
            self.progress_bar.hide()
            self.generate_docs_btn.setEnabled(True)

    # =========================================================================
    # ClaimsStepBase Implementation
    # =========================================================================

    def validate(self) -> List[str]:
        """Validate the step."""
        errors = []

        if not self.state.claims_layer:
            errors.append("Claims layer is required")

        if not self.state.tos_accepted:
            errors.append("Terms of Service must be accepted")

        # Processing is not required to proceed, but recommended
        if not self.state.processed_claims:
            # This is a soft warning, not an error
            pass

        return errors

    def on_enter(self):
        """Called when step becomes active."""
        self.load_state()

        # Update pricing label if pay-per-claim
        if self.state.access_info:
            access_type = self.state.access_info.get('access_type', '')
            if access_type == 'pay_per_claim':
                pricing = self.state.access_info.get('pricing', {})
                price_cents = pricing.get('self_service_per_claim_cents', 0)
                price_dollars = price_cents / 100
                if price_dollars > 0:
                    self.pricing_label.setText(f"${price_dollars:.2f}/claim")

    def on_leave(self):
        """Called when leaving step."""
        self.save_state()

    def save_state(self):
        """Save widget state to shared state."""
        # Most state is already saved during processing
        pass

    def load_state(self):
        """Load widget state from shared state."""
        # Update UI based on state
        has_processed = len(self.state.processed_claims) > 0

        self.generate_docs_btn.setEnabled(has_processed)

        if has_processed:
            self._display_results({'claims': self.state.processed_claims})
