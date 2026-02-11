# -*- coding: utf-8 -*-
"""
Project and company data models.
"""
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Company:
    """Company information."""
    id: int
    name: str
    projects: List['Project']
    
    def __str__(self):
        return self.name


@dataclass
class Project:
    """Project information."""
    id: int
    name: str
    company_id: int
    company_name: str
    crs: str = "4326"  # EPSG code, defaults to WGS84

    def __str__(self):
        return f"{self.company_name} - {self.name}"


@dataclass
class Permission:
    """User permissions for a model type."""
    model_name: str  # e.g., 'LandHolding', 'DrillCollar'
    level: str  # 'admin', 'editor', 'viewer'
    
    @property
    def can_view(self) -> bool:
        return self.level in ['admin', 'editor', 'viewer']
    
    @property
    def can_edit(self) -> bool:
        return self.level in ['admin', 'editor']
    
    @property
    def can_admin(self) -> bool:
        return self.level == 'admin'