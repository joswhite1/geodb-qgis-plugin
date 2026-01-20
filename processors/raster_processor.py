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
    QgsRectangle,
)

from ..utils.logger import PluginLogger


class RasterProcessor:
    """
    Processor for downloading and loading raster files (GeoTIFFs, DEMs, georeferenced PNGs).

    Handles:
    - File downloads from pre-signed S3 URLs
    - Local caching to avoid re-downloads
    - World file generation for georeferenced images (sketches from mobile app)
    - QGIS raster layer creation
    - Layer styling based on file type
    """

    # File extensions that are raster types
    RASTER_EXTENSIONS = {'.tif', '.tiff', '.img', '.hdr', '.ers', '.grd'}

    # Image extensions that need world files for georeferencing
    IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg'}

    # Category codes for DEMs (these get hillshade styling)
    DEM_CATEGORIES = {'DM'}

    # Category codes for geology sketches (these get transparent background styling)
    SKETCH_CATEGORIES = {'GL'}

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

    def _get_crs_string(
        self,
        project_file: Dict[str, Any],
        georef: Optional[Dict[str, Any]] = None
    ) -> Optional[str]:
        """
        Get CRS string from project file, preferring numeric epsg field.

        The server migration adds a numeric `epsg` field alongside the text `crs` field.
        This method provides backwards compatibility by checking both.

        Priority:
        1. project_file['epsg'] (new numeric field) -> "EPSG:XXXX"
        2. project_file['crs'] (legacy string field, may be "EPSG:XXXX" or Proj4)
        3. georef['epsg'] (from georeferencing data) -> "EPSG:XXXX"
        4. None (let QGIS use layer's embedded CRS)

        Args:
            project_file: ProjectFile dict from API
            georef: Optional georeferencing dict (for sketches)

        Returns:
            CRS string suitable for QgsCoordinateReferenceSystem, or None
        """
        # Prefer numeric epsg field (new server format)
        epsg = project_file.get('epsg')
        if epsg is not None:
            return f"EPSG:{epsg}"

        # Fall back to crs string (legacy or Proj4)
        crs = project_file.get('crs')
        if crs:
            return crs

        # Check georeferencing data (for sketches)
        if georef and georef.get('epsg'):
            return f"EPSG:{georef.get('epsg', 4326)}"

        return None

    def process_project_files(
        self,
        project_files: List[Dict[str, Any]],
        project_name: str,
        progress_callback: Optional[Callable[[int, str], None]] = None,
        auth_token: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Process a list of project files - download and load as layers.

        Now handles:
        - XYZ tiled rasters (tiles_available=true) - streamed from server
        - GeoTIFFs/DEMs (is_raster=true) - downloaded and cached locally
        - Georeferenced PNGs from mobile app (category='GL' with georeferencing)

        Args:
            project_files: List of ProjectFile records from API
            project_name: Name of the project (for layer naming/grouping)
            progress_callback: Optional callback(progress_percent, status_message)
            auth_token: Optional authentication token for XYZ tile requests

        Returns:
            Dictionary with results: {
                'loaded': number of layers loaded,
                'tiled': number of XYZ tile layers loaded,
                'skipped': number of non-raster files skipped,
                'errors': list of error messages,
                'layers': list of created layer names
            }
        """
        loaded = 0
        tiled = 0
        skipped = 0
        errors = []
        layer_names = []

        total = len(project_files)

        for i, pf in enumerate(project_files):
            if progress_callback:
                progress = int((i / total) * 100)
                progress_callback(progress, f"Processing {pf.get('name', 'file')}...")

            try:
                # Check if this file has XYZ tiles available (preferred for large rasters)
                tiles_available = pf.get('tiles_available', False)
                tiles_status = pf.get('tiles_status', 'none')

                if tiles_available and tiles_status == 'completed':
                    # Use XYZ tile layer instead of downloading full file
                    self.logger.info(
                        f"File '{pf.get('name')}' has XYZ tiles available, "
                        f"using tile streaming instead of download"
                    )

                    layer = self.process_tiled_raster(
                        project_file=pf,
                        project_name=project_name,
                        auth_token=auth_token
                    )

                    if layer and layer.isValid():
                        tiled += 1
                        layer_names.append(layer.name())
                        self.logger.info(f"Loaded XYZ tile layer: {layer.name()}")
                    else:
                        # Fall back to downloading the full file if tile layer fails
                        self.logger.warning(
                            f"Failed to create XYZ tile layer for '{pf.get('name')}', "
                            f"falling back to file download"
                        )
                        # Continue to file download logic below
                        tiles_available = False

                # If tiles not available or failed, use traditional download approach
                if not (tiles_available and tiles_status == 'completed'):
                    # Determine if this is a traditional raster or georeferenced image
                    is_raster = pf.get('is_raster', False)
                    georef = pf.get('georeferencing')

                    # Handle georeferencing that might be a JSON string
                    if georef and isinstance(georef, str):
                        import json
                        try:
                            georef = json.loads(georef)
                        except json.JSONDecodeError:
                            self.logger.warning(f"Failed to parse georeferencing JSON for {pf.get('name')}")
                            georef = None

                    # Log for debugging
                    self.logger.debug(
                        f"File '{pf.get('name')}': is_raster={is_raster}, "
                        f"category={pf.get('category')}, georef={georef is not None}, "
                        f"tiles_available={tiles_available}, tiles_status={tiles_status}"
                    )

                    # Skip files that aren't loadable (neither raster nor georeferenced)
                    if not is_raster and not georef:
                        self.logger.debug(f"Skipping non-georeferenced file: {pf.get('name')}")
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

                    # For georeferenced images (PNGs/JPGs), generate world file
                    # These are images that have georeferencing data but aren't GeoTIFFs
                    # (GeoTIFFs have embedded georeferencing and don't need world files)
                    file_ext = Path(local_path).suffix.lower()
                    needs_world_file = georef is not None and file_ext in self.IMAGE_EXTENSIONS
                    is_sketch = pf.get('category') in self.SKETCH_CATEGORIES

                    if needs_world_file:
                        self.logger.info(f"Generating world file for '{pf.get('name')}' with georef: {georef}")
                        world_file_path = self._generate_world_file(local_path, georef)
                        if not world_file_path:
                            errors.append(f"Failed to generate world file for: {pf.get('name')}")
                            continue

                    # Load as QGIS layer
                    # Prefer numeric epsg field (new), fall back to crs string (legacy)
                    crs_value = self._get_crs_string(pf, georef)
                    layer = self.load_raster_layer(
                        file_path=local_path,
                        layer_name=f"{project_name}_{pf.get('name')}",
                        crs=crs_value,
                        category=pf.get('category'),
                        is_sketch=is_sketch
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
            'tiled': tiled,
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
        category: Optional[str] = None,
        is_sketch: bool = False
    ) -> Optional[QgsRasterLayer]:
        """
        Load a raster file as a QGIS layer.

        Args:
            file_path: Path to the raster file
            layer_name: Name for the layer
            crs: Optional CRS string (e.g., 'EPSG:4326')
            category: Optional category code for styling (e.g., 'DM' for DEM)
            is_sketch: If True, apply sketch styling (transparent background)

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

        # Apply styling based on category or sketch flag
        if is_sketch:
            self._apply_sketch_style(layer)
        else:
            self._apply_raster_style(layer, category)

        # Add to QGIS project
        QgsProject.instance().addMapLayer(layer)
        self.logger.info(f"Added raster layer: {layer_name}")

        return layer

    def load_xyz_tile_layer(
        self,
        tile_url_template: str,
        layer_name: str,
        min_zoom: int = 0,
        max_zoom: int = 18,
        bounds: Optional[List[float]] = None,
        crs: Optional[str] = None,
        auth_token: Optional[str] = None
    ) -> Optional[QgsRasterLayer]:
        """
        Load an XYZ tile layer from a tile URL template.

        This creates a QGIS XYZ tile layer that streams tiles from the server
        rather than downloading the entire raster file. This is more efficient
        for large rasters.

        Args:
            tile_url_template: URL template with {z}, {x}, {y} placeholders
                             e.g., "https://api.geodb.io/api/v2/project-files/123/tiles/{z}/{x}/{y}.png"
            layer_name: Name for the layer in QGIS
            min_zoom: Minimum zoom level (default 0)
            max_zoom: Maximum zoom level (default 18)
            bounds: Optional bounding box [minX, minY, maxX, maxY] in WGS84
            crs: Optional CRS string (default EPSG:3857 for web tiles)
            auth_token: Optional authentication token for tile requests

        Returns:
            QgsRasterLayer if successful, None otherwise
        """
        try:
            # Build the XYZ connection URI
            # QGIS XYZ layers use a specific URI format
            # Format: type=xyz&url=...&zmin=...&zmax=...

            # URL-encode the template URL (but preserve {z}, {x}, {y} placeholders)
            # QGIS expects the URL to have %7B and %7D for { and }
            encoded_url = tile_url_template.replace('{', '%7B').replace('}', '%7D')

            # Build URI parameters
            uri_parts = [
                f"type=xyz",
                f"url={encoded_url}",
                f"zmin={min_zoom}",
                f"zmax={max_zoom}",
            ]

            # Add authentication if provided
            if auth_token:
                # For token auth, we need to use referer or http-header approach
                # QGIS XYZ layers support adding HTTP headers via authcfg or referer
                # For simplicity, if the URL supports api_key param, append it
                if '?' not in tile_url_template:
                    encoded_url_with_auth = f"{encoded_url}?api_key={auth_token}"
                else:
                    encoded_url_with_auth = f"{encoded_url}&api_key={auth_token}"
                uri_parts[1] = f"url={encoded_url_with_auth}"

            uri = '&'.join(uri_parts)

            self.logger.info(f"Creating XYZ tile layer: {layer_name}")
            self.logger.debug(f"XYZ URI: {uri}")

            # Create the XYZ raster layer
            layer = QgsRasterLayer(uri, layer_name, 'wms')

            if not layer.isValid():
                self.logger.error(f"Failed to create XYZ tile layer: {layer_name}")
                self.logger.error(f"Layer error: {layer.error().message() if layer.error() else 'Unknown'}")
                return None

            # Set CRS - XYZ tiles are typically in Web Mercator (EPSG:3857)
            # but QGIS will reproject as needed
            if crs:
                layer_crs = QgsCoordinateReferenceSystem(crs)
            else:
                # Default to Web Mercator for XYZ tiles
                layer_crs = QgsCoordinateReferenceSystem("EPSG:3857")

            if layer_crs.isValid():
                layer.setCrs(layer_crs)

            # Set extent if bounds provided (helps with layer visibility/navigation)
            if bounds and len(bounds) == 4:
                # Convert bounds from WGS84 to layer CRS if needed
                extent = QgsRectangle(bounds[0], bounds[1], bounds[2], bounds[3])
                # Note: setExtent is not directly available on QgsRasterLayer
                # The extent is determined by the tile source, but we can set
                # a custom extent hint via the data provider
                self.logger.debug(f"Layer bounds: {extent.toString()}")

            # Add to QGIS project
            QgsProject.instance().addMapLayer(layer)
            self.logger.info(f"Added XYZ tile layer: {layer_name}")

            return layer

        except Exception as e:
            self.logger.error(f"Error creating XYZ tile layer: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return None

    def process_tiled_raster(
        self,
        project_file: Dict[str, Any],
        project_name: str,
        auth_token: Optional[str] = None
    ) -> Optional[QgsRasterLayer]:
        """
        Process a project file that has XYZ tiles available.

        Instead of downloading the full raster file, this creates an XYZ tile
        layer that streams tiles on demand.

        Args:
            project_file: ProjectFile dict from API with tile fields
            project_name: Name of the project (for layer naming)
            auth_token: Optional authentication token for tile requests

        Returns:
            QgsRasterLayer if successful, None otherwise
        """
        # Extract tile information
        tile_url_template = project_file.get('tile_url_template')
        if not tile_url_template:
            self.logger.error(f"No tile_url_template for file: {project_file.get('name')}")
            return None

        # Get zoom levels
        min_zoom = project_file.get('tile_min_zoom', 0)
        max_zoom = project_file.get('tile_max_zoom', 18)

        # Get bounds if available
        bounds = project_file.get('bounds')
        if bounds and isinstance(bounds, str):
            import json
            try:
                bounds = json.loads(bounds)
            except json.JSONDecodeError:
                bounds = None

        # Get CRS
        crs = self._get_crs_string(project_file)

        # Create layer name
        layer_name = f"{project_name}_{project_file.get('name', 'Tiled Raster')}"

        self.logger.info(
            f"Processing tiled raster '{project_file.get('name')}': "
            f"zoom {min_zoom}-{max_zoom}, tiles_status={project_file.get('tiles_status')}"
        )

        return self.load_xyz_tile_layer(
            tile_url_template=tile_url_template,
            layer_name=layer_name,
            min_zoom=min_zoom,
            max_zoom=max_zoom,
            bounds=bounds,
            crs=crs,
            auth_token=auth_token
        )

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

    def _apply_sketch_style(self, layer: QgsRasterLayer) -> None:
        """
        Apply sketch styling to a georeferenced image layer.

        Makes white/near-white pixels transparent so the sketch overlays nicely
        on top of other map layers.

        Args:
            layer: The raster layer to style
        """
        try:
            from qgis.core import QgsRasterTransparency
            from qgis.PyQt.QtGui import QColor

            # Get the layer's renderer
            renderer = layer.renderer()
            if not renderer:
                self.logger.warning("No renderer available for sketch layer")
                return

            # Create transparency list for white/near-white pixels
            # We'll make pixels with RGB values all > 250 transparent
            transparency = QgsRasterTransparency()

            # For RGB images (3+ bands), set white pixels transparent
            if layer.bandCount() >= 3:
                # Create a transparency entry for white pixels
                # This uses the three-band transparency list
                white_transparent = QgsRasterTransparency.TransparentThreeValuePixel()
                white_transparent.red = 255.0
                white_transparent.green = 255.0
                white_transparent.blue = 255.0
                white_transparent.percentTransparent = 100.0

                # Also catch near-white (common in JPEGs due to compression)
                near_white = QgsRasterTransparency.TransparentThreeValuePixel()
                near_white.red = 254.0
                near_white.green = 254.0
                near_white.blue = 254.0
                near_white.percentTransparent = 100.0

                transparency.setTransparentThreeValuePixelList([white_transparent, near_white])

            renderer.setRasterTransparency(transparency)
            layer.triggerRepaint()

            self.logger.info("Applied sketch style (white transparent) to layer")

        except Exception as e:
            self.logger.warning(f"Failed to apply sketch style: {e}")

    def _generate_world_file(
        self,
        image_path: str,
        georef: Dict[str, Any]
    ) -> Optional[str]:
        """
        Generate a world file (.pgw, .jgw) for a georeferenced image.

        World file format (6 lines):
        1. Pixel size in X direction (scale)
        2. Rotation about Y axis (usually 0)
        3. Rotation about X axis (usually 0)
        4. Pixel size in Y direction (scale, usually negative)
        5. X coordinate of upper-left pixel center
        6. Y coordinate of upper-left pixel center

        Args:
            image_path: Path to the image file
            georef: Georeferencing data from API with:
                - sw_latitude, sw_longitude: Southwest corner
                - ne_latitude, ne_longitude: Northeast corner
                - canvas_width, canvas_height: Image dimensions in pixels

        Returns:
            Path to generated world file, or None on failure
        """
        try:
            # Parse georeferencing data (API uses full names: sw_latitude, sw_longitude, etc.)
            sw_lat = georef.get('sw_latitude')
            sw_lng = georef.get('sw_longitude')
            ne_lat = georef.get('ne_latitude')
            ne_lng = georef.get('ne_longitude')
            canvas_width = georef.get('canvas_width')
            canvas_height = georef.get('canvas_height')

            if None in (sw_lat, sw_lng, ne_lat, ne_lng, canvas_width, canvas_height):
                self.logger.error(f"Incomplete georeferencing data: {georef}")
                return None

            # Calculate pixel size (ground units per pixel)
            pixel_width = (ne_lng - sw_lng) / canvas_width
            pixel_height = (ne_lat - sw_lat) / canvas_height  # Will be negative in world file

            # Upper-left corner coordinates (center of upper-left pixel)
            # SW is bottom-left, so upper-left Y = ne_lat
            upper_left_x = sw_lng + (pixel_width / 2)
            upper_left_y = ne_lat - (abs(pixel_height) / 2)

            # World file content
            # Line 4 is negative because Y increases downward in image coords
            world_content = f"{pixel_width:.12f}\n0.0\n0.0\n{-abs(pixel_height):.12f}\n{upper_left_x:.12f}\n{upper_left_y:.12f}\n"

            # Determine world file extension
            world_ext = self._get_world_file_extension(image_path)
            world_path = Path(image_path).with_suffix(world_ext)

            # Write world file
            with open(world_path, 'w') as f:
                f.write(world_content)

            self.logger.info(f"Generated world file: {world_path}")
            return str(world_path)

        except Exception as e:
            self.logger.error(f"Failed to generate world file: {e}")
            return None

    def _get_world_file_extension(self, image_path: str) -> str:
        """
        Get the appropriate world file extension for an image file.

        World file naming convention:
        - .png -> .pgw
        - .jpg/.jpeg -> .jgw
        - .tif/.tiff -> .tfw
        - .gif -> .gfw
        - .bmp -> .bpw

        Args:
            image_path: Path to the image file

        Returns:
            World file extension (e.g., '.pgw')
        """
        ext = Path(image_path).suffix.lower()

        world_extensions = {
            '.png': '.pgw',
            '.jpg': '.jgw',
            '.jpeg': '.jgw',
            '.tif': '.tfw',
            '.tiff': '.tfw',
            '.gif': '.gfw',
            '.bmp': '.bpw',
        }

        return world_extensions.get(ext, '.wld')

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
