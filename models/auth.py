# -*- coding: utf-8 -*-
"""
Authentication data models.

These models represent the authentication state and user context
as returned by the geodb.io API v1.
"""
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any


@dataclass
class UserInfo:
    """User information from authentication."""
    user_id: int
    username: str
    email: str
    first_name: str = ""
    last_name: str = ""

    @property
    def full_name(self) -> str:
        """Get full name or fallback to username."""
        if self.first_name or self.last_name:
            return f"{self.first_name} {self.last_name}".strip()
        return self.username


@dataclass
class Company:
    """Company information from user context."""
    id: int
    name: str


@dataclass
class Project:
    """Project information from user context."""
    id: int
    name: str
    company: str
    crs: str = "4326"  # Default to WGS84
    proj4_string: Optional[str] = None  # Custom proj4 string for local grid CRS


@dataclass
class PointSampleType:
    """Point sample type for filtering (e.g., Soil, Rock Chip, Stream Sediment)."""
    id: int
    name: str


@dataclass
class AssayMergeSettings:
    """User's assay merge settings for a project."""
    default_strategy: str = "high"  # high, low, average
    default_units: str = "ppm"  # ppm, ppb, pct, opt
    convert_bdl: bool = True
    bdl_multiplier: float = 0.5
    element_configs: Dict[str, Dict[str, Any]] = field(default_factory=dict)


@dataclass
class UserContext:
    """
    Complete user context from /api/v1/me/ endpoint.

    This contains everything needed after login:
    - User's active company and project
    - Permission levels
    - Accessible companies and projects
    - Assay merge settings
    """
    user: UserInfo
    active_company: Optional[Company] = None
    active_project: Optional[Project] = None
    user_status: Optional[str] = None  # creator, owner, manager, admin, adder, viewer
    can_create: bool = False
    accessible_companies: List[Company] = field(default_factory=list)
    accessible_projects: List[Project] = field(default_factory=list)
    point_sample_types: List[PointSampleType] = field(default_factory=list)
    assay_merge_settings: Optional[AssayMergeSettings] = None

    @classmethod
    def from_api_response(cls, data: Dict[str, Any]) -> 'UserContext':
        """
        Create UserContext from API response.

        Args:
            data: Response from GET /api/v1/me/

        Returns:
            UserContext instance
        """
        # Parse user info
        user_data = data.get('user', {})
        user = UserInfo(
            user_id=0,  # ID not returned in /me/ endpoint
            username=user_data.get('email', ''),
            email=user_data.get('email', ''),
            first_name=user_data.get('first_name', ''),
            last_name=user_data.get('last_name', '')
        )

        # Parse active company
        active_company = None
        if data.get('active_company'):
            ac = data['active_company']
            active_company = Company(id=ac['id'], name=ac['name'])

        # Parse active project
        active_project = None
        if data.get('active_project'):
            ap = data['active_project']
            active_project = Project(
                id=ap['id'],
                name=ap['name'],
                company=ap.get('company', ''),
                crs=ap.get('crs', '4326'),
                proj4_string=ap.get('proj4_string')
            )

        # Parse accessible companies
        accessible_companies = []
        for c in data.get('accessible_companies', []):
            accessible_companies.append(Company(id=c['id'], name=c['name']))

        # Parse accessible projects
        accessible_projects = []
        for p in data.get('accessible_projects', []):
            accessible_projects.append(Project(
                id=p['id'],
                name=p['name'],
                company=p.get('company', ''),
                crs=p.get('crs', '4326')
            ))

        # Parse point sample types
        point_sample_types = []
        for pst in data.get('point_sample_types', []):
            point_sample_types.append(PointSampleType(
                id=pst['id'],
                name=pst['name']
            ))

        # Parse assay merge settings
        assay_settings = None
        if data.get('assay_merge_settings'):
            ams = data['assay_merge_settings']
            assay_settings = AssayMergeSettings(
                default_strategy=ams.get('default_strategy', 'high'),
                default_units=ams.get('default_units', 'ppm'),
                convert_bdl=ams.get('convert_bdl', True),
                bdl_multiplier=ams.get('bdl_multiplier', 0.5),
                element_configs=ams.get('element_configs', {})
            )

        return cls(
            user=user,
            active_company=active_company,
            active_project=active_project,
            user_status=data.get('user_status'),
            can_create=data.get('can_create', False),
            accessible_companies=accessible_companies,
            accessible_projects=accessible_projects,
            point_sample_types=point_sample_types,
            assay_merge_settings=assay_settings
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for storage/serialization."""
        result = {
            'user': {
                'email': self.user.email,
                'first_name': self.user.first_name,
                'last_name': self.user.last_name
            },
            'user_status': self.user_status,
            'can_create': self.can_create
        }

        if self.active_company:
            result['active_company'] = {
                'id': self.active_company.id,
                'name': self.active_company.name
            }

        if self.active_project:
            result['active_project'] = {
                'id': self.active_project.id,
                'name': self.active_project.name,
                'company': self.active_project.company,
                'crs': self.active_project.crs
            }

        result['accessible_companies'] = [
            {'id': c.id, 'name': c.name}
            for c in self.accessible_companies
        ]

        result['accessible_projects'] = [
            {'id': p.id, 'name': p.name, 'company': p.company, 'crs': p.crs}
            for p in self.accessible_projects
        ]

        result['point_sample_types'] = [
            {'id': pst.id, 'name': pst.name}
            for pst in self.point_sample_types
        ]

        if self.assay_merge_settings:
            result['assay_merge_settings'] = {
                'default_strategy': self.assay_merge_settings.default_strategy,
                'default_units': self.assay_merge_settings.default_units,
                'convert_bdl': self.assay_merge_settings.convert_bdl,
                'bdl_multiplier': self.assay_merge_settings.bdl_multiplier,
                'element_configs': self.assay_merge_settings.element_configs
            }

        return result


@dataclass
class AuthSession:
    """Active authentication session."""
    token: str
    user: UserInfo
    auth_config_id: Optional[str] = None
    user_context: Optional[UserContext] = None

    def is_valid(self) -> bool:
        """Check if session has required data."""
        return bool(self.token and self.user)

    def has_active_project(self) -> bool:
        """Check if user has an active project selected."""
        return (
            self.user_context is not None and
            self.user_context.active_project is not None
        )

    def get_active_project_id(self) -> Optional[int]:
        """Get the active project ID if set."""
        if self.has_active_project():
            return self.user_context.active_project.id
        return None

    def get_active_project_crs(self) -> str:
        """Get the active project's CRS, defaults to WGS84."""
        if self.has_active_project():
            return self.user_context.active_project.crs
        return "4326"

    def get_active_project_proj4(self) -> Optional[str]:
        """Get the active project's proj4 string for local grid CRS."""
        if self.has_active_project():
            return self.user_context.active_project.proj4_string
        return None