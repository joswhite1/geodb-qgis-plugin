# -*- coding: utf-8 -*-
"""
Claims wizard shared state object.

Manages all data for the claims workflow, persisted between wizard steps
and to GeoPackage metadata.
"""
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field
from pathlib import Path
import json
import sqlite3

from qgis.core import QgsVectorLayer, QgsProject


@dataclass
class ClaimsWizardState:
    """
    Shared state object passed between wizard steps.

    All state is stored here and persisted to GeoPackage metadata tables.
    Each step reads from and writes to this state object.
    """

    # Project context
    project_id: Optional[int] = None
    company_id: Optional[int] = None
    geopackage_path: Optional[str] = None

    # Step 1: Project Setup
    claimant_name: str = ""
    address_line1: str = ""
    address_line2: str = ""
    address_line3: str = ""
    mining_district: str = ""
    monument_type: str = "2' wooden post"  # Default monument description
    project_epsg: Optional[int] = None  # Auto-detected UTM zone

    # Step 2: Claim Layout
    claims_layer_id: Optional[str] = None  # QGIS layer ID for initial_layout
    grid_name_prefix: str = "GE"
    grid_rows: int = 2
    grid_cols: int = 4
    grid_azimuth: float = 0.0

    # GeoPackage layer IDs (for tracking persisted layers)
    initial_layout_layer_id: Optional[str] = None
    processed_claims_layer_id: Optional[str] = None
    waypoints_layer_id: Optional[str] = None

    # Step 3: Reference Points
    reference_points: List[Dict[str, Any]] = field(default_factory=list)

    # Step 4: Monument Settings
    monument_inset_ft: float = 25.0  # Default 25 feet from centerline
    lm_corner: int = 1  # Default Corner 1 for LM designation (ID/NM)

    # Step 5: Processing Results
    processed_claims: List[Dict[str, Any]] = field(default_factory=list)
    processed_waypoints: List[Dict[str, Any]] = field(default_factory=list)
    generated_documents: List[Dict[str, Any]] = field(default_factory=list)
    package_info: Optional[Dict[str, Any]] = None  # ClaimPackage info from server

    # License/TOS state (cached from server)
    access_info: Optional[Dict[str, Any]] = None
    tos_accepted: bool = False

    # Step completion tracking
    completed_steps: List[int] = field(default_factory=list)

    # Staff order fulfillment context
    # When set, links generated documents to existing order/package
    fulfillment_order_id: Optional[int] = None
    fulfillment_order_type: Optional[str] = None  # 'claim_purchase' or 'claim_order'
    fulfillment_order_number: Optional[str] = None  # For display (e.g., "CPO-2026-0001")
    fulfillment_claimant_info: Optional[Dict[str, Any]] = None  # Pre-populated from order

    @property
    def claims_layer(self) -> Optional[QgsVectorLayer]:
        """Get the claims layer from QGIS project."""
        if not self.claims_layer_id:
            return None
        layer = QgsProject.instance().mapLayer(self.claims_layer_id)
        if isinstance(layer, QgsVectorLayer):
            return layer
        return None

    @claims_layer.setter
    def claims_layer(self, layer: Optional[QgsVectorLayer]):
        """Set the claims layer ID."""
        if layer:
            self.claims_layer_id = layer.id()
        else:
            self.claims_layer_id = None

    def get_claimant_address_lines(self) -> List[str]:
        """Get non-empty address lines as a list."""
        lines = []
        if self.address_line1:
            lines.append(self.address_line1)
        if self.address_line2:
            lines.append(self.address_line2)
        if self.address_line3:
            lines.append(self.address_line3)
        return lines

    def is_step_complete(self, step: int) -> bool:
        """Check if a step has been completed."""
        return step in self.completed_steps

    def mark_step_complete(self, step: int):
        """Mark a step as completed."""
        if step not in self.completed_steps:
            self.completed_steps.append(step)
            self.completed_steps.sort()

    def mark_step_incomplete(self, step: int):
        """Mark a step as incomplete (e.g., when going back and making changes)."""
        if step in self.completed_steps:
            self.completed_steps.remove(step)
        # Also mark all subsequent steps as incomplete
        self.completed_steps = [s for s in self.completed_steps if s < step]

    def validate_for_step(self, step: int) -> List[str]:
        """
        Validate state for proceeding to next step.

        Args:
            step: Current step number (1-6)

        Returns:
            List of validation error messages (empty if valid)
        """
        errors = []

        if step == 1:
            # Project setup validation
            if not self.project_epsg:
                errors.append("Project CRS must be set to a UTM zone")
            if not self.claimant_name:
                errors.append("Claimant name is required")
            if not self.address_line1:
                errors.append("At least one address line is required")

        elif step == 2:
            # Claim layout validation
            if not self.claims_layer:
                errors.append("A claims layer must be created or selected")
            elif self.claims_layer.featureCount() == 0:
                errors.append("Claims layer must have at least one feature")

        elif step == 3:
            # Reference point is optional, no validation required
            pass

        elif step == 4:
            # Monument adjustment validation
            if self.monument_inset_ft <= 0:
                errors.append("Monument inset must be greater than 0")
            if self.lm_corner not in [1, 2, 3, 4]:
                errors.append("LM corner must be 1, 2, 3, or 4")

        elif step == 5:
            # Finalize validation
            if not self.claims_layer:
                errors.append("Claims layer is required")
            if not self.tos_accepted:
                errors.append("Terms of Service must be accepted")
            if not self.access_info:
                errors.append("License access must be verified")

        elif step == 6:
            # Export validation
            if not self.processed_claims:
                errors.append("Claims must be processed before export")

        return errors

    def save_to_geopackage(self) -> bool:
        """
        Save state to GeoPackage metadata tables.

        Returns:
            True if successful, False otherwise
        """
        if not self.geopackage_path:
            return False

        try:
            conn = sqlite3.connect(self.geopackage_path)
            cursor = conn.cursor()

            # Create metadata table if it doesn't exist
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS claims_metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            ''')

            # Save all metadata
            metadata = {
                'claimant_name': self.claimant_name,
                'address_line1': self.address_line1,
                'address_line2': self.address_line2,
                'address_line3': self.address_line3,
                'mining_district': self.mining_district,
                'monument_type': self.monument_type,
                'project_epsg': str(self.project_epsg) if self.project_epsg else '',
                'grid_name_prefix': self.grid_name_prefix,
                'grid_rows': str(self.grid_rows),
                'grid_cols': str(self.grid_cols),
                'grid_azimuth': str(self.grid_azimuth),
                'monument_inset_ft': str(self.monument_inset_ft),
                'lm_corner': str(self.lm_corner),
                'reference_points': json.dumps(self.reference_points),
                'completed_steps': json.dumps(self.completed_steps),
            }

            for key, value in metadata.items():
                cursor.execute('''
                    INSERT OR REPLACE INTO claims_metadata (key, value) VALUES (?, ?)
                ''', (key, value))

            conn.commit()
            conn.close()
            return True

        except Exception as e:
            print(f"Error saving to GeoPackage: {e}")
            return False

    def load_from_geopackage(self, path: str) -> bool:
        """
        Load state from GeoPackage metadata tables.

        Args:
            path: Path to the GeoPackage file

        Returns:
            True if successful, False otherwise
        """
        if not Path(path).exists():
            return False

        try:
            conn = sqlite3.connect(path)
            cursor = conn.cursor()

            # Check if metadata table exists
            cursor.execute('''
                SELECT name FROM sqlite_master
                WHERE type='table' AND name='claims_metadata'
            ''')
            if not cursor.fetchone():
                conn.close()
                return False

            # Load all metadata
            cursor.execute('SELECT key, value FROM claims_metadata')
            rows = cursor.fetchall()
            conn.close()

            metadata = dict(rows)

            # Apply to state
            self.geopackage_path = path
            self.claimant_name = metadata.get('claimant_name', '')
            # Support both new keys (address_line1/2/3) and old ClaimsStorageManager keys
            # (claimant_address, claimant_city, claimant_state) for backward compatibility
            self.address_line1 = metadata.get('address_line1', '') or metadata.get('claimant_address', '')
            self.address_line2 = metadata.get('address_line2', '') or metadata.get('claimant_city', '')
            self.address_line3 = metadata.get('address_line3', '') or metadata.get('claimant_state', '')
            self.mining_district = metadata.get('mining_district', '')
            self.monument_type = metadata.get('monument_type', "2' wooden post")

            epsg_str = metadata.get('project_epsg', '')
            self.project_epsg = int(epsg_str) if epsg_str else None

            self.grid_name_prefix = metadata.get('grid_name_prefix', 'GE')
            self.grid_rows = int(metadata.get('grid_rows', '2'))
            self.grid_cols = int(metadata.get('grid_cols', '4'))
            self.grid_azimuth = float(metadata.get('grid_azimuth', '0.0'))
            self.monument_inset_ft = float(metadata.get('monument_inset_ft', '25.0'))
            self.lm_corner = int(metadata.get('lm_corner', '1'))

            ref_points_json = metadata.get('reference_points', '[]')
            self.reference_points = json.loads(ref_points_json)

            completed_json = metadata.get('completed_steps', '[]')
            self.completed_steps = json.loads(completed_json)

            return True

        except Exception as e:
            print(f"Error loading from GeoPackage: {e}")
            return False

    def save_to_qgis_project(self):
        """Save key state to QGIS project settings for session persistence."""
        project = QgsProject.instance()

        if self.geopackage_path:
            project.writeEntry('geodb', 'claims/geopackage_path', self.geopackage_path)
        if self.claims_layer_id:
            project.writeEntry('geodb', 'claims/claims_layer_id', self.claims_layer_id)

        project.writeEntry('geodb', 'claims/completed_steps', json.dumps(self.completed_steps))

    def load_from_qgis_project(self):
        """Load key state from QGIS project settings."""
        project = QgsProject.instance()

        gpkg_path, _ = project.readEntry('geodb', 'claims/geopackage_path', '')
        if gpkg_path and Path(gpkg_path).exists():
            self.load_from_geopackage(gpkg_path)

        layer_id, _ = project.readEntry('geodb', 'claims/claims_layer_id', '')
        if layer_id:
            self.claims_layer_id = layer_id

        completed_json, _ = project.readEntry('geodb', 'claims/completed_steps', '[]')
        try:
            self.completed_steps = json.loads(completed_json)
        except json.JSONDecodeError:
            self.completed_steps = []

    def reset(self):
        """Reset state to defaults (for starting a new claims project)."""
        self.geopackage_path = None
        self.claimant_name = ""
        self.address_line1 = ""
        self.address_line2 = ""
        self.address_line3 = ""
        self.mining_district = ""
        self.monument_type = "2' wooden post"
        self.project_epsg = None
        self.claims_layer_id = None
        self.grid_name_prefix = "GE"
        self.grid_rows = 2
        self.grid_cols = 4
        self.grid_azimuth = 0.0
        self.reference_points = []
        self.monument_inset_ft = 25.0
        self.lm_corner = 1
        self.processed_claims = []
        self.processed_waypoints = []
        self.generated_documents = []
        self.package_info = None
        self.completed_steps = []
        self.initial_layout_layer_id = None
        self.processed_claims_layer_id = None
        self.waypoints_layer_id = None
        # Clear staff fulfillment context
        self.fulfillment_order_id = None
        self.fulfillment_order_type = None
        self.fulfillment_order_number = None
        self.fulfillment_claimant_info = None

    def set_fulfillment_context(self, order_data: Dict[str, Any]):
        """
        Set staff fulfillment context from order data.

        Args:
            order_data: Order dict from staff_pending_orders API
        """
        self.fulfillment_order_id = order_data.get('id')
        self.fulfillment_order_type = order_data.get('order_type')
        self.fulfillment_order_number = order_data.get('order_number')
        self.fulfillment_claimant_info = order_data.get('claimant_info')

        # Pre-populate claimant info if available
        claimant_info = order_data.get('claimant_info')
        if claimant_info:
            if claimant_info.get('claimant_name'):
                self.claimant_name = claimant_info['claimant_name']
            if claimant_info.get('address_1'):
                self.address_line1 = claimant_info['address_1']
            if claimant_info.get('address_2'):
                self.address_line2 = claimant_info['address_2']
            if claimant_info.get('address_3'):
                self.address_line3 = claimant_info['address_3']
            if claimant_info.get('district'):
                self.mining_district = claimant_info['district']

    @property
    def is_fulfillment_mode(self) -> bool:
        """Check if we're in staff order fulfillment mode."""
        return self.fulfillment_order_id is not None

    def restore_layers_from_geopackage(self) -> Dict[str, Optional[QgsVectorLayer]]:
        """
        Restore all claims layers from the GeoPackage.

        Called when reopening a QGIS project or resuming a claims workflow.
        Loads existing layers from GeoPackage and adds them to the project.

        Returns:
            Dict with table names as keys and loaded layers as values
        """
        if not self.geopackage_path:
            return {}

        from ..managers.claims_storage_manager import ClaimsStorageManager
        storage_manager = ClaimsStorageManager()

        layers = {}

        # Define layers to restore
        layer_configs = [
            (ClaimsStorageManager.INITIAL_LAYOUT_TABLE, "Initial Layout"),
            (ClaimsStorageManager.PROCESSED_CLAIMS_TABLE, "Processed Claims"),
            (ClaimsStorageManager.CLAIM_WAYPOINTS_TABLE, "Claims Waypoints"),
        ]

        # Try to load each layer
        for table_name, display_name in layer_configs:
            layer = storage_manager.load_layer(
                table_name,
                display_name,
                self.geopackage_path
            )
            layers[table_name] = layer

            # Store layer IDs for tracking
            if layer:
                if table_name == ClaimsStorageManager.INITIAL_LAYOUT_TABLE:
                    self.initial_layout_layer_id = layer.id()
                    # Also set as claims_layer for compatibility
                    self.claims_layer_id = layer.id()
                elif table_name == ClaimsStorageManager.PROCESSED_CLAIMS_TABLE:
                    self.processed_claims_layer_id = layer.id()
                elif table_name == ClaimsStorageManager.CLAIM_WAYPOINTS_TABLE:
                    self.waypoints_layer_id = layer.id()

        return layers
