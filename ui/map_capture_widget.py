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
from ..utils.theme import T


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


# Output-resolution presets for the captured raster.  The value is the target
# length (in pixels) of the image's *longest* edge; the other edge is derived
# from the Web-Mercator extent's aspect ratio so the image is never distorted.
# A value of 0 means "use the current on-screen canvas size".
RESOLUTION_PRESETS = [
    ("Screen (current view)", 0),
    ("High – 2048 px", 2048),
    ("Very high – 4096 px", 4096),
    ("Maximum – 8192 px", 8192),
]

# Rough compressed-PNG size estimate for map content (imagery + vectors).
# Real output varies a lot with content; this is only for a ballpark hint.
_EST_BYTES_PER_PIXEL = 1.0


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
        title.setStyleSheet(f"font-size: 16px; font-weight: bold; color: {T.SLATE_STRONG};")
        layout.addWidget(title)

        subtitle = QLabel(
            "Capture the current map view and upload it as a georeferenced "
            "raster to the server. The server will automatically generate "
            "tiles for mobile and web viewing."
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(f"color: {T.SLATE_MUTED}; font-size: 12px; margin-bottom: 8px;")
        layout.addWidget(subtitle)

        # --- Capture Section ---
        capture_group = QGroupBox("1. Capture Map View")
        capture_layout = QVBoxLayout()

        # Resolution selector
        res_row = QFormLayout()
        self.resolution_combo = QComboBox()
        for label, value in RESOLUTION_PRESETS:
            self.resolution_combo.addItem(label, value)
        # Default to "Very high – 4096 px" for crisp web tiles.
        vh_index = next(
            (i for i, (_, v) in enumerate(RESOLUTION_PRESETS) if v == 4096),
            0
        )
        self.resolution_combo.setCurrentIndex(vh_index)
        self.resolution_combo.currentIndexChanged.connect(
            self._update_size_estimate
        )
        res_row.addRow("Resolution:", self.resolution_combo)
        capture_layout.addLayout(res_row)

        # Estimated output size hint (updates live with the canvas + selector)
        self.size_estimate_label = QLabel("")
        self.size_estimate_label.setWordWrap(True)
        self.size_estimate_label.setStyleSheet(
            f"color: {T.SLATE_MUTED}; font-size: 11px; padding: 2px 4px 6px 4px;"
        )
        capture_layout.addWidget(self.size_estimate_label)

        self.capture_button = QPushButton("Capture Current View")
        self.capture_button.setStyleSheet(f"""
            QPushButton {{
                background-color: {T.ACCENT};
                color: {T.TEXT_ON_ACCENT};
                font-weight: bold;
                padding: 10px 20px;
                border-radius: 6px;
                font-size: 13px;
            }}
            QPushButton:hover {{ background-color: {T.ACCENT_HOVER}; }}
            QPushButton:pressed {{ background-color: {T.ACCENT_ACTIVE}; }}
        """)
        self.capture_button.clicked.connect(self._on_capture_clicked)
        capture_layout.addWidget(self.capture_button)

        # Preview area
        self.preview_label = QLabel("No capture yet")
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setMinimumHeight(200)
        self.preview_label.setMaximumHeight(300)
        self.preview_label.setStyleSheet(
            f"background-color: {T.SLATE_SUNKEN}; border: 2px dashed {T.SLATE_BORDER}; "
            f"border-radius: 8px; color: {T.SLATE_FAINT}; font-size: 13px;"
        )
        capture_layout.addWidget(self.preview_label)

        # Metadata display
        self.metadata_label = QLabel("")
        self.metadata_label.setWordWrap(True)
        self.metadata_label.setStyleSheet(
            f"color: {T.SLATE_TEXT}; font-size: 11px; padding: 4px 8px; "
            f"background-color: {T.SLATE_SURFACE}; border-radius: 4px;"
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
        self.upload_button.setStyleSheet(f"""
            QPushButton {{
                background-color: {T.SUCCESS};
                color: {T.TEXT_ON_ACCENT};
                font-weight: bold;
                padding: 10px 20px;
                border-radius: 6px;
                font-size: 13px;
            }}
            QPushButton:hover {{ background-color: {T.SUCCESS}; }}
            QPushButton:pressed {{ background-color: {T.SUCCESS_TEXT}; }}
            QPushButton:disabled {{
                background-color: {T.BORDER};
                color: {T.TEXT_FAINT};
            }}
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

        # Populate the initial size estimate
        self._update_size_estimate()

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

        if not crs.isValid():
            self._show_status(
                "The project has no valid CRS. Please set a project CRS first.",
                "error"
            )
            return

        # Every capture is rendered in EPSG:3857 (Web Mercator), regardless of
        # the project CRS, with an output size whose aspect ratio matches the
        # *Web-Mercator* extent.  This is the single correct path: it keeps the
        # reported bounds, the pixel grid, and the rendered content all in the
        # same projection and the same aspect ratio, so the server's affine
        # georeferencing (from_bounds) places it without distortion at any
        # project CRS / latitude.  Mismatched aspect ratios were the source of
        # the latitude-dependent stretch on WGS84 / Web-Mercator projects.
        try:
            target_long_edge = self._target_long_edge(canvas)
            result_3857 = self._render_in_3857(extent, crs, target_long_edge)
        except Exception as exc:
            self.logger.error(f"Map capture render failed: {exc}")
            self._show_status(f"Could not render the map view: {exc}", "error")
            return

        if not result_3857:
            self._show_status("Failed to render the map view.", "error")
            return

        file_path, epsg, bounds, resolution, width_px, height_px = result_3857

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
                pixel_width=self._captured_width,
                pixel_height=self._captured_height,
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

    def _extent_3857(self, native_extent, native_crs):
        """Transform a native-CRS extent (a QgsRectangle) to EPSG:3857.

        Returns the transformed QgsRectangle, or None if the transform fails.
        """
        from qgis.core import (
            QgsCoordinateReferenceSystem,
            QgsCoordinateTransform,
            QgsProject,
        )
        crs_3857 = QgsCoordinateReferenceSystem('EPSG:3857')
        if not crs_3857.isValid():
            return None
        xform = QgsCoordinateTransform(
            native_crs, crs_3857, QgsProject.instance()
        )
        return xform.transformBoundingBox(native_extent)

    @staticmethod
    def _output_size_for(extent_3857, long_edge):
        """Pick an (width, height) for `long_edge` that matches the aspect
        ratio of the Web-Mercator extent.  Keeping the image aspect ratio equal
        to the bounds aspect ratio is what prevents distortion.
        """
        ext_w = extent_3857.xMaximum() - extent_3857.xMinimum()
        ext_h = extent_3857.yMaximum() - extent_3857.yMinimum()
        if ext_w <= 0 or ext_h <= 0:
            return None
        aspect = ext_w / ext_h  # >1 = wider than tall
        if aspect >= 1.0:
            width_px = int(round(long_edge))
            height_px = max(1, int(round(long_edge / aspect)))
        else:
            height_px = int(round(long_edge))
            width_px = max(1, int(round(long_edge * aspect)))
        return width_px, height_px

    def _target_long_edge(self, canvas):
        """Resolve the selected resolution preset to a longest-edge pixel
        count.  A preset value of 0 means "use the current on-screen size".
        """
        value = self.resolution_combo.currentData()
        if value and value > 0:
            return int(value)
        # "Screen" preset: longest edge of the canvas, scaled for HiDPI.
        try:
            dpr = canvas.devicePixelRatioF()
        except Exception:
            dpr = 1.0
        return max(1, int(round(max(canvas.width(), canvas.height()) * dpr)))

    def _render_in_3857(self, native_extent, native_crs, target_long_edge):
        """Render the current map layers in EPSG:3857 (Web Mercator) at a size
        whose aspect ratio matches the Web-Mercator extent.

        Returns (file_path, epsg, bounds, resolution, width, height) on
        success, or None if the render fails.
        """
        from qgis.core import (
            QgsMapSettings,
            QgsMapRendererCustomPainterJob,
            QgsCoordinateReferenceSystem,
        )
        from qgis.PyQt.QtGui import QImage, QPainter
        from qgis.PyQt.QtCore import QSize
        from qgis.utils import iface

        crs_3857 = QgsCoordinateReferenceSystem('EPSG:3857')

        extent_3857 = self._extent_3857(native_extent, native_crs)
        if extent_3857 is None:
            return None

        size = self._output_size_for(extent_3857, target_long_edge)
        if size is None:
            return None
        width_px, height_px = size

        # Configure map settings for the 3857 render.  Because the output-size
        # aspect ratio matches the extent aspect ratio, QgsMapSettings does NOT
        # expand the extent, so visibleExtent() == extent_3857 and the reported
        # bounds describe the rendered pixels exactly.
        settings = QgsMapSettings()
        settings.setDestinationCrs(crs_3857)
        settings.setExtent(extent_3857)
        settings.setOutputSize(QSize(width_px, height_px))
        settings.setLayers(iface.mapCanvas().layers())
        settings.setBackgroundColor(iface.mapCanvas().canvasColor())

        # Render to a QImage
        image = QImage(
            QSize(width_px, height_px),
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        image.fill(0)
        painter = QPainter(image)
        job = QgsMapRendererCustomPainterJob(settings, painter)
        job.start()
        job.waitForFinished()
        painter.end()

        # Save to temp file
        temp_dir = tempfile.gettempdir()
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        file_path = os.path.join(
            temp_dir, f"geodb_map_capture_3857_{timestamp}.png"
        )
        if not image.save(file_path, "PNG"):
            self.logger.warning("Failed to save 3857 re-rendered image")
            return None

        # Report the *actual* rendered extent.  With matched aspect ratios this
        # equals extent_3857, but reading it back is robust if QGIS adjusts it.
        rendered = settings.visibleExtent()
        bounds_3857 = [
            rendered.xMinimum(),
            rendered.yMinimum(),
            rendered.xMaximum(),
            rendered.yMaximum(),
        ]
        resolution_3857 = (
            (rendered.xMaximum() - rendered.xMinimum()) / width_px
        )

        self.logger.info(
            f"Rendered capture in EPSG:3857 "
            f"({width_px}x{height_px}, res={resolution_3857:.4f})"
        )
        return file_path, 3857, bounds_3857, resolution_3857, width_px, height_px

    def _update_size_estimate(self):
        """Update the live 'estimated output size' hint from the current canvas
        extent and the selected resolution preset."""
        if not hasattr(self, 'size_estimate_label'):
            return
        try:
            from qgis.utils import iface
            canvas = iface.mapCanvas()
            if canvas is None:
                raise RuntimeError("no canvas")
            map_settings = canvas.mapSettings()
            extent = map_settings.extent()
            crs = map_settings.destinationCrs()
            extent_3857 = self._extent_3857(extent, crs)
            long_edge = self._target_long_edge(canvas)
            size = self._output_size_for(extent_3857, long_edge) \
                if extent_3857 is not None else None
            if not size:
                raise RuntimeError("no size")
            width_px, height_px = size
            est_bytes = width_px * height_px * _EST_BYTES_PER_PIXEL
            if est_bytes > 1024 * 1024:
                est_str = f"~{est_bytes / (1024 * 1024):.0f} MB"
            else:
                est_str = f"~{est_bytes / 1024:.0f} KB"
            self.size_estimate_label.setText(
                f"Output: {width_px} × {height_px} px "
                f"({est_str} estimated)"
            )
        except Exception:
            self.size_estimate_label.setText(
                "Output size shown after a project/map is open."
            )

    def _show_status(self, message: str, level: str = "info"):
        """Show a status message below the upload button."""
        color_map = {
            "info": T.ACCENT,
            "success": T.SUCCESS,
            "warning": T.WARNING,
            "error": T.DANGER,
        }
        color = color_map.get(level, T.SLATE_MUTED)
        self.status_label.setText(message)
        self.status_label.setStyleSheet(
            f"color: {color}; font-size: 12px; padding: 4px 8px; "
            f"background-color: {T.SLATE_SURFACE}; border-radius: 4px; border: 1px solid {color};"
        )
        self.status_label.setVisible(True)

    def set_enabled_state(self, enabled: bool):
        """Enable or disable the widget based on login/project state."""
        self.capture_button.setEnabled(enabled)
        if not enabled:
            self.upload_button.setEnabled(False)
        if enabled:
            self._update_size_estimate()
