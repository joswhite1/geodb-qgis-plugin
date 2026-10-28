# -*- coding: utf-8 -*-
"""
Custom exceptions for GeodbIO plugin.
"""


class GeodbException(Exception):
    """Base exception for all plugin errors."""


class APIException(GeodbException):
    """Base exception for API-related errors."""

    def __init__(self, message: str, status_code: int = None, response_data: dict = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_data = response_data or {}


class AuthenticationError(APIException):
    """Raised when authentication fails."""


class APIPermissionError(APIException):
    """Raised when user lacks required permissions."""


class NetworkError(APIException):
    """Raised when network communication fails."""


class ServerError(APIException):
    """Raised when server returns 5xx error."""


class ValidationError(APIException):
    """Raised when request validation fails."""


class NewerVersionLive(APIException):
    """Publish refused (409 ``newer_version_live``): someone published a newer
    version of the layer after this copy was pulled. ``newer_version`` holds
    ``{id, name, published_by, published_on}``; the caller asks the user and
    may send again with ``replace_newer``."""

    @property
    def newer_version(self) -> dict:
        return (self.response_data or {}).get('newer_version') or {}


class DataException(GeodbException):
    """Base exception for data-related errors."""


class GeometryError(DataException):
    """Raised when geometry processing fails."""


class FieldMappingError(DataException):
    """Raised when field mapping fails."""


class LayerError(DataException):
    """Raised when layer operations fail."""


class ConfigException(GeodbException):
    """Raised when configuration is invalid."""
