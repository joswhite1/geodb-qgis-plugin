# -*- coding: utf-8 -*-
"""
Basemaps widget for adding XYZ tile layers and reference layers to QGIS project.

Provides easy access to common basemap providers like ESRI, USGS, and OpenStreetMap,
as well as BLM PLSS cadastral reference layers for mining claims work.
"""
from typing import Optional

from qgis.PyQt.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QSlider, QGroupBox, QMessageBox, QFrame, QScrollArea,
    QSizePolicy, QCheckBox
)
from qgis.PyQt.QtCore import Qt, pyqtSignal
from qgis.core import (
    Qgis, QgsProject, QgsRasterLayer, QgsVectorLayer,
    QgsSimpleLineSymbolLayer, QgsSimpleFillSymbolLayer, QgsMarkerSymbol,
    QgsSymbol, QgsSingleSymbolRenderer, QgsCategorizedSymbolRenderer,
    QgsRendererCategory, QgsPalLayerSettings, QgsVectorLayerSimpleLabeling,
    QgsTextFormat, QgsTextBufferSettings
)
from qgis.PyQt.QtGui import QColor, QFont

from ..utils.crs_utils import extent_to_wgs84
from ..utils.logger import PluginLogger
from ..utils.compat import (
    Qt_ScrollBarAlwaysOff, Qt_ScrollBarAsNeeded, Qt_Horizontal,
    Qt_DashLine, Qt_Checked, QFrame_NoFrame,
    QSizePolicy_Preferred, QSizePolicy_Expanding,
)


# Default basemap providers with XYZ tile URLs
BASEMAP_PROVIDERS = {
    'usgs_imagery': {
        'name': 'USGS Imagery',
        'url': 'https://basemap.nationalmap.gov/arcgis/rest/services/USGSImageryOnly/MapServer/tile/{z}/{y}/{x}',
        'zmin': 0,
        'zmax': 16,
        'attribution': 'USGS The National Map'
    },
    'usgs_topo': {
        'name': 'USGS Topo',
        'url': 'https://basemap.nationalmap.gov/arcgis/rest/services/USGSTopo/MapServer/tile/{z}/{y}/{x}',
        'zmin': 0,
        'zmax': 16,
        'attribution': 'USGS The National Map'
    },
    'usa_topo': {
        'name': 'USA Topo Maps (Historical)',
        'url': 'https://services.arcgisonline.com/ArcGIS/rest/services/USA_Topo_Maps/MapServer/tile/{z}/{y}/{x}',
        'zmin': 0,
        'zmax': 15,
        'attribution': 'Esri, National Geographic, USGS'
    },
    'usgs_shaded_relief': {
        'name': 'USGS Shaded Relief',
        'url': 'https://basemap.nationalmap.gov/arcgis/rest/services/USGSShadedReliefOnly/MapServer/tile/{z}/{y}/{x}',
        'zmin': 0,
        'zmax': 16,
        'attribution': 'USGS The National Map'
    },
    'openstreetmap': {
        'name': 'OpenStreetMap',
        'url': 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
        'zmin': 0,
        'zmax': 19,
        'attribution': 'OpenStreetMap contributors'
    },
}

# geodb.io PLSS Reference Layers
# These stream PLSS data from the geodb.io server with proper labeling
GEODB_PLSS_ENDPOINTS = {
    'sections': '/services/api/plss/sections/',
    'townships': '/services/api/plss/townships/',
}

# PLSS Style presets
PLSS_STYLES = {
    'black': {
        'name': 'Black',
        'line_color': '#000000',
        'line_width': 1.5,
        'label_color': '#000000',
    },
    'white': {
        'name': 'White',
        'line_color': '#FFFFFF',
        'line_width': 1.5,
        'label_color': '#FFFFFF',
    },
    'blue': {
        'name': 'Blue (Townships)',
        'line_color': '#1e3a8a',
        'line_width': 2.0,
        'label_color': '#1e3a8a',
    },
    'green': {
        'name': 'Green (Sections)',
        'line_color': '#059669',
        'line_width': 1.5,
        'label_color': '#059669',
    },
}

# Maximum extent limits for PLSS layers (in kilometers)
# These prevent accidentally loading too much data
PLSS_EXTENT_LIMITS = {
    # Townships: ~10 km per township, allow up to ~150 km extent (~225 townships)
    'townships': {
        'max_extent_km': 150,
        'description': 'Townships (6×6 mile grid)',
        'approx_features_per_100km': 100,
    },
    # Sections: ~1.6 km per section, allow up to ~30 km extent (~350 sections)
    'sections': {
        'max_extent_km': 30,
        'description': 'Sections (1×1 mile grid)',
        'approx_features_per_100km': 3600,
    },
}

# USA Reference Layers - ArcGIS MapServer services for mining claims work
# All use arcgismapserver provider (raster tiles from server with server-side styling)
# These are cached/tiled services that stream properly on pan/zoom
USA_REFERENCE_LAYERS = {
    'federal_lands': {
        'name': 'BLM Surface Management Agency',
        # BLM's cached MapServer - shows all federal land by managing agency
        'url': 'https://gis.blm.gov/arcgis/rest/services/lands/BLM_Natl_SMA_Cached_with_PriUnk/MapServer',
        'type': 'arcgis_mapserver',
        'description': 'BLM, Forest Service, NPS, and other federal land boundaries',
    },
    'wilderness_areas': {
        'name': 'Wilderness Areas',
        # BLM's wilderness and WSA MapServer
        'url': 'https://gis.blm.gov/arcgis/rest/services/lands/BLM_Natl_NLCS_WLD_WSA/MapServer',
        'type': 'arcgis_mapserver',
        'description': 'Wilderness and Wilderness Study Areas',
    },
    'mineral_withdrawals': {
        'name': 'Mineral Withdrawal Areas',
        'url': 'https://apps.fs.usda.gov/arcx/rest/services/EDW/EDW_Withdrawal_01/MapServer',
        'type': 'arcgis_mapserver',
        'description': 'Areas withdrawn from mineral entry',
    },
}

# Federal Lands Agency Colors - matches BLM Map styling
FEDERAL_LANDS_COLORS = {
    'Bureau of Land Management': {'fill': '#FFEB3B', 'stroke': '#F9A825'},
    'Forest Service': {'fill': '#4CAF50', 'stroke': '#2E7D32'},
    'National Park Service': {'fill': '#8BC34A', 'stroke': '#558B2F'},
    'Fish and Wildlife Service': {'fill': '#03A9F4', 'stroke': '#0277BD'},
    'Bureau of Reclamation': {'fill': '#00BCD4', 'stroke': '#00838F'},
    'Department of Defense': {'fill': '#9E9E9E', 'stroke': '#616161'},
    'default': {'fill': '#BDBDBD', 'stroke': '#757575'},
}

# MRDS Commodity Colors - matches BLM Map styling
MRDS_COMMODITY_COLORS = {
    # Precious metals
    'Gold': '#FFD700',
    'Silver': '#C0C0C0',
    'Platinum': '#E5E4E2',
    # Base metals
    'Copper': '#B87333',
    'Lead': '#5D5D5D',
    'Zinc': '#7F7F7F',
    'Iron': '#CC6600',
    'Nickel': '#8B8680',
    # Industrial minerals
    'Uranium': '#4CAF50',
    'Lithium': '#90EE90',
    'Cobalt': '#0047AB',
    'Molybdenum': '#778899',
    'Tungsten': '#424242',
    # Rare earth elements
    'REE': '#9C27B0',
    'Rare Earth': '#9C27B0',
    # Other
    'Fluorite': '#673AB7',
    'Barite': '#795548',
    'Gypsum': '#EEEEEE',
    'Sulfur': '#FFEB3B',
    'Phosphate': '#8BC34A',
    # Default
    'default': '#9E9E9E',
}

# geodb.io MRDS endpoint
GEODB_MRDS_ENDPOINT = '/services/api/mrds/search/'


