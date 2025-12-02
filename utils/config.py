# -*- coding: utf-8 -*-
"""
Configuration management for GeodbIO plugin.
"""
import json
import os
from typing import Optional, Dict, Any
from pathlib import Path


# Dev mode is enabled via environment variable GEODB_DEV_MODE=1
# This shows the local development server option in the UI
DEV_MODE = os.environ.get('GEODB_DEV_MODE', '').lower() in ('1', 'true', 'yes')


class Config:
    """Central configuration management for the plugin."""
    
    DEFAULT_CONFIG = {
        "api": {
            "base_url": "https://api.geodb.io/api/v1",
            "local_base_url": "http://localhost:8000/api/v1",
            "use_local": False,
            "timeout": 30,
            "retry_attempts": 3
        },
        "data": {
            "coordinate_precision": 6,
            "default_crs": "EPSG:4326"
        },
        "ui": {
            "show_progress_dialog": True,
            "auto_save_qgis_project": True
        },
        "logging": {
            "level": "INFO",
            "enabled": True
        }
    }
    
    def __init__(self, config_path: Optional[str] = None):
        """
        Initialize configuration.
        
        Args:
            config_path: Optional path to config file. If None, uses default location.
        """
        if config_path is None:
            # Use QGIS profile directory
            from qgis.PyQt.QtCore import QSettings
            settings = QSettings()
            profile_path = settings.value('userProfilePath', '')
            if profile_path:
                config_path = os.path.join(profile_path, 'geodb_plugin_config.json')
            else:
                config_path = os.path.expanduser('~/.qgis3/geodb_plugin_config.json')
        
        self.config_path = config_path
        self._config = self._load_config()
    
    def _load_config(self) -> Dict[str, Any]:
        """Load configuration from file or create default."""
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r') as f:
                    user_config = json.load(f)
                # Merge with defaults
                config = self._deep_merge(self.DEFAULT_CONFIG.copy(), user_config)
                return config
            except Exception as e:
                print(f"Error loading config: {e}. Using defaults.")
                return self.DEFAULT_CONFIG.copy()
        else:
            # Create default config file
            self.save()
            return self.DEFAULT_CONFIG.copy()
    
    def _deep_merge(self, base: dict, update: dict) -> dict:
        """Deep merge two dictionaries."""
        for key, value in update.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                base[key] = self._deep_merge(base[key], value)
            else:
                base[key] = value
        return base
    
    def save(self) -> bool:
        """Save configuration to file."""
        try:
            # Ensure directory exists
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            
            with open(self.config_path, 'w') as f:
                json.dump(self._config, f, indent=2)
            return True
        except Exception as e:
            print(f"Error saving config: {e}")
            return False
    
    def get(self, key_path: str, default: Any = None) -> Any:
        """
        Get configuration value using dot notation.
        
        Args:
            key_path: Dot-separated path (e.g., 'api.base_url')
            default: Default value if key not found
            
        Returns:
            Configuration value or default
        """
        keys = key_path.split('.')
        value = self._config
        
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default
        
        return value
    
    def set(self, key_path: str, value: Any) -> bool:
        """
        Set configuration value using dot notation.
        
        Args:
            key_path: Dot-separated path (e.g., 'api.base_url')
            value: Value to set
            
        Returns:
            True if successful
        """
        keys = key_path.split('.')
        config = self._config
        
        # Navigate to the parent of the target key
        for key in keys[:-1]:
            if key not in config:
                config[key] = {}
            config = config[key]
        
        # Set the value
        config[keys[-1]] = value
        return self.save()
    
    @property
    def base_url(self) -> str:
        """Get the appropriate base URL (production or local)."""
        if self.get('api.use_local', False):
            return self.get('api.local_base_url')
        return self.get('api.base_url')
    
    @property
    def endpoints(self) -> Dict[str, str]:
        """Get all API endpoints based on geodb.io API v1 specification."""
        base = self.base_url
        return {
            # Authentication
            'login': f"{base}/api-token-auth/",
            'logout': f"{base}/api-logout/",
            'check_token': f"{base}/check-token/",

            # User context (critical - must call after login)
            'me': f"{base}/me/",
            'set_active_company': f"{base}/me/set-active-company/",
            'set_active_project': f"{base}/me/set-active-project/",
            'set_assay_merge_settings': f"{base}/me/set-assay-merge-settings/",

            # Projects
            'projects': f"{base}/projects/",

            # Drill data endpoints
            'drill_collars': f"{base}/drill-collars/",
            'drill_samples': f"{base}/drill-samples/",
            'drill_pads': f"{base}/drill-pads/",
            'drill_lithologies': f"{base}/drill-lithologies/",
            'drill_alterations': f"{base}/drill-alterations/",
            'drill_structures': f"{base}/drill-structures/",
            'drill_mineralizations': f"{base}/drill-mineralizations/",
            'drill_surveys': f"{base}/drill-surveys/",
            'drill_photos': f"{base}/drill-photos/",
            'drill_traces': f"{base}/drill-traces/",

            # Land holdings
            'landholdings': f"{base}/landholdings/",
            'landholding_types': f"{base}/landholding-types/",

            # Point samples
            'point_samples': f"{base}/point-samples/",

            # Project files (GeoTIFFs, DEMs, rasters)
            'project_files': f"{base}/project-files/",

            # Assay configurations
            'assay_range_configurations': f"{base}/assay-range-configurations/",
            'assay_merge_settings': f"{base}/assay-merge-settings/",
        }

    def get_model_endpoint(self, model_name: str) -> str:
        """
        Get the API endpoint for a specific model.

        Args:
            model_name: Model name (e.g., 'DrillCollar', 'LandHolding', 'DrillSample')

        Returns:
            Full endpoint URL for the model
        """
        # Map model names to endpoint keys
        model_map = {
            'DrillCollar': 'drill_collars',
            'DrillSample': 'drill_samples',
            'DrillPad': 'drill_pads',
            'DrillLithology': 'drill_lithologies',
            'DrillAlteration': 'drill_alterations',
            'DrillStructure': 'drill_structures',
            'DrillMineralization': 'drill_mineralizations',
            'DrillSurvey': 'drill_surveys',
            'DrillPhoto': 'drill_photos',
            'DrillTrace': 'drill_traces',
            'LandHolding': 'landholdings',
            'PointSample': 'point_samples',
            'ProjectFile': 'project_files',
        }

        endpoint_key = model_map.get(model_name)
        if endpoint_key:
            return self.endpoints.get(endpoint_key, '')
        return ''

    def toggle_local_mode(self, enabled: bool = True):
        """Switch between production and local API."""
        self.set('api.use_local', enabled)