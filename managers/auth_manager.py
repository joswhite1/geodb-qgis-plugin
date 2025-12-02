# -*- coding: utf-8 -*-
"""
Authentication management for GeodbIO plugin.

Implements the authentication flow as documented in USER_CONTEXT_API_GUIDE.md
"""
from typing import Optional, Tuple, Dict, Any
from qgis.core import QgsApplication, QgsAuthMethodConfig
from qgis.PyQt.QtCore import QSettings

from ..api.client import APIClient
from ..api.exceptions import AuthenticationError
from ..models.auth import AuthSession, UserInfo, UserContext
from ..utils.config import Config
from ..utils.logger import PluginLogger


class AuthManager:
    """
    Manages user authentication and credential storage.
    Uses QGIS Authentication Manager for secure token storage.

    After login, automatically fetches user context from /api/v1/me/
    to get active company, project, permissions, and settings.
    """

    AUTH_CONFIG_NAME = "geodb.io"
    AUTH_METHOD = "Basic"
    SETTINGS_KEY = "geodb/saved_email"

    def __init__(self, config: Config, api_client: APIClient):
        """
        Initialize authentication manager.

        Args:
            config: Configuration instance
            api_client: API client instance
        """
        self.config = config
        self.api_client = api_client
        self.logger = PluginLogger.get_logger()
        self.current_session: Optional[AuthSession] = None
        self.auth_manager = QgsApplication.authManager()
        self.settings = QSettings()

    def login(self, username: str, password: str, save_password: bool = False) -> Tuple[bool, Dict[str, Any]]:
        """
        Authenticate user and create session.

        This method:
        1. Authenticates with /api/v1/api-token-auth/
        2. Fetches user context from /api/v1/me/
        3. Stores credentials securely (token always, password optionally)
        4. Creates session with full context

        Args:
            username: User's email/username
            password: User's password
            save_password: Whether to save password in QGIS Auth Manager (default: False)

        Returns:
            Tuple of (success: bool, result: dict)
            On success: (True, {'token': str, 'user_context': dict})
            On failure: (False, {'error': str})
        """
        self.logger.info(f"Attempting login for user: {username}")

        try:
            # Step 1: Authenticate and get token
            response = self.api_client.login(username, password)

            token = response.get('token')
            user_data = response.get('user', {})

            if not token:
                return (False, {'error': 'No token received from server'})

            # Set token in API client for subsequent requests
            self.api_client.set_token(token)

            # Step 2: Fetch user context (critical step per API docs!)
            try:
                context_response = self.api_client.get_user_context()
                user_context = UserContext.from_api_response(context_response)
            except Exception as e:
                self.logger.warning(f"Failed to fetch user context: {e}")
                # Create minimal user context from login response
                user_context = UserContext(
                    user=UserInfo(
                        user_id=user_data.get('id', 0),
                        username=user_data.get('username', username),
                        email=user_data.get('email', username),
                        first_name=user_data.get('first_name', ''),
                        last_name=user_data.get('last_name', '')
                    )
                )

            # Step 3: Store credentials in QGIS Auth Manager
            auth_config_id = self._store_credentials(username, token, password if save_password else None)

            # Step 4: Create session with full context
            self.current_session = AuthSession(
                token=token,
                user=user_context.user,
                auth_config_id=auth_config_id,
                user_context=user_context
            )

            self.logger.info(f"Login successful for user: {username}")

            # Return success with context
            return (True, {
                'token': token,
                'user_context': user_context.to_dict()
            })

        except AuthenticationError as e:
            self.logger.error(f"Authentication failed: {e}")
            return (False, {'error': str(e)})
        except Exception as e:
            self.logger.error(f"Login failed: {e}")
            return (False, {'error': f"Login failed: {e}"})
    
    def logout(self) -> bool:
        """
        Logout current user and clear credentials.
        
        Returns:
            True if successful
        """
        if not self.current_session:
            return True
        
        self.logger.info("Logging out")
        
        try:
            # Notify server
            self.api_client.logout()
        except Exception as e:
            self.logger.warning(f"Server logout failed: {e}")
        
        # Clear local session
        self.current_session = None
        self.api_client.set_token(None)
        
        return True
    
    def restore_session(self) -> Optional[AuthSession]:
        """
        Restore session from stored credentials.

        This also fetches the user context from /api/v1/me/
        to ensure we have the latest permissions and settings.

        Returns:
            AuthSession if valid credentials found, None otherwise
        """
        self.logger.info("Attempting to restore session")

        # Find stored auth config
        auth_config_id = self._find_auth_config()
        if not auth_config_id:
            self.logger.info("No stored credentials found")
            return None

        # Load credentials
        auth_config = QgsAuthMethodConfig()
        if not self.auth_manager.loadAuthenticationConfig(auth_config_id, auth_config, True):
            self.logger.warning("Failed to load auth config")
            return None

        # Extract token (stored in realm field)
        token = auth_config.config('realm', '')
        username = auth_config.config('username', '')

        if not token:
            self.logger.warning("No token in stored credentials")
            return None

        # Set token and verify with server
        self.api_client.set_token(token)

        try:
            # Verify token is still valid
            self.api_client.check_token()

            # Fetch full user context (per API docs, do this after auth)
            context_response = self.api_client.get_user_context()
            user_context = UserContext.from_api_response(context_response)

            # Restore session with full context
            self.current_session = AuthSession(
                token=token,
                user=user_context.user,
                auth_config_id=auth_config_id,
                user_context=user_context
            )

            self.logger.info(f"Session restored for user: {username}")
            return self.current_session

        except AuthenticationError:
            self.logger.warning("Stored token is invalid")
            self._remove_credentials(auth_config_id)
            return None
        except Exception as e:
            self.logger.warning(f"Failed to restore session: {e}")
            self._remove_credentials(auth_config_id)
            return None

    def refresh_user_context(self) -> Optional[UserContext]:
        """
        Refresh user context from server.

        Call this when user changes active project or settings.

        Returns:
            Updated UserContext or None if failed
        """
        if not self.is_authenticated():
            return None

        try:
            context_response = self.api_client.get_user_context()
            user_context = UserContext.from_api_response(context_response)

            # Update session
            if self.current_session:
                self.current_session.user_context = user_context
                self.current_session.user = user_context.user

            return user_context

        except Exception as e:
            self.logger.error(f"Failed to refresh user context: {e}")
            return None

    def set_active_project(self, project_id: int) -> Optional[UserContext]:
        """
        Set the active project and refresh context.

        Args:
            project_id: Project ID to make active

        Returns:
            Updated UserContext or None if failed
        """
        if not self.is_authenticated():
            return None

        try:
            context_response = self.api_client.set_active_project(project_id)
            user_context = UserContext.from_api_response(context_response)

            if self.current_session:
                self.current_session.user_context = user_context

            return user_context

        except Exception as e:
            self.logger.error(f"Failed to set active project: {e}")
            return None

    def set_active_company(self, company_id: int) -> Optional[UserContext]:
        """
        Set the active company and refresh context.

        Args:
            company_id: Company ID to make active

        Returns:
            Updated UserContext or None if failed
        """
        if not self.is_authenticated():
            return None

        try:
            context_response = self.api_client.set_active_company(company_id)
            user_context = UserContext.from_api_response(context_response)

            if self.current_session:
                self.current_session.user_context = user_context

            return user_context

        except Exception as e:
            self.logger.error(f"Failed to set active company: {e}")
            return None

    def get_session(self) -> Optional[AuthSession]:
        """Get current authentication session."""
        return self.current_session

    def get_current_session(self) -> Optional[AuthSession]:
        """Get current authentication session (alias for get_session)."""
        return self.current_session

    def is_authenticated(self) -> bool:
        """Check if user is authenticated."""
        return self.current_session is not None and self.current_session.is_valid()

    # =========================================================================
    # Email storage for "Remember me" feature
    # =========================================================================

    def get_saved_email(self) -> Optional[str]:
        """Get saved email from settings."""
        return self.settings.value(self.SETTINGS_KEY, None)

    def save_email(self, email: str) -> None:
        """Save email to settings."""
        self.settings.setValue(self.SETTINGS_KEY, email)

    def clear_saved_email(self) -> None:
        """Clear saved email from settings."""
        self.settings.remove(self.SETTINGS_KEY)

    def get_saved_password(self) -> Optional[str]:
        """
        Get saved password from QGIS Auth Manager.

        Returns:
            Saved password or None if not found
        """
        auth_config_id = self._find_auth_config()
        if not auth_config_id:
            return None

        auth_config = QgsAuthMethodConfig()
        if not self.auth_manager.loadAuthenticationConfig(auth_config_id, auth_config, True):
            return None

        password = auth_config.config('password', '')
        return password if password else None
    
    def _store_credentials(self, username: str, token: str, password: Optional[str] = None) -> str:
        """
        Store credentials in QGIS Authentication Manager.

        Args:
            username: Username
            token: Authentication token
            password: Optional password to save securely

        Returns:
            Auth config ID
        """
        # Check if config already exists
        existing_id = self._find_auth_config()

        # Create or update config
        auth_config = QgsAuthMethodConfig()

        if existing_id:
            # Load existing
            self.auth_manager.loadAuthenticationConfig(existing_id, auth_config, True)
            auth_config_id = existing_id
        else:
            # Create new
            auth_config.setMethod(self.AUTH_METHOD)
            auth_config.setName(self.AUTH_CONFIG_NAME)
            auth_config_id = auth_config.id()

        # Store username and token
        # Note: Token is stored in 'realm' field as per legacy implementation
        auth_config.setConfig('username', username)
        auth_config.setConfig('realm', token)
        auth_config.setConfig('password', password if password else '')

        # Save to auth manager
        if existing_id:
            self.auth_manager.updateAuthenticationConfig(auth_config)
        else:
            self.auth_manager.storeAuthenticationConfig(auth_config)
            auth_config_id = auth_config.id()

        self.logger.debug(f"Stored credentials with ID: {auth_config_id}")
        return auth_config_id
    
    def _find_auth_config(self) -> Optional[str]:
        """Find existing auth config by name."""
        configs = self.auth_manager.availableAuthMethodConfigs()
        
        for config_id, config in configs.items():
            if config.name() == self.AUTH_CONFIG_NAME:
                return config_id
        
        return None
    
    def _remove_credentials(self, auth_config_id: str) -> bool:
        """Remove credentials from auth manager."""
        try:
            self.auth_manager.removeAuthenticationConfig(auth_config_id)
            self.logger.info("Removed stored credentials")
            return True
        except Exception as e:
            self.logger.error(f"Failed to remove credentials: {e}")
            return False