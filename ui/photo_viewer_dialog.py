# -*- coding: utf-8 -*-
"""
Photo Viewer Dialog for FieldNote photos.

Provides a Qt-based image viewer that can display photos from URLs
with caching support and navigation between multiple photos.
"""

import os
from typing import Optional, List, Dict, Any
from pathlib import Path

from qgis.PyQt.QtCore import Qt, QUrl, QSize, QThread, pyqtSignal, QByteArray
from qgis.PyQt.QtGui import QPixmap, QImage, QIcon
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QScrollArea, QSizePolicy, QWidget
)
from qgis.PyQt.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply

from ..utils.logger import PluginLogger


class PhotoLoader(QThread):
    """Background thread for loading photos from URLs."""

    loaded = pyqtSignal(QPixmap, str)  # pixmap, url
    error = pyqtSignal(str, str)  # error_message, url
    progress = pyqtSignal(int)  # percent

    def __init__(self, url: str, cache_dir: Optional[Path] = None):
        super().__init__()
        self.url = url
        self.cache_dir = cache_dir
        self._cancelled = False

    def run(self):
        """Load photo from URL or cache."""
        try:
            # Check cache first
            if self.cache_dir:
                cache_file = self._get_cache_path()
                if cache_file.exists():
                    pixmap = QPixmap(str(cache_file))
                    if not pixmap.isNull():
                        self.loaded.emit(pixmap, self.url)
                        return

            # Download from URL
            import urllib.request
            import ssl

            # Create SSL context that doesn't verify certificates (for development)
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

            request = urllib.request.Request(
                self.url,
                headers={'User-Agent': 'QGIS-GeodbPlugin/2.0'}
            )

            with urllib.request.urlopen(request, context=ctx, timeout=30) as response:
                data = response.read()

            if self._cancelled:
                return

            # Create pixmap from data
            image = QImage()
            if image.loadFromData(QByteArray(data)):
                pixmap = QPixmap.fromImage(image)

                # Save to cache
                if self.cache_dir and not pixmap.isNull():
                    cache_file = self._get_cache_path()
                    cache_file.parent.mkdir(parents=True, exist_ok=True)
                    pixmap.save(str(cache_file), 'PNG')

                self.loaded.emit(pixmap, self.url)
            else:
                self.error.emit("Failed to decode image data", self.url)

        except Exception as e:
            if not self._cancelled:
                self.error.emit(str(e), self.url)

    def _get_cache_path(self) -> Path:
        """Get cache file path for URL."""
        import hashlib
        url_hash = hashlib.md5(self.url.encode()).hexdigest()
        return self.cache_dir / f"{url_hash}.png"

    def cancel(self):
        """Cancel the loading operation."""
        self._cancelled = True


