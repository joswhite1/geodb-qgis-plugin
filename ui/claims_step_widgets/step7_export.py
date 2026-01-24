# -*- coding: utf-8 -*-
"""
Step 7: Waypoints Export

Handles:
- Add witness waypoints if necessary
- Update waypoint table
- Push to server
"""
from typing import List

from qgis.PyQt.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QFrame, QScrollArea, QMessageBox, QProgressBar,
    QApplication
)
from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtGui import QDesktopServices

from .step_base import ClaimsStepBase


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

        self.add_witness_btn = QPushButton("Add Witness Waypoint")
        self.add_witness_btn.setToolTip("Add a custom witness or reference waypoint")
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
        """Refresh the waypoints table from state."""
        waypoints = self.state.processed_waypoints

        self.waypoints_table.setRowCount(len(waypoints))

        for row, wp in enumerate(waypoints):
            # Name
            name_item = QTableWidgetItem(wp.get('name', ''))
            self.waypoints_table.setItem(row, 0, name_item)

            # Type
            type_item = QTableWidgetItem(wp.get('type', ''))
            self.waypoints_table.setItem(row, 1, type_item)

            # Claim
            claim_item = QTableWidgetItem(wp.get('claim_name', ''))
            self.waypoints_table.setItem(row, 2, claim_item)

            # Latitude
            lat = wp.get('latitude', wp.get('northing', 0))
            lat_item = QTableWidgetItem(f"{lat:.6f}" if lat else "")
            self.waypoints_table.setItem(row, 3, lat_item)

            # Longitude
            lon = wp.get('longitude', wp.get('easting', 0))
            lon_item = QTableWidgetItem(f"{lon:.6f}" if lon else "")
            self.waypoints_table.setItem(row, 4, lon_item)

        self.waypoint_count_label.setText(f"{len(waypoints)} waypoint(s)")

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
                f"{len(self.state.processed_waypoints)} waypoints to project {self.state.project_id}"
            )
            if self.state.processed_waypoints:
                first_wp = self.state.processed_waypoints[0]
                logger.info(f"[PUSH] First waypoint keys: {list(first_wp.keys())}")
                logger.info(f"[PUSH] First waypoint: {first_wp}")

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

            self.push_status_label.setText(
                f"Pushed: {lh_summary.get('created', 0)} LandHoldings, "
                f"{st_summary.get('created', 0)} ClaimStakes"
                + (f", {docs_linked} docs linked" if docs_linked else "")
            )
            self.push_status_label.setStyleSheet(self._get_success_label_style())

            doc_msg = f"\nDocuments: {docs_linked} linked to claims" if docs_linked else ""
            QMessageBox.information(
                self,
                "Push Complete",
                f"LandHoldings: {lh_summary.get('created', 0)} created\n"
                f"ClaimStakes: {st_summary.get('created', 0)} created{doc_msg}\n\n"
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
