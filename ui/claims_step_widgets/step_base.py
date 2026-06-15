# -*- coding: utf-8 -*-
"""
Base class for claims wizard step widgets.

Provides common interface and utility methods for all step widgets.
"""
from typing import List, TYPE_CHECKING
from abc import abstractmethod

from qgis.PyQt.QtWidgets import QWidget, QVBoxLayout, QLabel
from qgis.PyQt.QtCore import pyqtSignal

from ...utils.theme import T

if TYPE_CHECKING:
    from ..claims_wizard_state import ClaimsWizardState
    from ...managers.claims_manager import ClaimsManager


class ClaimsStepBase(QWidget):
    """
    Abstract base class for wizard step widgets.

    Each step widget must implement:
    - get_step_title(): Return the step title
    - get_step_description(): Return instructions for this step
    - validate(): Check if step is complete and return errors
    - on_enter(): Called when step becomes active
    - on_leave(): Called when leaving step
    - save_state(): Save widget state to shared state object
    - load_state(): Load widget state from shared state object

    Signals:
        validation_changed: Emitted when step validity changes
        step_completed: Emitted when step is marked complete
        status_message: Emitted to show status in main dialog (message, level)
    """

    validation_changed = pyqtSignal()
    step_completed = pyqtSignal()
    status_message = pyqtSignal(str, str)  # (message, level: 'info'/'warning'/'error')
    # Signal emitted when project context changes (for main dialog to update dropdowns)
    # Emits: (company_id: int, project_id: int)
    project_context_switched = pyqtSignal(int, int)
    # Signal emitted when access level is determined (Step 1 license check)
    # Emits: (access_info: dict) - the wizard uses this to rebuild steps
    access_level_changed = pyqtSignal(dict)

    def __init__(
        self,
        state: 'ClaimsWizardState',
        claims_manager: 'ClaimsManager',
        parent=None,
        data_manager=None
    ):
        """
        Initialize the step widget.

        Args:
            state: Shared ClaimsWizardState object
            claims_manager: ClaimsManager for API calls
            parent: Parent widget
            data_manager: Optional DataManager for file operations
        """
        super().__init__(parent)
        self.state = state
        self.claims_manager = claims_manager
        self.data_manager = data_manager
        self._is_active = False
        self._is_destroyed = False  # Flag to prevent crashes after unload

    def cleanup(self):
        """Clean up resources before deletion to prevent crashes.

        Called by parent wizard when plugin is being unloaded.
        Subclasses can override to add their own cleanup.
        """
        self._is_destroyed = True

    @abstractmethod
    def get_step_title(self) -> str:
        """
        Get the step title for display.

        Returns:
            Step title string (e.g., "Project Setup")
        """

    @abstractmethod
    def get_step_description(self) -> str:
        """
        Get the step description/instructions.

        Returns:
            Description string explaining what to do in this step
        """

    @abstractmethod
    def validate(self) -> List[str]:
        """
        Validate the current step state.

        Returns:
            List of error messages (empty list if valid)
        """

    def is_valid(self) -> bool:
        """
        Check if the step is currently valid.

        Returns:
            True if step passes validation
        """
        return len(self.validate()) == 0

    @abstractmethod
    def on_enter(self):
        """
        Called when this step becomes the active step.

        Use this to refresh UI, load data, etc.
        """

    @abstractmethod
    def on_leave(self):
        """
        Called when leaving this step (going to another step).

        Use this to save state, clean up, etc.
        """

    @abstractmethod
    def save_state(self):
        """
        Save widget state to the shared ClaimsWizardState.

        Called by on_leave() and when state needs to be persisted.
        """

    @abstractmethod
    def load_state(self):
        """
        Load widget state from the shared ClaimsWizardState.

        Called by on_enter() to populate UI from state.
        """

    def set_active(self, active: bool):
        """
        Set whether this step is the active step.

        Args:
            active: True if this step is now active
        """
        was_active = self._is_active
        self._is_active = active

        if active and not was_active:
            self.on_enter()
        elif not active and was_active:
            self.on_leave()

    def emit_validation_changed(self):
        """Emit the validation_changed signal."""
        self.validation_changed.emit()

    def emit_status(self, message: str, level: str = 'info'):
        """
        Emit a status message.

        Args:
            message: Message text
            level: One of 'info', 'warning', 'error', 'success'
        """
        self.status_message.emit(message, level)

    # =========================================================================
    # Common Styles (shared across all steps)
    # =========================================================================

    def _get_group_style(self) -> str:
        """Get group box style."""
        return f"""
            QGroupBox {{
                font-weight: bold;
                border: 1px solid {T.BORDER_SUBTLE};
                border-radius: 8px;
                margin-top: 12px;
                padding-top: 16px;
                background-color: {T.SURFACE};
            }}
            QGroupBox::title {{
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 8px;
                color: {T.TEXT_PRIMARY};
            }}
        """

    def _get_primary_button_style(self) -> str:
        """Get primary button style."""
        return f"""
            QPushButton {{
                padding: 10px 20px;
                background-color: {T.ACCENT};
                color: {T.TEXT_ON_ACCENT};
                border: none;
                border-radius: 6px;
                font-weight: bold;
                font-size: 14px;
                min-width: 120px;
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
        """Get secondary button style."""
        return f"""
            QPushButton {{
                padding: 8px 16px;
                background-color: {T.SURFACE};
                color: {T.TEXT_PRIMARY};
                border: 1px solid {T.BORDER};
                border-radius: 6px;
                font-size: 13px;
            }}
            QPushButton:hover {{
                background-color: {T.SURFACE_SUBTLE};
                border-color: {T.TEXT_FAINT};
            }}
            QPushButton:pressed {{
                background-color: {T.SURFACE_SUNKEN};
            }}
            QPushButton:disabled {{
                background-color: {T.SURFACE_SUNKEN};
                color: {T.TEXT_FAINT};
            }}
        """

    def _get_success_button_style(self) -> str:
        """Get success button style (green)."""
        return f"""
            QPushButton {{
                padding: 8px 16px;
                background-color: {T.SUCCESS};
                color: {T.TEXT_ON_ACCENT};
                border: none;
                border-radius: 6px;
                font-weight: bold;
                font-size: 13px;
            }}
            QPushButton:hover {{
                background-color: {T.SUCCESS_TEXT};
            }}
            QPushButton:pressed {{
                background-color: {T.SUCCESS_TEXT};
            }}
            QPushButton:disabled {{
                background-color: {T.SUCCESS_BG};
                color: {T.TEXT_MUTED};
            }}
        """

    def _get_staff_button_style(self) -> str:
        """Get staff-specific button style (purple)."""
        # Purple backgrounds (#7c3aed / #6d28d9 / #5b21b6) are left as raw hex:
        # they are the staff/category purple swatch the brief lists in the
        # leave-alone set, and theme.py exposes no purple token. Only the text
        # color (text-on-accent) is tokenized.
        return f"""
            QPushButton {{
                padding: 10px 16px;
                background-color: #7c3aed;
                color: {T.TEXT_ON_ACCENT};
                border: none;
                border-radius: 6px;
                font-weight: bold;
                font-size: 13px;
            }}
            QPushButton:hover {{
                background-color: #6d28d9;
            }}
            QPushButton:pressed {{
                background-color: #5b21b6;
            }}
        """

    def _get_input_style(self) -> str:
        """Get line edit / input style."""
        return f"""
            QLineEdit, QSpinBox, QDoubleSpinBox {{
                padding: 8px 12px;
                border: 1px solid {T.BORDER};
                border-radius: 6px;
                background-color: {T.INPUT_BG};
                color: {T.TEXT_PRIMARY};
                font-size: 13px;
            }}
            QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
                border-color: {T.ACCENT};
            }}
        """

    def _get_combo_style(self) -> str:
        """Get combo box style."""
        return f"""
            QComboBox {{
                padding: 8px 12px;
                border: 1px solid {T.BORDER};
                border-radius: 6px;
                background-color: {T.INPUT_BG};
                color: {T.TEXT_PRIMARY};
                font-size: 13px;
            }}
            QComboBox:focus {{
                border-color: {T.ACCENT};
            }}
            QComboBox::drop-down {{
                border: none;
                width: 30px;
            }}
            QComboBox QAbstractItemView {{
                background-color: {T.INPUT_BG};
                color: {T.TEXT_PRIMARY};
                border: 1px solid {T.BORDER};
                selection-background-color: {T.ACCENT};
                selection-color: {T.TEXT_ON_ACCENT};
            }}
            QComboBox QAbstractItemView::item {{
                padding: 6px 12px;
                color: {T.TEXT_PRIMARY};
            }}
            QComboBox QAbstractItemView::item:hover {{
                background-color: {T.INFO_BG};
                color: {T.ACCENT_HOVER};
            }}
        """

    def _get_info_label_style(self) -> str:
        """Get style for informational labels."""
        return f"color: {T.TEXT_MUTED}; font-size: 12px;"

    def _get_error_label_style(self) -> str:
        """Get style for error labels."""
        return f"color: {T.DANGER}; font-size: 12px;"

    def _get_success_label_style(self) -> str:
        """Get style for success labels."""
        return f"color: {T.SUCCESS}; font-size: 12px;"

    def _create_header(self) -> QWidget:
        """
        Create a standard header with title and description.

        Returns:
            QWidget containing header layout
        """
        header = QWidget()
        layout = QVBoxLayout(header)
        layout.setContentsMargins(0, 0, 0, 16)
        layout.setSpacing(8)

        title = QLabel(self.get_step_title())
        title.setStyleSheet(f"font-size: 18px; font-weight: bold; color: {T.TEXT_STRONG};")
        layout.addWidget(title)

        desc = QLabel(self.get_step_description())
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color: {T.TEXT_MUTED}; font-size: 13px;")
        layout.addWidget(desc)

        return header
