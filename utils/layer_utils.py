# -*- coding: utf-8 -*-
"""
Layer utility functions for the geodb QGIS plugin.

Provides common layer validation and management functions used across
the claims workflow steps.
"""
from typing import Optional

from qgis.core import QgsVectorLayer, QgsProject

try:
    from qgis.PyQt import sip
except ImportError:
    import sip


def is_layer_valid(layer: Optional[QgsVectorLayer]) -> bool:
    """
    Check if a QgsVectorLayer reference is still valid and usable.

    In PyQGIS, when a layer is deleted from QGIS (e.g., user manually
    removes it from the Layers panel), the Python object still exists
    but the underlying C++ object is destroyed. Accessing such an object
    raises RuntimeError.

    This function safely checks whether a layer reference can still be used.

    Args:
        layer: The layer to check (can be None)

    Returns:
        True if the layer is valid and can be used, False otherwise

    Example:
        >>> from geodb.utils.layer_utils import is_layer_valid
        >>> if is_layer_valid(self.state.claims_layer):
        ...     # Safe to use the layer
        ...     for feature in self.state.claims_layer.getFeatures():
        ...         pass
    """
    if layer is None:
        return False
    try:
        # sip.isdeleted() checks if the C++ object has been deleted
        if sip.isdeleted(layer):
            return False
        # Also verify the layer is still valid by accessing a property
        # This catches cases where the object exists but is in an invalid state
        _ = layer.isValid()
        return True
    except (RuntimeError, ReferenceError):
        return False


def is_layer_in_project(layer: Optional[QgsVectorLayer]) -> bool:
    """
    Check if a layer is valid AND still present in the current QGIS project.

    This is a stricter check than is_layer_valid() - it also verifies
    the layer hasn't been removed from the project's layer registry.

    Args:
        layer: The layer to check (can be None)

    Returns:
        True if the layer is valid and in the project, False otherwise

    Example:
        >>> from geodb.utils.layer_utils import is_layer_in_project
        >>> if not is_layer_in_project(self._layer):
        ...     self._layer = None  # Clear stale reference
        ...     self._layer = self._create_layer()  # Recreate
    """
    if not is_layer_valid(layer):
        return False
    try:
        # Check if the layer is registered in the current project
        return QgsProject.instance().mapLayer(layer.id()) is not None
    except (RuntimeError, ReferenceError):
        return False


def get_valid_layer_or_none(layer: Optional[QgsVectorLayer]) -> Optional[QgsVectorLayer]:
    """
    Return the layer if valid, or None if it's stale/deleted.

    Convenience function for clearing stale references inline.

    Args:
        layer: The layer to check (can be None)

    Returns:
        The same layer if valid, None otherwise

    Example:
        >>> self._layer = get_valid_layer_or_none(self._layer)
        >>> if self._layer is None:
        ...     self._layer = self._create_layer()
    """
    return layer if is_layer_valid(layer) else None


def get_layer_in_project_or_none(layer: Optional[QgsVectorLayer]) -> Optional[QgsVectorLayer]:
    """
    Return the layer if valid and in project, or None if stale/removed.

    Convenience function for clearing stale references when the layer
    must also be present in the QGIS project.

    Args:
        layer: The layer to check (can be None)

    Returns:
        The same layer if valid and in project, None otherwise

    Example:
        >>> self._layer = get_layer_in_project_or_none(self._layer)
        >>> if self._layer is None:
        ...     self._layer = self._load_or_create_layer()
    """
    return layer if is_layer_in_project(layer) else None
