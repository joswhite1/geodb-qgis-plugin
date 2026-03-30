# -*- coding: utf-8 -*-
"""
Federal Lands streaming layer manager for GeodbIO plugin.

Manages a live vector layer showing BLM and Forest Service land boundaries,
auto-refreshing as the user pans/zooms the map canvas.
Follows the same pattern as PLSSStreamingManager.

Colors match the Esri USA Federal Lands standard and geodb.io web map:
  - BLM: Yellow (#FFEB3B) with gold border (#F9A825)
  - Forest Service: Green (#4CAF50) with dark green border (#2E7D32)
"""
import json
from typing import Optional, Dict

try:
    import sip
except ImportError:
    from qgis.PyQt import sip

from qgis.PyQt.QtCore import QObject, QTimer, pyqtSignal, QThread
from qgis.core import (
    Qgis, QgsProject, QgsVectorLayer, QgsFeature, QgsGeometry,
    QgsSimpleFillSymbolLayer,
    QgsSymbol, QgsCategorizedSymbolRenderer, QgsRendererCategory,
)
from qgis.PyQt.QtGui import QColor

from ..api.client import APIClient
from ..utils.config import Config
from ..utils.crs_utils import extent_to_wgs84
from ..utils.geometry import geojson_to_wkt
from ..utils.logger import PluginLogger


# Reuse the same access types as BLM claims / PLSS
FEDERAL_LANDS_ACCESS_TYPES = {
    'staff',
    'enterprise_api', 'enterprise_integrated',
    'enterprise_api_trial', 'enterprise_integrated_trial',
}

# Agency color scheme matching Esri USA Federal Lands and geodb.io web map
FEDERAL_LANDS_STYLES = {
    'Bureau of Land Management': {
        'fill_color': '#FFEB3B',
        'fill_opacity': 80,      # 0-255
        'stroke_color': '#F9A825',
        'stroke_width': 0.5,
    },
    'Forest Service': {
        'fill_color': '#4CAF50',
        'fill_opacity': 80,
        'stroke_color': '#2E7D32',
        'stroke_width': 0.5,
    },
}


class FederalLandsFetchWorker(QThread):
    """Background worker to fetch Federal Lands GeoJSON from the server."""

    finished = pyqtSignal(int, dict)   # generation, geojson_data
    error = pyqtSignal(int, str)       # generation, error_message

    def __init__(self, url: str, token: str, generation: int, parent=None):
        super().__init__(parent)
        self.url = url
        self.token = token
        self.generation = generation

    def run(self):
        import urllib.request
        import ssl
        import logging
        from urllib.parse import urlparse

        log = logging.getLogger('GeodbIO')

        try:
            log.info(f"[FedLands Worker] Starting fetch gen={self.generation}")
            log.info(f"[FedLands Worker] URL: {self.url}")
            log.info(f"[FedLands Worker] Token present: {bool(self.token)}, "
                     f"length: {len(self.token) if self.token else 0}")

            parsed = urlparse(self.url)
            if parsed.scheme not in ('http', 'https'):
                self.error.emit(self.generation,
                                f"Invalid URL scheme: {parsed.scheme}")
                return

            req = urllib.request.Request(self.url)
            req.add_header('Authorization', f'Token {self.token}')
            req.add_header('Accept', 'application/json')
            req.add_header('User-Agent', 'GeodbIO-QGIS-Plugin/2.0')

            ctx = ssl.create_default_context()
            if 'localhost' in self.url or '127.0.0.1' in self.url:
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE

            if not req.full_url.startswith(('https://', 'http://')):
                raise ValueError(f"Unsupported URL scheme: {req.full_url}")

            log.info("[FedLands Worker] Sending request...")
            with urllib.request.urlopen(req, context=ctx, timeout=30) as response:
                status = response.getcode()
                raw = response.read()
                log.info(f"[FedLands Worker] Response status: {status}, "
                         f"body length: {len(raw)} bytes")
                data = json.loads(raw.decode('utf-8'))
                feature_count = len(data.get('features', []))
                log.info(f"[FedLands Worker] Parsed GeoJSON: "
                         f"{feature_count} features, "
                         f"keys: {list(data.keys())}")
                if feature_count == 0:
                    log.info(f"[FedLands Worker] Empty response body preview: "
                             f"{raw[:500].decode('utf-8', errors='replace')}")
                self.finished.emit(self.generation, data)

        except urllib.error.HTTPError as e:
            body = ''
            try:
                body = e.read().decode('utf-8', errors='replace')
            except Exception:
                pass
            log.error(f"[FedLands Worker] HTTP error {e.code}: {body[:500]}")
            if e.code == 403:
                self.error.emit(self.generation,
                                f"403: {body or 'Access denied'}")
            else:
                self.error.emit(self.generation,
                                f"HTTP {e.code}: {body or str(e)}")
        except Exception as e:
            log.error(f"[FedLands Worker] Exception: {type(e).__name__}: {e}")
            self.error.emit(self.generation, str(e))


