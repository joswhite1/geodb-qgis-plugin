# -*- coding: utf-8 -*-
"""
Field Work Planning dialog for preparing and pushing planned samples.

Allows users to:
1. Select any existing point layer in QGIS
2. Configure sequence number pattern (prefix, start number)
3. Set sample type
4. Optionally take each sample's name from a layer attribute field
   (e.g. the customer's own "AK26-1001S" IDs) instead of leaving it blank
5. Push points as "Planned" samples to geodb.io server
"""
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QComboBox, QSpinBox, QFrame, QProgressBar,
    QTextBrowser, QGroupBox, QFormLayout, QMessageBox, QCheckBox
)
from qgis.PyQt.QtCore import pyqtSignal
from qgis.PyQt.QtGui import QFont
from qgis.core import (
    QgsVectorLayer, QgsWkbTypes, QgsMapLayerProxyModel
)
from qgis.gui import QgsMapLayerComboBox, QgsFieldComboBox

from ..utils.logger import PluginLogger
from ..utils.compat import QFrame_HLine
from ..utils.theme import T


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
        self._select_active_layer()
        self._update_preview()

    def _setup_ui(self):
        """Set up the dialog UI."""
        self.setWindowTitle("Plan Field Samples")
        self.setMinimumWidth(500)
        self.setModal(True)
        # Theme the dialog surface so the card reads correctly in both light
        # and dark mode.
        self.setStyleSheet(
            f"FieldWorkDialog {{ background-color: {T.SURFACE}; }}"
        )

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
        header_label.setStyleSheet(f"color: {T.ACCENT_TEXT};")
        layout.addWidget(header_label)

        # Description
        desc_label = QLabel(
            "Select a point layer created with QGIS tools (grid, random points, etc.) "
            "and push it to geodb.io as planned samples for field collection."
        )
        desc_label.setWordWrap(True)
        desc_label.setStyleSheet(f"color: {T.TEXT_MUTED}; margin-bottom: 8px;")
        layout.addWidget(desc_label)

        # Separator
        line = QFrame()
        line.setFrameShape(QFrame_HLine)
        line.setStyleSheet(f"background-color: {T.BORDER_SUBTLE};")
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
        self.feature_count_label.setStyleSheet(f"color: {T.TEXT_MUTED};")
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

        # === Sample Name (optional, from a layer field) ===
        # By default planned samples have no name -- the crew scans the bag
        # barcode in the field. Optionally, a layer attribute can supply the
        # sample name up front (e.g. the customer's own "AK26-1001S" IDs).
        name_group = QGroupBox("Sample Name")
        name_group.setStyleSheet(self._get_group_style())
        name_layout = QFormLayout(name_group)

        self.use_name_field_check = QCheckBox("Use a layer field for the sample name")
        self.use_name_field_check.setChecked(False)
        self.use_name_field_check.setToolTip(
            "Off (default): names are left blank for the field crew to scan/enter.\n"
            "On: each sample's name comes from the chosen layer attribute."
        )
        name_layout.addRow(self.use_name_field_check)

        self.name_field_combo = QgsFieldComboBox()
        self.name_field_combo.setEnabled(False)
        name_layout.addRow("Name field:", self.name_field_combo)

        self.name_field_hint = QLabel(
            "Sequence numbers below are still generated for navigation; "
            "your field sets the sample name."
        )
        self.name_field_hint.setWordWrap(True)
        self.name_field_hint.setStyleSheet(f"color: {T.TEXT_MUTED}; font-size: 11px;")
        self.name_field_hint.setVisible(False)
        name_layout.addRow(self.name_field_hint)

        layout.addWidget(name_group)

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
            f"color: {T.SUCCESS_TEXT}; font-family: monospace; padding: 8px; "
            f"background-color: {T.SUCCESS_BG}; border-radius: 4px;"
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
            f"QTextBrowser {{ background-color: {T.SURFACE_SUBTLE}; color: {T.TEXT_PRIMARY}; "
            f"border: 1px solid {T.BORDER_SUBTLE}; border-radius: 4px; padding: 8px; }}"
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
        self.use_name_field_check.toggled.connect(self._on_use_name_field_toggled)
        self.name_field_combo.fieldChanged.connect(self._update_preview)
        self.cancel_button.clicked.connect(self.reject)
        self.preview_button.clicked.connect(self._show_full_preview)
        self.push_button.clicked.connect(self._on_push_clicked)

    def _on_use_name_field_toggled(self, checked: bool):
        """Enable/disable the name-field picker and its hint."""
        self.name_field_combo.setEnabled(checked)
        self.name_field_hint.setVisible(checked)
        self._update_preview()

    def _name_field(self):
        """Return the chosen name field, or None when the option is off."""
        if not self.use_name_field_check.isChecked():
            return None
        field = self.name_field_combo.currentField()
        return field or None

    def _select_active_layer(self):
        """Pre-select the currently active layer from the QGIS Layers panel."""
        try:
            from qgis.utils import iface
            if iface:
                active_layer = iface.activeLayer()
                if (active_layer and isinstance(active_layer, QgsVectorLayer) and
                        QgsWkbTypes.geometryType(active_layer.wkbType()) ==
                        QgsWkbTypes.PointGeometry):
                    self.layer_combo.setLayer(active_layer)
        except Exception:
            pass

    def _on_layer_changed(self, layer):
        """Handle layer selection change."""
        if layer and isinstance(layer, QgsVectorLayer):
            count = layer.featureCount()
            self.feature_count_label.setText(f"{count} features")
            self.push_button.setEnabled(count > 0)
            # Repopulate the name-field picker with this layer's attributes.
            self.name_field_combo.setLayer(layer)
        else:
            self.feature_count_label.setText("0 features")
            self.push_button.setEnabled(False)
            self.name_field_combo.setLayer(None)

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

        # When pulling names from a field, preview those names too -- it's what
        # the user actually cares about; the sequence is secondary (navigation).
        name_field = self._name_field()
        if name_field:
            name_vals = self._sample_field_values(layer, name_field, limit=3)
            if name_vals:
                shown = ', '.join(name_vals)
                if count > len(name_vals):
                    shown += ', ...'
                preview_text = f"Names: {shown}  |  Seq: {preview_text}"

        self.preview_label.setText(preview_text)

    @staticmethod
    def _sample_field_values(layer, field_name, limit=None):
        """Return stringified field values (str(raw).strip()) for preview.

        Mirrors how push_planned_samples reads the field, so the preview matches
        exactly what will be pushed.
        """
        values = []
        for feature in layer.getFeatures():
            raw = feature[field_name]
            values.append('' if raw is None else str(raw).strip())
            if limit is not None and len(values) >= limit:
                break
        return values

    def _show_full_preview(self):
        """Show full preview in message browser."""
        layer = self.layer_combo.currentLayer()
        if not layer:
            return

        count = layer.featureCount()
        prefix = self.prefix_edit.text()
        start = self.start_spin.value()
        padding = self.padding_spin.value()
        name_field = self._name_field()

        if name_field:
            # Pair each sample name with its generated sequence number.
            names = self._sample_field_values(layer, name_field, limit=50)
            lines = [
                f"<b>Sample names from '{name_field}' "
                f"(sequence number in brackets, {count} total):</b><br/>"
            ]
            for i, nm in enumerate(names):
                num = str(start + i).zfill(padding)
                shown = nm if nm else "(blank!)"
                lines.append(f"  {shown}  [{prefix}{num}]")
        else:
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

        # If pulling names from a field, validate it up front and block on any
        # blank / duplicate / overlong value rather than landing bad records.
        name_field = self._name_field()
        if self.use_name_field_check.isChecked() and not name_field:
            QMessageBox.warning(
                self, "No Name Field",
                "Choose a layer field for the sample name, or uncheck "
                "'Use a layer field for the sample name'."
            )
            return
        if name_field and not self._validate_name_field_or_warn(layer, name_field):
            return

        # Confirm
        if name_field:
            name_line = (
                f"Sample names: from field '{name_field}'\n"
                f"Sequence numbers (for navigation): "
                f"{self.prefix_edit.text()}{str(self.start_spin.value()).zfill(self.padding_spin.value())} ..."
            )
        else:
            name_line = (
                f"Sequence numbers: {self.prefix_edit.text()}{str(self.start_spin.value()).zfill(self.padding_spin.value())} "
                f"through {self.prefix_edit.text()}{str(self.start_spin.value() + count - 1).zfill(self.padding_spin.value())}"
            )
        reply = QMessageBox.question(
            self, "Confirm Push",
            f"This will create {count} planned samples in project '{project.name}'.\n\n"
            f"{name_line}\n\n"
            "Continue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if reply != QMessageBox.StandardButton.Yes:
            return

        self._execute_push(layer)

    def _validate_name_field_or_warn(self, layer, name_field) -> bool:
        """Run validate_name_field; on problems, show them and return False."""
        report = self.data_manager.validate_name_field(layer, name_field)
        if report['ok']:
            return True

        parts = []
        if report['blanks']:
            parts.append(f"• {len(report['blanks'])} feature(s) have a blank '{name_field}'")
        if report['duplicates']:
            dup_count = sum(len(f) for f in report['duplicates'].values())
            examples = ', '.join(list(report['duplicates'].keys())[:5])
            parts.append(
                f"• {len(report['duplicates'])} duplicated value(s) across "
                f"{dup_count} features (e.g. {examples})"
            )
        if report['too_long']:
            parts.append(
                f"• {len(report['too_long'])} value(s) exceed 50 characters"
            )

        QMessageBox.warning(
            self, "Fix the name field first",
            f"The field '{name_field}' can't be used as sample names yet:\n\n"
            + "\n".join(parts)
            + "\n\nSample names must be present and unique within the project. "
            "Fix these rows (or pick a different field) and try again."
        )
        return False

    def _execute_push(self, layer: QgsVectorLayer, skip_conflict_check: bool = False):
        """Execute the push operation."""
        self._set_pushing(True)
        self.message_browser.clear()
        self.message_browser.setVisible(True)

        try:
            prefix = self.prefix_edit.text()
            start = self.start_spin.value()
            padding = self.padding_spin.value()
            sample_type = self.sample_type_combo.currentData()
            name_field = self._name_field()

            self._log_message(f"Starting push of {layer.featureCount()} planned samples...")

            # Call data manager to push
            result = self.data_manager.push_planned_samples(
                source_layer=layer,
                prefix=prefix,
                start_number=start,
                padding=padding,
                sample_type=sample_type,
                name_field=name_field,
                progress_callback=self._on_progress,
                skip_conflict_check=skip_conflict_check
            )

            # Check if data manager returned conflict info instead of pushing
            if result.get('has_conflicts'):
                self._set_pushing(False)
                self._handle_conflicts(layer, result['conflicts'])
                return

            # Show results
            created = result.get('created', 0)
            updated = result.get('updated', 0)
            errors = result.get('errors', 0)

            if errors == 0:
                # Build success message
                if updated > 0:
                    msg = f"Created {created}, updated {updated} planned samples."
                else:
                    msg = f"Created {created} planned samples."

                self._log_message(
                    f"<span style='color: {T.SUCCESS_TEXT};'><b>Success!</b> {msg}</span>"
                )
                QMessageBox.information(
                    self, "Push Complete",
                    f"Successfully {msg}\n\n"
                    "Samples can now be assigned to field workers via the geodb.io dashboard."
                )
                self.push_completed.emit(result)
                self.accept()
            else:
                self._log_message(
                    f"<span style='color: {T.DANGER_TEXT};'><b>Completed with errors:</b> "
                    f"{created} created, {updated} updated, {errors} failed.</span>"
                )
                error_details = result.get('error_details', [])
                if error_details:
                    for err in error_details[:5]:
                        # Handle both old format (error key) and new format (errors key from bulk API)
                        error_msg = err.get('error') or err.get('errors') or 'Unknown error'
                        seq = err.get('sequence_number') or err.get('data', '')
                        if seq:
                            self._log_message(f"  - {seq}: {error_msg}")
                        else:
                            self._log_message(f"  - {error_msg}")
                    if len(error_details) > 5:
                        self._log_message(f"  ... and {len(error_details) - 5} more errors")

        except Exception as e:
            self.logger.error(f"Push failed: {e}")
            self._log_message(f"<span style='color: {T.DANGER_TEXT};'><b>Error:</b> {str(e)}</span>")
            QMessageBox.critical(self, "Push Failed", f"An error occurred:\n\n{str(e)}")

        finally:
            self._set_pushing(False)

    def _handle_conflicts(self, layer: QgsVectorLayer, conflicts: dict):
        """
        Show a warning dialog when existing samples would be overwritten.

        Gives the user the choice to overwrite, cancel, or adjust their
        sequence numbers to avoid the conflict.
        """
        would_update = conflicts.get('would_update', 0)
        would_create = conflicts.get('would_create', 0)
        conflict_list = conflicts.get('conflicts', [])

        # Build a readable list of the first few conflicts
        detail_lines = []
        for c in conflict_list[:10]:
            seq = c.get('sequence_number', '?')
            status_display = c.get('existing_status_display', 'Unknown')
            existing_name = c.get('existing_name', '')
            if existing_name:
                detail_lines.append(f"  {seq} ({status_display}, name: {existing_name})")
            else:
                detail_lines.append(f"  {seq} ({status_display})")
        if len(conflict_list) > 10:
            detail_lines.append(f"  ... and {len(conflict_list) - 10} more")

        details = "\n".join(detail_lines)

        self._log_message(
            f"<span style='color: {T.WARNING_TEXT};'><b>Warning:</b> "
            f"{would_update} existing samples would be overwritten.</span>"
        )

        msg = (
            f"{would_update} existing sample(s) already use these sequence numbers "
            f"and will be OVERWRITTEN:\n\n"
            f"{details}\n\n"
            f"{would_create} new sample(s) would be created.\n\n"
            f"Do you want to overwrite the existing samples?\n\n"
            f"Tip: Change the start number or prefix to avoid conflicts."
        )

        reply = QMessageBox.warning(
            self, "Existing Samples Found",
            msg,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            self._log_message("User confirmed overwrite. Pushing...")
            self._execute_push(layer, skip_conflict_check=True)
        else:
            self._log_message("Push cancelled by user.")

    def _on_progress(self, percent: int, message: str):
        """Handle progress updates."""
        self.progress_bar.setValue(percent)
        self._log_message(message)
        # Note: Removed QApplication.processEvents() to prevent heap corruption crashes

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
        self.use_name_field_check.setEnabled(not pushing)
        # The field picker is only live when the option is checked.
        self.name_field_combo.setEnabled(
            not pushing and self.use_name_field_check.isChecked()
        )
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
        return f"""
            QGroupBox {{
                font-weight: bold;
                border: 1px solid {T.BORDER_SUBTLE};
                border-radius: 6px;
                margin-top: 12px;
                padding-top: 16px;
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 8px;
                color: {T.TEXT_PRIMARY};
            }}
        """

    def _get_input_style(self) -> str:
        """Get stylesheet for input fields."""
        return f"""
            QLineEdit, QSpinBox, QComboBox {{
                padding: 6px 10px;
                border: 1px solid {T.BORDER};
                border-radius: 4px;
                background-color: {T.INPUT_BG};
                color: {T.TEXT_PRIMARY};
            }}
            QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{
                border-color: {T.ACCENT};
            }}
            QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled {{
                background-color: {T.INPUT_BG_DISABLED};
                color: {T.TEXT_FAINT};
            }}
        """

    def _get_primary_button_style(self) -> str:
        """Get stylesheet for primary button."""
        return f"""
            QPushButton {{
                padding: 10px 20px;
                background-color: {T.ACCENT};
                color: {T.TEXT_ON_ACCENT};
                border: none;
                border-radius: 6px;
                font-weight: bold;
                min-width: 150px;
            }}
            QPushButton:hover {{
                background-color: {T.ACCENT_HOVER};
            }}
            QPushButton:pressed {{
                background-color: {T.ACCENT_ACTIVE};
            }}
            QPushButton:disabled {{
                background-color: {T.ACCENT_DISABLED};
            }}
        """

    def _get_secondary_button_style(self) -> str:
        """Get stylesheet for secondary button."""
        return f"""
            QPushButton {{
                padding: 10px 20px;
                background-color: {T.SURFACE};
                color: {T.TEXT_PRIMARY};
                border: 1px solid {T.BORDER};
                border-radius: 6px;
                min-width: 80px;
            }}
            QPushButton:hover {{
                background-color: {T.SURFACE_SUBTLE};
                border-color: {T.TEXT_FAINT};
            }}
            QPushButton:pressed {{
                background-color: {T.SURFACE_SUNKEN};
            }}
            QPushButton:disabled {{
                color: {T.TEXT_FAINT};
            }}
        """
