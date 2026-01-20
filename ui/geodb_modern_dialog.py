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
from qgis.PyQt.QtCore import Qt, QTimer, QThread, pyqtSignal
from qgis.PyQt.QtWidgets import (
    QDialog, QMessageBox, QLabel, QComboBox, QPushButton,
    QVBoxLayout, QGroupBox, QTableWidget, QTableWidgetItem,
    QHBoxLayout, QHeaderView
)
from qgis.PyQt.QtGui import QColor, QTextCursor

try:
    from qgis.PyQt import sip
except ImportError:
    import sip

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
from ..managers.claims_manager import ClaimsManager
from ..models.auth import AuthSession, UserContext
from ..processors.style_processor import StyleProcessor
from .login_dialog import LoginDialog
from .assay_range_dialog import AssayRangeDialog
from .storage_dialog import StorageConfigDialog
from .field_work_dialog import FieldWorkDialog
from .claims_widget import ClaimsWidget  # Keep for reference, deprecated
from .claims_wizard_widget import ClaimsWizardWidget
from .claims_order_widget import ClaimsOrderWidget
from .basemaps_widget import BasemapsWidget

# Ensure plugin directory is in sys.path for UI resource imports
plugin_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if plugin_dir not in sys.path:
    sys.path.insert(0, plugin_dir)

# Load UI file
FORM_CLASS, _ = uic.loadUiType(os.path.join(
    os.path.dirname(__file__), 'geodb_modern_dialog.ui'))


class RefreshWorker(QThread):
    """Background worker thread for refreshing user context from server.

    This avoids the nested QEventLoop crash that occurs when making synchronous
    HTTP requests from within Qt signal handlers or modal dialogs.

    Uses urllib.request instead of QNetworkAccessManager since Qt network
    classes must be used from the main thread only.
    """
    finished = pyqtSignal(object)  # Dict response or None
    error = pyqtSignal(str)  # Error message

    def __init__(self, base_url: str, token: str):
        super().__init__()
        self.base_url = base_url
        self.token = token

    def run(self):
        """Fetch user context in background thread using urllib."""
        import urllib.request
        import ssl
        import json

        try:
            url = f"{self.base_url}/me/"

            # Create SSL context
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

            request = urllib.request.Request(
                url,
                headers={
                    'Authorization': f'Token {self.token}',
                    'Content-Type': 'application/json',
                    'User-Agent': 'QGIS-GeodbPlugin/2.0'
                }
            )

            with urllib.request.urlopen(request, context=ctx, timeout=30) as response:
                data = response.read().decode('utf-8')
                result = json.loads(data)
                self.finished.emit(result)

        except Exception as e:
            self.error.emit(str(e))


