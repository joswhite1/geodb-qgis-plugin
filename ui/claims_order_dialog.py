# -*- coding: utf-8 -*-
"""
QClaims Order status and management dialog.

Displays order details and status for pay-per-claim users.
"""
from typing import Dict, Any, Optional
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QGroupBox, QTableWidget, QTableWidgetItem,
    QHeaderView
)
from qgis.PyQt.QtCore import QTimer
from qgis.PyQt.QtGui import QFont

from ..managers.claims_manager import ClaimsManager
from ..utils.compat import QFrame_HLine, QAbstractItemView_NoEditTriggers, QHeaderView_Stretch
from ..utils.theme import T


class ClaimsOrderDialog(QDialog):
    """
    Dialog for viewing claim order status.

    Shows order details, status, and allows refreshing until fulfilled.
    """

    # Status colors (token attribute names on T, resolved against the active
    # theme at display time so badges read in both light and dark mode).
    STATUS_COLORS = {
        'pending_approval': 'WARNING',  # amber
        'approved': 'ACCENT',  # blue
        'paid': 'SUCCESS',  # green
        'fulfilled': 'SUCCESS',  # green
        'rejected': 'DANGER',  # red
        'cancelled': 'TEXT_MUTED',  # gray
    }

    def __init__(
        self,
        claims_manager: ClaimsManager,
        order_id: int,
        parent=None
    ):
        """
        Initialize order dialog.

        Args:
            claims_manager: ClaimsManager instance
            order_id: Order ID to display
            parent: Parent widget
        """
        super().__init__(parent)
        self.claims_manager = claims_manager
        self.order_id = order_id
        self.order_data: Optional[Dict[str, Any]] = None

        # Auto-refresh timer
        self.refresh_timer = QTimer(self)
        self.refresh_timer.timeout.connect(self._refresh_status)

        self._setup_ui()
        self._load_order()

    def _setup_ui(self):
        """Set up dialog UI."""
        self.setWindowTitle(f"Order #{self.order_id}")
        self.setMinimumWidth(500)
        self.setMinimumHeight(400)
        self.setModal(True)
        # Theme the dialog surface so the card reads correctly in both light
        # and dark mode (otherwise the host's dark window shows through).
        self.setStyleSheet(
            f"ClaimsOrderDialog {{ background-color: {T.SURFACE}; }}"
        )

        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 24, 24, 24)

        # Header
        header_layout = QHBoxLayout()

        self.header_label = QLabel(f"Order #{self.order_id}")
        header_font = QFont()
        header_font.setPointSize(16)
        header_font.setBold(True)
        self.header_label.setFont(header_font)
        header_layout.addWidget(self.header_label)

        header_layout.addStretch()

        self.status_badge = QLabel("Loading...")
        self.status_badge.setStyleSheet(self._get_badge_style(T.TEXT_MUTED))
        header_layout.addWidget(self.status_badge)

        layout.addLayout(header_layout)

        # Separator
        line = QFrame()
        line.setFrameShape(QFrame_HLine)
        line.setStyleSheet(f"background-color: {T.BORDER_SUBTLE};")
        layout.addWidget(line)

        # Order details group
        details_group = QGroupBox("Order Details")
        details_group.setStyleSheet(self._get_group_style())
        details_layout = QVBoxLayout(details_group)

        # Claims count
        self.claims_label = QLabel("Claims: -")
        self.claims_label.setStyleSheet("font-size: 14px;")
        details_layout.addWidget(self.claims_label)

        # Total
        self.total_label = QLabel("Total: -")
        self.total_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        details_layout.addWidget(self.total_label)

        # Created
        self.created_label = QLabel("Created: -")
        self.created_label.setStyleSheet(f"font-size: 13px; color: {T.TEXT_MUTED};")
        details_layout.addWidget(self.created_label)

        # Requires approval
        self.approval_label = QLabel("")
        self.approval_label.setWordWrap(True)
        self.approval_label.setStyleSheet(f"font-size: 13px; color: {T.TEXT_MUTED};")
        details_layout.addWidget(self.approval_label)

        layout.addWidget(details_group)

        # Status-specific info
        self.status_info_label = QLabel("")
        self.status_info_label.setWordWrap(True)
        self.status_info_label.setStyleSheet(f"""
            padding: 12px;
            background-color: {T.SURFACE_SUNKEN};
            border-radius: 6px;
            color: {T.TEXT_PRIMARY};
        """)
        layout.addWidget(self.status_info_label)

        # Documents group (shown when fulfilled)
        self.documents_group = QGroupBox("Documents")
        self.documents_group.setStyleSheet(self._get_group_style())
        docs_layout = QVBoxLayout(self.documents_group)

        self.documents_table = QTableWidget()
        self.documents_table.setColumnCount(2)
        self.documents_table.setHorizontalHeaderLabels(["Document", "Type"])
        self.documents_table.horizontalHeader().setSectionResizeMode(QHeaderView_Stretch)
        self.documents_table.verticalHeader().setVisible(False)
        self.documents_table.setEditTriggers(QAbstractItemView_NoEditTriggers)
        self.documents_table.setMaximumHeight(120)
        self.documents_table.setStyleSheet(self._get_table_style())
        docs_layout.addWidget(self.documents_table)

        self.documents_group.hide()
        layout.addWidget(self.documents_group)

        layout.addStretch()

        # Auto-refresh checkbox
        refresh_layout = QHBoxLayout()

        self.auto_refresh_label = QLabel("Auto-refresh every 30 seconds")
        self.auto_refresh_label.setStyleSheet(f"color: {T.TEXT_MUTED}; font-size: 12px;")
        refresh_layout.addWidget(self.auto_refresh_label)

        refresh_layout.addStretch()

        layout.addLayout(refresh_layout)

        # Buttons
        button_layout = QHBoxLayout()
        button_layout.setSpacing(12)

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setStyleSheet(self._get_secondary_button_style())
        self.refresh_btn.clicked.connect(self._refresh_status)
        button_layout.addWidget(self.refresh_btn)

        button_layout.addStretch()

        self.close_btn = QPushButton("Close")
        self.close_btn.setStyleSheet(self._get_primary_button_style())
        self.close_btn.clicked.connect(self.accept)
        button_layout.addWidget(self.close_btn)

        layout.addLayout(button_layout)

    def _load_order(self):
        """Load order data from server."""
        try:
            self.order_data = self.claims_manager.get_order_status(self.order_id)
            self._update_display()

            # Start auto-refresh if order is pending
            status = self.order_data.get('status', '')
            if status in ('pending_approval', 'approved', 'paid'):
                self.refresh_timer.start(30000)  # 30 seconds
            else:
                self.refresh_timer.stop()

        except Exception as e:
            self.status_badge.setText("Error")
            self.status_info_label.setText(f"Failed to load order: {e}")

    def _refresh_status(self):
        """Refresh order status."""
        self.refresh_btn.setEnabled(False)
        self.refresh_btn.setText("Refreshing...")
        # Note: Removed QApplication.processEvents() to prevent heap corruption crashes

        try:
            self._load_order()
        finally:
            self.refresh_btn.setEnabled(True)
            self.refresh_btn.setText("Refresh")

    def _update_display(self):
        """Update display with order data."""
        if not self.order_data:
            return

        status = self.order_data.get('status', 'unknown')
        status_display = self.order_data.get('status_display', status.replace('_', ' ').title())

        # Update status badge
        color = getattr(T, self.STATUS_COLORS.get(status, 'TEXT_MUTED'))
        self.status_badge.setText(status_display)
        self.status_badge.setStyleSheet(self._get_badge_style(color))

        # Update details
        self.claims_label.setText(f"Claims: {self.order_data.get('claim_count', 0)}")
        self.total_label.setText(f"Total: {self.order_data.get('total_display', '-')}")
        self.created_label.setText(f"Created: {self.order_data.get('created_at', '-')}")

        if self.order_data.get('requires_approval'):
            self.approval_label.setText("This order requires manager approval.")
        else:
            self.approval_label.setText("")

        # Update status-specific info
        if status == 'pending_approval':
            self.status_info_label.setText(
                "Your order is waiting for approval from a company manager. "
                "They will receive an email notification."
            )
            self.status_info_label.setStyleSheet(f"""
                padding: 12px;
                background-color: {T.WARNING_BG};
                border: 1px solid {T.WARNING};
                border-radius: 6px;
                color: {T.WARNING_TEXT};
            """)
        elif status == 'approved':
            self.status_info_label.setText(
                "Your order has been approved! Payment will be collected before processing begins."
            )
            self.status_info_label.setStyleSheet(f"""
                padding: 12px;
                background-color: {T.INFO_BG};
                border: 1px solid {T.ACCENT};
                border-radius: 6px;
                color: {T.ACCENT_ACTIVE};
            """)
        elif status == 'paid':
            self.status_info_label.setText(
                "Payment received! Your claims are being processed. "
                "This page will update automatically when complete."
            )
            self.status_info_label.setStyleSheet(f"""
                padding: 12px;
                background-color: {T.SUCCESS_BG};
                border: 1px solid {T.SUCCESS};
                border-radius: 6px;
                color: {T.SUCCESS_TEXT};
            """)
        elif status == 'fulfilled':
            self.status_info_label.setText(
                "Your claims have been processed successfully! "
                "Documents are available for download below."
            )
            self.status_info_label.setStyleSheet(f"""
                padding: 12px;
                background-color: {T.SUCCESS_BG};
                border: 1px solid {T.SUCCESS};
                border-radius: 6px;
                color: {T.SUCCESS_TEXT};
            """)
            self.refresh_timer.stop()
            self._show_documents()
        elif status == 'rejected':
            reason = self.order_data.get('rejection_reason', 'No reason provided.')
            self.status_info_label.setText(f"Order was rejected. Reason: {reason}")
            self.status_info_label.setStyleSheet(f"""
                padding: 12px;
                background-color: {T.DANGER_BG};
                border: 1px solid {T.DANGER};
                border-radius: 6px;
                color: {T.DANGER_TEXT};
            """)
            self.refresh_timer.stop()
        elif status == 'cancelled':
            self.status_info_label.setText("This order has been cancelled.")
            self.status_info_label.setStyleSheet(f"""
                padding: 12px;
                background-color: {T.SURFACE_SUNKEN};
                border: 1px solid {T.TEXT_MUTED};
                border-radius: 6px;
                color: {T.TEXT_PRIMARY};
            """)
            self.refresh_timer.stop()
        else:
            self.status_info_label.setText("")

    def _show_documents(self):
        """Show documents table for fulfilled orders."""
        documents = self.order_data.get('documents', [])
        if not documents:
            return

        self.documents_table.setRowCount(len(documents))

        for row, doc in enumerate(documents):
            filename = doc.get('filename', '-')
            doc_type = doc.get('document_type', '-').replace('_', ' ').title()

            self.documents_table.setItem(row, 0, QTableWidgetItem(filename))
            self.documents_table.setItem(row, 1, QTableWidgetItem(doc_type))

        self.documents_group.show()

    def _get_badge_style(self, color: str) -> str:
        """Get badge style with specified color."""
        return f"""
            QLabel {{
                padding: 6px 12px;
                background-color: {color};
                color: {T.TEXT_ON_ACCENT};
                border-radius: 12px;
                font-weight: bold;
                font-size: 12px;
            }}
        """

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

    def _get_table_style(self) -> str:
        """Get table style."""
        return f"""
            QTableWidget {{
                border: 1px solid {T.BORDER_SUBTLE};
                border-radius: 4px;
                background-color: {T.SURFACE};
                color: {T.TEXT_PRIMARY};
                gridline-color: {T.BORDER_SUBTLE};
            }}
            QHeaderView::section {{
                background-color: {T.SURFACE_SUBTLE};
                color: {T.TEXT_PRIMARY};
                padding: 8px;
                border: none;
                border-bottom: 1px solid {T.BORDER_SUBTLE};
                font-weight: bold;
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
                min-width: 80px;
            }}
            QPushButton:hover {{
                background-color: {T.ACCENT_HOVER};
            }}
            QPushButton:pressed {{
                background-color: {T.ACCENT_ACTIVE};
            }}
        """

    def _get_secondary_button_style(self) -> str:
        """Get secondary button style."""
        return f"""
            QPushButton {{
                padding: 10px 20px;
                background-color: {T.SURFACE};
                color: {T.TEXT_PRIMARY};
                border: 1px solid {T.BORDER};
                border-radius: 6px;
                font-size: 14px;
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
                background-color: {T.SURFACE_SUNKEN};
                color: {T.TEXT_FAINT};
            }}
        """

    def closeEvent(self, event):
        """Handle dialog close."""
        self.refresh_timer.stop()
        super().closeEvent(event)
