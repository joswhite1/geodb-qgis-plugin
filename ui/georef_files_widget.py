# -*- coding: utf-8 -*-
"""
Widget for scanning georeferenced raster layers in the current QGIS project,
comparing them against files already on the geodb.io server, and uploading
new ones as ProjectFiles.
"""
import os
from typing import Optional, List, Dict, Any

from qgis.PyQt.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QComboBox,
    QAbstractItemView, QFrame, QScrollArea, QMessageBox,
    QProgressBar, QCheckBox,
)
from qgis.PyQt.QtCore import Qt, pyqtSignal, QTimer
from qgis.PyQt.QtGui import QColor

from qgis.core import QgsProject, QgsRasterLayer

from ..utils.logger import PluginLogger
from .map_capture_widget import PROJECTFILE_CATEGORIES


# Raster file extensions that are natively georeferenced (embedded CRS/bounds)
NATIVE_GEOREF_EXTENSIONS = {'.tif', '.tiff', '.geotiff', '.img', '.hdr', '.ers', '.grd', '.ecw'}

# Image extensions that use sidecar world files for georeferencing
WORLDFILE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.bmp', '.gif'}

# World file extension mapping
WORLD_FILE_MAP = {
    '.png': '.pgw',
    '.jpg': '.jgw',
    '.jpeg': '.jgw',
    '.tif': '.tfw',
    '.tiff': '.tfw',
    '.gif': '.gfw',
    '.bmp': '.bpw',
}

# Status constants
STATUS_NEW = "New"
STATUS_ON_SERVER = "On Server"
STATUS_UPLOADING = "Uploading..."
STATUS_UPLOADED = "Uploaded"
STATUS_ERROR = "Error"
STATUS_SKIPPED = "Skipped (no file)"