class GeodbModernDialog(QDialog, FORM_CLASS):
    """Modern dialog for Geodb.io plugin with clean UI and manager integration."""
    
    def __init__(self, parent=None):
        """Initialize the dialog."""
        super(GeodbModernDialog, self).__init__(parent)
        self.setupUi(self)

        # Allow dialog to be resized smaller than content's natural minimum
        # This lets users shrink the window on smaller screens
        self.setMinimumSize(400, 300)

        # Initialize managers
        self.config = Config()
        self.logger = PluginLogger.get_logger()

        # Register UI handler so logs appear in the Log tab
        PluginLogger.register_ui_handler(self._log_message)

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
        self.claims_manager = ClaimsManager(self.api_client, self.config)

        # Claims wizard widget (replaces old ClaimsWidget)
        self.claims_wizard: Optional[ClaimsWizardWidget] = None
        # Claims order widget for pay-per-claim users
        self.claims_order_widget: Optional[ClaimsOrderWidget] = None
        # Keep legacy reference for backward compatibility
        self.claims_widget: Optional[ClaimsWidget] = None
        # Basemaps widget
        self.basemaps_widget: Optional[BasemapsWidget] = None

        # Current state
        self.current_session: Optional[AuthSession] = None
        self.current_model: Optional[str] = None
        self._local_mode_changing = False  # Guard against rapid toggles
        self.current_assay_config: Optional[dict] = None  # Selected AssayRangeConfiguration
        self.assay_config_is_none: bool = False  # True when "None (Gray circles)" is selected
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

        # Add Claims to model list and set up claims UI
        self._setup_claims_ui()

        # Set up basemaps UI
        self._setup_basemaps_ui()

        # Connect signals
        self._connect_signals()

        # Initialize UI state
        self._initialize_ui()

        # Add context header showing company/project
        self._setup_context_header()

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
        self.refreshProjectsButton.clicked.connect(self._on_refresh_projects_clicked)
        
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

        # Hide developer options group box unless in dev mode
        if not DEV_MODE:
            if hasattr(self, 'devModeGroupBox'):
                self.devModeGroupBox.setVisible(False)
            self.localModeCheckBox.setVisible(False)

        # Set local mode checkbox from config
        # Block signals to prevent triggering _on_local_mode_changed during init
        is_local = self.config.get('api.use_local', False)
        self.localModeCheckBox.blockSignals(True)
        self.localModeCheckBox.setChecked(is_local)
        self.localModeCheckBox.blockSignals(False)

        # Start on the Data Sync tab (index 1) since Account tab is for settings
        if hasattr(self, 'mainTabWidget'):
            self.mainTabWidget.setCurrentIndex(1)

        self._log_message("Welcome to Geodb.io QGIS Plugin v2.0", "info")
        self._log_message("Please login to begin synchronizing your geospatial data.", "info")
        if is_local:
            self._log_message("LOCAL DEVELOPMENT MODE ENABLED", "warning")

    def _setup_context_header(self):
        """Set up the context header showing current company and project."""
        from qgis.PyQt.QtWidgets import QFrame, QHBoxLayout
        from qgis.PyQt.QtCore import Qt

        # Create the header widget
        self.context_header = QFrame()
        self.context_header.setObjectName("contextHeader")
        self.context_header.setStyleSheet("""
            QFrame#contextHeader {
                background-color: #f0f9ff;
                border: 1px solid #bae6fd;
                border-radius: 6px;
                padding: 4px 8px;
                margin: 4px 8px 0px 8px;
            }
        """)

        header_layout = QHBoxLayout(self.context_header)
        header_layout.setContentsMargins(12, 6, 12, 6)
        header_layout.setSpacing(8)

        # Company label
        self.context_company_label = QLabel("Company:")
        self.context_company_label.setStyleSheet(
            "color: #64748b; font-size: 12px; font-weight: normal;"
        )
        header_layout.addWidget(self.context_company_label)

        self.context_company_value = QLabel("Not selected")
        self.context_company_value.setStyleSheet(
            "color: #0369a1; font-size: 12px; font-weight: bold;"
        )
        header_layout.addWidget(self.context_company_value)

        # Separator
        separator = QLabel("|")
        separator.setStyleSheet("color: #cbd5e1; font-size: 12px;")
        header_layout.addWidget(separator)

        # Project label
        self.context_project_label = QLabel("Project:")
        self.context_project_label.setStyleSheet(
            "color: #64748b; font-size: 12px; font-weight: normal;"
        )
        header_layout.addWidget(self.context_project_label)

        self.context_project_value = QLabel("Not selected")
        self.context_project_value.setStyleSheet(
            "color: #0369a1; font-size: 12px; font-weight: bold;"
        )
        header_layout.addWidget(self.context_project_value)

        # Stretch to push content to the left
        header_layout.addStretch()

        # Insert the header above the tab widget
        # Find the main layout and insert at position 0
        if self.layout():
            self.layout().insertWidget(0, self.context_header)

        # Initially hidden until logged in
        self.context_header.setVisible(False)

    def _update_context_header(self):
        """Update the context header with current company and project names."""
        if not hasattr(self, 'context_header'):
            return

        # Guard against deleted widgets
        try:
            if sip.isdeleted(self.context_header):
                return
        except (RuntimeError, ReferenceError):
            return

        company = self.project_manager.active_company
        project = self.project_manager.active_project

        if company:
            self.context_company_value.setText(company.name)
        else:
            self.context_company_value.setText("Not selected")

        if project:
            self.context_project_value.setText(project.name)
        else:
            self.context_project_value.setText("Not selected")

        # Show header only when logged in
        is_logged_in = self.current_session is not None
        self.context_header.setVisible(is_logged_in)

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

                # Update context header
                self._update_context_header()

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

            # Check QClaims access level and update claims tab
            self._check_claims_access_and_update_tab()

            # Update context header
            self._update_context_header()

        except Exception as e:
            self.logger.exception("Error after login")
            self._log_message(f"Error after login: {e}", "error")

    def _check_claims_access_and_update_tab(self):
        """Check QClaims access level and show appropriate claims widget."""
        try:
            access_info = self.claims_manager.check_access()
            self._update_claims_tab_for_access(access_info)

            # Log access level
            access_type = access_info.get('access_type', 'unknown')
            can_process = access_info.get('can_process_immediately', False)

            if can_process:
                self._log_message(f"QClaims access: {access_type} (full processing)", "info")
            else:
                self._log_message(f"QClaims access: {access_type} (pay-per-claim)", "info")

        except Exception as e:
            self.logger.warning(f"Could not check QClaims access: {e}")
            # Default to wizard if access check fails
            self._show_claims_wizard()
    
    def _on_logout_clicked(self):
        """Handle logout button click."""
        try:
            self._log_message("Logging out...", "info")
            self.auth_manager.logout()
            self.current_session = None

            # Clear claims manager cache (tokens are now invalid)
            self.claims_manager.clear_cache()

            # Update UI
            self._update_auth_status(False)
            self._clear_projects()
            self._update_context_header()
            self._log_message("Logged out successfully.", "success")

        except Exception as e:
            self.logger.exception("Logout error")
            self._log_message(f"Logout error: {e}", "error")
    
    def _on_local_mode_changed(self, state):
        """Handle local development mode toggle."""
        # Guard against rapid repeated calls (can happen during checkbox state changes)
        if self._local_mode_changing:
            return
        self._local_mode_changing = True

        try:
            is_enabled = bool(state)

            # Update configuration
            self.config.toggle_local_mode(is_enabled)

            # Update API client with new base URL (but clear the token - it's not valid for the new server)
            self.api_client = APIClient(self.config, token=None)

            # Update all managers with new API client
            self.auth_manager.api_client = self.api_client
            self.project_manager.api_client = self.api_client
            self.data_manager.api_client = self.api_client
            self.claims_manager.api = self.api_client
            self.claims_manager.clear_cache()

            # Log the change
            mode = "LOCAL DEVELOPMENT" if is_enabled else "PRODUCTION"
            url = self.config.base_url
            self._log_message(f"Switched to {mode} mode: {url}", "warning")

            # Force logout since tokens are server-specific
            if self.current_session:
                self._log_message("Logging out - tokens are server-specific", "warning")
                self.auth_manager.logout()
                self.current_session = None
                self._update_auth_status(False)
                self._clear_projects()
        finally:
            self._local_mode_changing = False

    def _update_auth_status(self, logged_in: bool):
        """Update authentication status in UI."""
        if logged_in and self.current_session:
            self.statusValue.setText("✓ Logged in")
            self.statusValue.setStyleSheet("color: #4caf50; font-weight: bold;")
            self.userValue.setText(self.current_session.user.username)
            self.loginButton.setEnabled(False)
            self.logoutButton.setEnabled(True)
            self.companyComboBox.setEnabled(True)
            self.refreshProjectsButton.setEnabled(True)
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
            self.refreshProjectsButton.setEnabled(False)

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

        # Block signals while we set up the combo boxes to avoid
        # triggering _on_company_changed and _on_project_changed prematurely
        self.companyComboBox.blockSignals(True)
        self.projectComboBox.blockSignals(True)

        try:
            self._populate_project_combos(companies)

            # Auto-select current company if set (from server's active_company)
            target_company_index = 0  # Default to first
            if self.project_manager.active_company:
                for i in range(self.companyComboBox.count()):
                    company = self.companyComboBox.itemData(i)
                    if company and company.id == self.project_manager.active_company.id:
                        target_company_index = i
                        break

            self.companyComboBox.setCurrentIndex(target_company_index)

            # Now populate projects for the selected company
            company = self.companyComboBox.itemData(target_company_index)
            if company:
                self.projectComboBox.clear()
                for project in company.projects:
                    self.projectComboBox.addItem(project.name, project)
                self.projectComboBox.setEnabled(True)

            # Auto-select current project if set (from server's active_project)
            target_project_index = 0  # Default to first
            if self.project_manager.active_project:
                for i in range(self.projectComboBox.count()):
                    project = self.projectComboBox.itemData(i)
                    if project and project.id == self.project_manager.active_project.id:
                        target_project_index = i
                        break

            self.projectComboBox.setCurrentIndex(target_project_index)

        finally:
            # Re-enable signals
            self.companyComboBox.blockSignals(False)
            self.projectComboBox.blockSignals(False)

        # Now manually trigger the project changed handler to update permissions
        # and other UI elements for the selected project
        self._on_project_changed(self.projectComboBox.currentIndex())

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

    def _on_refresh_projects_clicked(self):
        """Handle refresh projects button click.

        Uses a background QThread to fetch fresh user context from server,
        avoiding the nested QEventLoop crash that occurs when making synchronous
        HTTP requests from within modal dialogs.
        """
        if not self.auth_manager.is_authenticated():
            self._log_message("Not logged in - cannot refresh", "error")
            return

        self._log_message("Refreshing company and project lists...", "info")
        self.refreshProjectsButton.setEnabled(False)

        # Get the base URL and token for the worker thread
        base_url = self.config.base_url
        token = self.api_client.token

        # Use a worker thread to avoid nested event loop crash
        self._refresh_worker = RefreshWorker(base_url, token)
        self._refresh_worker.finished.connect(self._on_refresh_finished)
        self._refresh_worker.error.connect(self._on_refresh_error)
        self._refresh_worker.start()

    def _on_refresh_finished(self, context_response):
        """Handle successful refresh from worker thread."""
        self.refreshProjectsButton.setEnabled(True)

        if not context_response:
            self._log_message("Failed to refresh: Could not get user context", "error")
            return

        try:
            # Convert API response to UserContext
            user_context = UserContext.from_api_response(context_response)

            # Update auth manager session
            if self.auth_manager.current_session:
                self.auth_manager.current_session.user_context = user_context
                self.auth_manager.current_session.user = user_context.user

            # Update project manager with fresh context
            self.project_manager.load_from_user_context(user_context)

            # Update UI with fresh data
            self._load_projects_from_context()

            self._log_message("Company and project lists refreshed.", "success")
        except Exception as e:
            self.logger.exception("Failed to process refresh response")
            self._log_message(f"Failed to refresh: {e}", "error")

    def _on_refresh_error(self, error_message):
        """Handle refresh error from worker thread."""
        self.refreshProjectsButton.setEnabled(True)
        self.logger.error(f"Failed to refresh projects: {error_message}")
        self._log_message(f"Failed to refresh: {error_message}", "error")

    def _on_company_changed(self, index: int):
        """Handle company selection change.

        Uses QTimer.singleShot to defer the API call, avoiding nested event loop
        crashes when making synchronous HTTP requests from within Qt signal handlers.
        """
        if index < 0:
            return

        company = self.companyComboBox.itemData(index)
        if not company:
            return

        # Defer the API call to avoid nested event loop crash
        # (QEventLoop.exec_() inside a combo box signal handler causes Qt crash)
        QTimer.singleShot(0, lambda: self._do_company_change(company))

    def _do_company_change(self, company):
        """Actually perform the company change after signal handler completes."""
        try:
            # Notify server and get updated company with fresh projects list
            updated_company = self.project_manager.select_company(company)
            if not updated_company:
                self._log_message(f"Failed to select company: {company.name}", "error")
                return
            self._log_message(f"Selected company: {updated_company.name}", "info")
        except Exception as e:
            self.logger.exception("Failed to select company")
            self._log_message(f"Failed to select company: {e}", "error")
            return

        # Populate projects using the updated company's projects list
        self.projectComboBox.clear()
        for project in updated_company.projects:
            self.projectComboBox.addItem(project.name, project)

        self.projectComboBox.setEnabled(len(updated_company.projects) > 0)

        # Update context header (project will update when project is selected)
        self._update_context_header()
    
    def _on_project_changed(self, index: int):
        """Handle project selection change.

        Uses QTimer.singleShot to defer the API call, avoiding nested event loop
        crashes when making synchronous HTTP requests from within Qt signal handlers.
        """
        if index < 0:
            self.pullButton.setEnabled(False)
            self.pushButton.setEnabled(False)
            self._update_storage_status()
            self._update_field_work_button_state()
            return

        project = self.projectComboBox.itemData(index)
        if not project:
            return

        # Defer the API call to avoid nested event loop crash
        QTimer.singleShot(0, lambda: self._do_project_change(project))

    def _do_project_change(self, project):
        """Actually perform the project change after signal handler completes."""
        try:
            # Check if this project is already the active one (skip API call if so)
            current_active = self.project_manager.active_project
            if current_active and current_active.id == project.id:
                # Project is already active, just update UI without API call
                self._log_message(f"Active project: {project.name}", "info")
            else:
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

            # Update context header
            self._update_context_header()

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
        self.assay_config_is_none = False  # Reset "None" flag when model changes

        # Update title
        self.selectedModelLabel.setText(f"{model_name}")

        # Show/hide options based on model
        # First hide all model-specific option groups
        self.includeCompanyLandCheckBox.setVisible(False)
        self.assayOptionsGroupBox.setVisible(False)
        if hasattr(self, 'projectFileGroupBox'):
            self.projectFileGroupBox.setVisible(False)

        # Note: Claims is now in a separate tab, not in the model list

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
        elif model_name == "FieldNote":
            # FieldNote is pull-only (no push from QGIS)
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
        self.assay_config_is_none = False

        # Always add "None" option first - plots points as gray circles
        self.assayConfigComboBox.addItem("None (Gray circles)")

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

            # Populate combo box with configurations (after "None" option)
            for config in configs:
                element = config.get('element', '?')
                element_display = config.get('element_display', element)
                name = config.get('name', 'Unnamed')
                units = config.get('units', '')
                display_text = f"{element_display} - {name} ({units})"
                self.assayConfigComboBox.addItem(display_text)

            if configs:
                self._log_message(f"Loaded {len(configs)} assay configuration(s)", "success")
            else:
                self._log_message("No assay configurations found. Select 'None' to plot as gray circles.", "info")

            # Select first item (the "None" option)
            self.assayConfigComboBox.setCurrentIndex(0)
            self._on_assay_config_selected(0)

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
        # Index 0 is always "None (Gray circles)"
        if index == 0:
            self.current_assay_config = None
            self.assay_config_is_none = True
            self.mergeSettingsLabel.setText("Points will be displayed as gray circles without assay data.")
            self.rangesTable.setRowCount(0)
            self._log_message("Selected: None (Gray circles)", "info")
            return

        # Adjust index for actual configurations (subtract 1 for the "None" option)
        config_index = index - 1
        if config_index < 0 or config_index >= len(self.assay_configurations):
            self.current_assay_config = None
            self.assay_config_is_none = False
            self.mergeSettingsLabel.setText("No configuration selected")
            self.rangesTable.setRowCount(0)
            return

        config = self.assay_configurations[config_index]
        self.current_assay_config = config
        self.assay_config_is_none = False

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

        # For PointSample/DrillSample, log the selected configuration
        if self.current_model in ["PointSample", "DrillSample"]:
            if self.assay_config_is_none:
                self._log_message("Using style: Gray circles (no assay data)", "info")
            elif self.current_assay_config:
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

            # Apply styling for PointSample/DrillSample
            if self.current_model in ["PointSample", "DrillSample"]:
                # Build suffix for layer lookup
                suffix_parts = []
                if ps_type_name:
                    suffix_parts.append(ps_type_name.replace(' ', ''))
                if assay_element and assay_units:
                    suffix_parts.append(assay_element)
                    suffix_parts.append(assay_units)
                suffix = '_'.join(suffix_parts) if suffix_parts else None

                if self.assay_config_is_none:
                    # Apply simple gray circle style
                    self._apply_simple_gray_styling(layer_name_suffix=suffix)
                elif self.current_assay_config:
                    # Apply assay color-coded styling
                    self._apply_assay_styling(self.current_assay_config, layer_name_suffix=suffix)

            # Apply styling for FieldNote (two layers: notes + photos)
            elif self.current_model == "FieldNote":
                self._apply_fieldnote_styling(result)

            # Apply styling for Structure (FGDC geological symbols)
            elif self.current_model == "Structure":
                self._apply_structure_styling(result)

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
            # Pass auth token for XYZ tile layer authentication
            result = raster_processor.process_project_files(
                project_files=[pf],  # Single file as list
                project_name=project.name,
                progress_callback=self._on_progress,
                auth_token=self.api_client.token
            )

            # Count both downloaded and tiled layers
            loaded_count = result.get('loaded', 0)
            tiled_count = result.get('tiled', 0)
            total_loaded = loaded_count + tiled_count

            if total_loaded > 0:
                layer_name = result['layers'][0] if result['layers'] else file_name
                if tiled_count > 0:
                    self._log_message(
                        f"XYZ tile layer '{layer_name}' added to QGIS (streaming from server)",
                        "success"
                    )
                else:
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

    def _apply_simple_gray_styling(self, layer_name_suffix: str = None):
        """Apply simple gray circle styling to the pulled layer."""
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

            # Apply simple gray circle style
            success = self.style_processor.apply_simple_gray_style(layer)

            if success:
                self._log_message(
                    "✓ Applied gray circle style (no assay coloring)",
                    "success"
                )
            else:
                self._log_message(
                    "Could not apply gray circle style",
                    "warning"
                )

        except Exception as e:
            self.logger.exception("Failed to apply simple gray styling")
            self._log_message(f"Failed to apply gray style: {e}", "warning")

    def _apply_fieldnote_styling(self, result: dict):
        """Apply styling to FieldNote layers (notes and photos)."""
        try:
            # Style the notes layer
            notes_layer = result.get('notes_layer')
            if notes_layer:
                success = self.style_processor.apply_fieldnote_style(notes_layer)
                if success:
                    self._log_message(
                        "✓ Applied field note styling (blue markers)",
                        "success"
                    )

            # Style the photos layer
            photos_layer = result.get('photos_layer')
            if photos_layer:
                success = self.style_processor.apply_fieldnote_photo_style(photos_layer)
                if success:
                    photo_count = result.get('photo_count', 0)
                    self._log_message(
                        f"✓ Applied photo styling ({photo_count} photos with click-to-view)",
                        "success"
                    )

        except Exception as e:
            self.logger.exception("Failed to apply field note styling")
            self._log_message(f"Failed to apply styling: {e}", "warning")

    def _apply_structure_styling(self, result: dict):
        """Apply FGDC-standard geological structure styling to the pulled layer."""
        try:
            # Get layer directly from result (most reliable)
            layer = result.get('layer') if isinstance(result, dict) else None

            # Fallback: find by name
            if not layer:
                project = self.project_manager.active_project
                if project:
                    layer_name = f"{project.name}_Structure"
                else:
                    layer_name = "Structure"

                layer = self.sync_manager.layer_processor.find_layer_by_name(layer_name)
                if not layer:
                    # Try without project prefix
                    layer = self.sync_manager.layer_processor.find_layer_by_name("Structure")

            if not layer:
                self._log_message(
                    "Could not find Structure layer for styling",
                    "warning"
                )
                return

            self.logger.info(f"Applying structure styling to layer: {layer.name()}")
            self.logger.info(f"Layer has {layer.featureCount()} features")
            self.logger.info(f"Layer fields: {[f.name() for f in layer.fields()]}")

            # Apply FGDC structure styling
            success = self.style_processor.apply_structure_style(layer)

            if success:
                self._log_message(
                    "✓ Applied FGDC geological structure symbology with dip labels",
                    "success"
                )
            else:
                self._log_message(
                    "Could not apply structure styling - using default style",
                    "warning"
                )

        except Exception as e:
            self.logger.exception("Failed to apply structure styling")
            self._log_message(f"Failed to apply structure styling: {e}", "warning")

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
        # Always set merge_assays=True for sample models (required by API)
        # but only pass config ID if an actual config is selected
        if self.assayOptionsGroupBox.isVisible():
            options['merge_assays'] = True
            if self.current_assay_config:
                options['assay_config'] = self.current_assay_config
                options['assay_config_id'] = self.current_assay_config.get('id')

        return options
    
    def _on_progress(self, percent: int, message: str):
        """Handle progress updates."""
        self.progressBar.setValue(percent)
        self._log_message(f"[{percent}%] {message}", "info")
    
    # ==================== MESSAGES ====================
    
    def _log_message(self, message: str, level: str = "info", _from_logger: bool = False):
        """Add message to the message browser.

        Args:
            message: The message text to display
            level: Message level ('info', 'success', 'warning', 'error')
            _from_logger: Internal flag - True when called from UILogHandler to prevent
                         feedback loop. Don't set this manually.
        """
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

        # Highlight Log tab if not currently visible and message is important
        if level in ("error", "warning") and hasattr(self, 'mainTabWidget'):
            self._highlight_log_tab(level)

        # Also log to file - BUT NOT if this message came from the logger
        # (to avoid infinite feedback loop: logger -> UIHandler -> _log_message -> logger)
        if not _from_logger:
            if level == "error":
                self.logger.error(message)
            elif level == "warning":
                self.logger.warning(message)
            else:
                self.logger.info(message)

    def _highlight_log_tab(self, level: str = "warning"):
        """Highlight the Log tab to indicate new important messages."""
        if not hasattr(self, 'mainTabWidget'):
            return

        # Find the log tab index
        log_tab_index = -1
        for i in range(self.mainTabWidget.count()):
            if self.mainTabWidget.widget(i).objectName() == 'logTab':
                log_tab_index = i
                break

        if log_tab_index < 0:
            return

        # Only highlight if we're not already on the log tab
        if self.mainTabWidget.currentIndex() == log_tab_index:
            return

        # Set tab text color based on level
        tab_bar = self.mainTabWidget.tabBar()
        if level == "error":
            tab_bar.setTabTextColor(log_tab_index, QColor("#d32f2f"))
        else:
            tab_bar.setTabTextColor(log_tab_index, QColor("#ff9800"))

    def _reset_log_tab_highlight(self):
        """Reset the Log tab highlight when it's viewed."""
        if not hasattr(self, 'mainTabWidget'):
            return

        # Find the log tab index
        for i in range(self.mainTabWidget.count()):
            if self.mainTabWidget.widget(i).objectName() == 'logTab':
                tab_bar = self.mainTabWidget.tabBar()
                # Reset to default color (empty QColor uses default)
                tab_bar.setTabTextColor(i, QColor())
                break
    
    def _clear_messages(self):
        """Clear the messages browser."""
        self.messagesTextBrowser.clear()
        self._log_message("Messages cleared.", "info")

    # ==================== STORAGE MANAGEMENT ====================

    def _add_storage_button(self):
        """Add storage configuration button to the project selection section."""
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
        # The project layout is inside projectGroupBox -> projectGroupLayout -> projectLayout
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
            response = self.api_client.get_all_paginated(
                model_name='ProjectFile',
                project_id=project.id,
                params=params,
                include_deletion_metadata=False  # Just need the list
            )

            # Handle both dict (with 'results' key) and list responses for backwards compatibility
            if isinstance(response, dict):
                files = response.get('results', [])
            else:
                files = response if response else []

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
                file_size = pf.get('file_size') or 0  # Handle None values
                tiles_available = pf.get('tiles_available', False)
                tiles_status = pf.get('tiles_status', 'none')

                # Format file size
                if file_size > 1024 * 1024 * 1024:
                    size_str = f"{file_size / (1024*1024*1024):.1f} GB"
                elif file_size > 1024 * 1024:
                    size_str = f"{file_size / (1024*1024):.1f} MB"
                elif file_size > 1024:
                    size_str = f"{file_size / 1024:.1f} KB"
                else:
                    size_str = f"{file_size} bytes"

                # Add tile indicator for files with XYZ tiles available
                if tiles_available and tiles_status == 'completed':
                    display_text = f"{name} [{category}] (XYZ tiles)"
                else:
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
        file_size = pf.get('file_size') or 0  # Handle None values
        # Prefer numeric epsg (new), fall back to crs string (legacy)
        epsg = pf.get('epsg')
        crs = f"EPSG:{epsg}" if epsg else (pf.get('crs') or 'Not specified')
        resolution = pf.get('resolution')
        bounds = pf.get('bounds')

        # XYZ tile information
        tiles_available = pf.get('tiles_available', False)
        tiles_status = pf.get('tiles_status', 'none')
        tile_min_zoom = pf.get('tile_min_zoom')
        tile_max_zoom = pf.get('tile_max_zoom')

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

        # Format tile info
        if tiles_available and tiles_status == 'completed':
            tile_info = f"<span style='color:green;'>XYZ Tiles (zoom {tile_min_zoom}-{tile_max_zoom})</span>"
            delivery_method = "Streaming (tiles)"
        elif tiles_status == 'processing':
            tile_info = "<span style='color:orange;'>Processing tiles...</span>"
            delivery_method = "Download (file)"
        elif tiles_status == 'queued':
            tile_info = "<span style='color:gray;'>Tile generation queued</span>"
            delivery_method = "Download (file)"
        else:
            tile_info = "Not available"
            delivery_method = "Download (file)"

        html = f"""
        <table style="width:100%">
            <tr><td><b>Name:</b></td><td>{name}</td></tr>
            <tr><td><b>Category:</b></td><td>{category}</td></tr>
            <tr><td><b>Size:</b></td><td>{size_str}</td></tr>
            <tr><td><b>Delivery:</b></td><td>{delivery_method}</td></tr>
            <tr><td><b>XYZ Tiles:</b></td><td>{tile_info}</td></tr>
            <tr><td><b>CRS:</b></td><td>{crs}</td></tr>
            <tr><td><b>Resolution:</b></td><td>{res_str}</td></tr>
            <tr><td><b>Bounds:</b></td><td style="font-size:9pt">{bounds_str}</td></tr>
        </table>
        <p style="margin-top:8px; color:#6b7280;"><i>{description}</i></p>
        """

        self.projectFileMetadata.setHtml(html)

        # Log with delivery method info
        if tiles_available and tiles_status == 'completed':
            self._log_message(f"Selected: {name} (XYZ tiles, zoom {tile_min_zoom}-{tile_max_zoom})", "info")
        else:
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
        """Add field work planning button and pull field tasks button."""
        # Create the field work planning button
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

        # Create the pull field tasks button
        self.pullFieldTasksButton = QPushButton("Pull Field Tasks")
        self.pullFieldTasksButton.setEnabled(False)  # Enable when logged in with view permission
        self.pullFieldTasksButton.setToolTip(
            "Pull planned and assigned samples as a field tasks layer\n"
            "(separate from assay-colored layers)"
        )
        self.pullFieldTasksButton.clicked.connect(self._on_pull_field_tasks_clicked)

        # Style the button (blue to distinguish from green plan button)
        self.pullFieldTasksButton.setStyleSheet("""
            QPushButton {
                background-color: #2563eb;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #1d4ed8;
            }
            QPushButton:disabled {
                background-color: #9ca3af;
            }
        """)

        # Find the button layout and add our buttons
        # The buttonLayout contains pullButton and pushButton
        button_layout = self.buttonLayout

        # Insert buttons after pushButton
        button_layout.insertWidget(2, self.pullFieldTasksButton)
        button_layout.insertWidget(3, self.fieldWorkButton)

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

    def _on_pull_field_tasks_clicked(self):
        """Handle pull field tasks button click - pull planned/assigned samples."""
        if not self.project_manager.active_project:
            QMessageBox.warning(
                self, "No Project",
                "Please select a project before pulling field tasks."
            )
            return

        if not self.project_manager.can_view():
            QMessageBox.warning(
                self, "Permission Denied",
                "You need view permission to pull field tasks."
            )
            return

        try:
            self._log_message("Starting pull for field tasks (planned/assigned samples)...", "info")
            self.pullFieldTasksButton.setEnabled(False)
            self.progressBar.setVisible(True)
            self.progressBar.setValue(0)

            # Pull field tasks
            result = self.data_manager.pull_field_tasks(
                progress_callback=self._on_progress
            )

            pulled = result.get('pulled', 0)
            layer = result.get('layer')

            if pulled > 0:
                self._log_message(
                    f"✓ Field tasks pull complete: {pulled} tasks",
                    "success"
                )
                # Apply status-based styling to the layer
                if layer:
                    self._apply_field_task_styling(layer)
            else:
                self._log_message(
                    "No planned or assigned samples found for this project.",
                    "info"
                )

        except Exception as e:
            self.logger.exception("Pull field tasks failed")
            self._log_message(f"Pull field tasks failed: {e}", "error")
            QMessageBox.critical(self, "Error", f"Pull field tasks failed: {e}")
        finally:
            self.pullFieldTasksButton.setEnabled(True)
            self.progressBar.setVisible(False)

    def _apply_field_task_styling(self, layer):
        """Apply status-based categorized styling to field tasks layer."""
        try:
            success = self.style_processor.apply_field_task_style(layer)
            if success:
                self._log_message(
                    "✓ Applied status-based styling (Planned=gray, Assigned=yellow)",
                    "success"
                )
            else:
                self._log_message(
                    "Could not apply status styling - 'status' field not found",
                    "warning"
                )
        except Exception as e:
            self.logger.exception("Failed to apply field task styling")
            self._log_message(f"Failed to apply styling: {e}", "warning")

    def _update_field_work_button_state(self):
        """Update field work button enabled state based on permissions."""
        # Plan Field Samples button - requires edit permission
        if hasattr(self, 'fieldWorkButton'):
            can_edit = (
                self.current_session is not None and
                self.project_manager.active_project is not None and
                self.project_manager.can_edit()
            )
            self.fieldWorkButton.setEnabled(can_edit)

        # Pull Field Tasks button - requires view permission
        if hasattr(self, 'pullFieldTasksButton'):
            can_view = (
                self.current_session is not None and
                self.project_manager.active_project is not None and
                self.project_manager.can_view()
            )
            self.pullFieldTasksButton.setEnabled(can_view)

    # ==================== CLAIMS MANAGEMENT ====================

    def _setup_claims_ui(self):
        """Set up the Claims widget in the Claims tab.

        Initially sets up the claims wizard. The widget will be swapped
        to ClaimsOrderWidget for pay-per-claim users when their access
        level is determined after login.
        """
        # Create claims wizard widget (default for staff/enterprise users)
        self.claims_wizard = ClaimsWizardWidget(self.claims_manager, self)

        # Connect signals
        self.claims_wizard.status_message.connect(self._on_claims_status)
        self.claims_wizard.claims_processed.connect(self._on_claims_processed)
        self.claims_wizard.wizard_completed.connect(self._on_wizard_completed)

        # Add to the claims tab layout (defined in .ui file)
        if hasattr(self, 'claimsTabLayout'):
            self.claimsTabLayout.addWidget(self.claims_wizard)

        # Connect tab change signal to update wizard when Claims tab is selected
        if hasattr(self, 'mainTabWidget'):
            self.mainTabWidget.currentChanged.connect(self._on_tab_changed)

    def _setup_basemaps_ui(self):
        """Set up the Basemaps widget in the Basemaps tab."""
        # Create basemaps widget
        self.basemaps_widget = BasemapsWidget(self)

        # Connect signals
        self.basemaps_widget.basemap_added.connect(self._on_basemap_added)

        # Add to the basemaps tab layout (defined in .ui file)
        if hasattr(self, 'basemapsTabLayout'):
            self.basemapsTabLayout.addWidget(self.basemaps_widget)

    def _on_basemap_added(self, layer_name: str):
        """Handle basemap added signal."""
        self._log_message(f"Added basemap: {layer_name}", "success")

    def _on_tab_changed(self, index: int):
        """Handle main tab change."""
        if not hasattr(self, 'mainTabWidget'):
            return

        tab_widget = self.mainTabWidget.widget(index)
        if not tab_widget:
            return

        tab_name = tab_widget.objectName()

        if tab_name == 'claimsTab':
            # Claims tab selected - update wizard with project context
            self._update_claims_wizard_context()
            self._log_message("Claims Wizard - Create and manage US lode mining claims", "info")
        elif tab_name == 'logTab':
            # Log tab selected - scroll to bottom to show latest messages
            scrollbar = self.messagesTextBrowser.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())
            # Reset any highlight on the tab
            self._reset_log_tab_highlight()

    def _update_claims_wizard_context(self):
        """Update the claims widget with current project context."""
        if not self.project_manager.active_project or not self.project_manager.active_company:
            return

        project_id = self.project_manager.active_project.id
        company_id = self.project_manager.active_company.id

        # Update whichever widget is currently active
        # Use try/except to guard against deleted C++ objects
        try:
            if self.claims_wizard and not sip.isdeleted(self.claims_wizard):
                self.claims_wizard.set_project(project_id, company_id)
        except (RuntimeError, ReferenceError):
            pass

        try:
            if self.claims_order_widget and not sip.isdeleted(self.claims_order_widget):
                self.claims_order_widget.set_project(project_id, company_id)
        except (RuntimeError, ReferenceError):
            pass

    def _update_claims_tab_for_access(self, access_info: dict):
        """
        Show appropriate claims widget based on access level.

        For staff/enterprise users: Show ClaimsWizardWidget (full processing)
        For pay-per-claim users: Show ClaimsOrderWidget (simplified ordering)

        Args:
            access_info: Dict from claims_manager.check_access() containing:
                - can_process_immediately: bool
                - is_staff: bool
                - access_type: str
        """
        can_process = access_info.get('can_process_immediately', False)
        is_staff = access_info.get('is_staff', False)

        if can_process or is_staff:
            # Full access - show wizard (already the default)
            self._show_claims_wizard()
        else:
            # Pay-per-claim - show order widget
            self._show_claims_order_widget()

    def _show_claims_wizard(self):
        """Show the full ClaimsWizardWidget for staff/enterprise users."""
        if not hasattr(self, 'claimsTabLayout'):
            return

        # If order widget is showing, hide it
        if self.claims_order_widget:
            self.claims_order_widget.hide()

        # Create wizard if not exists
        if not self.claims_wizard:
            self.claims_wizard = ClaimsWizardWidget(self.claims_manager, self)
            self.claims_wizard.status_message.connect(self._on_claims_status)
            self.claims_wizard.claims_processed.connect(self._on_claims_processed)
            self.claims_wizard.wizard_completed.connect(self._on_wizard_completed)
            self.claimsTabLayout.addWidget(self.claims_wizard)

        # Show wizard
        self.claims_wizard.show()

        # Update context
        self._update_claims_wizard_context()

    def _show_claims_order_widget(self):
        """Show the ClaimsOrderWidget for pay-per-claim users."""
        if not hasattr(self, 'claimsTabLayout'):
            return

        # If wizard is showing, hide it
        if self.claims_wizard:
            self.claims_wizard.hide()

        # Create order widget if not exists
        if not self.claims_order_widget:
            self.claims_order_widget = ClaimsOrderWidget(self.claims_manager, self)
            self.claims_order_widget.status_message.connect(self._on_claims_status)
            self.claims_order_widget.order_submitted.connect(self._on_order_submitted)
            self.claimsTabLayout.addWidget(self.claims_order_widget)

        # Show order widget
        self.claims_order_widget.show()

        # Update context
        self._update_claims_wizard_context()

    def _on_wizard_completed(self):
        """Handle claims wizard completion."""
        self._log_message("Claims workflow completed successfully!", "success")

    def _show_claims_ui(self):
        """Switch to claims tab."""
        if hasattr(self, 'mainTabWidget'):
            for i in range(self.mainTabWidget.count()):
                if self.mainTabWidget.widget(i).objectName() == 'claimsTab':
                    self.mainTabWidget.setCurrentIndex(i)
                    break

    def _hide_claims_ui(self):
        """Switch to data sync tab."""
        if hasattr(self, 'mainTabWidget'):
            for i in range(self.mainTabWidget.count()):
                if self.mainTabWidget.widget(i).objectName() == 'dataSyncTab':
                    self.mainTabWidget.setCurrentIndex(i)
                    break

    def _show_log_tab(self):
        """Switch to log tab to show messages."""
        if hasattr(self, 'mainTabWidget'):
            for i in range(self.mainTabWidget.count()):
                if self.mainTabWidget.widget(i).objectName() == 'logTab':
                    self.mainTabWidget.setCurrentIndex(i)
                    break

    def _on_claims_status(self, message: str, level: str):
        """Handle status messages from claims widget."""
        self._log_message(message, level)

    def _on_claims_processed(self, result: dict):
        """Handle claims processing completion."""
        claims_count = len(result.get('claims', []))
        waypoints_count = len(result.get('waypoints', []))

        self._log_message(
            f"Claims processed: {claims_count} claims, {waypoints_count} waypoints",
            "success"
        )

    def _on_order_submitted(self, order: dict):
        """Handle order submission."""
        order_id = order.get('order_id')
        status = order.get('status', 'unknown')

        self._log_message(
            f"Order #{order_id} submitted (status: {status})",
            "info"
        )

        # Show order status dialog
        from .claims_order_dialog import ClaimsOrderDialog
        dialog = ClaimsOrderDialog(self.claims_manager, order_id, self)
        dialog.exec_()

    def cleanup(self):
        """Clean up resources before plugin unload to prevent crashes.

        This method is called by plugin.unload() to properly release
        all resources before the plugin is reloaded.
        """
        # Reset the logger completely to prevent stale handlers across reloads
        PluginLogger.reset()

        # Stop any running refresh worker thread
        if hasattr(self, '_refresh_worker') and self._refresh_worker is not None:
            try:
                self._refresh_worker.finished.disconnect()
                self._refresh_worker.error.disconnect()
                if self._refresh_worker.isRunning():
                    self._refresh_worker.quit()
                    self._refresh_worker.wait(1000)  # Wait up to 1 second
                    if self._refresh_worker.isRunning():
                        self._refresh_worker.terminate()
            except Exception:
                pass
            self._refresh_worker = None

        # Clean up claims wizard widget
        if self.claims_wizard is not None:
            try:
                # Call cleanup first to prevent deferred callbacks from crashing
                self.claims_wizard.cleanup()
            except Exception:
                pass
            try:
                self.claims_wizard.status_message.disconnect()
                self.claims_wizard.claims_processed.disconnect()
                self.claims_wizard.wizard_completed.disconnect()
            except Exception:
                pass
            try:
                self.claims_wizard.deleteLater()
            except Exception:
                pass
            self.claims_wizard = None

        # Clean up claims order widget
        if self.claims_order_widget is not None:
            try:
                self.claims_order_widget.status_message.disconnect()
                self.claims_order_widget.order_submitted.disconnect()
            except Exception:
                pass
            try:
                self.claims_order_widget.deleteLater()
            except Exception:
                pass
            self.claims_order_widget = None

        # Clean up basemaps widget
        if self.basemaps_widget is not None:
            try:
                self.basemaps_widget.basemap_added.disconnect()
            except Exception:
                pass
            try:
                self.basemaps_widget.deleteLater()
            except Exception:
                pass
            self.basemaps_widget = None

    def closeEvent(self, event):
        """Handle dialog close - cleanup UI log handler."""
        PluginLogger.unregister_ui_handler()
        super().closeEvent(event)