class FederalLandsStreamingManager(QObject):
    """Manages a streaming Federal Lands layer in QGIS.

    Creates a memory vector layer that auto-updates as the user pans/zooms,
    showing BLM (yellow) and Forest Service (green) land boundaries.
    Follows the same architecture as PLSSStreamingManager.
    """

    status_changed = pyqtSignal(str)
    loading_changed = pyqtSignal(bool)
    access_denied = pyqtSignal(str)
    log_message = pyqtSignal(str, str)

    def __init__(self, config: Config, api_client: APIClient, parent=None):
        super().__init__(parent)
        self._config = config
        self._api_client = api_client
        self._logger = PluginLogger.get_logger()

        self._layer: Optional[QgsVectorLayer] = None
        self._enabled = False
        self._generation = 0
        self._worker: Optional[FederalLandsFetchWorker] = None

        # Debounce timer: 500ms after last extent change
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(500)
        self._debounce_timer.timeout.connect(self._fetch_data)

        self._canvas = None
        self._canvas_connected = False

    @staticmethod
    def _layer_alive(layer) -> bool:
        return layer is not None and not sip.isdeleted(layer)

    def _log(self, msg: str, level: str = "info"):
        self._logger.info(f"[FedLands] {msg}")
        self.log_message.emit(f"[FedLands] {msg}", level)

    def enable(self):
        """Enable streaming and start listening for extent changes."""
        if self._enabled:
            return

        from qgis.utils import iface
        if not iface or not iface.mapCanvas():
            self._log("No map canvas available", "warning")
            return

        self._canvas = iface.mapCanvas()
        self._enabled = True
        self._log("Enabling Federal Lands streaming layer...")

        if not self._layer_alive(self._layer) or not self._layer.isValid():
            self._create_layer()

        if not self._canvas_connected:
            self._canvas.extentsChanged.connect(self._on_extent_changed)
            self._canvas_connected = True

        QgsProject.instance().layerRemoved.connect(self._on_layer_removed)

        self._fetch_data()
        self._log("Federal Lands streaming enabled")

    def disable(self):
        """Disable streaming and remove layer."""
        if not self._enabled:
            return

        self._enabled = False
        self._debounce_timer.stop()

        if self._canvas and self._canvas_connected:
            try:
                self._canvas.extentsChanged.disconnect(self._on_extent_changed)
            except (TypeError, RuntimeError):
                pass
            self._canvas_connected = False

        try:
            QgsProject.instance().layerRemoved.disconnect(self._on_layer_removed)
        except (TypeError, RuntimeError):
            pass

        if self._layer_alive(self._layer):
            try:
                QgsProject.instance().removeMapLayer(self._layer.id())
            except Exception:
                pass
        self._layer = None

        if self._worker and self._worker.isRunning():
            self._worker.terminate()
            self._worker = None

        self.status_changed.emit("")

    def cleanup(self):
        """Full cleanup on logout or plugin unload."""
        self.disable()

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    # ---- Internal methods ----

    def _on_extent_changed(self):
        if self._enabled:
            self._debounce_timer.start()

    def _on_layer_removed(self, layer_id: str):
        if self._layer_alive(self._layer) and layer_id == self._layer.id():
            self._layer = None
            self._log("Federal Lands layer removed by user")
            self._enabled = False
            self._debounce_timer.stop()
            if self._canvas and self._canvas_connected:
                try:
                    self._canvas.extentsChanged.disconnect(self._on_extent_changed)
                except (TypeError, RuntimeError):
                    pass
                self._canvas_connected = False
            try:
                QgsProject.instance().layerRemoved.disconnect(self._on_layer_removed)
            except (TypeError, RuntimeError):
                pass
            self.status_changed.emit("")

    def _fetch_data(self):
        """Fetch federal lands data for the current canvas extent."""
        self._log("_fetch_data() called")
        if not self._enabled or not self._canvas:
            self._log(f"Skipping fetch: enabled={self._enabled}, "
                      f"canvas={self._canvas is not None}")
            return

        extent = self._canvas.extent()
        map_crs = self._canvas.mapSettings().destinationCrs()
        self._log(f"Canvas CRS: {map_crs.authid()}, "
                  f"raw extent: {extent.toString()}")

        extent = extent_to_wgs84(extent, map_crs)
        if extent is None:
            self._log("extent_to_wgs84 returned None!", "warning")
            self.status_changed.emit("Cannot determine extent")
            return

        lon_span = extent.xMaximum() - extent.xMinimum()
        lat_span = extent.yMaximum() - extent.yMinimum()
        self._log(f"WGS84 extent: lon=[{extent.xMinimum():.4f}, "
                  f"{extent.xMaximum():.4f}], lat=[{extent.yMinimum():.4f}, "
                  f"{extent.yMaximum():.4f}], span=({lon_span:.2f}, {lat_span:.2f})")

        # Only fetch when zoomed in enough (< 4 degrees, same as PLSS townships)
        if lon_span > 4 or lat_span > 4:
            self._log(f"Extent too wide ({lon_span:.2f} x {lat_span:.2f}), "
                      f"need < 4 degrees")
            self._clear_layer()
            self.status_changed.emit("Zoom in to see Federal Lands")
            return

        bbox = (f"{extent.xMinimum()},{extent.yMinimum()},"
                f"{extent.xMaximum()},{extent.yMaximum()}")

        token = self._api_client.token
        if not token:
            self._log("No token available - login required", "warning")
            self.status_changed.emit("Login required")
            return

        endpoint = self._config.endpoints.get('federal_lands', '')
        if not endpoint:
            self._log("No federal_lands endpoint configured!", "error")
            return

        url = f"{endpoint}?bbox={bbox}"
        use_local = self._config.get('api.use_local', False)
        self._log(f"Endpoint: {endpoint}")
        self._log(f"Full URL: {url}")
        self._log(f"Using local server: {use_local}")

        self._generation += 1
        generation = self._generation

        self._log(f"Starting fetch gen={generation}")
        self.loading_changed.emit(True)

        if self._worker and self._worker.isRunning():
            self._log("Terminating previous worker")
            self._worker.terminate()

        worker = FederalLandsFetchWorker(url, token, generation, self)
        worker.finished.connect(self._on_fetch_complete)
        worker.error.connect(self._on_fetch_error)
        self._worker = worker
        worker.start()
        self._log(f"Worker started for gen={generation}")

    def _on_fetch_complete(self, generation: int, geojson_data: dict):
        self._log(f"_on_fetch_complete gen={generation} "
                  f"(current={self._generation})")
        if generation != self._generation:
            self._log(f"Stale response gen={generation}, ignoring")
            return

        if not self._layer_alive(self._layer) or not self._layer.isValid():
            self._log("Layer invalid, cannot render", "warning")
            self.loading_changed.emit(False)
            return

        features_data = geojson_data.get('features', [])
        self._log(f"{len(features_data)} features received, "
                  f"response keys: {list(geojson_data.keys())}")
        if features_data:
            sample = features_data[0]
            self._log(f"Sample feature keys: {list(sample.keys())}, "
                      f"properties: {sample.get('properties', {})}, "
                      f"geometry type: {sample.get('geometry', {}).get('type')}")

        # Replace all features
        self._layer.startEditing()
        self._layer.deleteFeatures([f.id() for f in self._layer.getFeatures()])

        new_features = []
        skipped_no_geom = 0
        skipped_no_wkt = 0
        skipped_empty_geom = 0
        for feat_data in features_data:
            geom_data = feat_data.get('geometry')
            if not geom_data:
                skipped_no_geom += 1
                continue

            wkt = geojson_to_wkt(geom_data)
            if not wkt:
                skipped_no_wkt += 1
                continue

            geom = QgsGeometry.fromWkt(wkt)
            if geom.isEmpty():
                skipped_empty_geom += 1
                continue

            # Promote Polygon to MultiPolygon for layer compatibility
            if geom.wkbType() in (3, 6):  # Polygon or MultiPolygon (2D)
                geom.convertToMultiType()
            elif geom.wkbType() in (1003, 1006):  # PolygonZ or MultiPolygonZ
                geom.convertToMultiType()

            props = feat_data.get('properties', {})
            feat = QgsFeature(self._layer.fields())
            feat.setGeometry(geom)
            feat.setAttribute('agency', props.get('agency', ''))
            feat.setAttribute('name', props.get('name', ''))
            feat.setAttribute('state', props.get('state', ''))
            new_features.append(feat)

        if skipped_no_geom or skipped_no_wkt or skipped_empty_geom:
            self._log(f"Skipped features: no_geom={skipped_no_geom}, "
                      f"no_wkt={skipped_no_wkt}, empty_geom={skipped_empty_geom}",
                      "warning")

        self._log(f"Adding {len(new_features)} features to layer")
        if new_features:
            self._layer.addFeatures(new_features)
        commit_ok = self._layer.commitChanges()
        if not commit_ok:
            self._log(f"Layer commit failed: {self._layer.commitErrors()}",
                      "error")

        # Count by agency
        blm_count = sum(1 for f in features_data
                        if f.get('properties', {}).get('agency') == 'Bureau of Land Management')
        fs_count = sum(1 for f in features_data
                       if f.get('properties', {}).get('agency') == 'Forest Service')

        parts = []
        if blm_count:
            parts.append(f"{blm_count} BLM")
        if fs_count:
            parts.append(f"{fs_count} Forest Service")
        self.status_changed.emit(
            ', '.join(parts) if parts else "No federal lands in this area"
        )

        self.loading_changed.emit(False)
        self._layer.triggerRepaint()

    def _on_fetch_error(self, generation: int, error_msg: str):
        self._log(f"_on_fetch_error gen={generation} "
                  f"(current={self._generation}): {error_msg}", "error")
        if generation != self._generation:
            self._log(f"Stale error gen={generation}, ignoring")
            return

        self.loading_changed.emit(False)
        self._log(f"Fetch error: {error_msg}", "error")

        if error_msg.startswith('403:'):
            self.access_denied.emit(error_msg)
        else:
            self.status_changed.emit(f"Error: {error_msg[:80]}")

    def _clear_layer(self):
        if self._layer_alive(self._layer) and self._layer.isValid():
            self._layer.startEditing()
            self._layer.deleteFeatures(
                [f.id() for f in self._layer.getFeatures()]
            )
            self._layer.commitChanges()

    def _create_layer(self):
        """Create a memory vector layer with categorized styling by agency."""
        fields_uri = (
            "field=agency:string&"
            "field=name:string&"
            "field=state:string"
        )

        layer = QgsVectorLayer(
            f"MultiPolygon?crs=EPSG:4326&{fields_uri}",
            "Federal Lands - Live",
            'memory'
        )

        if not layer.isValid():
            self._log("Failed to create Federal Lands layer", "error")
            return

        self._apply_categorized_style(layer)

        # Add to project in "Base Layers" group
        QgsProject.instance().addMapLayer(layer, False)
        root = QgsProject.instance().layerTreeRoot()
        base_group = root.findGroup("Base Layers")
        if not base_group:
            base_group = root.addGroup("Base Layers")
            clone = base_group.clone()
            root.insertChildNode(-1, clone)
            root.removeChildNode(base_group)
            base_group = root.findGroup("Base Layers")

        base_group.insertLayer(0, layer)
        self._layer = layer
        self._log("Created Federal Lands - Live")

    def _apply_categorized_style(self, layer: QgsVectorLayer):
        """Apply categorized renderer: BLM=yellow, Forest Service=green."""
        categories = []

        for agency_name, style in FEDERAL_LANDS_STYLES.items():
            symbol = QgsSymbol.defaultSymbol(layer.geometryType())
            symbol.deleteSymbolLayer(0)

            fill = QgsSimpleFillSymbolLayer()
            fill_color = QColor(style['fill_color'])
            fill_color.setAlpha(style['fill_opacity'])
            fill.setColor(fill_color)
            fill.setStrokeColor(QColor(style['stroke_color']))
            fill.setStrokeWidth(style['stroke_width'])
            symbol.appendSymbolLayer(fill)

            cat = QgsRendererCategory(agency_name, symbol, agency_name)
            categories.append(cat)

        renderer = QgsCategorizedSymbolRenderer('agency', categories)
        layer.setRenderer(renderer)
        layer.setOpacity(0.5)
