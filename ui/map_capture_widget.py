# -*- coding: utf-8 -*-
"""
Map Capture widget for capturing the QGIS map canvas and uploading
as a georeferenced ProjectFile to the geodb.io server.
"""
import os
import tempfile
from datetime import datetime
from typing import Optional

from qgis.PyQt.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QPushButton, QComboBox,
    QLineEdit, QTextEdit, QGroupBox, QFormLayout, QMessageBox,
    QFrame, QScrollArea
)
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QPixmap, QImage

from ..utils.logger import PluginLogger
from ..utils.compat import QFrame_NoFrame


# ProjectFile category choices (matching server model_variables.projectfile_choices)
PROJECTFILE_CATEGORIES = [
    ('DM', 'DEM (Digital Elevation Model)'),
    ('MG', 'Magnetics Raster'),
    ('GV', 'Gravity Raster'),
    ('EM', 'Electromagnetics Raster'),
    ('RD', 'Radiometrics Raster'),
    ('IP', 'Induced Polarization Raster'),
    ('RS', 'Resistivity Raster'),
    ('GL', 'Geology Raster'),
    ('ST', 'Satellite Imagery'),
    ('TP', 'Topographic Imagery'),
    ('AR', 'Aerial Photo/Orthophoto'),
    ('TX', '3D Texture (Clipped)'),
    ('GP', 'GeoPackage'),
    ('LF', 'Leapfrog Viewer File'),
    ('3D', '3D Model'),
    ('ZP', 'Zip Archive'),
    ('OT', 'Other'),
]