class PhotoViewerDialog(QDialog):
    """
    Dialog for viewing FieldNote photos with navigation.

    Features:
    - Displays photos from URLs with loading indicator
    - Caches photos locally for faster subsequent views
    - Navigation between multiple photos
    - Zoom to fit / actual size toggle
    - Photo metadata display
    """

    def __init__(
        self,
        parent=None,
        photos: Optional[List[Dict[str, Any]]] = None,
        initial_index: int = 0
    ):
        """
        Initialize the photo viewer dialog.

        Args:
            parent: Parent widget
            photos: List of photo dictionaries with 'image_url', 'thumbnail_url',
                   'fieldnote_name', 'description', etc.
            initial_index: Index of photo to display first
        """
        super().__init__(parent)
        self.logger = PluginLogger.get_logger()

        self.photos = photos or []
        self.current_index = initial_index
        self.current_pixmap: Optional[QPixmap] = None
        self.loader: Optional[PhotoLoader] = None
        self.fit_to_window = True

        # Setup cache directory
        self.cache_dir = self._get_cache_dir()

        self._setup_ui()
        self._connect_signals()

        # Load initial photo
        if self.photos:
            self._load_photo(self.current_index)

    def _get_cache_dir(self) -> Path:
        """Get the photo cache directory."""
        from qgis.core import QgsApplication
        settings_dir = Path(QgsApplication.qgisSettingsDirPath())
        cache_dir = settings_dir / 'geodb_cache' / 'photos'
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir

    def _setup_ui(self):
        """Setup the dialog UI."""
        self.setWindowTitle("Photo Viewer")
        self.setMinimumSize(800, 600)
        self.resize(1000, 750)

        # Main layout
        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.setContentsMargins(8, 8, 8, 8)

        # Info bar at top
        self.info_bar = QLabel()
        self.info_bar.setStyleSheet("""
            QLabel {
                background-color: #f0f0f0;
                padding: 8px;
                border-radius: 4px;
                color: #333;
            }
        """)
        layout.addWidget(self.info_bar)

        # Scroll area for image
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setAlignment(Qt.AlignCenter)
        self.scroll_area.setStyleSheet("QScrollArea { background-color: #2d2d2d; }")

        # Image label
        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setStyleSheet("QLabel { background-color: #2d2d2d; }")
        self.image_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.scroll_area.setWidget(self.image_label)
        layout.addWidget(self.scroll_area, 1)

        # Progress bar (hidden by default)
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("Loading photo...")
        layout.addWidget(self.progress_bar)

        # Navigation bar
        nav_layout = QHBoxLayout()

        # Previous button
        self.prev_button = QPushButton("◀ Previous")
        self.prev_button.setEnabled(False)
        nav_layout.addWidget(self.prev_button)

        # Page indicator
        self.page_label = QLabel("0 / 0")
        self.page_label.setAlignment(Qt.AlignCenter)
        self.page_label.setStyleSheet("font-weight: bold;")
        nav_layout.addWidget(self.page_label, 1)

        # Next button
        self.next_button = QPushButton("Next ▶")
        self.next_button.setEnabled(False)
        nav_layout.addWidget(self.next_button)

        nav_widget = QWidget()
        nav_widget.setLayout(nav_layout)
        layout.addWidget(nav_widget)

        # Control bar
        control_layout = QHBoxLayout()

        # Zoom toggle
        self.zoom_button = QPushButton("Actual Size")
        self.zoom_button.setToolTip("Toggle between fit-to-window and actual size")
        control_layout.addWidget(self.zoom_button)

        # Open in browser button
        self.browser_button = QPushButton("Open in Browser")
        self.browser_button.setToolTip("Open full-resolution image in web browser")
        control_layout.addWidget(self.browser_button)

        control_layout.addStretch()

        # Close button
        self.close_button = QPushButton("Close")
        control_layout.addWidget(self.close_button)

        control_widget = QWidget()
        control_widget.setLayout(control_layout)
        layout.addWidget(control_widget)

        # Update navigation state
        self._update_navigation()

    def _connect_signals(self):
        """Connect UI signals."""
        self.prev_button.clicked.connect(self._show_previous)
        self.next_button.clicked.connect(self._show_next)
        self.zoom_button.clicked.connect(self._toggle_zoom)
        self.browser_button.clicked.connect(self._open_in_browser)
        self.close_button.clicked.connect(self.close)

    def _update_navigation(self):
        """Update navigation button states."""
        total = len(self.photos)
        current = self.current_index + 1 if total > 0 else 0

        self.page_label.setText(f"{current} / {total}")
        self.prev_button.setEnabled(self.current_index > 0)
        self.next_button.setEnabled(self.current_index < total - 1)

    def _update_info_bar(self):
        """Update the info bar with current photo metadata."""
        if not self.photos or self.current_index >= len(self.photos):
            self.info_bar.setText("No photo selected")
            return

        photo = self.photos[self.current_index]
        fieldnote_name = photo.get('fieldnote_name', 'Unknown')
        filename = photo.get('original_filename', '')
        description = photo.get('description', '')

        info_parts = [f"<b>{fieldnote_name}</b>"]
        if filename:
            info_parts.append(f"| {filename}")
        if description:
            info_parts.append(f"<br><i>{description}</i>")

        self.info_bar.setText(" ".join(info_parts))

    def _load_photo(self, index: int):
        """Load photo at given index."""
        if index < 0 or index >= len(self.photos):
            return

        self.current_index = index
        photo = self.photos[index]
        url = photo.get('image_url', photo.get('thumbnail_url', ''))

        if not url:
            self.image_label.setText("No image URL available")
            self._update_navigation()
            self._update_info_bar()
            return

        # Show loading state
        self.image_label.setText("Loading...")
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

        # Cancel any existing loader
        if self.loader and self.loader.isRunning():
            self.loader.cancel()
            self.loader.wait()

        # Start new loader
        self.loader = PhotoLoader(url, self.cache_dir)
        self.loader.loaded.connect(self._on_photo_loaded)
        self.loader.error.connect(self._on_photo_error)
        self.loader.progress.connect(self._on_progress)
        self.loader.start()

        self._update_navigation()
        self._update_info_bar()

    def _on_photo_loaded(self, pixmap: QPixmap, url: str):
        """Handle successfully loaded photo."""
        self.progress_bar.setVisible(False)
        self.current_pixmap = pixmap

        if self.fit_to_window:
            self._display_fit_to_window()
        else:
            self._display_actual_size()

        self.logger.info(f"Photo loaded: {url}")

    def _on_photo_error(self, error: str, url: str):
        """Handle photo loading error."""
        self.progress_bar.setVisible(False)
        self.image_label.setText(f"Failed to load image:\n{error}")
        self.logger.error(f"Failed to load photo {url}: {error}")

    def _on_progress(self, percent: int):
        """Handle loading progress."""
        self.progress_bar.setValue(percent)

    def _display_fit_to_window(self):
        """Display photo scaled to fit window."""
        if not self.current_pixmap:
            return

        available_size = self.scroll_area.size() - QSize(20, 20)  # Margin
        scaled = self.current_pixmap.scaled(
            available_size,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation
        )
        self.image_label.setPixmap(scaled)
        self.image_label.adjustSize()

    def _display_actual_size(self):
        """Display photo at actual size."""
        if not self.current_pixmap:
            return

        self.image_label.setPixmap(self.current_pixmap)
        self.image_label.adjustSize()

    def _toggle_zoom(self):
        """Toggle between fit-to-window and actual size."""
        self.fit_to_window = not self.fit_to_window

        if self.fit_to_window:
            self.zoom_button.setText("Actual Size")
            self._display_fit_to_window()
        else:
            self.zoom_button.setText("Fit to Window")
            self._display_actual_size()

    def _show_previous(self):
        """Show previous photo."""
        if self.current_index > 0:
            self._load_photo(self.current_index - 1)

    def _show_next(self):
        """Show next photo."""
        if self.current_index < len(self.photos) - 1:
            self._load_photo(self.current_index + 1)

    def _open_in_browser(self):
        """Open current photo URL in web browser."""
        if not self.photos or self.current_index >= len(self.photos):
            return

        photo = self.photos[self.current_index]
        url = photo.get('image_url', '')

        if url:
            from qgis.PyQt.QtGui import QDesktopServices
            QDesktopServices.openUrl(QUrl(url))

    def resizeEvent(self, event):
        """Handle window resize."""
        super().resizeEvent(event)
        if self.fit_to_window and self.current_pixmap:
            self._display_fit_to_window()

    def closeEvent(self, event):
        """Handle dialog close."""
        # Cancel any running loader
        if self.loader and self.loader.isRunning():
            self.loader.cancel()
            self.loader.wait()
        super().closeEvent(event)

    def keyPressEvent(self, event):
        """Handle keyboard navigation."""
        if event.key() == Qt.Key_Left:
            self._show_previous()
        elif event.key() == Qt.Key_Right:
            self._show_next()
        elif event.key() == Qt.Key_Escape:
            self.close()
        elif event.key() == Qt.Key_Space:
            self._toggle_zoom()
        else:
            super().keyPressEvent(event)


def show_photo_viewer(parent, photos: List[Dict[str, Any]], initial_index: int = 0):
    """
    Convenience function to show the photo viewer dialog.

    Args:
        parent: Parent widget
        photos: List of photo dictionaries
        initial_index: Index of photo to display first

    Returns:
        PhotoViewerDialog instance
    """
    dialog = PhotoViewerDialog(parent, photos, initial_index)
    dialog.show()
    return dialog
