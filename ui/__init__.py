# -*- coding: utf-8 -*-
"""UI module for Geodb.io QGIS Plugin."""

from .geodb_modern_dialog import GeodbModernDialog
from .login_dialog import LoginDialog
from .assay_range_dialog import AssayRangeDialog
from .storage_dialog import StorageConfigDialog
from .claims_widget import ClaimsWidget
from .claims_tos_dialog import ClaimsTOSDialog
from .claims_order_dialog import ClaimsOrderDialog
from .claims_order_widget import ClaimsOrderWidget
from .reference_map_tool import ReferenceMapTool, ReferenceInputDialog, ReferencePointsWidget

__all__ = [
    'GeodbModernDialog',
    'LoginDialog',
    'AssayRangeDialog',
    'StorageConfigDialog',
    'ClaimsWidget',
    'ClaimsTOSDialog',
    'ClaimsOrderDialog',
    'ClaimsOrderWidget',
    'ReferenceMapTool',
    'ReferenceInputDialog',
    'ReferencePointsWidget',
]