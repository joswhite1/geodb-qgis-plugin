# -*- coding: utf-8 -*-
"""
Project and company management for GeodbIO plugin.

Uses user context from /api/v1/me/ for company/project data and permissions.
"""
from typing import List, Optional, Dict
from qgis.core import QgsProject

from ..api.client import APIClient
from ..models.project import Company, Project
from ..models.auth import UserContext
from ..utils.config import Config
from ..utils.logger import PluginLogger


class ProjectManager:
    """
    Manages companies, projects, and permissions.

    Gets company/project data from user context (returned by /api/v1/me/).
    Stores selected project in QGIS project variables.
    """

    PROJECT_VAR_SECTION = "geodb_vars"
    PROJECT_VAR_KEY = "selected_project"
    COMPANY_VAR_KEY = "selected_company"
    PROJECT_ID_KEY = "selected_project_id"
    PROJECT_CRS_KEY = "selected_project_crs"

    def __init__(self, config: Config, api_client: APIClient):
        """
        Initialize project manager.

        Args:
            config: Configuration instance
            api_client: API client instance
        """
        self.config = config
        self.api_client = api_client
        self.logger = PluginLogger.get_logger()

        self.companies: List[Company] = []
        self.active_project: Optional[Project] = None
        self.active_company: Optional[Company] = None
        self.user_status: Optional[str] = None
        self.can_create: bool = False

    def load_from_user_context(self, user_context: UserContext,
                               keep_selection: bool = True) -> None:
        """
        Load companies and projects from user context.

        This is the primary way to populate the project manager.
        Called after login, session restore, session renewal and Refresh with
        the context from /api/v1/me/.

        The plugin LEADS the server's active project (like the mobile app): a
        project this plugin already works in is KEPT, and the server is told
        to follow it. The server's active project is adopted only when the
        plugin has none (first sign-in: the project saved in this QGIS project
        wins, then the server's), or when the user has lost access to it.
        Before 2026-09-30 every one of these reloads adopted the server's
        project, so switching project in the browser silently moved the
        plugin mid-job.

        Args:
            user_context: UserContext from authentication
            keep_selection: False only for an explicit company switch, where
                the server's answer is the user's new choice.
        """
        self.logger.info("Loading companies and projects from user context")

        # Group projects by company
        company_projects: Dict[int, List[Project]] = {}
        company_names: Dict[int, str] = {}

        for ac in user_context.accessible_companies:
            company_names[ac.id] = ac.name
            company_projects[ac.id] = []

        for ap in user_context.accessible_projects:
            # Find company ID for this project
            for ac in user_context.accessible_companies:
                if ac.name == ap.company_name:
                    project = Project(
                        id=ap.id,
                        name=ap.name,
                        company_id=ac.id,
                        company_name=ap.company_name,
                        crs=ap.crs
                    )
                    company_projects[ac.id].append(project)
                    break

        # Build company list
        self.companies = []
        for company_id, projects in company_projects.items():
            company = Company(
                id=company_id,
                name=company_names.get(company_id, ''),
                projects=projects
            )
            self.companies.append(company)

        # Set user status and permissions
        self.user_status = user_context.user_status
        self.can_create = user_context.can_create

        server_project_id = user_context.active_project.id if user_context.active_project else None

        # The plugin's own project: the one it works in now, else the one this
        # QGIS project was last saved with. Kept only while still accessible.
        own_id = None
        if keep_selection:
            own_id = self.active_project.id if self.active_project else self._saved_project_id()
        own = self.find_project(own_id) if own_id else None
        if own_id and own is None:
            self.logger.warning(
                f"Project {own_id} is no longer accessible; using the server's active project")

        if own is not None:
            self.active_project = own
            self.active_company = self._find_company(own.company_id)
            if own.id != server_project_id:
                self.logger.info(
                    f"Keeping this plugin's project '{own.name}' (server was on "
                    f"{user_context.active_project.name if user_context.active_project else 'none'})")
                self._assert_on_server(own)
            else:
                self.api_client.pin_project(own.id)
            self.logger.info(f"Loaded {len(self.companies)} companies from user context")
            return

        # Set active project/company from context
        self.active_project = None
        if user_context.active_project:
            ap = user_context.active_project
            self.active_project = Project(
                id=ap.id,
                name=ap.name,
                company_id=user_context.active_company.id if user_context.active_company else 0,
                company_name=ap.company_name,
                crs=ap.crs
            )

        if user_context.active_company:
            ac = user_context.active_company
            # Find the full company object
            for company in self.companies:
                if company.id == ac.id:
                    self.active_company = company
                    break

        # A company switch unpins until the user picks the project; otherwise
        # the server's project is now this plugin's.
        self.api_client.pin_project(server_project_id if keep_selection else None)
        self.logger.info(f"Loaded {len(self.companies)} companies from user context")

    def find_project(self, project_id: Optional[int]) -> Optional[Project]:
        for company in self.companies:
            for project in company.projects:
                if project.id == project_id:
                    return project
        return None

    def _find_company(self, company_id: Optional[int]) -> Optional[Company]:
        return next((c for c in self.companies if c.id == company_id), None)

    def _saved_project_id(self) -> Optional[int]:
        """The project id this QGIS project was saved with (_save_to_project_vars)."""
        try:
            value, ok = QgsProject.instance().readNumEntry(
                self.PROJECT_VAR_SECTION, self.PROJECT_ID_KEY, 0)
            return int(value) if ok and value else None
        except Exception:
            return None

    def _assert_on_server(self, project: Project) -> None:
        """Make the server's active project this one and take its permissions.

        user_status / can_create in a user context describe the SERVER's
        active project, so after keeping our own they must be re-read for it.
        A failure is logged, not raised (this runs inside sign-in); the pin
        stays un-asserted, so the API client retries before the next request.
        """
        self.api_client.pin_project(project.id, asserted=False)
        try:
            response = self.api_client.set_active_project(project.id)
            context = UserContext.from_api_response(response)
            self.user_status = context.user_status
            self.can_create = context.can_create
            self.api_client.pin_project(project.id)
        except Exception as e:
            self.logger.warning(f"Could not set the server's active project to {project.name}: {e}")

    def clear_selection(self) -> None:
        """Forget the plugin's project (logout, or a switch to another server)."""
        self.active_project = None
        self.active_company = None
        self.api_client.pin_project(None)

    def load_companies(self) -> List[Company]:
        """
        Load user's companies and projects from API.

        NOTE: Prefer using load_from_user_context() which uses data
        already fetched from /api/v1/me/.

        Returns:
            List of Company objects
        """
        self.logger.info("Loading companies and projects from API")

        try:
            # Fetch user context directly
            response = self.api_client.get_user_context()
            user_context = UserContext.from_api_response(response)
            self.load_from_user_context(user_context)
            return self.companies

        except Exception as e:
            self.logger.error(f"Failed to load companies: {e}")
            raise

    def select_project(self, project: Project) -> bool:
        """
        Select a project and notify the server.

        Args:
            project: Project to select

        Returns:
            True if successful
        """
        self.logger.info(f"Selecting project: {project}")

        try:
            # Clear custom field schema cache for old project
            # (schemas may differ between projects)
            from ..models.schema_cache import clear_cache
            if self.active_project and self.active_project.id != project.id:
                clear_cache(self.active_project.id)
                self.logger.debug(f"Cleared schema cache for project {self.active_project.id}")

            # Notify server of project selection
            response = self.api_client.set_active_project(project.id)
            user_context = UserContext.from_api_response(response)

            # Update local state
            self.active_project = project
            self.api_client.pin_project(project.id)
            self.user_status = user_context.user_status
            self.can_create = user_context.can_create

            # Update company if changed
            if user_context.active_company:
                for company in self.companies:
                    if company.id == user_context.active_company.id:
                        self.active_company = company
                        break

            # Store in QGIS project variables
            self._save_to_project_vars(project)

            self.logger.info(f"Project selected: {project.name}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to select project: {e}")
            raise

    def select_company(self, company: Company) -> Optional[Company]:
        """
        Select a company and notify the server.

        Args:
            company: Company to select

        Returns:
            Updated Company object with refreshed projects list, or None on failure
        """
        self.logger.info(f"Selecting company: {company}")

        try:
            # Stop re-asserting the old project while the company changes; the
            # project picked next (select_project) pins the new one.
            self.api_client.pin_project(None)

            # Notify server of company selection
            response = self.api_client.set_active_company(company.id)
            user_context = UserContext.from_api_response(response)

            # Reload companies from the fresh user context
            # This ensures we have the correct projects for the selected company
            self.load_from_user_context(user_context, keep_selection=False)

            # Find and return the updated company object
            for c in self.companies:
                if c.id == company.id:
                    self.active_company = c
                    self.logger.info(
                        f"Company selected: {c.name} with {len(c.projects)} projects"
                    )
                    return c

            self.logger.warning(f"Company {company.id} not found after reload")
            return None

        except Exception as e:
            self.logger.error(f"Failed to select company: {e}")
            raise

    def get_permission_level(self) -> Optional[str]:
        """
        Get user's permission level for the current project.

        Returns one of: creator, owner, manager, admin, adder, viewer, or None
        """
        return self.user_status

    def can_view_data(self) -> bool:
        """Check if user can view data."""
        return self.user_status in ['creator', 'owner', 'manager', 'admin', 'adder', 'viewer']

    def can_edit_data(self) -> bool:
        """Check if user can edit data."""
        return self.user_status in ['creator', 'owner', 'manager', 'admin', 'adder']

    def can_create_records(self) -> bool:
        """Check if user can create new records."""
        return self.can_create

    def can_view(self, model_name: str = None) -> bool:
        """Check if user can view a model."""
        return self.can_view_data()

    def can_edit(self, model_name: str = None) -> bool:
        """Check if user can edit a model."""
        return self.can_edit_data()

    def get_active_project(self) -> Optional[Project]:
        """Get currently active project."""
        return self.active_project

    def get_companies(self) -> List[Company]:
        """Get list of loaded companies."""
        return self.companies

    def _save_to_project_vars(self, project: Project):
        """Save project selection to QGIS project variables."""
        qgs_project = QgsProject.instance()

        qgs_project.writeEntry(
            self.PROJECT_VAR_SECTION,
            self.PROJECT_VAR_KEY,
            project.name
        )
        qgs_project.writeEntry(
            self.PROJECT_VAR_SECTION,
            self.COMPANY_VAR_KEY,
            project.company_name
        )
        qgs_project.writeEntry(
            self.PROJECT_VAR_SECTION,
            self.PROJECT_ID_KEY,
            project.id
        )
        qgs_project.writeEntry(
            self.PROJECT_VAR_SECTION,
            self.PROJECT_CRS_KEY,
            project.crs
        )

        self.logger.debug("Saved project to QGIS project variables")
