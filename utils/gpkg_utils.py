# -*- coding: utf-8 -*-
"""
GeoPackage utility functions shared across the plugin.

Provides standalone functions for:
- Saving QGIS layer styles into a GeoPackage's layer_styles table
- Downloading a GeoPackage from the server to a local cache directory
"""
import os
import logging
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, urljoin

from qgis.core import (
    QgsProject, QgsVectorLayer, QgsBlockingNetworkRequest,
)
from qgis.PyQt.QtCore import QUrl
from qgis.PyQt.QtNetwork import QNetworkRequest


logger = logging.getLogger(__name__)


def save_styles_to_geopackage(gpkg_path: str) -> int:
    """
    Save current QGIS styles for all layers sourced from the given GeoPackage.

    Iterates all vector layers in the current QGIS project, finds those whose
    source points to the specified GeoPackage, and calls saveStyleToDatabase()
    to write styles into the GeoPackage's layer_styles table.

    This is non-destructive and idempotent.

    Args:
        gpkg_path: Absolute path to the GeoPackage file.

    Returns:
        Number of styles successfully saved.
    """
    saved = 0
    gpkg_norm = os.path.normpath(gpkg_path).replace('\\', '/')

    for layer_id, layer in QgsProject.instance().mapLayers().items():
        if not isinstance(layer, QgsVectorLayer):
            continue

        source = layer.source()
        source_path = source.split('|')[0]
        source_norm = os.path.normpath(source_path).replace('\\', '/')

        if source_norm != gpkg_norm:
            continue
        if '|layername=' not in source:
            continue

        # saveStyleToDatabase returns (message, success_bool)
        result = layer.saveStyleToDatabase(
            '',               # Empty name = default style
            'geodb sync',     # Description
            True,             # Use as default style
            ''                # No UI file
        )

        if isinstance(result, tuple):
            msg, success = result
            if success:
                saved += 1
            else:
                logger.warning(
                    "Failed to save style for layer '%s': %s",
                    layer.name(), msg
                )
        else:
            # Older QGIS versions may return differently
            saved += 1

    return saved


def download_geopackage(
    file_id: int,
    file_url: str,
    cache_dir: Path,
    base_url: Optional[str] = None,
    force_refresh: bool = False,
) -> Optional[str]:
    """
    Download a GeoPackage from the server to a local cache directory.

    Uses the pre-signed file_url from the API response. Caches files as
    ``pf_{file_id}.gpkg`` in the given cache directory.

    Args:
        file_id: Server-side ProjectFile ID.
        file_url: Pre-signed URL for the file download.
        cache_dir: Local directory for caching downloaded GeoPackages.
        base_url: Base server URL for resolving relative file_url values.
        force_refresh: If True, re-download even if a cached copy exists.

    Returns:
        Absolute path to the local GeoPackage file, or None on failure.
    """
    if not file_url:
        logger.error("No file_url provided for GeoPackage (id=%s)", file_id)
        return None

    cache_path = cache_dir / f"pf_{file_id}.gpkg"

    if not force_refresh and cache_path.exists():
        logger.info("Using cached GeoPackage: %s", cache_path)
        return str(cache_path)

    # Resolve relative URLs for local dev
    if not urlparse(file_url).scheme:
        if base_url:
            file_url = urljoin(base_url, file_url)
        else:
            logger.error("Relative file_url but no base_url provided")
            return None

    # Download
    request = QNetworkRequest(QUrl(file_url))
    request.setAttribute(
        QNetworkRequest.Attribute.RedirectPolicyAttribute,
        QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy
    )

    blocking_request = QgsBlockingNetworkRequest()
    error_code = blocking_request.get(request, forceRefresh=True)

    if error_code != QgsBlockingNetworkRequest.NoError:
        error_msg = blocking_request.errorMessage()
        logger.error("Download failed for pf_%s: %s", file_id, error_msg)
        return None

    reply = blocking_request.reply()
    data = reply.content()

    if not data or len(data) == 0:
        logger.error("Empty response for pf_%s", file_id)
        return None

    # Write to cache
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with open(cache_path, 'wb') as f:
        f.write(bytes(data))

    logger.info("Downloaded GeoPackage to %s (%d bytes)", cache_path, len(data))
    return str(cache_path)
