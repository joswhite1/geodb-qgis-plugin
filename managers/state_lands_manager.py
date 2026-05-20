# -*- coding: utf-8 -*-
"""
State Lands streaming layer manager for GeodbIO plugin.

Streams Alaska state-owned land polygons from the same
``/services/api/federal-lands/`` endpoint the federal-lands manager
uses (the endpoint is land-ownership-agnostic — see geodb's
``services/api/federal_lands_api.py``), filtered with
``ownership=state&state=AK``. Server-side data was loaded from the
AK DNR ``Ownership_StateLandAll`` FeatureServer; see
``project_landownership_ak_state_land`` in geoDB's memory.

Pairs with :mod:`managers.federal_lands_manager`. Both managers share
the same fetch-worker contract + extent/debounce/generation machinery;
they diverge on:

* URL query string (``ownership=federal`` vs ``ownership=state``)
* Layer name + symbology (pale gold single category vs categorized
  BLM/Forest-Service yellow/green)
* Status-label phrasing

Consolidating the two into a single parameterised manager is a future
refactor — see CLAUDE.md "no duplicate paths" rule. Documented as
paired so a future reader doesn't accidentally fix one without the
other.
"""
import json
from typing import Optional

try:
    import sip
except ImportError:
    from qgis.PyQt import sip

from qgis.PyQt.QtCore import QObject, QTimer, pyqtSignal
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsFeature, QgsGeometry,
    QgsSimpleFillSymbolLayer, QgsSymbol, QgsSingleSymbolRenderer,
)
from qgis.PyQt.QtGui import QColor

from ..api.client import APIClient
from ..utils.config import Config
from ..utils.crs_utils import extent_to_wgs84
from ..utils.geometry import geojson_to_wkt
from ..utils.logger import PluginLogger
from .federal_lands_manager import FederalLandsFetchWorker


# Reuse the same access gate as federal lands — both overlays are
# QClaims-subscription features on the same endpoint.
STATE_LANDS_ACCESS_TYPES = {
    'staff',
    'enterprise_api', 'enterprise_integrated',
    'enterprise_api_trial', 'enterprise_integrated_trial',
}

# Pale gold for AK state land — matches the staff-page preview map's
# state-land style + the MTRSC filing-map renderer's `state` category.
STATE_LANDS_STYLE = {
    'fill_color':   '#F5E6A1',
    'fill_opacity': 80,        # 0-255
    'stroke_color': '#A88A00',
    'stroke_width': 0.5,
}