class GeorefFilesWidget(QWidget):
    """Widget for discovering local georeferenced rasters and pushing them to the server."""

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
        self._local_rasters: List[Dict[str, Any]] = []
        self._server_files: List[Dict[str, Any]] = []
        self._is_uploading = False

        self._build_ui()

    def _build_ui(self):
        """Build the widget UI."""
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(10)
        layout.setContentsMargins(12, 12, 12, 12)

        # Title
        title = QLabel("Upload Georeferenced Files")
        title.setStyleSheet("font-size: 16px; font-weight: bold; color: #1e293b;")
        layout.addWidget(title)

        subtitle = QLabel(
            "Scan your QGIS project for georeferenced raster layers (GeoTIFFs, "
            "world-file images, etc.) and upload them to the server. Files already "
            "on the server are detected automatically."
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: #64748b; font-size: 12px; margin-bottom: 4px;")
        layout.addWidget(subtitle)

        # --- Toolbar ---
        toolbar = QHBoxLayout()

        self.scan_button = QPushButton("Scan Project Layers")
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
        toolbar.addWidget(self.scan_button)

        # Default category for uploads
        toolbar.addWidget(QLabel("Default category:"))
        self.category_combo = QComboBox()
        for code, label in PROJECTFILE_CATEGORIES:
            self.category_combo.addItem(label, code)
        # Default to 'GL' (Geology Raster)
        gl_index = next(
            (i for i, (code, _) in enumerate(PROJECTFILE_CATEGORIES) if code == 'GL'),
            0
        )
        self.category_combo.setCurrentIndex(gl_index)
        self.category_combo.setMaximumWidth(220)
        toolbar.addWidget(self.category_combo)

        toolbar.addStretch()
        layout.addLayout(toolbar)

        # --- Table ---
        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels([
            "", "Layer Name", "CRS", "Size", "Category", "Status"
        ])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setStyleSheet("""
            QTableWidget {
                border: 1px solid #e2e8f0;
                border-radius: 6px;
                gridline-color: #f1f5f9;
            }
            QTableWidget::item {
                padding: 4px 8px;
            }
            QHeaderView::section {
                background-color: #f8fafc;
                border: none;
                border-bottom: 2px solid #e2e8f0;
                padding: 6px 8px;
                font-weight: bold;
                color: #475569;
            }
        """)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 30)  # Checkbox column
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)

        self.table.setMinimumHeight(200)
        layout.addWidget(self.table)

        # --- Select all / none ---
        select_row = QHBoxLayout()
        self.select_all_btn = QPushButton("Select All New")
        self.select_all_btn.setStyleSheet("padding: 4px 10px; font-size: 11px;")
        self.select_all_btn.clicked.connect(self._select_all_new)
        select_row.addWidget(self.select_all_btn)

        self.select_none_btn = QPushButton("Select None")
        self.select_none_btn.setStyleSheet("padding: 4px 10px; font-size: 11px;")
        self.select_none_btn.clicked.connect(self._select_none)
        select_row.addWidget(self.select_none_btn)

        select_row.addStretch()

        self.count_label = QLabel("")
        self.count_label.setStyleSheet("color: #64748b; font-size: 11px;")
        select_row.addWidget(self.count_label)

        layout.addLayout(select_row)

        # --- Upload button + progress ---
        self.upload_button = QPushButton("Upload Selected to Server")
        self.upload_button.setEnabled(False)
        self.upload_button.setStyleSheet("""
            QPushButton {
                background-color: #10b981;
                color: white;
                font-weight: bold;
                padding: 10px 20px;
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
        self.upload_button.clicked.connect(self._on_upload_clicked)
        layout.addWidget(self.upload_button)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setStyleSheet("""
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
        layout.addWidget(self.progress_bar)

        # Status label
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setVisible(False)
        layout.addWidget(self.status_label)

        layout.addStretch()

        scroll.setWidget(content)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(scroll)

    # ==================== SCANNING ====================

    def _on_scan_clicked(self):
        """Scan the QGIS project for georeferenced raster layers and compare with server."""
        project = self.project_manager.active_project
        if not project:
            self._show_status("No project selected. Please select a project first.", "error")
            return

        self._show_status("Scanning project layers...", "info")
        self.scan_button.setEnabled(False)

        try:
            # Step 1: Discover local georeferenced rasters
            self._local_rasters = self._discover_local_rasters()

            if not self._local_rasters:
                self._show_status(
                    "No georeferenced raster layers found in the current QGIS project.",
                    "warning"
                )
                self._populate_table()
                self.scan_button.setEnabled(True)
                return

            # Step 2: Fetch server file list for comparison
            self._fetch_server_files(project)

            # Step 3: Compare and mark statuses
            self._compare_with_server()

            # Step 4: Populate the table
            self._populate_table()

            new_count = sum(1 for r in self._local_rasters if r['status'] == STATUS_NEW)
            total = len(self._local_rasters)
            self._show_status(
                f"Found {total} georeferenced layer(s), {new_count} new (not on server).",
                "success"
            )

        except Exception as e:
            self.logger.error(f"Scan failed: {e}")
            self._show_status(f"Scan failed: {e}", "error")

        finally:
            self.scan_button.setEnabled(True)

    def _discover_local_rasters(self) -> List[Dict[str, Any]]:
        """
        Discover all georeferenced raster layers in the current QGIS project.

        Returns list of dicts with layer metadata.
        """
        rasters = []
        qgs_project = QgsProject.instance()

        for layer_id, layer in qgs_project.mapLayers().items():
            if not isinstance(layer, QgsRasterLayer):
                continue

            # Skip XYZ/WMS/remote tile layers - we only want local files
            provider = layer.providerType()
            if provider in ('wms', 'arcgismapserver', 'arcgisfeatureserver'):
                continue

            source_path = layer.source()

            # Skip non-file sources (e.g. memory, network)
            if not source_path or not os.path.isfile(source_path):
                continue

            # Get file extension
            _, ext = os.path.splitext(source_path)
            ext = ext.lower()

            # Check if this is a georeferenced file type
            is_native_georef = ext in NATIVE_GEOREF_EXTENSIONS
            is_worldfile_georef = False

            if ext in WORLDFILE_EXTENSIONS:
                # Check if a world file exists alongside
                world_ext = WORLD_FILE_MAP.get(ext, '.wld')
                world_path = os.path.splitext(source_path)[0] + world_ext
                # Also check generic .wld
                wld_path = os.path.splitext(source_path)[0] + '.wld'
                if os.path.isfile(world_path) or os.path.isfile(wld_path):
                    is_worldfile_georef = True

            # Also accept any raster with a valid CRS and valid extent
            # (QGIS may have loaded it with embedded georeferencing we didn't detect)
            has_valid_crs = layer.crs().isValid()
            has_valid_extent = (
                layer.extent().isFinite()
                and not layer.extent().isEmpty()
                and layer.extent().width() > 0
                and layer.extent().height() > 0
            )

            if not (is_native_georef or is_worldfile_georef or (has_valid_crs and has_valid_extent)):
                continue

            # Extract metadata
            crs = layer.crs()
            extent = layer.extent()
            try:
                auth_id = crs.authid()  # e.g. "EPSG:26911"
                epsg = int(auth_id.split(':')[1])
            except (IndexError, ValueError, AttributeError):
                epsg = None

            bounds = [
                extent.xMinimum(),
                extent.yMinimum(),
                extent.xMaximum(),
                extent.yMaximum()
            ]

            # Resolution (ground units per pixel)
            resolution = None
            if layer.width() > 0:
                resolution = extent.width() / layer.width()

            # File size
            try:
                file_size = os.path.getsize(source_path)
            except OSError:
                file_size = 0

            rasters.append({
                'layer_id': layer_id,
                'layer_name': layer.name(),
                'source_path': source_path,
                'file_name': os.path.basename(source_path),
                'extension': ext,
                'epsg': epsg,
                'crs_authid': crs.authid() if has_valid_crs else 'Unknown',
                'bounds': bounds,
                'resolution': resolution,
                'width': layer.width(),
                'height': layer.height(),
                'file_size': file_size,
                'is_native_georef': is_native_georef,
                'is_worldfile_georef': is_worldfile_georef,
                'status': STATUS_NEW,  # Will be updated after server comparison
                'selected': True,  # Checked by default for new files
            })

        self.logger.info(f"Discovered {len(rasters)} local georeferenced raster(s)")
        return rasters

    def _fetch_server_files(self, project):
        """Fetch the list of ProjectFiles from the server for comparison."""
        try:
            response = self.api_client.get_all_paginated(
                model_name='ProjectFile',
                project_id=project.id,
                params={},
                include_deletion_metadata=False
            )

            if isinstance(response, dict):
                self._server_files = response.get('results', [])
            else:
                self._server_files = response if response else []

            self.logger.info(f"Fetched {len(self._server_files)} server file(s)")

        except Exception as e:
            self.logger.warning(f"Failed to fetch server files: {e}")
            self._server_files = []

    def _compare_with_server(self):
        """
        Compare local rasters against server files to detect duplicates.

        Matching strategy: match by file name AND approximate bounds + EPSG.
        If file name matches and bounds/EPSG are close enough, mark as already on server.
        """
        for raster in self._local_rasters:
            raster['status'] = STATUS_NEW
            raster['selected'] = True
            raster['server_match_id'] = None

            if not os.path.isfile(raster['source_path']):
                raster['status'] = STATUS_SKIPPED
                raster['selected'] = False
                continue

            for sf in self._server_files:
                if self._is_match(raster, sf):
                    raster['status'] = STATUS_ON_SERVER
                    raster['selected'] = False
                    raster['server_match_id'] = sf.get('id')
                    break

    def _is_match(self, local: Dict[str, Any], server: Dict[str, Any]) -> bool:
        """
        Check if a local raster matches a server file.

        Matches on:
        1. File name (case-insensitive)
        2. EPSG code (if both have one)
        3. Approximate bounds overlap (within 1% tolerance)
        """
        # Name match (compare base filenames)
        local_name = local['file_name'].lower()
        server_name = (server.get('name', '') or '').lower()

        # Strip extension from server name for comparison since server 'name'
        # may or may not include the extension
        local_stem = os.path.splitext(local_name)[0]
        server_stem = os.path.splitext(server_name)[0] if '.' in server_name else server_name

        name_matches = (
            local_name == server_name
            or local_stem == server_stem
            or local_stem == server_name
            or local_name == server_stem
        )

        if not name_matches:
            return False

        # EPSG match (if both have it)
        local_epsg = local.get('epsg')
        server_epsg = server.get('epsg')
        if local_epsg and server_epsg and local_epsg != server_epsg:
            return False

        # Bounds match (approximate, within 1% of extent size)
        local_bounds = local.get('bounds')
        server_bounds = server.get('bounds')

        if local_bounds and server_bounds and len(server_bounds) == 4:
            width = abs(local_bounds[2] - local_bounds[0])
            height = abs(local_bounds[3] - local_bounds[1])
            tolerance = max(width, height) * 0.01  # 1% tolerance

            if tolerance > 0:
                for i in range(4):
                    if abs(local_bounds[i] - server_bounds[i]) > tolerance:
                        return False

        return True

    # ==================== TABLE ====================

    def _populate_table(self):
        """Populate the table with discovered rasters."""
        self.table.setRowCount(0)
        self.table.setRowCount(len(self._local_rasters))

        for row, raster in enumerate(self._local_rasters):
            # Checkbox
            cb = QCheckBox()
            cb.setChecked(raster.get('selected', False))
            cb.stateChanged.connect(lambda state, r=row: self._on_checkbox_changed(r, state))

            # Disable checkbox for files already on server
            if raster['status'] == STATUS_ON_SERVER:
                cb.setEnabled(False)

            cb_widget = QWidget()
            cb_layout = QHBoxLayout(cb_widget)
            cb_layout.addWidget(cb)
            cb_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cb_layout.setContentsMargins(0, 0, 0, 0)
            self.table.setCellWidget(row, 0, cb_widget)

            # Layer name
            name_item = QTableWidgetItem(raster['layer_name'])
            name_item.setToolTip(raster['source_path'])
            self.table.setItem(row, 1, name_item)

            # CRS
            crs_item = QTableWidgetItem(raster.get('crs_authid', 'Unknown'))
            self.table.setItem(row, 2, crs_item)

            # File size
            size_str = self._format_file_size(raster.get('file_size', 0))
            size_item = QTableWidgetItem(size_str)
            size_item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row, 3, size_item)

            # Category (editable combo per row)
            cat_combo = QComboBox()
            for code, label in PROJECTFILE_CATEGORIES:
                cat_combo.addItem(label, code)
            # Set to default category
            default_code = self.category_combo.currentData()
            default_idx = next(
                (i for i, (code, _) in enumerate(PROJECTFILE_CATEGORIES) if code == default_code),
                0
            )
            cat_combo.setCurrentIndex(default_idx)
            cat_combo.setEnabled(raster['status'] != STATUS_ON_SERVER)
            self.table.setCellWidget(row, 4, cat_combo)

            # Status
            status_item = QTableWidgetItem(raster['status'])
            if raster['status'] == STATUS_ON_SERVER:
                status_item.setForeground(QColor('#64748b'))
            elif raster['status'] == STATUS_NEW:
                status_item.setForeground(QColor('#10b981'))
            elif raster['status'] == STATUS_ERROR:
                status_item.setForeground(QColor('#ef4444'))
            elif raster['status'] == STATUS_UPLOADED:
                status_item.setForeground(QColor('#3b82f6'))
            self.table.setItem(row, 5, status_item)

        self._update_counts()

    def _on_checkbox_changed(self, row: int, state: int):
        """Handle checkbox state change in the table."""
        if row < len(self._local_rasters):
            self._local_rasters[row]['selected'] = (state == 2)  # Qt.Checked == 2
            self._update_counts()

    def _select_all_new(self):
        """Select all rows with 'New' status."""
        for row, raster in enumerate(self._local_rasters):
            if raster['status'] == STATUS_NEW:
                raster['selected'] = True
                cb_widget = self.table.cellWidget(row, 0)
                if cb_widget:
                    cb = cb_widget.findChild(QCheckBox)
                    if cb:
                        cb.setChecked(True)
        self._update_counts()

    def _select_none(self):
        """Deselect all rows."""
        for row, raster in enumerate(self._local_rasters):
            raster['selected'] = False
            cb_widget = self.table.cellWidget(row, 0)
            if cb_widget:
                cb = cb_widget.findChild(QCheckBox)
                if cb:
                    cb.setChecked(False)
        self._update_counts()

    def _update_counts(self):
        """Update the count label and upload button state."""
        selected = sum(1 for r in self._local_rasters if r.get('selected', False))
        new_count = sum(1 for r in self._local_rasters if r['status'] == STATUS_NEW)
        total = len(self._local_rasters)

        self.count_label.setText(
            f"{total} layer(s) found, {new_count} new, {selected} selected"
        )
        self.upload_button.setEnabled(selected > 0 and not self._is_uploading)

    # ==================== UPLOAD ====================

    def _on_upload_clicked(self):
        """Upload all selected rasters to the server."""
        selected = [r for r in self._local_rasters if r.get('selected', False)]

        if not selected:
            self._show_status("No files selected for upload.", "warning")
            return

        project = self.project_manager.active_project
        if not project:
            self._show_status("No project selected.", "error")
            return

        # Confirm
        reply = QMessageBox.question(
            self,
            "Upload Georeferenced Files",
            f"Upload {len(selected)} file(s) to project '{project.name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        self._is_uploading = True
        self.upload_button.setEnabled(False)
        self.scan_button.setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setMaximum(len(selected))
        self.progress_bar.setValue(0)

        uploaded = 0
        errors = []

        for i, raster in enumerate(selected):
            row = self._local_rasters.index(raster)
            self._set_row_status(row, STATUS_UPLOADING)
            self.progress_bar.setValue(i)
            self._show_status(f"Uploading {raster['layer_name']}...", "info")

            # Force UI update
            from qgis.PyQt.QtWidgets import QApplication
            QApplication.processEvents()

            try:
                # Get category from the per-row combo
                cat_combo = self.table.cellWidget(row, 4)
                category = cat_combo.currentData() if cat_combo else self.category_combo.currentData()

                result = self.data_manager.upload_project_file(
                    file_path=raster['source_path'],
                    name=raster['file_name'],
                    category=category,
                    description=f"Uploaded from QGIS layer: {raster['layer_name']}",
                    is_raster=True,
                    epsg=raster.get('epsg'),
                    bounds=raster.get('bounds'),
                    resolution=raster.get('resolution'),
                )

                raster['status'] = STATUS_UPLOADED
                raster['selected'] = False
                raster['server_match_id'] = result.get('id')
                self._set_row_status(row, STATUS_UPLOADED)
                uploaded += 1
                self.upload_completed.emit(result)

            except Exception as e:
                self.logger.error(f"Upload failed for {raster['layer_name']}: {e}")
                raster['status'] = STATUS_ERROR
                self._set_row_status(row, STATUS_ERROR, str(e))
                errors.append(f"{raster['layer_name']}: {e}")

        self.progress_bar.setValue(len(selected))

        # Report results
        if errors:
            self._show_status(
                f"Uploaded {uploaded}/{len(selected)} file(s). "
                f"{len(errors)} error(s): {'; '.join(errors)}",
                "warning"
            )
        else:
            self._show_status(
                f"Successfully uploaded {uploaded} file(s).",
                "success"
            )

        self._is_uploading = False
        self.scan_button.setEnabled(True)
        self._update_counts()

        # Hide progress bar after a short delay
        QTimer.singleShot(3000, lambda: self.progress_bar.setVisible(False))

    def _set_row_status(self, row: int, status: str, tooltip: str = ""):
        """Update the status cell for a given row."""
        if row >= self.table.rowCount():
            return

        status_item = self.table.item(row, 5)
        if not status_item:
            status_item = QTableWidgetItem()
            self.table.setItem(row, 5, status_item)

        status_item.setText(status)
        if tooltip:
            status_item.setToolTip(tooltip)

        color_map = {
            STATUS_NEW: '#10b981',
            STATUS_ON_SERVER: '#64748b',
            STATUS_UPLOADING: '#f59e0b',
            STATUS_UPLOADED: '#3b82f6',
            STATUS_ERROR: '#ef4444',
            STATUS_SKIPPED: '#94a3b8',
        }
        status_item.setForeground(QColor(color_map.get(status, '#475569')))

        # Disable checkbox for uploaded/on-server rows
        if status in (STATUS_UPLOADED, STATUS_ON_SERVER):
            cb_widget = self.table.cellWidget(row, 0)
            if cb_widget:
                cb = cb_widget.findChild(QCheckBox)
                if cb:
                    cb.setChecked(False)
                    cb.setEnabled(False)

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
        if not enabled:
            self.upload_button.setEnabled(False)
