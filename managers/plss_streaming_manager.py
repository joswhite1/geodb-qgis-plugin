# -*- coding: utf-8 -*-
"""
PLSS Grid streaming layer manager for GeodbIO plugin.

Manages live vector layers that show PLSS townships and sections,
auto-refreshing as the user pans/zooms the map canvas.
Follows the same pattern as BLMClaimsManager.
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
    QgsCoordinateReferenceSystem, QgsCoordinateTransform,
    QgsSimpleLineSymbolLayer, QgsSimpleFillSymbolLayer,
    QgsSymbol, QgsSingleSymbolRenderer,
    QgsPalLayerSettings, QgsVectorLayerSimpleLabeling,
    QgsTextFormat, QgsTextBufferSettings
)
from qgis.PyQt.QtCore import Qt
from qgis.PyQt.QtGui import QColor, QFont

from ..api.client import APIClient
from ..utils.config import Config
from ..utils.logger import PluginLogger
from ..utils.compat import Qt_DashLine, Qt_SolidLine


# Reuse the same access types as BLM claims
PLSS_STREAMING_ACCESS_TYPES = {
    'staff',
    'enterprise_api', 'enterprise_integrated',
    'enterprise_api_trial', 'enterprise_integrated_trial',
}

# Default style for streaming layers
PLSS_STREAMING_STYLES = {
    'townships': {
        'line_color': '#1e3a8a',   # Blue
        'line_width': 2.0,
        'line_style': Qt_DashLine,
        'label_color': '#1e3a8a',
        'opacity': 0.7,
    },
    'sections': {
        'line_color': '#059669',   # Green
        'line_width': 1.0,
        'line_style': Qt_SolidLine,
        'label_color': '#059669',
        'opacity': 0.7,
    },
}


class PLSSFetchWorker(QThread):
    """Background worker to fetch PLSS GeoJSON from the server."""

    finished = pyqtSignal(int, str, dict)  # generation, layer_type, geojson_data
    error = pyqtSignal(int, str, str)      # generation, layer_type, error_message

    def __init__(self, url: str, token: str, generation: int,
                 layer_type: str, parent=None):
        super().__init__(parent)
        self.url = url
        self.token = token
        self.generation = generation
        self.layer_type = layer_type

    def run(self):
        import urllib.request
        import ssl
        from urllib.parse import urlparse

        try:
            parsed = urlparse(self.url)
            if parsed.scheme not in ('http', 'https'):
                self.error.emit(self.generation, self.layer_type,
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

            with urllib.request.urlopen(req, context=ctx, timeout=30) as response:
                data = json.loads(response.read().decode('utf-8'))
                self.finished.emit(self.generation, self.layer_type, data)

        except urllib.error.HTTPError as e:
            body = ''
            try:
                body = e.read().decode('utf-8', errors='replace')
            except Exception:
                pass
            if e.code == 403:
                self.error.emit(self.generation, self.layer_type,
                                f"403: {body or 'Access denied'}")
            else:
                self.error.emit(self.generation, self.layer_type,
                                f"HTTP {e.code}: {body or str(e)}")
        except Exception as e:
            self.error.emit(self.generation, self.layer_type, str(e))


class PLSSStreamingManager(QObject):
    """Manages streaming PLSS township and section layers in QGIS.

    Creates memory vector layers that auto-update as the user pans/zooms.
    Townships stream at wider zoom, sections at closer zoom.
    Both debounce rapid extent changes and discard stale responses.
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

        # Layers: one for townships, one for sections
        self._twp_layer: Optional[QgsVectorLayer] = None
        self._sec_layer: Optional[QgsVectorLayer] = None
        self._enabled = False

        # Request tracking per layer type
        self._generation = {'townships': 0, 'sections': 0}
        self._workers: Dict[str, Optional[PLSSFetchWorker]] = {
            'townships': None, 'sections': None
        }

        # Debounce timer: 500ms after last extent change
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(500)
        self._debounce_timer.timeout.connect(self._fetch_plss)

        self._canvas = None
        self._canvas_connected = False

    @staticmethod
    def _layer_alive(layer) -> bool:
        """Check if a QgsVectorLayer reference is still valid (not deleted by C++)."""
        return layer is not None and not sip.isdeleted(layer)

    def _log(self, msg: str, level: str = "info"):
        """Log to both internal logger and plugin log panel."""
        self._logger.info(f"[PLSS] {msg}")
        self.log_message.emit(f"[PLSS] {msg}", level)

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
        self._log("Enabling PLSS streaming layers...")

        # Create layers if needed
        if not self._layer_alive(self._twp_layer) or not self._twp_layer.isValid():
            self._create_layer('townships')
        if not self._layer_alive(self._sec_layer) or not self._sec_layer.isValid():
            self._create_layer('sections')

        # Connect to extent changes
        if not self._canvas_connected:
            self._canvas.extentsChanged.connect(self._on_extent_changed)
            self._canvas_connected = True

        QgsProject.instance().layerRemoved.connect(self._on_layer_removed)

        # Trigger initial fetch
        self._fetch_plss()
        self._log("PLSS streaming enabled")

    def disable(self):
        """Disable streaming and remove layers."""
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

        # Remove layers
        for layer in (self._twp_layer, self._sec_layer):
            if self._layer_alive(layer):
                try:
                    QgsProject.instance().removeMapLayer(layer.id())
                except Exception:
                    pass
        self._twp_layer = None
        self._sec_layer = None

        # Cancel workers
        for key in self._workers:
            if self._workers[key] and self._workers[key].isRunning():
                self._workers[key].terminate()
                self._workers[key] = None

        self.status_changed.emit("")

    def cleanup(self):
        """Full cleanup on logout or plugin unload."""
        self.disable()

    @property
    def is_enabled(self) -> bool:
        return self._enabled

    # ---- Internal methods ----

    def _on_extent_changed(self):
        """Handle canvas extent change — restart debounce timer."""
        if self._enabled:
            self._debounce_timer.start()

    def _on_layer_removed(self, layer_id: str):
        """Handle layer removal — disable if both layers were removed."""
        if self._layer_alive(self._twp_layer) and layer_id == self._twp_layer.id():
            self._twp_layer = None
        if self._layer_alive(self._sec_layer) and layer_id == self._sec_layer.id():
            self._sec_layer = None

        # If both layers are gone, disable entirely
        if not self._layer_alive(self._twp_layer) and not self._layer_alive(self._sec_layer):
            self._log("Both PLSS layers removed by user")
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

    def _fetch_plss(self):
        """Fetch PLSS data for the current canvas extent."""
        if not self._enabled or not self._canvas:
            return

        extent = self._canvas.extent()
        map_crs = self._canvas.mapSettings().destinationCrs()

        if map_crs.authid() != 'EPSG:4326':
            wgs84 = QgsCoordinateReferenceSystem('EPSG:4326')
            transform = QgsCoordinateTransform(map_crs, wgs84, QgsProject.instance())
            extent = transform.transformBoundingBox(extent)

        lon_span = extent.xMaximum() - extent.xMinimum()
        lat_span = extent.yMaximum() - extent.yMinimum()

        bbox = (f"{extent.xMinimum()},{extent.yMinimum()},"
                f"{extent.xMaximum()},{extent.yMaximum()}")

        token = self._api_client.token
        if not token:
            self.status_changed.emit("Login required")
            return

        # Townships: fetch when extent < 4 degrees (same as BLM)
        if lon_span <= 4 and lat_span <= 4:
            self._fetch_layer_type('townships', bbox, token)
        else:
            self._clear_layer(self._twp_layer)
            self.status_changed.emit("Zoom in to see PLSS grid")

        # Sections: only fetch when extent < 0.5 degrees (~55km)
        if lon_span <= 0.5 and lat_span <= 0.5:
            self._fetch_layer_type('sections', bbox, token)
        else:
            self._clear_layer(self._sec_layer)

    def _fetch_layer_type(self, layer_type: str, bbox: str, token: str):
        """Fetch a specific PLSS layer type."""
        endpoint_key = f'plss_{layer_type}'
        endpoint = self._config.endpoints.get(endpoint_key, '')
        if not endpoint:
            self._log(f"No {endpoint_key} endpoint configured!", "error")
            return

        url = f"{endpoint}?bbox={bbox}"
        if layer_type == 'sections':
            url += "&simplified=true"

        self._generation[layer_type] += 1
        generation = self._generation[layer_type]

        self._log(f"Fetching {layer_type}: {url[:100]}...")
        self.loading_changed.emit(True)

        # Cancel previous worker for this type
        if self._workers[layer_type] and self._workers[layer_type].isRunning():
            self._workers[layer_type].terminate()

        worker = PLSSFetchWorker(url, token, generation, layer_type, self)
        worker.finished.connect(self._on_fetch_complete)
        worker.error.connect(self._on_fetch_error)
        self._workers[layer_type] = worker
        worker.start()

    def _on_fetch_complete(self, generation: int, layer_type: str,
                           geojson_data: dict):
        """Handle successful fetch response."""
        if generation != self._generation.get(layer_type):
            return

        layer = self._twp_layer if layer_type == 'townships' else self._sec_layer
        if not self._layer_alive(layer) or not layer.isValid():
            self._log(f"{layer_type} layer invalid, cannot render", "warning")
            self.loading_changed.emit(False)
            return

        features_data = geojson_data.get('features', [])
        self._log(f"{layer_type}: {len(features_data)} features received")

        # Replace all features
        layer.startEditing()
        layer.deleteFeatures([f.id() for f in layer.getFeatures()])

        new_features = []
        for feat_data in features_data:
            geom_data = feat_data.get('geometry')
            if not geom_data:
                continue

            wkt = self._geojson_geom_to_wkt(geom_data)
            if not wkt:
                continue

            geom = QgsGeometry.fromWkt(wkt)
            if geom.isEmpty():
                continue

            props = feat_data.get('properties', {})
            feat = QgsFeature(layer.fields())
            feat.setGeometry(geom)

            if layer_type == 'townships':
                feat.setAttribute('plss_label', props.get('plss_label', ''))
                feat.setAttribute('township', props.get('township', ''))
                feat.setAttribute('range', props.get('range', ''))
                feat.setAttribute('state', props.get('state', ''))
                feat.setAttribute('meridian', props.get('meridian', ''))
                feat.setAttribute('area_acres', props.get('area_acres'))
            else:
                feat.setAttribute('plss_location', props.get('plss_location', ''))
                feat.setAttribute('township', props.get('township', ''))
                feat.setAttribute('range', props.get('range', ''))
                feat.setAttribute('section', props.get('section', ''))
                feat.setAttribute('state', props.get('state', ''))
                feat.setAttribute('meridian', props.get('meridian', ''))
                feat.setAttribute('area_acres', props.get('area_acres'))

            new_features.append(feat)

        if new_features:
            layer.addFeatures(new_features)
        layer.commitChanges()

        # Update status
        twp_count = self._twp_layer.featureCount() if self._layer_alive(self._twp_layer) and self._twp_layer.isValid() else 0
        sec_count = self._sec_layer.featureCount() if self._layer_alive(self._sec_layer) and self._sec_layer.isValid() else 0
        parts = []
        if twp_count:
            parts.append(f"{twp_count} townships")
        if sec_count:
            parts.append(f"{sec_count} sections")
        self.status_changed.emit(', '.join(parts) if parts else "No PLSS data in this area")

        self.loading_changed.emit(False)
        layer.triggerRepaint()

    def _on_fetch_error(self, generation: int, layer_type: str, error_msg: str):
        """Handle fetch error."""
        if generation != self._generation.get(layer_type):
            return

        self.loading_changed.emit(False)
        self._log(f"{layer_type} fetch error: {error_msg}", "error")

        if error_msg.startswith('403:'):
            self.access_denied.emit(error_msg)
        else:
            self.status_changed.emit(f"Error: {error_msg[:80]}")

    def _clear_layer(self, layer: Optional[QgsVectorLayer]):
        """Clear all features from a layer."""
        if self._layer_alive(layer) and layer.isValid():
            layer.startEditing()
            layer.deleteFeatures([f.id() for f in layer.getFeatures()])
            layer.commitChanges()

    def _create_layer(self, layer_type: str):
        """Create a memory vector layer for PLSS streaming."""
        fields_uri = self._build_fields_uri(layer_type)
        name = "PLSS Townships - Live" if layer_type == 'townships' else "PLSS Sections - Live"

        layer = QgsVectorLayer(
            f"Polygon?crs=EPSG:4326&{fields_uri}",
            name,
            'memory'
        )

        if not layer.isValid():
            self._log(f"Failed to create {layer_type} layer", "error")
            return

        style = PLSS_STREAMING_STYLES[layer_type]
        self._apply_style(layer, style)
        self._apply_labels(layer, layer_type, style)

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

        if layer_type == 'townships':
            self._twp_layer = layer
        else:
            self._sec_layer = layer

        self._log(f"Created {name}")

    def _apply_style(self, layer: QgsVectorLayer, style: dict):
        """Apply line styling to a PLSS layer."""
        symbol = QgsSymbol.defaultSymbol(layer.geometryType())
        symbol.deleteSymbolLayer(0)

        # Use a fill layer with no fill, just border
        fill = QgsSimpleFillSymbolLayer()
        fill.setColor(QColor(0, 0, 0, 0))  # Transparent fill
        fill.setStrokeColor(QColor(style['line_color']))
        fill.setStrokeWidth(style['line_width'])
        fill.setStrokeStyle(style['line_style'])
        symbol.appendSymbolLayer(fill)

        renderer = QgsSingleSymbolRenderer(symbol)
        layer.setRenderer(renderer)
        layer.setOpacity(style['opacity'])

    def _apply_labels(self, layer: QgsVectorLayer, layer_type: str, style: dict):
        """Apply labeling with scale-dependent visibility."""
        label_settings = QgsPalLayerSettings()
        label_settings.placement = Qgis.LabelPlacement.OverPoint

        if layer_type == 'sections':
            label_settings.fieldName = (
                "'Sec ' || \"section\" || '\\n' || \"township\" || '\\n' || \"range\""
            )
            label_settings.isExpression = True
            label_settings.scaleVisibility = True
            label_settings.minimumScale = 100000
            label_settings.maximumScale = 0
        else:
            label_settings.fieldName = "\"township\" || ' ' || \"range\""
            label_settings.isExpression = True
            label_settings.scaleVisibility = True
            label_settings.minimumScale = 500000
            label_settings.maximumScale = 0

        text_format = QgsTextFormat()
        font = QFont("Arial", 9)
        font.setBold(True)
        text_format.setFont(font)
        text_format.setColor(QColor(style['label_color']))

        buffer_settings = QgsTextBufferSettings()
        buffer_settings.setEnabled(True)
        buffer_settings.setSize(1.5)
        buffer_settings.setColor(QColor('#FFFFFF'))
        text_format.setBuffer(buffer_settings)

        label_settings.setFormat(text_format)
        labeling = QgsVectorLayerSimpleLabeling(label_settings)
        layer.setLabeling(labeling)
        layer.setLabelsEnabled(True)

    def _build_fields_uri(self, layer_type: str) -> str:
        """Build the fields portion of the memory layer URI."""
        if layer_type == 'townships':
            fields = [
                "field=plss_label:string",
                "field=township:string",
                "field=range:string",
                "field=state:string",
                "field=meridian:string",
                "field=area_acres:double",
            ]
        else:
            fields = [
                "field=plss_location:string",
                "field=township:string",
                "field=range:string",
                "field=section:string",
                "field=state:string",
                "field=meridian:string",
                "field=area_acres:double",
            ]
        return '&'.join(fields)

    @staticmethod
    def _geojson_geom_to_wkt(geom: dict) -> str:
        """Convert a GeoJSON geometry dict to WKT string."""
        geom_type = geom.get('type', '')
        coords = geom.get('coordinates', [])

        if geom_type == 'Polygon':
            rings = []
            for ring in coords:
                pts = ', '.join(f"{c[0]} {c[1]}" for c in ring)
                rings.append(f"({pts})")
            return f"POLYGON({', '.join(rings)})"
        elif geom_type == 'MultiPolygon':
            polys = []
            for polygon in coords:
                rings = []
                for ring in polygon:
                    pts = ', '.join(f"{c[0]} {c[1]}" for c in ring)
                    rings.append(f"({pts})")
                polys.append(f"({', '.join(rings)})")
            return f"MULTIPOLYGON({', '.join(polys)})"

        return ''
