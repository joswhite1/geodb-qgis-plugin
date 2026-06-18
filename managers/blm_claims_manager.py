# -*- coding: utf-8 -*-
"""
BLM Mining Claims streaming layer manager for GeodbIO plugin.

Manages a live vector layer that shows BLM mining claim density per PLSS section,
auto-refreshing as the user pans/zooms the map canvas. Provides snapshot functionality
to save the current view as a persistent layer.
"""
import json
from typing import Optional, Dict, Any

try:
    import sip
except ImportError:
    from qgis.PyQt import sip

from qgis.PyQt.QtCore import QObject, QTimer, pyqtSignal, QThread, QUrl, QByteArray
from qgis.PyQt.QtNetwork import QNetworkRequest
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsFeature, QgsGeometry, QgsField,
    QgsSimpleFillSymbolLayer, QgsSymbol, QgsGraduatedSymbolRenderer,
    QgsRendererRange, QgsApplication
)
from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtGui import QColor

from ..api.client import APIClient
from ..utils.config import Config
from ..utils.crs_utils import extent_to_wgs84
from ..utils.geometry import geojson_to_wkt
from ..utils.logger import PluginLogger


# Claim count color ramp matching the web app's section polygon view
BLM_CLAIM_COUNT_COLORS = [
    (1, 1, '#FFB6C1', 'Light pink'),
    (2, 4, '#FFA07A', 'Light salmon'),
    (5, 9, '#FF7F50', 'Coral'),
    (10, 19, '#FF6347', 'Tomato'),
    (20, 29, '#DC143C', 'Crimson'),
    (30, 39, '#B22222', 'Firebrick'),
    (40, 999999, '#8B0000', 'Dark red'),
]

class BLMFetchWorker(QThread):
    """Background worker to fetch BLM claims GeoJSON from the API.

    Uses urllib.request to avoid nested QEventLoop issues with Qt network classes
    (same pattern as RefreshWorker in geodb_modern_dialog.py).
    """

    finished = pyqtSignal(int, dict)  # generation, geojson_data
    error = pyqtSignal(int, str)      # generation, error_message

    def __init__(self, url: str, token: str, generation: int, parent=None):
        super().__init__(parent)
        self.url = url
        self.token = token
        self.generation = generation

    def run(self):
        import urllib.request
        import ssl
        from ..utils.http import safe_urlopen

        try:
            req = urllib.request.Request(self.url)
            req.add_header('Authorization', f'Token {self.token}')
            req.add_header('Accept', 'application/json')
            req.add_header('User-Agent', 'GeodbIO-QGIS-Plugin/2.0')

            ctx = ssl.create_default_context()
            if 'localhost' in self.url or '127.0.0.1' in self.url:
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE

            with safe_urlopen(req, context=ctx, timeout=30) as response:
                data = json.loads(response.read().decode('utf-8'))
                self.finished.emit(self.generation, data)

        except urllib.error.HTTPError as e:
            body = ''
            try:
                body = e.read().decode('utf-8', errors='replace')
            except Exception:
                pass
            if e.code == 403:
                self.error.emit(self.generation, f"403: {body or 'Access denied'}")
            else:
                self.error.emit(self.generation, f"HTTP {e.code}: {body or str(e)}")
        except Exception as e:
            self.error.emit(self.generation, str(e))


