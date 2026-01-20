# -*- coding: utf-8 -*-
"""
Staff Orders Dialog - View and pull pending claim orders for fulfillment.

Staff users can view pending ClaimPurchaseOrders (BLM Map) and ClaimOrders (QPlugin)
and pull them into QGIS to fulfill using the normal claims workflow.
"""
from typing import Dict, Any, Optional, List
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QTableWidget, QTableWidgetItem, QHeaderView,
    QApplication, QMessageBox, QAbstractItemView
)
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QFont, QColor, QBrush

from ..managers.claims_manager import ClaimsManager


class StaffOrdersDialog(QDialog):
    """
    Dialog for staff users to view and pull pending claim orders.

    Displays orders from both ClaimPurchaseOrder (BLM Map) and ClaimOrder (QPlugin)
    that need staff processing.
    """

    # Signal emitted when an order is selected for pulling
    # Emits: (order_data: dict)
    order_selected = pyqtSignal(dict)

    # Status display colors
    STATUS_COLORS = {
        'pending': '#f59e0b',      # amber
        'processing': '#2563eb',   # blue
        'paid': '#059669',         # green
        'approved': '#059669',     # green
    }

    def __init__(
        self,
        claims_manager: ClaimsManager,
        parent=None
    ):
        """
        Initialize staff orders dialog.

        Args:
            claims_manager: ClaimsManager instance for API calls
            parent: Parent widget
        """
        super().__init__(parent)
        self.claims_manager = claims_manager
        self.orders: List[Dict[str, Any]] = []
        self.selected_order: Optional[Dict[str, Any]] = None

        self._setup_ui()
        self._load_orders()

    def _setup_ui(self):
        """Set up dialog UI."""
        self.setWindowTitle("Staff - Pending Claim Orders")
        self.setMinimumWidth(800)
        self.setMinimumHeight(500)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 24, 24, 24)

        # Header
        header_layout = QHBoxLayout()

        header_label = QLabel("Pending Claim Orders")
        header_font = QFont()
        header_font.setPointSize(16)
        header_font.setBold(True)
        header_label.setFont(header_font)
        header_layout.addWidget(header_label)

        header_layout.addStretch()

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setStyleSheet(self._get_secondary_button_style())
        self.refresh_btn.clicked.connect(self._load_orders)
        header_layout.addWidget(self.refresh_btn)

        layout.addLayout(header_layout)

        # Info label
        info_label = QLabel(
            "Select an order to pull its claims into QGIS for processing. "
            "The order context will be tracked through the workflow."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: #6b7280; font-size: 13px; margin-bottom: 8px;")
        layout.addWidget(info_label)

        # Orders table
        self.orders_table = QTableWidget()
        self.orders_table.setColumnCount(7)
        self.orders_table.setHorizontalHeaderLabels([
            "Order #", "Type", "Status", "Customer", "Project", "Claims", "Created"
        ])

        # Configure table
        header = self.orders_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)  # Order #
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)  # Type
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)  # Status
        header.setSectionResizeMode(3, QHeaderView.Stretch)          # Customer
        header.setSectionResizeMode(4, QHeaderView.Stretch)          # Project
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)  # Claims
        header.setSectionResizeMode(6, QHeaderView.ResizeToContents)  # Created

        self.orders_table.verticalHeader().setVisible(False)
        self.orders_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.orders_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.orders_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.orders_table.setStyleSheet(self._get_table_style())

        self.orders_table.itemSelectionChanged.connect(self._on_selection_changed)
        self.orders_table.doubleClicked.connect(self._on_double_click)

        layout.addWidget(self.orders_table)

        # Order details panel
        self.details_frame = QFrame()
        self.details_frame.setStyleSheet("""
            QFrame {
                background-color: #f9fafb;
                border: 1px solid #e5e7eb;
                border-radius: 8px;
                padding: 12px;
            }
        """)
        details_layout = QVBoxLayout(self.details_frame)
        details_layout.setSpacing(8)

        self.details_label = QLabel("Select an order to view details")
        self.details_label.setStyleSheet("color: #6b7280;")
        details_layout.addWidget(self.details_label)

        self.claimant_label = QLabel("")
        self.claimant_label.setWordWrap(True)
        self.claimant_label.setStyleSheet("color: #374151; font-size: 13px;")
        details_layout.addWidget(self.claimant_label)

        self.extras_label = QLabel("")
        self.extras_label.setStyleSheet("color: #6b7280; font-size: 12px;")
        details_layout.addWidget(self.extras_label)

        layout.addWidget(self.details_frame)

        # Status label
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #6b7280; font-size: 12px;")
        layout.addWidget(self.status_label)

        # Buttons
        button_layout = QHBoxLayout()
        button_layout.setSpacing(12)

        self.pull_btn = QPushButton("Pull Order into QGIS")
        self.pull_btn.setStyleSheet(self._get_primary_button_style())
        self.pull_btn.clicked.connect(self._pull_selected_order)
        self.pull_btn.setEnabled(False)
        button_layout.addWidget(self.pull_btn)

        button_layout.addStretch()

        close_btn = QPushButton("Close")
        close_btn.setStyleSheet(self._get_secondary_button_style())
        close_btn.clicked.connect(self.reject)
        button_layout.addWidget(close_btn)

        layout.addLayout(button_layout)

    def _load_orders(self):
        """Load pending orders from server."""
        self.refresh_btn.setEnabled(False)
        self.refresh_btn.setText("Loading...")
        self.status_label.setText("Loading orders...")
        QApplication.processEvents()

        try:
            result = self.claims_manager.get_staff_pending_orders()
            self.orders = result.get('orders', [])
            self._populate_table()
            self.status_label.setText(f"Found {len(self.orders)} pending orders")

        except Exception as e:
            self.status_label.setText(f"Error loading orders: {e}")
            QMessageBox.warning(
                self,
                "Error",
                f"Failed to load pending orders:\n{e}"
            )

        finally:
            self.refresh_btn.setEnabled(True)
            self.refresh_btn.setText("Refresh")

    def _populate_table(self):
        """Populate table with orders."""
        self.orders_table.setRowCount(len(self.orders))

        for row, order in enumerate(self.orders):
            # Order number
            order_num = order.get('order_number', f"#{order.get('id', '?')}")
            self.orders_table.setItem(row, 0, QTableWidgetItem(order_num))

            # Type
            order_type = order.get('order_type', 'unknown')
            type_display = 'BLM Map' if order_type == 'claim_purchase' else 'QPlugin'
            self.orders_table.setItem(row, 1, QTableWidgetItem(type_display))

            # Status with color
            status = order.get('status', 'unknown')
            status_display = order.get('status_display', status.replace('_', ' ').title())
            status_item = QTableWidgetItem(status_display)
            status_color = self.STATUS_COLORS.get(status, '#6b7280')
            status_item.setForeground(QBrush(QColor(status_color)))
            self.orders_table.setItem(row, 2, status_item)

            # Customer
            customer = order.get('customer_email', '-')
            self.orders_table.setItem(row, 3, QTableWidgetItem(customer))

            # Project
            project = order.get('project_name', '-')
            self.orders_table.setItem(row, 4, QTableWidgetItem(project))

            # Claim count
            claim_count = str(order.get('claim_count', 0))
            self.orders_table.setItem(row, 5, QTableWidgetItem(claim_count))

            # Created date
            created_at = order.get('created_at', '')
            if created_at:
                # Format date nicely
                try:
                    from datetime import datetime
                    dt = datetime.fromisoformat(created_at.replace('Z', '+00:00'))
                    created_display = dt.strftime('%Y-%m-%d %H:%M')
                except Exception:
                    created_display = created_at[:16] if len(created_at) > 16 else created_at
            else:
                created_display = '-'
            self.orders_table.setItem(row, 6, QTableWidgetItem(created_display))

        # Clear selection
        self.orders_table.clearSelection()
        self.selected_order = None
        self._update_details()

    def _on_selection_changed(self):
        """Handle table selection change."""
        selected_rows = self.orders_table.selectedItems()
        if selected_rows:
            row = self.orders_table.currentRow()
            if 0 <= row < len(self.orders):
                self.selected_order = self.orders[row]
                self.pull_btn.setEnabled(True)
                self._update_details()
                return

        self.selected_order = None
        self.pull_btn.setEnabled(False)
        self._update_details()

    def _on_double_click(self):
        """Handle double click on table row."""
        if self.selected_order:
            self._pull_selected_order()

    def _update_details(self):
        """Update details panel with selected order."""
        if not self.selected_order:
            self.details_label.setText("Select an order to view details")
            self.claimant_label.setText("")
            self.extras_label.setText("")
            return

        order = self.selected_order
        order_num = order.get('order_number', f"#{order.get('id', '?')}")
        company = order.get('company_name', 'Unknown')

        self.details_label.setText(
            f"<b>{order_num}</b> - {company}"
        )

        # Claimant info (if available)
        claimant_info = order.get('claimant_info')
        if claimant_info:
            claimant_name = claimant_info.get('claimant_name', '')
            if claimant_name:
                self.claimant_label.setText(f"Claimant: {claimant_name}")
            else:
                self.claimant_label.setText("Claimant info available (no name)")
        else:
            self.claimant_label.setText("No claimant info - will need to be entered")

        # Extras
        extras = []
        if order.get('staking_service'):
            extras.append("Staking service requested")
        if order.get('expedited_delivery'):
            extras.append("Expedited delivery")
        if order.get('claim_package_id'):
            extras.append(f"Existing package: #{order['claim_package_id']}")

        self.extras_label.setText(" | ".join(extras) if extras else "")

    def _pull_selected_order(self):
        """Pull selected order and emit signal."""
        if not self.selected_order:
            return

        order = self.selected_order
        order_num = order.get('order_number', f"#{order.get('id', '?')}")
        claim_count = order.get('claim_count', 0)

        # Confirm
        reply = QMessageBox.question(
            self,
            "Pull Order",
            f"Pull order {order_num} ({claim_count} claims) into QGIS?\n\n"
            f"This will load the claim polygons and track the order context "
            f"through the workflow.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )

        if reply != QMessageBox.Yes:
            return

        # Emit signal with order data
        self.order_selected.emit(self.selected_order)
        self.accept()

    def get_selected_order(self) -> Optional[Dict[str, Any]]:
        """Get the selected order data."""
        return self.selected_order

    def _get_table_style(self) -> str:
        """Get table style."""
        return """
            QTableWidget {
                border: 1px solid #e5e7eb;
                border-radius: 8px;
                background-color: white;
                gridline-color: #e5e7eb;
            }
            QTableWidget::item {
                padding: 8px;
            }
            QTableWidget::item:selected {
                background-color: #dbeafe;
                color: #1e40af;
            }
            QHeaderView::section {
                background-color: #f9fafb;
                padding: 10px 8px;
                border: none;
                border-bottom: 1px solid #e5e7eb;
                font-weight: bold;
                color: #374151;
            }
        """

    def _get_primary_button_style(self) -> str:
        """Get primary button style."""
        return """
            QPushButton {
                padding: 12px 24px;
                background-color: #2563eb;
                color: white;
                border: none;
                border-radius: 6px;
                font-weight: bold;
                font-size: 14px;
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
            }
            QPushButton:hover {
                background-color: #f9fafb;
                border-color: #9ca3af;
            }
            QPushButton:pressed {
                background-color: #f3f4f6;
            }
            QPushButton:disabled {
                background-color: #f3f4f6;
                color: #9ca3af;
            }
        """
