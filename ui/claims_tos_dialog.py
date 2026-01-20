# -*- coding: utf-8 -*-
"""
QClaims Terms of Service acceptance dialog.

Displays the TOS content and requires user to accept before proceeding.
"""
from typing import Dict, Any, List
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTextBrowser, QCheckBox, QFrame, QScrollArea, QWidget
)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QFont


class ClaimsTOSDialog(QDialog):
    """
    Terms of Service acceptance dialog.

    Displays TOS content with sections and requires checkbox acceptance.
    """

    def __init__(self, tos_content: Dict[str, Any], parent=None):
        """
        Initialize TOS dialog.

        Args:
            tos_content: Dict with version, title, and sections
            parent: Parent widget
        """
        super().__init__(parent)
        self.tos_content = tos_content
        self._setup_ui()

    def _setup_ui(self):
        """Set up dialog UI."""
        self.setWindowTitle("QClaims Terms of Service")
        self.setMinimumWidth(600)
        self.setMinimumHeight(500)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 24, 24, 24)

        # Header
        title = self.tos_content.get('title', 'Terms of Service')
        version = self.tos_content.get('version', '1.0')

        header = QLabel(title)
        header_font = QFont()
        header_font.setPointSize(16)
        header_font.setBold(True)
        header.setFont(header_font)
        header.setStyleSheet("color: #1f2937;")
        layout.addWidget(header)

        version_label = QLabel(f"Version {version}")
        version_label.setStyleSheet("color: #6b7280; margin-bottom: 8px;")
        layout.addWidget(version_label)

        # Separator
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("background-color: #e5e7eb;")
        layout.addWidget(line)

        # Content area
        self.content_browser = QTextBrowser()
        self.content_browser.setOpenExternalLinks(True)
        self.content_browser.setStyleSheet("""
            QTextBrowser {
                border: 1px solid #e5e7eb;
                border-radius: 8px;
                padding: 16px;
                background-color: #fafafa;
                font-size: 14px;
            }
        """)

        # Build HTML content
        html_content = self._build_html_content()
        self.content_browser.setHtml(html_content)

        layout.addWidget(self.content_browser, 1)

        # Important notice
        notice_label = QLabel(
            "IMPORTANT: By accepting these terms, you acknowledge that QClaims is a "
            "software tool only. You are solely responsible for verifying all claim "
            "data before filing with government agencies."
        )
        notice_label.setWordWrap(True)
        notice_label.setStyleSheet("""
            padding: 12px;
            background-color: #fef3c7;
            border: 1px solid #f59e0b;
            border-radius: 6px;
            color: #92400e;
        """)
        layout.addWidget(notice_label)

        # Acceptance checkbox
        self.accept_checkbox = QCheckBox(
            "I have read, understand, and accept these Terms of Service"
        )
        self.accept_checkbox.setStyleSheet("""
            QCheckBox {
                font-weight: bold;
                color: #374151;
                padding: 8px;
            }
            QCheckBox::indicator {
                width: 20px;
                height: 20px;
            }
        """)
        self.accept_checkbox.stateChanged.connect(self._on_checkbox_changed)
        layout.addWidget(self.accept_checkbox)

        # Buttons
        button_layout = QHBoxLayout()
        button_layout.setSpacing(12)

        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setStyleSheet(self._get_secondary_button_style())
        self.cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(self.cancel_btn)

        button_layout.addStretch()

        self.accept_btn = QPushButton("Accept Terms of Service")
        self.accept_btn.setStyleSheet(self._get_primary_button_style())
        self.accept_btn.clicked.connect(self.accept)
        self.accept_btn.setEnabled(False)
        button_layout.addWidget(self.accept_btn)

        layout.addLayout(button_layout)

    def _build_html_content(self) -> str:
        """Build HTML content from TOS data.

        Handles two formats from the server:
        1. HTML content (is_html=True): Returns the HTML content directly
        2. Sections format (legacy): Builds HTML from sections array
        """
        # Check if content is already HTML (database-backed TOS)
        if self.tos_content.get('is_html') and self.tos_content.get('content'):
            # Wrap the HTML content in a styled container
            html_content = self.tos_content.get('content', '')
            return f'''
                <div style="font-family: Arial, sans-serif; line-height: 1.6;">
                    {html_content}
                </div>
            '''

        # Legacy format: build HTML from sections array
        sections = self.tos_content.get('sections', [])

        html_parts = ['<div style="font-family: Arial, sans-serif; line-height: 1.6;">']

        # Add important notice if present
        important_notice = self.tos_content.get('important_notice')
        if important_notice:
            html_parts.append(f'''
                <div style="margin-bottom: 20px; padding: 12px; background-color: #fef3c7; border: 1px solid #f59e0b; border-radius: 6px;">
                    <p style="color: #92400e; margin: 0; white-space: pre-wrap;">{important_notice}</p>
                </div>
            ''')

        for section in sections:
            title = section.get('title', '')
            content = section.get('content', '')
            # Convert newlines to <br> for proper display
            content_html = content.replace('\n', '<br>')

            html_parts.append(f'''
                <div style="margin-bottom: 20px;">
                    <h3 style="color: #1f2937; margin-bottom: 8px;">{title}</h3>
                    <p style="color: #4b5563; margin: 0;">{content_html}</p>
                </div>
            ''')

        # Add acknowledgment section if present
        acknowledgment = self.tos_content.get('acknowledgment')
        if acknowledgment:
            acknowledgment_html = acknowledgment.replace('\n', '<br>')
            html_parts.append(f'''
                <div style="margin-top: 24px; padding: 16px; background-color: #f3f4f6; border-radius: 6px;">
                    <h3 style="color: #1f2937; margin-bottom: 8px;">Acknowledgment</h3>
                    <p style="color: #4b5563; margin: 0;">{acknowledgment_html}</p>
                </div>
            ''')

        html_parts.append('</div>')

        return ''.join(html_parts)

    def _on_checkbox_changed(self, state):
        """Handle checkbox state change."""
        self.accept_btn.setEnabled(state == Qt.Checked)

    def _get_primary_button_style(self) -> str:
        """Get primary button style."""
        return """
            QPushButton {
                padding: 10px 20px;
                background-color: #2563eb;
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: bold;
                font-size: 14px;
                min-width: 180px;
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
        """Get secondary button style."""
        return """
            QPushButton {
                padding: 10px 20px;
                background-color: #ffffff;
                color: #374151;
                border: 1px solid #d1d5db;
                border-radius: 6px;
                font-size: 14px;
                min-width: 80px;
            }
            QPushButton:hover {
                background-color: #f9fafb;
                border-color: #9ca3af;
            }
            QPushButton:pressed {
                background-color: #f3f4f6;
            }
        """
