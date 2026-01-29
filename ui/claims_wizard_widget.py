# -*- coding: utf-8 -*-
"""
Claims wizard main container widget.

Provides step navigation, step indicator, and manages step widget transitions.
"""
from typing import Optional, List, TYPE_CHECKING

from qgis.PyQt.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QStackedWidget, QFrame, QMessageBox, QSizePolicy
)
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QFont

from .claims_wizard_state import ClaimsWizardState
from ..utils.logger import PluginLogger

if TYPE_CHECKING:
    from ..managers.claims_manager import ClaimsManager


class StepIndicator(QWidget):
    """
    Visual step indicator showing progress through the wizard.

    Displays numbered circles connected by lines, highlighting the current step
    and showing completed steps with checkmarks.
    """

    step_clicked = pyqtSignal(int)  # Emitted when a step is clicked

    def __init__(self, step_names: List[str], parent=None):
        super().__init__(parent)
        self.step_names = step_names
        self.current_step = 0
        self.completed_steps = []
        self._is_destroyed = False
        self._setup_ui()

    def cleanup(self):
        """Mark widget as destroyed to prevent crashes from deferred events."""
        self._is_destroyed = True

    def _setup_ui(self):
        """Set up the step indicator UI."""
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.setSpacing(0)

        self.step_widgets = []
        self.connector_widgets = []

        for i, name in enumerate(self.step_names):
            # Step circle and label
            step_widget = self._create_step_widget(i, name)
            self.step_widgets.append(step_widget)
            layout.addWidget(step_widget)

            # Connector line (except after last step)
            if i < len(self.step_names) - 1:
                connector = self._create_connector()
                self.connector_widgets.append(connector)
                layout.addWidget(connector)

        self._update_styles()

    def _create_step_widget(self, index: int, name: str) -> QWidget:
        """Create a step circle with label."""
        widget = QWidget()
        widget.setCursor(Qt.PointingHandCursor)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        layout.setAlignment(Qt.AlignCenter)

        # Circle with number
        circle = QLabel(str(index + 1))
        circle.setFixedSize(32, 32)
        circle.setAlignment(Qt.AlignCenter)
        circle.setObjectName(f"step_circle_{index}")
        layout.addWidget(circle, alignment=Qt.AlignCenter)

        # Short label
        label = QLabel(name)
        label.setAlignment(Qt.AlignCenter)
        label.setObjectName(f"step_label_{index}")
        label.setStyleSheet("font-size: 11px;")
        layout.addWidget(label)

        # Make clickable
        widget.mousePressEvent = lambda e, idx=index: self._on_step_clicked(idx)

        return widget

    def _create_connector(self) -> QFrame:
        """Create a horizontal connector line between steps."""
        connector = QFrame()
        connector.setFixedHeight(2)
        connector.setMinimumWidth(30)
        connector.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        return connector

    def _on_step_clicked(self, index: int):
        """Handle step click."""
        if self._is_destroyed:
            return
        self.step_clicked.emit(index)

    def set_current_step(self, step: int):
        """Set the current active step."""
        self.current_step = step
        self._update_styles()

    def set_completed_steps(self, completed: List[int]):
        """Set the list of completed steps."""
        self.completed_steps = completed
        self._update_styles()

    def _update_styles(self):
        """Update visual styles based on current state."""
        for i, widget in enumerate(self.step_widgets):
            circle = widget.findChild(QLabel, f"step_circle_{i}")
            label = widget.findChild(QLabel, f"step_label_{i}")

            if i == self.current_step:
                # Current step - highlighted
                circle.setStyleSheet("""
                    QLabel {
                        background-color: #2563eb;
                        color: white;
                        border-radius: 16px;
                        font-weight: bold;
                        font-size: 14px;
                    }
                """)
                label.setStyleSheet("font-size: 11px; color: #2563eb; font-weight: bold;")
            elif i in self.completed_steps:
                # Completed step - green with checkmark
                circle.setText("✓")
                circle.setStyleSheet("""
                    QLabel {
                        background-color: #059669;
                        color: white;
                        border-radius: 16px;
                        font-weight: bold;
                        font-size: 14px;
                    }
                """)
                label.setStyleSheet("font-size: 11px; color: #059669;")
            else:
                # Future step - gray
                circle.setText(str(i + 1))
                circle.setStyleSheet("""
                    QLabel {
                        background-color: #e5e7eb;
                        color: #6b7280;
                        border-radius: 16px;
                        font-size: 14px;
                    }
                """)
                label.setStyleSheet("font-size: 11px; color: #6b7280;")

        # Update connector colors
        for i, connector in enumerate(self.connector_widgets):
            if i < self.current_step or i in self.completed_steps:
                connector.setStyleSheet("background-color: #059669;")
            else:
                connector.setStyleSheet("background-color: #e5e7eb;")


