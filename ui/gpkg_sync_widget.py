# -*- coding: utf-8 -*-
"""
Widget for syncing GeoPackage files between QGIS and the geodb.io server.

Push: Scan open GeoPackages in the project, save styles, upload to server.
Pull: List server GeoPackages, download and load all layers with styles.
Versioning: Overwrite existing or auto-increment version suffix.
"""
import os
import re
from pathlib import Path
from typing import Optional, List, Dict, Any
from urllib.parse import urlparse

from qgis.PyQt.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QGroupBox,
    QAbstractItemView, QFrame, QScrollArea, QMessageBox,
    QProgressBar, QCheckBox, QApplication,
)
from qgis.PyQt.QtCore import Qt, pyqtSignal, QTimer, QUrl
from qgis.PyQt.QtGui import QColor
from qgis.PyQt.QtNetwork import QNetworkRequest

from qgis.core import (
    QgsProject, QgsVectorLayer, QgsApplication,
    QgsBlockingNetworkRequest,
)

from ..utils.logger import PluginLogger


class GpkgSyncWidget(QWidget):
    """Widget for syncing GeoPackage files between QGIS and the geodb.io server."""

    # Signals
    status_message = pyqtSignal(str, str)  # message, level
    upload_completed = pyqtSignal(dict)  # server response

    def __init__(self, data_manager, project_manager, api_client, parent=None):
        super().__init__(parent)
        self.data_manager = data_manager
        self.project_manager = project_manager
        self.api_client = api_client
        self.logger = PluginLogger.get_logger()

        # State
        self._local_geopackages: List[Dict[str, Any]] = []
        self._server_geopackages: List[Dict[str, Any]] = []
        self._is_uploading = False
        self._is_downloading = False

        # Cache directory for downloaded GeoPackages
        profile_dir = QgsApplication.qgisSettingsDirPath()
        self._cache_dir = Path(profile_dir) / 'geodb_cache' / 'geopackages'
        self._cache_dir.mkdir(parents=True, exist_ok=True)

        self._build_ui()

    # ==================== UI BUILD ====================

    def _build_ui(self):
        """Build the widget UI with push and pull sections."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(10)
        layout.setContentsMargins(12, 12, 12, 12)

        # Title
        title = QLabel("GeoPackage Sync")
        title.setStyleSheet("font-size: 16px; font-weight: bold; color: #1e293b;")
        layout.addWidget(title)

        subtitle = QLabel(
            "Push GeoPackage files from your QGIS project to the server, or pull "
            "GeoPackages from the server to load locally. Layer styles are preserved "
            "in the GeoPackage for seamless sharing."
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: #64748b; font-size: 12px; margin-bottom: 4px;")
        layout.addWidget(subtitle)

        # --- Push Section ---
        self._build_push_section(layout)

        # --- Pull Section ---
        self._build_pull_section(layout)

        # --- Status ---
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setVisible(False)
        layout.addWidget(self.status_label)

        layout.addStretch()

        scroll.setWidget(content)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(scroll)

    def _build_push_section(self, parent_layout):
        """Build the Push GeoPackage section."""
        group = QGroupBox("Push GeoPackage to Server")
        group.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                font-size: 13px;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                margin-top: 8px;
                padding-top: 16px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
                color: #1e293b;
            }
        """)
        group_layout = QVBoxLayout(group)
        group_layout.setSpacing(8)

        # Scan button
        scan_row = QHBoxLayout()
        self.scan_button = QPushButton("Scan Project")
        self.scan_button.setStyleSheet("""
            QPushButton {
                background-color: #3b82f6;
                color: white;
                font-weight: bold;
                padding: 8px 16px;
                border-radius: 6px;
                font-size: 12px;
            }
            QPushButton:hover { background-color: #2563eb; }
            QPushButton:pressed { background-color: #1d4ed8; }
        """)
        self.scan_button.clicked.connect(self._on_scan_clicked)
        scan_row.addWidget(self.scan_button)

        self.push_count_label = QLabel("")
        self.push_count_label.setStyleSheet("color: #64748b; font-size: 11px;")
        scan_row.addWidget(self.push_count_label)

        scan_row.addStretch()
        group_layout.addLayout(scan_row)

        # Push table
        self.push_table = QTableWidget()
        self.push_table.setColumnCount(4)
        self.push_table.setHorizontalHeaderLabels(["", "GeoPackage", "Layers", "Size"])
        self.push_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.push_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.push_table.setAlternatingRowColors(True)
        self.push_table.verticalHeader().setVisible(False)
        self.push_table.setStyleSheet("""
            QTableWidget {
                border: 1px solid #e2e8f0;
                border-radius: 6px;
                gridline-color: #f1f5f9;
            }
            QTableWidget::item { padding: 4px 8px; }
            QHeaderView::section {
                background-color: #f8fafc;
                border: none;
                border-bottom: 2px solid #e2e8f0;
                padding: 6px 8px;
                font-weight: bold;
                color: #475569;
            }
        """)
        header = self.push_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.push_table.setColumnWidth(0, 30)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.push_table.setMinimumHeight(120)
        self.push_table.setMaximumHeight(200)
        group_layout.addWidget(self.push_table)

        # Save styles checkbox
        self.save_styles_checkbox = QCheckBox("Save layer styles into GeoPackage before upload")
        self.save_styles_checkbox.setChecked(True)
        self.save_styles_checkbox.setStyleSheet("font-size: 12px; color: #475569;")
        group_layout.addWidget(self.save_styles_checkbox)

        # Push button + progress
        push_row = QHBoxLayout()
        push_row.addStretch()
        self.push_button = QPushButton("Push Selected")
        self.push_button.setEnabled(False)
        self.push_button.setStyleSheet("""
            QPushButton {
                background-color: #10b981;
                color: white;
                font-weight: bold;
                padding: 8px 20px;
                border-radius: 6px;
                font-size: 13px;
            }
            QPushButton:hover { background-color: #059669; }
            QPushButton:pressed { background-color: #047857; }
            QPushButton:disabled {
                background-color: #d1d5db;
                color: #9ca3af;
            }
        """)
        self.push_button.clicked.connect(self._on_push_clicked)
        push_row.addWidget(self.push_button)
        group_layout.addLayout(push_row)

        self.push_progress = QProgressBar()
        self.push_progress.setVisible(False)
        self.push_progress.setStyleSheet("""
            QProgressBar {
                border: 1px solid #e2e8f0;
                border-radius: 4px;
                text-align: center;
                height: 20px;
            }
            QProgressBar::chunk {
                background-color: #10b981;
                border-radius: 3px;
            }
        """)
        group_layout.addWidget(self.push_progress)

        parent_layout.addWidget(group)

    def _build_pull_section(self, parent_layout):
        """Build the Pull GeoPackage section."""
        group = QGroupBox("Pull GeoPackage from Server")
        group.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                font-size: 13px;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                margin-top: 8px;
                padding-top: 16px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 6px;
                color: #1e293b;
            }
        """)
        group_layout = QVBoxLayout(group)
        group_layout.setSpacing(8)

        # Refresh button
        refresh_row = QHBoxLayout()
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setStyleSheet("""
            QPushButton {
                background-color: #3b82f6;
                color: white;
                font-weight: bold;
                padding: 8px 16px;
                border-radius: 6px;
                font-size: 12px;
            }
            QPushButton:hover { background-color: #2563eb; }
            QPushButton:pressed { background-color: #1d4ed8; }
        """)
        self.refresh_button.clicked.connect(self._on_refresh_clicked)
        refresh_row.addWidget(self.refresh_button)

        self.pull_count_label = QLabel("")
        self.pull_count_label.setStyleSheet("color: #64748b; font-size: 11px;")
        refresh_row.addWidget(self.pull_count_label)

        refresh_row.addStretch()
        group_layout.addLayout(refresh_row)

        # Pull table
        self.pull_table = QTableWidget()
        self.pull_table.setColumnCount(4)
        self.pull_table.setHorizontalHeaderLabels(["", "GeoPackage", "Size", "Date"])
        self.pull_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.pull_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.pull_table.setAlternatingRowColors(True)
        self.pull_table.verticalHeader().setVisible(False)
        self.pull_table.setStyleSheet("""
            QTableWidget {
                border: 1px solid #e2e8f0;
                border-radius: 6px;
                gridline-color: #f1f5f9;
            }
            QTableWidget::item { padding: 4px 8px; }
            QHeaderView::section {
                background-color: #f8fafc;
                border: none;
                border-bottom: 2px solid #e2e8f0;
                padding: 6px 8px;
                font-weight: bold;
                color: #475569;
            }
        """)
        header = self.pull_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.pull_table.setColumnWidth(0, 30)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.pull_table.setMinimumHeight(120)
        self.pull_table.setMaximumHeight(200)
        group_layout.addWidget(self.pull_table)

        # Pull button + progress
        pull_row = QHBoxLayout()
        pull_row.addStretch()
        self.pull_button = QPushButton("Pull Selected")
        self.pull_button.setEnabled(False)
        self.pull_button.setStyleSheet("""
            QPushButton {
                background-color: #8b5cf6;
                color: white;
                font-weight: bold;
                padding: 8px 20px;
                border-radius: 6px;
                font-size: 13px;
            }
            QPushButton:hover { background-color: #7c3aed; }
            QPushButton:pressed { background-color: #6d28d9; }
            QPushButton:disabled {
                background-color: #d1d5db;
                color: #9ca3af;
            }
        """)
        self.pull_button.clicked.connect(self._on_pull_clicked)
        pull_row.addWidget(self.pull_button)
        group_layout.addLayout(pull_row)

        self.pull_progress = QProgressBar()
        self.pull_progress.setVisible(False)
        self.pull_progress.setStyleSheet("""
            QProgressBar {
                border: 1px solid #e2e8f0;
                border-radius: 4px;
                text-align: center;
                height: 20px;
            }
            QProgressBar::chunk {
                background-color: #8b5cf6;
                border-radius: 3px;
            }
        """)
        group_layout.addWidget(self.pull_progress)

        parent_layout.addWidget(group)

    # ==================== PUSH: SCANNING ====================

    def _on_scan_clicked(self):
        """Scan the QGIS project for GeoPackage layers."""
        project = self.project_manager.active_project
        if not project:
            self._show_status("No project selected. Please select a project first.", "error")
            return

        self._show_status("Scanning project for GeoPackages...", "info")
        self.scan_button.setEnabled(False)

        try:
            self._local_geopackages = self._discover_local_geopackages()

            if not self._local_geopackages:
                self._show_status(
                    "No GeoPackage layers found in the current QGIS project.",
                    "warning"
                )
                self._populate_push_table()
                return

            count = len(self._local_geopackages)
            total_layers = sum(g['layer_count'] for g in self._local_geopackages)
            self._show_status(
                f"Found {count} GeoPackage(s) with {total_layers} total layer(s).",
                "success"
            )
            self._populate_push_table()

        except Exception as e:
            self.logger.error(f"GeoPackage scan failed: {e}")
            self._show_status(f"Scan failed: {e}", "error")
        finally:
            self.scan_button.setEnabled(True)

    def _discover_local_geopackages(self) -> List[Dict[str, Any]]:
        """
        Discover all unique GeoPackage files referenced by layers in the QGIS project.

        Returns list of dicts with GeoPackage metadata.
        """
        gpkg_map: Dict[str, Dict[str, Any]] = {}
        qgs_project = QgsProject.instance()

        for layer_id, layer in qgs_project.mapLayers().items():
            if not isinstance(layer, QgsVectorLayer):
                continue

            source = layer.source()
            if not source:
                continue

            # Extract the GeoPackage path from the source URI
            # Format: "/path/to/file.gpkg|layername=LayerName"
            gpkg_path = source.split('|')[0]
            if not gpkg_path.lower().endswith('.gpkg'):
                continue

            # Normalize path for comparison
            gpkg_path_normalized = os.path.normpath(gpkg_path)

            if not os.path.isfile(gpkg_path_normalized):
                continue

            if gpkg_path_normalized not in gpkg_map:
                try:
                    file_size = os.path.getsize(gpkg_path_normalized)
                except OSError:
                    file_size = 0

                gpkg_map[gpkg_path_normalized] = {
                    'path': gpkg_path_normalized,
                    'filename': os.path.basename(gpkg_path_normalized),
                    'file_size': file_size,
                    'layer_count': 0,
                    'layer_names': [],
                    'selected': True,
                }

            gpkg_map[gpkg_path_normalized]['layer_count'] += 1
            gpkg_map[gpkg_path_normalized]['layer_names'].append(layer.name())

        result = list(gpkg_map.values())
        self.logger.info(f"Discovered {len(result)} local GeoPackage(s)")
        return result

    def _populate_push_table(self):
        """Populate the push table with discovered GeoPackages."""
        self.push_table.setRowCount(0)
        self.push_table.setRowCount(len(self._local_geopackages))

        for row, gpkg in enumerate(self._local_geopackages):
            # Checkbox
            cb = QCheckBox()
            cb.setChecked(gpkg.get('selected', True))
            cb.stateChanged.connect(lambda state, r=row: self._on_push_checkbox_changed(r, state))
            cb_widget = QWidget()
            cb_layout = QHBoxLayout(cb_widget)
            cb_layout.addWidget(cb)
            cb_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cb_layout.setContentsMargins(0, 0, 0, 0)
            self.push_table.setCellWidget(row, 0, cb_widget)

            # Filename
            name_item = QTableWidgetItem(gpkg['filename'])
            name_item.setToolTip(gpkg['path'])
            self.push_table.setItem(row, 1, name_item)

            # Layer count
            layers_str = f"{gpkg['layer_count']} layer(s)"
            layers_item = QTableWidgetItem(layers_str)
            layers_item.setToolTip(', '.join(gpkg['layer_names']))
            layers_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            self.push_table.setItem(row, 2, layers_item)

            # File size
            size_item = QTableWidgetItem(self._format_file_size(gpkg['file_size']))
            size_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            self.push_table.setItem(row, 3, size_item)

        self.push_count_label.setText(f"{len(self._local_geopackages)} GeoPackage(s) found")
        self._update_push_button_state()

    def _on_push_checkbox_changed(self, row: int, state: int):
        """Handle checkbox change in push table."""
        if row < len(self._local_geopackages):
            self._local_geopackages[row]['selected'] = (state == Qt.CheckState.Checked.value)
            self._update_push_button_state()

    def _update_push_button_state(self):
        """Update push button enabled state."""
        selected = sum(1 for g in self._local_geopackages if g.get('selected', False))
        self.push_button.setEnabled(selected > 0 and not self._is_uploading)

    # ==================== PUSH: UPLOAD ====================

    def _on_push_clicked(self):
        """Push selected GeoPackages to the server."""
        selected = [g for g in self._local_geopackages if g.get('selected', False)]
        if not selected:
            self._show_status("No GeoPackages selected.", "warning")
            return

        project = self.project_manager.active_project
        if not project:
            self._show_status("No project selected.", "error")
            return

        self._is_uploading = True
        self.push_button.setEnabled(False)
        self.scan_button.setEnabled(False)
        self.push_progress.setVisible(True)
        self.push_progress.setMaximum(len(selected))
        self.push_progress.setValue(0)

        # Fetch server GeoPackages for version conflict detection
        self._show_status("Checking server for existing GeoPackages...", "info")
        QApplication.processEvents()
        self._fetch_server_geopackages()

        uploaded = 0
        errors = []
        cancelled = 0

        for i, gpkg in enumerate(selected):
            self.push_progress.setValue(i)
            QApplication.processEvents()

            try:
                # Save styles if checkbox is checked
                if self.save_styles_checkbox.isChecked():
                    self._show_status(
                        f"Saving styles into {gpkg['filename']}...", "info"
                    )
                    QApplication.processEvents()
                    saved = self._save_styles_to_geopackage(gpkg['path'])
                    if saved > 0:
                        self.logger.info(
                            f"Saved {saved} style(s) into {gpkg['filename']}"
                        )

                # Check for version conflict
                upload_name = gpkg['filename']
                conflict = self._check_version_conflict(upload_name, self._server_geopackages)

                if conflict:
                    action = self._show_version_dialog(upload_name, conflict)

                    if action == 'overwrite':
                        self._show_status(
                            f"Overwriting {upload_name} on server...", "info"
                        )
                        QApplication.processEvents()
                        try:
                            self.api_client.delete_record(
                                'ProjectFile', conflict['id']
                            )
                        except Exception as e:
                            self.logger.error(
                                f"Failed to delete old file: {e}"
                            )
                            errors.append(f"{upload_name}: Failed to overwrite - {e}")
                            continue
                    elif action == 'new_version':
                        upload_name = self._get_next_version_name(
                            upload_name, self._server_geopackages
                        )
                        self.logger.info(
                            f"Saving as new version: {upload_name}"
                        )
                    else:  # cancel
                        cancelled += 1
                        continue

                # Upload
                self._show_status(f"Uploading {upload_name}...", "info")
                QApplication.processEvents()

                result = self.data_manager.upload_project_file(
                    file_path=gpkg['path'],
                    name=upload_name,
                    category='GP',
                    description=f"GeoPackage synced from QGIS ({gpkg['layer_count']} layers)",
                    is_raster=False,
                )

                uploaded += 1
                self.upload_completed.emit(result)
                self.logger.info(
                    f"Uploaded GeoPackage '{upload_name}' (ID: {result.get('id', '?')})"
                )

                # Add to server list so subsequent pushes in the same session
                # detect this as existing
                self._server_geopackages.append({
                    'id': result.get('id'),
                    'name': upload_name,
                })

            except Exception as e:
                self.logger.error(f"Upload failed for {gpkg['filename']}: {e}")
                errors.append(f"{gpkg['filename']}: {e}")

        self.push_progress.setValue(len(selected))

        # Report
        parts = []
        if uploaded:
            parts.append(f"{uploaded} uploaded")
        if cancelled:
            parts.append(f"{cancelled} cancelled")
        if errors:
            parts.append(f"{len(errors)} error(s)")

        if errors:
            self._show_status(
                f"Push complete: {', '.join(parts)}. Errors: {'; '.join(errors)}",
                "warning"
            )
        else:
            self._show_status(f"Push complete: {', '.join(parts)}.", "success")

        self._is_uploading = False
        self.scan_button.setEnabled(True)
        self._update_push_button_state()
        QTimer.singleShot(3000, lambda: self.push_progress.setVisible(False))

    def _save_styles_to_geopackage(self, gpkg_path: str) -> int:
        """
        Save current QGIS styles for all layers sourced from this GeoPackage.

        Uses saveStyleToDatabase() to write styles into the GeoPackage's
        layer_styles table. This is non-destructive and idempotent.

        Returns the number of styles successfully saved.
        """
        saved = 0
        gpkg_norm = os.path.normpath(gpkg_path).replace('\\', '/')

        for layer_id, layer in QgsProject.instance().mapLayers().items():
            if not isinstance(layer, QgsVectorLayer):
                continue

            source = layer.source()
            source_path = source.split('|')[0]
            source_norm = os.path.normpath(source_path).replace('\\', '/')

            if source_norm != gpkg_norm:
                continue
            if '|layername=' not in source:
                continue

            # saveStyleToDatabase returns (message, success_bool)
            result = layer.saveStyleToDatabase(
                '',               # Empty name = default style
                'geodb sync',     # Description
                True,             # Use as default style
                ''                # No UI file
            )

            if isinstance(result, tuple):
                msg, success = result
                if success:
                    saved += 1
                else:
                    self.logger.warning(
                        f"Failed to save style for layer '{layer.name()}': {msg}"
                    )
            else:
                # Older QGIS versions may return differently
                saved += 1

        return saved

    def _fetch_server_geopackages(self):
        """Fetch GeoPackage ProjectFiles from the server."""
        project = self.project_manager.active_project
        if not project:
            self._server_geopackages = []
            return

        try:
            response = self.api_client.get_all_paginated(
                model_name='ProjectFile',
                project_id=project.id,
                params={'category': 'GP'},
                include_deletion_metadata=False
            )

            if isinstance(response, dict):
                self._server_geopackages = response.get('results', [])
            else:
                self._server_geopackages = response if response else []

            self.logger.info(
                f"Fetched {len(self._server_geopackages)} server GeoPackage(s)"
            )
        except Exception as e:
            self.logger.warning(f"Failed to fetch server GeoPackages: {e}")
            self._server_geopackages = []

    def _check_version_conflict(
        self, filename: str, server_files: List[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """
        Check if a GeoPackage with the same name exists on the server.

        Returns the conflicting server file dict, or None.
        """
        name_lower = filename.lower()
        for sf in server_files:
            server_name = (sf.get('name', '') or '').lower()
            if server_name == name_lower:
                return sf
        return None

    def _show_version_dialog(
        self, filename: str, existing: Dict[str, Any]
    ) -> str:
        """
        Show a dialog when a GeoPackage already exists on the server.

        Returns: 'overwrite', 'new_version', or 'cancel'.
        """
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Icon.Question)
        msg.setWindowTitle("GeoPackage Already Exists")
        msg.setText(
            f"A GeoPackage named '{filename}' already exists on the server."
        )
        msg.setInformativeText("What would you like to do?")

        overwrite_btn = msg.addButton(
            "Overwrite", QMessageBox.ButtonRole.DestructiveRole
        )
        version_btn = msg.addButton(
            "Save as New Version", QMessageBox.ButtonRole.AcceptRole
        )
        cancel_btn = msg.addButton(QMessageBox.StandardButton.Cancel)

        msg.setDefaultButton(version_btn)
        msg.exec()

        clicked = msg.clickedButton()
        if clicked == overwrite_btn:
            return 'overwrite'
        elif clicked == version_btn:
            return 'new_version'
        else:
            return 'cancel'

    def _get_next_version_name(
        self, base_name: str, server_files: List[Dict[str, Any]]
    ) -> str:
        """
        Generate the next version name for a GeoPackage.

        Given 'MyProject.gpkg' and server files, returns 'MyProject_v2.gpkg'.
        If 'MyProject_v2.gpkg' exists, returns 'MyProject_v3.gpkg', etc.
        """
        stem = Path(base_name).stem  # e.g. 'MyProject'

        # Strip existing _vN suffix to get the true base name
        base_match = re.match(r'^(.+?)(?:_v\d+)?$', stem)
        true_stem = base_match.group(1) if base_match else stem

        # Find the highest version number among server files
        max_version = 1  # The original file counts as v1
        pattern = re.compile(
            rf'^{re.escape(true_stem)}(?:_v(\d+))?\.gpkg$',
            re.IGNORECASE
        )

        for sf in server_files:
            server_name = sf.get('name', '') or ''
            match = pattern.match(server_name)
            if match:
                v = int(match.group(1)) if match.group(1) else 1
                max_version = max(max_version, v)

        return f"{true_stem}_v{max_version + 1}.gpkg"

    # ==================== PULL: REFRESH ====================

    def _on_refresh_clicked(self):
        """Refresh the list of GeoPackages available on the server."""
        project = self.project_manager.active_project
        if not project:
            self._show_status("No project selected. Please select a project first.", "error")
            return

        self._show_status("Fetching GeoPackages from server...", "info")
        self.refresh_button.setEnabled(False)

        try:
            self._fetch_server_geopackages()

            if not self._server_geopackages:
                self._show_status(
                    "No GeoPackages found on the server for this project.",
                    "warning"
                )
                self._populate_pull_table()
                return

            count = len(self._server_geopackages)
            self._show_status(
                f"Found {count} GeoPackage(s) on the server.",
                "success"
            )
            self._populate_pull_table()

        except Exception as e:
            self.logger.error(f"Refresh failed: {e}")
            self._show_status(f"Refresh failed: {e}", "error")
        finally:
            self.refresh_button.setEnabled(True)

    def _populate_pull_table(self):
        """Populate the pull table with server GeoPackages."""
        self.pull_table.setRowCount(0)
        self.pull_table.setRowCount(len(self._server_geopackages))

        for row, gpkg in enumerate(self._server_geopackages):
            # Checkbox
            cb = QCheckBox()
            cb.setChecked(False)
            cb.stateChanged.connect(
                lambda state, r=row: self._on_pull_checkbox_changed(r, state)
            )
            cb_widget = QWidget()
            cb_layout = QHBoxLayout(cb_widget)
            cb_layout.addWidget(cb)
            cb_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cb_layout.setContentsMargins(0, 0, 0, 0)
            self.pull_table.setCellWidget(row, 0, cb_widget)

            # Name
            name = gpkg.get('name', 'Unknown')
            name_item = QTableWidgetItem(name)
            description = gpkg.get('description', '')
            if description:
                name_item.setToolTip(description)
            self.pull_table.setItem(row, 1, name_item)

            # Size
            file_size = gpkg.get('file_size', 0) or 0
            size_item = QTableWidgetItem(self._format_file_size(file_size))
            size_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            self.pull_table.setItem(row, 2, size_item)

            # Date
            date_str = gpkg.get('date_created', '') or ''
            if date_str and len(date_str) >= 10:
                date_str = date_str[:10]  # Just the date portion
            date_item = QTableWidgetItem(date_str)
            date_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            self.pull_table.setItem(row, 3, date_item)

        self.pull_count_label.setText(
            f"{len(self._server_geopackages)} GeoPackage(s) on server"
        )
        self._update_pull_button_state()

    def _on_pull_checkbox_changed(self, row: int, state: int):
        """Handle checkbox change in pull table."""
        self._update_pull_button_state()

    def _update_pull_button_state(self):
        """Update pull button enabled state."""
        selected = 0
        for row in range(self.pull_table.rowCount()):
            cb_widget = self.pull_table.cellWidget(row, 0)
            if cb_widget:
                cb = cb_widget.findChild(QCheckBox)
                if cb and cb.isChecked():
                    selected += 1

        self.pull_button.setEnabled(selected > 0 and not self._is_downloading)

    # ==================== PULL: DOWNLOAD ====================

    def _on_pull_clicked(self):
        """Pull selected GeoPackages from the server."""
        # Gather selected server files
        selected = []
        for row in range(self.pull_table.rowCount()):
            cb_widget = self.pull_table.cellWidget(row, 0)
            if cb_widget:
                cb = cb_widget.findChild(QCheckBox)
                if cb and cb.isChecked() and row < len(self._server_geopackages):
                    selected.append(self._server_geopackages[row])

        if not selected:
            self._show_status("No GeoPackages selected.", "warning")
            return

        # Confirm
        reply = QMessageBox.question(
            self,
            "Pull GeoPackages",
            f"Download and load {len(selected)} GeoPackage(s) into the project?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._is_downloading = True
        self.pull_button.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.pull_progress.setVisible(True)
        self.pull_progress.setMaximum(len(selected))
        self.pull_progress.setValue(0)

        loaded = 0
        errors = []

        for i, server_file in enumerate(selected):
            self.pull_progress.setValue(i)
            name = server_file.get('name', 'Unknown')
            self._show_status(f"Downloading {name}...", "info")
            QApplication.processEvents()

            try:
                local_path = self._download_geopackage(server_file)
                if not local_path:
                    errors.append(f"{name}: Download failed")
                    continue

                self._show_status(f"Loading layers from {name}...", "info")
                QApplication.processEvents()

                layer_count = self._load_geopackage_layers(local_path, name)
                loaded += 1
                self.logger.info(
                    f"Loaded GeoPackage '{name}' with {layer_count} layer(s)"
                )

            except Exception as e:
                self.logger.error(f"Pull failed for {name}: {e}")
                errors.append(f"{name}: {e}")

        self.pull_progress.setValue(len(selected))

        if errors:
            self._show_status(
                f"Loaded {loaded}/{len(selected)} GeoPackage(s). "
                f"Errors: {'; '.join(errors)}",
                "warning"
            )
        else:
            self._show_status(
                f"Successfully loaded {loaded} GeoPackage(s).",
                "success"
            )

        self._is_downloading = False
        self.refresh_button.setEnabled(True)
        self._update_pull_button_state()
        QTimer.singleShot(3000, lambda: self.pull_progress.setVisible(False))

    def _download_geopackage(self, server_file: Dict[str, Any]) -> Optional[str]:
        """
        Download a GeoPackage file from the server.

        Uses the pre-signed file_url from the API response.
        Caches files at: {QGIS_profile}/geodb_cache/geopackages/pf_{id}.gpkg

        Returns the local file path, or None on failure.
        """
        file_id = server_file.get('id')
        file_url = server_file.get('file_url', '')
        name = server_file.get('name', f'pf_{file_id}.gpkg')

        if not file_url:
            self.logger.error(f"No file_url for GeoPackage {name}")
            return None

        # Check cache
        cache_path = self._cache_dir / f"pf_{file_id}.gpkg"
        if cache_path.exists():
            self.logger.info(f"Using cached GeoPackage: {cache_path}")
            return str(cache_path)

        # Resolve relative URLs for local dev
        if not urlparse(file_url).scheme:
            base_url = self._get_base_url()
            if base_url:
                from urllib.parse import urljoin
                file_url = urljoin(base_url, file_url)

        # Download
        request = QNetworkRequest(QUrl(file_url))
        request.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy
        )

        blocking_request = QgsBlockingNetworkRequest()
        error_code = blocking_request.get(request, forceRefresh=True)

        if error_code != QgsBlockingNetworkRequest.NoError:
            error_msg = blocking_request.errorMessage()
            self.logger.error(f"Download failed for {name}: {error_msg}")
            return None

        reply = blocking_request.reply()
        data = reply.content()

        if not data or len(data) == 0:
            self.logger.error(f"Empty response for {name}")
            return None

        # Write to cache
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_path, 'wb') as f:
            f.write(bytes(data))

        self.logger.info(f"Downloaded GeoPackage to {cache_path} ({len(data)} bytes)")
        return str(cache_path)

    def _load_geopackage_layers(self, gpkg_path: str, display_name: str) -> int:
        """
        Load all vector layers from a GeoPackage into the QGIS project.

        Styles saved in the GeoPackage's layer_styles table are automatically
        applied by QGIS when loading via the OGR provider.

        Returns the number of layers loaded.
        """
        try:
            from osgeo import ogr
        except ImportError:
            self.logger.error("GDAL/OGR not available for layer enumeration")
            return 0

        ds = ogr.Open(gpkg_path)
        if not ds:
            self.logger.error(f"Cannot open GeoPackage: {gpkg_path}")
            return 0

        layer_count = ds.GetLayerCount()
        loaded = 0
        qgs_project = QgsProject.instance()

        # Strip .gpkg extension for display prefix
        prefix = Path(display_name).stem

        for i in range(layer_count):
            ogr_layer = ds.GetLayerByIndex(i)
            if not ogr_layer:
                continue

            layer_name = ogr_layer.GetName()

            # Skip internal GeoPackage tables
            if layer_name.startswith('gpkg_') or layer_name == 'layer_styles':
                continue
            # Skip geodb metadata table if present
            if layer_name == 'geodb_metadata':
                continue

            # Build the QGIS layer URI
            layer_uri = f"{gpkg_path}|layername={layer_name}"
            qgs_display_name = f"{prefix}_{layer_name}"

            qgs_layer = QgsVectorLayer(layer_uri, qgs_display_name, "ogr")
            if qgs_layer.isValid():
                qgs_project.addMapLayer(qgs_layer)
                loaded += 1
                self.logger.info(f"Loaded layer: {qgs_display_name}")
            else:
                self.logger.warning(
                    f"Invalid layer skipped: {layer_name} from {display_name}"
                )

        ds = None  # Close OGR dataset
        return loaded

    def _get_base_url(self) -> Optional[str]:
        """Get the server base URL for resolving relative file URLs."""
        try:
            endpoint = self.api_client.config.base_url
            parsed = urlparse(endpoint)
            return f"{parsed.scheme}://{parsed.netloc}"
        except Exception:
            return None

    # ==================== UTILITIES ====================

    @staticmethod
    def _format_file_size(size_bytes: int) -> str:
        """Format file size to human-readable string."""
        if size_bytes <= 0:
            return "?"
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.0f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"

    def _show_status(self, message: str, level: str = "info"):
        """Show a status message."""
        color_map = {
            "info": "#3b82f6",
            "success": "#10b981",
            "warning": "#f59e0b",
            "error": "#ef4444",
        }
        color = color_map.get(level, "#64748b")
        self.status_label.setText(message)
        self.status_label.setStyleSheet(
            f"color: {color}; font-size: 12px; padding: 4px 8px; "
            f"background-color: #f8fafc; border-radius: 4px; border: 1px solid {color};"
        )
        self.status_label.setVisible(True)
        self.status_message.emit(message, level)

    def set_enabled_state(self, enabled: bool):
        """Enable or disable the widget based on login/project state."""
        self.scan_button.setEnabled(enabled)
        self.refresh_button.setEnabled(enabled)
        if not enabled:
            self.push_button.setEnabled(False)
            self.pull_button.setEnabled(False)
