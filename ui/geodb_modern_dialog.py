# -*- coding: utf-8 -*-
"""
/***************************************************************************
 GeodbModernDialog
 Modern dialog for Geodb.io QGIS plugin v2.0
 ***************************************************************************/
"""

import os
import sys
from typing import Optional, Callable, List, Dict, Any
from qgis.PyQt import uic
from qgis.PyQt.QtCore import Qt, QTimer
from qgis.PyQt.QtWidgets import (
    QDialog, QMessageBox, QLabel, QComboBox, QPushButton,
    QVBoxLayout, QGroupBox, QTableWidget, QTableWidgetItem,
    QHBoxLayout, QHeaderView
)
from qgis.PyQt.QtGui import QColor, QTextCursor

# Import our new managers
from ..utils.config import Config, DEV_MODE
from ..utils.logger import PluginLogger
from ..api.client import APIClient
from ..api.exceptions import (
    AuthenticationError,
    PermissionError as APIPermissionError,
    NetworkError,
    ValidationError
)
from ..managers.auth_manager import AuthManager
from ..managers.project_manager import ProjectManager
from ..managers.data_manager import DataManager
from ..managers.sync_manager import SyncManager
from ..managers.storage_manager import StorageManager, StorageMode
from ..models.auth import AuthSession, UserContext
from ..processors.style_processor import StyleProcessor
from .login_dialog import LoginDialog
from .assay_range_dialog import AssayRangeDialog
from .storage_dialog import StorageConfigDialog
from .field_work_dialog import FieldWorkDialog

# Ensure plugin directory is in sys.path for UI resource imports
plugin_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if plugin_dir not in sys.path:
    sys.path.insert(0, plugin_dir)

# Load UI file
FORM_CLASS, _ = uic.loadUiType(os.path.join(
    os.path.dirname(__file__), 'geodb_modern_dialog.ui'))


