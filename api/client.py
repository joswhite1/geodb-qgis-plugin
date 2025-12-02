# -*- coding: utf-8 -*-
"""
HTTP client for geodb.io API v1 communication.

This client implements the RESTful API as documented in COMPLETE_API_REFERENCE.md
"""
import json
from typing import Dict, Any, Optional, Callable, List
from qgis.PyQt.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply
from qgis.PyQt.QtCore import QUrl, QByteArray, QEventLoop
from qgis.core import QgsNetworkAccessManager

from .exceptions import (
    NetworkError,
    AuthenticationError,
    PermissionError,
    ServerError,
    ValidationError,
    APIException
)
from ..utils.config import Config
from ..utils.logger import PluginLogger


class APIClient:
    """
    HTTP client for all API communication.
    Handles authentication, retries, and response parsing.
    """
    
    def __init__(self, config: Config, token: Optional[str] = None):
        """
        Initialize API client.
        
        Args:
            config: Configuration instance
            token: Optional authentication token
        """
        self.config = config
        self.token = token
        self.logger = PluginLogger.get_logger()
        self.network_manager = QgsNetworkAccessManager.instance()
    
    def set_token(self, token: str):
        """Set authentication token."""
        self.token = token
    
    def _make_request(
        self,
        method: str,
        url: str,
        data: Optional[Dict] = None,
        headers: Optional[Dict] = None,
        progress_callback: Optional[Callable[[int], None]] = None
    ) -> Dict[str, Any]:
        """
        Make HTTP request and return parsed response.
        
        Args:
            method: HTTP method (GET, POST, etc.)
            url: Request URL
            data: Optional request body data
            headers: Optional additional headers
            progress_callback: Optional callback for progress updates
            
        Returns:
            Parsed JSON response
            
        Raises:
            APIException: On request failure
        """
        self.logger.debug(f"{method} {url}")
        
        # Prepare headers
        request_headers = {
            'Content-Type': 'application/json'
        }
        
        # Add authentication token
        if self.token:
            request_headers['Authorization'] = f'Token {self.token}'
        
        # Add custom headers
        if headers:
            request_headers.update(headers)
        
        # Create request
        request = QNetworkRequest(QUrl(url))
        for key, value in request_headers.items():
            request.setRawHeader(QByteArray(key.encode()), QByteArray(value.encode()))
        
        # Prepare data
        request_data = None
        if data:
            json_data = json.dumps(data)
            # Log first PATCH request for debugging
            if method == 'PATCH':
                self.logger.info(f"PATCH data sample (first 500 chars): {json_data[:500]}")
            request_data = QByteArray(json_data.encode('utf-8'))
        
        # Execute request with retry logic
        max_retries = self.config.get('api.retry_attempts', 3)
        last_error = None
        
        for attempt in range(max_retries):
            try:
                response = self._execute_request(method, request, request_data)
                return response
            except NetworkError as e:
                last_error = e
                self.logger.warning(f"Request attempt {attempt + 1} failed: {e}")
                if attempt < max_retries - 1:
                    continue
                else:
                    raise
            except APIException:
                # Don't retry on authentication/validation errors
                raise
        
        raise last_error
    
    def _execute_request(
        self,
        method: str,
        request: QNetworkRequest,
        data: Optional[QByteArray]
    ) -> Dict[str, Any]:
        """Execute single HTTP request synchronously."""
        
        # Create event loop for synchronous behavior
        loop = QEventLoop()
        
        # Make request based on method
        if method == 'GET':
            reply = self.network_manager.get(request)
        elif method == 'POST':
            reply = self.network_manager.post(request, data or QByteArray())
        elif method == 'PUT':
            reply = self.network_manager.put(request, data or QByteArray())
        elif method == 'PATCH':
            # PATCH is used for partial updates
            reply = self.network_manager.sendCustomRequest(
                request, QByteArray(b'PATCH'), data or QByteArray()
            )
        elif method == 'DELETE':
            reply = self.network_manager.deleteResource(request)
        else:
            raise ValueError(f"Unsupported HTTP method: {method}")
        
        # Wait for completion
        reply.finished.connect(loop.quit)
        loop.exec_()
        
        # Process response
        return self._process_response(reply)
    
    def _process_response(self, reply: QNetworkReply) -> Dict[str, Any]:
        """
        Process network reply and handle errors.
        
        Args:
            reply: QNetworkReply object
            
        Returns:
            Parsed JSON response
            
        Raises:
            APIException: On error response
        """
        status_code = reply.attribute(QNetworkRequest.HttpStatusCodeAttribute)
        response_data = bytes(reply.readAll()).decode('utf-8')
        
        # Check for network errors
        if reply.error() != QNetworkReply.NoError:
            error_msg = reply.errorString()
            self.logger.error(f"Network error: {error_msg}")
            raise NetworkError(f"Network error: {error_msg}", status_code=status_code)
        
        # Parse JSON response
        try:
            parsed_data = json.loads(response_data) if response_data else {}
        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to parse JSON: {e}")
            raise APIException(f"Invalid JSON response: {e}", status_code=status_code)
        
        # Handle HTTP errors
        if status_code >= 400:
            error_msg = parsed_data.get('error') or parsed_data.get('detail') or 'Unknown error'
            
            if status_code == 401:
                raise AuthenticationError(error_msg, status_code, parsed_data)
            elif status_code == 403:
                raise PermissionError(error_msg, status_code, parsed_data)
            elif status_code == 400:
                raise ValidationError(error_msg, status_code, parsed_data)
            elif status_code >= 500:
                raise ServerError(error_msg, status_code, parsed_data)
            else:
                raise APIException(error_msg, status_code, parsed_data)
        
        reply.deleteLater()
        return parsed_data
    
    # =========================================================================
    # Authentication Endpoints
    # =========================================================================

    def check_token(self) -> Dict[str, Any]:
        """
        Check if current token is valid.

        Returns:
            Dict with expiration_time, remaining_time, companies list
        """
        url = self.config.endpoints['check_token']
        return self._make_request('POST', url)

    def login(self, username: str, password: str) -> Dict[str, Any]:
        """
        Authenticate user and get Knox token.

        Args:
            username: User's email/username
            password: User's password

        Returns:
            Dict with 'token' and 'user' keys
        """
        url = self.config.endpoints['login']
        self.logger.info(f"Login attempt - Base URL: {self.config.base_url}")
        self.logger.info(f"Login attempt - Full URL: {url}")
        data = {'username': username, 'password': password}
        return self._make_request('POST', url, data=data)

    def logout(self) -> None:
        """Logout and invalidate current token."""
        url = self.config.endpoints['logout']
        self._make_request('POST', url)

    # =========================================================================
    # User Context Endpoints (Critical - call after login!)
    # =========================================================================

    def get_user_context(self) -> Dict[str, Any]:
        """
        Get current user context including active company, project, and permissions.

        IMPORTANT: Must call this immediately after login to get:
        - Active company and project
        - User permissions (user_status, can_create)
        - Accessible companies and projects list
        - Assay merge settings

        Returns:
            Dict with user context data
        """
        url = self.config.endpoints['me']
        return self._make_request('GET', url)

    def set_active_company(self, company_id: int) -> Dict[str, Any]:
        """
        Set the user's active company.

        Args:
            company_id: ID of the company to make active

        Returns:
            Updated user context
        """
        url = self.config.endpoints['set_active_company']
        return self._make_request('POST', url, data={'company_id': company_id})

    def set_active_project(self, project_id: int) -> Dict[str, Any]:
        """
        Set the user's active project.

        Args:
            project_id: ID of the project to make active

        Returns:
            Updated user context (company is auto-set to project's parent)
        """
        url = self.config.endpoints['set_active_project']
        return self._make_request('POST', url, data={'project_id': project_id})

    def set_assay_merge_settings(
        self,
        project_id: int,
        settings: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Update assay merge settings for a project.

        Args:
            project_id: Project ID (required)
            settings: Dict with optional keys:
                - default_strategy: 'high', 'low', or 'average'
                - default_units: 'ppm', 'ppb', 'pct', or 'opt'
                - convert_bdl: bool
                - bdl_multiplier: float 0.0-1.0
                - element_configs: dict of per-element settings

        Returns:
            Updated user context
        """
        url = self.config.endpoints['set_assay_merge_settings']
        data = {'project_id': project_id, **settings}
        return self._make_request('POST', url, data=data)

    # =========================================================================
    # Projects Endpoints
    # =========================================================================

    def get_projects(self, company_id: Optional[int] = None) -> Dict[str, Any]:
        """
        Get list of projects.

        Args:
            company_id: Optional filter by company

        Returns:
            Paginated list of projects
        """
        url = self.config.endpoints['projects']
        if company_id:
            url = f"{url}?company_id={company_id}"
        return self._make_request('GET', url)

    # =========================================================================
    # RESTful Data Endpoints
    # =========================================================================

    def get_model_data(
        self,
        model_name: str,
        project_id: int,
        params: Optional[Dict[str, Any]] = None,
        progress_callback: Optional[Callable[[int], None]] = None
    ) -> Dict[str, Any]:
        """
        Get all records of a model type for a project.

        Args:
            model_name: Model name (e.g., 'DrillCollar', 'LandHolding')
            project_id: Project ID to filter by
            params: Optional additional query parameters
            progress_callback: Optional progress callback

        Returns:
            Paginated API response with 'results' list
        """
        endpoint = self.config.get_model_endpoint(model_name)
        if not endpoint:
            raise ValueError(f"Unknown model: {model_name}")

        url = f"{endpoint}?project_id={project_id}"

        if params:
            for key, value in params.items():
                url = f"{url}&{key}={value}"

        return self._make_request('GET', url, progress_callback=progress_callback)

    def get_all_paginated(
        self,
        model_name: str,
        project_id: int,
        params: Optional[Dict[str, Any]] = None,
        progress_callback: Optional[Callable[[int], None]] = None
    ) -> List[Dict[str, Any]]:
        """
        Get ALL records of a model type, handling pagination automatically.

        Args:
            model_name: Model name (e.g., 'DrillCollar', 'LandHolding')
            project_id: Project ID to filter by
            params: Optional additional query parameters
            progress_callback: Optional progress callback

        Returns:
            Complete list of all records
        """
        all_results = []
        endpoint = self.config.get_model_endpoint(model_name)
        if not endpoint:
            raise ValueError(f"Unknown model: {model_name}")

        url = f"{endpoint}?project_id={project_id}"
        if params:
            for key, value in params.items():
                url = f"{url}&{key}={value}"

        while url:
            response = self._make_request('GET', url, progress_callback=progress_callback)

            # Handle both paginated (dict with 'results') and non-paginated (list) responses
            if isinstance(response, list):
                # Non-paginated response - direct list of results
                all_results.extend(response)
                url = None  # No pagination
            else:
                # Paginated response - dict with 'results', 'next', 'count'
                results = response.get('results', [])
                all_results.extend(results)

                # Get next page URL
                url = response.get('next')

                if progress_callback and 'count' in response:
                    progress = int((len(all_results) / response['count']) * 100)
                    progress_callback(progress)

        return all_results

    def create_record(
        self,
        model_name: str,
        data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Create a new record.

        Args:
            model_name: Model name (e.g., 'DrillCollar', 'LandHolding')
            data: Record data

        Returns:
            Created record
        """
        endpoint = self.config.get_model_endpoint(model_name)
        if not endpoint:
            raise ValueError(f"Unknown model: {model_name}")

        return self._make_request('POST', endpoint, data=data)

    def update_record(
        self,
        model_name: str,
        record_id: int,
        data: Dict[str, Any],
        partial: bool = True
    ) -> Dict[str, Any]:
        """
        Update an existing record.

        Args:
            model_name: Model name
            record_id: Record ID
            data: Updated data
            partial: If True, use PATCH (partial update), else PUT (full replace)

        Returns:
            Updated record
        """
        endpoint = self.config.get_model_endpoint(model_name)
        if not endpoint:
            raise ValueError(f"Unknown model: {model_name}")

        url = f"{endpoint}{record_id}/"
        method = 'PATCH' if partial else 'PUT'
        return self._make_request(method, url, data=data)

    def delete_record(self, model_name: str, record_id: int) -> None:
        """
        Delete a record (soft delete).

        Args:
            model_name: Model name
            record_id: Record ID
        """
        endpoint = self.config.get_model_endpoint(model_name)
        if not endpoint:
            raise ValueError(f"Unknown model: {model_name}")

        url = f"{endpoint}{record_id}/"
        self._make_request('DELETE', url)

    # =========================================================================
    # Convenience methods for specific models
    # =========================================================================

    def get_drill_collars(
        self,
        project_id: int,
        merge_assays: bool = False
    ) -> List[Dict[str, Any]]:
        """Get all drill collars for a project."""
        params = {'merge_assays': 'true'} if merge_assays else None
        return self.get_all_paginated('DrillCollar', project_id, params=params)

    def get_drill_samples(
        self,
        project_id: int,
        merge_assays: bool = True
    ) -> List[Dict[str, Any]]:
        """Get all drill samples for a project with optional assay merging."""
        params = {'merge_assays': 'true'} if merge_assays else None
        return self.get_all_paginated('DrillSample', project_id, params=params)

    def get_landholdings(self, project_id: int) -> List[Dict[str, Any]]:
        """Get all land holdings for a project."""
        return self.get_all_paginated('LandHolding', project_id)

    def get_landholding_types(self, company_id: int) -> List[Dict[str, Any]]:
        """
        Get land holding types for a company.

        Args:
            company_id: Company ID to filter types

        Returns:
            List of land holding type dictionaries
        """
        url = self.config.endpoints['landholding_types']
        url = f"{url}?company_id={company_id}"

        response = self._make_request('GET', url)
        # Handle both paginated (dict with 'results') and non-paginated (list) responses
        if isinstance(response, list):
            return response
        return response.get('results', [])

    def find_record_by_natural_key(
        self,
        model_name: str,
        natural_key: Dict[str, Any],
        project_id: int
    ) -> Optional[Dict[str, Any]]:
        """
        Find a record by its natural key fields.

        Args:
            model_name: Model name
            natural_key: Dict with natural key field values (e.g., {'name': 'DDH-001', 'project': 'MyProject'})
            project_id: Project ID

        Returns:
            First matching record, or None if not found
        """
        endpoint = self.config.get_model_endpoint(model_name)
        if not endpoint:
            raise ValueError(f"Unknown model: {model_name}")

        # Build query params
        params = {'project_id': project_id}
        params.update(natural_key)

        # Build URL with query string
        query_string = '&'.join([f"{k}={v}" for k, v in params.items()])
        url = f"{endpoint}?{query_string}"

        response = self._make_request('GET', url)
        # Handle both paginated (dict with 'results') and non-paginated (list) responses
        if isinstance(response, list):
            results = response
        else:
            results = response.get('results', [])

        return results[0] if results else None

    def get_point_samples(
        self,
        project_id: int,
        merge_assays: bool = True
    ) -> List[Dict[str, Any]]:
        """Get all point samples for a project."""
        params = {'merge_assays': 'true'} if merge_assays else None
        return self.get_all_paginated('PointSample', project_id, params=params)

    # =========================================================================
    # Assay Range Configurations
    # =========================================================================

    def get_assay_range_configurations(
        self,
        project_id: int,
        is_active: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Get color range configurations for assay visualization.

        Args:
            project_id: Project ID
            is_active: Filter to only active configurations

        Returns:
            List of assay range configurations
        """
        url = self.config.endpoints['assay_range_configurations']
        url = f"{url}?project_id={project_id}"
        if is_active:
            url = f"{url}&is_active=true"

        response = self._make_request('GET', url)
        # Handle both paginated (dict with 'results') and non-paginated (list) responses
        if isinstance(response, list):
            return response
        return response.get('results', [])

    def create_assay_range_configuration(
        self,
        data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Create a new assay range configuration.

        Args:
            data: Configuration data including name, project, element, ranges, etc.

        Returns:
            Created configuration
        """
        url = self.config.endpoints['assay_range_configurations']
        return self._make_request('POST', url, data=data)

    def get_assay_merge_settings(
        self,
        project_id: Optional[int] = None,
        company_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Get assay merge settings for a project or company.

        Args:
            project_id: Optional project ID filter
            company_id: Optional company ID filter

        Returns:
            List of assay merge settings
        """
        url = self.config.endpoints['assay_merge_settings']
        params = []

        if project_id:
            params.append(f"project_id={project_id}")
        if company_id:
            params.append(f"company_id={company_id}")

        if params:
            url = f"{url}?{'&'.join(params)}"

        response = self._make_request('GET', url)
        # Handle both paginated (dict with 'results') and non-paginated (list) responses
        if isinstance(response, list):
            return response
        return response.get('results', [])

    # =========================================================================
    # Legacy compatibility methods (deprecated)
    # =========================================================================

    def pull_data(
        self,
        project_id: int,
        model_name: str,
        last_sync: Optional[str] = None,
        progress_callback: Optional[Callable[[int], None]] = None
    ) -> Dict[str, Any]:
        """
        DEPRECATED: Use get_model_data() or get_all_paginated() instead.

        Pull data from server using RESTful endpoints.
        """
        self.logger.warning(
            "pull_data() is deprecated. Use get_model_data() or get_all_paginated()."
        )
        results = self.get_all_paginated(model_name, project_id, progress_callback=progress_callback)
        return {'features': results, 'count': len(results)}

    def push_data(
        self,
        project_id: int,
        model_name: str,
        features: list,
        progress_callback: Optional[Callable[[int], None]] = None
    ) -> Dict[str, Any]:
        """
        DEPRECATED: Use create_record() or update_record() instead.

        Push data to server - creates or updates records individually.
        """
        self.logger.warning(
            "push_data() is deprecated. Use create_record() or update_record()."
        )
        results = []
        errors = []

        for i, feature in enumerate(features):
            try:
                if 'id' in feature and feature['id']:
                    # Update existing record
                    result = self.update_record(model_name, feature['id'], feature)
                else:
                    # Create new record
                    result = self.create_record(model_name, feature)
                results.append(result)
            except APIException as e:
                errors.append({'feature': feature, 'error': str(e)})

            if progress_callback:
                progress = int(((i + 1) / len(features)) * 100)
                progress_callback(progress)

        return {
            'success': len(results),
            'errors': errors,
            'results': results
        }