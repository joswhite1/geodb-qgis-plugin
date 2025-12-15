# -*- coding: utf-8 -*-
"""
Field Work Planning dialog for preparing and pushing planned samples.

Allows users to:
1. Select any existing point layer in QGIS
2. Configure sequence number pattern (prefix, start number)
3. Set sample type
4. Push points as "Planned" samples to geodb.io server
"""
from typing import Optional, List, Dict, Any, Callable
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QComboBox, QSpinBox, QFrame, QProgressBar,
    QTextBrowser, QGroupBox, QFormLayout, QMessageBox, QApplication
)
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QFont
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsWkbTypes, QgsMapLayerProxyModel
)
from qgis.gui import QgsMapLayerComboBox

from ..utils.logger import PluginLogger


# Sample type choices matching the API
SAMPLE_TYPE_CHOICES = [
    ('SL', 'Soil'),
    ('RK', 'Rock Chip'),
    ('OC', 'Outcrop'),
    ('FL', 'Float'),
    ('SS', 'Stream Sediment'),
    ('PC', 'Pan Concentrate'),
    ('OT', 'Other'),
]


class FieldWorkDialog(QDialog):
    """
    Dialog for preparing point layers as planned field samples.

    Signals:
        push_completed: Emitted when push succeeds, with results dict
    """

    push_completed = pyqtSignal(dict)

    def __init__(self, parent=None, data_manager=None, project_manager=None):
        """
        Initialize field work dialog.

        Args:
            parent: Parent widget
            data_manager: DataManager instance for push operations
            project_manager: ProjectManager instance for project context
        """
        super().__init__(parent)
        self.data_manager = data_manager
        self.project_manager = project_manager
        self.logger = PluginLogger.get_logger()
        self._is_pushing = False

        self._setup_ui()
        self._connect_signals()
        self._update_preview()

    def _setup_ui(self):
        """Set up the dialog UI."""
        self.setWindowTitle("Plan Field Samples")
        self.setMinimumWidth(500)
        self.setModal(True)

        # Main layout
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 24, 24, 24)

        # Header
        header_label = QLabel("Prepare Samples for Field Collection")
        header_font = QFont()
        header_font.setPointSize(14)
        header_font.setBold(True)
        header_label.setFont(header_font)
        header_label.setStyleSheet("color: #2563eb;")
        layout.addWidget(header_label)

        # Description
        desc_label = QLabel(
            "Select a point layer created with QGIS tools (grid, random points, etc.) "
            "and push it to geodb.io as planned samples for field collection."
        )
        desc_label.setWordWrap(True)
        desc_label.setStyleSheet("color: #6b7280; margin-bottom: 8px;")
        layout.addWidget(desc_label)

        # Separator
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("background-color: #e5e7eb;")
        layout.addWidget(line)

        # === Source Layer Selection ===
        layer_group = QGroupBox("Source Layer")
        layer_group.setStyleSheet(self._get_group_style())
        layer_layout = QFormLayout(layer_group)

        # Layer combo box (points only)
        self.layer_combo = QgsMapLayerComboBox()
        self.layer_combo.setFilters(QgsMapLayerProxyModel.PointLayer)
        self.layer_combo.setAllowEmptyLayer(True)
        self.layer_combo.setShowCrs(True)
        layer_layout.addRow("Point Layer:", self.layer_combo)

        # Feature count label
        self.feature_count_label = QLabel("0 features")
        self.feature_count_label.setStyleSheet("color: #6b7280;")
        layer_layout.addRow("Features:", self.feature_count_label)

        layout.addWidget(layer_group)

        # === Sample Configuration ===
        config_group = QGroupBox("Sample Configuration")
        config_group.setStyleSheet(self._get_group_style())
        config_layout = QFormLayout(config_group)

        # Sample type
        self.sample_type_combo = QComboBox()
        for code, name in SAMPLE_TYPE_CHOICES:
            self.sample_type_combo.addItem(name, code)
        config_layout.addRow("Sample Type:", self.sample_type_combo)

        layout.addWidget(config_group)

        # === Sequence Number Configuration ===
        seq_group = QGroupBox("Sequence Numbers")
        seq_group.setStyleSheet(self._get_group_style())
        seq_layout = QFormLayout(seq_group)

        # Prefix
        self.prefix_edit = QLineEdit("SS-")
        self.prefix_edit.setPlaceholderText("e.g., SS-, GRID-A-, SOIL-")
        self.prefix_edit.setStyleSheet(self._get_input_style())
        seq_layout.addRow("Prefix:", self.prefix_edit)

        # Start number
        self.start_spin = QSpinBox()
        self.start_spin.setRange(1, 99999)
        self.start_spin.setValue(1)
        self.start_spin.setStyleSheet(self._get_input_style())
        seq_layout.addRow("Start at:", self.start_spin)

        # Zero padding
        self.padding_spin = QSpinBox()
        self.padding_spin.setRange(1, 6)
        self.padding_spin.setValue(3)
        self.padding_spin.setToolTip("Number of digits (3 = 001, 4 = 0001)")
        self.padding_spin.setStyleSheet(self._get_input_style())
        seq_layout.addRow("Zero Padding:", self.padding_spin)

        # Preview
        self.preview_label = QLabel("SS-001, SS-002, SS-003, ...")
        self.preview_label.setStyleSheet(
            "color: #059669; font-family: monospace; padding: 8px; "
            "background-color: #ecfdf5; border-radius: 4px;"
        )
        seq_layout.addRow("Preview:", self.preview_label)

        layout.addWidget(seq_group)

        # === Progress and Messages ===
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.message_browser = QTextBrowser()
        self.message_browser.setMaximumHeight(100)
        self.message_browser.setStyleSheet(
            "QTextBrowser { background-color: #f9fafb; border: 1px solid #e5e7eb; "
            "border-radius: 4px; padding: 8px; }"
        )
        self.message_browser.setVisible(False)
        layout.addWidget(self.message_browser)

        # === Buttons ===
        button_layout = QHBoxLayout()
        button_layout.setSpacing(12)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setStyleSheet(self._get_secondary_button_style())
        button_layout.addWidget(self.cancel_button)

        button_layout.addStretch()

        self.preview_button = QPushButton("Preview")
        self.preview_button.setStyleSheet(self._get_secondary_button_style())
        self.preview_button.setToolTip("Show sequence numbers that will be assigned")
        button_layout.addWidget(self.preview_button)

        self.push_button = QPushButton("Push as Planned Samples")
        self.push_button.setStyleSheet(self._get_primary_button_style())
        self.push_button.setDefault(True)
        button_layout.addWidget(self.push_button)

        layout.addLayout(button_layout)

    def _connect_signals(self):
        """Connect UI signals to handlers."""
        self.layer_combo.layerChanged.connect(self._on_layer_changed)
        self.prefix_edit.textChanged.connect(self._update_preview)
        self.start_spin.valueChanged.connect(self._update_preview)
        self.padding_spin.valueChanged.connect(self._update_preview)
        self.cancel_button.clicked.connect(self.reject)
        self.preview_button.clicked.connect(self._show_full_preview)
        self.push_button.clicked.connect(self._on_push_clicked)

    def _on_layer_changed(self, layer):
        """Handle layer selection change."""
        if layer and isinstance(layer, QgsVectorLayer):
            count = layer.featureCount()
            self.feature_count_label.setText(f"{count} features")
            self.push_button.setEnabled(count > 0)
        else:
            self.feature_count_label.setText("0 features")
            self.push_button.setEnabled(False)

        self._update_preview()

    def _update_preview(self):
        """Update sequence number preview."""
        layer = self.layer_combo.currentLayer()
        if not layer:
            self.preview_label.setText("No layer selected")
            return

        count = layer.featureCount()
        if count == 0:
            self.preview_label.setText("Layer has no features")
            return

        prefix = self.prefix_edit.text()
        start = self.start_spin.value()
        padding = self.padding_spin.value()

        # Generate preview (first 3 and last if more than 4)
        samples = []
        preview_count = min(count, 3)
        for i in range(preview_count):
            num = str(start + i).zfill(padding)
            samples.append(f"{prefix}{num}")

        if count > 4:
            last_num = str(start + count - 1).zfill(padding)
            preview_text = f"{', '.join(samples)}, ... {prefix}{last_num}"
        elif count == 4:
            num = str(start + 3).zfill(padding)
            samples.append(f"{prefix}{num}")
            preview_text = ', '.join(samples)
        else:
            preview_text = ', '.join(samples)

        self.preview_label.setText(preview_text)

    def _show_full_preview(self):
        """Show full preview in message browser."""
        layer = self.layer_combo.currentLayer()
        if not layer:
            return

        count = layer.featureCount()
        prefix = self.prefix_edit.text()
        start = self.start_spin.value()
        padding = self.padding_spin.value()

        # Generate all sequence numbers
        lines = [f"<b>Sequence numbers to be assigned ({count} total):</b><br/>"]
        for i in range(min(count, 50)):  # Show max 50
            num = str(start + i).zfill(padding)
            lines.append(f"  {prefix}{num}")

        if count > 50:
            lines.append(f"  ... and {count - 50} more")

        self.message_browser.setHtml("<br/>".join(lines))
        self.message_browser.setVisible(True)

    def _on_push_clicked(self):
        """Handle push button click."""
        layer = self.layer_combo.currentLayer()
        if not layer:
            QMessageBox.warning(self, "No Layer", "Please select a point layer.")
            return

        # Validate project selection
        if not self.project_manager or not self.project_manager.get_active_project():
            QMessageBox.warning(
                self, "No Project",
                "Please select a project in the main plugin dialog first."
            )
            return

        # Validate edit permission
        if not self.project_manager.can_edit():
            QMessageBox.warning(
                self, "Permission Denied",
                "You do not have permission to edit data in this project."
            )
            return

        project = self.project_manager.get_active_project()
        count = layer.featureCount()

        # Confirm
        reply = QMessageBox.question(
            self, "Confirm Push",
            f"This will create {count} planned samples in project '{project.name}'.\n\n"
            f"Sequence numbers: {self.prefix_edit.text()}{str(self.start_spin.value()).zfill(self.padding_spin.value())} "
            f"through {self.prefix_edit.text()}{str(self.start_spin.value() + count - 1).zfill(self.padding_spin.value())}\n\n"
            "Continue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply != QMessageBox.Yes:
            return

        self._execute_push(layer)

    def _execute_push(self, layer: QgsVectorLayer):
        """Execute the push operation."""
        self._set_pushing(True)
        self.message_browser.clear()
        self.message_browser.setVisible(True)

        try:
            prefix = self.prefix_edit.text()
            start = self.start_spin.value()
            padding = self.padding_spin.value()
            sample_type = self.sample_type_combo.currentData()

            self._log_message(f"Starting push of {layer.featureCount()} planned samples...")

            # Call data manager to push
            result = self.data_manager.push_planned_samples(
                source_layer=layer,
                prefix=prefix,
                start_number=start,
                padding=padding,
                sample_type=sample_type,
                progress_callback=self._on_progress
            )

            # Show results
            created = result.get('created', 0)
            errors = result.get('errors', 0)

            if errors == 0:
                self._log_message(
                    f"<span style='color: #059669;'><b>Success!</b> "
                    f"Created {created} planned samples.</span>"
                )
                QMessageBox.information(
                    self, "Push Complete",
                    f"Successfully created {created} planned samples.\n\n"
                    "Samples can now be assigned to field workers via the geodb.io dashboard."
                )
                self.push_completed.emit(result)
                self.accept()
            else:
                self._log_message(
                    f"<span style='color: #dc2626;'><b>Completed with errors:</b> "
                    f"{created} created, {errors} failed.</span>"
                )
                error_details = result.get('error_details', [])
                if error_details:
                    for err in error_details[:5]:
                        self._log_message(f"  - {err.get('error', 'Unknown error')}")
                    if len(error_details) > 5:
                        self._log_message(f"  ... and {len(error_details) - 5} more errors")

        except Exception as e:
            self.logger.error(f"Push failed: {e}")
            self._log_message(f"<span style='color: #dc2626;'><b>Error:</b> {str(e)}</span>")
            QMessageBox.critical(self, "Push Failed", f"An error occurred:\n\n{str(e)}")

        finally:
            self._set_pushing(False)

    def _on_progress(self, percent: int, message: str):
        """Handle progress updates."""
        self.progress_bar.setValue(percent)
        self._log_message(message)
        QApplication.processEvents()

    def _log_message(self, message: str):
        """Add message to browser."""
        self.message_browser.append(message)
        # Scroll to bottom
        cursor = self.message_browser.textCursor()
        cursor.movePosition(cursor.End)
        self.message_browser.setTextCursor(cursor)

    def _set_pushing(self, pushing: bool):
        """Set pushing state."""
        self._is_pushing = pushing

        self.layer_combo.setEnabled(not pushing)
        self.sample_type_combo.setEnabled(not pushing)
        self.prefix_edit.setEnabled(not pushing)
        self.start_spin.setEnabled(not pushing)
        self.padding_spin.setEnabled(not pushing)
        self.preview_button.setEnabled(not pushing)
        self.push_button.setEnabled(not pushing)
        self.cancel_button.setEnabled(not pushing)

        self.progress_bar.setVisible(pushing)
        if pushing:
            self.progress_bar.setValue(0)
            self.push_button.setText("Pushing...")
        else:
            self.push_button.setText("Push as Planned Samples")

    def _get_group_style(self) -> str:
        """Get stylesheet for group boxes."""
        return """
            QGroupBox {
                font-weight: bold;
                border: 1px solid #e5e7eb;
                border-radius: 6px;
                margin-top: 12px;
                padding-top: 16px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 8px;
                color: #374151;
            }
        """

    def _get_input_style(self) -> str:
        """Get stylesheet for input fields."""
        return """
            QLineEdit, QSpinBox, QComboBox {
                padding: 6px 10px;
                border: 1px solid #d1d5db;
                border-radius: 4px;
                background-color: #ffffff;
            }
            QLineEdit:focus, QSpinBox:focus, QComboBox:focus {
                border-color: #2563eb;
            }
            QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {
                background-color: #f3f4f6;
                color: #9ca3af;
            }
        """

    def _get_primary_button_style(self) -> str:
        """Get stylesheet for primary button."""
        return """
            QPushButton {
                padding: 10px 20px;
                background-color: #2563eb;
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: bold;
                min-width: 150px;
            }
            QPushButton:hover {
                background-color: #1d4ed8;
            }
            QPushButton:pressed {
                background-color: #1e40af;
            }
            QPushButton:disabled {
                background-color: #93c5fd;
            }
        """

    def _get_secondary_button_style(self) -> str:
        """Get stylesheet for secondary button."""
        return """
            QPushButton {
                padding: 10px 20px;
                background-color: #ffffff;
                color: #374151;
                border: 1px solid #d1d5db;
                border-radius: 6px;
                min-width: 80px;
            }
            QPushButton:hover {
                background-color: #f9fafb;
                border-color: #9ca3af;
            }
            QPushButton:pressed {
                background-color: #f3f4f6;
            }
            QPushButton:disabled {
                color: #9ca3af;
            }
        """
