# -*- coding: utf-8 -*-
"""
Claims storage manager for GeoPackage persistence.

Provides local storage for claims workflow data including:
- Reference points
- Form metadata (claimant info, settings)
- Processed claims cache
- Session recovery data

Implements the QClaims GeoPackage schema for compatibility.

Reference: QClaims q_claims.py (lines 1334-1609)
"""
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional

from qgis.core import (
    QgsVectorLayer, QgsVectorFileWriter, QgsProject,
    QgsFeature, QgsGeometry, QgsField, QgsFields,
    QgsCoordinateReferenceSystem, QgsPointXY, QgsWkbTypes
)
from qgis.PyQt.QtCore import QVariant

from ..utils.logger import PluginLogger


class ClaimsStorageManager:
    """
    Manages claims GeoPackage storage with QClaims metadata format.

    Provides persistence for:
    - Reference points (for bearing/distance calculations)
    - Form metadata (claimant info, monument type, etc.)
    - Processed claims data (for session recovery)
    - Waypoints (for GPX export)

    The GeoPackage is identified by a special marker in the qclaims_metadata
    table: _qclaims_geopackage = 'true'
    """

    # Standard table names
    METADATA_TABLE = 'qclaims_metadata'
    REFERENCE_POINTS_TABLE = 'reference_points'
    CLAIMS_DRAFT_TABLE = 'claims_draft'
    WAYPOINTS_TABLE = 'waypoints'

    # Claims workflow layer table names
    INITIAL_LAYOUT_TABLE = 'initial_layout'
    PROCESSED_CLAIMS_TABLE = 'processed_claims'
    CLAIM_WAYPOINTS_TABLE = 'claim_waypoints'

    # Claims layer generator table names (Step 5 - Adjust)
    CORNER_POINTS_TABLE = 'corner_points'
    LM_CORNERS_TABLE = 'lm_corners'
    CENTERLINES_TABLE = 'center_lines'
    MONUMENTS_TABLE = 'monuments'
    SIDELINE_MONUMENTS_TABLE = 'sideline_monuments'
    ENDLINE_MONUMENTS_TABLE = 'endline_monuments'

    # Metadata keys
    KEY_IDENTIFIER = '_qclaims_geopackage'
    KEY_PROJECT_NAME = 'project_name'
    KEY_CREATED_AT = 'created_at'
    KEY_LAST_MODIFIED = 'last_modified'
    KEY_EPSG = 'epsg'
    KEY_CLAIMANT_NAME = 'claimant_name'
    KEY_CLAIMANT_ADDRESS = 'claimant_address'
    KEY_CLAIMANT_CITY = 'claimant_city'
    KEY_CLAIMANT_STATE = 'claimant_state'
    KEY_CLAIMANT_ZIP = 'claimant_zip'
    KEY_MINING_DISTRICT = 'mining_district'
    KEY_MONUMENT_TYPE = 'monument_type'
    KEY_MONUMENT_INSET_FT = 'monument_inset_ft'

    def __init__(self):
        """Initialize the claims storage manager."""
        self.logger = PluginLogger.get_logger()
        self._current_gpkg: Optional[str] = None

    def get_or_create_geopackage(
        self,
        project_name: str,
        directory: str,
        epsg: int = 4326
    ) -> str:
        """
        Get existing or create new claims GeoPackage.

        Args:
            project_name: Name for the project (used in filename)
            directory: Directory to store the GeoPackage
            epsg: EPSG code for the coordinate system (default WGS84)

        Returns:
            Path to the GeoPackage file

        Raises:
            OSError: If directory cannot be created or accessed
        """
        # Ensure directory exists
        dir_path = Path(directory)
        dir_path.mkdir(parents=True, exist_ok=True)

        # Generate filename from project name
        safe_name = "".join(c if c.isalnum() or c in '-_' else '_' for c in project_name)
        gpkg_name = f"{safe_name}_claims.gpkg"
        gpkg_path = str(dir_path / gpkg_name)

        if os.path.exists(gpkg_path):
            # Verify it's a QClaims geopackage
            if self.is_qclaims_geopackage(gpkg_path):
                self._current_gpkg = gpkg_path
                self.logger.info(f"[CLAIMS STORAGE] Using existing GeoPackage: {gpkg_path}")
                return gpkg_path
            else:
                # Rename existing non-QClaims file
                backup_path = gpkg_path.replace('.gpkg', '_backup.gpkg')
                os.rename(gpkg_path, backup_path)
                self.logger.warning(
                    f"[CLAIMS STORAGE] Renamed non-QClaims file to: {backup_path}"
                )

        # Create new GeoPackage
        self._create_geopackage(gpkg_path, project_name, epsg)
        self._current_gpkg = gpkg_path
        return gpkg_path

    def _create_geopackage(self, gpkg_path: str, project_name: str, epsg: int):
        """
        Create a new QClaims GeoPackage with required tables.

        Args:
            gpkg_path: Path for the new GeoPackage
            project_name: Project name for metadata
            epsg: EPSG code for layers
        """
        try:
            # Create a temporary point layer to initialize the GeoPackage
            crs = QgsCoordinateReferenceSystem(f'EPSG:{epsg}')

            # Create reference points layer
            ref_layer = self._create_reference_points_layer(crs)

            # Write to GeoPackage (creates the file)
            options = QgsVectorFileWriter.SaveVectorOptions()
            options.driverName = 'GPKG'
            options.layerName = self.REFERENCE_POINTS_TABLE
            options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile

            error = QgsVectorFileWriter.writeAsVectorFormatV3(
                ref_layer,
                gpkg_path,
                QgsProject.instance().transformContext(),
                options
            )

            if error[0] != QgsVectorFileWriter.NoError:
                raise Exception(f"Failed to create GeoPackage: {error[1]}")

            # Now add metadata table using SQLite
            self._create_metadata_table(gpkg_path)
            self._save_metadata(gpkg_path, {
                self.KEY_IDENTIFIER: 'true',
                self.KEY_PROJECT_NAME: project_name,
                self.KEY_CREATED_AT: datetime.now().isoformat(),
                self.KEY_LAST_MODIFIED: datetime.now().isoformat(),
                self.KEY_EPSG: str(epsg),
            })

            self.logger.info(f"[CLAIMS STORAGE] Created new GeoPackage: {gpkg_path}")

        except Exception as e:
            self.logger.error(f"[CLAIMS STORAGE] Failed to create GeoPackage: {e}")
            raise

    def _create_reference_points_layer(
        self,
        crs: QgsCoordinateReferenceSystem
    ) -> QgsVectorLayer:
        """
        Create a memory layer for reference points.

        Args:
            crs: Coordinate reference system for the layer

        Returns:
            QgsVectorLayer with reference points schema
        """
        layer = QgsVectorLayer(
            f"Point?crs={crs.authid()}",
            self.REFERENCE_POINTS_TABLE,
            "memory"
        )

        fields = QgsFields()
        fields.append(QgsField("name", QVariant.String, len=255))
        fields.append(QgsField("easting", QVariant.Double))
        fields.append(QgsField("northing", QVariant.Double))
        fields.append(QgsField("epsg", QVariant.Int))
        fields.append(QgsField("created_at", QVariant.String, len=50))

        layer.dataProvider().addAttributes(fields)
        layer.updateFields()

        return layer

    def _create_metadata_table(self, gpkg_path: str):
        """
        Create the metadata table in the GeoPackage.

        Args:
            gpkg_path: Path to the GeoPackage file
        """
        conn = sqlite3.connect(gpkg_path)
        cursor = conn.cursor()

        cursor.execute(f'''
            CREATE TABLE IF NOT EXISTS {self.METADATA_TABLE} (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')

        conn.commit()
        conn.close()

    def _save_metadata(self, gpkg_path: str, metadata: Dict[str, str]):
        """
        Save metadata to the GeoPackage.

        Args:
            gpkg_path: Path to the GeoPackage file
            metadata: Dictionary of key-value pairs to save
        """
        conn = sqlite3.connect(gpkg_path)
        cursor = conn.cursor()

        for key, value in metadata.items():
            cursor.execute(f'''
                INSERT OR REPLACE INTO {self.METADATA_TABLE} (key, value)
                VALUES (?, ?)
            ''', (key, value))

        # Update last modified
        cursor.execute(f'''
            INSERT OR REPLACE INTO {self.METADATA_TABLE} (key, value)
            VALUES (?, ?)
        ''', (self.KEY_LAST_MODIFIED, datetime.now().isoformat()))

        conn.commit()
        conn.close()

    def is_qclaims_geopackage(self, gpkg_path: str) -> bool:
        """
        Check if a GeoPackage has the QClaims identifier.

        Args:
            gpkg_path: Path to the GeoPackage file

        Returns:
            True if the file is a QClaims GeoPackage
        """
        if not os.path.exists(gpkg_path):
            return False

        try:
            conn = sqlite3.connect(gpkg_path)
            cursor = conn.cursor()

            # Check if metadata table exists
            cursor.execute('''
                SELECT name FROM sqlite_master
                WHERE type='table' AND name=?
            ''', (self.METADATA_TABLE,))

            if not cursor.fetchone():
                conn.close()
                return False

            # Check for identifier
            cursor.execute(f'''
                SELECT value FROM {self.METADATA_TABLE}
                WHERE key = ?
            ''', (self.KEY_IDENTIFIER,))

            result = cursor.fetchone()
            conn.close()

            return result is not None and result[0] == 'true'

        except Exception as e:
            self.logger.warning(
                f"[CLAIMS STORAGE] Error checking GeoPackage: {e}"
            )
            return False

    def save_metadata(self, gpkg_path: str, metadata: Dict[str, str]):
        """
        Save form metadata to the GeoPackage.

        Public wrapper for _save_metadata.

        Args:
            gpkg_path: Path to the GeoPackage file
            metadata: Dictionary of metadata to save
        """
        self._save_metadata(gpkg_path, metadata)
        self.logger.debug(f"[CLAIMS STORAGE] Saved metadata: {list(metadata.keys())}")

    def load_metadata(self, gpkg_path: str) -> Dict[str, str]:
        """
        Load form metadata from the GeoPackage.

        Args:
            gpkg_path: Path to the GeoPackage file

        Returns:
            Dictionary of all metadata key-value pairs
        """
        if not os.path.exists(gpkg_path):
            return {}

        try:
            conn = sqlite3.connect(gpkg_path)
            cursor = conn.cursor()

            cursor.execute(f'SELECT key, value FROM {self.METADATA_TABLE}')
            rows = cursor.fetchall()

            conn.close()

            return {row[0]: row[1] for row in rows}

        except Exception as e:
            self.logger.error(f"[CLAIMS STORAGE] Failed to load metadata: {e}")
            return {}

    def save_claimant_info(self, gpkg_path: str, claimant_info: Dict[str, str]):
        """
        Save claimant information to metadata.

        Args:
            gpkg_path: Path to the GeoPackage file
            claimant_info: Dictionary with claimant details
        """
        metadata = {
            self.KEY_CLAIMANT_NAME: claimant_info.get('name', ''),
            self.KEY_CLAIMANT_ADDRESS: claimant_info.get('address', ''),
            self.KEY_CLAIMANT_CITY: claimant_info.get('city', ''),
            self.KEY_CLAIMANT_STATE: claimant_info.get('state', ''),
            self.KEY_CLAIMANT_ZIP: claimant_info.get('zip', ''),
            self.KEY_MINING_DISTRICT: claimant_info.get('mining_district', ''),
            self.KEY_MONUMENT_TYPE: claimant_info.get('monument_type', ''),
        }

        if 'monument_inset_ft' in claimant_info:
            metadata[self.KEY_MONUMENT_INSET_FT] = str(
                claimant_info['monument_inset_ft']
            )

        self._save_metadata(gpkg_path, metadata)
        self.logger.info("[CLAIMS STORAGE] Saved claimant info")

    def load_claimant_info(self, gpkg_path: str) -> Dict[str, str]:
        """
        Load claimant information from metadata.

        Args:
            gpkg_path: Path to the GeoPackage file

        Returns:
            Dictionary with claimant details
        """
        metadata = self.load_metadata(gpkg_path)

        return {
            'name': metadata.get(self.KEY_CLAIMANT_NAME, ''),
            'address': metadata.get(self.KEY_CLAIMANT_ADDRESS, ''),
            'city': metadata.get(self.KEY_CLAIMANT_CITY, ''),
            'state': metadata.get(self.KEY_CLAIMANT_STATE, ''),
            'zip': metadata.get(self.KEY_CLAIMANT_ZIP, ''),
            'mining_district': metadata.get(self.KEY_MINING_DISTRICT, ''),
            'monument_type': metadata.get(self.KEY_MONUMENT_TYPE, ''),
            'monument_inset_ft': float(
                metadata.get(self.KEY_MONUMENT_INSET_FT, '25.0')
            ),
        }

    def save_reference_point(
        self,
        point: QgsPointXY,
        description: str,
        epsg: int,
        gpkg_path: Optional[str] = None
    ):
        """
        Save a reference point to the GeoPackage.

        Args:
            point: Point coordinates (x=easting, y=northing)
            description: Description of the reference point
            epsg: EPSG code of the coordinate system
            gpkg_path: Path to GeoPackage (uses current if not specified)
        """
        if gpkg_path is None:
            gpkg_path = self._current_gpkg

        if not gpkg_path or not os.path.exists(gpkg_path):
            raise ValueError("No GeoPackage available for saving reference point")

        try:
            # Open the layer
            layer_uri = f"{gpkg_path}|layername={self.REFERENCE_POINTS_TABLE}"
            layer = QgsVectorLayer(layer_uri, "ref_points", "ogr")

            if not layer.isValid():
                self.logger.error(
                    f"[CLAIMS STORAGE] Could not open layer: {layer_uri}"
                )
                raise Exception("Could not open reference points layer")

            # Create feature
            feature = QgsFeature(layer.fields())
            feature.setGeometry(QgsGeometry.fromPointXY(point))
            feature.setAttribute("name", description)
            feature.setAttribute("easting", point.x())
            feature.setAttribute("northing", point.y())
            feature.setAttribute("epsg", epsg)
            feature.setAttribute("created_at", datetime.now().isoformat())

            # Add feature using data provider
            provider = layer.dataProvider()
            success, added_features = provider.addFeatures([feature])

            if not success:
                raise Exception(f"Failed to add feature: {provider.error().message()}")

            self.logger.info(
                f"[CLAIMS STORAGE] Saved reference point: {description}"
            )

        except Exception as e:
            self.logger.error(f"[CLAIMS STORAGE] Failed to save reference point: {e}")
            raise

    def load_reference_points(
        self,
        gpkg_path: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Load all reference points from the GeoPackage.

        Args:
            gpkg_path: Path to GeoPackage (uses current if not specified)

        Returns:
            List of reference point dictionaries
        """
        if gpkg_path is None:
            gpkg_path = self._current_gpkg

        if not gpkg_path or not os.path.exists(gpkg_path):
            return []

        try:
            layer_uri = f"{gpkg_path}|layername={self.REFERENCE_POINTS_TABLE}"
            layer = QgsVectorLayer(layer_uri, "ref_points", "ogr")

            if not layer.isValid():
                return []

            points = []
            for feature in layer.getFeatures():
                geom = feature.geometry()
                if geom and not geom.isNull():
                    pt = geom.asPoint()
                    points.append({
                        'name': feature.attribute('name'),
                        'easting': pt.x(),
                        'northing': pt.y(),
                        'epsg': feature.attribute('epsg'),
                        'created_at': feature.attribute('created_at'),
                    })

            return points

        except Exception as e:
            self.logger.error(
                f"[CLAIMS STORAGE] Failed to load reference points: {e}"
            )
            return []

    def clear_reference_points(self, gpkg_path: Optional[str] = None):
        """
        Clear all reference points from the GeoPackage.

        Args:
            gpkg_path: Path to GeoPackage (uses current if not specified)
        """
        if gpkg_path is None:
            gpkg_path = self._current_gpkg

        if not gpkg_path or not os.path.exists(gpkg_path):
            return

        try:
            layer_uri = f"{gpkg_path}|layername={self.REFERENCE_POINTS_TABLE}"
            layer = QgsVectorLayer(layer_uri, "ref_points", "ogr")

            if layer.isValid():
                # Delete all features
                feature_ids = [f.id() for f in layer.getFeatures()]
                layer.dataProvider().deleteFeatures(feature_ids)
                self.logger.info("[CLAIMS STORAGE] Cleared all reference points")

        except Exception as e:
            self.logger.error(
                f"[CLAIMS STORAGE] Failed to clear reference points: {e}"
            )

    def get_current_geopackage(self) -> Optional[str]:
        """
        Get the path to the currently active GeoPackage.

        Returns:
            Path to the current GeoPackage or None
        """
        return self._current_gpkg

    def set_current_geopackage(self, gpkg_path: str):
        """
        Set the current GeoPackage path.

        Args:
            gpkg_path: Path to the GeoPackage file
        """
        if os.path.exists(gpkg_path):
            self._current_gpkg = gpkg_path
            self.logger.info(f"[CLAIMS STORAGE] Set current GeoPackage: {gpkg_path}")
        else:
            raise FileNotFoundError(f"GeoPackage not found: {gpkg_path}")

    def get_reference_points_layer(
        self,
        gpkg_path: Optional[str] = None,
        layer_display_name: Optional[str] = None,
        epsg: Optional[int] = None
    ) -> Optional[QgsVectorLayer]:
        """
        Get the reference points layer from the GeoPackage.

        If the layer doesn't exist in the GeoPackage, it will be created.

        Args:
            gpkg_path: Path to GeoPackage (uses current if not specified)
            layer_display_name: Display name for the layer (default: "Reference Points")
            epsg: EPSG code for CRS when creating layer (default: from metadata or 4326)

        Returns:
            QgsVectorLayer or None if not available
        """
        if gpkg_path is None:
            gpkg_path = self._current_gpkg

        if not gpkg_path or not os.path.exists(gpkg_path):
            self.logger.warning(
                f"[CLAIMS STORAGE] Cannot get reference points layer: "
                f"GeoPackage not found at {gpkg_path}"
            )
            return None

        if layer_display_name is None:
            layer_display_name = "Reference Points"

        layer_uri = f"{gpkg_path}|layername={self.REFERENCE_POINTS_TABLE}"
        layer = QgsVectorLayer(layer_uri, layer_display_name, "ogr")

        if layer.isValid():
            self.logger.debug(
                f"[CLAIMS STORAGE] Loaded reference points layer from {gpkg_path}"
            )
            return layer

        # Table doesn't exist - create it
        self.logger.info(
            f"[CLAIMS STORAGE] Reference points table not found in {gpkg_path}, creating..."
        )

        try:
            # Use provided EPSG, or get from metadata, or use default
            if epsg is None:
                metadata = self.load_metadata(gpkg_path)
                epsg = int(metadata.get(self.KEY_EPSG, '4326'))
            crs = QgsCoordinateReferenceSystem(f'EPSG:{epsg}')

            # Create the reference points layer in the GeoPackage
            ref_layer = self._create_reference_points_layer(crs)

            # Write to GeoPackage
            options = QgsVectorFileWriter.SaveVectorOptions()
            options.driverName = 'GPKG'
            options.layerName = self.REFERENCE_POINTS_TABLE
            options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteLayer

            error = QgsVectorFileWriter.writeAsVectorFormatV3(
                ref_layer,
                gpkg_path,
                QgsProject.instance().transformContext(),
                options
            )

            if error[0] != QgsVectorFileWriter.NoError:
                self.logger.error(
                    f"[CLAIMS STORAGE] Failed to create reference points table: {error[1]}"
                )
                return None

            # Now load the layer we just created
            layer = QgsVectorLayer(layer_uri, layer_display_name, "ogr")
            if layer.isValid():
                self.logger.info(
                    f"[CLAIMS STORAGE] Created and loaded reference points layer"
                )
                return layer
            else:
                self.logger.error(
                    "[CLAIMS STORAGE] Created reference points table but layer is invalid"
                )
                return None

        except Exception as e:
            self.logger.error(
                f"[CLAIMS STORAGE] Failed to create reference points layer: {e}"
            )
            return None

    # =========================================================================
    # Claims Workflow Layer Methods
    # =========================================================================

    def create_or_update_layer(
        self,
        table_name: str,
        layer_display_name: str,
        geometry_type: str,
        fields: QgsFields,
        crs: QgsCoordinateReferenceSystem,
        gpkg_path: Optional[str] = None,
        add_to_project: bool = True
    ) -> QgsVectorLayer:
        """
        Create or replace a layer in the claims GeoPackage.

        If the layer already exists in the GeoPackage, it will be replaced.

        Args:
            table_name: Name for the table in GeoPackage (e.g., 'initial_layout')
            layer_display_name: Display name for the layer in QGIS
            geometry_type: Geometry type ('Point', 'Polygon', etc.)
            fields: QgsFields object defining the layer schema
            crs: Coordinate reference system for the layer
            gpkg_path: Path to GeoPackage (uses current if not specified)
            add_to_project: If True, adds layer to QGIS project (default True)

        Returns:
            QgsVectorLayer loaded from the GeoPackage

        Raises:
            ValueError: If no GeoPackage path is available
            Exception: If layer creation fails
        """
        if gpkg_path is None:
            gpkg_path = self._current_gpkg

        if not gpkg_path:
            raise ValueError("No GeoPackage path available")

        # Ensure parent directory exists
        gpkg_dir = os.path.dirname(gpkg_path)
        if gpkg_dir and not os.path.exists(gpkg_dir):
            os.makedirs(gpkg_dir, exist_ok=True)

        # Remove existing layer from QGIS project if present
        self._remove_layer_from_project(table_name, gpkg_path)

        # Delete existing table from GeoPackage if it exists AND is valid
        # (Skip deletion attempt on invalid files to avoid OGR error spam)
        if os.path.exists(gpkg_path) and self._is_valid_geopackage(gpkg_path):
            self._delete_table_from_geopackage(table_name, gpkg_path)

        # Create a temporary memory layer with the schema
        temp_uri = f"{geometry_type}?crs={crs.authid()}"
        temp_layer = QgsVectorLayer(temp_uri, table_name, "memory")
        temp_layer.dataProvider().addAttributes(fields.toList())
        temp_layer.updateFields()

        # Write to GeoPackage
        options = QgsVectorFileWriter.SaveVectorOptions()
        options.driverName = 'GPKG'
        options.layerName = table_name

        # Check if GeoPackage exists AND is valid (has required system tables)
        gpkg_valid = False
        if os.path.exists(gpkg_path):
            gpkg_valid = self._is_valid_geopackage(gpkg_path)
            if not gpkg_valid:
                # File exists but is not a valid GeoPackage - remove it
                self.logger.warning(
                    f"[CLAIMS STORAGE] Removing invalid GeoPackage file: {gpkg_path}"
                )
                try:
                    os.remove(gpkg_path)
                except Exception as e:
                    self.logger.error(f"[CLAIMS STORAGE] Failed to remove invalid file: {e}")

        if gpkg_valid:
            options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteLayer
        else:
            options.actionOnExistingFile = QgsVectorFileWriter.CreateOrOverwriteFile

        error = QgsVectorFileWriter.writeAsVectorFormatV3(
            temp_layer,
            gpkg_path,
            QgsProject.instance().transformContext(),
            options
        )

        if error[0] != QgsVectorFileWriter.NoError:
            error_msg = error[1] if error[1] else "Unknown error"
            self.logger.error(f"[CLAIMS STORAGE] Failed to create layer: {error_msg}")
            raise Exception(f"Failed to create layer '{table_name}': {error_msg}")

        # Load the layer from GeoPackage
        layer_uri = f"{gpkg_path}|layername={table_name}"
        layer = QgsVectorLayer(layer_uri, layer_display_name, "ogr")

        if not layer.isValid():
            raise Exception(f"Failed to load layer '{table_name}' from GeoPackage")

        # Add to QGIS project in Claims Workflow group (if requested)
        if add_to_project:
            self._add_layer_to_claims_group(layer)

        self.logger.info(
            f"[CLAIMS STORAGE] Created layer '{layer_display_name}' "
            f"(table: {table_name}) in {gpkg_path}"
        )

        return layer

    def load_layer(
        self,
        table_name: str,
        layer_display_name: str,
        gpkg_path: Optional[str] = None,
        add_to_project: bool = True
    ) -> Optional[QgsVectorLayer]:
        """
        Load an existing layer from the claims GeoPackage.

        Args:
            table_name: Name of the table in GeoPackage
            layer_display_name: Display name for the layer in QGIS
            gpkg_path: Path to GeoPackage (uses current if not specified)
            add_to_project: Whether to add the layer to the QGIS project

        Returns:
            QgsVectorLayer if found and valid, None otherwise
        """
        if gpkg_path is None:
            gpkg_path = self._current_gpkg

        if not gpkg_path or not os.path.exists(gpkg_path):
            return None

        # Check if table exists in GeoPackage
        if not self._table_exists_in_geopackage(table_name, gpkg_path):
            return None

        # Check if layer is already in project
        existing_layer = self._find_layer_in_project(table_name, gpkg_path)
        if existing_layer:
            return existing_layer

        # Load the layer
        layer_uri = f"{gpkg_path}|layername={table_name}"
        layer = QgsVectorLayer(layer_uri, layer_display_name, "ogr")

        if not layer.isValid():
            self.logger.warning(
                f"[CLAIMS STORAGE] Failed to load layer '{table_name}' from {gpkg_path}"
            )
            return None

        if add_to_project:
            self._add_layer_to_claims_group(layer)
            self.logger.info(
                f"[CLAIMS STORAGE] Loaded layer '{layer_display_name}' from GeoPackage"
            )

        return layer

    def delete_layer(
        self,
        table_name: str,
        gpkg_path: Optional[str] = None
    ) -> bool:
        """
        Remove a layer from the GeoPackage and QGIS project.

        Args:
            table_name: Name of the table to delete
            gpkg_path: Path to GeoPackage (uses current if not specified)

        Returns:
            True if deletion was successful, False otherwise
        """
        if gpkg_path is None:
            gpkg_path = self._current_gpkg

        if not gpkg_path or not os.path.exists(gpkg_path):
            return False

        # Remove from QGIS project first
        self._remove_layer_from_project(table_name, gpkg_path)

        # Delete from GeoPackage
        return self._delete_table_from_geopackage(table_name, gpkg_path)

    def layer_exists(
        self,
        table_name: str,
        gpkg_path: Optional[str] = None
    ) -> bool:
        """
        Check if a layer exists in the GeoPackage.

        Args:
            table_name: Name of the table to check
            gpkg_path: Path to GeoPackage (uses current if not specified)

        Returns:
            True if the layer exists
        """
        if gpkg_path is None:
            gpkg_path = self._current_gpkg

        if not gpkg_path or not os.path.exists(gpkg_path):
            return False

        return self._table_exists_in_geopackage(table_name, gpkg_path)

    def clear_layer_features(
        self,
        table_name: str,
        gpkg_path: Optional[str] = None
    ) -> bool:
        """
        Clear all features from a layer without deleting the layer.

        Args:
            table_name: Name of the table to clear
            gpkg_path: Path to GeoPackage (uses current if not specified)

        Returns:
            True if successful, False otherwise
        """
        if gpkg_path is None:
            gpkg_path = self._current_gpkg

        if not gpkg_path or not os.path.exists(gpkg_path):
            return False

        try:
            layer_uri = f"{gpkg_path}|layername={table_name}"
            layer = QgsVectorLayer(layer_uri, table_name, "ogr")

            if not layer.isValid():
                return False

            # Delete all features
            feature_ids = [f.id() for f in layer.getFeatures()]
            if feature_ids:
                layer.dataProvider().deleteFeatures(feature_ids)
                self.logger.info(
                    f"[CLAIMS STORAGE] Cleared {len(feature_ids)} features from '{table_name}'"
                )

            return True

        except Exception as e:
            self.logger.error(
                f"[CLAIMS STORAGE] Failed to clear layer '{table_name}': {e}"
            )
            return False

    # =========================================================================
    # Private Helper Methods
    # =========================================================================

    def _is_valid_geopackage(self, gpkg_path: str) -> bool:
        """
        Check if a file is a valid GeoPackage with required system tables.

        A valid GeoPackage must have gpkg_spatial_ref_sys and gpkg_contents tables.

        Args:
            gpkg_path: Path to the file to check

        Returns:
            True if the file is a valid GeoPackage
        """
        try:
            conn = sqlite3.connect(gpkg_path)
            cursor = conn.cursor()

            # Check for required GeoPackage system tables
            cursor.execute('''
                SELECT name FROM sqlite_master
                WHERE type='table' AND name IN ('gpkg_spatial_ref_sys', 'gpkg_contents')
            ''')

            tables = [row[0] for row in cursor.fetchall()]
            conn.close()

            # Both tables must exist for a valid GeoPackage
            is_valid = 'gpkg_spatial_ref_sys' in tables and 'gpkg_contents' in tables
            if not is_valid:
                self.logger.debug(
                    f"[CLAIMS STORAGE] File missing required GeoPackage tables: {gpkg_path}"
                )
            return is_valid

        except Exception as e:
            self.logger.warning(
                f"[CLAIMS STORAGE] Error validating GeoPackage: {e}"
            )
            return False

    def _table_exists_in_geopackage(self, table_name: str, gpkg_path: str) -> bool:
        """Check if a table exists in the GeoPackage."""
        try:
            conn = sqlite3.connect(gpkg_path)
            cursor = conn.cursor()

            cursor.execute('''
                SELECT name FROM sqlite_master
                WHERE type='table' AND name=?
            ''', (table_name,))

            result = cursor.fetchone()
            conn.close()

            return result is not None

        except Exception as e:
            self.logger.warning(
                f"[CLAIMS STORAGE] Error checking table existence: {e}"
            )
            return False

    def _delete_table_from_geopackage(self, table_name: str, gpkg_path: str) -> bool:
        """Delete a table from the GeoPackage using OGR."""
        try:
            from osgeo import ogr

            # Open GeoPackage in update mode
            ds = ogr.Open(gpkg_path, 1)
            if ds is None:
                self.logger.warning(
                    f"[CLAIMS STORAGE] Could not open GeoPackage for update: {gpkg_path}"
                )
                return False

            # Find and delete the layer
            for i in range(ds.GetLayerCount()):
                layer = ds.GetLayerByIndex(i)
                if layer.GetName() == table_name:
                    ds.DeleteLayer(i)
                    self.logger.info(
                        f"[CLAIMS STORAGE] Deleted table '{table_name}' from GeoPackage"
                    )
                    ds = None  # Close the dataset
                    return True

            ds = None  # Close the dataset
            return False

        except ImportError:
            # Fallback to SQLite if OGR not available
            self.logger.warning(
                "[CLAIMS STORAGE] OGR not available, using SQLite fallback for table deletion"
            )
            try:
                conn = sqlite3.connect(gpkg_path)
                cursor = conn.cursor()
                # Drop the table
                cursor.execute(f'DROP TABLE IF EXISTS "{table_name}"')
                # Also clean up GeoPackage metadata tables
                cursor.execute(
                    'DELETE FROM gpkg_contents WHERE table_name = ?',
                    (table_name,)
                )
                cursor.execute(
                    'DELETE FROM gpkg_geometry_columns WHERE table_name = ?',
                    (table_name,)
                )
                conn.commit()
                conn.close()
                return True
            except Exception as e:
                self.logger.error(f"[CLAIMS STORAGE] SQLite table deletion failed: {e}")
                return False

        except Exception as e:
            self.logger.error(
                f"[CLAIMS STORAGE] Failed to delete table '{table_name}': {e}"
            )
            return False

    def _remove_layer_from_project(self, table_name: str, gpkg_path: str):
        """Remove a layer from the QGIS project if it exists."""
        layer = self._find_layer_in_project(table_name, gpkg_path)
        if layer:
            # Get the layer name BEFORE removing it (the C++ object is deleted after removal)
            layer_name = layer.name()
            layer_id = layer.id()
            QgsProject.instance().removeMapLayer(layer_id)
            self.logger.debug(
                f"[CLAIMS STORAGE] Removed layer '{layer_name}' from project"
            )

    def _find_layer_in_project(
        self,
        table_name: str,
        gpkg_path: str
    ) -> Optional[QgsVectorLayer]:
        """Find a layer in the QGIS project by its GeoPackage source."""
        expected_source = f"{gpkg_path}|layername={table_name}"

        for layer in QgsProject.instance().mapLayers().values():
            if isinstance(layer, QgsVectorLayer):
                # Check if layer source matches
                if layer.source() == expected_source:
                    return layer
                # Also check normalized paths
                if (os.path.normpath(layer.source().split('|')[0]) ==
                        os.path.normpath(gpkg_path)):
                    if f"layername={table_name}" in layer.source():
                        return layer

        return None

    def _add_layer_to_claims_group(self, layer: QgsVectorLayer):
        """
        Add a layer to the Claims Workflow group in the QGIS project.

        Args:
            layer: The layer to add
        """
        try:
            project = QgsProject.instance()
            root = project.layerTreeRoot()

            # Find or create the Claims Workflow group
            group = root.findGroup("Claims Workflow")
            if not group:
                group = root.insertGroup(0, "Claims Workflow")

            # Add layer to project (without adding to layer tree automatically)
            project.addMapLayer(layer, False)
            # Add to the group
            group.addLayer(layer)

        except Exception as e:
            self.logger.error(f"[CLAIMS STORAGE] Failed to add layer to group: {e}")
            # Fallback to adding without group
            QgsProject.instance().addMapLayer(layer)
