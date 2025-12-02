# -*- coding: utf-8 -*-
"""
Resource module compatibility layer.
Qt Designer expects resources_rc, but we use resources.py

When uic.loadUiType() processes UI files, it imports this module
in a context where relative imports fail. We handle both cases.
"""
try:
    from .resources import *
except ImportError:
    # Fallback for UI loader context - use absolute path import
    import os
    import importlib.util

    _this_dir = os.path.dirname(os.path.abspath(__file__))
    _resources_path = os.path.join(_this_dir, 'resources.py')

    _spec = importlib.util.spec_from_file_location('_geodb_resources', _resources_path)
    _module = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_module)

    # Import the key functions that Qt resources need
    qInitResources = _module.qInitResources
    qCleanupResources = _module.qCleanupResources

    # Initialize resources
    qInitResources()