class GeodbModernDialog(QDialog, FORM_CLASS):
    """Modern dialog for Geodb.io plugin with clean UI and manager integration."""
    
    def __init__(self, parent=None):
        """Initialize the dialog."""
        super(GeodbModernDialog, self).__init__(parent)
        self.setupUi(self)
        
        # Initialize managers
        self.config = Config()
        self.logger = PluginLogger.get_logger()
        self.api_client = APIClient(self.config)
        self.auth_manager = AuthManager(self.config, self.api_client)
        self.project_manager = ProjectManager(self.config, self.api_client)
        self.sync_manager = SyncManager(self.config)
        self.storage_manager = StorageManager()
        self.data_manager = DataManager(
            self.config,
            self.api_client,
            self.project_manager,
            self.sync_manager
        )
        self.style_processor = StyleProcessor()

        # Current state
        self.current_session: Optional[AuthSession] = None
        self.current_model: Optional[str] = None
        self.current_assay_config: Optional[dict] = None  # Selected AssayRangeConfiguration
        self.assay_configurations: List[Dict[str, Any]] = []  # Loaded configurations
        self.merge_settings_map: Dict[int, Dict[str, Any]] = {}  # Map of merge_settings_id -> settings
        self.project_files: List[Dict[str, Any]] = []  # Loaded ProjectFile records
        self.selected_project_file: Optional[Dict[str, Any]] = None  # Currently selected file

        # Add storage button dynamically (since not in .ui file)
        self._add_storage_button()

        # Add field work button dynamically
        self._add_field_work_button()

        # Replace manual assay options with AssayRangeConfiguration selector
        self._replace_assay_options_ui()

        # Add ProjectFile options UI
        self._add_projectfile_options_ui()

        # Connect signals
        self._connect_signals()

        # Initialize UI state
        self._initialize_ui()

        # Try to restore session
        self._try_restore_session()
    
    def _connect_signals(self):
        """Connect UI signals to handlers."""
        # Authentication
        self.loginButton.clicked.connect(self._on_login_clicked)
        self.logoutButton.clicked.connect(self._on_logout_clicked)
        self.localModeCheckBox.stateChanged.connect(self._on_local_mode_changed)
        
        # Project selection
        self.companyComboBox.currentIndexChanged.connect(self._on_company_changed)
        self.projectComboBox.currentIndexChanged.connect(self._on_project_changed)
        
        # Model selection
        self.modelListWidget.currentItemChanged.connect(self._on_model_selected)
        
        # Actions
        self.pullButton.clicked.connect(self._on_pull_clicked)
        self.pushButton.clicked.connect(self._on_push_clicked)
        
        # Options
        self.includeMergedAssaysCheckBox.stateChanged.connect(self._on_assay_options_changed)
        
        # Messages
        self.clearMessagesButton.clicked.connect(self._clear_messages)
        self.closeButton.clicked.connect(self.close)
    
    def _initialize_ui(self):
        """Initialize UI state."""
        self.modelListWidget.setCurrentRow(0)
        self._update_auth_status(False)

        # Hide local mode checkbox unless in dev mode
        if not DEV_MODE:
            self.localModeCheckBox.setVisible(False)

        # Set local mode checkbox from config
        is_local = self.config.get('api.use_local', False)
        self.localModeCheckBox.setChecked(is_local)
        
        self._log_message("Welcome to Geodb.io QGIS Plugin v2.0", "info")
        self._log_message("Please login to begin synchronizing your geospatial data.", "info")
        if is_local:
            self._log_message("⚠️ LOCAL DEVELOPMENT MODE ENABLED", "warning")
    
    def _try_restore_session(self):
        """Try to restore previous session."""
        try:
            session = self.auth_manager.restore_session()
            if session:
                self.current_session = session

                # Load user context into project manager
                if session.user_context:
                    self.project_manager.load_from_user_context(session.user_context)

                self._update_auth_status(True)
                self._load_projects_from_context()

                user_name = session.user.full_name or session.user.email
                self._log_message(f"Session restored. Welcome back, {user_name}!", "success")

                # Show active project if set
                if session.has_active_project():
                    project = session.user_context.active_project
                    self._log_message(f"Active project: {project.name}", "info")

        except Exception as e:
            self.logger.error(f"Failed to restore session: {e}")
    
    # ==================== AUTHENTICATION ====================

    def _on_login_clicked(self):
        """Handle login button click - opens the new styled login dialog."""
        # Create and show the login dialog
        login_dialog = LoginDialog(self, self.auth_manager)

        # Connect signals
        login_dialog.login_successful.connect(self._on_login_successful)

        # Show dialog
        result = login_dialog.exec_()

        if result != QDialog.Accepted:
            self._log_message("Login cancelled.", "info")

    def _on_login_successful(self, token: str, user_context: dict):
        """Handle successful login from LoginDialog."""
        try:
            # Get the session from auth manager
            self.current_session = self.auth_manager.get_session()

            if not self.current_session:
                self._log_message("Failed to get session after login.", "error")
                return

            # Load user context into project manager
            if self.current_session.user_context:
                self.project_manager.load_from_user_context(self.current_session.user_context)

            # Update UI
            self._update_auth_status(True)
            self._load_projects_from_context()

            # Welcome message
            user_name = self.current_session.user.full_name or self.current_session.user.email
            self._log_message(f"Login successful! Welcome, {user_name}!", "success")

            # Show active project if set
            if self.current_session.has_active_project():
                project = self.current_session.user_context.active_project
                self._log_message(f"Active project: {project.name}", "info")

        except Exception as e:
            self.logger.exception("Error after login")
            self._log_message(f"Error after login: {e}", "error")
    
    def _on_logout_clicked(self):
        """Handle logout button click."""
        try:
            self._log_message("Logging out...", "info")
            self.auth_manager.logout()
            self.current_session = None
            
            # Update UI
            self._update_auth_status(False)
            self._clear_projects()
            self._log_message("Logged out successfully.", "success")
            
        except Exception as e:
            self.logger.exception("Logout error")
            self._log_message(f"Logout error: {e}", "error")
    
    def _on_local_mode_changed(self, state):
        """Handle local development mode toggle."""
        is_enabled = bool(state)
        
        # Update configuration
        self.config.toggle_local_mode(is_enabled)
        
        # Update API client with new base URL
        self.api_client = APIClient(self.config, self.api_client.token)
        
        # Update all managers with new API client
        self.auth_manager.api_client = self.api_client
        self.project_manager.api_client = self.api_client
        self.data_manager.api_client = self.api_client
        
        # Log the change
        mode = "LOCAL DEVELOPMENT" if is_enabled else "PRODUCTION"
        url = self.config.base_url
        self._log_message(f"⚙️ Switched to {mode} mode: {url}", "warning")
        
        # Warn if logged in
        if self.current_session:
            self._log_message("⚠️ You may need to log out and log in again", "warning")
    
    def _update_auth_status(self, logged_in: bool):
        """Update authentication status in UI."""
        if logged_in and self.current_session:
            self.statusValue.setText("✓ Logged in")
            self.statusValue.setStyleSheet("color: #4caf50; font-weight: bold;")
            self.userValue.setText(self.current_session.user.username)
            self.loginButton.setEnabled(False)
            self.logoutButton.setEnabled(True)
            self.companyComboBox.setEnabled(True)
        else:
            self.statusValue.setText("✗ Not logged in")
            self.statusValue.setStyleSheet("color: #d32f2f; font-weight: bold;")
            self.userValue.setText("-")
            self.loginButton.setEnabled(True)
            self.logoutButton.setEnabled(False)
            self.companyComboBox.setEnabled(False)
            self.projectComboBox.setEnabled(False)
            self.pullButton.setEnabled(False)
            self.pushButton.setEnabled(False)

        # Update field work button state
        self._update_field_work_button_state()
    
    # ==================== PROJECT MANAGEMENT ====================

    def _load_projects(self):
        """Load companies and projects from API (legacy method)."""
        try:
            self._log_message("Loading projects...", "info")
            companies = self.project_manager.load_companies()
            self._populate_project_combos(companies)

        except Exception as e:
            self.logger.exception("Failed to load projects")
            self._log_message(f"Failed to load projects: {e}", "error")

    def _load_projects_from_context(self):
        """Load companies and projects from already-loaded user context."""
        companies = self.project_manager.get_companies()
        self._populate_project_combos(companies)

        # Auto-select current company/project if set
        if self.project_manager.active_company:
            for i in range(self.companyComboBox.count()):
                company = self.companyComboBox.itemData(i)
                if company and company.id == self.project_manager.active_company.id:
                    self.companyComboBox.setCurrentIndex(i)
                    break

        if self.project_manager.active_project:
            for i in range(self.projectComboBox.count()):
                project = self.projectComboBox.itemData(i)
                if project and project.id == self.project_manager.active_project.id:
                    self.projectComboBox.setCurrentIndex(i)
                    break

    def _populate_project_combos(self, companies):
        """Populate company and project combo boxes."""
        self.companyComboBox.clear()
        for company in companies:
            self.companyComboBox.addItem(company.name, company)

        if companies:
            self._log_message(f"Loaded {len(companies)} companies.", "success")
        else:
            self._log_message("No companies found.", "warning")
    
    def _clear_projects(self):
        """Clear project selection."""
        self.companyComboBox.clear()
        self.projectComboBox.clear()
        self.permissionValue.setText("-")
    
    def _on_company_changed(self, index: int):
        """Handle company selection change."""
        if index < 0:
            return
        
        company = self.companyComboBox.itemData(index)
        if not company:
            return
        
        # Populate projects
        self.projectComboBox.clear()
        for project in company.projects:
            self.projectComboBox.addItem(project.name, project)
        
        self.projectComboBox.setEnabled(True)
    
    def _on_project_changed(self, index: int):
        """Handle project selection change."""
        if index < 0:
            self.pullButton.setEnabled(False)
            self.pushButton.setEnabled(False)
            self._update_storage_status()
            self._update_field_work_button_state()
            return

        project = self.projectComboBox.itemData(index)
        if not project:
            return

        try:
            # Select project (notifies server and gets updated permissions)
            self.project_manager.select_project(project)
            self._log_message(f"Selected project: {project.name}", "success")

            # Update permission display based on user_status from context
            user_status = self.project_manager.get_permission_level()
            if user_status:
                self.permissionValue.setText(user_status.upper())

                # Enable buttons based on permissions
                can_view = self.project_manager.can_view()
                can_edit = self.project_manager.can_edit()

                self.pullButton.setEnabled(can_view)
                self.pushButton.setEnabled(can_edit)

                if can_edit:
                    self.permissionValue.setStyleSheet("color: #4caf50; font-weight: bold;")
                elif can_view:
                    self.permissionValue.setStyleSheet("color: #ff9800; font-weight: bold;")
                else:
                    self.permissionValue.setStyleSheet("color: #d32f2f; font-weight: bold;")
            else:
                self.permissionValue.setText("No permission")
                self.permissionValue.setStyleSheet("color: #d32f2f; font-weight: bold;")
                self.pullButton.setEnabled(False)
                self.pushButton.setEnabled(False)

            # Update storage status for this project
            self._update_storage_status()

            # Update field work button state
            self._update_field_work_button_state()

        except Exception as e:
            self.logger.exception("Failed to select project")
            self._log_message(f"Failed to select project: {e}", "error")

    # ==================== MODEL SELECTION ====================
    
    def _on_model_selected(self, current, previous):
        """Handle model selection from list."""
        if not current:
            return

        model_name = current.text()
        self.current_model = model_name
        self.current_assay_config = None  # Reset assay config when model changes

        # Update title
        self.selectedModelLabel.setText(f"{model_name}")

        # Show/hide options based on model
        # First hide all model-specific option groups
        self.includeCompanyLandCheckBox.setVisible(False)
        self.assayOptionsGroupBox.setVisible(False)
        if hasattr(self, 'projectFileGroupBox'):
            self.projectFileGroupBox.setVisible(False)

        if model_name == "LandHolding":
            self.includeCompanyLandCheckBox.setVisible(True)
            self.pushButton.setEnabled(self.project_manager.can_edit())
        elif model_name in ["PointSample", "DrillSample"]:
            self.assayOptionsGroupBox.setVisible(True)
            self.pushButton.setEnabled(self.project_manager.can_edit())
            # Show sample type filter only for PointSample (not DrillSample)
            if hasattr(self, 'sampleTypeComboBox'):
                self.sampleTypeComboBox.setVisible(model_name == 'PointSample')
            if hasattr(self, 'sampleTypeLabel'):
                self.sampleTypeLabel.setVisible(model_name == 'PointSample')
            # Load assay configurations when sample model is selected
            self._load_assay_configurations()
        elif model_name == "ProjectFile":
            # ProjectFile is raster/file-based - show file selection UI
            if hasattr(self, 'projectFileGroupBox'):
                self.projectFileGroupBox.setVisible(True)
                # Load available files
                self._load_project_files()
            # Disable push for ProjectFile (read-only in QGIS plugin)
            self.pushButton.setEnabled(False)
        else:
            self.pushButton.setEnabled(self.project_manager.can_edit())

        self._log_message(f"Selected model: {model_name}", "info")
    
    def _on_assay_options_changed(self, state):
        """Enable/disable assay merge controls."""
        # This method is now deprecated since we replaced manual controls
        # Kept for backward compatibility during transition
        pass

    def _load_assay_configurations(self):
        """Load AssayRangeConfigurations and AssayMergeSettings from API."""
        project = self.project_manager.active_project
        company = self.project_manager.active_company

        if not project:
            self._log_message("Please select a project first to load assay configurations.", "warning")
            return

        self.assayConfigComboBox.clear()
        self.assay_configurations = []
        self.merge_settings_map = {}
        self.current_assay_config = None

        try:
            # Fetch AssayMergeSettings for the company
            if company:
                merge_settings_list = self.api_client.get_assay_merge_settings(
                    company_id=company.id
                )
                for ms in merge_settings_list:
                    self.merge_settings_map[ms['id']] = ms
                self._log_message(f"Loaded {len(merge_settings_list)} merge settings configuration(s)", "info")

            # Fetch AssayRangeConfigurations for the project
            configs = self.api_client.get_assay_range_configurations(
                project_id=project.id,
                is_active=True
            )
            self.assay_configurations = configs

            if not configs:
                self._log_message("No assay configurations found for this project.", "warning")
                self.mergeSettingsLabel.setText("No configurations available. Create one on geodb.io first.")
                self.rangesTable.setRowCount(0)
                return

            # Populate combo box
            for config in configs:
                element = config.get('element', '?')
                element_display = config.get('element_display', element)
                name = config.get('name', 'Unnamed')
                units = config.get('units', '')
                display_text = f"{element_display} - {name} ({units})"
                self.assayConfigComboBox.addItem(display_text)

            self._log_message(f"Loaded {len(configs)} assay configuration(s)", "success")

            # Select first item
            if configs:
                self.assayConfigComboBox.setCurrentIndex(0)

        except Exception as e:
            self._log_message(f"Error loading assay configurations: {str(e)}", "error")
            self.logger.error(f"Failed to load assay configurations: {e}", exc_info=True)

        # Also populate sample types from user context
        self._populate_sample_types()

    def _populate_sample_types(self):
        """Populate sample type combo box from user context."""
        # Clear existing items except "All Types"
        while self.sampleTypeComboBox.count() > 1:
            self.sampleTypeComboBox.removeItem(1)

        # Get sample types from user context
        if not self.current_session or not self.current_session.user_context:
            return

        sample_types = self.current_session.user_context.point_sample_types
        if not sample_types:
            self._log_message("No sample types available for this company.", "info")
            return

        # Add each sample type
        for pst in sample_types:
            self.sampleTypeComboBox.addItem(pst.name, pst.id)

        self._log_message(f"Loaded {len(sample_types)} sample type(s)", "info")

    def _get_selected_sample_type(self):
        """Get the currently selected sample type ID, or None for 'All Types'."""
        index = self.sampleTypeComboBox.currentIndex()
        if index <= 0:  # "All Types" or nothing selected
            return None
        return self.sampleTypeComboBox.itemData(index)

    def _get_selected_sample_type_name(self):
        """Get the currently selected sample type name, or None for 'All Types'."""
        index = self.sampleTypeComboBox.currentIndex()
        if index <= 0:  # "All Types" or nothing selected
            return None
        return self.sampleTypeComboBox.currentText()

    def _on_assay_config_selected(self, index: int):
        """Handle assay configuration selection change."""
        if index < 0 or index >= len(self.assay_configurations):
            self.current_assay_config = None
            self.mergeSettingsLabel.setText("No configuration selected")
            self.rangesTable.setRowCount(0)
            return

        config = self.assay_configurations[index]
        self.current_assay_config = config

        # Display merge settings
        self._display_merge_settings(config)

        # Display color ranges
        self._display_color_ranges(config)

        self._log_message(f"Selected configuration: {config.get('name', 'Unnamed')}", "info")

    def _display_merge_settings(self, config: Dict[str, Any]):
        """Display merge settings info for the selected configuration."""
        merge_settings_id = config.get('assay_merge_settings')

        if not merge_settings_id or merge_settings_id not in self.merge_settings_map:
            self.mergeSettingsLabel.setText("Merge settings information not available.")
            return

        merge_settings = self.merge_settings_map[merge_settings_id]

        # Extract details
        name = merge_settings.get('name', 'Unknown')
        default_strategy = merge_settings.get('default_strategy', 'high')
        default_units = merge_settings.get('default_units', 'ppm')
        convert_bdl = merge_settings.get('convert_bdl', True)
        bdl_multiplier = merge_settings.get('bdl_multiplier', 0.5)

        # Check for element-specific override
        element = config.get('element', '')
        element_overrides = merge_settings.get('element_overrides', [])
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

        self.mergeSettingsLabel.setText("<br>".join(info_parts))

    def _display_color_ranges(self, config: Dict[str, Any]):
        """Display color ranges in the table."""
        ranges = config.get('ranges', [])
        units = config.get('units', '')

        self.rangesTable.setRowCount(len(ranges))

        for row, range_item in enumerate(sorted(ranges, key=lambda x: x.get('from_value', 0))):
            from_val = range_item.get('from_value', 0)
            to_val = range_item.get('to_value', 0)
            color_hex = range_item.get('color', '#CCCCCC')
            label = range_item.get('label', '')

            # From value
            from_item = QTableWidgetItem(f"{from_val:.2f}")
            self.rangesTable.setItem(row, 0, from_item)

            # To value
            to_item = QTableWidgetItem(f"{to_val:.2f}")
            self.rangesTable.setItem(row, 1, to_item)

            # Color (display with colored background)
            color_item = QTableWidgetItem(color_hex)
            color_item.setBackground(QColor(color_hex))
            # Set text color to contrast with background
            bg_color = QColor(color_hex)
            luminance = (0.299 * bg_color.red() + 0.587 * bg_color.green() + 0.114 * bg_color.blue()) / 255
            text_color = QColor("white") if luminance < 0.5 else QColor("black")
            color_item.setForeground(text_color)
            self.rangesTable.setItem(row, 2, color_item)

            # Label
            label_item = QTableWidgetItem(label)
            self.rangesTable.setItem(row, 3, label_item)

    # ==================== SYNC ACTIONS ====================

    def _on_pull_clicked(self):
        """Handle pull button click."""
        if not self.current_model:
            QMessageBox.warning(self, "No Model", "Please select a model first.")
            return

        if not self.project_manager.active_project:
            QMessageBox.warning(self, "No Project", "Please select a project first.")
            return

        # For PointSample/DrillSample, check that a configuration is selected
        if self.current_model in ["PointSample", "DrillSample"]:
            if not self.current_assay_config:
                QMessageBox.warning(
                    self,
                    "No Configuration",
                    "Please select an assay configuration before pulling data.\n\n"
                    "If no configurations are available, create one on geodb.io first."
                )
                return

            element = self.current_assay_config.get('element', 'Unknown')
            name = self.current_assay_config.get('name', 'Unnamed')
            self._log_message(f"Using color scheme: {name} ({element})", "info")

        # For ProjectFile, check that a file is selected
        if self.current_model == "ProjectFile":
            if not self.selected_project_file:
                QMessageBox.warning(
                    self,
                    "No File Selected",
                    "Please select a raster file from the dropdown before pulling.\n\n"
                    "If no files are available, upload GeoTIFFs or DEMs on geodb.io first."
                )
                return
            # Use special raster pull
            self._execute_raster_pull()
            return

        # Execute pull for vector models
        self._execute_pull()

    def _execute_pull(self):
        """Execute the actual pull operation."""
        # Check storage configuration first
        if not self._check_storage_before_pull():
            self._log_message("Pull cancelled - storage not configured", "warning")
            return

        # Get options
        options = self._get_sync_options()

        try:
            self._log_message(f"Starting PULL for {self.current_model}...", "info")
            self.pullButton.setEnabled(False)
            self.progressBar.setVisible(True)
            self.progressBar.setValue(0)

            # Get sample type filter for PointSample
            ps_type_id = None
            ps_type_name = None
            if self.current_model == "PointSample":
                ps_type_id = self._get_selected_sample_type()
                ps_type_name = self._get_selected_sample_type_name()
                if ps_type_name:
                    self._log_message(f"Filtering by sample type: {ps_type_name}", "info")

            # Get assay element and units for layer naming (if assay config is selected)
            assay_element = None
            assay_units = None
            if self.current_assay_config and self.current_model in ["PointSample", "DrillSample"]:
                assay_element = self.current_assay_config.get('element')
                assay_units = self.current_assay_config.get('units')

            # Perform pull with progress callback
            result = self.data_manager.pull_model_data(
                self.current_model,
                merge_assays=options.get('merge_assays', False),
                assay_config_id=options.get('assay_config_id'),
                ps_type_id=ps_type_id,
                ps_type_name=ps_type_name,
                assay_element=assay_element,
                assay_units=assay_units,
                progress_callback=self._on_progress
            )

            # Handle different result formats
            if isinstance(result, dict):
                pulled = result.get('pulled', 0)
                added = result.get('added', 0)
                updated = result.get('updated', 0)
                self._log_message(
                    f"✓ Pull complete: {pulled} records ({added} added, {updated} updated)",
                    "success"
                )
            else:
                self._log_message(
                    f"✓ Pull complete: {result.records_created} created, "
                    f"{result.records_updated} updated",
                    "success"
                )

                if result.layer:
                    self._log_message(f"Layer '{result.layer.name()}' added to QGIS", "success")

            # Apply assay styling if config was selected
            if self.current_assay_config and self.current_model in ["PointSample", "DrillSample"]:
                # Build full suffix for layer lookup: SampleType_Element_Units
                suffix_parts = []
                if ps_type_name:
                    suffix_parts.append(ps_type_name.replace(' ', ''))
                if assay_element and assay_units:
                    suffix_parts.append(assay_element)
                    suffix_parts.append(assay_units)
                suffix = '_'.join(suffix_parts) if suffix_parts else None
                self._apply_assay_styling(self.current_assay_config, layer_name_suffix=suffix)

        except APIPermissionError as e:
            self._log_message(f"Permission denied: {e}", "error")
            QMessageBox.critical(self, "Permission Denied", str(e))
        except NetworkError as e:
            self._log_message(f"Network error: {e}", "error")
            QMessageBox.critical(self, "Network Error", str(e))
        except Exception as e:
            self.logger.exception("Pull failed")
            self._log_message(f"Pull failed: {e}", "error")
            QMessageBox.critical(self, "Error", f"Pull failed: {e}")
        finally:
            self.pullButton.setEnabled(True)
            self.progressBar.setVisible(False)

    def _execute_raster_pull(self):
        """Execute pull for a single selected raster file (ProjectFile)."""
        from ..processors.raster_processor import RasterProcessor

        pf = self.selected_project_file
        if not pf:
            return

        project = self.project_manager.active_project
        file_name = pf.get('name', 'Unknown')

        try:
            self._log_message(f"Starting download of '{file_name}'...", "info")
            self.pullButton.setEnabled(False)
            self.progressBar.setVisible(True)
            self.progressBar.setValue(0)

            # Get base URL for local development (handles relative file URLs)
            # In local dev, file_url is like '/media/PROJECT/file.tif'
            # In production, file_url is a full S3 presigned URL
            base_url = self._get_server_base_url()

            # Initialize raster processor with base URL for resolving relative paths
            raster_processor = RasterProcessor(base_url=base_url)

            # Download and load the single file
            result = raster_processor.process_project_files(
                project_files=[pf],  # Single file as list
                project_name=project.name,
                progress_callback=self._on_progress
            )

            if result['loaded'] > 0:
                layer_name = result['layers'][0] if result['layers'] else file_name
                self._log_message(
                    f"Raster layer '{layer_name}' added to QGIS",
                    "success"
                )
            elif result['errors']:
                for error in result['errors']:
                    self._log_message(f"Error: {error}", "error")
            else:
                self._log_message(f"File skipped or not loaded", "warning")

        except NetworkError as e:
            self._log_message(f"Network error: {e}", "error")
            QMessageBox.critical(self, "Network Error", str(e))
        except Exception as e:
            self.logger.exception("Raster pull failed")
            self._log_message(f"Download failed: {e}", "error")
            QMessageBox.critical(self, "Error", f"Download failed: {e}")
        finally:
            self.pullButton.setEnabled(True)
            self.progressBar.setVisible(False)

    def _get_server_base_url(self) -> str:
        """
        Get the server base URL for resolving relative file paths.

        In local development, file_url from API is relative (e.g., '/media/PROJECT/file.tif')
        and needs to be prefixed with the server root (e.g., 'http://localhost:8000').

        In production, file_url is already a full S3 presigned URL, so this is not needed.

        Returns:
            Server base URL without the /api/v1 suffix (e.g., 'http://localhost:8000')
        """
        from urllib.parse import urlparse

        # Get the configured API base URL
        api_base_url = self.config.base_url  # e.g., 'http://localhost:8000/api/v1'

        # Parse and extract just the scheme + netloc (server root)
        parsed = urlparse(api_base_url)
        server_root = f"{parsed.scheme}://{parsed.netloc}"

        return server_root

    def _apply_assay_styling(self, config: dict, layer_name_suffix: str = None):
        """Apply AssayRangeConfiguration styling to the pulled layer."""
        try:
            # Build the effective layer name (including sample type if filtered)
            effective_model_name = self.current_model
            if layer_name_suffix:
                effective_model_name = f"{self.current_model}_{layer_name_suffix}"

            # Find the layer by model name (with project prefix)
            project = self.project_manager.active_project
            if project:
                full_layer_name = f"{project.name}_{effective_model_name}"
            else:
                full_layer_name = effective_model_name

            layer = self.sync_manager.layer_processor.find_layer_by_name(full_layer_name)
            if not layer:
                # Try without project prefix as fallback
                layer = self.sync_manager.layer_processor.find_layer_by_name(effective_model_name)

            if not layer:
                self._log_message(
                    f"Could not find layer '{full_layer_name}' for styling",
                    "warning"
                )
                return

            element = config.get('element', 'Au')
            units = config.get('units', 'ppm')
            # Use the units from config - API now returns values in the configured units
            value_field = f"{element}_{units}"

            # Debug: log field names
            self.logger.info(f"Looking for field '{value_field}' in layer '{layer.name()}'")
            self.logger.info(f"Available fields: {[f.name() for f in layer.fields()]}")

            # Apply the graduated style
            success = self.style_processor.apply_assay_style(
                layer,
                config,
                value_field=value_field
            )

            if success:
                ranges_count = len(config.get('ranges', []))
                self._log_message(
                    f"✓ Applied {element} color scheme with {ranges_count} grade ranges",
                    "success"
                )
            else:
                self._log_message(
                    f"Could not apply color scheme - field '{value_field}' may not exist",
                    "warning"
                )

        except Exception as e:
            self.logger.exception("Failed to apply assay styling")
            self._log_message(f"Failed to apply color scheme: {e}", "warning")
    
    def _on_push_clicked(self):
        """Handle push button click."""
        if not self.current_model:
            QMessageBox.warning(self, "No Model", "Please select a model first.")
            return
        
        if not self.project_manager.active_project:
            QMessageBox.warning(self, "No Project", "Please select a project first.")
            return
        
        # Confirm
        reply = QMessageBox.question(
            self, 
            "Confirm Push", 
            f"Are you sure you want to push {self.current_model} data to the server?\n\n"
            "This will upload your local changes.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if reply != QMessageBox.Yes:
            return
        
        try:
            self._log_message(f"Starting PUSH for {self.current_model}...", "info")
            self.pushButton.setEnabled(False)
            self.progressBar.setVisible(True)
            self.progressBar.setValue(0)
            
            # Perform push with progress callback
            result = self.data_manager.push_model_data(
                self.current_model,
                progress_callback=self._on_progress
            )
            
            # Handle dictionary result format
            if isinstance(result, dict):
                created = result.get('created', 0)
                updated = result.get('updated', 0)
                errors = result.get('errors', 0)
                skipped = result.get('skipped', 0)

                message_parts = []
                if created > 0:
                    message_parts.append(f"{created} created")
                if updated > 0:
                    message_parts.append(f"{updated} updated")
                if errors > 0:
                    message_parts.append(f"{errors} errors")
                if skipped > 0:
                    message_parts.append(f"{skipped} unchanged (skipped)")

                if message_parts:
                    message = f"✓ Push complete: {', '.join(message_parts)}"
                else:
                    message = "✓ Push complete: no changes"

                self._log_message(message, "success")
            else:
                self._log_message(
                    f"✓ Push complete: {result.records_created} created, "
                    f"{result.records_updated} updated, {result.records_deleted} deleted",
                    "success"
                )
            
        except APIPermissionError as e:
            self._log_message(f"Permission denied: {e}", "error")
            QMessageBox.critical(self, "Permission Denied", str(e))
        except ValidationError as e:
            self._log_message(f"Validation error: {e}", "error")
            QMessageBox.critical(self, "Validation Error", str(e))
        except Exception as e:
            self.logger.exception("Push failed")
            self._log_message(f"Push failed: {e}", "error")
            QMessageBox.critical(self, "Error", f"Push failed: {e}")
        finally:
            self.pushButton.setEnabled(True)
            self.progressBar.setVisible(False)
    
    def _get_sync_options(self) -> dict:
        """Get current sync options from UI."""
        options = {}

        # LandHolding options
        if self.includeCompanyLandCheckBox.isVisible():
            options['include_company_land'] = self.includeCompanyLandCheckBox.isChecked()

        # Assay configuration options (from selected AssayRangeConfiguration)
        if self.assayOptionsGroupBox.isVisible() and self.current_assay_config:
            options['merge_assays'] = True
            options['assay_config'] = self.current_assay_config
            # The config ID will be used by the data manager to apply styling
            options['assay_config_id'] = self.current_assay_config.get('id')

        return options
    
    def _on_progress(self, percent: int, message: str):
        """Handle progress updates."""
        self.progressBar.setValue(percent)
        self._log_message(f"[{percent}%] {message}", "info")
    
    # ==================== MESSAGES ====================
    
    def _log_message(self, message: str, level: str = "info"):
        """Add message to the message browser."""
        # Color coding
        colors = {
            "info": "#666666",
            "success": "#4caf50",
            "warning": "#ff9800",
            "error": "#d32f2f"
        }
        color = colors.get(level, "#000000")
        
        # Format message
        html = f'<span style="color: {color};">{message}</span>'
        
        # Append to browser
        cursor = self.messagesTextBrowser.textCursor()
        cursor.movePosition(QTextCursor.End)
        self.messagesTextBrowser.setTextCursor(cursor)
        self.messagesTextBrowser.insertHtml(html + "<br>")
        
        # Scroll to bottom
        scrollbar = self.messagesTextBrowser.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
        
        # Also log to file
        if level == "error":
            self.logger.error(message)
        elif level == "warning":
            self.logger.warning(message)
        else:
            self.logger.info(message)
    
    def _clear_messages(self):
        """Clear the messages browser."""
        self.messagesTextBrowser.clear()
        self._log_message("Messages cleared.", "info")

    # ==================== STORAGE MANAGEMENT ====================

    def _add_storage_button(self):
        """Add storage configuration button to the project row."""
        from qgis.PyQt.QtWidgets import QPushButton, QLabel

        # Create storage status label
        self.storageLabel = QLabel("Storage:")
        self.storageLabel.setStyleSheet("font-weight: bold;")

        self.storageValue = QLabel("Not configured")
        self.storageValue.setStyleSheet("color: #ff9800;")

        # Create storage button
        self.storageButton = QPushButton("Configure Storage")
        self.storageButton.setEnabled(False)  # Enable when project is selected
        self.storageButton.setToolTip("Configure where to store downloaded data")
        self.storageButton.clicked.connect(self._on_storage_clicked)

        # Find the project layout and insert our widgets before the spacer
        # The project layout is in authGroupBox -> authLayout -> projectLayout
        project_layout = self.projectLayout

        # Insert before the last spacer (position count - 1)
        insert_pos = project_layout.count() - 1

        project_layout.insertWidget(insert_pos, self.storageLabel)
        project_layout.insertWidget(insert_pos + 1, self.storageValue)
        project_layout.insertWidget(insert_pos + 2, self.storageButton)

    def _replace_assay_options_ui(self):
        """Replace manual assay merge options with AssayRangeConfiguration selector."""
        # Hide all the old manual controls
        self.includeMergedAssaysCheckBox.setVisible(False)
        self.mergeMethodLabel.setVisible(False)
        self.mergeMethodComboBox.setVisible(False)
        self.unitsLabel.setVisible(False)
        self.unitsComboBox.setVisible(False)
        self.convertBDLCheckBox.setVisible(False)
        self.bdlRatioLabel.setVisible(False)
        self.bdlRatioSpinBox.setVisible(False)
        self.elementsLabel.setVisible(False)
        self.elementsLineEdit.setVisible(False)

        # Get the form layout from the group box
        form_layout = self.assayFormLayout

        # Clear the form layout
        while form_layout.count():
            child = form_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        # Create new UI for AssayRangeConfiguration selection
        # Row 0: Sample Type filter (for PointSample only)
        self.sampleTypeLabel = QLabel("Sample Type:")  # Store reference for visibility toggle
        self.sampleTypeComboBox = QComboBox()
        self.sampleTypeComboBox.setToolTip(
            "Filter by sample type (e.g., Soil, Rock Chip). "
            "Leave as 'All Types' to pull all samples."
        )
        self.sampleTypeComboBox.addItem("All Types", None)  # Default: no filter
        form_layout.addRow(self.sampleTypeLabel, self.sampleTypeComboBox)

        # Row 1: Configuration selector
        config_label = QLabel("Assay Configuration:")
        self.assayConfigComboBox = QComboBox()
        self.assayConfigComboBox.setToolTip("Select a pre-configured assay color scheme")
        self.assayConfigComboBox.currentIndexChanged.connect(self._on_assay_config_selected)
        form_layout.addRow(config_label, self.assayConfigComboBox)

        # Row 2: Reload button
        self.reloadConfigsButton = QPushButton("Reload Configurations")
        self.reloadConfigsButton.setToolTip("Reload assay configurations from server")
        self.reloadConfigsButton.clicked.connect(self._load_assay_configurations)
        form_layout.addRow("", self.reloadConfigsButton)

        # Add a separator/label for merge settings
        merge_settings_title = QLabel("<b>Merge Settings:</b>")
        form_layout.addRow(merge_settings_title)

        # Row 2+: Merge settings display (read-only)
        self.mergeSettingsLabel = QLabel("No configuration selected")
        self.mergeSettingsLabel.setWordWrap(True)
        self.mergeSettingsLabel.setStyleSheet("color: #374151; padding: 8px; background-color: #f9fafb; border-radius: 4px;")
        form_layout.addRow(self.mergeSettingsLabel)

        # Add separator for color ranges
        ranges_title = QLabel("<b>Color Ranges:</b>")
        form_layout.addRow(ranges_title)

        # Create a table for displaying color ranges
        self.rangesTable = QTableWidget()
        self.rangesTable.setColumnCount(4)
        self.rangesTable.setHorizontalHeaderLabels(["From", "To", "Color", "Label"])
        self.rangesTable.horizontalHeader().setStretchLastSection(True)
        self.rangesTable.setMaximumHeight(150)
        self.rangesTable.setEditTriggers(QTableWidget.NoEditTriggers)
        form_layout.addRow(self.rangesTable)

    def _add_projectfile_options_ui(self):
        """Create UI for ProjectFile (GeoTIFF/DEM) selection."""
        from qgis.PyQt.QtWidgets import QGroupBox, QFormLayout, QTextEdit

        # Create a new group box for ProjectFile options
        self.projectFileGroupBox = QGroupBox("Raster File Selection")
        self.projectFileGroupBox.setVisible(False)  # Hidden by default

        # Create form layout
        pf_layout = QFormLayout()
        self.projectFileGroupBox.setLayout(pf_layout)

        # File selector dropdown
        file_label = QLabel("Available Files:")
        self.projectFileComboBox = QComboBox()
        self.projectFileComboBox.setToolTip(
            "Select a GeoTIFF, DEM, or other raster file to download"
        )
        self.projectFileComboBox.currentIndexChanged.connect(self._on_project_file_selected)
        pf_layout.addRow(file_label, self.projectFileComboBox)

        # Reload button
        self.reloadFilesButton = QPushButton("Reload Files")
        self.reloadFilesButton.setToolTip("Reload available files from server")
        self.reloadFilesButton.clicked.connect(self._load_project_files)
        pf_layout.addRow("", self.reloadFilesButton)

        # File metadata display
        metadata_title = QLabel("<b>File Details:</b>")
        pf_layout.addRow(metadata_title)

        # Metadata text area (read-only)
        self.projectFileMetadata = QTextEdit()
        self.projectFileMetadata.setReadOnly(True)
        self.projectFileMetadata.setMaximumHeight(150)
        self.projectFileMetadata.setStyleSheet(
            "background-color: #f9fafb; border: 1px solid #e5e7eb; border-radius: 4px; padding: 8px;"
        )
        self.projectFileMetadata.setPlaceholderText("Select a file to see its details")
        pf_layout.addRow(self.projectFileMetadata)

        # Insert the group box into the options layout (inside optionsGroupBox)
        # Find the optionsLayout and insert after the assayOptionsGroupBox
        options_layout = self.optionsLayout
        # Insert at position 2 (after includeCompanyLandCheckBox and assayOptionsGroupBox)
        options_layout.insertWidget(2, self.projectFileGroupBox)

    def _load_project_files(self):
        """Load available ProjectFile records from API."""
        project = self.project_manager.active_project
        if not project:
            self._log_message("Please select a project first.", "warning")
            return

        self.projectFileComboBox.clear()
        self.project_files = []
        self.selected_project_file = None
        self.projectFileMetadata.clear()

        try:
            self._log_message("Loading available raster files...", "info")

            # Fetch all project files (both raster and non-raster for selection)
            # Filter to rasters on API side
            params = {'is_raster': 'true'}
            files = self.api_client.get_all_paginated(
                model_name='ProjectFile',
                project_id=project.id,
                params=params
            )

            self.project_files = files

            if not files:
                self._log_message("No raster files found for this project.", "warning")
                self.projectFileComboBox.addItem("No raster files available", None)
                return

            # Populate dropdown with file names and categories
            self.projectFileComboBox.addItem(f"-- Select a file ({len(files)} available) --", None)

            # Group files by category for better organization
            for pf in files:
                name = pf.get('name', 'Unnamed')
                category = pf.get('category_display', pf.get('category', ''))
                file_size = pf.get('file_size', 0)

                # Format file size
                if file_size > 1024 * 1024 * 1024:
                    size_str = f"{file_size / (1024*1024*1024):.1f} GB"
                elif file_size > 1024 * 1024:
                    size_str = f"{file_size / (1024*1024):.1f} MB"
                elif file_size > 1024:
                    size_str = f"{file_size / 1024:.1f} KB"
                else:
                    size_str = f"{file_size} bytes"

                display_text = f"{name} [{category}] ({size_str})"
                self.projectFileComboBox.addItem(display_text, pf.get('id'))

            self._log_message(f"Loaded {len(files)} raster file(s)", "success")

        except Exception as e:
            self.logger.exception("Failed to load project files")
            self._log_message(f"Failed to load files: {e}", "error")

    def _on_project_file_selected(self, index):
        """Handle project file selection from dropdown."""
        if index < 0:
            return

        file_id = self.projectFileComboBox.currentData()
        if not file_id:
            self.selected_project_file = None
            self.projectFileMetadata.clear()
            return

        # Find the selected file in our list
        selected = None
        for pf in self.project_files:
            if pf.get('id') == file_id:
                selected = pf
                break

        self.selected_project_file = selected

        if selected:
            self._display_project_file_metadata(selected)

    def _display_project_file_metadata(self, pf: Dict[str, Any]):
        """Display metadata for the selected project file."""
        # Format metadata as HTML for display
        name = pf.get('name', 'Unknown')
        category = pf.get('category_display', pf.get('category', 'N/A'))
        description = pf.get('description', '') or 'No description'
        file_size = pf.get('file_size', 0)
        crs = pf.get('crs', 'Not specified')
        resolution = pf.get('resolution')
        bounds = pf.get('bounds')

        # Format file size
        if file_size > 1024 * 1024 * 1024:
            size_str = f"{file_size / (1024*1024*1024):.2f} GB"
        elif file_size > 1024 * 1024:
            size_str = f"{file_size / (1024*1024):.2f} MB"
        elif file_size > 1024:
            size_str = f"{file_size / 1024:.2f} KB"
        else:
            size_str = f"{file_size} bytes"

        # Format bounds
        bounds_str = 'Not specified'
        if bounds:
            if isinstance(bounds, list) and len(bounds) == 4:
                bounds_str = f"[{bounds[0]:.4f}, {bounds[1]:.4f}, {bounds[2]:.4f}, {bounds[3]:.4f}]"
            elif isinstance(bounds, str):
                bounds_str = bounds

        # Format resolution
        res_str = f"{resolution:.2f} units" if resolution else "Not specified"

        html = f"""
        <table style="width:100%">
            <tr><td><b>Name:</b></td><td>{name}</td></tr>
            <tr><td><b>Category:</b></td><td>{category}</td></tr>
            <tr><td><b>Size:</b></td><td>{size_str}</td></tr>
            <tr><td><b>CRS:</b></td><td>{crs}</td></tr>
            <tr><td><b>Resolution:</b></td><td>{res_str}</td></tr>
            <tr><td><b>Bounds:</b></td><td style="font-size:9pt">{bounds_str}</td></tr>
        </table>
        <p style="margin-top:8px; color:#6b7280;"><i>{description}</i></p>
        """

        self.projectFileMetadata.setHtml(html)
        self._log_message(f"Selected: {name} ({size_str})", "info")

    def _on_storage_clicked(self):
        """Handle storage configuration button click."""
        project = self.project_manager.active_project
        if not project:
            QMessageBox.warning(self, "No Project", "Please select a project first.")
            return

        # Show storage configuration dialog
        dialog = StorageConfigDialog(
            parent=self,
            storage_manager=self.storage_manager,
            project_id=project.id,
            project_name=project.name
        )

        dialog.storage_configured.connect(self._on_storage_configured)
        dialog.exec_()

    def _on_storage_configured(self, mode: str, path: str):
        """Handle storage configuration from dialog."""
        project = self.project_manager.active_project
        if not project:
            return

        # Save configuration
        geopackage_path = path if mode == StorageMode.GEOPACKAGE else None
        success = self.storage_manager.configure_storage(
            project.id,
            mode,
            geopackage_path
        )

        if success:
            # Update sync manager's layer processor
            if mode == StorageMode.GEOPACKAGE:
                self.sync_manager.layer_processor.set_geopackage_path(path)
                self._log_message(f"Storage configured: GeoPackage at {path}", "success")
            else:
                self.sync_manager.layer_processor.set_geopackage_path(None)
                self._log_message("Storage configured: Memory layers (temporary)", "success")
                self._log_message(
                    "Warning: Memory layers will be lost when QGIS closes!",
                    "warning"
                )

            # Update UI
            self._update_storage_status()
        else:
            self._log_message("Failed to configure storage", "error")

    def _update_storage_status(self):
        """Update storage status display in UI."""
        project = self.project_manager.active_project
        if not project:
            self.storageValue.setText("No project")
            self.storageValue.setStyleSheet("color: #666;")
            self.storageButton.setEnabled(False)
            return

        self.storageButton.setEnabled(True)

        config = self.storage_manager.get_storage_config(project.id)

        if not config.get('configured'):
            self.storageValue.setText("Not configured")
            self.storageValue.setStyleSheet("color: #ff9800;")
        elif config.get('is_geopackage'):
            path = config.get('geopackage_path', '')
            # Show just filename for display
            from pathlib import Path
            filename = Path(path).name if path else 'Unknown'
            self.storageValue.setText(f"GeoPackage: {filename}")
            self.storageValue.setStyleSheet("color: #4caf50;")
        else:
            self.storageValue.setText("Memory (temporary)")
            self.storageValue.setStyleSheet("color: #ff9800;")

    def _check_storage_before_pull(self) -> bool:
        """
        Check if storage is configured before pull operation.

        Returns:
            True if storage is configured or user configured it, False to cancel
        """
        project = self.project_manager.active_project
        if not project:
            return False

        # Check if storage is already configured
        if self.storage_manager.is_configured(project.id):
            # Make sure layer processor is using correct path
            config = self.storage_manager.get_storage_config(project.id)
            if config.get('is_geopackage'):
                self.sync_manager.layer_processor.set_geopackage_path(
                    config.get('geopackage_path')
                )
            else:
                self.sync_manager.layer_processor.set_geopackage_path(None)
            return True

        # Storage not configured - ask user
        reply = QMessageBox.question(
            self,
            "Configure Storage",
            "Storage has not been configured for this project.\n\n"
            "Would you like to configure where to store the data?\n\n"
            "• GeoPackage: Data is saved to a file and persists\n"
            "• Memory: Data is temporary and lost when QGIS closes\n\n"
            "Click 'Yes' to configure, or 'No' to use temporary memory storage.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes
        )

        if reply == QMessageBox.Yes:
            # Show storage configuration dialog
            self._on_storage_clicked()
            # Check if they configured it
            return self.storage_manager.is_configured(project.id)
        else:
            # Use memory storage as default
            self.storage_manager.configure_storage(project.id, StorageMode.MEMORY)
            self.sync_manager.layer_processor.set_geopackage_path(None)
            self._update_storage_status()
            self._log_message(
                "Using temporary memory storage. Data will be lost when QGIS closes!",
                "warning"
            )
            return True

    # ==================== FIELD WORK PLANNING ====================

    def _add_field_work_button(self):
        """Add field work planning button near the Pull/Push buttons."""
        # Create the field work button
        self.fieldWorkButton = QPushButton("Plan Field Samples...")
        self.fieldWorkButton.setEnabled(False)  # Enable when logged in with edit permission
        self.fieldWorkButton.setToolTip(
            "Create planned samples from any point layer for field collection"
        )
        self.fieldWorkButton.clicked.connect(self._on_field_work_clicked)

        # Style the button to distinguish it from Pull/Push
        self.fieldWorkButton.setStyleSheet("""
            QPushButton {
                background-color: #059669;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #047857;
            }
            QPushButton:disabled {
                background-color: #9ca3af;
            }
        """)

        # Find the button layout and add our button
        # The buttonLayout contains pullButton and pushButton
        button_layout = self.buttonLayout

        # Insert after pushButton (position 1) as position 2
        button_layout.insertWidget(2, self.fieldWorkButton)

    def _on_field_work_clicked(self):
        """Handle field work button click - open the planning dialog."""
        if not self.project_manager.active_project:
            QMessageBox.warning(
                self, "No Project",
                "Please select a project before planning field samples."
            )
            return

        if not self.project_manager.can_edit():
            QMessageBox.warning(
                self, "Permission Denied",
                "You need edit permission to create planned samples."
            )
            return

        # Open the Field Work Dialog
        dialog = FieldWorkDialog(
            parent=self,
            data_manager=self.data_manager,
            project_manager=self.project_manager
        )

        dialog.push_completed.connect(self._on_field_work_completed)
        dialog.exec_()

    def _on_field_work_completed(self, result: dict):
        """Handle field work push completion."""
        created = result.get('created', 0)
        errors = result.get('errors', 0)

        if errors == 0:
            self._log_message(
                f"✓ Field work planning complete: Created {created} planned samples",
                "success"
            )
        else:
            self._log_message(
                f"⚠ Field work planning: {created} created, {errors} errors",
                "warning"
            )

    def _update_field_work_button_state(self):
        """Update field work button enabled state based on permissions."""
        if hasattr(self, 'fieldWorkButton'):
            can_edit = (
                self.current_session is not None and
                self.project_manager.active_project is not None and
                self.project_manager.can_edit()
            )
            self.fieldWorkButton.setEnabled(can_edit)