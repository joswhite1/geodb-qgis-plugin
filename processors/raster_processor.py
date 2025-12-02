# -*- coding: utf-8 -*-
"""
Raster processor for handling GeoTIFF/DEM file downloads and QGIS layer creation.

Supports downloading georeferenced raster files from the API and loading them
as raster layers in QGIS.
"""
import os
import tempfile
from pathlib import Path
from typing import Optional, Callable, Dict, Any, List, Tuple
from urllib.parse import urlparse, urljoin

from qgis.PyQt.QtCore import QUrl, QEventLoop, QByteArray, QFile, QIODevice
from qgis.PyQt.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply
from qgis.core import (
    QgsProject,
    QgsRasterLayer,
    QgsCoordinateReferenceSystem,
    QgsNetworkAccessManager,
)

from ..utils.logger import PluginLogger


class RasterProcessor:
    """
    Processor for downloading and loading raster files (GeoTIFFs, DEMs).

    Handles:
    - File downloads from pre-signed S3 URLs
    - Local caching to avoid re-downloads
    - QGIS raster layer creation
    - Layer styling based on file type
    """

    # File extensions that are raster types
    RASTER_EXTENSIONS = {'.tif', '.tiff', '.img', '.hdr', '.ers', '.grd'}

    # Category codes for DEMs (these get hillshade styling)
    DEM_CATEGORIES = {'DM'}

    def __init__(self, cache_dir: Optional[str] = None, base_url: Optional[str] = None):
        """
        Initialize raster processor.

        Args:
            cache_dir: Optional directory for caching downloaded files.
                      If None, uses a temp directory in the user's profile.
            base_url: Optional base URL for converting relative URLs to absolute.
                     Required for local development where file_url is like '/media/...'
                     In production, file_url contains full S3 presigned URLs.
        """
        self.logger = PluginLogger.get_logger()
        self.network_manager = QgsNetworkAccessManager.instance()
        self.base_url = base_url

        # Set up cache directory
        if cache_dir:
            self.cache_dir = Path(cache_dir)
        else:
            # Use QGIS profile directory for persistent cache
            from qgis.core import QgsApplication
            profile_dir = QgsApplication.qgisSettingsDirPath()
            self.cache_dir = Path(profile_dir) / 'geodb_cache' / 'rasters'

        # Ensure cache directory exists
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.logger.info(f"Raster cache directory: {self.cache_dir}")
        if base_url:
            self.logger.info(f"Using base URL for relative paths: {base_url}")

    def _resolve_url(self, url: str) -> str:
        """
        Resolve a URL, converting relative URLs to absolute if base_url is set.

        In local development, the API returns relative URLs like '/media/PROJECT/file.tif'
        In production, the API returns absolute presigned S3 URLs.

        Args:
            url: The URL from the API (may be relative or absolute)

        Returns:
            Absolute URL ready for download
        """
        if not url:
            return url

        # Check if URL is already absolute
        parsed = urlparse(url)
        if parsed.scheme in ('http', 'https'):
            return url

        # Relative URL - need to prepend base_url
        if self.base_url:
            # urljoin handles the path joining correctly
            absolute_url = urljoin(self.base_url, url)
            self.logger.debug(f"Resolved relative URL: {url} -> {absolute_url}")
            return absolute_url
        else:
            self.logger.warning(
                f"Relative URL '{url}' but no base_url configured. "
                "Set base_url for local development."
            )
            return url

    def process_project_files(
        self,
        project_files: List[Dict[str, Any]],
        project_name: str,
        progress_callback: Optional[Callable[[int, str], None]] = None
    ) -> Dict[str, Any]:
        """
        Process a list of project files - download and load as layers.

        Args:
            project_files: List of ProjectFile records from API
            project_name: Name of the project (for layer naming/grouping)
            progress_callback: Optional callback(progress_percent, status_message)

        Returns:
            Dictionary with results: {
                'loaded': number of layers loaded,
                'skipped': number of non-raster files skipped,
                'errors': list of error messages,
                'layers': list of created layer names
            }
        """
        loaded = 0
        skipped = 0
        errors = []
        layer_names = []

        total = len(project_files)

        for i, pf in enumerate(project_files):
            if progress_callback:
                progress = int((i / total) * 100)
                progress_callback(progress, f"Processing {pf.get('name', 'file')}...")

            try:
                # Check if this is a raster file
                if not pf.get('is_raster', False):
                    self.logger.debug(f"Skipping non-raster file: {pf.get('name')}")
                    skipped += 1
                    continue

                # Get the file URL and resolve it (handle relative URLs in local dev)
                file_url = pf.get('file_url')
                self.logger.info(f"File '{pf.get('name')}' - raw file_url: {file_url}")
                if not file_url:
                    errors.append(f"No file URL for: {pf.get('name')}")
                    continue

                # Resolve relative URLs to absolute
                resolved_url = self._resolve_url(file_url)
                self.logger.info(f"File '{pf.get('name')}' - resolved URL: {resolved_url}")

                # Download the file
                local_path = self.download_file(
                    url=resolved_url,
                    file_id=pf.get('id'),
                    filename=pf.get('name'),
                    progress_callback=lambda p: progress_callback(
                        int((i / total) * 100) + int((p / total) * 0.5),
                        f"Downloading {pf.get('name')}..."
                    ) if progress_callback else None
                )

                if not local_path:
                    errors.append(f"Failed to download: {pf.get('name')}")
                    continue

                # Load as QGIS layer
                layer = self.load_raster_layer(
                    file_path=local_path,
                    layer_name=f"{project_name}_{pf.get('name')}",
                    crs=pf.get('crs'),
                    category=pf.get('category')
                )

                if layer and layer.isValid():
                    loaded += 1
                    layer_names.append(layer.name())
                else:
                    errors.append(f"Failed to load layer: {pf.get('name')}")

            except Exception as e:
                errors.append(f"{pf.get('name')}: {str(e)}")
                self.logger.error(f"Error processing project file {pf.get('name')}: {e}")

        if progress_callback:
            progress_callback(100, "Processing complete")

        return {
            'loaded': loaded,
            'skipped': skipped,
            'errors': errors,
            'layers': layer_names
        }

    def download_file(
        self,
        url: str,
        file_id: int,
        filename: str,
        progress_callback: Optional[Callable[[int], None]] = None
    ) -> Optional[str]:
        """
        Download a file from URL to local cache.

        Args:
            url: Pre-signed URL to download from
            file_id: Unique ID for cache key
            filename: Original filename (for extension detection)
            progress_callback: Optional callback(progress_percent)

        Returns:
            Path to downloaded file, or None on failure
        """
        # Determine file extension from filename or URL
        ext = Path(filename).suffix.lower() if filename else ''
        if not ext:
            parsed = urlparse(url)
            ext = Path(parsed.path).suffix.lower()
        if not ext:
            ext = '.tif'  # Default to .tif

        # Create cache path using file_id for uniqueness
        cache_filename = f"pf_{file_id}{ext}"
        cache_path = self.cache_dir / cache_filename

        # Check if already cached
        if cache_path.exists():
            self.logger.info(f"Using cached file: {cache_path}")
            return str(cache_path)

        self.logger.info(f"Downloading file to: {cache_path}")

        try:
            # Create request
            request = QNetworkRequest(QUrl(url))
            request.setAttribute(
                QNetworkRequest.RedirectPolicyAttribute,
                QNetworkRequest.NoLessSafeRedirectPolicy
            )

            # Execute download synchronously
            loop = QEventLoop()
            reply = self.network_manager.get(request)

            # Track download progress
            if progress_callback:
                def on_progress(received, total):
                    if total > 0:
                        progress = int((received / total) * 100)
                        progress_callback(progress)
                reply.downloadProgress.connect(on_progress)

            reply.finished.connect(loop.quit)
            loop.exec_()

            # Check for errors
            if reply.error() != QNetworkReply.NoError:
                error_msg = reply.errorString()
                self.logger.error(f"Download failed: {error_msg}")
                reply.deleteLater()
                return None

            # Write to file
            data = reply.readAll()
            reply.deleteLater()

            # Ensure parent directory exists
            cache_path.parent.mkdir(parents=True, exist_ok=True)

            with open(cache_path, 'wb') as f:
                f.write(bytes(data))

            self.logger.info(f"Downloaded {len(data)} bytes to {cache_path}")
            return str(cache_path)

        except Exception as e:
            self.logger.error(f"Error downloading file: {e}")
            return None

    def load_raster_layer(
        self,
        file_path: str,
        layer_name: str,
        crs: Optional[str] = None,
        category: Optional[str] = None
    ) -> Optional[QgsRasterLayer]:
        """
        Load a raster file as a QGIS layer.

        Args:
            file_path: Path to the raster file
            layer_name: Name for the layer
            crs: Optional CRS string (e.g., 'EPSG:4326')
            category: Optional category code for styling (e.g., 'DM' for DEM)

        Returns:
            QgsRasterLayer if successful, None otherwise
        """
        if not os.path.exists(file_path):
            self.logger.error(f"File not found: {file_path}")
            return None

        # Create raster layer
        layer = QgsRasterLayer(file_path, layer_name)

        if not layer.isValid():
            self.logger.error(f"Failed to load raster layer: {layer_name}")
            return None

        # Set CRS if provided and not already set
        if crs and not layer.crs().isValid():
            layer_crs = QgsCoordinateReferenceSystem(crs)
            if layer_crs.isValid():
                layer.setCrs(layer_crs)
                self.logger.info(f"Set layer CRS to: {crs}")

        # Apply styling based on category
        self._apply_raster_style(layer, category)

        # Add to QGIS project
        QgsProject.instance().addMapLayer(layer)
        self.logger.info(f"Added raster layer: {layer_name}")

        return layer

    def _apply_raster_style(
        self,
        layer: QgsRasterLayer,
        category: Optional[str] = None
    ) -> None:
        """
        Apply appropriate styling to a raster layer based on its category.

        Args:
            layer: The raster layer to style
            category: Category code (e.g., 'DM' for DEM)
        """
        if not layer or not layer.isValid():
            return

        try:
            from qgis.core import (
                QgsRasterShader,
                QgsColorRampShader,
                QgsSingleBandPseudoColorRenderer,
                QgsStyle,
            )

            # Get band count for styling decisions
            band_count = layer.bandCount()

            if band_count == 1:
                # Single-band raster - apply color ramp
                if category in self.DEM_CATEGORIES:
                    # DEM: Apply terrain/elevation color ramp
                    self._apply_dem_style(layer)
                else:
                    # Other single-band: Apply grayscale stretch
                    self._apply_singleband_gray_style(layer)
            else:
                # Multi-band raster - use default RGB display
                # QGIS handles this automatically
                pass

        except Exception as e:
            self.logger.warning(f"Failed to apply raster style: {e}")

    def _apply_dem_style(self, layer: QgsRasterLayer) -> None:
        """Apply elevation-appropriate styling to a DEM layer."""
        try:
            from qgis.core import (
                QgsRasterShader,
                QgsColorRampShader,
                QgsSingleBandPseudoColorRenderer,
                QgsGradientColorRamp,
            )
            from qgis.PyQt.QtGui import QColor

            # Get min/max elevation values
            stats = layer.dataProvider().bandStatistics(1)
            min_val = stats.minimumValue
            max_val = stats.maximumValue

            # Create color ramp shader
            shader = QgsRasterShader()
            color_ramp_shader = QgsColorRampShader()
            color_ramp_shader.setColorRampType(QgsColorRampShader.Interpolated)

            # Create elevation color ramp (green -> yellow -> brown -> white)
            color_ramp_items = [
                QgsColorRampShader.ColorRampItem(min_val, QColor(34, 139, 34), f"{min_val:.0f}"),
                QgsColorRampShader.ColorRampItem(
                    min_val + (max_val - min_val) * 0.25,
                    QColor(154, 205, 50),
                    f"{min_val + (max_val - min_val) * 0.25:.0f}"
                ),
                QgsColorRampShader.ColorRampItem(
                    min_val + (max_val - min_val) * 0.5,
                    QColor(210, 180, 140),
                    f"{min_val + (max_val - min_val) * 0.5:.0f}"
                ),
                QgsColorRampShader.ColorRampItem(
                    min_val + (max_val - min_val) * 0.75,
                    QColor(139, 90, 43),
                    f"{min_val + (max_val - min_val) * 0.75:.0f}"
                ),
                QgsColorRampShader.ColorRampItem(max_val, QColor(255, 255, 255), f"{max_val:.0f}"),
            ]

            color_ramp_shader.setColorRampItemList(color_ramp_items)
            shader.setRasterShaderFunction(color_ramp_shader)

            # Create and set renderer
            renderer = QgsSingleBandPseudoColorRenderer(
                layer.dataProvider(),
                1,  # Band 1
                shader
            )
            layer.setRenderer(renderer)
            layer.triggerRepaint()

            self.logger.info(f"Applied DEM style to layer (range: {min_val:.1f} - {max_val:.1f})")

        except Exception as e:
            self.logger.warning(f"Failed to apply DEM style: {e}")

    def _apply_singleband_gray_style(self, layer: QgsRasterLayer) -> None:
        """Apply grayscale styling to a single-band raster."""
        try:
            from qgis.core import (
                QgsSingleBandGrayRenderer,
                QgsContrastEnhancement,
            )

            renderer = QgsSingleBandGrayRenderer(layer.dataProvider(), 1)

            # Apply contrast enhancement
            stats = layer.dataProvider().bandStatistics(1)
            enhancement = QgsContrastEnhancement(layer.dataProvider().dataType(1))
            enhancement.setContrastEnhancementAlgorithm(
                QgsContrastEnhancement.StretchToMinimumMaximum
            )
            enhancement.setMinimumValue(stats.minimumValue)
            enhancement.setMaximumValue(stats.maximumValue)

            renderer.setContrastEnhancement(enhancement)
            layer.setRenderer(renderer)
            layer.triggerRepaint()

            self.logger.info("Applied grayscale style to layer")

        except Exception as e:
            self.logger.warning(f"Failed to apply grayscale style: {e}")

    def clear_cache(self, older_than_days: Optional[int] = None) -> int:
        """
        Clear the raster cache.

        Args:
            older_than_days: Only delete files older than this many days.
                            If None, deletes all cached files.

        Returns:
            Number of files deleted
        """
        import time

        deleted = 0
        cutoff_time = None

        if older_than_days is not None:
            cutoff_time = time.time() - (older_than_days * 24 * 60 * 60)

        for file_path in self.cache_dir.glob('*'):
            if file_path.is_file():
                if cutoff_time is None or file_path.stat().st_mtime < cutoff_time:
                    try:
                        file_path.unlink()
                        deleted += 1
                    except Exception as e:
                        self.logger.warning(f"Failed to delete cache file {file_path}: {e}")

        self.logger.info(f"Cleared {deleted} files from raster cache")
        return deleted

    def get_cache_size(self) -> Tuple[int, int]:
        """
        Get cache statistics.

        Returns:
            Tuple of (file_count, total_bytes)
        """
        file_count = 0
        total_bytes = 0

        for file_path in self.cache_dir.glob('*'):
            if file_path.is_file():
                file_count += 1
                total_bytes += file_path.stat().st_size

        return file_count, total_bytes