class StateLandsStreamingManager(QObject):
    """Streaming AK state-lands overlay.

    Mirrors :class:`FederalLandsStreamingManager` — same lifecycle,
    same extent-debounce-fetch machinery, same QClaims access gate.
    Differs only in URL filter + symbology + layer name. See the
    module docstring for why the two live side-by-side rather than
    behind a shared parameterised base class.
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
        self._logger.info(f"[StateLands] {msg}")
        self.log_message.emit(f"[StateLands] {msg}", level)

    def enable(self):
        if self._enabled:
            return

        from qgis.utils import iface
        if not iface or not iface.mapCanvas():
            self._log("No map canvas available", "warning")
            return

        self._canvas = iface.mapCanvas()
        self._enabled = True
        self._log("Enabling AK State Lands streaming layer...")

        if not self._layer_alive(self._layer) or not self._layer.isValid():
            self._create_layer()

        if not self._canvas_connected:
            self._canvas.extentsChanged.connect(self._on_extent_changed)
            self._canvas_connected = True

        QgsProject.instance().layerRemoved.connect(self._on_layer_removed)

        self._fetch_data()
        self._log("AK State Lands streaming enabled")

    def disable(self):
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
            self._log("AK State Lands layer removed by user")
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
        if not self._enabled or not self._canvas:
            return

        extent = self._canvas.extent()
        map_crs = self._canvas.mapSettings().destinationCrs()

        extent = extent_to_wgs84(extent, map_crs)
        if extent is None:
            self.status_changed.emit("Cannot determine extent")
            return

        lon_span = extent.xMaximum() - extent.xMinimum()
        lat_span = extent.yMaximum() - extent.yMinimum()
        # Same 4-degree gate as federal lands. The bbox query has a
        # 1000-feature server cap so very-wide views would silently
        # truncate — refuse to fetch instead.
        if lon_span > 4 or lat_span > 4:
            self._clear_layer()
            self.status_changed.emit("Zoom in to see AK State Lands")
            return

        bbox = (f"{extent.xMinimum()},{extent.yMinimum()},"
                f"{extent.xMaximum()},{extent.yMaximum()}")

        token = self._api_client.token
        if not token:
            self._log("No token available - login required", "warning")
            self.status_changed.emit("Login required")
            return

        # Reuses the federal-lands endpoint with ownership + state
        # filters. See ``services/api/federal_lands_api.py`` —
        # ownership='state' returns LandOwnership rows where
        # ``ownership_type='state'``.
        endpoint = self._config.endpoints.get('federal_lands', '')
        if not endpoint:
            self._log("No federal_lands endpoint configured!", "error")
            return

        url = f"{endpoint}?bbox={bbox}&ownership=state&state=AK"

        self._generation += 1
        generation = self._generation
        self.loading_changed.emit(True)

        if self._worker and self._worker.isRunning():
            self._worker.terminate()

        worker = FederalLandsFetchWorker(url, token, generation, self)
        worker.finished.connect(self._on_fetch_complete)
        worker.error.connect(self._on_fetch_error)
        self._worker = worker
        worker.start()

    def _on_fetch_complete(self, generation: int, geojson_data: dict):
        if generation != self._generation:
            return

        if not self._layer_alive(self._layer) or not self._layer.isValid():
            self.loading_changed.emit(False)
            return

        features_data = geojson_data.get('features', [])

        self._layer.startEditing()
        self._layer.deleteFeatures([f.id() for f in self._layer.getFeatures()])

        new_features = []
        for feat_data in features_data:
            geom_data = feat_data.get('geometry')
            if not geom_data:
                continue

            wkt = geojson_to_wkt(geom_data)
            if not wkt:
                continue

            geom = QgsGeometry.fromWkt(wkt)
            if geom.isEmpty():
                continue

            # Promote Polygon to MultiPolygon for layer compatibility
            if geom.wkbType() in (3, 6):       # Polygon / MultiPolygon (2D)
                geom.convertToMultiType()
            elif geom.wkbType() in (1003, 1006):  # PolygonZ / MultiPolygonZ
                geom.convertToMultiType()

            props = feat_data.get('properties', {})
            feat = QgsFeature(self._layer.fields())
            feat.setGeometry(geom)
            feat.setAttribute('agency', props.get('agency', ''))
            feat.setAttribute('name', props.get('name', ''))
            feat.setAttribute('state', props.get('state', ''))
            new_features.append(feat)

        if new_features:
            self._layer.addFeatures(new_features)
        commit_ok = self._layer.commitChanges()
        if not commit_ok:
            self._log(f"Layer commit failed: {self._layer.commitErrors()}",
                      "error")

        n = len(new_features)
        self.status_changed.emit(
            f"{n} AK state-land polygons" if n else "No AK state lands in this area"
        )

        self.loading_changed.emit(False)
        self._layer.triggerRepaint()

    def _on_fetch_error(self, generation: int, error_msg: str):
        if generation != self._generation:
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
        fields_uri = (
            "field=agency:string&"
            "field=name:string&"
            "field=state:string"
        )

        layer = QgsVectorLayer(
            f"MultiPolygon?crs=EPSG:4326&{fields_uri}",
            "AK State Lands - Live",
            'memory'
        )

        if not layer.isValid():
            self._log("Failed to create AK State Lands layer", "error")
            return

        self._apply_style(layer)

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
        self._log("Created AK State Lands - Live")

    def _apply_style(self, layer: QgsVectorLayer):
        """Apply single-symbol pale-gold styling.

        Diverges from FederalLandsStreamingManager — federal lands
        uses a categorized renderer (BLM yellow / Forest Service
        green). AK state land is a single ownership category so a
        single fill is the right call.
        """
        symbol = QgsSymbol.defaultSymbol(layer.geometryType())
        symbol.deleteSymbolLayer(0)

        fill = QgsSimpleFillSymbolLayer()
        fill_color = QColor(STATE_LANDS_STYLE['fill_color'])
        fill_color.setAlpha(STATE_LANDS_STYLE['fill_opacity'])
        fill.setColor(fill_color)
        fill.setStrokeColor(QColor(STATE_LANDS_STYLE['stroke_color']))
        fill.setStrokeWidth(STATE_LANDS_STYLE['stroke_width'])
        symbol.appendSymbolLayer(fill)

        renderer = QgsSingleSymbolRenderer(symbol)
        layer.setRenderer(renderer)
        layer.setOpacity(0.5)
