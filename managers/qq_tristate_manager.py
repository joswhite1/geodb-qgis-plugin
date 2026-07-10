# -*- coding: utf-8 -*-
"""Tri-state quarter-section (QQ) streaming layer for the GeodbIO plugin (WT3 §4b).

Streams the server-computed tri-state QQ layer from the geoDB DRF v1 surface
(``/api/v1/qq-tristate/``, Knox token). The state (``unplotted`` / ``plotted`` /
``mixed``) is computed PER VIEWER on the server — a private, in-window plot is
``unplotted`` for every token but its uploader's, so the plugin can never leak a
private plot's existence. NO client-side derivation: a ``QgsRuleBasedRenderer``
keys purely on the served ``qq_state`` (+ ``tier``) attributes.

Styling mirrors the web:
  • unplotted → class-color fill (the "as today" QQ fill, kept)
  • plotted   → no fill, tier-styled outline
  • mixed     → BLACK diagonal-hatch fill (``QgsLinePatternFillSymbolLayer``) so
    it survives colour-blindness AND black-and-white filing-map prints.

Follows the ``managers/state_lands_manager.py`` streaming contract and reuses the
SSL-safe :class:`FederalLandsFetchWorker`.
"""
from typing import Optional

try:
    import sip
except ImportError:
    from qgis.PyQt import sip

from qgis.PyQt.QtCore import Qt, QObject, QTimer, pyqtSignal
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsFeature, QgsGeometry,
    QgsSimpleFillSymbolLayer, QgsLinePatternFillSymbolLayer, QgsFillSymbol,
    QgsSymbol, QgsRuleBasedRenderer,
)
from qgis.PyQt.QtGui import QColor

from ..api.client import APIClient
from ..utils.config import Config
from ..utils.crs_utils import extent_to_wgs84
from ..utils.geometry import geojson_to_wkt
from ..utils.logger import PluginLogger
from .federal_lands_manager import FederalLandsFetchWorker

COL_QQ = '#C97D10'         # unplotted quarter-section (class-color)
COL_PLOTTED = '#2F6DBD'    # plotted outline


