# -*- coding: utf-8 -*-
"""
Login dialog for geodb.io authentication.

Provides a clean, user-friendly login interface with:
- Email and password fields in a single dialog
- Remember me option
- Loading state during authentication
- Clear error messages
- Two-factor authentication (2FA) support
"""
from typing import Optional, Tuple
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QCheckBox, QFrame
)
from qgis.PyQt.QtCore import Qt, pyqtSignal, QDateTime
from qgis.PyQt.QtGui import QFont

from .two_factor_dialog import TwoFactorDialog
from ..utils.compat import Qt_AlignCenter, QFrame_HLine, QLineEdit_Password, QDialog_Accepted
from ..utils.theme import T


class LoginDialog(QDialog):
    """
    Modern login dialog for geodb.io authentication.

    Signals:
        login_successful: Emitted when login succeeds, with (token, user_context) tuple
        login_cancelled: Emitted when user cancels login
    """

    login_successful = pyqtSignal(str, dict)  # token, user_context
    login_cancelled = pyqtSignal()

    def __init__(self, parent=None, auth_manager=None):
        """
        Initialize login dialog.

        Args:
            parent: Parent widget
            auth_manager: AuthManager instance for handling authentication
        """
        super().__init__(parent)
        self.auth_manager = auth_manager
        self._is_loading = False
        self._failed_attempts = 0
        self._lockout_until = None

        self._setup_ui()
        self._load_saved_credentials()

    def _setup_ui(self):
        """Set up the dialog UI."""
        self.setWindowTitle("Login to geodb.io")
        self.setFixedWidth(400)
        self.setModal(True)
        # Theme the dialog surface so the card reads correctly in both light
        # and dark mode (otherwise the host's dark window shows behind the
        # light input boxes).
        self.setStyleSheet(
            f"LoginDialog {{ background-color: {T.SURFACE}; }}"
        )

        # Main layout
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(32, 32, 32, 32)

        # Header
        header_label = QLabel("geodb.io")
        header_font = QFont()
        header_font.setPointSize(24)
        header_font.setBold(True)
        header_label.setFont(header_font)
        header_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        header_label.setStyleSheet(f"color: {T.ACCENT_TEXT};")
        layout.addWidget(header_label)

        # Subtitle
        subtitle_label = QLabel("Sign in to sync your geological data")
        subtitle_label.setAlignment(Qt_AlignCenter)
        subtitle_label.setStyleSheet(f"color: {T.TEXT_MUTED}; margin-bottom: 16px;")
        layout.addWidget(subtitle_label)

        # Separator line
        line = QFrame()
        line.setFrameShape(QFrame_HLine)
        line.setStyleSheet(f"background-color: {T.BORDER_SUBTLE};")
        layout.addWidget(line)

        layout.addSpacing(8)

        # Email field
        email_label = QLabel("Email")
        email_label.setStyleSheet(f"font-weight: bold; color: {T.TEXT_PRIMARY};")
        layout.addWidget(email_label)

        self.email_input = QLineEdit()
        self.email_input.setPlaceholderText("you@example.com")
        self.email_input.setStyleSheet(self._get_input_style())
        self.email_input.returnPressed.connect(self._focus_password)
        layout.addWidget(self.email_input)

        layout.addSpacing(8)

        # Password field
        password_label = QLabel("Password")
        password_label.setStyleSheet(f"font-weight: bold; color: {T.TEXT_PRIMARY};")
        layout.addWidget(password_label)

        self.password_input = QLineEdit()
        self.password_input.setPlaceholderText("Enter your password")
        self.password_input.setEchoMode(QLineEdit_Password)
        self.password_input.setStyleSheet(self._get_input_style())
        self.password_input.returnPressed.connect(self._on_login_clicked)
        layout.addWidget(self.password_input)

        layout.addSpacing(8)

        # Remember me checkbox
        self.remember_checkbox = QCheckBox("Remember my email")
        self.remember_checkbox.setStyleSheet(f"color: {T.TEXT_MUTED};")
        layout.addWidget(self.remember_checkbox)

        # Save password checkbox
        self.save_password_checkbox = QCheckBox("Save password (stored securely in QGIS)")
        self.save_password_checkbox.setStyleSheet(f"color: {T.TEXT_MUTED};")
        layout.addWidget(self.save_password_checkbox)

        layout.addSpacing(16)

        # Error message label (hidden by default)
        self.error_label = QLabel()
        self.error_label.setStyleSheet(f"""
            color: {T.DANGER_TEXT};
            background-color: {T.DANGER_BG};
            border: 1px solid {T.DANGER};
            border-radius: 6px;
            padding: 8px 12px;
        """)
        self.error_label.setWordWrap(True)
        self.error_label.hide()
        layout.addWidget(self.error_label)

        # Buttons
        button_layout = QHBoxLayout()
        button_layout.setSpacing(12)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setStyleSheet(self._get_secondary_button_style())
        self.cancel_button.clicked.connect(self._on_cancel_clicked)
        button_layout.addWidget(self.cancel_button)

        self.login_button = QPushButton("Sign In")
        self.login_button.setStyleSheet(self._get_primary_button_style())
        self.login_button.clicked.connect(self._on_login_clicked)
        self.login_button.setDefault(True)
        button_layout.addWidget(self.login_button)

        layout.addLayout(button_layout)

        # Footer
        layout.addSpacing(16)
        footer_label = QLabel("Don't have an account? Visit geodb.io to sign up.")
        footer_label.setAlignment(Qt_AlignCenter)
        footer_label.setStyleSheet(f"color: {T.TEXT_FAINT}; font-size: 11px;")
        layout.addWidget(footer_label)

    def _get_input_style(self) -> str:
        """Get stylesheet for input fields."""
        return f"""
            QLineEdit {{
                padding: 10px 12px;
                border: 1px solid {T.BORDER};
                border-radius: 6px;
                background-color: {T.INPUT_BG};
                color: {T.TEXT_PRIMARY};
                font-size: 14px;
            }}
            QLineEdit:focus {{
                border-color: {T.ACCENT};
                outline: none;
            }}
            QLineEdit:disabled {{
                background-color: {T.INPUT_BG_DISABLED};
                color: {T.TEXT_FAINT};
            }}
        """

    def _get_input_error_style(self) -> str:
        """Get stylesheet for input fields in error state."""
        return f"""
            QLineEdit {{
                padding: 10px 12px;
                border: 2px solid {T.DANGER};
                border-radius: 6px;
                background-color: {T.DANGER_BG};
                color: {T.TEXT_PRIMARY};
                font-size: 14px;
            }}
            QLineEdit:focus {{
                border-color: {T.DANGER};
                outline: none;
            }}
            QLineEdit:disabled {{
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
                font-size: 14px;
                min-width: 100px;
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
                font-size: 14px;
                min-width: 100px;
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

    def _focus_password(self):
        """Move focus to password field."""
        self.password_input.setFocus()

    def _load_saved_credentials(self):
        """Load saved email and password from settings if available."""
        if self.auth_manager:
            # Load saved email
            saved_email = self.auth_manager.get_saved_email()
            if saved_email:
                self.email_input.setText(saved_email)
                self.remember_checkbox.setChecked(True)

            # Load saved password (from QGIS Auth Manager)
            saved_password = self.auth_manager.get_saved_password()
            if saved_password:
                self.password_input.setText(saved_password)
                self.save_password_checkbox.setChecked(True)

            # Focus appropriate field
            if saved_email and not saved_password:
                self.password_input.setFocus()
            elif not saved_email:
                self.email_input.setFocus()

    def _set_loading(self, loading: bool):
        """Set loading state of the dialog."""
        self._is_loading = loading

        self.email_input.setEnabled(not loading)
        self.password_input.setEnabled(not loading)
        self.remember_checkbox.setEnabled(not loading)
        self.save_password_checkbox.setEnabled(not loading)
        self.login_button.setEnabled(not loading)
        self.cancel_button.setEnabled(not loading)

        if loading:
            self.login_button.setText("Signing in...")
            # Note: Removed QApplication.processEvents() to prevent heap corruption crashes
        else:
            self.login_button.setText("Sign In")

    def _show_error(self, message: str, field: str = None):
        """Display error message and optionally highlight the offending field."""
        self.error_label.setText(message)
        self.error_label.show()

        # Highlight the relevant field with red border
        if field == 'password':
            self.password_input.setStyleSheet(self._get_input_error_style())
            self.password_input.setFocus()
        elif field == 'email':
            self.email_input.setStyleSheet(self._get_input_error_style())
            self.email_input.setFocus()

    def _hide_error(self):
        """Hide error message and reset field styles."""
        self.error_label.hide()
        self.email_input.setStyleSheet(self._get_input_style())
        self.password_input.setStyleSheet(self._get_input_style())

    def _validate_inputs(self) -> bool:
        """Validate email and password inputs."""
        email = self.email_input.text().strip()
        password = self.password_input.text()

        if not email:
            self._show_error("Please enter your email address.", field='email')
            return False

        if '@' not in email or '.' not in email:
            self._show_error("Please enter a valid email address.", field='email')
            return False

        if not password:
            self._show_error("Please enter your password.", field='password')
            return False

        return True

    def _on_login_clicked(self):
        """Handle login button click."""
        self._hide_error()

        # Check lockout from repeated failed attempts
        if self._lockout_until and QDateTime.currentDateTime() < self._lockout_until:
            remaining = QDateTime.currentDateTime().secsTo(self._lockout_until)
            self._show_error(f"Too many failed attempts. Try again in {remaining}s.")
            return

        if not self._validate_inputs():
            return

        email = self.email_input.text().strip()
        password = self.password_input.text()

        # Clear password from widget immediately (captured in local variable)
        self.password_input.clear()

        self._set_loading(True)

        try:
            if self.auth_manager:
                # Save email if remember me is checked
                if self.remember_checkbox.isChecked():
                    self.auth_manager.save_email(email)
                else:
                    self.auth_manager.clear_saved_email()

                # Attempt login
                success, result = self.auth_manager.login(
                    email,
                    password,
                    save_password=self.save_password_checkbox.isChecked()
                )

                if success:
                    # Reset throttle on success
                    self._failed_attempts = 0
                    self._lockout_until = None
                    token = result.get('token', '')
                    user_context = result.get('user_context', {})
                    self.login_successful.emit(token, user_context)
                    self.accept()
                elif result.get('requires_2fa', False):
                    # 2FA required - show 2FA dialog
                    self._set_loading(False)
                    self._handle_2fa_required(result)
                else:
                    self._failed_attempts += 1
                    if self._failed_attempts >= 3:
                        delay_seconds = min(30, 2 ** (self._failed_attempts - 3) * 5)
                        self._lockout_until = QDateTime.currentDateTime().addSecs(delay_seconds)
                    error_msg = result.get('error', 'Login failed. Please try again.')
                    error_field = result.get('field')
                    self._show_error(error_msg, field=error_field)
            else:
                self._show_error("Authentication manager not available.")

        except Exception as e:
            self._failed_attempts += 1
            if self._failed_attempts >= 3:
                delay_seconds = min(30, 2 ** (self._failed_attempts - 3) * 5)
                self._lockout_until = QDateTime.currentDateTime().addSecs(delay_seconds)
            self._show_error(f"An error occurred: {str(e)}")

        finally:
            self._set_loading(False)

    def _handle_2fa_required(self, login_result: dict):
        """
        Handle 2FA requirement by showing the 2FA dialog.

        Args:
            login_result: Dict containing session_token, user_id, has_recovery_email
        """
        session_token = login_result.get('session_token', '')
        user_id = login_result.get('user_id', 0)
        has_recovery_email = login_result.get('has_recovery_email', False)
        username = login_result.get('username', '')
        save_password = login_result.get('save_password', False)

        # Get API client from auth manager
        api_client = self.auth_manager.api_client if self.auth_manager else None

        # Show 2FA dialog
        tfa_success, token, expiry = TwoFactorDialog.verify(
            parent=self,
            api_client=api_client,
            session_token=session_token,
            user_id=user_id,
            has_recovery_email=has_recovery_email
        )

        if tfa_success and token:
            # Complete the login with the token from 2FA
            success, result = self.auth_manager.complete_2fa_login(
                token=token,
                username=username,
                save_password=save_password
            )

            if success:
                user_context = result.get('user_context', {})
                self.login_successful.emit(token, user_context)
                self.accept()
            else:
                error_msg = result.get('error', 'Failed to complete login after 2FA.')
                self._show_error(error_msg)
        else:
            # User cancelled 2FA or verification failed
            self._show_error("Two-factor authentication was cancelled or failed.")

    def _on_cancel_clicked(self):
        """Handle cancel button click."""
        self.login_cancelled.emit()
        self.reject()

    def get_credentials(self) -> Tuple[str, str]:
        """
        Get the entered credentials.

        Returns:
            Tuple of (email, password)
        """
        return (
            self.email_input.text().strip(),
            self.password_input.text()
        )

    @staticmethod
    def get_login(parent=None, auth_manager=None) -> Tuple[bool, Optional[str], Optional[dict]]:
        """
        Static method to show login dialog and return results.

        Args:
            parent: Parent widget
            auth_manager: AuthManager instance

        Returns:
            Tuple of (success, token, user_context)
        """
        dialog = LoginDialog(parent, auth_manager)
        result = dialog.exec()

        if result == QDialog_Accepted and auth_manager:
            session = auth_manager.get_session()
            if session:
                return (True, session.token, session.user_context)

        return (False, None, None)
