# -*- coding: utf-8 -*-
"""
Two-Factor Authentication dialog for geodb.io.

Provides UI for entering TOTP codes and handling 2FA recovery flow.
"""
from typing import Optional, Tuple, TYPE_CHECKING
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QFrame, QStackedWidget, QWidget
)
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QFont

if TYPE_CHECKING:
    from ..api.client import APIClient


class TwoFactorDialog(QDialog):
    """
    Dialog for two-factor authentication verification.

    Handles both TOTP code entry and recovery flow when user
    has lost access to their authenticator app.

    Signals:
        verification_successful: Emitted with (token, expiry) on success
        verification_cancelled: Emitted when user cancels
    """

    verification_successful = pyqtSignal(str, str)  # token, expiry
    verification_cancelled = pyqtSignal()

    # Pages in stacked widget
    PAGE_CODE_ENTRY = 0
    PAGE_RECOVERY = 1
    PAGE_RECOVERY_CODE = 2

    def __init__(
        self,
        parent=None,
        api_client: Optional['APIClient'] = None,
        session_token: str = "",
        user_id: int = 0,
        has_recovery_email: bool = False
    ):
        """
        Initialize 2FA dialog.

        Args:
            parent: Parent widget
            api_client: APIClient instance for 2FA API calls
            session_token: Temporary session token from login
            user_id: User ID (for display purposes)
            has_recovery_email: Whether user has recovery email configured
        """
        super().__init__(parent)
        self.api_client = api_client
        self.session_token = session_token
        self.user_id = user_id
        self.has_recovery_email = has_recovery_email
        self._is_loading = False
        self._recovery_email_masked = ""

        self._setup_ui()

    def _setup_ui(self):
        """Set up the dialog UI."""
        self.setWindowTitle("Two-Factor Authentication")
        self.setFixedWidth(420)
        self.setModal(True)

        # Main layout
        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(32, 32, 32, 32)

        # Header
        header_label = QLabel("Two-Factor Authentication")
        header_font = QFont()
        header_font.setPointSize(18)
        header_font.setBold(True)
        header_label.setFont(header_font)
        header_label.setAlignment(Qt.AlignCenter)
        header_label.setStyleSheet("color: #2563eb;")
        layout.addWidget(header_label)

        # Separator
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("background-color: #e5e7eb;")
        layout.addWidget(line)

        # Stacked widget for different pages
        self.stacked_widget = QStackedWidget()
        layout.addWidget(self.stacked_widget)

        # Page 0: Code entry
        self._create_code_entry_page()

        # Page 1: Recovery request
        self._create_recovery_page()

        # Page 2: Recovery code entry
        self._create_recovery_code_page()

        # Error message (shared across pages)
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

        # Success message (for recovery email sent)
        self.success_label = QLabel()
        self.success_label.setStyleSheet("""
            color: #059669;
            background-color: #ecfdf5;
            border: 1px solid #a7f3d0;
            border-radius: 6px;
            padding: 8px 12px;
        """)
        self.success_label.setWordWrap(True)
        self.success_label.hide()
        layout.addWidget(self.success_label)

        # Buttons
        self.button_layout = QHBoxLayout()
        self.button_layout.setSpacing(12)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setStyleSheet(self._get_secondary_button_style())
        self.cancel_button.clicked.connect(self._on_cancel_clicked)
        self.button_layout.addWidget(self.cancel_button)

        self.verify_button = QPushButton("Verify")
        self.verify_button.setStyleSheet(self._get_primary_button_style())
        self.verify_button.clicked.connect(self._on_verify_clicked)
        self.verify_button.setDefault(True)
        self.button_layout.addWidget(self.verify_button)

        layout.addLayout(self.button_layout)

    def _create_code_entry_page(self):
        """Create the TOTP code entry page."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 16, 0, 0)

        # Instructions
        instructions = QLabel(
            "Enter the 6-digit code from your authenticator app."
        )
        instructions.setWordWrap(True)
        instructions.setStyleSheet("color: #6b7280; margin-bottom: 8px;")
        layout.addWidget(instructions)

        # Code input
        code_label = QLabel("Authentication Code")
        code_label.setStyleSheet("font-weight: bold; color: #374151;")
        layout.addWidget(code_label)

        self.code_input = QLineEdit()
        self.code_input.setPlaceholderText("000000")
        self.code_input.setMaxLength(8)  # Allow backup codes which may be longer
        self.code_input.setStyleSheet(self._get_input_style())
        self.code_input.returnPressed.connect(self._on_verify_clicked)
        # Center the text for better UX with numeric codes
        self.code_input.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.code_input)

        layout.addSpacing(8)

        # Backup code hint
        backup_hint = QLabel(
            "You can also use a backup code if you saved one during setup."
        )
        backup_hint.setWordWrap(True)
        backup_hint.setStyleSheet("color: #9ca3af; font-size: 11px;")
        layout.addWidget(backup_hint)

        layout.addSpacing(16)

        # Recovery link (only if user has recovery email)
        if self.has_recovery_email:
            recovery_link = QPushButton("Can't access your authenticator?")
            recovery_link.setStyleSheet("""
                QPushButton {
                    color: #2563eb;
                    background: transparent;
                    border: none;
                    text-decoration: underline;
                    padding: 0;
                }
                QPushButton:hover {
                    color: #1d4ed8;
                }
            """)
            recovery_link.setCursor(Qt.PointingHandCursor)
            recovery_link.clicked.connect(self._show_recovery_page)
            layout.addWidget(recovery_link)

        layout.addStretch()
        self.stacked_widget.addWidget(page)

    def _create_recovery_page(self):
        """Create the recovery request page."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 16, 0, 0)

        # Instructions
        instructions = QLabel(
            "We can send a recovery code to your recovery email address. "
            "This will allow you to sign in without your authenticator app."
        )
        instructions.setWordWrap(True)
        instructions.setStyleSheet("color: #6b7280; margin-bottom: 16px;")
        layout.addWidget(instructions)

        # Warning
        warning = QLabel(
            "Note: Recovery codes are only valid for 10 minutes. "
            "You can request up to 3 recovery codes per hour."
        )
        warning.setWordWrap(True)
        warning.setStyleSheet("""
            color: #b45309;
            background-color: #fffbeb;
            border: 1px solid #fcd34d;
            border-radius: 6px;
            padding: 8px 12px;
        """)
        layout.addWidget(warning)

        layout.addSpacing(16)

        # Send recovery code button
        self.send_recovery_button = QPushButton("Send Recovery Code")
        self.send_recovery_button.setStyleSheet(self._get_primary_button_style())
        self.send_recovery_button.clicked.connect(self._on_send_recovery_clicked)
        layout.addWidget(self.send_recovery_button)

        layout.addSpacing(8)

        # Back link
        back_link = QPushButton("Back to authenticator code")
        back_link.setStyleSheet("""
            QPushButton {
                color: #6b7280;
                background: transparent;
                border: none;
                text-decoration: underline;
                padding: 0;
            }
            QPushButton:hover {
                color: #374151;
            }
        """)
        back_link.setCursor(Qt.PointingHandCursor)
        back_link.clicked.connect(self._show_code_entry_page)
        layout.addWidget(back_link)

        layout.addStretch()
        self.stacked_widget.addWidget(page)

    def _create_recovery_code_page(self):
        """Create the recovery code entry page."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 16, 0, 0)

        # Instructions (will be updated with masked email)
        self.recovery_instructions = QLabel(
            "Enter the 6-digit recovery code sent to your email."
        )
        self.recovery_instructions.setWordWrap(True)
        self.recovery_instructions.setStyleSheet("color: #6b7280; margin-bottom: 8px;")
        layout.addWidget(self.recovery_instructions)

        # Recovery code input
        code_label = QLabel("Recovery Code")
        code_label.setStyleSheet("font-weight: bold; color: #374151;")
        layout.addWidget(code_label)

        self.recovery_code_input = QLineEdit()
        self.recovery_code_input.setPlaceholderText("000000")
        self.recovery_code_input.setMaxLength(6)
        self.recovery_code_input.setStyleSheet(self._get_input_style())
        self.recovery_code_input.returnPressed.connect(self._on_verify_recovery_clicked)
        self.recovery_code_input.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.recovery_code_input)

        layout.addSpacing(16)

        # Verify recovery button
        self.verify_recovery_button = QPushButton("Verify Recovery Code")
        self.verify_recovery_button.setStyleSheet(self._get_primary_button_style())
        self.verify_recovery_button.clicked.connect(self._on_verify_recovery_clicked)
        layout.addWidget(self.verify_recovery_button)

        layout.addSpacing(8)

        # Resend link
        resend_link = QPushButton("Didn't receive it? Send again")
        resend_link.setStyleSheet("""
            QPushButton {
                color: #6b7280;
                background: transparent;
                border: none;
                text-decoration: underline;
                padding: 0;
            }
            QPushButton:hover {
                color: #374151;
            }
        """)
        resend_link.setCursor(Qt.PointingHandCursor)
        resend_link.clicked.connect(self._on_send_recovery_clicked)
        layout.addWidget(resend_link)

        layout.addStretch()
        self.stacked_widget.addWidget(page)

    def _get_input_style(self) -> str:
        """Get stylesheet for input fields."""
        return """
            QLineEdit {
                padding: 12px 16px;
                border: 1px solid #d1d5db;
                border-radius: 6px;
                background-color: #ffffff;
                font-size: 18px;
                font-family: monospace;
                letter-spacing: 4px;
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

    def _show_error(self, message: str):
        """Display error message."""
        self.success_label.hide()
        self.error_label.setText(message)
        self.error_label.show()

    def _show_success(self, message: str):
        """Display success message."""
        self.error_label.hide()
        self.success_label.setText(message)
        self.success_label.show()

    def _hide_messages(self):
        """Hide all messages."""
        self.error_label.hide()
        self.success_label.hide()

    def _set_loading(self, loading: bool):
        """Set loading state."""
        self._is_loading = loading
        self.code_input.setEnabled(not loading)
        self.recovery_code_input.setEnabled(not loading)
        self.verify_button.setEnabled(not loading)
        self.cancel_button.setEnabled(not loading)
        self.send_recovery_button.setEnabled(not loading)
        self.verify_recovery_button.setEnabled(not loading)

        if loading:
            self.verify_button.setText("Verifying...")
        else:
            self.verify_button.setText("Verify")

    def _show_code_entry_page(self):
        """Show the TOTP code entry page."""
        self._hide_messages()
        self.stacked_widget.setCurrentIndex(self.PAGE_CODE_ENTRY)
        self.verify_button.show()
        self.code_input.setFocus()

    def _show_recovery_page(self):
        """Show the recovery request page."""
        self._hide_messages()
        self.stacked_widget.setCurrentIndex(self.PAGE_RECOVERY)
        self.verify_button.hide()

    def _show_recovery_code_page(self):
        """Show the recovery code entry page."""
        self._hide_messages()
        self.stacked_widget.setCurrentIndex(self.PAGE_RECOVERY_CODE)
        self.verify_button.hide()
        self.recovery_code_input.setFocus()

        # Update instructions with masked email
        if self._recovery_email_masked:
            self.recovery_instructions.setText(
                f"Enter the 6-digit recovery code sent to {self._recovery_email_masked}"
            )

    def _on_verify_clicked(self):
        """Handle verify button click for TOTP code."""
        self._hide_messages()

        code = self.code_input.text().strip()
        if not code:
            self._show_error("Please enter your authentication code.")
            self.code_input.setFocus()
            return

        if not self.api_client:
            self._show_error("API client not available.")
            return

        self._set_loading(True)

        try:
            response = self.api_client.verify_2fa(self.session_token, code)
            token = response.get('token', '')
            expiry = response.get('expiry', '')

            if token:
                self.verification_successful.emit(token, expiry)
                self.accept()
            else:
                self._show_error("Verification failed. Please try again.")

        except Exception as e:
            error_msg = str(e)
            # Parse common error messages
            if 'Invalid' in error_msg or 'invalid' in error_msg:
                self._show_error("Invalid code. Please check and try again.")
            elif 'expired' in error_msg.lower():
                self._show_error("Session expired. Please login again.")
            elif 'attempts' in error_msg.lower():
                self._show_error("Too many failed attempts. Please login again.")
            else:
                self._show_error(f"Verification failed: {error_msg}")
            self.code_input.selectAll()
            self.code_input.setFocus()

        finally:
            self._set_loading(False)

    def _on_send_recovery_clicked(self):
        """Handle send recovery code button click."""
        self._hide_messages()

        if not self.api_client:
            self._show_error("API client not available.")
            return

        self._set_loading(True)
        self.send_recovery_button.setText("Sending...")

        try:
            response = self.api_client.request_2fa_recovery(self.session_token)
            self._recovery_email_masked = response.get('recovery_email_masked', 'your email')

            self._show_success(
                f"Recovery code sent to {self._recovery_email_masked}. "
                "Check your inbox (and spam folder)."
            )

            # Switch to recovery code entry page
            self._show_recovery_code_page()

        except Exception as e:
            error_msg = str(e)
            if 'rate' in error_msg.lower() or 'limit' in error_msg.lower():
                self._show_error(
                    "Too many recovery requests. Please wait before trying again."
                )
            elif 'no recovery email' in error_msg.lower():
                self._show_error(
                    "No recovery email configured. Please contact support."
                )
            else:
                self._show_error(f"Failed to send recovery code: {error_msg}")

        finally:
            self._set_loading(False)
            self.send_recovery_button.setText("Send Recovery Code")

    def _on_verify_recovery_clicked(self):
        """Handle verify recovery code button click."""
        self._hide_messages()

        code = self.recovery_code_input.text().strip()
        if not code:
            self._show_error("Please enter the recovery code.")
            self.recovery_code_input.setFocus()
            return

        if not self.api_client:
            self._show_error("API client not available.")
            return

        self._set_loading(True)
        self.verify_recovery_button.setText("Verifying...")

        try:
            response = self.api_client.verify_2fa_recovery(self.session_token, code)
            token = response.get('token', '')
            expiry = response.get('expiry', '')

            if token:
                self.verification_successful.emit(token, expiry)
                self.accept()
            else:
                self._show_error("Verification failed. Please try again.")

        except Exception as e:
            error_msg = str(e)
            if 'Invalid' in error_msg or 'invalid' in error_msg:
                self._show_error("Invalid recovery code. Please check and try again.")
            elif 'expired' in error_msg.lower():
                self._show_error("Recovery code expired. Please request a new one.")
            else:
                self._show_error(f"Verification failed: {error_msg}")
            self.recovery_code_input.selectAll()
            self.recovery_code_input.setFocus()

        finally:
            self._set_loading(False)
            self.verify_recovery_button.setText("Verify Recovery Code")

    def _on_cancel_clicked(self):
        """Handle cancel button click."""
        self.verification_cancelled.emit()
        self.reject()

    @staticmethod
    def verify(
        parent=None,
        api_client: Optional['APIClient'] = None,
        session_token: str = "",
        user_id: int = 0,
        has_recovery_email: bool = False
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        Static method to show 2FA dialog and return results.

        Args:
            parent: Parent widget
            api_client: APIClient instance
            session_token: Temporary session token from login
            user_id: User ID
            has_recovery_email: Whether user has recovery email

        Returns:
            Tuple of (success, token, expiry)
        """
        dialog = TwoFactorDialog(
            parent,
            api_client,
            session_token,
            user_id,
            has_recovery_email
        )

        token = None
        expiry = None

        def on_success(t, e):
            nonlocal token, expiry
            token = t
            expiry = e

        dialog.verification_successful.connect(on_success)
        result = dialog.exec_()

        if result == QDialog.Accepted and token:
            return (True, token, expiry)

        return (False, None, None)
