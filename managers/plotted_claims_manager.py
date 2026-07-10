# -*- coding: utf-8 -*-
"""Plotted-claims streaming layer manager for the GeodbIO plugin (WT3 §4b/§4c).

Streams the lead-file *plotted* claim boundaries from the geoDB DRF v1 surface
(``/api/v1/plotted-claims/?scope=…``, Knox token). One manager parameterised by
``scope``:

* ``scope='mine'``   → "My plotted claims" — the token owner's own rows,
  INCLUDING in-window private plots (violet, days-remaining), and ONLY the
  owner's (server-enforced).
* ``scope='public'`` → "Public claims (plotted)" — rows whose private window has
  elapsed, at the confidence floor.

Renders two sublayers from one fetch, grouped under the scope name:
  • claim polygons — rule-based renderer keyed on ``tier`` (solid A/B · dashed C ·
    dotted D) + a violet rule for the owner's private rows (§4b tier styling).
  • tie + derivation lines — the served §4c features (anchor→tie→corners), so
    QGIS shows the same derivation chain as the web overlay.

Pairs with :mod:`managers.qq_tristate_manager`. Reuses the SSL-safe
:class:`FederalLandsFetchWorker` (never construct an ssl context per thread — see
``utils/http.get_shared_ssl_context`` + the v2.23.1 Windows-crash fix). Follows
the streaming-manager contract established in ``managers/state_lands_manager.py``.
"""
import json
from typing import Optional

try:
    import sip
except ImportError:
    from qgis.PyQt import sip

from qgis.PyQt.QtCore import Qt, QObject, QTimer, pyqtSignal
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsFeature, QgsGeometry,
    QgsSimpleFillSymbolLayer, QgsSimpleLineSymbolLayer, QgsSymbol,
    QgsSingleSymbolRenderer, QgsRuleBasedRenderer,
)
from qgis.PyQt.QtGui import QColor

from ..api.client import APIClient
from ..utils.config import Config
from ..utils.crs_utils import extent_to_wgs84
from ..utils.geometry import geojson_to_wkt
from ..utils.logger import PluginLogger
from .federal_lands_manager import FederalLandsFetchWorker

# CVD-validated palette (matches the web layer — design artifact §3).
COL_PLOTTED = '#2F6DBD'
COL_PRIVATE = '#7D4CC9'
COL_ANCHOR = '#2E8B4A'

_SCOPE_LABEL = {'mine': 'My Plotted Claims', 'public': 'Public Plotted Claims'}


def _tier_pen(tier):
    """Tier → outline pen style mirroring the web: solid A/B, dashed C, dotted D."""
    if tier == 'C':
        return Qt.DashLine
    if tier == 'D':
        return Qt.DotLine
    return Qt.SolidLine             # A/B


