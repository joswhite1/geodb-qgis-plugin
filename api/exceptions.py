# -*- coding: utf-8 -*-
"""
Custom exceptions for GeodbIO plugin.
"""


class GeodbException(Exception):
    """Base exception for all plugin errors."""
    pass


class APIException(GeodbException):
    """Base exception for API-related errors."""
    
    def __init__(self, message: str, status_code: int = None, response_data: dict = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_data = response_data or {}


class AuthenticationError(APIException):
    """Raised when authentication fails."""
    pass


class PermissionError(APIException):
    """Raised when user lacks required permissions."""
    pass


class NetworkError(APIException):
    """Raised when network communication fails."""
    pass


class ServerError(APIException):
    """Raised when server returns 5xx error."""
    pass


class ValidationError(APIException):
    """Raised when request validation fails."""
    pass


class DataException(GeodbException):
    """Base exception for data-related errors."""
    pass


class GeometryError(DataException):
    """Raised when geometry processing fails."""
    pass


class FieldMappingError(DataException):
    """Raised when field mapping fails."""
    pass


class LayerError(DataException):
    """Raised when layer operations fail."""
    pass


class ConfigException(GeodbException):
    """Raised when configuration is invalid."""
    pass