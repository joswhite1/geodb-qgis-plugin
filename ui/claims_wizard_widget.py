# -*- coding: utf-8 -*-
"""
Claims wizard main container widget.

Provides step navigation, step indicator, and manages step widget transitions.
"""
from typing import List, TYPE_CHECKING

from qgis.PyQt.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QStackedWidget, QFrame, QMessageBox, QSizePolicy
)
from qgis.PyQt.QtCore import Qt, QUrl, pyqtSignal
from qgis.PyQt.QtGui import QDesktopServices
from qgis.core import QgsProject

from .claims_wizard_state import ClaimsWizardState
from ..utils.logger import PluginLogger
from ..utils.compat import QSizePolicy_Expanding, QSizePolicy_Fixed

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
        widget.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Circle with number
        circle = QLabel(str(index + 1))
        circle.setFixedSize(32, 32)
        circle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        circle.setObjectName(f"step_circle_{index}")
        layout.addWidget(circle, alignment=Qt.AlignmentFlag.AlignCenter)

        # Short label
        label = QLabel(name)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
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
        connector.setSizePolicy(QSizePolicy_Expanding, QSizePolicy_Fixed)
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
        """Set the list of completed steps.

        Args:
            completed: List of completed step indices (0-based).
        """
        self.completed_steps = completed
        self._update_styles()

    def _update_styles(self):
        """Update visual styles based on current state."""
        for i, widget in enumerate(self.step_widgets):
            circle = widget.findChild(QLabel, f"step_circle_{i}")
            label = widget.findChild(QLabel, f"step_label_{i}")

            if i == self.current_step:
                # Current step - highlighted (restore number in case it was a checkmark)
                circle.setText(str(i + 1))
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
        # Connector i sits between step i and step i+1; it should be green
        # if the step to its right is completed or is the current step.
        for i, connector in enumerate(self.connector_widgets):
            right_step = i + 1
            if right_step <= self.current_step or right_step in self.completed_steps:
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

    # Enterprise/Staff steps (full processing workflow)
    ENTERPRISE_STEP_NAMES = [
        "Setup",
        "Layout",
        "Reference",
        "Monument",
        "Adjust",
        "Finalize",
        "Export"
    ]

    # Pay-per-claim steps (simplified: setup, layout, purchase)
    PAY_PER_CLAIM_STEP_NAMES = [
        "Setup",
        "Layout",
        "Order"
    ]

    # Default to enterprise steps (rebuilt after access check)
    STEP_NAMES = ENTERPRISE_STEP_NAMES

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

        # Auto-resume to the appropriate step, or start at step 1
        self._auto_resume()

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

    def _completed_steps_for_indicator(self) -> List[int]:
        """Convert 1-indexed state completed_steps to 0-indexed for the indicator."""
        return [s - 1 for s in self.state.completed_steps if s >= 1]

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
        """Create all step widgets (default enterprise/staff flow)."""
        self._create_enterprise_steps()

    def _create_enterprise_steps(self):
        """Create the full enterprise/staff step sequence."""
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
            ClaimsStep5AdjustWidget,
            ClaimsStep6Widget,
            ClaimsStep7Widget,
        ]

        for StepClass in step_classes:
            step = StepClass(self.state, self.claims_manager, self)
            self._connect_step_signals(step)
            self.step_widgets.append(step)
            self.stack.addWidget(step)

    def _connect_step_signals(self, step):
        """Connect common signals for a step widget."""
        step.status_message.connect(self._on_step_status)
        step.validation_changed.connect(self._update_navigation_buttons)
        step.project_context_switched.connect(self._on_project_context_switched)

        # Wire up access_level_changed from Step 1 to rebuild wizard steps
        step.access_level_changed.connect(self._on_access_level_changed)

        # Wire up processing_completed from Step 6 to emit claims_processed
        if hasattr(step, 'processing_completed'):
            step.processing_completed.connect(self._on_claims_processing_completed)

    def _connect_signals(self):
        """Connect signals."""
        self.step_indicator.step_clicked.connect(self._on_step_indicator_clicked)

    def _on_step_status(self, message: str, level: str):
        """Forward status messages from step widgets."""
        self.status_message.emit(message, level)

    def _on_project_context_switched(self, company_id: int, project_id: int):
        """Forward project context switch signal from step widgets."""
        self.project_context_switched.emit(company_id, project_id)

    def _on_claims_processing_completed(self, result: dict):
        """Forward claims processing result from Step 6."""
        self.claims_processed.emit(result)

    def _on_access_level_changed(self, access_info: dict):
        """
        Handle access level determination from Step 1.

        Rebuilds the wizard steps based on whether the user can process
        claims immediately (enterprise/staff) or needs to purchase them
        (pay-per-claim).
        """
        self._rebuild_steps_for_access(access_info)

    def _rebuild_steps_for_access(self, access_info: dict):
        """
        Rebuild wizard steps based on user's access level.

        If user can process immediately (enterprise/staff), keep full 7-step flow.
        If pay-per-claim, rebuild with only 3 steps: Setup, Layout, Order.

        Args:
            access_info: Dict from check_access() with access_type, can_process_immediately, etc.
        """
        can_process = access_info.get('can_process_immediately', False)

        if can_process:
            target_step_names = self.ENTERPRISE_STEP_NAMES
        else:
            target_step_names = self.PAY_PER_CLAIM_STEP_NAMES

        # Check if we need to rebuild (avoid unnecessary rebuilds)
        if self.STEP_NAMES == target_step_names and len(self.step_widgets) > 0:
            return

        self.logger.info(
            f"[WIZARD] Rebuilding steps for access: "
            f"can_process_immediately={can_process}, "
            f"steps={'enterprise' if can_process else 'pay-per-claim'}"
        )

        # Save Step 1 reference before clearing (we'll preserve it)
        step1_widget = self.step_widgets[0] if self.step_widgets else None
        step2_widget = self.step_widgets[1] if len(self.step_widgets) > 1 else None

        # Clean up existing step widgets (except step 1 and 2 which we reuse)
        for i in range(len(self.step_widgets) - 1, 1, -1):
            widget = self.step_widgets[i]
            if hasattr(widget, 'cleanup'):
                try:
                    widget.cleanup()
                except Exception:
                    pass
            self.stack.removeWidget(widget)
            widget.deleteLater()

        # Keep step 1 and step 2, clear the rest from the list
        self.step_widgets = self.step_widgets[:2]

        # Update step names
        self.STEP_NAMES = target_step_names

        if can_process:
            # Enterprise/Staff: add remaining steps 3-7
            from .claims_step_widgets import (
                ClaimsStep3Widget, ClaimsStep4Widget,
                ClaimsStep5AdjustWidget, ClaimsStep6Widget, ClaimsStep7Widget
            )
            remaining_classes = [
                ClaimsStep3Widget,
                ClaimsStep4Widget,
                ClaimsStep5AdjustWidget,
                ClaimsStep6Widget,
                ClaimsStep7Widget,
            ]
        else:
            # Pay-per-claim: add only the Order step
            from .claims_step_widgets import ClaimsStep3OrderWidget
            remaining_classes = [
                ClaimsStep3OrderWidget,
            ]

        for StepClass in remaining_classes:
            step = StepClass(self.state, self.claims_manager, self)
            self._connect_step_signals(step)
            self.step_widgets.append(step)
            self.stack.addWidget(step)

        # Rebuild the step indicator with new step names
        old_indicator = self.step_indicator
        self.step_indicator = StepIndicator(self.STEP_NAMES)
        self.step_indicator.setStyleSheet("""
            QWidget {
                background-color: #f9fafb;
                border-bottom: 1px solid #e5e7eb;
            }
        """)

        # Replace old indicator in layout
        main_layout = self.layout()
        main_layout.replaceWidget(old_indicator, self.step_indicator)
        old_indicator.cleanup()
        old_indicator.deleteLater()

        # Reconnect step indicator signal
        self.step_indicator.step_clicked.connect(self._on_step_indicator_clicked)

        # Reset to current step (stay on step 1 if we're there)
        current = min(self.current_step, len(self.step_widgets) - 1)
        self.current_step = current
        self.stack.setCurrentIndex(current)

        # Update UI
        self.step_indicator.set_current_step(current)
        # Convert 1-indexed state to 0-indexed for indicator, filter to valid range
        valid_completed = [s - 1 for s in self.state.completed_steps
                          if s >= 1 and s <= len(self.step_widgets)]
        self.step_indicator.set_completed_steps(valid_completed)
        self._update_navigation_buttons()

        self.logger.info(
            f"[WIZARD] Steps rebuilt: {len(self.step_widgets)} steps "
            f"({', '.join(self.STEP_NAMES)})"
        )

    def _update_from_state(self):
        """Update UI from state (after loading from project)."""
        self.step_indicator.set_completed_steps(self._completed_steps_for_indicator())
        self._update_navigation_buttons()

    def _auto_resume(self):
        """
        Auto-resume to the appropriate step when the wizard opens.

        If there are completed steps from a previous session, offer to
        resume where the user left off. Otherwise start at step 1.
        """
        if not self.step_widgets:
            return

        completed = sorted(self.state.completed_steps)
        if not completed:
            # No previous progress — start at step 1
            self.step_widgets[0].set_active(True)
            return

        # Find the first incomplete step (the next one to work on)
        # completed_steps are 1-indexed
        num_steps = len(self.step_widgets)
        resume_step = 0  # 0-indexed, default to step 1
        for step_num in range(1, num_steps + 1):
            if step_num not in self.state.completed_steps:
                resume_step = step_num - 1  # Convert to 0-indexed
                break
        else:
            # All steps completed — go to the last step
            resume_step = num_steps - 1

        # Build a description of progress
        prefix = self.state.grid_name_prefix or ""
        project_desc = f" ({prefix} Lode Claims)" if prefix else ""

        if resume_step == 0:
            # Only step 1 is incomplete, just start there
            self.step_widgets[0].set_active(True)
            return

        # All steps complete — offer to review or start over
        if all(s in self.state.completed_steps for s in range(1, num_steps + 1)):
            reply = QMessageBox.question(
                self,
                "Resume Claims Project",
                f"Previous claims project{project_desc} is complete "
                f"(all {num_steps} steps finished).\n\n"
                "Would you like to review from Step 1, or continue "
                "from the last step?\n\n"
                "You can also use the step indicators to jump to any step.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes
            )
            if reply == QMessageBox.StandardButton.Yes:
                # "Yes" = continue from last step
                self.go_to_step(resume_step)
            else:
                # "No" = start from step 1
                self.step_widgets[0].set_active(True)
            return

        # Partially complete — offer to resume at the next incomplete step
        resume_name = self.STEP_NAMES[resume_step] if resume_step < len(self.STEP_NAMES) else f"Step {resume_step + 1}"
        completed_names = []
        for s in sorted(completed):
            idx = s - 1
            if idx < len(self.STEP_NAMES):
                completed_names.append(f"  Step {s}: {self.STEP_NAMES[idx]}")

        reply = QMessageBox.question(
            self,
            "Resume Claims Project",
            f"Found previous claims project{project_desc} with progress:\n\n"
            + "\n".join(completed_names)
            + f"\n\nResume at Step {resume_step + 1}: {resume_name}?\n\n"
            "Choose Yes to resume, or No to start from Step 1.\n"
            "You can also use the step indicators to jump to any step.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes
        )

        if reply == QMessageBox.StandardButton.Yes:
            self.go_to_step(resume_step)
        else:
            # Start from step 1 — don't invalidate completed steps,
            # user can still jump forward via step indicators
            self.step_widgets[0].set_active(True)

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
            target = self.current_step - 1

            # Check if going back would invalidate completed downstream steps
            steps_to_invalidate = [s for s in self.state.completed_steps if s >= target + 2]
            if steps_to_invalidate:
                step_names = []
                for s in sorted(steps_to_invalidate):
                    idx = s - 1  # Convert to 0-indexed
                    if idx < len(self.STEP_NAMES):
                        step_names.append(f"Step {s}: {self.STEP_NAMES[idx]}")
                reply = QMessageBox.question(
                    self,
                    "Go Back?",
                    f"Going back will invalidate:\n\n"
                    + "\n".join(f"• {n}" for n in step_names)
                    + "\n\nYou'll need to redo these steps. Continue?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return

            # Save current step state before leaving
            current_widget = self.step_widgets[self.current_step]
            current_widget.save_state()

            # Persist to project/GeoPackage (same as Next button)
            self.state.save_to_qgis_project()
            if self.state.geopackage_path:
                self.state.save_to_geopackage()

            # Invalidate all steps after the target step.
            # Going back means the user may change something, so downstream
            # steps that depended on previous input are no longer valid.
            self.state.mark_step_incomplete(target + 2)  # 1-indexed, invalidate from step after target

            self.go_to_step(target)

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
            # Check if going back would invalidate any completed downstream steps
            steps_to_invalidate = [s for s in self.state.completed_steps if s >= step_index + 2]
            if steps_to_invalidate:
                step_names = []
                for s in sorted(steps_to_invalidate):
                    idx = s - 1  # Convert to 0-indexed
                    if idx < len(self.STEP_NAMES):
                        step_names.append(f"Step {s}: {self.STEP_NAMES[idx]}")
                reply = QMessageBox.question(
                    self,
                    "Go Back?",
                    f"Going back to Step {step_index + 1} will invalidate:\n\n"
                    + "\n".join(f"• {n}" for n in step_names)
                    + "\n\nYou'll need to redo these steps. Continue?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.No
                )
                if reply != QMessageBox.StandardButton.Yes:
                    return

            # Going back - save current state and invalidate downstream steps
            current_widget = self.step_widgets[self.current_step]
            current_widget.save_state()
            self.state.save_to_qgis_project()
            if self.state.geopackage_path:
                self.state.save_to_geopackage()
            self.state.mark_step_incomplete(step_index + 2)  # 1-indexed, invalidate from step after target
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
            self.state.save_to_qgis_project()
            if self.state.geopackage_path:
                self.state.save_to_geopackage()
            self.go_to_step(step_index)

    def _on_reset_clicked(self):
        """Handle reset/start over button click."""
        reply = QMessageBox.question(
            self,
            "Start Over?",
            "This will clear all claim data and start a new claims project.\n\n"
            "Are you sure you want to start over?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            self.reset_wizard()

    def _on_wizard_completed(self):
        """Handle wizard completion — open Claim Packages page and return to Sync Data."""
        self.status_message.emit("Claims workflow complete!", "success")

        # Open the Claim Packages page so the user can download documents
        QDesktopServices.openUrl(QUrl("https://geodb.io/geodata/claim-packages/"))

        QMessageBox.information(
            self,
            "Claims Complete",
            "Congratulations! Your claims workflow is complete.\n\n"
            "Your Claim Packages page has been opened in the browser."
        )

        self.wizard_completed.emit()

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
        self.step_indicator.set_completed_steps(self._completed_steps_for_indicator())
        self._update_navigation_buttons()

        return True

    def reset_wizard(self):
        """Reset the wizard to initial state."""
        # Remove old generated layers from project before resetting state
        # Step 5 (index 4) holds generated layers that need cleanup
        if len(self.step_widgets) > 4:
            step5 = self.step_widgets[4]
            if hasattr(step5, '_remove_old_generated_layers'):
                step5._remove_old_generated_layers()

        # Remove old waypoints layer from Step 6 if it exists
        if self.state.waypoints_layer_id:
            try:
                QgsProject.instance().removeMapLayer(self.state.waypoints_layer_id)
            except Exception:
                pass

        # Remove the "Claims Workflow" layer group from the layer tree
        self._remove_claims_layer_group()

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

    def _remove_claims_layer_group(self):
        """Remove the 'Claims Workflow' layer group from the QGIS layer tree."""
        try:
            root = QgsProject.instance().layerTreeRoot()
            for child in root.children():
                if hasattr(child, 'name') and child.name().startswith("Claims Workflow"):
                    root.removeChildNode(child)
                    break
        except Exception:
            pass

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
