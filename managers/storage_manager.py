# -*- coding: utf-8 -*-
"""
Storage management for GeodbIO plugin.

Handles GeoPackage and memory layer storage, with per-project
storage preferences and path mappings.
"""
import os
from pathlib import Path
from typing import Optional, Dict, Any

from qgis.core import QgsSettings, QgsApplication
from qgis.PyQt.QtCore import QStandardPaths

from ..utils.logger import PluginLogger


class StorageMode:
    """Storage mode constants."""
    MEMORY = 'memory'
    GEOPACKAGE = 'geopackage'


class StorageManager:
    """
    Manages storage preferences and GeoPackage paths for projects.

    Stores mappings between project IDs and their storage configuration
    in QSettings for persistence across sessions.
    """

    # Settings keys
    SETTINGS_PREFIX = 'geodb/storage'
    DEFAULT_DIR_KEY = f'{SETTINGS_PREFIX}/default_directory'
    PROJECT_STORAGE_KEY = f'{SETTINGS_PREFIX}/projects'

    def __init__(self):
        """Initialize storage manager."""
        self.logger = PluginLogger.get_logger()
        self.settings = QgsSettings()
        self._current_project_id: Optional[int] = None

    def get_default_directory(self) -> Path:
        """
        Get default directory for GeoPackage files.

        Returns user's Documents/GeodbData folder, creating it if needed.

        Returns:
            Path to default storage directory
        """
        # Check if user has set a custom default
        custom_default = self.settings.value(self.DEFAULT_DIR_KEY, '')
        if custom_default and os.path.isdir(custom_default):
            return Path(custom_default)

        # Use platform-appropriate Documents folder
        docs_path = QStandardPaths.writableLocation(
            QStandardPaths.DocumentsLocation
        )
        default_dir = Path(docs_path) / 'GeodbData'

        # Create if doesn't exist
        if not default_dir.exists():
            try:
                default_dir.mkdir(parents=True, exist_ok=True)
                self.logger.info(f"Created default storage directory: {default_dir}")
            except OSError as e:
                self.logger.warning(f"Could not create default directory: {e}")
                # Fall back to temp directory
                default_dir = Path(QStandardPaths.writableLocation(
                    QStandardPaths.TempLocation
                )) / 'GeodbData'
                default_dir.mkdir(parents=True, exist_ok=True)

        return default_dir

    def set_default_directory(self, path: str) -> bool:
        """
        Set default directory for new GeoPackage files.

        Args:
            path: Directory path

        Returns:
            True if successful
        """
        if os.path.isdir(path):
            self.settings.setValue(self.DEFAULT_DIR_KEY, path)
            self.logger.info(f"Set default storage directory: {path}")
            return True
        return False

    def get_storage_mode(self, project_id: int) -> str:
        """
        Get storage mode for a project.

        Args:
            project_id: Project ID

        Returns:
            StorageMode.MEMORY or StorageMode.GEOPACKAGE
        """
        key = f'{self.PROJECT_STORAGE_KEY}/{project_id}/mode'
        return self.settings.value(key, StorageMode.MEMORY)

    def set_storage_mode(self, project_id: int, mode: str):
        """
        Set storage mode for a project.

        Args:
            project_id: Project ID
            mode: StorageMode.MEMORY or StorageMode.GEOPACKAGE
        """
        key = f'{self.PROJECT_STORAGE_KEY}/{project_id}/mode'
        self.settings.setValue(key, mode)
        self.logger.info(f"Set storage mode for project {project_id}: {mode}")

    def get_geopackage_path(self, project_id: int) -> Optional[Path]:
        """
        Get GeoPackage path for a project.

        Args:
            project_id: Project ID

        Returns:
            Path to GeoPackage file, or None if not set
        """
        key = f'{self.PROJECT_STORAGE_KEY}/{project_id}/gpkg_path'
        path_str = self.settings.value(key, '')

        if path_str:
            return Path(path_str)
        return None

    def set_geopackage_path(self, project_id: int, path: str):
        """
        Set GeoPackage path for a project.

        Args:
            project_id: Project ID
            path: Path to GeoPackage file
        """
        key = f'{self.PROJECT_STORAGE_KEY}/{project_id}/gpkg_path'
        self.settings.setValue(key, str(path))
        self.logger.info(f"Set GeoPackage path for project {project_id}: {path}")

    def get_suggested_filename(self, project_name: str, project_id: int) -> str:
        """
        Generate suggested GeoPackage filename for a project.

        Args:
            project_name: Project name
            project_id: Project ID

        Returns:
            Suggested filename (without path)
        """
        # Sanitize project name for filename
        safe_name = "".join(
            c if c.isalnum() or c in (' ', '-', '_') else '_'
            for c in project_name
        ).strip()
        safe_name = safe_name.replace(' ', '_')

        return f"{safe_name}_{project_id}.gpkg"

    def get_suggested_path(self, project_name: str, project_id: int) -> Path:
        """
        Get suggested full path for a project's GeoPackage.

        Args:
            project_name: Project name
            project_id: Project ID

        Returns:
            Full suggested path
        """
        directory = self.get_default_directory()
        filename = self.get_suggested_filename(project_name, project_id)
        return directory / filename

    def is_configured(self, project_id: int) -> bool:
        """
        Check if storage is configured for a project.

        Args:
            project_id: Project ID

        Returns:
            True if storage has been configured (not just default memory)
        """
        key = f'{self.PROJECT_STORAGE_KEY}/{project_id}/configured'
        return self.settings.value(key, False, type=bool)

    def mark_configured(self, project_id: int):
        """
        Mark a project's storage as configured.

        Args:
            project_id: Project ID
        """
        key = f'{self.PROJECT_STORAGE_KEY}/{project_id}/configured'
        self.settings.setValue(key, True)

    def get_storage_config(self, project_id: int) -> Dict[str, Any]:
        """
        Get complete storage configuration for a project.

        Args:
            project_id: Project ID

        Returns:
            Dictionary with mode, path, and configured status
        """
        mode = self.get_storage_mode(project_id)
        gpkg_path = self.get_geopackage_path(project_id)
        configured = self.is_configured(project_id)

        return {
            'mode': mode,
            'geopackage_path': str(gpkg_path) if gpkg_path else None,
            'configured': configured,
            'is_memory': mode == StorageMode.MEMORY,
            'is_geopackage': mode == StorageMode.GEOPACKAGE
        }

    def configure_storage(
        self,
        project_id: int,
        mode: str,
        geopackage_path: Optional[str] = None
    ) -> bool:
        """
        Configure storage for a project.

        Args:
            project_id: Project ID
            mode: StorageMode.MEMORY or StorageMode.GEOPACKAGE
            geopackage_path: Path to GeoPackage (required if mode is GEOPACKAGE)

        Returns:
            True if successful
        """
        if mode == StorageMode.GEOPACKAGE:
            if not geopackage_path:
                self.logger.error("GeoPackage path required for GEOPACKAGE mode")
                return False

            # Ensure parent directory exists
            gpkg_path = Path(geopackage_path)
            if not gpkg_path.parent.exists():
                try:
                    gpkg_path.parent.mkdir(parents=True, exist_ok=True)
                except OSError as e:
                    self.logger.error(f"Could not create directory: {e}")
                    return False

            self.set_geopackage_path(project_id, geopackage_path)

        self.set_storage_mode(project_id, mode)
        self.mark_configured(project_id)

        self.logger.info(
            f"Configured storage for project {project_id}: "
            f"mode={mode}, path={geopackage_path}"
        )
        return True

    def clear_project_config(self, project_id: int):
        """
        Clear storage configuration for a project.

        Args:
            project_id: Project ID
        """
        prefix = f'{self.PROJECT_STORAGE_KEY}/{project_id}'
        # Remove all keys for this project
        self.settings.remove(prefix)
        self.logger.info(f"Cleared storage config for project {project_id}")

    def has_unsaved_memory_layers(self) -> bool:
        """
        Check if there are memory layers with geodb data that haven't been saved.

        This is used to warn users before closing QGIS.

        Returns:
            True if there are unsaved memory layers
        """
        from qgis.core import QgsProject

        for layer in QgsProject.instance().mapLayers().values():
            # Check if it's a memory layer
            if layer.dataProvider() and layer.dataProvider().name() == 'memory':
                # Check if it's one of our layers (has geodb sync metadata)
                # We check for layers named after our models
                layer_name = layer.name()
                geodb_models = [
                    'DrillCollar', 'DrillSample', 'DrillPad', 'DrillLithology',
                    'DrillAlteration', 'DrillStructure', 'DrillMineralization',
                    'DrillSurvey', 'DrillPhoto', 'LandHolding', 'PointSample'
                ]
                if layer_name in geodb_models and layer.featureCount() > 0:
                    return True

        return False

    def get_memory_layer_names(self) -> list:
        """
        Get names of memory layers containing geodb data.

        Returns:
            List of layer names
        """
        from qgis.core import QgsProject

        memory_layers = []
        geodb_models = [
            'DrillCollar', 'DrillSample', 'DrillPad', 'DrillLithology',
            'DrillAlteration', 'DrillStructure', 'DrillMineralization',
            'DrillSurvey', 'DrillPhoto', 'LandHolding', 'PointSample'
        ]

        for layer in QgsProject.instance().mapLayers().values():
            if layer.dataProvider() and layer.dataProvider().name() == 'memory':
                if layer.name() in geodb_models and layer.featureCount() > 0:
                    memory_layers.append(layer.name())

        return memory_layers
