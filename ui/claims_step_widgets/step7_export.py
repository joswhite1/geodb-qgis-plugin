# -*- coding: utf-8 -*-
"""
Step 7: Waypoints Export

Handles:
- Add witness waypoints if necessary
- Update waypoint table
- Push to server
"""
from pathlib import Path
from typing import List, Dict, Any

from qgis.PyQt.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QFrame, QScrollArea, QMessageBox, QProgressBar,
    QFileDialog, QComboBox, QLineEdit, QTextEdit
)
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsCoordinateReferenceSystem,
    QgsCoordinateTransform
)

from .step_base import ClaimsStepBase
from ...utils.logger import PluginLogger
from ...utils.layer_utils import is_layer_valid
from ...utils.compat import QFrame_NoFrame, QHeaderView_Stretch, QHeaderView_ResizeToContents


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

    def __init__(self, state, claims_manager, parent=None, data_manager=None):
        super().__init__(state, claims_manager, parent, data_manager=data_manager)
        self.logger = PluginLogger.get_logger()
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

        # Waypoints Table Group
        layout.addWidget(self._create_waypoints_group())

        # Push & Upload Group (claims/stakes + GeoPackage)
        layout.addWidget(self._create_push_group())

        # Generate Maps Group (independent, can be done separately)
        layout.addWidget(self._create_maps_group())

        # Upload Documents to Package Group
        layout.addWidget(self._create_upload_documents_group())

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
        self.waypoints_table.horizontalHeader().setSectionResizeMode(QHeaderView_Stretch)
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

        self.auto_witness_btn = QPushButton("Auto-generate Witness Points")
        self.auto_witness_btn.setToolTip(
            "Automatically generate witness waypoints for stakes on private land"
        )
        self.auto_witness_btn.setStyleSheet(self._get_secondary_button_style())
        self.auto_witness_btn.clicked.connect(self._auto_generate_witnesses)
        btn_layout.addWidget(self.auto_witness_btn)

        self.add_witness_btn = QPushButton("Manual Instructions")
        self.add_witness_btn.setToolTip("How to add a witness waypoint manually")
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

    def _create_push_group(self) -> QGroupBox:
        """Create the push & upload to server group."""
        group = QGroupBox("Push & Upload to Server")
        group.setStyleSheet(self._get_group_style())
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        # Info
        info_label = QLabel(
            "Push processed claims as LandHoldings and waypoints as ClaimStakes "
            "to the geodb.io server, then upload the claims GeoPackage. "
            "This enables mobile access for field staking "
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

        self.push_btn = QPushButton("Push && Upload to Server")
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

    def _create_maps_group(self) -> QGroupBox:
        """Create the Generate Maps group."""
        group = QGroupBox("Generate Maps")
        group.setStyleSheet(self._get_group_style())
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        info_label = QLabel(
            "Generate print-ready map layouts for your claims. Creates QGIS "
            "print layouts with proper labels, scale, and layer visibility "
            "for field use and county/state filing."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet(self._get_info_label_style())
        layout.addWidget(info_label)

        # Progress bar (hidden until generating)
        self.maps_progress = QProgressBar()
        self.maps_progress.setStyleSheet("""
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
        self.maps_progress.hide()
        layout.addWidget(self.maps_progress)

        # Button
        btn_layout = QHBoxLayout()

        self.generate_maps_btn = QPushButton("Generate Maps")
        self.generate_maps_btn.setToolTip(
            "Create Field Map, Filing Map, and state-specific maps "
            "as QGIS print layouts"
        )
        self.generate_maps_btn.setStyleSheet(self._get_success_button_style())
        self.generate_maps_btn.clicked.connect(self._generate_maps)
        btn_layout.addWidget(self.generate_maps_btn)

        btn_layout.addStretch()

        layout.addLayout(btn_layout)

        # Status label
        self.maps_status_label = QLabel("")
        self.maps_status_label.setStyleSheet(self._get_info_label_style())
        layout.addWidget(self.maps_status_label)

        return group

    # =========================================================================
    # Upload Documents to Package
    # =========================================================================

    # Document type choices matching server ClaimPackageDocument.DOCUMENT_TYPE_CHOICES
    DOCUMENT_TYPES = [
        ('field_map', 'Map'),
        ('location_notice', 'Location Notice'),
        ('blm_filing', 'BLM Filing Receipt'),
        ('county_recording', 'County Recording'),
        ('survey', 'Survey/Plat'),
        ('noith', 'Notice of Intent to Hold'),
        ('qclaims_export', 'QClaims Export'),
        ('work_package', 'Work Package'),
        ('other', 'Other'),
    ]

    def _create_upload_documents_group(self) -> QGroupBox:
        """Create the Upload Documents to Package group."""
        group = QGroupBox("Upload Documents to Package")
        group.setStyleSheet(self._get_group_style())
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        info_label = QLabel(
            "Upload maps, filing receipts, county recordings, and other documents "
            "to the claim package on geodb.io. Files are linked to all claims in "
            "the package."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet(self._get_info_label_style())
        layout.addWidget(info_label)

        # --- File picker row ---
        file_row = QHBoxLayout()
        file_row.setSpacing(8)

        self.upload_file_path = QLineEdit()
        self.upload_file_path.setPlaceholderText("Select a file to upload...")
        self.upload_file_path.setReadOnly(True)
        self.upload_file_path.setStyleSheet("""
            QLineEdit {
                border: 1px solid #d1d5db;
                border-radius: 4px;
                padding: 6px 10px;
                background-color: #f9fafb;
            }
        """)
        file_row.addWidget(self.upload_file_path, stretch=1)

        browse_btn = QPushButton("Browse...")
        browse_btn.setStyleSheet("""
            QPushButton {
                background-color: #f3f4f6;
                border: 1px solid #d1d5db;
                border-radius: 4px;
                padding: 6px 16px;
                font-weight: 500;
            }
            QPushButton:hover { background-color: #e5e7eb; }
        """)
        browse_btn.clicked.connect(self._browse_upload_file)
        file_row.addWidget(browse_btn)

        layout.addLayout(file_row)

        # --- Type + Title row ---
        fields_row = QHBoxLayout()
        fields_row.setSpacing(8)

        # Document type combo
        type_layout = QVBoxLayout()
        type_label = QLabel("Document Type")
        type_label.setStyleSheet("font-size: 11px; color: #6b7280; margin-bottom: 2px;")
        type_layout.addWidget(type_label)

        self.upload_doc_type = QComboBox()
        for code, display in self.DOCUMENT_TYPES:
            self.upload_doc_type.addItem(display, code)
        self.upload_doc_type.setStyleSheet("""
            QComboBox {
                border: 1px solid #d1d5db;
                border-radius: 4px;
                padding: 6px 10px;
                min-width: 160px;
            }
        """)
        type_layout.addWidget(self.upload_doc_type)
        fields_row.addLayout(type_layout)

        # Title
        title_layout = QVBoxLayout()
        title_label = QLabel("Title (optional)")
        title_label.setStyleSheet("font-size: 11px; color: #6b7280; margin-bottom: 2px;")
        title_layout.addWidget(title_label)

        self.upload_title = QLineEdit()
        self.upload_title.setPlaceholderText("Auto-filled from filename")
        self.upload_title.setStyleSheet("""
            QLineEdit {
                border: 1px solid #d1d5db;
                border-radius: 4px;
                padding: 6px 10px;
            }
        """)
        title_layout.addWidget(self.upload_title)
        fields_row.addLayout(title_layout, stretch=1)

        layout.addLayout(fields_row)

        # --- Description row ---
        desc_label = QLabel("Description (optional)")
        desc_label.setStyleSheet("font-size: 11px; color: #6b7280; margin-bottom: 2px;")
        layout.addWidget(desc_label)

        self.upload_description = QTextEdit()
        self.upload_description.setPlaceholderText("Optional description...")
        self.upload_description.setMaximumHeight(60)
        self.upload_description.setStyleSheet("""
            QTextEdit {
                border: 1px solid #d1d5db;
                border-radius: 4px;
                padding: 4px 8px;
            }
        """)
        layout.addWidget(self.upload_description)

        # --- Upload button + progress ---
        self.upload_progress = QProgressBar()
        self.upload_progress.setStyleSheet("""
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
        self.upload_progress.hide()
        layout.addWidget(self.upload_progress)

        upload_btn_row = QHBoxLayout()

        self.upload_btn = QPushButton("Upload to Package")
        self.upload_btn.setStyleSheet(self._get_primary_button_style())
        self.upload_btn.clicked.connect(self._upload_document)
        upload_btn_row.addWidget(self.upload_btn)

        self.refresh_docs_btn = QPushButton("Refresh List")
        self.refresh_docs_btn.setStyleSheet("""
            QPushButton {
                background-color: #f3f4f6;
                border: 1px solid #d1d5db;
                border-radius: 4px;
                padding: 8px 16px;
                font-weight: 500;
            }
            QPushButton:hover { background-color: #e5e7eb; }
        """)
        self.refresh_docs_btn.clicked.connect(self._refresh_documents_list)
        upload_btn_row.addWidget(self.refresh_docs_btn)

        upload_btn_row.addStretch()
        layout.addLayout(upload_btn_row)

        # --- Status label ---
        self.upload_status_label = QLabel("")
        self.upload_status_label.setStyleSheet(self._get_info_label_style())
        layout.addWidget(self.upload_status_label)

        # --- Uploaded documents table ---
        self.docs_table = QTableWidget()
        self.docs_table.setColumnCount(3)
        self.docs_table.setHorizontalHeaderLabels(["Title", "Type", "Size"])
        self.docs_table.horizontalHeader().setSectionResizeMode(0, QHeaderView_Stretch)
        self.docs_table.horizontalHeader().setSectionResizeMode(1, QHeaderView_ResizeToContents)
        self.docs_table.horizontalHeader().setSectionResizeMode(2, QHeaderView_ResizeToContents)
        self.docs_table.verticalHeader().setVisible(False)
        self.docs_table.setMaximumHeight(150)
        self.docs_table.setStyleSheet("""
            QTableWidget {
                border: 1px solid #e5e7eb;
                border-radius: 4px;
                background-color: white;
                gridline-color: #e5e7eb;
            }
            QHeaderView::section {
                background-color: #f9fafb;
                padding: 6px;
                border: none;
                border-bottom: 1px solid #e5e7eb;
                font-weight: 600;
                font-size: 11px;
            }
        """)
        layout.addWidget(self.docs_table)

        return group

    def _browse_upload_file(self):
        """Open file dialog to select a document to upload."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Document to Upload",
            "",
            "All Supported Files (*.pdf *.png *.jpg *.jpeg *.tif *.tiff *.gpx *.gpkg *.zip *.docx);;"
            "PDF Files (*.pdf);;"
            "Images (*.png *.jpg *.jpeg *.tif *.tiff);;"
            "GPX Files (*.gpx);;"
            "ZIP Archives (*.zip);;"
            "All Files (*)"
        )
        if file_path:
            self.upload_file_path.setText(file_path)
            # Auto-fill title from filename if title is empty
            if not self.upload_title.text().strip():
                self.upload_title.setText(Path(file_path).stem)

    def _upload_document(self):
        """Upload the selected file to the claim package."""
        file_path = self.upload_file_path.text().strip()
        if not file_path:
            QMessageBox.warning(self, "No File", "Please select a file to upload.")
            return

        if not Path(file_path).is_file():
            QMessageBox.warning(self, "File Not Found", f"File not found:\n{file_path}")
            return

        if not self.state.claim_package_id:
            QMessageBox.warning(
                self, "No Package",
                "No claim package exists yet.\n\n"
                "Complete Step 6 (Generate Documents) first to create the package, "
                "or push claims to the server."
            )
            return

        doc_type = self.upload_doc_type.currentData()
        title = self.upload_title.text().strip()
        description = self.upload_description.toPlainText().strip()

        self.upload_btn.setEnabled(False)
        self.upload_btn.setText("Uploading...")
        self.upload_progress.show()
        self.upload_progress.setValue(30)

        try:
            result = self.claims_manager.upload_package_document(
                claim_package_id=self.state.claim_package_id,
                file_path=file_path,
                document_type=doc_type,
                title=title,
                description=description,
            )

            self.upload_progress.setValue(100)

            uploaded_title = result.get('title', Path(file_path).name)
            type_display = result.get('document_type_display', doc_type)

            self.upload_status_label.setText(
                f"Uploaded: {uploaded_title} ({type_display})"
            )
            self.upload_status_label.setStyleSheet(self._get_success_label_style())

            self.logger.info(
                f"[CLAIMS] Uploaded document '{uploaded_title}' to package "
                f"{self.state.claim_package_id}"
            )

            # Clear form for next upload
            self.upload_file_path.clear()
            self.upload_title.clear()
            self.upload_description.clear()

            # Refresh the documents list
            self._refresh_documents_list()

        except Exception as e:
            self.logger.error(f"[CLAIMS] Document upload error: {e}")
            self.upload_status_label.setText(f"Upload failed: {e}")
            self.upload_status_label.setStyleSheet(self._get_error_label_style())
            QMessageBox.critical(self, "Upload Error", f"Failed to upload document:\n\n{e}")

        finally:
            self.upload_btn.setEnabled(True)
            self.upload_btn.setText("Upload to Package")
            self.upload_progress.hide()

    def _refresh_documents_list(self):
        """Fetch and display documents already uploaded to this package."""
        if not self.state.claim_package_id:
            self.docs_table.setRowCount(0)
            return

        try:
            result = self.claims_manager.list_package_documents(
                self.state.claim_package_id
            )
            documents = result.get('documents', [])

            self.docs_table.setRowCount(len(documents))
            for row, doc in enumerate(documents):
                title_item = QTableWidgetItem(doc.get('title', ''))
                self.docs_table.setItem(row, 0, title_item)

                type_display = doc.get('document_type_display', doc.get('document_type', ''))
                type_item = QTableWidgetItem(type_display)
                self.docs_table.setItem(row, 1, type_item)

                file_size = doc.get('file_size', 0)
                size_str = self._format_file_size(file_size) if file_size else ""
                size_item = QTableWidgetItem(size_str)
                self.docs_table.setItem(row, 2, size_item)

            self.logger.info(
                f"[CLAIMS] Loaded {len(documents)} documents for package "
                f"{self.state.claim_package_id}"
            )

        except Exception as e:
            self.logger.error(f"[CLAIMS] Error loading package documents: {e}")
            self.docs_table.setRowCount(0)

    @staticmethod
    def _format_file_size(size_bytes: int) -> str:
        """Format file size in human-readable form."""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        else:
            return f"{size_bytes / (1024 * 1024):.1f} MB"

    # =========================================================================
    # Map Generation
    # =========================================================================

    def _generate_maps(self):
        """Generate all applicable print layout maps."""
        if not self.state.processed_claims:
            QMessageBox.warning(
                self, "No Claims",
                "Claims must be processed before generating maps.\n"
                "Complete Step 6 first."
            )
            return

        self.generate_maps_btn.setEnabled(False)
        self.generate_maps_btn.setText("Generating...")
        self.maps_progress.show()
        self.maps_progress.setValue(0)

        try:
            from ...processors.claims_map_generator import ClaimsMapGenerator

            self.maps_progress.setValue(10)
            generator = ClaimsMapGenerator(self.state)

            self.maps_progress.setValue(30)
            results = generator.generate_all_maps()

            self.maps_progress.setValue(90)

            # Build summary of created layouts
            layout_names = [v for v in results.values() if v]
            self.maps_progress.setValue(100)

            state_code = results.get('state_filing_map')
            state_note = ""
            if state_code:
                state_note = (
                    "\n\nNote: State filing map included for state-specific "
                    "requirements."
                )

            QMessageBox.information(
                self, "Maps Generated",
                f"Created {len(layout_names)} print layout(s):\n\n"
                + "\n".join(f"  \u2022 {name}" for name in layout_names)
                + state_note
                + "\n\nOpen the Layout Manager (Project \u2192 Layouts) "
                "to view, edit, and export them."
            )

            self.maps_status_label.setText(
                f"Generated {len(layout_names)} map layout(s)"
            )
            self.maps_status_label.setStyleSheet(self._get_success_label_style())
            self.logger.info(
                f"[CLAIMS] Generated {len(layout_names)} map layouts: "
                + ", ".join(layout_names)
            )

        except Exception as e:
            self.logger.error(f"[CLAIMS] Map generation error: {e}")
            self.maps_status_label.setText(f"Error: {e}")
            self.maps_status_label.setStyleSheet(self._get_error_label_style())
            QMessageBox.critical(
                self, "Map Generation Error",
                f"Failed to generate maps:\n\n{e}"
            )

        finally:
            self.generate_maps_btn.setEnabled(True)
            self.generate_maps_btn.setText("Generate Maps")
            self.maps_progress.hide()

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
        if not is_layer_valid(waypoints_layer):
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
            is_null = (lat_val is None or
                       (hasattr(lat_val, 'isNull') and lat_val.isNull()) or
                       lat_val == 0)

            if is_null:
                # Validate that Name is filled in
                name_val = feature['Name'] if 'Name' in field_names else None
                has_name = (name_val is not None and
                            not (hasattr(name_val, 'isNull') and name_val.isNull()) and
                            str(name_val).strip() != '')
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
        if not is_layer_valid(waypoints_layer):
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
        """Find the Claims Waypoints layer for the current claim block.

        Uses the state-tracked layer ID first (set by Step 6 when the layer
        is created), then falls back to matching by project-suffixed name
        to avoid returning a waypoints layer from a previous claim block.

        Returns:
            The waypoints layer, or None if not found.
        """
        # 1. Prefer the layer already tracked in wizard state (set by Step 6)
        if self.state.waypoints_layer_id:
            layer = QgsProject.instance().mapLayer(self.state.waypoints_layer_id)
            if is_layer_valid(layer):
                self.logger.debug(f"[Step7] Using waypoints layer from state: {layer.name()}")
                return layer

        # 2. Fallback: match by name, preferring project-suffixed name
        #    Derive expected suffix from the claims layer name
        base_name = "Claims Waypoints"
        expected_name = base_name
        if self.state.claims_layer and is_layer_valid(self.state.claims_layer):
            layer_name = self.state.claims_layer.name()
            if '[' in layer_name and ']' in layer_name:
                start = layer_name.index('[') + 1
                end = layer_name.index(']')
                project_name = layer_name[start:end]
                expected_name = f"{base_name} [{project_name}]"

        # First pass: look for exact project-suffixed match
        if expected_name != base_name:
            for layer in QgsProject.instance().mapLayers().values():
                if isinstance(layer, QgsVectorLayer) and is_layer_valid(layer):
                    if layer.name() == expected_name:
                        self.logger.info(f"[Step7] Using waypoints layer (name match): {layer.name()}")
                        self.state.waypoints_layer_id = layer.id()
                        return layer

        # Second pass: fall back to any "Claims Waypoints" layer (single block scenario)
        for layer in QgsProject.instance().mapLayers().values():
            if isinstance(layer, QgsVectorLayer) and is_layer_valid(layer):
                if layer.name() == base_name or layer.name().startswith(f"{base_name} ["):
                    self.logger.info(f"[Step7] Using waypoints layer (fallback scan): {layer.name()}")
                    self.state.waypoints_layer_id = layer.id()
                    return layer

        return None

    def _auto_generate_witnesses(self):
        """Auto-generate witness waypoints for stakes on private land."""
        if not self.state.processed_waypoints:
            QMessageBox.warning(
                self, "No Waypoints",
                "Process claims first to generate waypoints."
            )
            return

        if not self.state.processed_claims:
            QMessageBox.warning(
                self, "No Claims",
                "Process claims first."
            )
            return

        # Confirm
        reply = QMessageBox.question(
            self,
            "Auto-generate Witness Points",
            "This will check each corner, sideline, and endline waypoint against "
            "federal land boundaries.\n\n"
            "For any stake on private land, a witness waypoint will be generated "
            "on nearby public land (preferring claim boundary edges).\n\n"
            "Requires federal lands data to be imported on the server.\n\n"
            "Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self.auto_witness_btn.setEnabled(False)
        self.auto_witness_btn.setText("Generating...")

        try:
            result = self.claims_manager.generate_witnesses(
                waypoints=self.state.processed_waypoints,
                claims=self.state.processed_claims,
                epsg=self.state.project_epsg,
            )

            witnesses = result.get('witnesses', [])
            witness_count = result.get('witness_count', 0)
            private_count = result.get('private_stake_count', 0)

            if not witnesses:
                QMessageBox.information(
                    self,
                    "No Witnesses Needed",
                    "All stakes are on public land. No witness waypoints needed."
                )
                return

            # Add witnesses to the QGIS waypoints layer first, then update state
            self._add_witnesses_to_layer(witnesses)
            self.state.processed_waypoints.extend(witnesses)

            # Refresh table
            self._refresh_waypoints_table()

            QMessageBox.information(
                self,
                "Witness Points Generated",
                f"Generated {witness_count} witness point(s) for "
                f"{private_count} stake(s) on private land.\n\n"
                "Witness points have been added to the waypoints table."
            )

            self.emit_status(
                f"Generated {witness_count} witness points",
                "success"
            )

        except Exception as e:
            self.logger.error(f"[CLAIMS] Witness generation error: {e}")
            QMessageBox.critical(self, "Error", str(e))

        finally:
            self.auto_witness_btn.setEnabled(True)
            self.auto_witness_btn.setText("Auto-generate Witness Points")

    def _add_witnesses_to_layer(self, witnesses):
        """Add witness waypoints to the QGIS waypoints layer."""
        from qgis.core import QgsFeature, QgsGeometry, QgsPointXY

        waypoints_layer = self._get_waypoints_layer()
        if not is_layer_valid(waypoints_layer):
            self.logger.warning("[CLAIMS] No waypoints layer to add witnesses to")
            return

        source_crs = waypoints_layer.crs()
        wgs84 = QgsCoordinateReferenceSystem('EPSG:4326')
        transform = None
        if source_crs and source_crs != wgs84:
            transform = QgsCoordinateTransform(
                wgs84, source_crs, QgsProject.instance()
            )

        was_editing = waypoints_layer.isEditable()
        if not was_editing:
            waypoints_layer.startEditing()

        field_names = waypoints_layer.fields().names()

        for wit in witnesses:
            lat = wit.get('lat')
            lon = wit.get('lon')
            if lat is None or lon is None:
                continue

            point = QgsPointXY(lon, lat)
            if transform:
                point = transform.transform(point)

            feat = QgsFeature(waypoints_layer.fields())
            feat.setGeometry(QgsGeometry.fromPointXY(point))

            if 'Name' in field_names:
                feat.setAttribute('Name', wit.get('sequence_number', wit.get('name', '')))
            if 'waypoint_type' in field_names:
                feat.setAttribute('waypoint_type', 'witness')
            if 'Symbol' in field_names:
                feat.setAttribute('Symbol', 'Navaid, Amber')
            if 'Latitude' in field_names:
                feat.setAttribute('Latitude', lat)
            if 'Longitude' in field_names:
                feat.setAttribute('Longitude', lon)

            waypoints_layer.addFeature(feat)

        if not was_editing:
            waypoints_layer.commitChanges()
        else:
            waypoints_layer.triggerRepaint()

        self.logger.info(
            f"[CLAIMS] Added {len(witnesses)} witness points to waypoints layer"
        )

    def _add_witness_waypoint(self):
        """Show instructions for manually adding a witness waypoint."""
        QMessageBox.information(
            self,
            "Manual Witness Waypoint",
            "To add a witness waypoint manually:\n\n"
            "1. Click on the 'Claims Waypoints' layer in QGIS\n"
            "2. Enable editing mode\n"
            "3. Use the Add Point tool to add a waypoint\n"
            "4. Enter the waypoint name (e.g., 'WIT 32') in the Name field\n"
            "5. Click 'Refresh Table' to update this list\n\n"
            "Or use 'Auto-generate Witness Points' to let the server\n"
            "identify private-land stakes and place witnesses automatically."
        )

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

        # Stronger warning if already pushed (server uses create-or-update so
        # it's safe, but the user should know they're re-pushing)
        if self.state.claims_pushed:
            reply = QMessageBox.question(
                self,
                "Already Pushed",
                "These claims have already been pushed to the server.\n\n"
                "Pushing again will update the existing LandHoldings and ClaimStakes. "
                "Do you want to continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )
        else:
            # Confirm
            reply = QMessageBox.question(
                self,
                "Push to Server",
                f"Push {len(self.state.processed_claims)} claims as LandHoldings and "
                f"{len(self.state.processed_waypoints)} waypoints as ClaimStakes to the server?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No
            )

        if reply != QMessageBox.StandardButton.Yes:
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

            self.progress_bar.setValue(50)

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

            self.progress_bar.setValue(65)

            # Track that claims have been pushed (for double-push warning)
            self.state.claims_pushed = True
            self.state.save_to_qgis_project()
            if self.state.geopackage_path:
                self.state.save_to_geopackage()

            # --- GeoPackage upload + link ---
            gpkg_uploaded = False
            if self.state.geopackage_path and self.state.claim_package_id and self.data_manager:
                try:
                    self.push_status_label.setText("Saving layer styles to GeoPackage...")
                    self.progress_bar.setValue(70)

                    from ...utils.gpkg_utils import save_styles_to_geopackage
                    styles_saved = save_styles_to_geopackage(self.state.geopackage_path)
                    logger.info(f"[PUSH] Saved {styles_saved} style(s) to GeoPackage")

                    self.push_status_label.setText("Uploading GeoPackage to server...")
                    self.progress_bar.setValue(80)

                    gpkg_name = Path(self.state.geopackage_path).name
                    upload_result = self.data_manager.upload_project_file(
                        file_path=self.state.geopackage_path,
                        name=gpkg_name,
                        category='GP',
                        description=f"Claims GeoPackage for {self.state.grid_name_prefix or 'claims'}",
                        is_raster=False,
                        epsg=self.state.project_epsg,
                    )

                    self.push_status_label.setText("Linking GeoPackage to claim package...")
                    self.progress_bar.setValue(90)

                    project_file_id = upload_result.get('id')
                    if project_file_id:
                        self.claims_manager.link_geopackage(
                            self.state.claim_package_id, project_file_id
                        )
                        gpkg_uploaded = True
                        logger.info(
                            f"[PUSH] GeoPackage uploaded (ProjectFile {project_file_id}) "
                            f"and linked to ClaimPackage {self.state.claim_package_id}"
                        )

                except Exception as gpkg_err:
                    # Non-fatal — claims push already succeeded
                    logger.warning(f"[PUSH] GeoPackage upload/link failed: {gpkg_err}")
                    self.emit_status(
                        f"Warning: GeoPackage upload failed: {gpkg_err}", "warning"
                    )

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

            gpkg_msg = ", GeoPackage uploaded" if gpkg_uploaded else ""
            self.push_status_label.setText(
                f"Pushed: {lh_str} LandHoldings, {st_str} ClaimStakes"
                + (f", {docs_linked} docs linked" if docs_linked else "")
                + gpkg_msg
            )
            self.push_status_label.setStyleSheet(self._get_success_label_style())

            doc_msg = f"\nDocuments: {docs_linked} linked to claims" if docs_linked else ""
            orphan_msg = f"\n{st_orphans} orphan stakes cleaned up" if st_orphans else ""
            gpkg_info = "\nGeoPackage: uploaded and linked to claim package" if gpkg_uploaded else ""
            QMessageBox.information(
                self,
                "Push Complete",
                f"LandHoldings: {lh_str}\n"
                f"ClaimStakes: {st_str}{orphan_msg}{doc_msg}{gpkg_info}\n\n"
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
        self._refresh_documents_list()

    def on_leave(self):
        """Called when leaving step."""
        self.save_state()

    def save_state(self):
        """Save widget state to shared state."""
        # Most state is already stored during operations

    def load_state(self):
        """Load widget state from shared state."""
        # Update button states
        has_processed = len(self.state.processed_claims) > 0
        self.push_btn.setEnabled(has_processed)
        self.generate_maps_btn.setEnabled(has_processed)

        # Upload requires a claim package to exist
        has_package = bool(self.state.claim_package_id)
        self.upload_btn.setEnabled(has_package)
        self.refresh_docs_btn.setEnabled(has_package)
        if not has_package:
            self.upload_status_label.setText(
                "Complete Step 6 or push claims first to enable document uploads."
            )
            self.upload_status_label.setStyleSheet(self._get_info_label_style())
