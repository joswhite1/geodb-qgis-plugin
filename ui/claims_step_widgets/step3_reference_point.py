# -*- coding: utf-8 -*-
"""
Step 3: Reference Point

Handles:
- Choose reference point on map (interactive tool)
- Store reference description
"""
from typing import List

from qgis.PyQt.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QGroupBox, QFrame, QScrollArea
)

from .step_base import ClaimsStepBase


class ClaimsStep3Widget(ClaimsStepBase):
    """
    Step 3: Reference Point

    Add optional reference points for bearing/distance calculations.
    """

    def get_step_title(self) -> str:
        return "Reference Point"

    def get_step_description(self) -> str:
        return (
            "Add reference points to describe your claims' location relative to "
            "landmarks like road intersections, survey monuments, or natural features. "
            "This step is optional but recommended for clear location descriptions."
        )

    def __init__(self, state, claims_manager, parent=None):
        super().__init__(state, claims_manager, parent)
        self._setup_ui()

    def _setup_ui(self):
        """Set up the step UI."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        scroll_content = QWidget()
        layout = QVBoxLayout(scroll_content)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # Header
        layout.addWidget(self._create_header())

        # Reference Points Group
        layout.addWidget(self._create_reference_group())

        # Instructions
        layout.addWidget(self._create_instructions())

        layout.addStretch()

        scroll.setWidget(scroll_content)
        main_layout.addWidget(scroll)

    def _create_reference_group(self) -> QGroupBox:
        """Create the reference points group."""
        group = QGroupBox("Reference Points")
        group.setStyleSheet(self._get_group_style())
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        # Info
        info_label = QLabel(
            "Click on the map to add reference points. Each point should represent "
            "a recognizable landmark that helps locate your claims."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet(self._get_info_label_style())
        layout.addWidget(info_label)

        # Use the existing ReferencePointsWidget
        try:
            from ..reference_map_tool import ReferencePointsWidget
            self.reference_widget = ReferencePointsWidget()
            self.reference_widget.reference_points_changed.connect(
                self._on_reference_points_changed
            )
            layout.addWidget(self.reference_widget)
        except ImportError:
            # Fallback if widget not available
            placeholder = QLabel("Reference points widget not available")
            placeholder.setStyleSheet("color: #dc2626;")
            layout.addWidget(placeholder)

        return group

    def _create_instructions(self) -> QWidget:
        """Create the instructions panel."""
        frame = QFrame()
        frame.setStyleSheet("""
            QFrame {
                background-color: #eff6ff;
                border: 1px solid #bfdbfe;
                border-radius: 8px;
                padding: 12px;
            }
        """)
        layout = QVBoxLayout(frame)
        layout.setSpacing(8)

        title = QLabel("How to Add Reference Points")
        title.setStyleSheet("font-weight: bold; color: #1e40af;")
        layout.addWidget(title)

        instructions = QLabel(
            "1. Click the 'Add Reference Point' button above\n"
            "2. Click on the map at your reference location\n"
            "3. Enter a description (e.g., 'SW corner of Section 12')\n"
            "4. The bearing and distance from the reference to your claims will be calculated\n\n"
            "Good reference points include:\n"
            "• Survey monuments and section corners\n"
            "• Road intersections\n"
            "• Named peaks or natural features\n"
            "• Permanent man-made structures"
        )
        instructions.setWordWrap(True)
        instructions.setStyleSheet("color: #1e40af;")
        layout.addWidget(instructions)

        skip_note = QLabel(
            "Note: This step is optional. Click 'Next' to continue without reference points."
        )
        skip_note.setWordWrap(True)
        skip_note.setStyleSheet("color: #6b7280; font-style: italic;")
        layout.addWidget(skip_note)

        return frame

    def _on_reference_points_changed(self):
        """Handle reference points list change."""
        if hasattr(self, 'reference_widget'):
            self.state.reference_points = self.reference_widget.get_reference_points()
            self.emit_validation_changed()

    # =========================================================================
    # ClaimsStepBase Implementation
    # =========================================================================

    def validate(self) -> List[str]:
        """Validate the step."""
        # Reference points are optional, so always valid
        return []

    def on_enter(self):
        """Called when step becomes active."""
        # Set the EPSG from project state so coordinates transform to UTM
        if hasattr(self, 'reference_widget') and self.state.project_epsg:
            self.reference_widget.set_layer_epsg(self.state.project_epsg)

        # Set up GeoPackage storage for reference points layer
        self._setup_reference_storage()

        self.load_state()

    def _setup_reference_storage(self):
        """Configure reference widget to use GeoPackage storage."""
        from ...utils.logger import PluginLogger
        logger = PluginLogger.get_logger()

        if not hasattr(self, 'reference_widget'):
            logger.warning("[Step3] reference_widget not available")
            return

        if not self.state.geopackage_path:
            logger.warning("[Step3] No geopackage_path in state")
            return

        try:
            from ...managers.claims_storage_manager import ClaimsStorageManager
            storage_manager = ClaimsStorageManager()
            storage_manager.set_current_geopackage(self.state.geopackage_path)

            # Get project name from the claims layer name (e.g., "Initial Layout [GE1 Lode Claims]")
            # This is more reliable than metadata which might not be set
            project_name = None
            if self.state.claims_layer:
                layer_name = self.state.claims_layer.name()
                # Extract project name from "Initial Layout [Project Name]" format
                if '[' in layer_name and ']' in layer_name:
                    start = layer_name.find('[') + 1
                    end = layer_name.find(']')
                    project_name = layer_name[start:end]

            # Fall back to metadata if no layer name available
            if not project_name:
                metadata = storage_manager.load_metadata(self.state.geopackage_path)
                project_name = metadata.get('project_name', None)

            logger.info(
                f"[Step3] Setting up reference storage: path={self.state.geopackage_path}, "
                f"project_name={project_name}, epsg={self.state.project_epsg}"
            )

            # Configure widget to use GeoPackage storage (auto-loads layer)
            self.reference_widget.set_storage(
                storage_manager,
                self.state.geopackage_path,
                project_name=project_name
            )

        except Exception as e:
            # Log error but continue - will fall back to memory layer
            logger.error(f"[Step3] Failed to set up GeoPackage storage: {e}")

    def on_leave(self):
        """Called when leaving step."""
        self.save_state()

        # Persist to GeoPackage if available
        if self.state.geopackage_path:
            self.state.save_to_geopackage()

        # Deactivate any active map tools
        try:
            from qgis.utils import iface
            if hasattr(self, 'reference_widget') and hasattr(self.reference_widget, '_map_tool'):
                if self.reference_widget._map_tool:
                    iface.mapCanvas().unsetMapTool(self.reference_widget._map_tool)
        except Exception:
            pass

    def save_state(self):
        """Save widget state to shared state."""
        if hasattr(self, 'reference_widget'):
            self.state.reference_points = self.reference_widget.get_reference_points()

    def load_state(self):
        """Load widget state from shared state."""
        from ...utils.logger import PluginLogger
        logger = PluginLogger.get_logger()

        if not hasattr(self, 'reference_widget'):
            return

        # Try to load reference points from GeoPackage first
        gpkg_points = []
        if self.state.geopackage_path:
            try:
                from ...managers.claims_storage_manager import ClaimsStorageManager
                storage_manager = ClaimsStorageManager()
                gpkg_points = storage_manager.load_reference_points(self.state.geopackage_path)
                if gpkg_points:
                    logger.info(
                        f"[Step3] Loaded {len(gpkg_points)} reference points from GeoPackage"
                    )
            except Exception as e:
                logger.warning(f"[Step3] Could not load points from GeoPackage: {e}")

        # If state has reference points, use those
        if self.state.reference_points:
            # Use set_reference_points to replace widget's list entirely
            self.reference_widget.set_reference_points(self.state.reference_points)
            logger.debug(
                f"[Step3] Loaded {len(self.state.reference_points)} reference points from state"
            )
        elif gpkg_points:
            # If state is empty but GeoPackage has points, use those
            self.reference_widget.set_reference_points(gpkg_points)
            self.state.reference_points = gpkg_points
            logger.info(
                f"[Step3] Populated state from GeoPackage with {len(gpkg_points)} points"
            )
        else:
            # If state is empty but widget has points, save widget points to state
            # (handles case where points were added but state wasn't updated)
            widget_points = self.reference_widget.get_reference_points()
            if widget_points:
                self.state.reference_points = widget_points
                logger.debug(
                    f"[Step3] Populated state from widget with {len(widget_points)} points"
                )
