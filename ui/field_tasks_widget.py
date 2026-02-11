# -*- coding: utf-8 -*-
"""
Field Tasks widget for pulling and planning field sample tasks.

Provides a dedicated tab for field task workflows:
- Pull planned/assigned field tasks from the server
- Plan new field samples from any QGIS point layer
"""
from qgis.PyQt.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QGroupBox, QMessageBox, QFrame, QProgressBar
)
from qgis.PyQt.QtCore import pyqtSignal

from .field_work_dialog import FieldWorkDialog
from ..utils.logger import PluginLogger


class FieldTasksWidget(QWidget):
    """Widget for the Field Tasks tab - pull and plan field samples."""

    # Signals to communicate with main dialog
    status_message = pyqtSignal(str, str)  # (message, level)
    field_work_completed = pyqtSignal(dict)  # push result from planning dialog

    def __init__(self, data_manager, project_manager, style_processor, parent=None):
        super().__init__(parent)
        self.data_manager = data_manager
        self.project_manager = project_manager
        self.style_processor = style_processor
        self.logger = PluginLogger.get_logger('field_tasks_widget')

        self._setup_ui()

    def _setup_ui(self):
        """Build the field tasks tab UI."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(12)

        # Header
        header = QLabel("Field Tasks")
        header.setStyleSheet(
            "font-size: 18px; font-weight: bold; color: #0e7490;"
        )
        main_layout.addWidget(header)

        # Description
        desc = QLabel(
            "Pull planned or assigned field samples from the server, "
            "or create new planned samples from any QGIS point layer."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #6b7280; margin-bottom: 4px;")
        main_layout.addWidget(desc)

        # Separator
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        main_layout.addWidget(line)

        # --- Pull Field Tasks group ---
        pull_group = QGroupBox("Pull Field Tasks")
        pull_group.setStyleSheet(
            "QGroupBox { font-weight: bold; padding-top: 14px; }"
        )
        pull_layout = QVBoxLayout(pull_group)

        pull_desc = QLabel(
            "Download planned and assigned samples from geodb.io as a "
            "field tasks layer. Samples are styled by status: "
            "Planned (gray), Assigned (yellow), Collected (green), Skipped (red)."
        )
        pull_desc.setWordWrap(True)
        pull_desc.setStyleSheet("font-weight: normal; color: #374151;")
        pull_layout.addWidget(pull_desc)

        btn_row = QHBoxLayout()
        self.pull_button = QPushButton("Pull Field Tasks")
        self.pull_button.setEnabled(False)
        self.pull_button.setToolTip(
            "Pull planned and assigned samples as a field tasks layer\n"
            "(separate from assay-colored layers)"
        )
        self.pull_button.setStyleSheet("""
            QPushButton {
                background-color: #2563eb;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #1d4ed8; }
            QPushButton:disabled { background-color: #9ca3af; }
        """)
        self.pull_button.clicked.connect(self._on_pull_field_tasks_clicked)
        btn_row.addWidget(self.pull_button)
        btn_row.addStretch()
        pull_layout.addLayout(btn_row)

        main_layout.addWidget(pull_group)

        # --- Plan Field Samples group ---
        plan_group = QGroupBox("Plan Field Samples")
        plan_group.setStyleSheet(
            "QGroupBox { font-weight: bold; padding-top: 14px; }"
        )
        plan_layout = QVBoxLayout(plan_group)

        plan_desc = QLabel(
            "Select a point layer in QGIS and push it to the server as "
            "planned samples for field collection. You can configure "
            "sequence numbers and sample types."
        )
        plan_desc.setWordWrap(True)
        plan_desc.setStyleSheet("font-weight: normal; color: #374151;")
        plan_layout.addWidget(plan_desc)

        btn_row2 = QHBoxLayout()
        self.plan_button = QPushButton("Plan Field Samples...")
        self.plan_button.setEnabled(False)
        self.plan_button.setToolTip(
            "Create planned samples from any point layer for field collection"
        )
        self.plan_button.setStyleSheet("""
            QPushButton {
                background-color: #059669;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover { background-color: #047857; }
            QPushButton:disabled { background-color: #9ca3af; }
        """)
        self.plan_button.clicked.connect(self._on_plan_field_samples_clicked)
        btn_row2.addWidget(self.plan_button)
        btn_row2.addStretch()
        plan_layout.addLayout(btn_row2)

        main_layout.addWidget(plan_group)

        # Progress bar (hidden by default)
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setTextVisible(True)
        main_layout.addWidget(self.progress_bar)

        # Vertical spacer
        main_layout.addStretch()

    # ==================== PUBLIC API ====================

    def update_button_state(self):
        """Update button enabled states based on auth and permissions."""
        has_project = self.project_manager.active_project is not None

        # Pull Field Tasks - requires view permission
        can_view = has_project and self.project_manager.can_view()
        self.pull_button.setEnabled(can_view)

        # Plan Field Samples - requires edit permission
        can_edit = has_project and self.project_manager.can_edit()
        self.plan_button.setEnabled(can_edit)

    # ==================== PULL FIELD TASKS ====================

    def _on_pull_field_tasks_clicked(self):
        """Handle pull field tasks button click."""
        if not self.project_manager.active_project:
            QMessageBox.warning(
                self, "No Project",
                "Please select a project before pulling field tasks."
            )
            return

        if not self.project_manager.can_view():
            QMessageBox.warning(
                self, "Permission Denied",
                "You need view permission to pull field tasks."
            )
            return

        try:
            self.status_message.emit(
                "Starting pull for field tasks (planned/assigned samples)...",
                "info"
            )
            self.pull_button.setEnabled(False)
            self.progress_bar.setVisible(True)
            self.progress_bar.setValue(0)

            result = self.data_manager.pull_field_tasks(
                progress_callback=self._on_progress
            )

            pulled = result.get('pulled', 0)
            layer = result.get('layer')

            if pulled > 0:
                self.status_message.emit(
                    f"\u2713 Field tasks pull complete: {pulled} tasks",
                    "success"
                )
                if layer:
                    self._apply_field_task_styling(layer)
            else:
                self.status_message.emit(
                    "No planned or assigned samples found for this project.",
                    "info"
                )

        except Exception as e:
            self.logger.exception("Pull field tasks failed")
            self.status_message.emit(f"Pull field tasks failed: {e}", "error")
            QMessageBox.critical(self, "Error", f"Pull field tasks failed: {e}")
        finally:
            self.pull_button.setEnabled(True)
            self.progress_bar.setVisible(False)

    def _apply_field_task_styling(self, layer):
        """Apply status-based categorized styling to field tasks layer."""
        try:
            success = self.style_processor.apply_field_task_style(layer)
            if success:
                self.status_message.emit(
                    "\u2713 Applied status-based styling (Planned=gray, Assigned=yellow)",
                    "success"
                )
            else:
                self.status_message.emit(
                    "Could not apply status styling - 'status' field not found",
                    "warning"
                )
        except Exception as e:
            self.logger.exception("Failed to apply field task styling")
            self.status_message.emit(f"Failed to apply styling: {e}", "warning")

    def _on_progress(self, percent: int, message: str):
        """Handle progress updates from data manager."""
        self.progress_bar.setValue(percent)
        self.status_message.emit(f"[{percent}%] {message}", "info")

    # ==================== PLAN FIELD SAMPLES ====================

    def _on_plan_field_samples_clicked(self):
        """Handle plan field samples button click - open the planning dialog."""
        if not self.project_manager.active_project:
            QMessageBox.warning(
                self, "No Project",
                "Please select a project before planning field samples."
            )
            return

        if not self.project_manager.can_edit():
            QMessageBox.warning(
                self, "Permission Denied",
                "You need edit permission to create planned samples."
            )
            return

        dialog = FieldWorkDialog(
            parent=self,
            data_manager=self.data_manager,
            project_manager=self.project_manager
        )

        dialog.push_completed.connect(self.field_work_completed.emit)
        dialog.exec_()