class ClaimsWizardWidget(QWidget):
    """
    Main wizard container with step navigation.

    Manages step widgets, navigation buttons, and state persistence.

    Signals:
        status_message: (str, str) - Message and level for status display
        claims_processed: (dict) - Emitted when claims are processed
        wizard_completed: () - Emitted when wizard workflow completes
    """

    status_message = pyqtSignal(str, str)
    claims_processed = pyqtSignal(dict)
    wizard_completed = pyqtSignal()
    # Signal emitted when project context changes (for main dialog to update dropdowns)
    # Emits: (company_id: int, project_id: int)
    project_context_switched = pyqtSignal(int, int)

    STEP_NAMES = [
        "Setup",
        "Layout",
        "Reference",
        "Monument",
        "Adjust",
        "Finalize",
        "Export"
    ]

    def __init__(self, claims_manager: 'ClaimsManager', parent=None):
        """
        Initialize the wizard widget.

        Args:
            claims_manager: ClaimsManager for API calls
            parent: Parent widget
        """
        super().__init__(parent)
        self.claims_manager = claims_manager
        self.logger = PluginLogger.get_logger()
        self.state = ClaimsWizardState()
        self.current_step = 0
        self.step_widgets = []
        self._is_destroyed = False

        self._setup_ui()
        self._create_step_widgets()
        self._connect_signals()

        # Try to restore state from QGIS project
        self.state.load_from_qgis_project()
        self._update_from_state()

        # Activate first step to populate form fields from loaded state
        if self.step_widgets:
            self.step_widgets[0].set_active(True)

    def cleanup(self):
        """Clean up resources before deletion to prevent crashes.

        Called by parent dialog before plugin unload to properly release
        resources and prevent deferred callbacks from crashing.
        """
        self._is_destroyed = True

        # Clean up step indicator
        if hasattr(self, 'step_indicator') and self.step_indicator:
            self.step_indicator.cleanup()

        # Clean up step widgets
        for widget in self.step_widgets:
            if hasattr(widget, 'cleanup'):
                try:
                    widget.cleanup()
                except Exception:
                    pass

    def _setup_ui(self):
        """Set up the wizard UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Step indicator at top
        self.step_indicator = StepIndicator(self.STEP_NAMES)
        self.step_indicator.setStyleSheet("""
            QWidget {
                background-color: #f9fafb;
                border-bottom: 1px solid #e5e7eb;
            }
        """)
        layout.addWidget(self.step_indicator)

        # Stacked widget for step content
        self.stack = QStackedWidget()
        self.stack.setStyleSheet("background-color: white;")
        layout.addWidget(self.stack, 1)

        # Navigation bar at bottom
        nav_bar = self._create_navigation_bar()
        layout.addWidget(nav_bar)

    def _create_navigation_bar(self) -> QWidget:
        """Create the navigation bar with Back/Next buttons."""
        nav = QWidget()
        nav.setStyleSheet("""
            QWidget {
                background-color: #f9fafb;
                border-top: 1px solid #e5e7eb;
            }
        """)
        layout = QHBoxLayout(nav)
        layout.setContentsMargins(16, 12, 16, 12)

        # Cancel/Reset button
        self.reset_btn = QPushButton("Start Over")
        self.reset_btn.setStyleSheet("""
            QPushButton {
                padding: 8px 16px;
                color: #dc2626;
                background-color: transparent;
                border: none;
                font-size: 13px;
            }
            QPushButton:hover {
                text-decoration: underline;
            }
        """)
        self.reset_btn.clicked.connect(self._on_reset_clicked)
        layout.addWidget(self.reset_btn)

        layout.addStretch()

        # Back button
        self.back_btn = QPushButton("Back")
        self.back_btn.setStyleSheet("""
            QPushButton {
                padding: 10px 24px;
                background-color: #ffffff;
                color: #374151;
                border: 1px solid #d1d5db;
                border-radius: 6px;
                font-size: 14px;
                min-width: 100px;
            }
            QPushButton:hover {
                background-color: #f9fafb;
                border-color: #9ca3af;
            }
            QPushButton:disabled {
                background-color: #f3f4f6;
                color: #9ca3af;
            }
        """)
        self.back_btn.clicked.connect(self._on_back_clicked)
        layout.addWidget(self.back_btn)

        # Next/Finish button
        self.next_btn = QPushButton("Next")
        self.next_btn.setStyleSheet("""
            QPushButton {
                padding: 10px 24px;
                background-color: #2563eb;
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: bold;
                font-size: 14px;
                min-width: 100px;
            }
            QPushButton:hover {
                background-color: #1d4ed8;
            }
            QPushButton:disabled {
                background-color: #93c5fd;
            }
        """)
        self.next_btn.clicked.connect(self._on_next_clicked)
        layout.addWidget(self.next_btn)

        return nav

    def _create_step_widgets(self):
        """Create all step widgets."""
        # Import step widgets here to avoid circular imports
        from .claims_step_widgets import (
            ClaimsStep1Widget, ClaimsStep2Widget, ClaimsStep3Widget,
            ClaimsStep4Widget, ClaimsStep5AdjustWidget, ClaimsStep6Widget,
            ClaimsStep7Widget
        )

        step_classes = [
            ClaimsStep1Widget,
            ClaimsStep2Widget,
            ClaimsStep3Widget,
            ClaimsStep4Widget,
            ClaimsStep5AdjustWidget,  # New: Monument Adjustment step
            ClaimsStep6Widget,
            ClaimsStep7Widget,
        ]

        for StepClass in step_classes:
            step = StepClass(self.state, self.claims_manager, self)
            step.status_message.connect(self._on_step_status)
            step.validation_changed.connect(self._update_navigation_buttons)
            step.project_context_switched.connect(self._on_project_context_switched)
            self.step_widgets.append(step)
            self.stack.addWidget(step)

    def _connect_signals(self):
        """Connect signals."""
        self.step_indicator.step_clicked.connect(self._on_step_indicator_clicked)

    def _on_step_status(self, message: str, level: str):
        """Forward status messages from step widgets."""
        self.status_message.emit(message, level)

    def _on_project_context_switched(self, company_id: int, project_id: int):
        """Forward project context switch signal from step widgets."""
        self.project_context_switched.emit(company_id, project_id)

    def _update_from_state(self):
        """Update UI from state (after loading from project)."""
        self.step_indicator.set_completed_steps(self.state.completed_steps)
        self._update_navigation_buttons()

    def _update_navigation_buttons(self):
        """Update Back/Next button states."""
        # Back button - disabled on first step
        self.back_btn.setEnabled(self.current_step > 0)

        # Next button text
        if self.current_step == len(self.step_widgets) - 1:
            self.next_btn.setText("Finish")
        else:
            self.next_btn.setText("Next")

        # Next button enabled based on current step validation
        current_widget = self.step_widgets[self.current_step]
        self.next_btn.setEnabled(current_widget.is_valid())

    def _on_back_clicked(self):
        """Handle Back button click."""
        if self.current_step > 0:
            self.go_to_step(self.current_step - 1)

    def _on_next_clicked(self):
        """Handle Next button click."""
        current_widget = self.step_widgets[self.current_step]

        # Validate current step
        errors = current_widget.validate()
        if errors:
            error_msg = "Please fix the following issues:\n\n" + "\n".join(f"• {e}" for e in errors)
            QMessageBox.warning(self, "Validation Error", error_msg)
            return

        # Save current step state
        current_widget.save_state()
        self.state.mark_step_complete(self.current_step + 1)  # Steps are 1-indexed in state

        # Save to QGIS project
        self.state.save_to_qgis_project()

        # Persist all state to GeoPackage
        if self.state.geopackage_path:
            self.state.save_to_geopackage()

        if self.current_step < len(self.step_widgets) - 1:
            # Go to next step
            self.go_to_step(self.current_step + 1)
        else:
            # Wizard complete
            self._on_wizard_completed()

    def _on_step_indicator_clicked(self, step_index: int):
        """Handle click on step indicator."""
        # Can always go back to completed steps
        # Can only go forward if current step is valid
        if step_index < self.current_step:
            # Going back - always allowed
            self.go_to_step(step_index)
        elif step_index > self.current_step:
            # Going forward - check if current step is valid
            current_widget = self.step_widgets[self.current_step]
            if not current_widget.is_valid():
                errors = current_widget.validate()
                error_msg = "Please complete this step first:\n\n" + "\n".join(f"• {e}" for e in errors)
                QMessageBox.warning(self, "Cannot Skip Ahead", error_msg)
                return

            # Check if all steps between current and target are complete
            for i in range(self.current_step, step_index):
                if (i + 1) not in self.state.completed_steps:  # +1 because state uses 1-indexed
                    QMessageBox.warning(
                        self,
                        "Cannot Skip Ahead",
                        f"Please complete step {i + 1} first."
                    )
                    return

            # Save current step and go to target
            current_widget.save_state()
            self.state.mark_step_complete(self.current_step + 1)
            self.go_to_step(step_index)

    def _on_reset_clicked(self):
        """Handle reset/start over button click."""
        reply = QMessageBox.question(
            self,
            "Start Over?",
            "This will clear all claim data and start a new claims project.\n\n"
            "Are you sure you want to start over?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            self.reset_wizard()

    def _on_wizard_completed(self):
        """Handle wizard completion."""
        self.status_message.emit("Claims workflow complete!", "success")
        self.wizard_completed.emit()

        QMessageBox.information(
            self,
            "Claims Complete",
            "Congratulations! Your claims workflow is complete.\n\n"
            "You can:\n"
            "• Go back to review any step\n"
            "• Export additional documents\n"
            "• Start a new claims project"
        )

    def go_to_step(self, step_index: int) -> bool:
        """
        Navigate to a specific step.

        Args:
            step_index: Target step index (0-based)

        Returns:
            True if navigation successful
        """
        if step_index < 0 or step_index >= len(self.step_widgets):
            return False

        # Leave current step
        if self.step_widgets:
            self.step_widgets[self.current_step].set_active(False)

        # Update current step
        self.current_step = step_index
        self.stack.setCurrentIndex(step_index)

        # Enter new step
        self.step_widgets[step_index].set_active(True)

        # Update UI
        self.step_indicator.set_current_step(step_index)
        self.step_indicator.set_completed_steps(self.state.completed_steps)
        self._update_navigation_buttons()

        return True

    def reset_wizard(self):
        """Reset the wizard to initial state."""
        # Reset state
        self.state.reset()

        # Reset all step widgets
        for step in self.step_widgets:
            step.load_state()

        # Go to first step
        self.current_step = 0
        self.stack.setCurrentIndex(0)
        self.step_widgets[0].set_active(True)

        # Update UI
        self.step_indicator.set_current_step(0)
        self.step_indicator.set_completed_steps([])
        self._update_navigation_buttons()

        self.status_message.emit("Wizard reset - starting fresh", "info")

    def set_project(self, project_id: int, company_id: int):
        """
        Set the current project context.

        Args:
            project_id: Project ID
            company_id: Company ID
        """
        self.state.project_id = project_id
        self.state.company_id = company_id

        # Refresh first step if it's active
        if self.current_step == 0 and self.step_widgets:
            self.step_widgets[0].on_enter()

    def refresh(self):
        """Refresh the current step."""
        if self.step_widgets and 0 <= self.current_step < len(self.step_widgets):
            self.step_widgets[self.current_step].on_enter()