class MapCaptureWidget(QWidget):
    """Widget for capturing the QGIS map canvas and uploading to geodb.io."""

    # Signals
    status_message = pyqtSignal(str, str)  # message, level
    upload_completed = pyqtSignal(dict)  # server response

    def __init__(self, data_manager, project_manager, parent=None):
        super().__init__(parent)
        self.data_manager = data_manager
        self.project_manager = project_manager
        self.logger = PluginLogger.get_logger()

        # State
        self._captured_file_path: Optional[str] = None
        self._captured_epsg: Optional[int] = None
        self._captured_bounds: Optional[list] = None
        self._captured_resolution: Optional[float] = None
        self._captured_width: Optional[int] = None
        self._captured_height: Optional[int] = None

        self._build_ui()

    def _build_ui(self):
        """Build the widget UI."""
        # Use a scroll area for the whole widget
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame_NoFrame)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(12)
        layout.setContentsMargins(12, 12, 12, 12)

        # Title
        title = QLabel("Map Capture")
        title.setStyleSheet("font-size: 16px; font-weight: bold; color: #1e293b;")
        layout.addWidget(title)

        subtitle = QLabel(
            "Capture the current map view and upload it as a georeferenced "
            "raster to the server. The server will automatically generate "
            "tiles for mobile and web viewing."
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet("color: #64748b; font-size: 12px; margin-bottom: 8px;")
        layout.addWidget(subtitle)

        # --- Capture Section ---
        capture_group = QGroupBox("1. Capture Map View")
        capture_layout = QVBoxLayout()

        self.capture_button = QPushButton("Capture Current View")
        self.capture_button.setStyleSheet("""
            QPushButton {
                background-color: #3b82f6;
                color: white;
                font-weight: bold;
                padding: 10px 20px;
                border-radius: 6px;
                font-size: 13px;
            }
            QPushButton:hover { background-color: #2563eb; }
            QPushButton:pressed { background-color: #1d4ed8; }
        """)
        self.capture_button.clicked.connect(self._on_capture_clicked)
        capture_layout.addWidget(self.capture_button)

        # Preview area
        self.preview_label = QLabel("No capture yet")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumHeight(200)
        self.preview_label.setMaximumHeight(300)
        self.preview_label.setStyleSheet(
            "background-color: #f1f5f9; border: 2px dashed #cbd5e1; "
            "border-radius: 8px; color: #94a3b8; font-size: 13px;"
        )
        capture_layout.addWidget(self.preview_label)

        # Metadata display
        self.metadata_label = QLabel("")
        self.metadata_label.setWordWrap(True)
        self.metadata_label.setStyleSheet(
            "color: #475569; font-size: 11px; padding: 4px 8px; "
            "background-color: #f8fafc; border-radius: 4px;"
        )
        self.metadata_label.setVisible(False)
        capture_layout.addWidget(self.metadata_label)

        capture_group.setLayout(capture_layout)
        layout.addWidget(capture_group)

        # --- Upload Section ---
        upload_group = QGroupBox("2. Upload Settings")
        upload_form = QFormLayout()

        # Name field
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("e.g., Geology Map - North Zone")
        upload_form.addRow("Name:", self.name_edit)

        # Category dropdown
        self.category_combo = QComboBox()
        for code, label in PROJECTFILE_CATEGORIES:
            self.category_combo.addItem(label, code)
        # Default to 'GL' (Geology Raster)
        gl_index = next(
            (i for i, (code, _) in enumerate(PROJECTFILE_CATEGORIES) if code == 'GL'),
            0
        )
        self.category_combo.setCurrentIndex(gl_index)
        upload_form.addRow("Category:", self.category_combo)

        # Description
        self.description_edit = QTextEdit()
        self.description_edit.setPlaceholderText("Optional description...")
        self.description_edit.setMaximumHeight(80)
        upload_form.addRow("Description:", self.description_edit)

        upload_group.setLayout(upload_form)
        layout.addWidget(upload_group)

        # Upload button
        self.upload_button = QPushButton("Upload to Server")
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

        # Status label
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setVisible(False)
        layout.addWidget(self.status_label)

        layout.addStretch()

        scroll.setWidget(content)

        # Main layout wrapping the scroll area
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(scroll)

    def _on_capture_clicked(self):
        """Capture the current QGIS map canvas."""
        try:
            from qgis.utils import iface
        except ImportError:
            self._show_status("Could not access QGIS interface.", "error")
            return

        canvas = iface.mapCanvas()
        if canvas is None:
            self._show_status("No map canvas available.", "error")
            return

        # Get map settings
        map_settings = canvas.mapSettings()
        extent = map_settings.extent()
        crs = map_settings.destinationCrs()

        # Extract EPSG code
        auth_id = crs.authid()  # e.g. "EPSG:26911"
        try:
            epsg = int(auth_id.split(':')[1])
        except (IndexError, ValueError):
            self._show_status(
                f"Could not determine EPSG code from CRS: {auth_id}. "
                "Please set a valid CRS for the project.",
                "error"
            )
            return

        # Compute bounds
        bounds = [
            extent.xMinimum(),
            extent.yMinimum(),
            extent.xMaximum(),
            extent.yMaximum()
        ]

        # Save canvas to temp PNG
        temp_dir = tempfile.gettempdir()
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        file_name = f"geodb_map_capture_{timestamp}.png"
        file_path = os.path.join(temp_dir, file_name)

        # Use QgsMapCanvas.saveAsImage() to render
        canvas.saveAsImage(file_path)

        if not os.path.exists(file_path):
            self._show_status("Failed to save map canvas image.", "error")
            return

        # Read actual image dimensions from the saved file
        # This accounts for HiDPI/Retina displays where saveAsImage() produces
        # an image larger than the logical canvas.width()/height()
        saved_image = QImage(file_path)
        if saved_image.isNull():
            self._show_status("Failed to read saved image.", "error")
            return

        width_px = saved_image.width()
        height_px = saved_image.height()

        # Compute resolution using actual image dimensions
        resolution = (extent.xMaximum() - extent.xMinimum()) / width_px

        # Store capture state
        self._captured_file_path = file_path
        self._captured_epsg = epsg
        self._captured_bounds = bounds
        self._captured_resolution = resolution
        self._captured_width = width_px
        self._captured_height = height_px

        # Show preview
        pixmap = QPixmap(file_path)
        if not pixmap.isNull():
            scaled = pixmap.scaled(
                self.preview_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            self.preview_label.setPixmap(scaled)

        # Show metadata
        file_size = os.path.getsize(file_path)
        if file_size > 1024 * 1024:
            size_str = f"{file_size / (1024 * 1024):.1f} MB"
        else:
            size_str = f"{file_size / 1024:.0f} KB"

        self.metadata_label.setText(
            f"<b>Size:</b> {width_px} x {height_px} px ({size_str}) | "
            f"<b>CRS:</b> EPSG:{epsg} | "
            f"<b>Resolution:</b> {resolution:.4f} units/px<br>"
            f"<b>Extent:</b> [{bounds[0]:.2f}, {bounds[1]:.2f}] to "
            f"[{bounds[2]:.2f}, {bounds[3]:.2f}]"
        )
        self.metadata_label.setVisible(True)

        # Auto-populate name
        if not self.name_edit.text():
            self.name_edit.setText(
                f"Map Capture {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            )

        # Enable upload
        self.upload_button.setEnabled(True)
        self._show_status("Map captured successfully. Fill in details and upload.", "success")
        self.status_message.emit("Map canvas captured.", "info")

    def _on_upload_clicked(self):
        """Upload the captured map to the server."""
        if not self._captured_file_path or not os.path.exists(self._captured_file_path):
            self._show_status("No capture available. Please capture the map first.", "error")
            return

        name = self.name_edit.text().strip()
        if not name:
            self._show_status("Please enter a name for the file.", "error")
            return

        project = self.project_manager.active_project
        if not project:
            self._show_status("No project selected. Please select a project first.", "error")
            return

        category = self.category_combo.currentData()
        description = self.description_edit.toPlainText().strip()

        # Confirm upload
        reply = QMessageBox.question(
            self,
            "Upload Map Capture",
            f"Upload '{name}' to project '{project.name}'?\n\n"
            f"Category: {self.category_combo.currentText()}\n"
            f"CRS: EPSG:{self._captured_epsg}\n"
            f"Size: {self._captured_width} x {self._captured_height} px",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply != QMessageBox.StandardButton.Yes:
            return

        # Disable UI during upload
        self.upload_button.setEnabled(False)
        self.capture_button.setEnabled(False)
        self._show_status("Uploading...", "info")

        try:
            result = self.data_manager.upload_project_file(
                file_path=self._captured_file_path,
                name=name,
                category=category,
                description=description,
                is_raster=True,
                epsg=self._captured_epsg,
                bounds=self._captured_bounds,
                resolution=self._captured_resolution,
                progress_callback=self._on_upload_progress
            )

            self._show_status("Upload successful!", "success")
            self.status_message.emit(f"Uploaded '{name}' successfully.", "success")
            self.upload_completed.emit(result)

            # Clean up temp file
            try:
                os.remove(self._captured_file_path)
            except OSError:
                pass

            # Reset state
            self._captured_file_path = None
            self.preview_label.clear()
            self.preview_label.setText("Upload complete! Capture another view.")
            self.metadata_label.setVisible(False)
            self.name_edit.clear()
            self.description_edit.clear()

        except Exception as e:
            self.logger.error(f"Upload failed: {e}")
            self._show_status(f"Upload failed: {e}", "error")
            self.status_message.emit(f"Upload failed: {e}", "error")
            self.upload_button.setEnabled(True)

        finally:
            self.capture_button.setEnabled(True)

    def _on_upload_progress(self, percent: int, message: str):
        """Handle upload progress updates."""
        self._show_status(f"{message} ({percent}%)", "info")

    def _show_status(self, message: str, level: str = "info"):
        """Show a status message below the upload button."""
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

    def set_enabled_state(self, enabled: bool):
        """Enable or disable the widget based on login/project state."""
        self.capture_button.setEnabled(enabled)
        if not enabled:
            self.upload_button.setEnabled(False)
