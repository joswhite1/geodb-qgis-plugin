# -*- coding: utf-8 -*-
"""
Step 4: Monument Adjustment

Handles:
- Move monuments (adjust monument inset distance)
- LM corner designation (ID/NM)
- State-specific monument types (AZ endlines, WY sidelines)
"""
from typing import List

from qgis.PyQt.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QGroupBox, QFormLayout,
    QDoubleSpinBox, QComboBox, QFrame, QScrollArea
)

from .step_base import ClaimsStepBase
from ...utils.compat import QFrame_NoFrame
from ...utils.theme import T


class ClaimsStep4Widget(ClaimsStepBase):
    """
    Step 4: Monument Adjustment

    Configure monument positions and state-specific requirements.
    """

    def get_step_title(self) -> str:
        return "Monument Configuration"

    def get_step_description(self) -> str:
        return (
            "Configure monument positions for your claims. The discovery monument is "
            "placed along the centerline at a specified distance (inset). For Idaho and "
            "New Mexico, you can also designate which corner is the LM corner."
        )

    def __init__(self, state, claims_manager, parent=None, data_manager=None):
        super().__init__(state, claims_manager, parent, data_manager=data_manager)
        self._setup_ui()

    def _setup_ui(self):
        """Set up the step UI."""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame_NoFrame)

        scroll_content = QWidget()
        layout = QVBoxLayout(scroll_content)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # Header
        layout.addWidget(self._create_header())

        # Monument Inset Group
        layout.addWidget(self._create_inset_group())

        # LM Corner Group (for ID/NM)
        layout.addWidget(self._create_lm_corner_group())

        # State Requirements Info
        layout.addWidget(self._create_state_info())

        layout.addStretch()

        scroll.setWidget(scroll_content)
        main_layout.addWidget(scroll)

    def _create_inset_group(self) -> QGroupBox:
        """Create the monument inset group."""
        group = QGroupBox("Discovery Monument Position")
        group.setStyleSheet(self._get_group_style())
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        # Info
        info_label = QLabel(
            "The discovery monument is placed along the claim centerline, offset from "
            "the center point. A typical inset is 25 feet from the centerline midpoint."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet(self._get_info_label_style())
        layout.addWidget(info_label)

        # Inset distance
        form = QFormLayout()
        form.setSpacing(8)

        self.inset_spin = QDoubleSpinBox()
        self.inset_spin.setRange(0, 100)
        self.inset_spin.setValue(25)
        self.inset_spin.setSuffix(" feet")
        self.inset_spin.setMinimumWidth(120)
        self.inset_spin.setStyleSheet(self._get_input_style())
        self.inset_spin.valueChanged.connect(self._on_inset_changed)
        form.addRow("Monument Inset:", self.inset_spin)

        layout.addLayout(form)

        # Visual diagram
        diagram_label = QLabel(
            "                    Claim Centerline\n"
            "    ←─────────────────────────────────→\n"
            "                    |←─inset─→|\n"
            "                              ◆ Discovery Monument"
        )
        diagram_label.setStyleSheet(f"""
            font-family: monospace;
            background-color: {T.SURFACE_SUBTLE};
            padding: 12px;
            border-radius: 4px;
            color: {T.TEXT_PRIMARY};
        """)
        layout.addWidget(diagram_label)

        return group

    def _create_lm_corner_group(self) -> QGroupBox:
        """Create the LM corner designation group (for Idaho/New Mexico)."""
        group = QGroupBox("LM Corner Designation (Idaho/New Mexico)")
        group.setStyleSheet(self._get_group_style())
        layout = QVBoxLayout(group)
        layout.setSpacing(8)

        # Info
        info_label = QLabel(
            "In Idaho and New Mexico, you designate which corner of each claim is the "
            "Location Monument (LM) corner. The discovery monument is placed relative to "
            "this corner. Default is Corner 1 (typically southwest).\n\n"
            "Choose \"Cluster LMs\" to let the server pick a shared interior corner "
            "where adjacent claims meet — so a 4-claim intersection gets one survey "
            "point instead of four scattered LMs. Cluster mode prefers shared corners "
            "on public land; if no corner is on public land it slides along an edge "
            "to the nearest public-land position."
        )
        info_label.setWordWrap(True)
        info_label.setStyleSheet(self._get_info_label_style())
        layout.addWidget(info_label)

        # Corner selector. The 'Cluster LMs' entry uses sentinel data=0 — the
        # widget translates that into state.auto_lm_cluster=True + lm_corner=1
        # (the rotation default; the server ignores it in cluster mode).
        form = QFormLayout()
        form.setSpacing(8)

        self.lm_corner_combo = QComboBox()
        self.lm_corner_combo.setStyleSheet(self._get_combo_style())
        self.lm_corner_combo.addItem("Corner 1 (Default)", 1)
        self.lm_corner_combo.addItem("Corner 2", 2)
        self.lm_corner_combo.addItem("Corner 3", 3)
        self.lm_corner_combo.addItem("Corner 4", 4)
        self.lm_corner_combo.addItem("Cluster LMs (smart, ID/NM)", 0)
        self.lm_corner_combo.currentIndexChanged.connect(self._on_lm_corner_changed)
        form.addRow("LM Corner:", self.lm_corner_combo)

        layout.addLayout(form)

        # Note about when this applies
        note_label = QLabel(
            "Note: This setting applies to Idaho and New Mexico claims only. "
            "For other states, the corner numbering is determined by the claim orientation."
        )
        note_label.setWordWrap(True)
        note_label.setStyleSheet(f"color: {T.TEXT_MUTED}; font-style: italic; font-size: 11px;")
        layout.addWidget(note_label)

        return group

    def _create_state_info(self) -> QWidget:
        """Create the state requirements information panel."""
        frame = QFrame()
        frame.setStyleSheet(f"""
            QFrame {{
                background-color: {T.WARNING_BG};
                border: 1px solid {T.WARNING};
                border-radius: 8px;
                padding: 12px;
            }}
        """)
        layout = QVBoxLayout(frame)
        layout.setSpacing(8)

        title = QLabel("State-Specific Monument Requirements")
        title.setStyleSheet(f"font-weight: bold; color: {T.WARNING_TEXT};")
        layout.addWidget(title)

        info = QLabel(
            "Different states have different monument requirements:\n\n"
            "• Arizona: Endline monuments required (center of each 600' side)\n"
            "• Wyoming: Sideline monuments required (center of each 1500' side)\n"
            "• South Dakota: Both sideline AND endline monuments\n"
            "• Nevada/California: Monument description must be provided\n"
            "• Idaho/New Mexico: LM corner designation required\n\n"
            "These additional monuments will be automatically generated during processing "
            "based on the state where your claims are located."
        )
        info.setWordWrap(True)
        info.setStyleSheet(f"color: {T.WARNING_TEXT};")
        layout.addWidget(info)

        return frame

    def _on_inset_changed(self, value: float):
        """Handle inset value change."""
        self.state.monument_inset_ft = value
        self.emit_validation_changed()

    def _on_lm_corner_changed(self, index: int):
        """Handle LM corner selection change.

        Sentinel data=0 means "Cluster LMs": flip auto_lm_cluster on and
        leave lm_corner at 1. Data 1-4 is a literal corner pick — turn
        auto_lm_cluster off so the server uses the corner verbatim.
        """
        corner = self.lm_corner_combo.currentData()
        if corner is None:
            return
        if corner == 0:
            self.state.auto_lm_cluster = True
            self.state.lm_corner = 1
        else:
            self.state.auto_lm_cluster = False
            self.state.lm_corner = corner
        self.emit_validation_changed()

    # =========================================================================
    # ClaimsStepBase Implementation
    # =========================================================================

    def validate(self) -> List[str]:
        """Validate the step."""
        errors = []

        if self.inset_spin.value() <= 0:
            errors.append("Monument inset must be greater than 0")

        return errors

    def on_enter(self):
        """Called when step becomes active."""
        self.load_state()

    def on_leave(self):
        """Called when leaving step."""
        self.save_state()

    def save_state(self):
        """Save widget state to shared state."""
        self.state.monument_inset_ft = self.inset_spin.value()
        # Already updated synchronously by _on_lm_corner_changed; nothing else
        # to do here. (Direct read of currentData would clobber auto_lm_cluster
        # after it was set, since the cluster sentinel is also 0.)

    def load_state(self):
        """Load widget state from shared state."""
        self.inset_spin.setValue(self.state.monument_inset_ft)

        # Set LM corner combo. Cluster mode = data sentinel 0; literal pick =
        # data matches state.lm_corner.
        target_data = 0 if self.state.auto_lm_cluster else self.state.lm_corner
        for i in range(self.lm_corner_combo.count()):
            if self.lm_corner_combo.itemData(i) == target_data:
                self.lm_corner_combo.setCurrentIndex(i)
                break