class BasemapsWidget(QWidget):
    """Widget for adding basemap layers to the QGIS project."""

    # Signal emitted when a basemap is added
    basemap_added = pyqtSignal(str)  # layer name

    def __init__(self, parent: Optional[QWidget] = None):
        """Initialize the basemaps widget."""
        super().__init__(parent)
        self.logger = PluginLogger.get_logger()
        self._setup_ui()

    def _setup_ui(self):
        """Set up the widget UI."""
        # Main layout for this widget - will contain the scroll area
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Scroll area to make content scrollable
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame_NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt_ScrollBarAlwaysOff)
        scroll_area.setVerticalScrollBarPolicy(Qt_ScrollBarAsNeeded)

        # Container widget that goes inside the scroll area
        scroll_content = QWidget()
        scroll_content.setSizePolicy(QSizePolicy_Preferred, QSizePolicy_Preferred)
        layout = QVBoxLayout(scroll_content)
        layout.setSpacing(15)
        layout.setContentsMargins(10, 10, 10, 10)

        # Header
        header = QLabel("Basemaps & Reference Layers")
        header.setStyleSheet("font-size: 16px; font-weight: bold; color: #1f2937;")
        layout.addWidget(header)

        # Description
        desc = QLabel(
            "Add basemap and reference layers to your project. "
            "Layers are added to the 'Base Layers' group at the bottom of the layer stack."
        )
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #6b7280; font-size: 12px; margin-bottom: 10px;")
        layout.addWidget(desc)

        # ==================== Basemaps ====================
        basemap_group = QGroupBox("Basemaps")
        basemap_group.setStyleSheet(self._get_group_style())
        basemap_layout = QVBoxLayout(basemap_group)
        basemap_layout.setSpacing(10)

        # Provider combo + opacity + add button
        provider_layout = QHBoxLayout()
        provider_label = QLabel("Provider:")
        provider_label.setStyleSheet("font-weight: bold;")
        provider_layout.addWidget(provider_label)

        self.provider_combo = QComboBox()
        self.provider_combo.setStyleSheet(self._get_combo_style())
        self.provider_combo.setMinimumWidth(250)
        for key, provider in BASEMAP_PROVIDERS.items():
            self.provider_combo.addItem(provider['name'], key)
        provider_layout.addWidget(self.provider_combo)
        provider_layout.addStretch()
        basemap_layout.addLayout(provider_layout)

        # Opacity slider
        opacity_layout = QHBoxLayout()
        opacity_label = QLabel("Opacity:")
        opacity_label.setStyleSheet("font-weight: bold;")
        opacity_layout.addWidget(opacity_label)

        self.opacity_slider = QSlider(Qt_Horizontal)
        self.opacity_slider.setMinimum(0)
        self.opacity_slider.setMaximum(100)
        self.opacity_slider.setValue(100)
        self.opacity_slider.setStyleSheet(self._get_slider_style())
        self.opacity_slider.valueChanged.connect(self._on_opacity_changed)
        opacity_layout.addWidget(self.opacity_slider)

        self.opacity_label = QLabel("100%")
        self.opacity_label.setMinimumWidth(40)
        opacity_layout.addWidget(self.opacity_label)
        basemap_layout.addLayout(opacity_layout)

        # Add button
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        self.add_btn = QPushButton("Add Basemap")
        self.add_btn.setStyleSheet(self._get_primary_button_style())
        self.add_btn.clicked.connect(self._on_add_clicked)
        btn_layout.addWidget(self.add_btn)

        basemap_layout.addLayout(btn_layout)

        # Quick add buttons
        quick_label = QLabel("Quick add at full opacity:")
        quick_label.setStyleSheet("color: #6b7280; font-size: 12px; margin-top: 4px;")
        basemap_layout.addWidget(quick_label)

        btn_row1 = QHBoxLayout()
        btn_row1.setSpacing(8)

        usgs_imagery_btn = QPushButton("USGS Imagery")
        usgs_imagery_btn.setStyleSheet(self._get_secondary_button_style())
        usgs_imagery_btn.clicked.connect(lambda: self._quick_add('usgs_imagery'))
        btn_row1.addWidget(usgs_imagery_btn)

        usgs_topo_btn = QPushButton("USGS Topo")
        usgs_topo_btn.setStyleSheet(self._get_secondary_button_style())
        usgs_topo_btn.clicked.connect(lambda: self._quick_add('usgs_topo'))
        btn_row1.addWidget(usgs_topo_btn)

        usa_topo_btn = QPushButton("USA Topo (Historical)")
        usa_topo_btn.setStyleSheet(self._get_secondary_button_style())
        usa_topo_btn.clicked.connect(lambda: self._quick_add('usa_topo'))
        btn_row1.addWidget(usa_topo_btn)

        btn_row1.addStretch()
        basemap_layout.addLayout(btn_row1)

        btn_row2 = QHBoxLayout()
        btn_row2.setSpacing(8)

        shaded_relief_btn = QPushButton("Shaded Relief")
        shaded_relief_btn.setStyleSheet(self._get_secondary_button_style())
        shaded_relief_btn.clicked.connect(lambda: self._quick_add('usgs_shaded_relief'))
        btn_row2.addWidget(shaded_relief_btn)

        osm_btn = QPushButton("OpenStreetMap")
        osm_btn.setStyleSheet(self._get_secondary_button_style())
        osm_btn.clicked.connect(lambda: self._quick_add('openstreetmap'))
        btn_row2.addWidget(osm_btn)

        btn_row2.addStretch()
        basemap_layout.addLayout(btn_row2)

        layout.addWidget(basemap_group)

        # ==================== PLSS Grid ====================
        plss_group = QGroupBox("PLSS Grid")
        plss_group.setStyleSheet(self._get_group_style())
        plss_layout = QVBoxLayout(plss_group)
        plss_layout.setSpacing(8)

        plss_desc = QLabel(
            "PLSS township and section grid boundaries for US public land surveys."
        )
        plss_desc.setWordWrap(True)
        plss_desc.setStyleSheet("color: #6b7280; font-size: 12px;")
        plss_layout.addWidget(plss_desc)

        # --- Streaming sub-section ---
        plss_stream_label = QLabel("Streaming (auto-refreshes on pan/zoom)")
        plss_stream_label.setStyleSheet(
            "font-weight: bold; font-size: 12px; color: #374151; margin-top: 4px;"
        )
        plss_layout.addWidget(plss_stream_label)

        plss_stream_desc = QLabel(
            "Townships appear at wider zoom, sections at closer zoom. "
            "Requires QClaims subscription."
        )
        plss_stream_desc.setWordWrap(True)
        plss_stream_desc.setStyleSheet("color: #6b7280; font-size: 11px;")
        plss_layout.addWidget(plss_stream_desc)

        self.plss_stream_toggle = QCheckBox("Show PLSS Grid (Streaming)")
        self.plss_stream_toggle.setEnabled(False)
        self.plss_stream_toggle.stateChanged.connect(self._on_plss_stream_toggle_changed)
        plss_layout.addWidget(self.plss_stream_toggle)

        self.plss_stream_status_label = QLabel("")
        self.plss_stream_status_label.setWordWrap(True)
        self.plss_stream_status_label.setStyleSheet(
            "color: #6b7280; font-size: 11px; font-style: italic;"
        )
        plss_layout.addWidget(self.plss_stream_status_label)

        # Separator
        plss_sep = QFrame()
        plss_sep.setFrameShape(QFrame.HLine)
        plss_sep.setStyleSheet("color: #e5e7eb;")
        plss_layout.addWidget(plss_sep)

        # --- Download sub-section ---
        plss_dl_label = QLabel("Download (snapshot of current extent)")
        plss_dl_label.setStyleSheet(
            "font-weight: bold; font-size: 12px; color: #374151; margin-top: 4px;"
        )
        plss_layout.addWidget(plss_dl_label)

        plss_dl_desc = QLabel(
            "Download PLSS grid for the current map extent as a static layer "
            "with section and township labels."
        )
        plss_dl_desc.setWordWrap(True)
        plss_dl_desc.setStyleSheet("color: #6b7280; font-size: 11px;")
        plss_layout.addWidget(plss_dl_desc)

        # Style selector row
        style_row = QHBoxLayout()
        style_label = QLabel("Line Style:")
        style_label.setStyleSheet("font-weight: bold;")
        style_row.addWidget(style_label)

        self.plss_style_combo = QComboBox()
        self.plss_style_combo.setStyleSheet(self._get_combo_style())
        self.plss_style_combo.setMinimumWidth(150)
        for key, style in PLSS_STYLES.items():
            self.plss_style_combo.addItem(style['name'], key)
        self.plss_style_combo.setCurrentIndex(0)
        style_row.addWidget(self.plss_style_combo)
        style_row.addStretch()
        plss_layout.addLayout(style_row)

        # Layer buttons
        plss_btn_row = QHBoxLayout()
        plss_btn_row.setSpacing(8)

        sections_btn = QPushButton("Download Sections")
        sections_btn.setStyleSheet(self._get_secondary_button_style())
        sections_btn.clicked.connect(lambda: self._add_geodb_plss_layer('sections'))
        plss_btn_row.addWidget(sections_btn)

        townships_btn = QPushButton("Download Townships")
        townships_btn.setStyleSheet(self._get_secondary_button_style())
        townships_btn.clicked.connect(lambda: self._add_geodb_plss_layer('townships'))
        plss_btn_row.addWidget(townships_btn)

        plss_btn_row.addStretch()
        plss_layout.addLayout(plss_btn_row)

        extent_note = QLabel(
            "Note: Sections require zoom ≤30 km, Townships ≤150 km. "
            "Pan/zoom to your area of interest first."
        )
        extent_note.setWordWrap(True)
        extent_note.setStyleSheet("color: #9ca3af; font-size: 11px; font-style: italic;")
        plss_layout.addWidget(extent_note)

        layout.addWidget(plss_group)

        # Store PLSS streaming manager reference
        self._plss_stream_manager = None
        self._plss_stream_has_access = False

        # ==================== Public Lands ====================
        public_lands_group = QGroupBox("Public Lands")
        public_lands_group.setStyleSheet(self._get_group_style())
        public_lands_layout = QVBoxLayout(public_lands_group)
        public_lands_layout.setSpacing(8)

        public_lands_desc = QLabel(
            "Federal land boundaries, wilderness areas, and mineral withdrawal areas."
        )
        public_lands_desc.setWordWrap(True)
        public_lands_desc.setStyleSheet("color: #6b7280; font-size: 12px;")
        public_lands_layout.addWidget(public_lands_desc)

        # --- Streaming sub-section ---
        fed_stream_label = QLabel("Streaming - BLM + Forest Service (auto-refreshes on pan/zoom)")
        fed_stream_label.setStyleSheet(
            "font-weight: bold; font-size: 12px; color: #374151; margin-top: 4px;"
        )
        public_lands_layout.addWidget(fed_stream_label)

        fed_stream_desc = QLabel(
            "Stream BLM and Forest Service land boundaries from geodb.io. "
            "Requires QClaims subscription."
        )
        fed_stream_desc.setWordWrap(True)
        fed_stream_desc.setStyleSheet("color: #6b7280; font-size: 11px;")
        public_lands_layout.addWidget(fed_stream_desc)

        self.federal_lands_toggle = QCheckBox("Show Federal Lands (Streaming)")
        self.federal_lands_toggle.setEnabled(False)
        self.federal_lands_toggle.stateChanged.connect(self._on_federal_lands_toggle_changed)
        public_lands_layout.addWidget(self.federal_lands_toggle)

        # Color legend
        legend_layout = QHBoxLayout()
        legend_layout.setSpacing(12)

        blm_swatch = QLabel()
        blm_swatch.setFixedSize(14, 14)
        blm_swatch.setStyleSheet(
            "background-color: #FFEB3B; border: 1px solid #F9A825; border-radius: 2px;"
        )
        legend_layout.addWidget(blm_swatch)
        legend_layout.addWidget(QLabel("BLM"))

        fs_swatch = QLabel()
        fs_swatch.setFixedSize(14, 14)
        fs_swatch.setStyleSheet(
            "background-color: #4CAF50; border: 1px solid #2E7D32; border-radius: 2px;"
        )
        legend_layout.addWidget(fs_swatch)
        legend_layout.addWidget(QLabel("Forest Service"))

        legend_layout.addStretch()
        public_lands_layout.addLayout(legend_layout)

        self.federal_lands_status_label = QLabel("")
        self.federal_lands_status_label.setWordWrap(True)
        self.federal_lands_status_label.setStyleSheet(
            "color: #6b7280; font-size: 11px; font-style: italic;"
        )
        public_lands_layout.addWidget(self.federal_lands_status_label)

        # Store Federal Lands streaming manager reference
        self._federal_lands_manager = None
        self._federal_lands_has_access = False

        # Separator
        pub_sep = QFrame()
        pub_sep.setFrameShape(QFrame.HLine)
        pub_sep.setStyleSheet("color: #e5e7eb;")
        public_lands_layout.addWidget(pub_sep)

        # --- Esri MapServer sub-section ---
        esri_label = QLabel("Esri/USGS MapServer Layers (server-rendered tiles)")
        esri_label.setStyleSheet(
            "font-weight: bold; font-size: 12px; color: #374151; margin-top: 4px;"
        )
        public_lands_layout.addWidget(esri_label)

        esri_desc = QLabel(
            "Add pre-rendered tile layers from BLM and USFS map services. "
            "No login required."
        )
        esri_desc.setWordWrap(True)
        esri_desc.setStyleSheet("color: #6b7280; font-size: 11px;")
        public_lands_layout.addWidget(esri_desc)

        usa_btn_row1 = QHBoxLayout()
        usa_btn_row1.setSpacing(8)

        federal_lands_btn = QPushButton("Surface Management Agency")
        federal_lands_btn.setToolTip("BLM, Forest Service, NPS, etc. (color-coded by agency)")
        federal_lands_btn.setStyleSheet(self._get_secondary_button_style())
        federal_lands_btn.clicked.connect(lambda: self._add_usa_reference_layer('federal_lands'))
        usa_btn_row1.addWidget(federal_lands_btn)

        wilderness_btn = QPushButton("Wilderness Areas")
        wilderness_btn.setToolTip("Designated wilderness areas (no mining allowed)")
        wilderness_btn.setStyleSheet(self._get_secondary_button_style())
        wilderness_btn.clicked.connect(lambda: self._add_usa_reference_layer('wilderness_areas'))
        usa_btn_row1.addWidget(wilderness_btn)

        usa_btn_row1.addStretch()
        public_lands_layout.addLayout(usa_btn_row1)

        usa_btn_row2 = QHBoxLayout()
        usa_btn_row2.setSpacing(8)

        withdrawal_btn = QPushButton("Mineral Withdrawals")
        withdrawal_btn.setToolTip("Areas withdrawn from mineral entry")
        withdrawal_btn.setStyleSheet(self._get_secondary_button_style())
        withdrawal_btn.clicked.connect(lambda: self._add_usa_reference_layer('mineral_withdrawals'))
        usa_btn_row2.addWidget(withdrawal_btn)

        usa_btn_row2.addStretch()
        public_lands_layout.addLayout(usa_btn_row2)

        layout.addWidget(public_lands_group)

        # ==================== MRDS Mineral Occurrences ====================
        mrds_group = QGroupBox("MRDS Mineral Occurrences")
        mrds_group.setStyleSheet(self._get_group_style())
        mrds_layout = QVBoxLayout(mrds_group)
        mrds_layout.setSpacing(8)

        mrds_desc = QLabel(
            "Add USGS Mineral Resources Data System (MRDS) points from geodb.io. "
            "Points are color-coded by primary commodity. Data frozen at 2011."
        )
        mrds_desc.setWordWrap(True)
        mrds_desc.setStyleSheet("color: #6b7280; font-size: 12px;")
        mrds_layout.addWidget(mrds_desc)

        mrds_btn_row = QHBoxLayout()
        mrds_btn_row.setSpacing(8)

        mrds_btn = QPushButton("Add MRDS Layer")
        mrds_btn.setToolTip("Add mineral occurrence points (requires zoom to ~100km extent)")
        mrds_btn.setStyleSheet(self._get_secondary_button_style())
        mrds_btn.clicked.connect(self._add_mrds_layer)
        mrds_btn_row.addWidget(mrds_btn)

        mrds_btn_row.addStretch()
        mrds_layout.addLayout(mrds_btn_row)

        mrds_note = QLabel(
            "Note: Pan/zoom to your area of interest first (max ~100km extent)."
        )
        mrds_note.setWordWrap(True)
        mrds_note.setStyleSheet("color: #9ca3af; font-size: 11px; font-style: italic;")
        mrds_layout.addWidget(mrds_note)

        layout.addWidget(mrds_group)

        # BLM Mining Claims streaming section
        self.blm_group = QGroupBox("BLM Mining Claims (from geodb.io)")
        self.blm_group.setStyleSheet(self._get_group_style())
        blm_layout = QVBoxLayout(self.blm_group)
        blm_layout.setSpacing(8)

        blm_desc = QLabel(
            "Stream BLM mining claim density per PLSS section. "
            "Sections are colored by number of active claims."
        )
        blm_desc.setWordWrap(True)
        blm_desc.setStyleSheet("color: #6b7280; font-size: 12px;")
        blm_layout.addWidget(blm_desc)

        # Toggle checkbox
        self.blm_toggle = QCheckBox("Show BLM Claims")
        self.blm_toggle.setEnabled(False)
        self.blm_toggle.stateChanged.connect(self._on_blm_toggle_changed)
        blm_layout.addWidget(self.blm_toggle)

        # Filters row
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)

        state_label = QLabel("State:")
        state_label.setStyleSheet("font-weight: bold;")
        filter_row.addWidget(state_label)

        self.blm_state_combo = QComboBox()
        self.blm_state_combo.setStyleSheet(self._get_combo_style())
        self.blm_state_combo.setMinimumWidth(80)
        self.blm_state_combo.addItem("All States", None)
        for st in ['AZ', 'CA', 'CO', 'ID', 'MT', 'NM', 'NV', 'OR', 'SD', 'UT', 'WA', 'WY']:
            self.blm_state_combo.addItem(st, st)
        self.blm_state_combo.setEnabled(False)
        self.blm_state_combo.currentIndexChanged.connect(self._on_blm_filter_changed)
        filter_row.addWidget(self.blm_state_combo)

        type_label = QLabel("Type:")
        type_label.setStyleSheet("font-weight: bold;")
        filter_row.addWidget(type_label)

        self.blm_type_combo = QComboBox()
        self.blm_type_combo.setStyleSheet(self._get_combo_style())
        self.blm_type_combo.setMinimumWidth(100)
        self.blm_type_combo.addItem("All Types", None)
        self.blm_type_combo.addItem("Lode Claim", "Lode")
        self.blm_type_combo.addItem("Placer Claim", "Placer")
        self.blm_type_combo.addItem("Mill Site", "Mill Site")
        self.blm_type_combo.addItem("Tunnel Site", "Tunnel Site")
        self.blm_type_combo.setEnabled(False)
        self.blm_type_combo.currentIndexChanged.connect(self._on_blm_filter_changed)
        filter_row.addWidget(self.blm_type_combo)

        filter_row.addStretch()
        blm_layout.addLayout(filter_row)

        # Snapshot button
        blm_btn_row = QHBoxLayout()
        blm_btn_row.setSpacing(8)

        self.blm_snapshot_btn = QPushButton("Save to Layer")
        self.blm_snapshot_btn.setToolTip("Save current BLM claims view as a persistent layer")
        self.blm_snapshot_btn.setStyleSheet(self._get_secondary_button_style())
        self.blm_snapshot_btn.setEnabled(False)
        self.blm_snapshot_btn.clicked.connect(self._on_blm_snapshot)
        blm_btn_row.addWidget(self.blm_snapshot_btn)

        blm_btn_row.addStretch()
        blm_layout.addLayout(blm_btn_row)

        # Status label
        self.blm_status_label = QLabel("")
        self.blm_status_label.setWordWrap(True)
        self.blm_status_label.setStyleSheet("color: #6b7280; font-size: 11px; font-style: italic;")
        blm_layout.addWidget(self.blm_status_label)

        # Color legend
        legend_row = QHBoxLayout()
        legend_row.setSpacing(4)
        from ..managers.blm_claims_manager import BLM_CLAIM_COUNT_COLORS
        for lower, upper, color_hex, _ in BLM_CLAIM_COUNT_COLORS:
            swatch = QLabel()
            swatch.setFixedSize(14, 14)
            swatch.setStyleSheet(
                f"background-color: {color_hex}; border: 1px solid #8B0000; border-radius: 2px;"
            )
            legend_row.addWidget(swatch)
            if upper >= 999999:
                lbl = QLabel(f"{lower}+")
            elif lower == upper:
                lbl = QLabel(f"{lower}")
            else:
                lbl = QLabel(f"{lower}-{upper}")
            lbl.setStyleSheet("font-size: 10px; color: #6b7280;")
            legend_row.addWidget(lbl)
        legend_row.addStretch()
        blm_layout.addLayout(legend_row)

        layout.addWidget(self.blm_group)

        # Store references for manager wiring
        self._blm_manager = None
        self._blm_has_access = False

        # Add stretch at the end to push content to the top
        layout.addStretch()

        # Complete scroll area setup
        scroll_area.setWidget(scroll_content)
        main_layout.addWidget(scroll_area)

        # Allow the widget to shrink
        self.setSizePolicy(QSizePolicy_Preferred, QSizePolicy_Expanding)
        self.setMinimumHeight(200)  # Reasonable minimum

    def _on_opacity_changed(self, value: int):
        """Handle opacity slider change."""
        self.opacity_label.setText(f"{value}%")

    def _on_add_clicked(self):
        """Handle add basemap button click."""
        provider_key = self.provider_combo.currentData()
        opacity = self.opacity_slider.value() / 100.0
        self._add_basemap(provider_key, opacity)

    def _quick_add(self, provider_key: str):
        """Quick add a basemap at full opacity."""
        self._add_basemap(provider_key, 1.0)

    def _add_basemap(self, provider_key: str, opacity: float = 1.0):
        """
        Add a basemap layer to the project.

        Args:
            provider_key: Key from BASEMAP_PROVIDERS dict
            opacity: Layer opacity (0.0 to 1.0)
        """
        provider = BASEMAP_PROVIDERS.get(provider_key)
        if not provider:
            QMessageBox.warning(self, "Error", f"Unknown provider: {provider_key}")
            return

        try:
            # Build XYZ tile URI
            url = provider['url']
            zmin = provider.get('zmin', 0)
            zmax = provider.get('zmax', 18)

            # QGIS XYZ tile URI format
            uri = f"type=xyz&url={url}&zmin={zmin}&zmax={zmax}"

            # Create raster layer
            layer_name = provider['name']
            layer = QgsRasterLayer(uri, layer_name, 'wms')

            if not layer.isValid():
                QMessageBox.warning(
                    self,
                    "Error",
                    f"Failed to create basemap layer: {layer_name}\n\n"
                    "The tile service may be unavailable."
                )
                return

            # Set opacity
            layer.renderer().setOpacity(opacity)

            # Add to project (don't add to layer tree yet)
            QgsProject.instance().addMapLayer(layer, False)

            # Get or create "Base Layers" group at the bottom
            root = QgsProject.instance().layerTreeRoot()
            base_layers_group = root.findGroup("Base Layers")

            if not base_layers_group:
                # Create the group at the bottom of the layer tree
                base_layers_group = root.addGroup("Base Layers")
                # Move it to the bottom
                clone = base_layers_group.clone()
                root.insertChildNode(-1, clone)
                root.removeChildNode(base_layers_group)
                base_layers_group = root.findGroup("Base Layers")

            # Add layer to the Base Layers group
            base_layers_group.addLayer(layer)

            self.logger.info(f"[BASEMAPS] Added basemap: {layer_name} to 'Base Layers' group (opacity: {opacity:.0%})")
            self.basemap_added.emit(layer_name)

            QMessageBox.information(
                self,
                "Basemap Added",
                f"Added '{layer_name}' to the 'Base Layers' group."
            )

        except Exception as e:
            self.logger.error(f"[BASEMAPS] Failed to add basemap: {e}")
            QMessageBox.critical(self, "Error", f"Failed to add basemap: {e}")

    def _add_geodb_plss_layer(self, layer_type: str):
        """
        Add a PLSS layer from geodb.io server with styling and labels.

        The layer is loaded into a memory layer with spatial indexing to prevent
        QGIS from freezing when interacting with the layer (right-click, etc.).

        Args:
            layer_type: 'sections' or 'townships'
        """
        from qgis.utils import iface
        from qgis.core import (
            QgsCoordinateReferenceSystem, QgsCoordinateTransform,
            QgsApplication, QgsFeature
        )

        try:
            # Get current map extent for bbox
            if not iface or not iface.mapCanvas():
                QMessageBox.warning(
                    self,
                    "No Map",
                    "Please open a map view and navigate to your area of interest first."
                )
                return

            canvas = iface.mapCanvas()
            extent = canvas.extent()
            map_crs = canvas.mapSettings().destinationCrs()

            # Check extent size limit
            extent_check = self._check_plss_extent(extent, map_crs, layer_type)
            if not extent_check['ok']:
                QMessageBox.warning(
                    self,
                    "Extent Too Large",
                    extent_check['message']
                )
                return

            # Transform extent to WGS84 for the API (if needed)
            extent = extent_to_wgs84(extent, map_crs)
            if extent is None:
                QMessageBox.warning(
                    self,
                    "Transform Error",
                    "Could not transform the current map extent to WGS84.\n\n"
                    "This can happen when a wide-extent layer (e.g. national basemap) "
                    "is loaded in a local CRS like UTM. Try zooming in or "
                    "switching the project CRS to EPSG:4326."
                )
                return

            # Build bbox string (minx,miny,maxx,maxy)
            bbox = f"{extent.xMinimum()},{extent.yMinimum()},{extent.xMaximum()},{extent.yMaximum()}"

            # Get base URL from config (use production by default)
            # TODO: Get from plugin config for local dev mode support
            base_url = "https://api.geodb.io"

            # Build the GeoJSON URL
            endpoint = GEODB_PLSS_ENDPOINTS.get(layer_type)
            if not endpoint:
                QMessageBox.warning(self, "Error", f"Unknown PLSS layer type: {layer_type}")
                return

            url = f"{base_url}{endpoint}?bbox={bbox}&simplified=true"

            # Layer name based on type
            if layer_type == 'sections':
                layer_name = "PLSS Sections"
            else:
                layer_name = "PLSS Townships"

            # Show loading message
            self.logger.info(f"[BASEMAPS] Loading PLSS {layer_type} from geodb.io...")

            # Create temporary OGR layer to fetch the data
            # This is synchronous but necessary to get the data
            temp_layer = QgsVectorLayer(url, f"temp_{layer_name}", 'ogr')

            # Allow UI to remain responsive during load
            QgsApplication.processEvents()

            if not temp_layer.isValid():
                QMessageBox.warning(
                    self,
                    "Error",
                    f"Failed to load PLSS {layer_type} layer.\n\n"
                    "The geodb.io service may be unavailable or there is no PLSS data "
                    "for the current map extent.\n\n"
                    "Make sure you are viewing an area within the US PLSS system."
                )
                return

            # Get feature count early (while we still have the OGR layer)
            feature_count = temp_layer.featureCount()

            # Create a memory layer with the same structure
            # Memory layers are much faster for QGIS interactions (right-click, etc.)
            geom_type = temp_layer.geometryType()
            if geom_type == 0:  # Point
                geom_str = "Point"
            elif geom_type == 1:  # Line
                geom_str = "LineString"
            elif geom_type == 2:  # Polygon
                geom_str = "Polygon"
            else:
                geom_str = "Polygon"  # Default for PLSS

            # Build field definitions for memory layer URI
            fields = temp_layer.fields()
            field_defs = []
            for field in fields:
                field_type = field.typeName().lower()
                if 'int' in field_type:
                    field_defs.append(f"field={field.name()}:integer")
                elif 'real' in field_type or 'double' in field_type or 'float' in field_type:
                    field_defs.append(f"field={field.name()}:double")
                else:
                    field_defs.append(f"field={field.name()}:string")

            # Create memory layer URI
            mem_uri = f"{geom_str}?crs=EPSG:4326&{'&'.join(field_defs)}"
            layer = QgsVectorLayer(mem_uri, layer_name, 'memory')

            if not layer.isValid():
                self.logger.error("[BASEMAPS] Failed to create memory layer for PLSS")
                # Fall back to using the OGR layer directly
                layer = temp_layer
            else:
                # Copy features from OGR layer to memory layer
                layer.startEditing()
                features = []
                for feat in temp_layer.getFeatures():
                    new_feat = QgsFeature(layer.fields())
                    new_feat.setGeometry(feat.geometry())
                    for field in fields:
                        try:
                            new_feat.setAttribute(field.name(), feat.attribute(field.name()))
                        except Exception:
                            pass
                    features.append(new_feat)
                    # Keep UI responsive during copy
                    if len(features) % 100 == 0:
                        QgsApplication.processEvents()

                layer.addFeatures(features)
                layer.commitChanges()

                # Create spatial index for fast queries (prevents freezing on right-click)
                layer.dataProvider().createSpatialIndex()

                self.logger.info(f"[BASEMAPS] Copied {len(features)} features to memory layer with spatial index")

            # Apply styling
            self._apply_plss_style(layer, layer_type)

            # Apply labeling (with scale dependency)
            self._apply_plss_labels(layer, layer_type)

            # Add to project (don't add to layer tree yet)
            QgsProject.instance().addMapLayer(layer, False)

            # Get or create "Base Layers" group at the bottom
            root = QgsProject.instance().layerTreeRoot()
            base_layers_group = root.findGroup("Base Layers")

            if not base_layers_group:
                # Create the group at the bottom of the layer tree
                base_layers_group = root.addGroup("Base Layers")
                # Move it to the bottom
                clone = base_layers_group.clone()
                root.insertChildNode(-1, clone)
                root.removeChildNode(base_layers_group)
                base_layers_group = root.findGroup("Base Layers")

            # Add layer to the Base Layers group
            base_layers_group.addLayer(layer)

            self.logger.info(
                f"[BASEMAPS] Added PLSS {layer_type} layer with {feature_count} features"
            )
            self.basemap_added.emit(layer_name)

            QMessageBox.information(
                self,
                "PLSS Layer Added",
                f"Added '{layer_name}' with {feature_count} features.\n\n"
                f"Labels show section/township info in the selected style."
            )

        except Exception as e:
            self.logger.error(f"[BASEMAPS] Failed to add PLSS layer: {e}")
            QMessageBox.critical(self, "Error", f"Failed to add PLSS layer: {e}")

    def _check_plss_extent(self, extent, crs, layer_type: str) -> dict:
        """
        Check if the map extent is within acceptable limits for PLSS data.

        Args:
            extent: QgsRectangle of current map extent
            crs: Current map CRS
            layer_type: 'sections' or 'townships'

        Returns:
            dict with 'ok' (bool) and 'message' (str) keys
        """
        from qgis.core import QgsDistanceArea

        limits = PLSS_EXTENT_LIMITS.get(layer_type, {})
        max_extent_km = limits.get('max_extent_km', 100)

        # Calculate extent dimensions in kilometers
        distance_calc = QgsDistanceArea()
        distance_calc.setSourceCrs(crs, QgsProject.instance().transformContext())
        distance_calc.setEllipsoid('WGS84')

        # Get width and height in meters, convert to km
        # Use the center latitude for more accurate calculation
        center_y = (extent.yMinimum() + extent.yMaximum()) / 2

        try:
            # Calculate width (east-west distance)
            from qgis.core import QgsPointXY
            p1 = QgsPointXY(extent.xMinimum(), center_y)
            p2 = QgsPointXY(extent.xMaximum(), center_y)
            width_m = distance_calc.measureLine(p1, p2)
            width_km = width_m / 1000.0

            # Calculate height (north-south distance)
            center_x = (extent.xMinimum() + extent.xMaximum()) / 2
            p3 = QgsPointXY(center_x, extent.yMinimum())
            p4 = QgsPointXY(center_x, extent.yMaximum())
            height_m = distance_calc.measureLine(p3, p4)
            height_km = height_m / 1000.0
        except Exception:
            # Fallback: rough estimate assuming degrees (at ~45° latitude)
            # 1 degree ≈ 111 km latitude, ~78 km longitude
            width_km = (extent.xMaximum() - extent.xMinimum()) * 78
            height_km = (extent.yMaximum() - extent.yMinimum()) * 111

        max_dimension = max(width_km, height_km)

        if max_dimension > max_extent_km:
            layer_desc = limits.get('description', layer_type)
            return {
                'ok': False,
                'message': (
                    f"Current map extent is too large for {layer_desc}.\n\n"
                    f"Current extent: ~{max_dimension:.0f} km\n"
                    f"Maximum allowed: {max_extent_km} km\n\n"
                    f"Please zoom in closer before loading this layer."
                )
            }

        return {'ok': True, 'message': ''}

    def _apply_plss_style(self, layer: QgsVectorLayer, layer_type: str):
        """
        Apply line styling to PLSS layer.

        Args:
            layer: The vector layer to style
            layer_type: 'sections' or 'townships'
        """
        # Get selected style
        style_key = self.plss_style_combo.currentData()
        style = PLSS_STYLES.get(style_key, PLSS_STYLES['black'])

        # Create line symbol
        symbol = QgsSymbol.defaultSymbol(layer.geometryType())
        symbol.deleteSymbolLayer(0)

        line_layer = QgsSimpleLineSymbolLayer()
        line_layer.setColor(QColor(style['line_color']))
        line_layer.setWidth(style['line_width'])

        # Townships get dashed lines
        if layer_type == 'townships':
            line_layer.setPenStyle(Qt_DashLine)

        symbol.appendSymbolLayer(line_layer)

        # Apply renderer
        renderer = QgsSingleSymbolRenderer(symbol)
        layer.setRenderer(renderer)

    def _apply_plss_labels(self, layer: QgsVectorLayer, layer_type: str):
        """
        Apply labeling to PLSS layer with scale-dependent visibility.

        Labels show:
        - Sections: "Sec XX\nT##N\nR##E"
        - Townships: "T##N R##E"

        Scale dependency prevents performance issues when zoomed out:
        - Sections: Labels visible only at 1:100,000 or closer
        - Townships: Labels visible only at 1:500,000 or closer

        Args:
            layer: The vector layer to label
            layer_type: 'sections' or 'townships'
        """
        # Get selected style for label color
        style_key = self.plss_style_combo.currentData()
        style = PLSS_STYLES.get(style_key, PLSS_STYLES['black'])

        # Create label settings
        label_settings = QgsPalLayerSettings()
        label_settings.placement = Qgis.LabelPlacement.OverPoint

        # Build label expression based on layer type
        # The geodb.io API returns: township, range, section (for sections)
        # or: township, range (for townships)
        if layer_type == 'sections':
            # Multi-line label: Sec XX \n T##N \n R##E
            label_settings.fieldName = (
                "'Sec ' || \"section\" || '\\n' || \"township\" || '\\n' || \"range\""
            )
            label_settings.isExpression = True
            # Only show section labels when zoomed in (1:100,000 or closer)
            label_settings.scaleVisibility = True
            label_settings.minimumScale = 100000  # Don't show when zoomed out past 1:100,000
            label_settings.maximumScale = 0  # No max (show when zoomed in as far as needed)
        else:
            # Single line: T##N R##E
            label_settings.fieldName = "\"township\" || ' ' || \"range\""
            label_settings.isExpression = True
            # Townships can be labeled at wider zoom levels
            label_settings.scaleVisibility = True
            label_settings.minimumScale = 500000  # Don't show when zoomed out past 1:500,000
            label_settings.maximumScale = 0

        # Text format
        text_format = QgsTextFormat()

        # Font
        font = QFont("Arial", 9)
        font.setBold(True)
        text_format.setFont(font)

        # Color
        text_format.setColor(QColor(style['label_color']))

        # Buffer (halo) for readability - inverse of label color
        buffer_settings = QgsTextBufferSettings()
        buffer_settings.setEnabled(True)
        buffer_settings.setSize(1.5)
        if style['label_color'] == '#FFFFFF':
            buffer_settings.setColor(QColor('#000000'))
        else:
            buffer_settings.setColor(QColor('#FFFFFF'))
        text_format.setBuffer(buffer_settings)

        label_settings.setFormat(text_format)

        # Apply labeling
        labeling = QgsVectorLayerSimpleLabeling(label_settings)
        layer.setLabeling(labeling)
        layer.setLabelsEnabled(True)

    def _add_usa_reference_layer(self, layer_key: str):
        """
        Add a USA reference layer (federal lands, wilderness, mineral withdrawals).

        All layers use arcgismapserver provider for proper tile streaming on pan/zoom.
        Styling comes from the server.

        Args:
            layer_key: Key from USA_REFERENCE_LAYERS dict
        """
        layer_config = USA_REFERENCE_LAYERS.get(layer_key)
        if not layer_config:
            QMessageBox.warning(self, "Error", f"Unknown layer type: {layer_key}")
            return

        try:
            layer_name = layer_config['name']
            url = layer_config['url']

            # Use arcgismapserver provider - streams tiles with server-side rendering
            uri = f"crs='EPSG:3857' url='{url}'"
            layer = QgsRasterLayer(uri, layer_name, 'arcgismapserver')

            if not layer.isValid():
                QMessageBox.warning(
                    self,
                    "Error",
                    f"Failed to load {layer_name}.\n\n"
                    "The service may be unavailable."
                )
                return

            # Add to project
            QgsProject.instance().addMapLayer(layer, False)

            # Get or create "Base Layers" group
            root = QgsProject.instance().layerTreeRoot()
            base_layers_group = root.findGroup("Base Layers")

            if not base_layers_group:
                base_layers_group = root.addGroup("Base Layers")
                clone = base_layers_group.clone()
                root.insertChildNode(-1, clone)
                root.removeChildNode(base_layers_group)
                base_layers_group = root.findGroup("Base Layers")

            base_layers_group.addLayer(layer)

            self.logger.info(f"[BASEMAPS] Added USA reference layer: {layer_name}")
            self.basemap_added.emit(layer_name)

            QMessageBox.information(
                self,
                "Layer Added",
                f"Added '{layer_name}' to the 'Base Layers' group."
            )

        except Exception as e:
            self.logger.error(f"[BASEMAPS] Failed to add USA reference layer: {e}")
            QMessageBox.critical(self, "Error", f"Failed to add layer: {e}")

    def _apply_federal_lands_style(self, layer: QgsVectorLayer):
        """
        Apply categorized styling to federal lands layer by agency.
        """
        from qgis.core import QgsWkbTypes

        # Build categories for each agency
        categories = []
        for agency, colors in FEDERAL_LANDS_COLORS.items():
            if agency == 'default':
                continue

            # Create fill symbol
            symbol = QgsSymbol.defaultSymbol(QgsWkbTypes.PolygonGeometry)
            symbol.deleteSymbolLayer(0)

            fill_layer = QgsSimpleFillSymbolLayer()
            fill_layer.setColor(QColor(colors['fill']))
            fill_layer.setStrokeColor(QColor(colors['stroke']))
            fill_layer.setStrokeWidth(0.5)
            symbol.appendSymbolLayer(fill_layer)

            category = QgsRendererCategory(agency, symbol, agency)
            categories.append(category)

        # Add default category for unknown agencies
        default_colors = FEDERAL_LANDS_COLORS['default']
        default_symbol = QgsSymbol.defaultSymbol(QgsWkbTypes.PolygonGeometry)
        default_symbol.deleteSymbolLayer(0)
        default_fill = QgsSimpleFillSymbolLayer()
        default_fill.setColor(QColor(default_colors['fill']))
        default_fill.setStrokeColor(QColor(default_colors['stroke']))
        default_fill.setStrokeWidth(0.5)
        default_symbol.appendSymbolLayer(default_fill)
        categories.append(QgsRendererCategory('', default_symbol, 'Other'))

        # Create and apply categorized renderer
        renderer = QgsCategorizedSymbolRenderer('Agency', categories)
        layer.setRenderer(renderer)

    def _apply_wilderness_style(self, layer: QgsVectorLayer):
        """
        Apply styling to wilderness areas layer - cyan/teal color.
        """
        from qgis.core import QgsWkbTypes

        symbol = QgsSymbol.defaultSymbol(QgsWkbTypes.PolygonGeometry)
        symbol.deleteSymbolLayer(0)

        fill_layer = QgsSimpleFillSymbolLayer()
        fill_layer.setColor(QColor('#06b6d4'))  # Cyan fill
        fill_layer.setStrokeColor(QColor('#0891b2'))  # Darker cyan stroke
        fill_layer.setStrokeWidth(1.0)
        symbol.appendSymbolLayer(fill_layer)

        renderer = QgsSingleSymbolRenderer(symbol)
        layer.setRenderer(renderer)
        layer.setOpacity(0.4)  # Semi-transparent

    def _add_mrds_layer(self):
        """
        Add MRDS mineral occurrences layer from geodb.io with commodity-based styling.

        The layer is loaded into a memory layer with spatial indexing to prevent
        QGIS from freezing when interacting with the layer (right-click, etc.).
        """
        from qgis.utils import iface
        from qgis.core import (
            QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsApplication,
            QgsFeature
        )

        try:
            # Get current map extent for bbox
            if not iface or not iface.mapCanvas():
                QMessageBox.warning(
                    self,
                    "No Map",
                    "Please open a map view and navigate to your area of interest first."
                )
                return

            canvas = iface.mapCanvas()
            extent = canvas.extent()
            map_crs = canvas.mapSettings().destinationCrs()

            # Check extent size (~100km limit for MRDS)
            extent_check = self._check_extent_size(extent, map_crs, 100)
            if not extent_check['ok']:
                QMessageBox.warning(
                    self,
                    "Extent Too Large",
                    f"Current map extent is too large for MRDS data.\n\n"
                    f"Current extent: ~{extent_check['extent_km']:.0f} km\n"
                    f"Maximum allowed: 100 km\n\n"
                    f"Please zoom in closer before loading this layer."
                )
                return

            # Transform extent to WGS84 for the API
            extent = extent_to_wgs84(extent, map_crs)
            if extent is None:
                QMessageBox.warning(
                    self,
                    "Transform Error",
                    "Could not transform the current map extent to WGS84.\n\n"
                    "This can happen when a wide-extent layer (e.g. national basemap) "
                    "is loaded in a local CRS like UTM. Try zooming in or "
                    "switching the project CRS to EPSG:4326."
                )
                return

            # Build bbox string
            bbox = f"{extent.xMinimum()},{extent.yMinimum()},{extent.xMaximum()},{extent.yMaximum()}"

            # Get base URL
            base_url = "https://api.geodb.io"
            url = f"{base_url}{GEODB_MRDS_ENDPOINT}?bbox={bbox}&limit=2000"

            self.logger.info("[BASEMAPS] Loading MRDS data from geodb.io...")

            # Create temporary OGR layer to fetch the data
            temp_layer = QgsVectorLayer(url, "temp_MRDS", 'ogr')

            # Allow UI to remain responsive during load
            QgsApplication.processEvents()

            if not temp_layer.isValid():
                QMessageBox.warning(
                    self,
                    "Error",
                    "Failed to load MRDS layer.\n\n"
                    "The geodb.io service may be unavailable or there is no MRDS data "
                    "for the current map extent."
                )
                return

            # Get feature count early
            feature_count = temp_layer.featureCount()

            # Create a memory layer with the same structure for better performance
            fields = temp_layer.fields()
            field_defs = []
            for field in fields:
                field_type = field.typeName().lower()
                if 'int' in field_type:
                    field_defs.append(f"field={field.name()}:integer")
                elif 'real' in field_type or 'double' in field_type or 'float' in field_type:
                    field_defs.append(f"field={field.name()}:double")
                else:
                    field_defs.append(f"field={field.name()}:string")

            mem_uri = f"Point?crs=EPSG:4326&{'&'.join(field_defs)}"
            layer = QgsVectorLayer(mem_uri, "MRDS Mineral Occurrences", 'memory')

            if not layer.isValid():
                self.logger.error("[BASEMAPS] Failed to create memory layer for MRDS")
                layer = temp_layer
            else:
                # Copy features from OGR layer to memory layer
                layer.startEditing()
                features = []
                for feat in temp_layer.getFeatures():
                    new_feat = QgsFeature(layer.fields())
                    new_feat.setGeometry(feat.geometry())
                    for field in fields:
                        try:
                            new_feat.setAttribute(field.name(), feat.attribute(field.name()))
                        except Exception:
                            pass
                    features.append(new_feat)
                    if len(features) % 100 == 0:
                        QgsApplication.processEvents()

                layer.addFeatures(features)
                layer.commitChanges()

                # Create spatial index for fast queries
                layer.dataProvider().createSpatialIndex()

                self.logger.info(f"[BASEMAPS] Copied {len(features)} MRDS features to memory layer with spatial index")

            # Apply commodity-based categorized styling
            self._apply_mrds_style(layer)

            # Add to project
            QgsProject.instance().addMapLayer(layer, False)

            # Get or create "Base Layers" group
            root = QgsProject.instance().layerTreeRoot()
            base_layers_group = root.findGroup("Base Layers")

            if not base_layers_group:
                base_layers_group = root.addGroup("Base Layers")
                clone = base_layers_group.clone()
                root.insertChildNode(-1, clone)
                root.removeChildNode(base_layers_group)
                base_layers_group = root.findGroup("Base Layers")

            base_layers_group.addLayer(layer)

            self.logger.info(f"[BASEMAPS] Added MRDS layer with {feature_count} features")
            self.basemap_added.emit("MRDS Mineral Occurrences")

            QMessageBox.information(
                self,
                "MRDS Layer Added",
                f"Added 'MRDS Mineral Occurrences' with {feature_count} features.\n\n"
                f"Points are color-coded by primary commodity."
            )

        except Exception as e:
            self.logger.error(f"[BASEMAPS] Failed to add MRDS layer: {e}")
            QMessageBox.critical(self, "Error", f"Failed to add MRDS layer: {e}")

    def _apply_mrds_style(self, layer: QgsVectorLayer):
        """
        Apply categorized marker styling to MRDS layer by primary commodity.
        """

        # Build categories for each commodity
        categories = []
        for commodity, color in MRDS_COMMODITY_COLORS.items():
            if commodity == 'default':
                continue

            # Create marker symbol
            symbol = QgsMarkerSymbol.createSimple({
                'name': 'circle',
                'size': '3',
                'color': color,
                'outline_color': 'white',
                'outline_width': '0.5'
            })

            category = QgsRendererCategory(commodity, symbol, commodity)
            categories.append(category)

        # Add default category
        default_color = MRDS_COMMODITY_COLORS['default']
        default_symbol = QgsMarkerSymbol.createSimple({
            'name': 'circle',
            'size': '3',
            'color': default_color,
            'outline_color': 'white',
            'outline_width': '0.5'
        })
        categories.append(QgsRendererCategory('', default_symbol, 'Other'))

        # Create and apply categorized renderer
        renderer = QgsCategorizedSymbolRenderer('primary_commodity', categories)
        layer.setRenderer(renderer)

    def _check_extent_size(self, extent, crs, max_extent_km: float) -> dict:
        """
        Check if map extent is within a specified size limit.

        Args:
            extent: QgsRectangle of current map extent
            crs: Current map CRS
            max_extent_km: Maximum allowed extent in kilometers

        Returns:
            dict with 'ok' (bool) and 'extent_km' (float) keys
        """
        from qgis.core import QgsDistanceArea, QgsPointXY

        distance_calc = QgsDistanceArea()
        distance_calc.setSourceCrs(crs, QgsProject.instance().transformContext())
        distance_calc.setEllipsoid('WGS84')

        center_y = (extent.yMinimum() + extent.yMaximum()) / 2

        try:
            p1 = QgsPointXY(extent.xMinimum(), center_y)
            p2 = QgsPointXY(extent.xMaximum(), center_y)
            width_m = distance_calc.measureLine(p1, p2)
            width_km = width_m / 1000.0

            center_x = (extent.xMinimum() + extent.xMaximum()) / 2
            p3 = QgsPointXY(center_x, extent.yMinimum())
            p4 = QgsPointXY(center_x, extent.yMaximum())
            height_m = distance_calc.measureLine(p3, p4)
            height_km = height_m / 1000.0
        except Exception:
            width_km = (extent.xMaximum() - extent.xMinimum()) * 78
            height_km = (extent.yMaximum() - extent.yMinimum()) * 111

        max_dimension = max(width_km, height_km)

        return {
            'ok': max_dimension <= max_extent_km,
            'extent_km': max_dimension
        }

    # ==================== BLM Claims Methods ====================

    def set_blm_manager(self, blm_manager, has_access: bool):
        """Wire up the BLM claims manager and set access state.

        Args:
            blm_manager: BLMClaimsManager instance (or None on logout)
            has_access: Whether user has QClaims enterprise/staff access
        """
        self._blm_manager = blm_manager
        self._blm_has_access = has_access

        if blm_manager is None:
            # Logout state
            self.blm_toggle.setChecked(False)
            self.blm_toggle.setEnabled(False)
            self.blm_state_combo.setEnabled(False)
            self.blm_type_combo.setEnabled(False)
            self.blm_snapshot_btn.setEnabled(False)
            self.blm_status_label.setText("Login required to view BLM claims")
            return

        if not has_access:
            self.blm_toggle.setChecked(False)
            self.blm_toggle.setEnabled(False)
            self.blm_state_combo.setEnabled(False)
            self.blm_type_combo.setEnabled(False)
            self.blm_snapshot_btn.setEnabled(False)
            self.blm_status_label.setText(
                "BLM Claims requires a QClaims subscription. Visit geodb.io for details."
            )
            return

        # Has access — enable controls
        self.blm_toggle.setEnabled(True)
        self.blm_state_combo.setEnabled(True)
        self.blm_type_combo.setEnabled(True)
        self.blm_snapshot_btn.setEnabled(True)
        self.blm_status_label.setText("")

        # Connect manager signals
        blm_manager.status_changed.connect(self._on_blm_status_changed)
        blm_manager.loading_changed.connect(self._on_blm_loading_changed)
        blm_manager.access_denied.connect(self._on_blm_access_denied)

    def _on_blm_toggle_changed(self, state):
        """Handle BLM claims toggle checkbox."""
        if not self._blm_manager:
            return

        if state == Qt_Checked:
            self._blm_manager.enable()
        else:
            self._blm_manager.disable()
            self.blm_status_label.setText("")

    def _on_blm_filter_changed(self):
        """Handle BLM filter combo changes."""
        if not self._blm_manager or not self._blm_manager.is_enabled:
            return

        state = self.blm_state_combo.currentData()
        claim_type = self.blm_type_combo.currentData()
        self._blm_manager.set_filters(state=state, claim_type=claim_type)

    def _on_blm_snapshot(self):
        """Handle Save to Layer button click."""
        if not self._blm_manager:
            return

        layer = self._blm_manager.snapshot_to_layer()
        if layer:
            QMessageBox.information(
                self,
                "Snapshot Saved",
                f"Saved '{layer.name()}' with {layer.featureCount()} sections.\n\n"
                f"The layer has been added to your Layers panel."
            )
        else:
            QMessageBox.warning(
                self,
                "No Data",
                "No BLM claims data to snapshot. Enable the layer and navigate to an area with claims first."
            )

    def _on_blm_status_changed(self, status: str):
        """Update BLM status label."""
        self.blm_status_label.setText(status)

    def _on_blm_loading_changed(self, loading: bool):
        """Update UI for loading state."""
        if loading:
            self.blm_snapshot_btn.setEnabled(False)
        else:
            self.blm_snapshot_btn.setEnabled(self._blm_has_access)

    def _on_blm_access_denied(self, message: str):
        """Handle 403 from server — show upsell in status, not a dialog."""
        self.blm_status_label.setText(
            "BLM Claims requires a QClaims subscription. Visit geodb.io for details."
        )
        self.blm_toggle.setChecked(False)
        self.blm_toggle.setEnabled(False)

    # ==================== PLSS Streaming Methods ====================

    def set_plss_stream_manager(self, plss_manager, has_access: bool):
        """Wire up the PLSS streaming manager and set access state."""
        self._plss_stream_manager = plss_manager
        self._plss_stream_has_access = has_access

        if plss_manager is None:
            # Logout state
            self.plss_stream_toggle.setChecked(False)
            self.plss_stream_toggle.setEnabled(False)
            self.plss_stream_status_label.setText("Login required to stream PLSS grid")
            return

        if not has_access:
            self.plss_stream_toggle.setChecked(False)
            self.plss_stream_toggle.setEnabled(False)
            self.plss_stream_status_label.setText(
                "PLSS streaming requires a QClaims subscription. Visit geodb.io for details."
            )
            return

        # Has access — enable controls
        self.plss_stream_toggle.setEnabled(True)
        self.plss_stream_status_label.setText("")

        # Connect manager signals
        plss_manager.status_changed.connect(self._on_plss_stream_status_changed)
        plss_manager.loading_changed.connect(self._on_plss_stream_loading_changed)
        plss_manager.access_denied.connect(self._on_plss_stream_access_denied)

    def _on_plss_stream_toggle_changed(self, state):
        """Handle PLSS streaming toggle checkbox."""
        if not self._plss_stream_manager:
            return

        if state == Qt_Checked:
            self._plss_stream_manager.enable()
        else:
            self._plss_stream_manager.disable()
            self.plss_stream_status_label.setText("")

    def _on_plss_stream_status_changed(self, status: str):
        """Update PLSS streaming status label."""
        self.plss_stream_status_label.setText(status)

    def _on_plss_stream_loading_changed(self, loading: bool):
        """Update UI for PLSS loading state."""
        pass  # No snapshot button to toggle for PLSS

    def _on_plss_stream_access_denied(self, message: str):
        """Handle 403 from server."""
        self.plss_stream_status_label.setText(
            "PLSS streaming requires a QClaims subscription. Visit geodb.io for details."
        )
        self.plss_stream_toggle.setChecked(False)
        self.plss_stream_toggle.setEnabled(False)

    # ==================== Federal Lands Streaming Methods ====================

    def set_federal_lands_manager(self, manager, has_access: bool):
        """Wire up the Federal Lands streaming manager and set access state."""
        self._federal_lands_manager = manager
        self._federal_lands_has_access = has_access

        if manager is None:
            # Logout state
            self.federal_lands_toggle.setChecked(False)
            self.federal_lands_toggle.setEnabled(False)
            self.federal_lands_status_label.setText("Login required to stream Federal Lands")
            return

        if not has_access:
            self.federal_lands_toggle.setChecked(False)
            self.federal_lands_toggle.setEnabled(False)
            self.federal_lands_status_label.setText(
                "Federal Lands streaming requires a QClaims subscription. Visit geodb.io for details."
            )
            return

        # Has access — enable controls
        self.federal_lands_toggle.setEnabled(True)
        self.federal_lands_status_label.setText("")

        # Connect manager signals
        manager.status_changed.connect(self._on_federal_lands_status_changed)
        manager.loading_changed.connect(self._on_federal_lands_loading_changed)
        manager.access_denied.connect(self._on_federal_lands_access_denied)

    def _on_federal_lands_toggle_changed(self, state):
        """Handle Federal Lands streaming toggle checkbox."""
        if not self._federal_lands_manager:
            return

        if state == Qt_Checked:
            self._federal_lands_manager.enable()
        else:
            self._federal_lands_manager.disable()
            self.federal_lands_status_label.setText("")

    def _on_federal_lands_status_changed(self, status: str):
        """Update Federal Lands streaming status label."""
        self.federal_lands_status_label.setText(status)

    def _on_federal_lands_loading_changed(self, loading: bool):
        """Update UI for Federal Lands loading state."""
        pass

    def _on_federal_lands_access_denied(self, message: str):
        """Handle 403 from server."""
        self.federal_lands_status_label.setText(
            "Federal Lands streaming requires a QClaims subscription. Visit geodb.io for details."
        )
        self.federal_lands_toggle.setChecked(False)
        self.federal_lands_toggle.setEnabled(False)

    # ==================== Styling Helpers ====================

    def _get_group_style(self) -> str:
        """Get group box style."""
        return """
            QGroupBox {
                font-weight: bold;
                border: 1px solid #e5e7eb;
                border-radius: 6px;
                margin-top: 12px;
                padding-top: 10px;
                background-color: #f9fafb;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
                color: #374151;
            }
        """

    def _get_combo_style(self) -> str:
        """Get combo box style."""
        return """
            QComboBox {
                padding: 8px 12px;
                border: 1px solid #d1d5db;
                border-radius: 6px;
                background-color: white;
                font-size: 13px;
            }
            QComboBox:focus {
                border-color: #2563eb;
            }
            QComboBox::drop-down {
                border: none;
                width: 30px;
            }
            QComboBox QAbstractItemView {
                background-color: white;
                border: 1px solid #d1d5db;
                selection-background-color: #2563eb;
                selection-color: white;
            }
            QComboBox QAbstractItemView::item {
                padding: 6px 12px;
                color: #374151;
            }
            QComboBox QAbstractItemView::item:hover {
                background-color: #dbeafe;
                color: #1d4ed8;
            }
        """

    def _get_slider_style(self) -> str:
        """Get slider style."""
        return """
            QSlider::groove:horizontal {
                border: 1px solid #d1d5db;
                height: 8px;
                background: #e5e7eb;
                border-radius: 4px;
            }
            QSlider::handle:horizontal {
                background: #2563eb;
                border: none;
                width: 18px;
                margin: -5px 0;
                border-radius: 9px;
            }
            QSlider::handle:horizontal:hover {
                background: #1d4ed8;
            }
            QSlider::sub-page:horizontal {
                background: #2563eb;
                border-radius: 4px;
            }
        """

    def _get_primary_button_style(self) -> str:
        """Get primary button style."""
        return """
            QPushButton {
                background-color: #2563eb;
                color: white;
                border: none;
                padding: 10px 24px;
                border-radius: 6px;
                font-weight: bold;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #1d4ed8;
            }
            QPushButton:pressed {
                background-color: #1e40af;
            }
        """

    def _get_secondary_button_style(self) -> str:
        """Get secondary button style."""
        return """
            QPushButton {
                background-color: white;
                color: #374151;
                border: 1px solid #d1d5db;
                padding: 8px 16px;
                border-radius: 6px;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #f3f4f6;
                border-color: #9ca3af;
            }
            QPushButton:pressed {
                background-color: #e5e7eb;
            }
        """
