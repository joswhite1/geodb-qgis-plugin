# -*- coding: utf-8 -*-
"""
API response wrapper.
"""
from typing import Any, Optional, Dict
from dataclasses import dataclass


@dataclass
class APIResponse:
    """Standardized API response wrapper."""
    success: bool
    data: Any = None
    error: Optional[str] = None
    status_code: Optional[int] = None
    headers: Optional[Dict[str, str]] = None
    
    @classmethod
    def from_success(cls, data: Any, status_code: int = 200, headers: Dict = None):
        """Create success response."""
        return cls(success=True, data=data, status_code=status_code, headers=headers)
    
    @classmethod
    def from_error(cls, error: str, status_code: int = None, data: Any = None):
        """Create error response."""
        return cls(success=False, error=error, status_code=status_code, data=data)