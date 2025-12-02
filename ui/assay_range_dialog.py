# -*- coding: utf-8 -*-
"""
Assay Range Configuration selection dialog.

Allows users to select an AssayRangeConfiguration for visualizing
PointSample or DrillSample data with color-coded grade values.
"""
from typing import Optional, List, Dict, Any, Callable
from qgis.PyQt.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QPushButton, QFrame, QTableWidget, QTableWidgetItem,
    QHeaderView, QSpacerItem, QSizePolicy, QGroupBox,
    QProgressBar, QApplication
)
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.PyQt.QtGui import QFont, QColor, QBrush


class AssayRangeDialog(QDialog):
    """
    Dialog for selecting and viewing AssayRangeConfiguration.

    Displays available configurations for the current project,
    shows the color ranges in a table, and allows the user to
    select one for visualization.

    Signals:
        config_selected: Emitted when user confirms selection, with config dict
        pull_requested: Emitted when user wants to pull data with selected config
    """

    config_selected = pyqtSignal(dict)  # Selected configuration
    pull_requested = pyqtSignal(dict)   # Request to pull with this config

    def __init__(
        self,
        parent=None,
        api_client=None,
        project_id: int = None,
        company_id: int = None,
        model_type: str = 'PointSample'
    ):
        """
        Initialize the dialog.

        Args:
            parent: Parent widget
            api_client: API client for fetching configurations
            project_id: Current project ID
            company_id: Current company ID (to fetch company-wide configs)
            model_type: 'PointSample' or 'DrillSample'
        """
        super().__init__(parent)
        self.api_client = api_client
        self.project_id = project_id
        self.company_id = company_id
        self.model_type = model_type
        self.configurations: List[Dict[str, Any]] = []
        self.merge_settings_map: Dict[int, Dict[str, Any]] = {}  # Map of merge_settings_id -> settings
        self.selected_config: Optional[Dict[str, Any]] = None

        self._setup_ui()
        self._load_configurations()

    def _setup_ui(self):
        """Set up the dialog UI."""
        self.setWindowTitle("Select Assay Visualization")
        self.setMinimumWidth(550)
        self.setMinimumHeight(450)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setSpacing(16)
        layout.setContentsMargins(24, 24, 24, 24)

        # Header
        header_label = QLabel("Assay Range Configuration")
        header_font = QFont()
        header_font.setPointSize(16)
        header_font.setBold(True)
        header_label.setFont(header_font)
        header_label.setStyleSheet("color: #1f2937;")
        layout.addWidget(header_label)

        # Description
        desc_label = QLabel(
            f"Select a color scheme to visualize {self.model_type} assay values. "
            "The ranges below show how values will be colored on the map."
        )
        desc_label.setWordWrap(True)
        desc_label.setStyleSheet("color: #6b7280; margin-bottom: 8px;")
        layout.addWidget(desc_label)

        # Separator
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("background-color: #e5e7eb;")
        layout.addWidget(line)

        # Configuration selector
        selector_layout = QHBoxLayout()

        element_label = QLabel("Element:")
        element_label.setStyleSheet("font-weight: bold; color: #374151;")
        selector_layout.addWidget(element_label)

        self.config_combo = QComboBox()
        self.config_combo.setMinimumWidth(300)
        self.config_combo.setStyleSheet(self._get_combo_style())
        self.config_combo.currentIndexChanged.connect(self._on_config_selected)
        selector_layout.addWidget(self.config_combo)

        selector_layout.addStretch()

        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setStyleSheet(self._get_secondary_button_style())
        self.refresh_button.clicked.connect(self._load_configurations)
        selector_layout.addWidget(self.refresh_button)

        layout.addLayout(selector_layout)

        # Progress bar (hidden by default)
        self.progress_bar = QProgressBar()
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: 1px solid #d1d5db;
                border-radius: 4px;
                text-align: center;
                height: 20px;
            }
            QProgressBar::chunk {
                background-color: #2563eb;
                border-radius: 3px;
            }
        """)
        self.progress_bar.hide()
        layout.addWidget(self.progress_bar)

        # Range table group
        range_group = QGroupBox("Color Ranges")
        range_group.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                border: 1px solid #e5e7eb;
                border-radius: 8px;
                margin-top: 12px;
                padding-top: 16px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 8px;
            }
        """)
        range_layout = QVBoxLayout(range_group)

        self.range_table = QTableWidget()
        self.range_table.setColumnCount(5)
        self.range_table.setHorizontalHeaderLabels([
            "From", "To", "Color", "Size", "Label"
        ])
        self.range_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.range_table.verticalHeader().setVisible(False)
        self.range_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.range_table.setSelectionMode(QTableWidget.NoSelection)
        self.range_table.setStyleSheet("""
            QTableWidget {
                border: none;
                background-color: white;
                gridline-color: #e5e7eb;
            }
            QHeaderView::section {
                background-color: #f9fafb;
                padding: 8px;
                border: none;
                border-bottom: 1px solid #e5e7eb;
                font-weight: bold;
                color: #374151;
            }
            QTableWidget::item {
                padding: 8px;
            }
        """)
        range_layout.addWidget(self.range_table)

        # Default color info
        self.default_color_label = QLabel()
        self.default_color_label.setStyleSheet("color: #6b7280; padding: 8px;")
        range_layout.addWidget(self.default_color_label)

        layout.addWidget(range_group)

        # Merge Settings Info Group
        merge_group = QGroupBox("Assay Merge Settings")
        merge_group.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                border: 1px solid #e5e7eb;
                border-radius: 8px;
                margin-top: 12px;
                padding-top: 16px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 8px;
            }
        """)
        merge_layout = QVBoxLayout(merge_group)

        self.merge_settings_label = QLabel()
        self.merge_settings_label.setWordWrap(True)
        self.merge_settings_label.setStyleSheet("color: #374151; padding: 8px;")
        merge_layout.addWidget(self.merge_settings_label)

        layout.addWidget(merge_group)

        # Status label
        self.status_label = QLabel()
        self.status_label.setStyleSheet("color: #6b7280; font-style: italic;")
        layout.addWidget(self.status_label)

        layout.addStretch()

        # Buttons
        button_layout = QHBoxLayout()
        button_layout.setSpacing(12)

        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setStyleSheet(self._get_secondary_button_style())
        self.cancel_button.clicked.connect(self.reject)
        button_layout.addWidget(self.cancel_button)

        button_layout.addStretch()

        self.pull_button = QPushButton(f"Pull {self.model_type}s with Colors")
        self.pull_button.setStyleSheet(self._get_primary_button_style())
        self.pull_button.clicked.connect(self._on_pull_clicked)
        self.pull_button.setEnabled(False)
        button_layout.addWidget(self.pull_button)

        layout.addLayout(button_layout)

    def _get_combo_style(self) -> str:
        """Get stylesheet for combo box."""
        return """
            QComboBox {
                padding: 8px 12px;
                border: 1px solid #d1d5db;
                border-radius: 6px;
                background-color: white;
                font-size: 14px;
            }
            QComboBox:focus {
                border-color: #2563eb;
            }
            QComboBox::drop-down {
                border: none;
                width: 30px;
            }
            QComboBox::down-arrow {
                width: 12px;
                height: 12px;
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
                min-width: 120px;
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

    def _load_configurations(self):
        """Load available configurations and merge settings from API."""
        if not self.api_client:
            self.status_label.setText("No API client available")
            return

        if not self.project_id and not self.company_id:
            self.status_label.setText("No project or company selected")
            return

        self.status_label.setText("Loading configurations...")
        self.config_combo.clear()
        self.configurations = []
        self.merge_settings_map = {}
        self.pull_button.setEnabled(False)
        QApplication.processEvents()

        try:
            # Fetch AssayMergeSettings for the company first
            # These are needed to display merge strategy and units info
            if self.company_id:
                merge_settings_list = self.api_client.get_assay_merge_settings(
                    company_id=self.company_id
                )
                # Build a map of merge_settings_id -> settings
                for ms in merge_settings_list:
                    self.merge_settings_map[ms['id']] = ms

            # Fetch configurations using the dedicated API method
            # This already filters to is_active=True
            configs = self.api_client.get_assay_range_configurations(
                project_id=self.project_id,
                is_active=True
            )

            self.configurations = configs

            if not configs:
                self.status_label.setText(
                    "No color configurations found for this project. "
                    "Please create one on geodb.io first."
                )
                self._clear_range_table()
                self._clear_merge_settings()
                return

            # Populate combo box
            for config in configs:
                element = config.get('element', '?')
                element_display = config.get('element_display', element)
                name = config.get('name', 'Unnamed')
                units = config.get('units', '')

                display_text = f"{element_display} - {name} ({units})"
                self.config_combo.addItem(display_text, config)

            self.status_label.setText(f"Found {len(configs)} configuration(s)")

            # Select first item
            if configs:
                self.config_combo.setCurrentIndex(0)
                self._on_config_selected(0)

        except Exception as e:
            self.status_label.setText(f"Error loading configurations: {str(e)}")

    def _on_config_selected(self, index: int):
        """Handle configuration selection change."""
        if index < 0 or index >= len(self.configurations):
            self._clear_range_table()
            self._clear_merge_settings()
            self.pull_button.setEnabled(False)
            return

        config = self.configurations[index]
        self.selected_config = config
        self._display_ranges(config)
        self._display_merge_settings(config)
        self.pull_button.setEnabled(True)

    def _display_ranges(self, config: Dict[str, Any]):
        """Display color ranges in the table."""
        ranges = config.get('ranges', [])
        default_color = config.get('default_color', '#CCCCCC')
        units = config.get('units', '')

        # Sort by from_value
        sorted_ranges = sorted(ranges, key=lambda r: r.get('from_value', 0))

        self.range_table.setRowCount(len(sorted_ranges))

        for row, range_item in enumerate(sorted_ranges):
            from_val = range_item.get('from_value', 0)
            to_val = range_item.get('to_value', 0)
            color = range_item.get('color', '#CCCCCC')
            size = range_item.get('size', 2)
            label = range_item.get('label', '')

            # From value
            from_item = QTableWidgetItem(f"{from_val:,.4g} {units}")
            from_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.range_table.setItem(row, 0, from_item)

            # To value
            to_item = QTableWidgetItem(f"{to_val:,.4g} {units}")
            to_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.range_table.setItem(row, 1, to_item)

            # Color (with background)
            color_item = QTableWidgetItem(color)
            color_item.setTextAlignment(Qt.AlignCenter)
            try:
                qcolor = QColor(color)
                color_item.setBackground(QBrush(qcolor))
                # Use white or black text based on luminance
                luminance = 0.299 * qcolor.red() + 0.587 * qcolor.green() + 0.114 * qcolor.blue()
                text_color = QColor('#ffffff') if luminance < 128 else QColor('#000000')
                color_item.setForeground(QBrush(text_color))
            except:
                pass
            self.range_table.setItem(row, 2, color_item)

            # Size
            size_item = QTableWidgetItem(str(size))
            size_item.setTextAlignment(Qt.AlignCenter)
            self.range_table.setItem(row, 3, size_item)

            # Label
            label_item = QTableWidgetItem(label)
            self.range_table.setItem(row, 4, label_item)

        # Display default color
        self.default_color_label.setText(
            f"Default color for null/out-of-range values: {default_color}"
        )

    def _display_merge_settings(self, config: Dict[str, Any]):
        """Display merge settings info for this configuration."""
        merge_settings_id = config.get('assay_merge_settings')

        if not merge_settings_id or merge_settings_id not in self.merge_settings_map:
            self.merge_settings_label.setText(
                "Merge settings information not available."
            )
            return

        merge_settings = self.merge_settings_map[merge_settings_id]

        # Extract merge settings details
        name = merge_settings.get('name', 'Unknown')
        default_strategy = merge_settings.get('default_strategy', 'high')
        default_units = merge_settings.get('default_units', 'ppm')
        convert_bdl = merge_settings.get('convert_bdl', True)
        bdl_multiplier = merge_settings.get('bdl_multiplier', 0.5)

        # Check for element-specific overrides
        element = config.get('element', '')
        element_overrides = merge_settings.get('element_overrides', [])

        # Find override for this element
        element_override = None
        for override in element_overrides:
            if override.get('element') == element:
                element_override = override
                break

        # Build info text
        info_parts = [
            f"<b>Configuration:</b> {name}",
            f"<b>Merge Strategy:</b> {default_strategy.title()}"
        ]

        if element_override:
            override_strategy = element_override.get('strategy', default_strategy)
            override_units = element_override.get('target_units', default_units)
            info_parts.append(
                f"<b>Element Override ({element}):</b> {override_strategy.title()}, {override_units}"
            )
        else:
            info_parts.append(f"<b>Default Units:</b> {default_units}")

        if convert_bdl:
            info_parts.append(
                f"<b>Below Detection Limit:</b> Convert to {bdl_multiplier * 100:.0f}% of detection limit"
            )
        else:
            info_parts.append("<b>Below Detection Limit:</b> No conversion")

        self.merge_settings_label.setText("<br>".join(info_parts))

    def _clear_merge_settings(self):
        """Clear merge settings display."""
        self.merge_settings_label.setText("")

    def _clear_range_table(self):
        """Clear the range table."""
        self.range_table.setRowCount(0)
        self.default_color_label.setText("")
        self.selected_config = None

    def _on_pull_clicked(self):
        """Handle pull button click."""
        if self.selected_config:
            self.pull_requested.emit(self.selected_config)
            self.config_selected.emit(self.selected_config)
            self.accept()

    def get_selected_config(self) -> Optional[Dict[str, Any]]:
        """Get the selected configuration."""
        return self.selected_config

    def set_progress(self, value: int, message: str = ""):
        """Update progress bar."""
        self.progress_bar.show()
        self.progress_bar.setValue(value)
        if message:
            self.status_label.setText(message)
        QApplication.processEvents()

    def hide_progress(self):
        """Hide progress bar."""
        self.progress_bar.hide()

    @staticmethod
    def select_config(
        parent=None,
        api_client=None,
        project_id: int = None,
        company_id: int = None,
        model_type: str = 'PointSample'
    ) -> Optional[Dict[str, Any]]:
        """
        Static method to show dialog and return selected configuration.

        Args:
            parent: Parent widget
            api_client: API client instance
            project_id: Current project ID
            company_id: Current company ID
            model_type: 'PointSample' or 'DrillSample'

        Returns:
            Selected configuration dict or None if cancelled
        """
        dialog = AssayRangeDialog(parent, api_client, project_id, company_id, model_type)
        result = dialog.exec_()

        if result == QDialog.Accepted:
            return dialog.get_selected_config()

        return None
