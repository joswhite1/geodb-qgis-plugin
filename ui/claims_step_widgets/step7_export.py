# -*- coding: utf-8 -*-
"""
Step 7: Waypoints Export

Handles:
- Add witness waypoints if necessary
- Update waypoint table
- Push to server
"""
from typing import List, Dict, Any

from qgis.PyQt.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QFrame, QScrollArea, QMessageBox, QProgressBar,
    QApplication
)
from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtGui import QDesktopServices
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsCoordinateReferenceSystem,
    QgsCoordinateTransform
)

from .step_base import ClaimsStepBase
from ...utils.logger import PluginLogger


class ClaimsStep7Widget(ClaimsStepBase):
    """
    Step 7: Waypoints Export

    Export waypoints and push claims to server.
    """

    def get_step_title(self) -> str:
        return "Waypoints & Export"

    def get_step_description(self) -> str:
        return (
            "Review your waypoints, add witness points if needed, then push your claims "
            "to the geodb.io server for mobile access and tracking. GPX files are included "
            "in the downloadable Claim Package."
        )

    def __init__(self, state, claims_manager, parent=None):
        super().__init__(state, claims_manager, parent)
        self.logger = PluginLogger.get_logger()
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

        # Waypoints Table Group
        layout.addWidget(self._create_waypoints_group())

        # Documents Download Group
        layout.addWidget(self._create_documents_group())

        # Push to Server Group
        layout.addWidget(self._create_push_group())

        layout.addStretch()

        scroll.setWidget(scroll_content)
        main_layout.addWidget(scroll)

    def _create_waypoints_group(self) -> QGroupBox:
        """Create the waypoints table group."""
        group = QGroupBox("Waypoints")
        group.setStyleSheet(self._get_group_style())
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        # Info
        info_label = QLabel(
            "Review the waypoints generated for your claims. These include corner posts, "
            "discovery monuments, and any state-required monuments."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet(self._get_info_label_style())
        layout.addWidget(info_label)

        # Table
        self.waypoints_table = QTableWidget()
        self.waypoints_table.setColumnCount(5)
        self.waypoints_table.setHorizontalHeaderLabels(["Name", "Type", "Claim", "Lat", "Lon"])
        self.waypoints_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.waypoints_table.verticalHeader().setVisible(False)
        self.waypoints_table.setMaximumHeight(250)
        self.waypoints_table.setStyleSheet("""
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
        layout.addWidget(self.waypoints_table)

        # Buttons
        btn_layout = QHBoxLayout()

        self.add_witness_btn = QPushButton("Instructions")
        self.add_witness_btn.setToolTip("How to add a witness waypoint")
        self.add_witness_btn.setStyleSheet(self._get_secondary_button_style())
        self.add_witness_btn.clicked.connect(self._add_witness_waypoint)
        btn_layout.addWidget(self.add_witness_btn)

        self.refresh_table_btn = QPushButton("Refresh Table")
        self.refresh_table_btn.setStyleSheet(self._get_secondary_button_style())
        self.refresh_table_btn.clicked.connect(self._refresh_waypoints_table)
        btn_layout.addWidget(self.refresh_table_btn)

        btn_layout.addStretch()

        layout.addLayout(btn_layout)

        # Count label
        self.waypoint_count_label = QLabel("")
        self.waypoint_count_label.setStyleSheet(self._get_info_label_style())
        layout.addWidget(self.waypoint_count_label)

        return group

    def _create_documents_group(self) -> QGroupBox:
        """Create the documents download group."""
        group = QGroupBox("Claim Documents")
        group.setStyleSheet(self._get_group_style())
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        # Info
        info_label = QLabel(
            "Download your generated claim documents (location notices, corner certificates) "
            "from the server. Documents are bundled into a Claim Package for easy access."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet(self._get_info_label_style())
        layout.addWidget(info_label)

        # Button
        btn_layout = QHBoxLayout()

        self.download_docs_btn = QPushButton("Download Documents")
        self.download_docs_btn.setToolTip("Open the Claim Package page to download documents")
        self.download_docs_btn.setStyleSheet(self._get_success_button_style())
        self.download_docs_btn.clicked.connect(self._download_documents)
        self.download_docs_btn.setEnabled(False)
        btn_layout.addWidget(self.download_docs_btn)

        btn_layout.addStretch()

        layout.addLayout(btn_layout)

        # Status
        self.docs_status_label = QLabel("")
        self.docs_status_label.setStyleSheet(self._get_info_label_style())
        layout.addWidget(self.docs_status_label)

        return group

    def _create_push_group(self) -> QGroupBox:
        """Create the push to server group."""
        group = QGroupBox("Push to geodb.io Server")
        group.setStyleSheet(self._get_group_style())
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        # Info
        info_label = QLabel(
            "Push processed claims as LandHoldings and waypoints as ClaimStakes "
            "to the geodb.io server. This enables mobile access for field staking "
            "and tracks claim status in your project."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet(self._get_info_label_style())
        layout.addWidget(info_label)

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
                background-color: #059669;
                border-radius: 3px;
            }
        """)
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)

        # Button
        btn_layout = QHBoxLayout()

        self.push_btn = QPushButton("Push to Server")
        self.push_btn.setStyleSheet(self._get_primary_button_style())
        self.push_btn.clicked.connect(self._push_to_server)
        btn_layout.addWidget(self.push_btn)

        btn_layout.addStretch()

        layout.addLayout(btn_layout)

        # Push status
        self.push_status_label = QLabel("")
        self.push_status_label.setStyleSheet(self._get_info_label_style())
        layout.addWidget(self.push_status_label)

        return group

    # =========================================================================
    # Waypoints Methods
    # =========================================================================

    def _refresh_waypoints_table(self):
        """Refresh the waypoints table from QGIS layer and state.

        This method auto-populates attributes for any manually-added waypoints
        (e.g. witness points), then syncs from the QGIS layer into state and
        updates the table widget.
        """
        # Auto-populate fields for manually-added waypoints (QClaims pattern)
        self._auto_populate_layer_attributes()

        # Sync waypoints from QGIS layer to state (picks up user-added WIT points)
        self._sync_waypoints_from_layer()

        waypoints = self.state.processed_waypoints

        self.waypoints_table.setRowCount(len(waypoints))

        for row, wp in enumerate(waypoints):
            # Name (sequence_number field, e.g., "WP 1", "LM 3", "WIT")
            name_item = QTableWidgetItem(wp.get('sequence_number', wp.get('name', '')))
            self.waypoints_table.setItem(row, 0, name_item)

            # Type
            type_item = QTableWidgetItem(wp.get('type', ''))
            self.waypoints_table.setItem(row, 1, type_item)

            # Claim
            claim_item = QTableWidgetItem(wp.get('claim_name', wp.get('claim', '')))
            self.waypoints_table.setItem(row, 2, claim_item)

            # Latitude - use lat (WGS84) first, fallback to northing (UTM)
            lat = wp.get('lat', wp.get('latitude', wp.get('northing', 0)))
            lat_item = QTableWidgetItem(f"{lat:.6f}" if lat else "")
            self.waypoints_table.setItem(row, 3, lat_item)

            # Longitude - use lon (WGS84) first, fallback to easting (UTM)
            lon = wp.get('lon', wp.get('longitude', wp.get('easting', 0)))
            lon_item = QTableWidgetItem(f"{lon:.6f}" if lon else "")
            self.waypoints_table.setItem(row, 4, lon_item)

        self.waypoint_count_label.setText(f"{len(waypoints)} waypoint(s)")

    def _auto_populate_layer_attributes(self):
        """Auto-populate attributes for manually-added waypoints in the layer.

        Detects features with NULL/missing Latitude (manually-added rows) and
        fills in Latitude, Longitude, Altitude, Symbol, Date, Time, No, and
        waypoint_type from the feature geometry and reference values.

        This mirrors the QClaims update_waypoints_table() pattern: the user
        adds a point and a Name, then this populates everything else.
        """
        waypoints_layer = self._get_waypoints_layer()
        if not waypoints_layer or not waypoints_layer.isValid():
            return

        # Get source CRS for coordinate transforms
        source_crs = waypoints_layer.crs()
        wgs84 = QgsCoordinateReferenceSystem('EPSG:4326')
        transform = None
        if source_crs and source_crs != wgs84:
            transform = QgsCoordinateTransform(source_crs, wgs84, QgsProject.instance())

        field_names = waypoints_layer.fields().names()

        # Find features that need auto-population (NULL Latitude = manually added)
        features_to_update = []
        reference_feature = None
        total_features = 0

        for feature in waypoints_layer.getFeatures():
            total_features += 1
            lat_val = feature['Latitude'] if 'Latitude' in field_names else None

            # Check if Latitude is NULL or missing (manually-added row)
            is_null = (lat_val is None
                       or (hasattr(lat_val, 'isNull') and lat_val.isNull())
                       or lat_val == 0)

            if is_null:
                # Validate that Name is filled in
                name_val = feature['Name'] if 'Name' in field_names else None
                has_name = (name_val is not None
                            and not (hasattr(name_val, 'isNull') and name_val.isNull())
                            and str(name_val).strip() != '')
                if has_name and not feature.geometry().isEmpty():
                    features_to_update.append(feature)
            elif reference_feature is None:
                # Use first complete feature as reference for Date/Time/Altitude
                reference_feature = feature

        if not features_to_update:
            return

        # Get reference values from existing complete feature
        if reference_feature:
            def _val(val, default):
                if val is None or (hasattr(val, 'isNull') and val.isNull()):
                    return default
                return val

            ref_date = str(_val(reference_feature['Date'], '')) if 'Date' in field_names else ''
            ref_time = str(_val(reference_feature['Time'], '00:00:00')) if 'Time' in field_names else '00:00:00'
            ref_altitude = _val(reference_feature['Altitude'], 0) if 'Altitude' in field_names else 0
        else:
            from datetime import datetime
            now = datetime.now()
            ref_date = f"{now.month}/{now.day}/{now.year}"
            ref_time = '00:00:00'
            ref_altitude = 0

        # Update features in the layer
        was_editing = waypoints_layer.isEditable()
        if not was_editing:
            waypoints_layer.startEditing()

        updated_count = 0
        for feature in features_to_update:
            fid = feature.id()
            point = feature.geometry().asPoint()

            # Convert to WGS84
            if transform:
                wgs84_point = transform.transform(point)
                lat = wgs84_point.y()
                lon = wgs84_point.x()
            else:
                lat = point.y()
                lon = point.x()

            # Build attribute updates
            attr_map = {}
            for field_name, value in [
                ('Latitude', round(lat, 7)),
                ('Longitude', round(lon, 7)),
                ('Altitude', ref_altitude),
                ('Symbol', 'Navaid, White'),
                ('Date', ref_date),
                ('Time', ref_time),
                ('waypoint_type', 'witness'),
                ('No', total_features - len(features_to_update) + updated_count + 1),
            ]:
                idx = waypoints_layer.fields().indexOf(field_name)
                if idx >= 0:
                    attr_map[idx] = value

            waypoints_layer.changeAttributeValues(fid, attr_map)
            updated_count += 1

        if not was_editing:
            waypoints_layer.commitChanges()
        else:
            waypoints_layer.triggerRepaint()

        if updated_count > 0:
            self.logger.info(
                f"[CLAIMS] Auto-populated {updated_count} manually-added waypoint(s)"
            )
            self.emit_status(
                f"Updated {updated_count} waypoint(s) with coordinates and metadata",
                "success"
            )

    def _sync_waypoints_from_layer(self):
        """Sync waypoints from QGIS layer back to state.

        Reads waypoints from the Claims Waypoints layer, including any
        user-added witness points (WIT). Updates state.processed_waypoints
        with the current layer contents.
        """
        # Find the waypoints layer by ID or by name pattern
        waypoints_layer = self._get_waypoints_layer()
        if not waypoints_layer or not waypoints_layer.isValid():
            self.logger.debug("[CLAIMS] No valid waypoints layer found for sync")
            return

        # Get source CRS for coordinate transforms
        source_crs = waypoints_layer.crs()
        wgs84 = QgsCoordinateReferenceSystem('EPSG:4326')
        transform = None
        if source_crs and source_crs != wgs84:
            transform = QgsCoordinateTransform(source_crs, wgs84, QgsProject.instance())

        # Read all features from layer
        new_waypoints: List[Dict[str, Any]] = []
        for feature in waypoints_layer.getFeatures():
            geom = feature.geometry()
            if geom.isEmpty():
                continue

            point = geom.asPoint()
            easting = point.x()
            northing = point.y()

            # Transform to WGS84 for lat/lon
            if transform:
                wgs84_point = transform.transform(point)
                lat = wgs84_point.y()
                lon = wgs84_point.x()
            else:
                lat = northing
                lon = easting

            # Read attributes from layer
            # NOTE: GeoPackage layers may return QVariant (including NULL QVariant)
            # for attribute values. Convert to plain Python types to avoid
            # QVariant errors downstream (e.g., QTableWidgetItem constructor).
            def _str(val, default=''):
                """Convert a QVariant or any value to a plain Python string."""
                if val is None or (hasattr(val, 'isNull') and val.isNull()):
                    return default
                s = str(val)
                return s if s else default

            name = _str(feature['Name']) if 'Name' in feature.fields().names() else ''
            wp_type = _str(feature['waypoint_type'], 'witness') if 'waypoint_type' in feature.fields().names() else 'witness'
            claim = _str(feature['claim']) if 'claim' in feature.fields().names() else ''
            symbol = _str(feature['Symbol'], 'City (Medium)') if 'Symbol' in feature.fields().names() else 'City (Medium)'

            wp_data = {
                'sequence_number': name,
                'name': name,  # Fallback field
                'type': wp_type,
                'claim': claim,
                'claim_name': claim,
                'easting': round(easting, 8),
                'northing': round(northing, 8),
                'lat': round(lat, 7),
                'lon': round(lon, 7),
                'latitude': round(lat, 7),  # Fallback field
                'longitude': round(lon, 7),  # Fallback field
                'symbol': symbol,
            }
            new_waypoints.append(wp_data)

        if new_waypoints:
            self.state.processed_waypoints = new_waypoints
            self.logger.info(f"[CLAIMS] Synced {len(new_waypoints)} waypoints from layer")

    def _get_waypoints_layer(self) -> QgsVectorLayer:
        """Find the Claims Waypoints layer.

        Returns:
            The waypoints layer, or None if not found.
        """
        # First try by stored layer ID
        if self.state.waypoints_layer_id:
            layer = QgsProject.instance().mapLayer(self.state.waypoints_layer_id)
            if layer and layer.isValid():
                return layer

        # Fallback: search by name pattern
        for layer in QgsProject.instance().mapLayers().values():
            if isinstance(layer, QgsVectorLayer):
                if layer.name().startswith("Claims Waypoints"):
                    return layer

        return None

    def _add_witness_waypoint(self):
        """Add a custom witness waypoint."""
        # For now, show info message
        # Full implementation would open a dialog to add custom waypoints
        QMessageBox.information(
            self,
            "Add Witness Waypoint",
            "To add a witness waypoint:\n\n"
            "1. Click on the 'Claims Waypoints' layer in QGIS\n"
            "2. Enable editing mode\n"
            "3. Use the Add Point tool to add a waypoint\n"
            "4. Enter the waypoint name in the attribute table\n"
            "5. Click 'Refresh Table' to update this list\n\n"
            "Witness waypoints help locate claim monuments from recognizable features."
        )

    # =========================================================================
    # Export Methods
    # =========================================================================

    def _download_documents(self):
        """Open browser to Claim Packages page for document download."""
        url = "https://geodb.io/geodata/claim-packages/"
        QDesktopServices.openUrl(QUrl(url))
        self.docs_status_label.setText("Opened Claim Packages in browser")
        self.docs_status_label.setStyleSheet(self._get_success_label_style())
        self.emit_status("Opened Claim Packages page in browser", "success")

    # =========================================================================
    # Push Methods
    # =========================================================================

    def _push_to_server(self):
        """Push claims and waypoints to server."""
        if not self.state.processed_claims:
            QMessageBox.warning(self, "No Claims", "Process claims first before pushing to server.")
            return

        if not self.state.project_id:
            QMessageBox.warning(self, "No Project", "Please select a project first.")
            return

        # Confirm
        reply = QMessageBox.question(
            self,
            "Push to Server",
            f"Push {len(self.state.processed_claims)} claims as LandHoldings and "
            f"{len(self.state.processed_waypoints)} waypoints as ClaimStakes to the server?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply != QMessageBox.Yes:
            return

        self.progress_bar.show()
        self.progress_bar.setValue(0)
        self.push_btn.setEnabled(False)
        # Note: Removed QApplication.processEvents() to prevent heap corruption crashes

        try:
            self.progress_bar.setValue(30)

            # Debug: Log what we're pushing
            from ...utils.logger import PluginLogger
            logger = PluginLogger.get_logger()
            logger.info(
                f"[PUSH] Pushing {len(self.state.processed_claims)} claims, "
                f"{len(self.state.processed_waypoints)} waypoints to project {self.state.project_id}, "
                f"claim_package_id={self.state.claim_package_id}"
            )
            if self.state.processed_waypoints:
                first_wp = self.state.processed_waypoints[0]
                logger.info(f"[PUSH] First waypoint keys: {list(first_wp.keys())}")
                logger.info(f"[PUSH] First waypoint: {first_wp}")

            if not self.state.claim_package_id:
                logger.warning(
                    "[PUSH] claim_package_id is None! This will cause a duplicate "
                    "ClaimPackage on the server. Was 'Generate Documents' run in Step 6?"
                )

            result = self.claims_manager.push_to_server(
                self.state.processed_claims,
                self.state.processed_waypoints,
                self.state.project_id,
                self.state.project_epsg,  # Pass EPSG for UTM coordinate preservation
                self.state.claim_package_id  # Link claims to existing package from document generation
            )

            self.progress_bar.setValue(70)

            # Show result
            lh_summary = result.get('landholdings', {}).get('summary', {})
            st_summary = result.get('stakes', {}).get('summary', {})

            # Link documents to landholdings if we have generated document IDs
            doc_ids = getattr(self.state, 'generated_document_ids', [])
            docs_linked = 0
            if doc_ids and lh_summary.get('created', 0) > 0:
                try:
                    # Get claim names for linking
                    claim_names = [c.get('name') for c in self.state.processed_claims if c.get('name')]
                    if claim_names:
                        link_result = self.claims_manager.link_documents_to_landholdings(
                            document_ids=doc_ids,
                            landholding_names=claim_names,
                            project_id=self.state.project_id
                        )
                        docs_linked = link_result.get('documents_linked', 0)
                except Exception as link_err:
                    # Don't fail the whole push if document linking fails
                    self.emit_status(f"Warning: Could not link documents: {link_err}", "warning")

            self.progress_bar.setValue(100)

            # Build summary strings showing both created and updated counts
            lh_created = lh_summary.get('created', 0)
            lh_updated = lh_summary.get('updated', 0)
            st_created = st_summary.get('created', 0)
            st_updated = st_summary.get('updated', 0)
            st_orphans = result.get('stakes', {}).get('orphan_stakes_deleted', 0)

            lh_str = f"{lh_created} created" + (f", {lh_updated} updated" if lh_updated else "")
            st_str = f"{st_created} created" + (f", {st_updated} updated" if st_updated else "")
            if st_orphans:
                st_str += f", {st_orphans} orphans removed"

            self.push_status_label.setText(
                f"Pushed: {lh_str} LandHoldings, {st_str} ClaimStakes"
                + (f", {docs_linked} docs linked" if docs_linked else "")
            )
            self.push_status_label.setStyleSheet(self._get_success_label_style())

            doc_msg = f"\nDocuments: {docs_linked} linked to claims" if docs_linked else ""
            orphan_msg = f"\n{st_orphans} orphan stakes cleaned up" if st_orphans else ""
            QMessageBox.information(
                self,
                "Push Complete",
                f"LandHoldings: {lh_str}\n"
                f"ClaimStakes: {st_str}{orphan_msg}{doc_msg}\n\n"
                "Your claims are now available on geodb.io and the mobile app."
            )

            self.emit_status("Claims pushed to server successfully", "success")

        except Exception as e:
            self.push_status_label.setText(f"Push failed: {e}")
            self.push_status_label.setStyleSheet(self._get_error_label_style())
            QMessageBox.critical(self, "Error", str(e))

        finally:
            self.progress_bar.hide()
            self.push_btn.setEnabled(True)

    # =========================================================================
    # ClaimsStepBase Implementation
    # =========================================================================

    def validate(self) -> List[str]:
        """Validate the step."""
        errors = []

        # Export is the final step - processing should have been done
        if not self.state.processed_claims:
            errors.append("Claims must be processed before export")

        return errors

    def on_enter(self):
        """Called when step becomes active."""
        self.load_state()
        self._refresh_waypoints_table()

    def on_leave(self):
        """Called when leaving step."""
        self.save_state()

    def save_state(self):
        """Save widget state to shared state."""
        # Most state is already stored during operations
        pass

    def load_state(self):
        """Load widget state from shared state."""
        # Update button states
        has_processed = len(self.state.processed_claims) > 0
        has_documents = len(self.state.generated_documents) > 0 or self.state.package_info is not None

        self.download_docs_btn.setEnabled(has_documents)
        self.push_btn.setEnabled(has_processed)

        # Update documents status label
        if self.state.package_info:
            pkg_num = self.state.package_info.get('package_number', '')
            doc_count = len(self.state.generated_documents)
            self.docs_status_label.setText(f"{doc_count} document(s) in Package {pkg_num}")
        elif has_documents:
            doc_count = len(self.state.generated_documents)
            self.docs_status_label.setText(f"{doc_count} document(s) available")
