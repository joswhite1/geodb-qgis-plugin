# -*- coding: utf-8 -*-
"""
Storage configuration dialog for GeodbIO plugin.

Allows users to choose between memory (temporary) and GeoPackage storage
for their project data.
"""
import os
from pathlib import Path

from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGroupBox,
    QRadioButton, QLineEdit, QPushButton, QLabel,
    QFileDialog, QMessageBox, QButtonGroup, QFrame
)
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QFont

from ..managers.storage_manager import StorageManager, StorageMode
from ..utils.logger import PluginLogger


class StorageConfigDialog(QDialog):
    """
    Dialog for configuring project data storage.

    Allows choosing between:
    - Temporary (Memory) storage - data lost when QGIS closes
    - GeoPackage storage - persistent local file
    """

    # Emitted when storage is configured: (mode, geopackage_path or None)
    storage_configured = pyqtSignal(str, str)

    def __init__(
        self,
        parent=None,
        storage_manager: StorageManager = None,
        project_id: int = None,
        project_name: str = None
    ):
        """
        Initialize storage configuration dialog.

        Args:
            parent: Parent widget
            storage_manager: StorageManager instance
            project_id: Current project ID
            project_name: Current project name (for suggested filename)
        """
        super().__init__(parent)
        self.storage_manager = storage_manager or StorageManager()
        self.project_id = project_id
        self.project_name = project_name or "Project"
        self.logger = PluginLogger.get_logger()

        self._setup_ui()
        self._load_current_config()
        self._connect_signals()

    def _setup_ui(self):
        """Set up the dialog UI."""
        self.setWindowTitle("Configure Data Storage")
        self.setMinimumWidth(550)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setSpacing(15)

        # Header
        header_label = QLabel(f"Storage for: {self.project_name}")
        header_font = QFont()
        header_font.setPointSize(11)
        header_font.setBold(True)
        header_label.setFont(header_font)
        header_label.setStyleSheet("color: #5bbad5;")
        layout.addWidget(header_label)

        # Description
        desc_label = QLabel(
            "Choose how to store data pulled from the server. "
            "GeoPackage storage is recommended for persistent data."
        )
        desc_label.setWordWrap(True)
        desc_label.setStyleSheet("color: #666;")
        layout.addWidget(desc_label)

        # Storage options group
        options_group = QGroupBox("Storage Type")
        options_layout = QVBoxLayout(options_group)

        # Radio button group
        self.button_group = QButtonGroup(self)

        # Memory option
        self.memory_radio = QRadioButton("Temporary (Memory)")
        self.memory_radio.setToolTip(
            "Data is stored in memory only. "
            "All data will be lost when QGIS closes unless you save the project."
        )
        self.button_group.addButton(self.memory_radio)
        options_layout.addWidget(self.memory_radio)

        # Memory warning
        memory_warning = QLabel(
            "  ⚠️ Data will be lost when QGIS closes"
        )
        memory_warning.setStyleSheet("color: #ff9800; font-style: italic; margin-left: 20px;")
        options_layout.addWidget(memory_warning)

        options_layout.addSpacing(10)

        # Separator
        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setStyleSheet("color: #ddd;")
        options_layout.addWidget(separator)

        options_layout.addSpacing(10)

        # GeoPackage option
        self.gpkg_radio = QRadioButton("GeoPackage File (Recommended)")
        self.gpkg_radio.setToolTip(
            "Data is stored in a local GeoPackage file. "
            "Data persists between QGIS sessions."
        )
        self.button_group.addButton(self.gpkg_radio)
        options_layout.addWidget(self.gpkg_radio)

        # GeoPackage benefits
        gpkg_benefits = QLabel(
            "  ✓ Data persists between sessions\n"
            "  ✓ Can be shared and backed up\n"
            "  ✓ Works offline"
        )
        gpkg_benefits.setStyleSheet("color: #4caf50; margin-left: 20px;")
        options_layout.addWidget(gpkg_benefits)

        # GeoPackage path selection
        self.gpkg_path_widget = QFrame()
        gpkg_path_layout = QHBoxLayout(self.gpkg_path_widget)
        gpkg_path_layout.setContentsMargins(20, 10, 0, 0)

        path_label = QLabel("File:")
        gpkg_path_layout.addWidget(path_label)

        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("Choose GeoPackage location...")
        gpkg_path_layout.addWidget(self.path_edit, 1)

        self.browse_btn = QPushButton("Browse...")
        self.browse_btn.setMaximumWidth(100)
        gpkg_path_layout.addWidget(self.browse_btn)

        options_layout.addWidget(self.gpkg_path_widget)

        layout.addWidget(options_group)

        # Default directory info
        default_dir = self.storage_manager.get_default_directory()
        default_info = QLabel(f"Default location: {default_dir}")
        default_info.setStyleSheet("color: #888; font-size: 9pt;")
        default_info.setWordWrap(True)
        layout.addWidget(default_info)

        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setMinimumWidth(80)
        button_layout.addWidget(self.cancel_btn)

        self.save_btn = QPushButton("Save Configuration")
        self.save_btn.setMinimumWidth(120)
        self.save_btn.setDefault(True)
        self.save_btn.setStyleSheet(
            "QPushButton { background-color: #5bbad5; color: white; font-weight: bold; }"
            "QPushButton:hover { background-color: #4aa9c4; }"
        )
        button_layout.addWidget(self.save_btn)

        layout.addLayout(button_layout)

    def _connect_signals(self):
        """Connect UI signals."""
        self.memory_radio.toggled.connect(self._on_mode_changed)
        self.gpkg_radio.toggled.connect(self._on_mode_changed)
        self.browse_btn.clicked.connect(self._browse_gpkg)
        self.cancel_btn.clicked.connect(self.reject)
        self.save_btn.clicked.connect(self._save_config)

    def _load_current_config(self):
        """Load current storage configuration."""
        if self.project_id:
            config = self.storage_manager.get_storage_config(self.project_id)

            if config['is_geopackage']:
                self.gpkg_radio.setChecked(True)
                if config['geopackage_path']:
                    self.path_edit.setText(config['geopackage_path'])
            else:
                self.memory_radio.setChecked(True)

            # If not configured, suggest a path
            if not config['configured'] and self.project_id:
                suggested = self.storage_manager.get_suggested_path(
                    self.project_name, self.project_id
                )
                self.path_edit.setText(str(suggested))
        else:
            # No project - default to memory
            self.memory_radio.setChecked(True)

        self._on_mode_changed()

    def _on_mode_changed(self):
        """Handle storage mode change."""
        is_gpkg = self.gpkg_radio.isChecked()
        self.gpkg_path_widget.setEnabled(is_gpkg)
        self.path_edit.setEnabled(is_gpkg)
        self.browse_btn.setEnabled(is_gpkg)

    def _browse_gpkg(self):
        """Open file dialog to choose GeoPackage location."""
        # Start in current path or default directory
        current_path = self.path_edit.text()
        if current_path:
            start_dir = str(Path(current_path).parent)
        else:
            start_dir = str(self.storage_manager.get_default_directory())

        # Suggested filename
        suggested_name = self.storage_manager.get_suggested_filename(
            self.project_name, self.project_id or 0
        )

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Choose GeoPackage Location",
            os.path.join(start_dir, suggested_name),
            "GeoPackage Files (*.gpkg);;All Files (*)"
        )

        if file_path:
            # Ensure .gpkg extension
            if not file_path.lower().endswith('.gpkg'):
                file_path += '.gpkg'
            self.path_edit.setText(file_path)

    def _save_config(self):
        """Save storage configuration."""
        if not self.project_id:
            QMessageBox.warning(
                self,
                "No Project",
                "Please select a project before configuring storage."
            )
            return

        if self.gpkg_radio.isChecked():
            # Validate GeoPackage path
            gpkg_path = self.path_edit.text().strip()

            if not gpkg_path:
                QMessageBox.warning(
                    self,
                    "Path Required",
                    "Please specify a GeoPackage file location."
                )
                return

            # Ensure .gpkg extension
            if not gpkg_path.lower().endswith('.gpkg'):
                gpkg_path += '.gpkg'

            # Check if parent directory exists or can be created
            parent_dir = Path(gpkg_path).parent
            if not parent_dir.exists():
                try:
                    parent_dir.mkdir(parents=True, exist_ok=True)
                except OSError as e:
                    QMessageBox.critical(
                        self,
                        "Error",
                        f"Could not create directory:\n{e}"
                    )
                    return

            # Configure storage
            success = self.storage_manager.configure_storage(
                self.project_id,
                StorageMode.GEOPACKAGE,
                gpkg_path
            )

            if success:
                self.storage_configured.emit(StorageMode.GEOPACKAGE, gpkg_path)
                self.accept()
            else:
                QMessageBox.critical(
                    self,
                    "Error",
                    "Failed to configure storage. Check the log for details."
                )
        else:
            # Memory mode
            success = self.storage_manager.configure_storage(
                self.project_id,
                StorageMode.MEMORY,
                None
            )

            if success:
                self.storage_configured.emit(StorageMode.MEMORY, "")
                self.accept()

    def get_storage_mode(self) -> str:
        """Get selected storage mode."""
        if self.gpkg_radio.isChecked():
            return StorageMode.GEOPACKAGE
        return StorageMode.MEMORY

    def get_geopackage_path(self) -> str:
        """Get selected GeoPackage path."""
        if self.gpkg_radio.isChecked():
            return self.path_edit.text().strip()
        return ""
