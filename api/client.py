# -*- coding: utf-8 -*-
"""
HTTP client for geodb.io API v1 communication.

This client implements the RESTful API as documented in COMPLETE_API_REFERENCE.md

IMPORTANT: This client uses QgsBlockingNetworkRequest instead of QEventLoop
to avoid heap corruption crashes. QEventLoop.exec() processes all Qt events
which can cause reentrancy issues when combined with QApplication.processEvents()
calls elsewhere in the codebase.
"""
import json
from typing import Dict, Any, Optional, Callable, List
from urllib.parse import urlparse, urlencode
from qgis.PyQt.QtNetwork import QNetworkRequest, QNetworkReply
from qgis.PyQt.QtCore import QUrl, QByteArray
from qgis.core import QgsNetworkAccessManager, QgsBlockingNetworkRequest

from .exceptions import (
    NetworkError,
    AuthenticationError,
    APIPermissionError,
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

    def get(self, endpoint: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Convenience method for GET requests to API endpoints.

        Args:
            endpoint: API path relative to base URL (e.g., 'custom-field-schemas/for_model/')
            params: Optional query parameters

        Returns:
            Parsed JSON response
        """
        base_url = self.config.base_url.rstrip('/')
        url = f"{base_url}/{endpoint.lstrip('/')}"
        if params:
            url = f"{url}?{urlencode(params)}"
        return self._make_request('GET', url)

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

        # Prepare data (only needs to be done once)
        request_data = None
        if data:
            json_data = json.dumps(data)
            request_data = QByteArray(json_data.encode('utf-8'))

        # Long-running endpoints (e.g. claims/preview-layers/ for 700+ claims) can
        # take several minutes on the server. The ingress allows up to 300s; mirror
        # that on the client so QGIS doesn't abort the read mid-response and leave
        # the plugin with a partial body that fails to JSON-decode.
        transfer_timeout_ms = self.config.get('api.transfer_timeout_ms', 300000)

        # Helper to create fresh request (QNetworkRequest may be modified by QgsBlockingNetworkRequest)
        def create_request():
            qurl = QUrl(url)
            req = QNetworkRequest(qurl)
            for key, value in request_headers.items():
                req.setRawHeader(QByteArray(key.encode()), QByteArray(value.encode()))
            try:
                req.setTransferTimeout(transfer_timeout_ms)
            except (AttributeError, TypeError):
                pass  # Older Qt builds lack setTransferTimeout; QGIS network manager default applies.
            return req

        # Execute request with retry logic
        max_retries = self.config.get('api.retry_attempts', 3)
        last_error = None

        for attempt in range(max_retries):
            try:
                # Create fresh request for each attempt
                request = create_request()
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
        """
        Execute single HTTP request synchronously using QgsBlockingNetworkRequest.

        This method uses QgsBlockingNetworkRequest instead of QEventLoop to avoid
        heap corruption crashes. QgsBlockingNetworkRequest is thread-safe and
        doesn't process other Qt events while waiting, preventing reentrancy issues.
        """
        blocking_request = QgsBlockingNetworkRequest()

        # Execute request based on method
        if method == 'GET':
            error_code = blocking_request.get(request, forceRefresh=True)
        elif method == 'POST':
            error_code = blocking_request.post(request, data or QByteArray(), forceRefresh=True)
        elif method == 'PUT':
            error_code = blocking_request.put(request, data or QByteArray())
        elif method == 'DELETE':
            error_code = blocking_request.deleteResource(request)
        elif method == 'PATCH':
            # QgsBlockingNetworkRequest doesn't have native PATCH support
            # Use HEAD to check, then fall back to custom implementation
            # For now, we'll use a workaround with sendCustomRequest via QNetworkAccessManager
            return self._execute_patch_request(request, data)
        else:
            raise ValueError(f"Unsupported HTTP method: {method}")

        # Check for network-level errors
        if error_code != QgsBlockingNetworkRequest.NoError:
            error_msg = blocking_request.errorMessage()
            self.logger.error(f"Network error ({error_code}): {error_msg}")
            raise NetworkError(f"Network error: {error_msg}")

        # Process response
        return self._process_blocking_response(blocking_request.reply())

    def _execute_patch_request(
        self,
        request: QNetworkRequest,
        data: Optional[QByteArray]
    ) -> Dict[str, Any]:
        """
        Execute PATCH request using QNetworkAccessManager with minimal event processing.

        QgsBlockingNetworkRequest doesn't support PATCH natively, so we use
        QNetworkAccessManager.sendCustomRequest() with a simple wait loop.
        This is only used for PATCH requests (partial updates).
        """
        from qgis.PyQt.QtCore import QEventLoop, QTimer

        reply = self.network_manager.sendCustomRequest(
            request, QByteArray(b'PATCH'), data or QByteArray()
        )

        # Use a simple blocking wait with timeout instead of event loop
        # This minimizes the window for reentrancy issues
        loop = QEventLoop()
        reply.finished.connect(loop.quit)

        # Add timeout to prevent infinite blocking
        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(loop.quit)
        timer.start(30000)  # 30 second timeout

        if not reply.isFinished():
            loop.exec()

        timer.stop()

        if not reply.isFinished():
            reply.abort()
            reply.deleteLater()
            raise NetworkError("PATCH request timed out")

        return self._process_response(reply)

    def _process_blocking_response(self, reply) -> Dict[str, Any]:
        """
        Process QgsNetworkReplyContent from QgsBlockingNetworkRequest.

        Args:
            reply: QgsNetworkReplyContent object (different from QNetworkReply)

        Returns:
            Parsed JSON response

        Raises:
            APIException: On error response
        """
        status_code = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
        response_data = bytes(reply.content()).decode('utf-8', errors='replace')

        # Parse JSON response first (even for errors, to get validation messages).
        # On decode failure, surface a real error instead of returning {} silently —
        # otherwise upstream callers see "empty response" with no diagnostic context.
        # Truncated bodies (e.g. ingress timeout mid-response) and non-JSON error
        # pages (HTML 502/504 from upstream proxies) both end up here.
        parsed_data = {}
        if response_data:
            try:
                parsed_data = json.loads(response_data)
            except json.JSONDecodeError as exc:
                snippet = response_data[:500].replace('\n', ' ')
                self.logger.error(
                    f"JSON decode failed: status={status_code} "
                    f"len={len(response_data)} err={exc} body[:500]={snippet!r}"
                )
                # If the server reported success but the body is unparseable, the
                # response was almost certainly truncated by an upstream timeout.
                if not status_code or status_code < 400:
                    raise NetworkError(
                        f"Server response was unparseable "
                        f"(status={status_code}, {len(response_data)} bytes received). "
                        f"Likely truncated by a network timeout — try again or "
                        f"reduce the request size."
                    )
                # For error status codes, fall through to the HTTP error branch
                # below using parsed_data={} (the body wasn't JSON anyway).

        # Check for HTTP errors
        if status_code and status_code >= 400:
            error_msg = parsed_data.get('error') or parsed_data.get('detail') or f'HTTP {status_code} error'

            # For 400 errors, include full validation details in the message
            if status_code == 400:
                # DRF returns field errors as dict: {"field_name": ["error message"]}
                validation_details = []
                for field, errors in parsed_data.items():
                    if field not in ('error', 'detail'):
                        if isinstance(errors, list):
                            validation_details.append(f"{field}: {', '.join(str(e) for e in errors)}")
                        else:
                            validation_details.append(f"{field}: {errors}")
                if validation_details:
                    error_msg = f"Validation failed - {'; '.join(validation_details)}"
                self.logger.error(f"[API 400] Validation error: {error_msg}")
                raise ValidationError(error_msg, status_code, parsed_data)
            elif status_code == 401:
                raise AuthenticationError(error_msg, status_code, parsed_data)
            elif status_code == 403:
                raise APIPermissionError(error_msg, status_code, parsed_data)
            elif status_code >= 500:
                raise ServerError(error_msg, status_code, parsed_data)
            else:
                raise APIException(error_msg, status_code, parsed_data)

        return parsed_data

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
        try:
            status_code = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            response_data = bytes(reply.readAll()).decode('utf-8')

            # Parse JSON response first (even for errors, to get validation messages)
            parsed_data = {}
            try:
                parsed_data = json.loads(response_data) if response_data else {}
            except json.JSONDecodeError:
                pass  # Will handle below

            # Check for network errors - but for HTTP errors (4xx, 5xx) process normally
            # QNetworkReply reports 400/500 as errors, but we want to parse their response
            if reply.error() != QNetworkReply.NoError:
                # If we have a valid HTTP status code, handle as HTTP error below
                if status_code and status_code >= 400:
                    pass  # Fall through to HTTP error handling
                else:
                    # True network error (connection refused, timeout, etc.)
                    error_msg = reply.errorString()
                    self.logger.error(f"Network error: {error_msg}")
                    self.logger.error(f"Response body: {response_data}")
                    raise NetworkError(f"Network error: {error_msg}", status_code=status_code)

            # Handle HTTP errors
            if status_code and status_code >= 400:
                error_msg = parsed_data.get('error') or parsed_data.get('detail') or f'HTTP {status_code} error'

                # For 400 errors, include full validation details in the message
                if status_code == 400:
                    # DRF returns field errors as dict: {"field_name": ["error message"]}
                    validation_details = []
                    for field, errors in parsed_data.items():
                        if field not in ('error', 'detail'):
                            if isinstance(errors, list):
                                validation_details.append(f"{field}: {', '.join(str(e) for e in errors)}")
                            else:
                                validation_details.append(f"{field}: {errors}")
                    if validation_details:
                        error_msg = f"Validation failed - {'; '.join(validation_details)}"
                    self.logger.error(f"[API 400] Validation error: {error_msg}")
                    self.logger.error(f"[API 400] Full response: {parsed_data}")
                    raise ValidationError(error_msg, status_code, parsed_data)
                elif status_code == 401:
                    raise AuthenticationError(error_msg, status_code, parsed_data)
                elif status_code == 403:
                    raise APIPermissionError(error_msg, status_code, parsed_data)
                elif status_code >= 500:
                    raise ServerError(error_msg, status_code, parsed_data)
                else:
                    raise APIException(error_msg, status_code, parsed_data)

            return parsed_data
        finally:
            reply.deleteLater()

    # =========================================================================
    # Multipart File Upload
    # =========================================================================

    def upload_file(
        self,
        url: str,
        file_path: str,
        fields: Dict[str, str],
        file_field_name: str = 'file'
    ) -> Dict[str, Any]:
        """
        Upload a file via multipart/form-data POST.

        Uses QgsBlockingNetworkRequest with a manually constructed multipart
        body (QByteArray), since QgsBlockingNetworkRequest.post() does not
        accept QHttpMultiPart directly.

        Args:
            url: Full URL to POST to
            file_path: Local path to the file to upload
            fields: Dict of form fields (strings) to include alongside the file
            file_field_name: Name of the file field in the form (default 'file')

        Returns:
            Parsed JSON response

        Raises:
            APIException: On request failure
        """
        import os
        import uuid

        self.logger.info(f"Uploading file: {file_path} to {url}")

        # Generate a unique boundary string
        boundary = f'----GeodbBoundary{uuid.uuid4().hex}'
        boundary_bytes = boundary.encode('utf-8')

        # Build multipart body manually
        body_parts = []

        # Add text fields
        for key, value in fields.items():
            body_parts.append(b'--' + boundary_bytes + b'\r\n')
            body_parts.append(
                f'Content-Disposition: form-data; name="{key}"\r\n'.encode('utf-8')
            )
            body_parts.append(b'\r\n')
            body_parts.append(str(value).encode('utf-8'))
            body_parts.append(b'\r\n')

        # Add file part
        file_name = os.path.basename(file_path)
        body_parts.append(b'--' + boundary_bytes + b'\r\n')
        body_parts.append(
            f'Content-Disposition: form-data; name="{file_field_name}"; '
            f'filename="{file_name}"\r\n'.encode('utf-8')
        )
        body_parts.append(b'Content-Type: application/octet-stream\r\n')
        body_parts.append(b'\r\n')

        with open(file_path, 'rb') as f:
            body_parts.append(f.read())
        body_parts.append(b'\r\n')

        # Closing boundary
        body_parts.append(b'--' + boundary_bytes + b'--\r\n')

        body_data = b''.join(body_parts)

        # Build request with multipart content type
        qurl = QUrl(url)
        request = QNetworkRequest(qurl)
        request.setRawHeader(
            QByteArray(b'Content-Type'),
            QByteArray(f'multipart/form-data; boundary={boundary}'.encode())
        )
        if self.token:
            request.setRawHeader(
                QByteArray(b'Authorization'),
                QByteArray(f'Token {self.token}'.encode())
            )

        # Execute with QgsBlockingNetworkRequest
        blocking_request = QgsBlockingNetworkRequest()
        error_code = blocking_request.post(
            request, QByteArray(body_data), forceRefresh=True
        )

        if error_code != QgsBlockingNetworkRequest.NoError:
            error_msg = blocking_request.errorMessage()
            # Check if there's a response body with validation details
            try:
                reply = blocking_request.reply()
                response_data = bytes(reply.content()).decode('utf-8')
                if response_data:
                    return self._process_blocking_response(reply)
            except Exception:
                pass
            self.logger.error(f"Upload error ({error_code}): {error_msg}")
            raise NetworkError(f"Upload failed: {error_msg}")

        return self._process_blocking_response(blocking_request.reply())

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

        # Clear any existing token before login - sending a stale/invalid token
        # to the login endpoint causes Knox to return "Invalid token." error
        old_token = self.token
        self.token = None

        try:
            data = {'username': username, 'password': password}
            return self._make_request('POST', url, data=data)
        except Exception:
            # Restore the old token if login fails (allows retry without losing state)
            self.token = old_token
            raise

    def logout(self) -> None:
        """Logout and invalidate current token."""
        url = self.config.endpoints['logout']
        self._make_request('POST', url)

    # =========================================================================
    # Two-Factor Authentication (2FA) Endpoints
    # =========================================================================

    def verify_2fa(self, session_token: str, code: str) -> Dict[str, Any]:
        """
        Verify 2FA code and get Knox token.

        Args:
            session_token: Temporary session token from login response
            code: 6-digit TOTP code or backup code

        Returns:
            Dict with 'token' and 'expiry' on success

        Raises:
            AuthenticationError: If code is invalid or too many attempts
        """
        url = self.config.endpoints['verify_2fa']
        data = {'session_token': session_token, 'code': code}
        return self._make_request('POST', url, data=data)

    def request_2fa_recovery(self, session_token: str) -> Dict[str, Any]:
        """
        Request 2FA recovery code to be sent to recovery email.

        Args:
            session_token: Temporary session token from login response

        Returns:
            Dict with 'message' and 'recovery_email_masked'

        Raises:
            APIException: If no recovery email or rate limited
        """
        url = self.config.endpoints['request_2fa_recovery']
        data = {'session_token': session_token}
        return self._make_request('POST', url, data=data)

    def verify_2fa_recovery(self, session_token: str, recovery_code: str) -> Dict[str, Any]:
        """
        Verify 2FA recovery code and get Knox token.

        Args:
            session_token: Temporary session token from login response
            recovery_code: 6-digit recovery code sent to email

        Returns:
            Dict with 'token' and 'expiry' on success

        Raises:
            AuthenticationError: If recovery code is invalid
        """
        url = self.config.endpoints['verify_2fa_recovery']
        data = {'session_token': session_token, 'recovery_code': recovery_code}
        return self._make_request('POST', url, data=data)

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

    # =========================================================================
    # Projects Endpoints
    # =========================================================================

    # =========================================================================
    # Security helpers
    # =========================================================================

    def _validate_pagination_url(self, next_url: str) -> bool:
        """Ensure pagination URL belongs to the same API origin.

        Prevents a compromised or MITM'd response from redirecting the client
        to send auth tokens to an attacker-controlled server.
        """
        if not next_url:
            return False
        base = urlparse(self.config.base_url)
        target = urlparse(next_url)
        return (target.scheme == base.scheme and
                target.netloc == base.netloc)

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

        params_dict = {'project_id': project_id}
        if params:
            params_dict.update(params)
        url = f"{endpoint}?{urlencode(params_dict)}"

        return self._make_request('GET', url, progress_callback=progress_callback)

    def get_all_paginated(
        self,
        model_name: str,
        project_id: int,
        params: Optional[Dict[str, Any]] = None,
        progress_callback: Optional[Callable[[int], None]] = None,
        include_deletion_metadata: bool = True
    ) -> Dict[str, Any]:
        """
        Get ALL records of a model type, handling pagination automatically.

        Also captures deletion sync metadata from the API response:
        - deleted_ids: IDs of soft-deleted records that should be removed locally
        - sync_timestamp: Timestamp to use for next incremental sync
        - deleted_since_applied: The deleted_since filter that was applied (if any)

        Args:
            model_name: Model name (e.g., 'DrillCollar', 'LandHolding')
            project_id: Project ID to filter by
            params: Optional additional query parameters
            progress_callback: Optional progress callback
            include_deletion_metadata: If True, return dict with 'results' and deletion
                metadata. If False, return just the list (backward compatibility).

        Returns:
            If include_deletion_metadata=True (default):
                Dict with 'results', 'deleted_ids', 'sync_timestamp', 'deleted_since_applied'
            If include_deletion_metadata=False:
                List of all records (backward compatible)
        """
        all_results = []
        endpoint = self.config.get_model_endpoint(model_name)
        if not endpoint:
            raise ValueError(f"Unknown model: {model_name}")

        params_dict = {'project_id': project_id, 'limit': 1000}
        if params:
            params_dict.update(params)
        url = f"{endpoint}?{urlencode(params_dict)}"

        self.logger.info(f"get_all_paginated initial URL: {url}")

        # Deletion sync metadata (captured from first page response)
        deleted_ids = []
        sync_timestamp = None
        deleted_since_applied = None
        first_page = True

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

                # Capture deletion sync metadata from first page only
                # (these values are consistent across all pages)
                if first_page:
                    deleted_ids = response.get('deleted_ids', [])
                    sync_timestamp = response.get('sync_timestamp')
                    deleted_since_applied = response.get('deleted_since_applied')

                    if deleted_ids:
                        self.logger.info(
                            f"Deletion sync: {len(deleted_ids)} deleted IDs received "
                            f"(deleted_since_applied={deleted_since_applied})"
                        )
                    first_page = False

                # Get next page URL (validate origin to prevent token exfiltration)
                next_url = response.get('next')
                if next_url and self._validate_pagination_url(next_url):
                    url = next_url
                else:
                    if next_url:
                        self.logger.warning("Rejected pagination URL from different origin")
                    url = None

                if progress_callback and response.get('count'):
                    progress = int((len(all_results) / response['count']) * 100)
                    progress_callback(progress)

        # Return based on include_deletion_metadata flag
        if include_deletion_metadata:
            return {
                'results': all_results,
                'deleted_ids': deleted_ids,
                'sync_timestamp': sync_timestamp,
                'deleted_since_applied': deleted_since_applied
            }
        else:
            # Backward compatibility - just return the list
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
        Update an existing record by ID.

        Note: Consider using upsert_record() instead, which uses natural key
        lookup and doesn't require tracking server IDs.

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

    def upsert_record(
        self,
        model_name: str,
        data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Create or update a record using natural key lookup.

        The server looks up the record by its natural key (e.g., name + project
        for DrillPad). If found, it updates the existing record. If not found,
        it creates a new record.

        This is the preferred method for sync operations as it doesn't require
        tracking server IDs on the client side.

        Args:
            model_name: Model name (e.g., 'DrillCollar', 'LandHolding')
            data: Record data (must include natural key fields)

        Returns:
            Dict with record data and 'status' field ('created' or 'updated')
        """
        endpoint = self.config.get_model_endpoint(model_name)
        if not endpoint:
            raise ValueError(f"Unknown model: {model_name}")

        return self._make_request('POST', endpoint, data=data)

    def bulk_upsert_records(
        self,
        model_name: str,
        records: List[Dict[str, Any]],
        cleanup_orphans: bool = False
    ) -> Dict[str, Any]:
        """
        Bulk create or update multiple records in a single request.

        Uses the /bulk/ endpoint which accepts an array of objects and performs
        upsert operations in a single database transaction. Much faster than
        individual requests for large datasets.

        Args:
            model_name: Model name (e.g., 'PointSample', 'DrillCollar')
            records: List of record data dicts (each must include natural key fields)
            cleanup_orphans: For models that support it (currently ClaimStake), opt
                into post-upsert orphan cleanup. The server hard-deletes any
                Planned-status records in the same project/package whose natural
                keys are NOT in this push. Use when the caller is pushing the
                authoritative full set (e.g. after re-processing claims). Default
                False — partial pushes (e.g. a field-worker mobile sync) MUST NOT
                set this, or unrelated PL records get wiped.

        Returns:
            Dict with 'results', 'errors', and 'summary' keys:
            {
                'results': [...],  # Created/updated records
                'errors': [...],   # Any errors that occurred
                'summary': {'created': N, 'updated': N, 'errors': N}
            }
        """
        endpoint = self.config.get_model_endpoint(model_name)
        if not endpoint:
            raise ValueError(f"Unknown model: {model_name}")

        url = f"{endpoint}bulk/"
        if cleanup_orphans:
            url = f"{url}?cleanup_orphans=true"
        return self._make_request('POST', url, data=records)

    def check_conflicts(
        self,
        model_name: str,
        records: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Check which records would be overwritten by a bulk push.

        Calls the /check-conflicts/ endpoint which performs a dry-run lookup
        without making any changes.

        Args:
            model_name: Model name (e.g., 'PointSample')
            records: List of record data dicts (same format as bulk_upsert_records)

        Returns:
            Dict with 'would_create', 'would_update', and 'conflicts' keys:
            {
                'would_create': N,
                'would_update': N,
                'conflicts': [
                    {
                        'index': 0,
                        'sequence_number': 'SS-001',
                        'existing_id': 123,
                        'existing_status': 'PL',
                        'existing_status_display': 'Planned',
                        'existing_name': '',
                        'matched_by': 'sequence_number'
                    },
                    ...
                ]
            }
        """
        endpoint = self.config.get_model_endpoint(model_name)
        if not endpoint:
            raise ValueError(f"Unknown model: {model_name}")

        url = f"{endpoint}check-conflicts/"
        return self._make_request('POST', url, data=records)

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

    def get_drill_pads(self, project_id: int) -> List[Dict[str, Any]]:
        """
        Get all drill pads for a project.

        Args:
            project_id: Project ID to filter by

        Returns:
            List of drill pad dictionaries with polygon geometry
        """
        result = self.get_all_paginated('DrillPad', project_id)
        return result.get('results', []) if isinstance(result, dict) else result

    # =========================================================================
    # Lookup Tables (Lithology, Alteration)
    # =========================================================================

    def get_lithologies(self, project_id: int) -> List[Dict[str, Any]]:
        """
        Get all lithology types for a project.

        Args:
            project_id: Project ID to filter by

        Returns:
            List of lithology dictionaries with 'id', 'name', 'color', etc.
        """
        url = self.config.endpoints['lithologies']
        url = f"{url}?project_id={project_id}"
        self.logger.info(f"Fetching lithologies from: {url}")

        response = self._make_request('GET', url)
        if isinstance(response, list):
            return response
        return response.get('results', [])

    def get_alterations(self, project_id: int) -> List[Dict[str, Any]]:
        """
        Get all alteration types for a project.

        Args:
            project_id: Project ID to filter by

        Returns:
            List of alteration dictionaries with 'id', 'name', 'color', etc.
        """
        url = self.config.endpoints['alterations']
        url = f"{url}?project_id={project_id}"

        response = self._make_request('GET', url)
        if isinstance(response, list):
            return response
        return response.get('results', [])

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