class QQTristateStreamingManager(QObject):
    """Streaming tri-state QQ overlay (rule-based renderer on ``qq_state``)."""

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
        self._logger.info(f"[QQTristate] {msg}")
        self.log_message.emit(f"[QQTristate] {msg}", level)

    def enable(self):
        if self._enabled:
            return
        from qgis.utils import iface
        if not iface or not iface.mapCanvas():
            self._log("No map canvas available", "warning")
            return
        self._canvas = iface.mapCanvas()
        self._enabled = True
        if not self._layer_alive(self._layer) or not self._layer.isValid():
            self._create_layer()
        if not self._canvas_connected:
            self._canvas.extentsChanged.connect(self._on_extent_changed)
            self._canvas_connected = True
        QgsProject.instance().layerRemoved.connect(self._on_layer_removed)
        self._fetch_data()

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

    def _on_extent_changed(self):
        if self._enabled:
            self._debounce_timer.start()

    def _on_layer_removed(self, layer_id: str):
        if self._layer_alive(self._layer) and layer_id == self._layer.id():
            self.disable()

    def _fetch_data(self):
        if not self._enabled or not self._canvas:
            return
        extent = self._canvas.extent()
        map_crs = self._canvas.mapSettings().destinationCrs()
        extent = extent_to_wgs84(extent, map_crs)
        if extent is None:
            self.status_changed.emit("Cannot determine extent")
            return
        if (extent.xMaximum() - extent.xMinimum()) > 4 or (extent.yMaximum() - extent.yMinimum()) > 4:
            self._clear_layer()
            self.status_changed.emit("Zoom in to see quarter-sections")
            return
        bbox = (f"{extent.xMinimum()},{extent.yMinimum()},"
                f"{extent.xMaximum()},{extent.yMaximum()}")
        token = self._api_client.token
        if not token:
            self.status_changed.emit("Login required")
            return
        endpoint = self._config.endpoints.get('qq_tristate', '')
        if not endpoint:
            self._log("No qq_tristate endpoint configured!", "error")
            return
        url = f"{endpoint}?bbox={bbox}"
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
        feats = geojson_data.get('features', [])
        self._layer.startEditing()
        self._layer.deleteFeatures([f.id() for f in self._layer.getFeatures()])
        new_features = []
        for fd in feats:
            geom_data = fd.get('geometry')
            if not geom_data:
                continue
            wkt = geojson_to_wkt(geom_data)
            if not wkt:
                continue
            geom = QgsGeometry.fromWkt(wkt)
            if geom.isEmpty():
                continue
            if geom.wkbType() in (3, 6, 1003, 1006):
                geom.convertToMultiType()
            props = fd.get('properties', {})
            f = QgsFeature(self._layer.fields())
            f.setGeometry(geom)
            f.setAttribute('serial', str(props.get('serial') or ''))
            f.setAttribute('name', str(props.get('name') or ''))
            f.setAttribute('qq_state', str(props.get('qq_state') or 'unplotted'))
            f.setAttribute('tier', str(props.get('tier') or ''))
            f.setAttribute('disposition', str(props.get('disposition') or ''))
            new_features.append(f)
        if new_features:
            self._layer.addFeatures(new_features)
        self._layer.commitChanges()
        n = len(new_features)
        self.status_changed.emit(f"{n} quarter-sections" if n else "No quarter-sections in this area")
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
            self._layer.deleteFeatures([f.id() for f in self._layer.getFeatures()])
            self._layer.commitChanges()

    def _create_layer(self):
        fields_uri = ("field=serial:string&field=name:string&field=qq_state:string&"
                      "field=tier:string&field=disposition:string")
        layer = QgsVectorLayer(f"MultiPolygon?crs=EPSG:4326&{fields_uri}",
                               "Quarter-sections (tri-state)", 'memory')
        if not layer.isValid():
            self._log("Failed to create QQ tri-state layer", "error")
            return
        self._apply_rules(layer)
        QgsProject.instance().addMapLayer(layer, False)
        root = QgsProject.instance().layerTreeRoot()
        base_group = root.findGroup("Base Layers")
        if not base_group:
            base_group = root.addGroup("Base Layers")
        base_group.insertLayer(0, layer)
        self._layer = layer
        self._log("Created Quarter-sections (tri-state)")

    def _apply_rules(self, layer):
        """Rule-based renderer keyed on the served ``qq_state`` (+ ELSE)."""
        root_rule = QgsRuleBasedRenderer.Rule(None)

        # 1) unplotted — class-color fill (kept "as today").
        unplotted = QgsSymbol.defaultSymbol(layer.geometryType())
        unplotted.deleteSymbolLayer(0)
        fill = QgsSimpleFillSymbolLayer()
        c = QColor(COL_QQ)
        c.setAlpha(64)
        fill.setColor(c)
        fill.setStrokeColor(QColor(COL_QQ))
        fill.setStrokeWidth(0.3)
        unplotted.appendSymbolLayer(fill)
        r1 = QgsRuleBasedRenderer.Rule(unplotted)
        r1.setFilterExpression('"qq_state" = \'unplotted\'')
        r1.setLabel('Unplotted quarter-section')
        root_rule.appendChild(r1)

        # 2) plotted — no fill, blue tier-styled outline.
        plotted = QgsSymbol.defaultSymbol(layer.geometryType())
        plotted.deleteSymbolLayer(0)
        pf = QgsSimpleFillSymbolLayer()
        pf.setBrushStyle(Qt.NoBrush)                    # Qt.NoBrush — no fill
        pf.setStrokeColor(QColor(COL_PLOTTED))
        pf.setStrokeWidth(0.6)
        plotted.appendSymbolLayer(pf)
        r2 = QgsRuleBasedRenderer.Rule(plotted)
        r2.setFilterExpression('"qq_state" = \'plotted\'')
        r2.setLabel('Plotted')
        root_rule.appendChild(r2)

        # 3) mixed — BLACK diagonal-hatch fill (survives CVD + B&W filing maps).
        mixed = QgsFillSymbol()
        mixed.deleteSymbolLayer(0)
        hatch = QgsLinePatternFillSymbolLayer()
        hatch.setLineAngle(45)
        hatch.setDistance(2.2)                 # mm between hatch lines
        hatch.setColor(QColor('#000000'))
        hatch.setLineWidth(0.3)
        mixed.appendSymbolLayer(hatch)
        outline = QgsSimpleFillSymbolLayer()
        outline.setBrushStyle(Qt.NoBrush)               # no solid fill over the hatch
        outline.setStrokeColor(QColor(COL_PLOTTED))
        outline.setStrokeWidth(0.6)
        mixed.appendSymbolLayer(outline)
        r3 = QgsRuleBasedRenderer.Rule(mixed)
        r3.setFilterExpression('"qq_state" = \'mixed\'')
        r3.setLabel('Mixed (partially plotted)')
        root_rule.appendChild(r3)

        # ELSE — light gray outline.
        other = QgsSymbol.defaultSymbol(layer.geometryType())
        other.deleteSymbolLayer(0)
        of = QgsSimpleFillSymbolLayer()
        of.setBrushStyle(Qt.NoBrush)
        of.setStrokeColor(QColor('#8a8a92'))
        of.setStrokeWidth(0.3)
        other.appendSymbolLayer(of)
        rel = QgsRuleBasedRenderer.Rule(other)
        rel.setFilterExpression('ELSE')
        rel.setLabel('Other')
        root_rule.appendChild(rel)

        layer.setRenderer(QgsRuleBasedRenderer(root_rule))
