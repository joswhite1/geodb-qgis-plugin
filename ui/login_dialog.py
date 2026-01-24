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
    QPushButton, QCheckBox, QFrame, QSpacerItem, QSizePolicy,
    QMessageBox, QApplication
)
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QFont, QPixmap, QIcon

from .two_factor_dialog import TwoFactorDialog


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

        self._setup_ui()
        self._load_saved_credentials()

    def _setup_ui(self):
        """Set up the dialog UI."""
        self.setWindowTitle("Login to geodb.io")
        self.setFixedWidth(400)
        self.setModal(True)

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
        header_label.setAlignment(Qt.AlignCenter)
        header_label.setStyleSheet("color: #2563eb;")
        layout.addWidget(header_label)

        # Subtitle
        subtitle_label = QLabel("Sign in to sync your geological data")
        subtitle_label.setAlignment(Qt.AlignCenter)
        subtitle_label.setStyleSheet("color: #6b7280; margin-bottom: 16px;")
        layout.addWidget(subtitle_label)

        # Separator line
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("background-color: #e5e7eb;")
        layout.addWidget(line)

        layout.addSpacing(8)

        # Email field
        email_label = QLabel("Email")
        email_label.setStyleSheet("font-weight: bold; color: #374151;")
        layout.addWidget(email_label)

        self.email_input = QLineEdit()
        self.email_input.setPlaceholderText("you@example.com")
        self.email_input.setStyleSheet(self._get_input_style())
        self.email_input.returnPressed.connect(self._focus_password)
        layout.addWidget(self.email_input)

        layout.addSpacing(8)

        # Password field
        password_label = QLabel("Password")
        password_label.setStyleSheet("font-weight: bold; color: #374151;")
        layout.addWidget(password_label)

        self.password_input = QLineEdit()
        self.password_input.setPlaceholderText("Enter your password")
        self.password_input.setEchoMode(QLineEdit.Password)
        self.password_input.setStyleSheet(self._get_input_style())
        self.password_input.returnPressed.connect(self._on_login_clicked)
        layout.addWidget(self.password_input)

        layout.addSpacing(8)

        # Remember me checkbox
        self.remember_checkbox = QCheckBox("Remember my email")
        self.remember_checkbox.setStyleSheet("color: #6b7280;")
        layout.addWidget(self.remember_checkbox)

        # Save password checkbox
        self.save_password_checkbox = QCheckBox("Save password (stored securely in QGIS)")
        self.save_password_checkbox.setStyleSheet("color: #6b7280;")
        layout.addWidget(self.save_password_checkbox)

        layout.addSpacing(16)

        # Error message label (hidden by default)
        self.error_label = QLabel()
        self.error_label.setStyleSheet("""
            color: #dc2626;
            background-color: #fef2f2;
            border: 1px solid #fecaca;
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
        footer_label.setAlignment(Qt.AlignCenter)
        footer_label.setStyleSheet("color: #9ca3af; font-size: 11px;")
        layout.addWidget(footer_label)

    def _get_input_style(self) -> str:
        """Get stylesheet for input fields."""
        return """
            QLineEdit {
                padding: 10px 12px;
                border: 1px solid #d1d5db;
                border-radius: 6px;
                background-color: #ffffff;
                font-size: 14px;
            }
            QLineEdit:focus {
                border-color: #2563eb;
                outline: none;
            }
            QLineEdit:disabled {
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
                font-size: 14px;
                min-width: 100px;
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
                font-size: 14px;
                min-width: 100px;
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

    def _show_error(self, message: str):
        """Display error message."""
        self.error_label.setText(message)
        self.error_label.show()

    def _hide_error(self):
        """Hide error message."""
        self.error_label.hide()

    def _validate_inputs(self) -> bool:
        """Validate email and password inputs."""
        email = self.email_input.text().strip()
        password = self.password_input.text()

        if not email:
            self._show_error("Please enter your email address.")
            self.email_input.setFocus()
            return False

        if '@' not in email or '.' not in email:
            self._show_error("Please enter a valid email address.")
            self.email_input.setFocus()
            return False

        if not password:
            self._show_error("Please enter your password.")
            self.password_input.setFocus()
            return False

        return True

    def _on_login_clicked(self):
        """Handle login button click."""
        self._hide_error()

        if not self._validate_inputs():
            return

        email = self.email_input.text().strip()
        password = self.password_input.text()

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
                    token = result.get('token', '')
                    user_context = result.get('user_context', {})
                    self.login_successful.emit(token, user_context)
                    self.accept()
                elif result.get('requires_2fa', False):
                    # 2FA required - show 2FA dialog
                    self._set_loading(False)
                    self._handle_2fa_required(result)
                else:
                    error_msg = result.get('error', 'Login failed. Please try again.')
                    self._show_error(error_msg)
            else:
                self._show_error("Authentication manager not available.")

        except Exception as e:
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
        result = dialog.exec_()

        if result == QDialog.Accepted and auth_manager:
            session = auth_manager.get_session()
            if session:
                return (True, session.token, session.user_context)

        return (False, None, None)
