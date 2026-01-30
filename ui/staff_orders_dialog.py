# -*- coding: utf-8 -*-
"""
Staff Orders Dialog - View and pull pending claim orders and proposed claims.

Staff users can view:
1. Pending ClaimPurchaseOrders (BLM Map) and ClaimOrders (QPlugin)
2. Admin-uploaded ProposedMiningClaims that need processing

Both can be pulled into QGIS for processing through the claims workflow.
"""
from typing import Dict, Any, Optional, List
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFrame, QTableWidget, QTableWidgetItem, QHeaderView,
    QMessageBox, QAbstractItemView, QTabWidget, QWidget, QComboBox
)
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QFont, QColor, QBrush

from ..managers.claims_manager import ClaimsManager


class StaffOrdersDialog(QDialog):
    """
    Dialog for staff users to view and pull pending claim orders and proposed claims.

    Displays:
    1. Pending Orders tab: ClaimPurchaseOrder (BLM Map) and ClaimOrder (QPlugin)
    2. Proposed Claims tab: Admin-uploaded ProposedMiningClaim records
    """

    # Signal emitted when an order is selected for pulling
    # Emits: (order_data: dict)
    order_selected = pyqtSignal(dict)

    # Signal emitted when proposed claims are selected for pulling
    # Emits: (claims_data: dict) with project info and claim features
    proposed_claims_selected = pyqtSignal(dict)

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

        # Pending orders data
        self.orders: List[Dict[str, Any]] = []
        self.selected_order: Optional[Dict[str, Any]] = None

        # Proposed claims data
        self.projects_with_claims: List[Dict[str, Any]] = []
        self.selected_project: Optional[Dict[str, Any]] = None
        self.proposed_claims: List[Dict[str, Any]] = []

        self._setup_ui()
        self._load_all_data()

    def _setup_ui(self):
        """Set up dialog UI with tabs."""
        self.setWindowTitle("Staff - Claim Orders & Proposed Claims")
        self.setMinimumWidth(900)
        self.setMinimumHeight(600)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 24, 24, 24)

        # Header
        header_layout = QHBoxLayout()

        header_label = QLabel("Staff Claims Management")
        header_font = QFont()
        header_font.setPointSize(16)
        header_font.setBold(True)
        header_label.setFont(header_font)
        header_layout.addWidget(header_label)

        header_layout.addStretch()

        self.refresh_btn = QPushButton("Refresh All")
        self.refresh_btn.setStyleSheet(self._get_secondary_button_style())
        self.refresh_btn.clicked.connect(self._load_all_data)
        header_layout.addWidget(self.refresh_btn)

        layout.addLayout(header_layout)

        # Tab widget
        self.tab_widget = QTabWidget()
        self.tab_widget.setStyleSheet(self._get_tab_style())

        # Pending Orders tab
        self.orders_tab = QWidget()
        self._setup_orders_tab()
        self.tab_widget.addTab(self.orders_tab, "Pending Orders")

        # Proposed Claims tab
        self.proposed_tab = QWidget()
        self._setup_proposed_tab()
        self.tab_widget.addTab(self.proposed_tab, "Proposed Claims")

        layout.addWidget(self.tab_widget)

        # Status label
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #6b7280; font-size: 12px;")
        layout.addWidget(self.status_label)

        # Buttons
        button_layout = QHBoxLayout()
        button_layout.setSpacing(12)

        self.pull_btn = QPushButton("Pull into QGIS")
        self.pull_btn.setStyleSheet(self._get_primary_button_style())
        self.pull_btn.clicked.connect(self._pull_selected)
        self.pull_btn.setEnabled(False)
        button_layout.addWidget(self.pull_btn)

        button_layout.addStretch()

        close_btn = QPushButton("Close")
        close_btn.setStyleSheet(self._get_secondary_button_style())
        close_btn.clicked.connect(self.reject)
        button_layout.addWidget(close_btn)

        layout.addLayout(button_layout)

        # Connect tab change to update pull button
        self.tab_widget.currentChanged.connect(self._on_tab_changed)

    def _setup_orders_tab(self):
        """Set up the Pending Orders tab."""
        layout = QVBoxLayout(self.orders_tab)
        layout.setSpacing(12)
        layout.setContentsMargins(0, 12, 0, 0)

        # Info label
        info_label = QLabel(
            "Select an order to pull its claims into QGIS for processing. "
            "The order context will be tracked through the workflow."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: #6b7280; font-size: 13px;")
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

        self.orders_table.itemSelectionChanged.connect(self._on_order_selection_changed)
        self.orders_table.doubleClicked.connect(self._on_order_double_click)

        layout.addWidget(self.orders_table)

        # Order details panel
        self.order_details_frame = QFrame()
        self.order_details_frame.setStyleSheet(self._get_details_frame_style())
        details_layout = QVBoxLayout(self.order_details_frame)
        details_layout.setSpacing(8)

        self.order_details_label = QLabel("Select an order to view details")
        self.order_details_label.setStyleSheet("color: #6b7280;")
        details_layout.addWidget(self.order_details_label)

        self.claimant_label = QLabel("")
        self.claimant_label.setWordWrap(True)
        self.claimant_label.setStyleSheet("color: #374151; font-size: 13px;")
        details_layout.addWidget(self.claimant_label)

        self.extras_label = QLabel("")
        self.extras_label.setStyleSheet("color: #6b7280; font-size: 12px;")
        details_layout.addWidget(self.extras_label)

        layout.addWidget(self.order_details_frame)

    def _setup_proposed_tab(self):
        """Set up the Proposed Claims tab."""
        layout = QVBoxLayout(self.proposed_tab)
        layout.setSpacing(12)
        layout.setContentsMargins(0, 12, 0, 0)

        # Info label
        info_label = QLabel(
            "Select a project to view admin-uploaded proposed claims. "
            "Pull approved claims into QGIS for processing through the claims workflow."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet("color: #6b7280; font-size: 13px;")
        layout.addWidget(info_label)

        # Project selector
        project_layout = QHBoxLayout()
        project_label = QLabel("Project:")
        project_label.setStyleSheet("font-weight: bold;")
        project_layout.addWidget(project_label)

        self.project_combo = QComboBox()
        self.project_combo.setMinimumWidth(300)
        self.project_combo.setStyleSheet(self._get_combo_style())
        self.project_combo.currentIndexChanged.connect(self._on_project_selected)
        project_layout.addWidget(self.project_combo)

        project_layout.addStretch()

        # Filter buttons
        self.filter_all_btn = QPushButton("All")
        self.filter_all_btn.setCheckable(True)
        self.filter_all_btn.setChecked(True)
        self.filter_all_btn.clicked.connect(lambda: self._set_filter('all'))
        project_layout.addWidget(self.filter_all_btn)

        self.filter_approved_btn = QPushButton("Approved Only")
        self.filter_approved_btn.setCheckable(True)
        self.filter_approved_btn.clicked.connect(lambda: self._set_filter('approved'))
        project_layout.addWidget(self.filter_approved_btn)

        layout.addLayout(project_layout)

        # Selection buttons row
        selection_layout = QHBoxLayout()

        self.select_all_btn = QPushButton("Select All")
        self.select_all_btn.setStyleSheet(self._get_secondary_button_style())
        self.select_all_btn.clicked.connect(self._select_all_claims)
        self.select_all_btn.setEnabled(False)
        selection_layout.addWidget(self.select_all_btn)

        self.deselect_all_btn = QPushButton("Deselect All")
        self.deselect_all_btn.setStyleSheet(self._get_secondary_button_style())
        self.deselect_all_btn.clicked.connect(self._deselect_all_claims)
        self.deselect_all_btn.setEnabled(False)
        selection_layout.addWidget(self.deselect_all_btn)

        selection_layout.addStretch()

        # Selection hint
        selection_hint = QLabel("Tip: Shift+click to select a range, Ctrl+click to toggle")
        selection_hint.setStyleSheet("color: #9ca3af; font-size: 11px; font-style: italic;")
        selection_layout.addWidget(selection_hint)

        layout.addLayout(selection_layout)

        # Proposed claims table
        self.proposed_table = QTableWidget()
        self.proposed_table.setColumnCount(5)
        self.proposed_table.setHorizontalHeaderLabels([
            "Claim Name", "Type", "Acreage", "PLSS Location", "Approved"
        ])

        # Configure table
        header = self.proposed_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)  # Name
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)  # Type
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)  # Acreage
        header.setSectionResizeMode(3, QHeaderView.Stretch)           # PLSS
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)  # Approved

        self.proposed_table.verticalHeader().setVisible(False)
        self.proposed_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.proposed_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        # ExtendedSelection: click to select, Ctrl+click to toggle, Shift+click for range
        self.proposed_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.proposed_table.setStyleSheet(self._get_table_style())

        self.proposed_table.itemSelectionChanged.connect(self._on_proposed_selection_changed)

        layout.addWidget(self.proposed_table)

        # Proposed claims details panel
        self.proposed_details_frame = QFrame()
        self.proposed_details_frame.setStyleSheet(self._get_details_frame_style())
        details_layout = QVBoxLayout(self.proposed_details_frame)
        details_layout.setSpacing(8)

        self.proposed_details_label = QLabel("Select claims to pull into QGIS")
        self.proposed_details_label.setStyleSheet("color: #6b7280;")
        details_layout.addWidget(self.proposed_details_label)

        self.proposed_counts_label = QLabel("")
        self.proposed_counts_label.setStyleSheet("color: #374151; font-size: 13px;")
        details_layout.addWidget(self.proposed_counts_label)

        layout.addWidget(self.proposed_details_frame)

        # Store current filter
        self._current_filter = 'all'

    def _load_all_data(self):
        """Load both pending orders and proposed claims projects."""
        self.refresh_btn.setEnabled(False)
        self.refresh_btn.setText("Loading...")
        self.status_label.setText("Loading data...")

        try:
            # Load pending orders
            self._load_orders()

            # Load projects with proposed claims
            self._load_proposed_projects()

            self.status_label.setText("Data loaded successfully")

        except Exception as e:
            self.status_label.setText(f"Error loading data: {e}")

        finally:
            self.refresh_btn.setEnabled(True)
            self.refresh_btn.setText("Refresh All")

    def _load_orders(self):
        """Load pending orders from server."""
        try:
            result = self.claims_manager.get_staff_pending_orders()
            self.orders = result.get('orders', [])
            self._populate_orders_table()
        except Exception as e:
            QMessageBox.warning(
                self,
                "Error",
                f"Failed to load pending orders:\n{e}"
            )

    def _populate_orders_table(self):
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
        self._update_order_details()

        # Update tab title with count
        self.tab_widget.setTabText(0, f"Pending Orders ({len(self.orders)})")

    def _load_proposed_projects(self):
        """Load projects with proposed claims."""
        try:
            self.projects_with_claims = self.claims_manager.get_projects_with_proposed_claims()
            self._populate_project_combo()
        except Exception as e:
            QMessageBox.warning(
                self,
                "Error",
                f"Failed to load proposed claims projects:\n{e}"
            )

    def _populate_project_combo(self):
        """Populate project combo box."""
        self.project_combo.clear()
        self.project_combo.addItem("-- Select a project --", None)

        total_claims = 0
        for project in self.projects_with_claims:
            name = project.get('name', 'Unknown')
            company = project.get('company_name', '')
            claim_count = project.get('total_claims', 0)
            total_claims += claim_count

            display = f"{name} ({company}) - {claim_count} claims"
            self.project_combo.addItem(display, project)

        # Update tab title with total
        self.tab_widget.setTabText(1, f"Proposed Claims ({total_claims})")

    def _on_project_selected(self, index: int):
        """Handle project selection."""
        if index <= 0:
            self.proposed_claims = []
            self.proposed_table.setRowCount(0)
            self.selected_project = None
            self._update_proposed_details()
            self._update_pull_button()
            return

        self.selected_project = self.project_combo.itemData(index)
        if self.selected_project:
            self._load_proposed_claims(self.selected_project['id'])

    def _load_proposed_claims(self, project_id: int):
        """Load proposed claims for a project."""
        try:
            result = self.claims_manager.get_proposed_claims(project_id)
            self.proposed_claims = result.get('proposed_claims', [])
            self._populate_proposed_table()
        except Exception as e:
            QMessageBox.warning(
                self,
                "Error",
                f"Failed to load proposed claims:\n{e}"
            )

    def _populate_proposed_table(self):
        """Populate proposed claims table."""
        # Filter claims based on current filter
        claims = self.proposed_claims
        if self._current_filter == 'approved':
            claims = [c for c in claims if c.get('properties', {}).get('approved')]

        self.proposed_table.setRowCount(len(claims))

        for row, claim in enumerate(claims):
            props = claim.get('properties', {})

            # Claim name
            name = props.get('claim_name', '-')
            self.proposed_table.setItem(row, 0, QTableWidgetItem(name))

            # Type
            claim_type = props.get('claim_type_display', props.get('claim_type', '-'))
            self.proposed_table.setItem(row, 1, QTableWidgetItem(claim_type))

            # Acreage
            acreage = props.get('acreage', 0)
            self.proposed_table.setItem(row, 2, QTableWidgetItem(f"{acreage:.2f}"))

            # PLSS Location
            plss = props.get('plss_location', '-')
            self.proposed_table.setItem(row, 3, QTableWidgetItem(plss))

            # Approved status
            approved = props.get('approved', False)
            approved_item = QTableWidgetItem("Yes" if approved else "No")
            approved_item.setForeground(QBrush(QColor('#059669' if approved else '#f59e0b')))
            self.proposed_table.setItem(row, 4, approved_item)

        self.proposed_table.clearSelection()
        self._update_proposed_details()

        # Enable/disable selection buttons based on whether there are claims
        has_claims = len(claims) > 0
        self.select_all_btn.setEnabled(has_claims)
        self.deselect_all_btn.setEnabled(has_claims)

    def _select_all_claims(self):
        """Select all claims in the proposed claims table."""
        self.proposed_table.selectAll()

    def _deselect_all_claims(self):
        """Deselect all claims in the proposed claims table."""
        self.proposed_table.clearSelection()

    def _set_filter(self, filter_type: str):
        """Set the proposed claims filter."""
        self._current_filter = filter_type

        # Update button states
        self.filter_all_btn.setChecked(filter_type == 'all')
        self.filter_approved_btn.setChecked(filter_type == 'approved')

        # Refresh table
        self._populate_proposed_table()

    def _on_tab_changed(self, index: int):
        """Handle tab change."""
        self._update_pull_button()

    def _on_order_selection_changed(self):
        """Handle order table selection change."""
        selected_rows = self.orders_table.selectedItems()
        if selected_rows:
            row = self.orders_table.currentRow()
            if 0 <= row < len(self.orders):
                self.selected_order = self.orders[row]
                self._update_order_details()
                self._update_pull_button()
                return

        self.selected_order = None
        self._update_order_details()
        self._update_pull_button()

    def _on_order_double_click(self):
        """Handle double click on order table row."""
        if self.selected_order:
            self._pull_selected()

    def _on_proposed_selection_changed(self):
        """Handle proposed claims selection change."""
        self._update_proposed_details()
        self._update_pull_button()

    def _update_order_details(self):
        """Update order details panel."""
        if not self.selected_order:
            self.order_details_label.setText("Select an order to view details")
            self.claimant_label.setText("")
            self.extras_label.setText("")
            return

        order = self.selected_order
        order_num = order.get('order_number', f"#{order.get('id', '?')}")
        company = order.get('company_name', 'Unknown')

        self.order_details_label.setText(f"<b>{order_num}</b> - {company}")

        # Claimant info
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

    def _update_proposed_details(self):
        """Update proposed claims details panel."""
        selected_rows = set()
        for item in self.proposed_table.selectedItems():
            selected_rows.add(item.row())

        if not selected_rows:
            self.proposed_details_label.setText("Select claims to pull into QGIS")
            self.proposed_counts_label.setText("")
            return

        count = len(selected_rows)
        project_name = self.selected_project.get('name', 'Unknown') if self.selected_project else 'Unknown'
        company_name = self.selected_project.get('company_name', '') if self.selected_project else ''

        self.proposed_details_label.setText(
            f"<b>{count} claim(s) selected</b> from {project_name} ({company_name})"
        )

        # Count approved vs pending
        approved = 0
        for row in selected_rows:
            claim = self._get_filtered_claims()[row]
            if claim.get('properties', {}).get('approved'):
                approved += 1
        pending = count - approved

        status_parts = []
        if approved:
            status_parts.append(f"{approved} approved")
        if pending:
            status_parts.append(f"{pending} pending approval")
        self.proposed_counts_label.setText(" | ".join(status_parts))

    def _get_filtered_claims(self) -> List[Dict[str, Any]]:
        """Get claims based on current filter."""
        if self._current_filter == 'approved':
            return [c for c in self.proposed_claims if c.get('properties', {}).get('approved')]
        return self.proposed_claims

    def _update_pull_button(self):
        """Update pull button state based on current tab and selection."""
        current_tab = self.tab_widget.currentIndex()

        if current_tab == 0:
            # Pending Orders tab
            self.pull_btn.setEnabled(self.selected_order is not None)
            self.pull_btn.setText("Pull Order into QGIS")
        else:
            # Proposed Claims tab
            selected_rows = set()
            for item in self.proposed_table.selectedItems():
                selected_rows.add(item.row())
            has_selection = len(selected_rows) > 0 and self.selected_project is not None
            self.pull_btn.setEnabled(has_selection)
            count = len(selected_rows)
            self.pull_btn.setText(f"Pull {count} Claim(s) into QGIS" if count else "Pull Claims into QGIS")

    def _pull_selected(self):
        """Pull selected order or proposed claims."""
        current_tab = self.tab_widget.currentIndex()

        if current_tab == 0:
            self._pull_selected_order()
        else:
            self._pull_selected_proposed_claims()

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

    def _pull_selected_proposed_claims(self):
        """Pull selected proposed claims and emit signal."""
        if not self.selected_project:
            return

        # Get selected claims
        selected_rows = set()
        for item in self.proposed_table.selectedItems():
            selected_rows.add(item.row())

        if not selected_rows:
            return

        filtered_claims = self._get_filtered_claims()
        selected_claims = [filtered_claims[row] for row in sorted(selected_rows)]

        project_name = self.selected_project.get('name', 'Unknown')
        count = len(selected_claims)

        # Confirm
        reply = QMessageBox.question(
            self,
            "Pull Proposed Claims",
            f"Pull {count} proposed claim(s) from {project_name} into QGIS?\n\n"
            f"This will load the claim polygons for processing "
            f"through the claims workflow.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )

        if reply != QMessageBox.Yes:
            return

        # Build data to emit
        claims_data = {
            'source': 'proposed_claims',
            'project_id': self.selected_project.get('id'),
            'project_name': project_name,
            'company_id': self.selected_project.get('company_id'),
            'company_name': self.selected_project.get('company_name'),
            'claims': selected_claims
        }

        # Emit signal
        self.proposed_claims_selected.emit(claims_data)
        self.accept()

    def get_selected_order(self) -> Optional[Dict[str, Any]]:
        """Get the selected order data."""
        return self.selected_order

    # =========================================================================
    # Styles
    # =========================================================================

    def _get_tab_style(self) -> str:
        """Get tab widget style matching main UI."""
        return """
            QTabWidget::pane {
                border: 1px solid #d1d5db;
                border-radius: 4px;
                background-color: white;
            }
            QTabBar::tab {
                padding: 10px 25px;
                font-weight: bold;
                min-width: 90px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                margin-right: 4px;
            }
            QTabBar::tab:selected {
                background-color: #5bbad5;
                color: white;
            }
            QTabBar::tab:!selected {
                background-color: #e5e7eb;
                color: #374151;
            }
            QTabBar::tab:hover:!selected {
                background-color: #d1d5db;
            }
        """

    def _get_table_style(self) -> str:
        """Get table style."""
        return """
            QTableWidget {
                border: 1px solid #e5e7eb;
                border-radius: 8px;
                background-color: white;
                gridline-color: #e5e7eb;
                selection-background-color: #5bbad5;
                selection-color: white;
            }
            QTableWidget::item {
                padding: 8px;
                color: #374151;
            }
            QTableWidget::item:selected {
                background-color: #5bbad5;
                color: white;
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

    def _get_details_frame_style(self) -> str:
        """Get details frame style."""
        return """
            QFrame {
                background-color: #f9fafb;
                border: 1px solid #e5e7eb;
                border-radius: 8px;
                padding: 12px;
            }
        """

    def _get_combo_style(self) -> str:
        """Get combo box style."""
        return """
            QComboBox {
                padding: 8px 12px;
                border: 1px solid #d1d5db;
                border-radius: 6px;
                background-color: white;
            }
            QComboBox:hover {
                border-color: #9ca3af;
            }
            QComboBox::drop-down {
                border: none;
                padding-right: 8px;
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
