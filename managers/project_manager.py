# -*- coding: utf-8 -*-
"""
Project and company management for GeodbIO plugin.

Uses user context from /api/v1/me/ for company/project data and permissions.
"""
from typing import List, Optional, Dict
from qgis.core import QgsProject

from ..api.client import APIClient
from ..models.project import Company, Project, Permission
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
        # Keep for backwards compatibility
        self.permissions: Dict[str, Permission] = {}

    def load_from_user_context(self, user_context: UserContext) -> None:
        """
        Load companies and projects from user context.

        This is the primary way to populate the project manager.
        Called after login with the context from /api/v1/me/.

        Args:
            user_context: UserContext from authentication
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
                if ac.name == ap.company:
                    project = Project(
                        id=ap.id,
                        name=ap.name,
                        company_id=ac.id,
                        company_name=ap.company,
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

        # Set active project/company from context
        if user_context.active_project:
            ap = user_context.active_project
            self.active_project = Project(
                id=ap.id,
                name=ap.name,
                company_id=user_context.active_company.id if user_context.active_company else 0,
                company_name=ap.company,
                crs=ap.crs
            )

        if user_context.active_company:
            ac = user_context.active_company
            # Find the full company object
            for company in self.companies:
                if company.id == ac.id:
                    self.active_company = company
                    break

        self.logger.info(f"Loaded {len(self.companies)} companies from user context")

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
            # Notify server of project selection
            response = self.api_client.set_active_project(project.id)
            user_context = UserContext.from_api_response(response)

            # Update local state
            self.active_project = project
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

    def select_company(self, company: Company) -> bool:
        """
        Select a company and notify the server.

        Args:
            company: Company to select

        Returns:
            True if successful
        """
        self.logger.info(f"Selecting company: {company}")

        try:
            # Notify server of company selection
            response = self.api_client.set_active_company(company.id)
            user_context = UserContext.from_api_response(response)

            # Update local state
            self.active_company = company
            self.user_status = user_context.user_status
            self.can_create = user_context.can_create

            # Update project if set
            if user_context.active_project:
                ap = user_context.active_project
                self.active_project = Project(
                    id=ap.id,
                    name=ap.name,
                    company_id=company.id,
                    company_name=ap.company,
                    crs=ap.crs
                )
            else:
                self.active_project = None

            self.logger.info(f"Company selected: {company.name}")
            return True

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

    def can_admin_data(self) -> bool:
        """Check if user has admin access."""
        return self.user_status in ['creator', 'owner', 'manager', 'admin']

    def can_create_records(self) -> bool:
        """Check if user can create new records."""
        return self.can_create

    # Legacy methods for backwards compatibility
    def get_permission(self, model_name: str) -> Optional[Permission]:
        """Get permission for a specific model (legacy)."""
        return self.permissions.get(model_name)

    def can_view(self, model_name: str = None) -> bool:
        """Check if user can view a model."""
        return self.can_view_data()

    def can_edit(self, model_name: str = None) -> bool:
        """Check if user can edit a model."""
        return self.can_edit_data()

    def can_admin(self, model_name: str = None) -> bool:
        """Check if user has admin access to a model."""
        return self.can_admin_data()

    def load_permissions(self, project_id: int) -> Dict[str, Permission]:
        """
        DEPRECATED: Permissions now come from user context.

        The /api/v1/me/ endpoint returns user_status which determines
        permissions across all models.
        """
        self.logger.warning("load_permissions() is deprecated. Use user_status from user context.")
        return self.permissions

    def get_active_project(self) -> Optional[Project]:
        """Get currently active project."""
        return self.active_project

    def get_active_company(self) -> Optional[Company]:
        """Get currently active company."""
        return self.active_company

    def get_companies(self) -> List[Company]:
        """Get list of loaded companies."""
        return self.companies

    def get_projects_for_company(self, company_id: int) -> List[Project]:
        """Get projects for a specific company."""
        for company in self.companies:
            if company.id == company_id:
                return company.projects
        return []

    def restore_from_project_vars(self) -> Optional[Project]:
        """
        Restore project selection from QGIS project variables.

        Returns:
            Project if found, None otherwise
        """
        qgs_project = QgsProject.instance()

        project_id, ok = qgs_project.readNumEntry(
            self.PROJECT_VAR_SECTION,
            self.PROJECT_ID_KEY,
            0
        )

        if not ok or project_id == 0:
            return None

        # Find project in loaded companies
        for company in self.companies:
            for project in company.projects:
                if project.id == project_id:
                    self.active_project = project
                    self.active_company = company
                    self.logger.info(f"Restored project from vars: {project}")
                    return project

        return None

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