class BLMClaimsManager(QObject):
    """Manages the BLM Mining Claims streaming layer in QGIS.

    Creates a memory vector layer that auto-updates with PLSS section polygons
    colored by claim count as the user pans/zooms. Debounces rapid extent changes
    and discards stale responses.
    """

    status_changed = pyqtSignal(str)    # Status text for UI
    loading_changed = pyqtSignal(bool)  # Loading spinner state
    access_denied = pyqtSignal(str)     # 403 error message
    log_message = pyqtSignal(str, str)  # (message, level) for plugin log panel

    def __init__(self, config: Config, api_client: APIClient, parent=None):
        super().__init__(parent)
        self._config = config
        self._api_client = api_client
        self._logger = PluginLogger.get_logger()

        self._streaming_layer: Optional[QgsVectorLayer] = None
        self._enabled = False
        self._filters: Dict[str, Optional[str]] = {'state': None, 'claim_type': None}
        self._request_generation = 0
        self._worker: Optional[BLMFetchWorker] = None

        # Debounce timer: 500ms after last extent change before fetching
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(500)
        self._debounce_timer.timeout.connect(self._fetch_claims)

        # Track canvas connection
        self._canvas = None
        self._canvas_connected = False

    @staticmethod
    def _layer_alive(layer) -> bool:
        """Check if a QgsVectorLayer reference is still valid (not deleted by C++)."""
        return layer is not None and not sip.isdeleted(layer)

    def _log(self, msg: str, level: str = "info"):
        """Log to both internal logger and plugin log panel."""
        self._logger.info(f"[BLM] {msg}")
        self.log_message.emit(f"[BLM] {msg}", level)

    def enable(self):
        """Enable the streaming layer and start listening for extent changes."""
        if self._enabled:
            return

        from qgis.utils import iface
        if not iface or not iface.mapCanvas():
            self._log("No map canvas available", "warning")
            return

        self._canvas = iface.mapCanvas()
        self._enabled = True
        self._log("Enabling streaming layer...")

        # Create the streaming layer if needed
        if not self._layer_alive(self._streaming_layer) or not self._streaming_layer.isValid():
            self._create_streaming_layer()

        # Connect to extent changes
        if not self._canvas_connected:
            self._canvas.extentsChanged.connect(self._on_extent_changed)
            self._canvas_connected = True

        # Connect to layer removal to detect if user removes our layer
        QgsProject.instance().layerRemoved.connect(self._on_layer_removed)

        # Trigger initial fetch
        self._fetch_claims()
        self._log("Streaming layer enabled")

    def disable(self):
        """Disable the streaming layer and stop listening."""
        if not self._enabled:
            return

        self._enabled = False
        self._debounce_timer.stop()

        # Disconnect signals
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

        # Remove the streaming layer from project
        if self._layer_alive(self._streaming_layer):
            try:
                QgsProject.instance().removeMapLayer(self._streaming_layer.id())
            except Exception:
                pass
        self._streaming_layer = None

        # Cancel any pending worker
        if self._worker and self._worker.isRunning():
            self._worker.terminate()
            self._worker = None

        self.status_changed.emit("")
        self._logger.info("[BLM] Streaming layer disabled")

    def cleanup(self):
        """Full cleanup on logout or plugin unload."""
        self.disable()

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    def set_filters(self, state: Optional[str] = None, claim_type: Optional[str] = None):
        """Update filters and trigger immediate refresh."""
        self._filters['state'] = state
        self._filters['claim_type'] = claim_type
        if self._enabled:
            self._fetch_claims()

    def snapshot_to_layer(self) -> Optional[QgsVectorLayer]:
        """Save the current streaming layer contents as a persistent snapshot layer."""
        if not self._layer_alive(self._streaming_layer) or not self._streaming_layer.isValid():
            return None

        from datetime import datetime
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")

        # Create new memory layer with same fields
        fields_uri = self._build_fields_uri()
        snap_layer = QgsVectorLayer(
            f"Polygon?crs=EPSG:4326&{fields_uri}",
            f"BLM Claims Snapshot - {timestamp}",
            'memory'
        )

        if not snap_layer.isValid():
            self._logger.error("[BLM] Failed to create snapshot layer")
            return None

        # Copy features
        snap_layer.startEditing()
        features = []
        for feat in self._streaming_layer.getFeatures():
            new_feat = QgsFeature(snap_layer.fields())
            new_feat.setGeometry(feat.geometry())
            for i in range(feat.fields().count()):
                try:
                    new_feat.setAttribute(i, feat.attribute(i))
                except Exception:
                    pass
            features.append(new_feat)
        snap_layer.addFeatures(features)
        snap_layer.commitChanges()

        # Copy renderer
        snap_layer.setRenderer(self._streaming_layer.renderer().clone())
        snap_layer.setOpacity(self._streaming_layer.opacity())

        # Create spatial index
        snap_layer.dataProvider().createSpatialIndex()

        # Add to project (NOT in Base Layers group — it's user data)
        QgsProject.instance().addMapLayer(snap_layer)

        self._logger.info(f"[BLM] Snapshot created with {len(features)} features")
        return snap_layer

    # ---- Internal methods ----

    def _on_extent_changed(self):
        """Handle canvas extent change — restart debounce timer."""
        if self._enabled:
            self._debounce_timer.start()

    def _on_layer_removed(self, layer_id: str):
        """Handle layer removal — disable if our layer was removed."""
        if self._layer_alive(self._streaming_layer) and layer_id == self._streaming_layer.id():
            self._logger.info("[BLM] Streaming layer removed by user")
            self._streaming_layer = None
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

    def _fetch_claims(self):
        """Fetch BLM claims for the current canvas extent."""
        if not self._enabled or not self._canvas:
            return

        # Get canvas extent in EPSG:4326
        extent = self._canvas.extent()
        map_crs = self._canvas.mapSettings().destinationCrs()

        extent = extent_to_wgs84(extent, map_crs)
        if extent is None:
            self._log("Could not transform canvas extent to WGS84", "warning")
            self.status_changed.emit("Cannot determine extent")
            self.loading_changed.emit(False)
            return

        # Check if zoomed out too far (>4 degrees span)
        lon_span = extent.xMaximum() - extent.xMinimum()
        lat_span = extent.yMaximum() - extent.yMinimum()
        if lon_span > 4 or lat_span > 4:
            self.status_changed.emit("Zoom in to see BLM claims")
            self.loading_changed.emit(False)
            # Clear existing features
            if self._layer_alive(self._streaming_layer) and self._streaming_layer.isValid():
                self._streaming_layer.startEditing()
                self._streaming_layer.deleteFeatures(
                    [f.id() for f in self._streaming_layer.getFeatures()]
                )
                self._streaming_layer.commitChanges()
            return

        # Build URL
        bbox = f"{extent.xMinimum()},{extent.yMinimum()},{extent.xMaximum()},{extent.yMaximum()}"
        endpoint = self._config.endpoints.get('blm_claims_sections', '')
        if not endpoint:
            self._log("No blm_claims_sections endpoint configured!", "error")
            return

        url = f"{endpoint}?bbox={bbox}"
        if self._filters.get('state'):
            url += f"&state={self._filters['state']}"
        if self._filters.get('claim_type'):
            url += f"&claim_type={self._filters['claim_type']}"

        # Increment generation to discard stale responses
        self._request_generation += 1
        generation = self._request_generation

        # Get token
        token = self._api_client.token
        if not token:
            self.status_changed.emit("Login required")
            self.loading_changed.emit(False)
            return

        self.loading_changed.emit(True)
        self.status_changed.emit("Loading...")

        # Cancel previous worker if still running
        if self._worker and self._worker.isRunning():
            self._worker.terminate()

        # Spawn background fetch
        self._worker = BLMFetchWorker(url, token, generation, self)
        self._worker.finished.connect(self._on_fetch_complete)
        self._worker.error.connect(self._on_fetch_error)
        self._worker.start()

    def _on_fetch_complete(self, generation: int, geojson_data: dict):
        """Handle successful fetch response."""
        # Discard stale responses
        if generation != self._request_generation:
            return

        self.loading_changed.emit(False)

        if not self._layer_alive(self._streaming_layer) or not self._streaming_layer.isValid():
            return

        features_data = geojson_data.get('features', [])

        # Clear existing features and add new ones
        self._streaming_layer.startEditing()
        self._streaming_layer.deleteFeatures(
            [f.id() for f in self._streaming_layer.getFeatures()]
        )

        new_features = []
        for feat_data in features_data:
            geom_data = feat_data.get('geometry')
            props = feat_data.get('properties', {})

            if not geom_data:
                continue

            geom = QgsGeometry.fromWkt(geojson_to_wkt(geom_data))
            if geom.isNull():
                # Try direct GeoJSON import
                geom = QgsGeometry.fromRect(
                    QgsGeometry.fromJson(json.dumps(geom_data)).boundingBox()
                )
                geom = QgsGeometry.fromJson(json.dumps(geom_data))
                if geom.isNull():
                    continue

            feat = QgsFeature(self._streaming_layer.fields())
            feat.setGeometry(geom)

            # Map properties to fields
            feat.setAttribute('section_id', props.get('section_id'))
            feat.setAttribute('plss_location', props.get('plss_location', ''))
            feat.setAttribute('township', props.get('township', ''))
            feat.setAttribute('range', props.get('range', ''))
            feat.setAttribute('section', props.get('section', ''))
            feat.setAttribute('state', props.get('state', ''))
            feat.setAttribute('area_acres', props.get('area_acres'))
            feat.setAttribute('claim_count', props.get('claim_count', 0))
            feat.setAttribute('total_acreage', props.get('total_acreage'))

            # Arrays come as lists from JSON — store as comma-separated strings
            claim_ids = props.get('claim_ids', [])
            claim_names = props.get('claim_names', [])
            feat.setAttribute('claim_ids', ', '.join(str(x) for x in claim_ids) if isinstance(claim_ids, list) else str(claim_ids or ''))
            feat.setAttribute('claim_names', ', '.join(str(x) for x in claim_names) if isinstance(claim_names, list) else str(claim_names or ''))
            feat.setAttribute('most_recent_date', props.get('most_recent_date', ''))

            new_features.append(feat)

        if new_features:
            self._streaming_layer.addFeatures(new_features)

        self._streaming_layer.commitChanges()

        # Update status
        count = len(new_features)
        if count == 0:
            self.status_changed.emit("No claims in this area")
        else:
            total_claims = sum(
                f.attribute('claim_count') or 0 for f in self._streaming_layer.getFeatures()
            )
            self.status_changed.emit(f"{count} sections, {total_claims} claims")

        # Refresh canvas
        self._streaming_layer.triggerRepaint()

    def _on_fetch_error(self, generation: int, error_msg: str):
        """Handle fetch error."""
        if generation != self._request_generation:
            return

        self.loading_changed.emit(False)
        self._log(f"Fetch error: {error_msg}", "error")

        if error_msg.startswith('403:'):
            self.access_denied.emit(error_msg)
            self.status_changed.emit("Subscription required")
        else:
            self.status_changed.emit(f"Error: {error_msg[:60]}")

    def _create_streaming_layer(self):
        """Create the memory vector layer for streaming BLM claims."""
        fields_uri = self._build_fields_uri()
        self._streaming_layer = QgsVectorLayer(
            f"Polygon?crs=EPSG:4326&{fields_uri}",
            "BLM Claims - Live",
            'memory'
        )

        if not self._streaming_layer.isValid():
            self._logger.error("[BLM] Failed to create streaming layer")
            return

        # Apply graduated symbology
        self._apply_style(self._streaming_layer)

        # Add to project in "Base Layers" group
        QgsProject.instance().addMapLayer(self._streaming_layer, False)

        root = QgsProject.instance().layerTreeRoot()
        base_layers_group = root.findGroup("Base Layers")
        if not base_layers_group:
            base_layers_group = root.addGroup("Base Layers")
            clone = base_layers_group.clone()
            root.insertChildNode(-1, clone)
            root.removeChildNode(base_layers_group)
            base_layers_group = root.findGroup("Base Layers")

        base_layers_group.insertLayer(0, self._streaming_layer)
        self._logger.info("[BLM] Created streaming layer")

    def _apply_style(self, layer: QgsVectorLayer):
        """Apply graduated symbology on claim_count matching the web app colors."""
        ranges = []
        for lower, upper, color_hex, label in BLM_CLAIM_COUNT_COLORS:
            symbol = QgsSymbol.defaultSymbol(layer.geometryType())
            symbol.deleteSymbolLayer(0)

            fill = QgsSimpleFillSymbolLayer()
            fill.setColor(QColor(color_hex))
            fill.setStrokeColor(QColor('#8B0000'))  # Dark red border
            fill.setStrokeWidth(1.0)
            symbol.appendSymbolLayer(fill)

            display_upper = f"{upper}" if upper < 999999 else "+"
            display_label = f"{lower}" if lower == upper else f"{lower}-{display_upper}"
            if upper >= 999999:
                display_label = f"{lower}+"

            ranges.append(QgsRendererRange(lower, upper, symbol, display_label))

        renderer = QgsGraduatedSymbolRenderer('claim_count', ranges)
        layer.setRenderer(renderer)
        layer.setOpacity(0.5)  # Semi-transparent, matching web app

    def _build_fields_uri(self) -> str:
        """Build the fields portion of the memory layer URI."""
        fields = [
            "field=section_id:integer",
            "field=plss_location:string",
            "field=township:string",
            "field=range:string",
            "field=section:string",
            "field=state:string",
            "field=area_acres:double",
            "field=claim_count:integer",
            "field=total_acreage:double",
            "field=claim_ids:string(0)",
            "field=claim_names:string(0)",
            "field=most_recent_date:string",
        ]
        return '&'.join(fields)

    # GeoJSON-to-WKT conversion is now in utils.geometry.geojson_to_wkt