class PlottedClaimsStreamingManager(QObject):
    """Streaming plotted-claims overlay (scope=mine | public)."""

    status_changed = pyqtSignal(str)
    loading_changed = pyqtSignal(bool)
    access_denied = pyqtSignal(str)
    log_message = pyqtSignal(str, str)

    def __init__(self, config: Config, api_client: APIClient, scope: str = 'public', parent=None):
        super().__init__(parent)
        self._config = config
        self._api_client = api_client
        self._scope = 'mine' if scope == 'mine' else 'public'
        self._logger = PluginLogger.get_logger()

        self._layer: Optional[QgsVectorLayer] = None        # claim polygons
        self._line_layer: Optional[QgsVectorLayer] = None   # tie + derivation
        self._enabled = False
        self._generation = 0
        self._worker: Optional[FederalLandsFetchWorker] = None

        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(500)
        self._debounce_timer.timeout.connect(self._fetch_data)

        self._canvas = None
        self._canvas_connected = False

    # ---- lifecycle (mirrors state_lands_manager) ----
    @staticmethod
    def _layer_alive(layer) -> bool:
        return layer is not None and not sip.isdeleted(layer)

    def _log(self, msg: str, level: str = "info"):
        self._logger.info(f"[PlottedClaims:{self._scope}] {msg}")
        self.log_message.emit(f"[PlottedClaims:{self._scope}] {msg}", level)

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
            self._create_layers()
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
        for lyr in (self._layer, self._line_layer):
            if self._layer_alive(lyr):
                try:
                    QgsProject.instance().removeMapLayer(lyr.id())
                except Exception:
                    pass
        self._layer = None
        self._line_layer = None
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

    # ---- fetch ----
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
            self._clear_layers()
            self.status_changed.emit("Zoom in to see plotted claims")
            return
        bbox = (f"{extent.xMinimum()},{extent.yMinimum()},"
                f"{extent.xMaximum()},{extent.yMaximum()}")
        token = self._api_client.token
        if not token:
            self.status_changed.emit("Login required")
            return
        endpoint = self._config.endpoints.get('plotted_claims', '')
        if not endpoint:
            self._log("No plotted_claims endpoint configured!", "error")
            return
        url = f"{endpoint}?bbox={bbox}&scope={self._scope}"
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

        poly_feats, line_feats = [], []
        for fd in feats:
            role = (fd.get('properties') or {}).get('feature_role')
            geom_data = fd.get('geometry')
            if not geom_data:
                continue
            wkt = geojson_to_wkt(geom_data)
            if not wkt:
                continue
            geom = QgsGeometry.fromWkt(wkt)
            if geom.isEmpty():
                continue
            props = fd.get('properties', {})
            if role == 'claim':
                if geom.wkbType() in (3, 6, 1003, 1006):
                    geom.convertToMultiType()
                f = QgsFeature(self._layer.fields())
                f.setGeometry(geom)
                f.setAttribute('claim_name', str(props.get('claim_name') or ''))
                f.setAttribute('tier', str(props.get('tier') or ''))
                f.setAttribute('is_own', 1 if props.get('is_own') else 0)
                f.setAttribute('is_public', 1 if props.get('is_public') else 0)
                f.setAttribute('days_remaining', props.get('days_remaining') if props.get('days_remaining') is not None else None)
                poly_feats.append(f)
            elif role in ('tie', 'derivation'):
                f = QgsFeature(self._line_layer.fields())
                f.setGeometry(geom)
                f.setAttribute('role', role)
                f.setAttribute('label', str(props.get('label') or ''))
                f.setAttribute('citation', str(props.get('citation') or ''))
                line_feats.append(f)
            # anchors (Point) are served too; the web overlay renders the monument
            # marker. The plugin shows the derivation chain via the tie line's
            # origin, so a separate point layer is intentionally omitted here.

        self._replace(self._layer, poly_feats)
        if self._layer_alive(self._line_layer):
            self._replace(self._line_layer, line_feats)

        n = len(poly_feats)
        label = 'your plotted claims' if self._scope == 'mine' else 'public plotted claims'
        self.status_changed.emit(f"{n} {label}" if n else f"No {label} in this area")
        self.loading_changed.emit(False)
        self._layer.triggerRepaint()

    @staticmethod
    def _replace(layer, features):
        if not (layer and not sip.isdeleted(layer) and layer.isValid()):
            return
        layer.startEditing()
        layer.deleteFeatures([f.id() for f in layer.getFeatures()])
        if features:
            layer.addFeatures(features)
        layer.commitChanges()

    def _on_fetch_error(self, generation: int, error_msg: str):
        if generation != self._generation:
            return
        self.loading_changed.emit(False)
        self._log(f"Fetch error: {error_msg}", "error")
        if error_msg.startswith('403:'):
            self.access_denied.emit(error_msg)
        else:
            self.status_changed.emit(f"Error: {error_msg[:80]}")

    def _clear_layers(self):
        for lyr in (self._layer, self._line_layer):
            self._replace(lyr, [])

    # ---- layer creation + styling ----
    def _create_layers(self):
        poly_uri = ("MultiPolygon?crs=EPSG:4326&field=claim_name:string&field=tier:string&"
                    "field=is_own:integer&field=is_public:integer&field=days_remaining:integer")
        line_uri = ("MultiLineString?crs=EPSG:4326&field=role:string&field=label:string&field=citation:string")
        name = _SCOPE_LABEL[self._scope]

        poly = QgsVectorLayer(poly_uri, f"{name} — boundaries", 'memory')
        line = QgsVectorLayer(line_uri, f"{name} — derivation", 'memory')
        if not poly.isValid():
            self._log("Failed to create plotted-claims polygon layer", "error")
            return
        self._apply_claim_style(poly)
        self._apply_line_style(line)

        for lyr in (poly, line):
            if lyr.isValid():
                QgsProject.instance().addMapLayer(lyr, False)
        group = self._ensure_group(name)
        group.insertLayer(0, poly)
        if line.isValid():
            group.insertLayer(1, line)
        self._layer = poly
        self._line_layer = line if line.isValid() else None
        self._log(f"Created {name} layers")

    def _ensure_group(self, name):
        root = QgsProject.instance().layerTreeRoot()
        group = root.findGroup(name)
        if not group:
            group = root.addGroup(name)
        return group

    def _apply_claim_style(self, layer):
        """Rule-based renderer: tier outline-style (solid A/B · dashed C · dotted
        D) + a violet rule for the owner's in-window private rows."""
        root_rule = QgsRuleBasedRenderer.Rule(None)

        def _fill_symbol(stroke_hex, pen_style, fill_hex, fill_alpha):
            sym = QgsSymbol.defaultSymbol(layer.geometryType())
            sym.deleteSymbolLayer(0)
            fill = QgsSimpleFillSymbolLayer()
            fc = QColor(fill_hex)
            fc.setAlpha(fill_alpha)
            fill.setColor(fc)
            fill.setStrokeColor(QColor(stroke_hex))
            fill.setStrokeWidth(0.6)
            fill.setStrokeStyle(pen_style)
            sym.appendSymbolLayer(fill)
            return sym

        rules = [
            ('My private (in-window)', '"is_own" = 1 AND "is_public" = 0', COL_PRIVATE, 40, Qt.SolidLine),
            ('Tier A/B', '"tier" IN (\'A\',\'B\')', COL_PLOTTED, 24, _tier_pen('A')),
            ('Tier C', '"tier" = \'C\'', COL_PLOTTED, 20, _tier_pen('C')),
            ('Tier D', '"tier" = \'D\'', COL_PLOTTED, 16, _tier_pen('D')),
        ]
        for label, expr, hexc, alpha, pen in rules:
            rule = QgsRuleBasedRenderer.Rule(_fill_symbol(hexc, pen, hexc, alpha))
            rule.setFilterExpression(expr)
            rule.setLabel(label)
            root_rule.appendChild(rule)

        default = QgsRuleBasedRenderer.Rule(_fill_symbol(COL_PLOTTED, Qt.SolidLine, COL_PLOTTED, 18))
        default.setFilterExpression('ELSE')
        default.setLabel('Plotted')
        root_rule.appendChild(default)
        layer.setRenderer(QgsRuleBasedRenderer(root_rule))

    def _apply_line_style(self, layer):
        sym = QgsSymbol.defaultSymbol(layer.geometryType())
        sym.deleteSymbolLayer(0)
        line = QgsSimpleLineSymbolLayer()
        line.setColor(QColor(COL_ANCHOR))
        line.setWidth(0.4)
        line.setCustomDashVector([3.0, 2.0])
        line.setUseCustomDashPattern(True)
        sym.appendSymbolLayer(line)
        layer.setRenderer(QgsSingleSymbolRenderer(sym))
