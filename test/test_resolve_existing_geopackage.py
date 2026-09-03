# -*- coding: utf-8 -*-
"""Tests for ClaimsWizardState.resolve_existing_geopackage (v2.29.1).

Guards the fix for the v2.28.0 split-GeoPackage regression: the claims
workflow must reuse the EXISTING claim-block GeoPackage (the Step-1 file that
holds the Initial Layout) and must NEVER silently create a second file. When
no GeoPackage exists, resolution returns None so the caller falls back to
memory layers — the pre-v2.28.0 behavior.

Runs WITHOUT QGIS: qgis/Qt modules are stubbed before import so the resolution
logic can be verified with plain ``python3 test/test_resolve_existing_geopackage.py``
from the repo root. (Do not run via ``-m unittest test.<name>`` — the test
package __init__ imports the real qgis for the QGIS-environment suites.)

Covers:
* path already set in state           -> returned unchanged (no recovery, no mint)
* path empty, layout gpkg-backed      -> RECOVERED from the claims layer source
  (the reopen/reload case that used to spawn a second file)
* path empty, memory-backed layout    -> None (no gpkg source to recover)
* path empty, gpkg source but missing -> None (file gone from disk)
* path empty, non-gpkg source (.shp)  -> None
* path empty, no claims layer at all   -> None
* NEVER creates a file: the default GeodbData dir is untouched in every case
"""

import importlib.util
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path


# ---------------------------------------------------------------------------
# qgis/Qt stubs — permissive dummies registered before the module import
# ---------------------------------------------------------------------------
class _Dummy:
    """Truthy, callable, attribute-permissive stand-in for Qt/QGIS objects."""

    def __init__(self, *args, **kwargs):
        pass

    def __call__(self, *args, **kwargs):
        return _Dummy()

    def __getattr__(self, name):
        return _Dummy()


def _stub_module(name):
    mod = types.ModuleType(name)
    mod.__getattr__ = lambda attr: _Dummy()  # PEP 562
    sys.modules[name] = mod
    return mod


_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_state_module():
    """Import ui/claims_wizard_state.py with qgis stubbed, avoiding the
    Qt-heavy package __init__ chain (same technique as the ordering test)."""
    for name in ('qgis', 'qgis.core', 'qgis.utils', 'qgis.PyQt',
                 'qgis.PyQt.QtCore', 'qgis.PyQt.QtWidgets', 'qgis.PyQt.QtGui'):
        if name not in sys.modules:
            _stub_module(name)

    # Synthetic parent package so the module's relative context resolves
    # without executing ui/__init__ (which pulls in Qt-heavy widgets).
    pkg = types.ModuleType('geodbplug')
    pkg.__path__ = [_REPO]
    sys.modules.setdefault('geodbplug', pkg)
    ui = types.ModuleType('geodbplug.ui')
    ui.__path__ = [os.path.join(_REPO, 'ui')]
    sys.modules.setdefault('geodbplug.ui', ui)

    path = os.path.join(_REPO, 'ui', 'claims_wizard_state.py')
    spec = importlib.util.spec_from_file_location(
        'geodbplug.ui.claims_wizard_state', path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


_state_mod = _load_state_module()
ClaimsWizardState = _state_mod.ClaimsWizardState


class _FakeLayer:
    """Stand-in for a QgsVectorLayer with a controllable data source."""

    def __init__(self, source):
        self._source = source

    def source(self):
        return self._source


class _StateWithLayer(ClaimsWizardState):
    """Override the QGIS-dependent claims_layer property so the resolution
    logic can be driven from a fake layer without a live QgsProject."""

    _fake_layer = None

    @property
    def claims_layer(self):
        return self._fake_layer


class ResolveExistingGeopackageTest(unittest.TestCase):

    def setUp(self):
        # A real temp .gpkg file on disk so Path.exists() is genuinely true.
        self._dir = tempfile.mkdtemp()
        self.gpkg = os.path.join(self._dir, 'BEM_claims.gpkg')
        open(self.gpkg, 'w').close()
        # Snapshot the default GeodbData dir so we can assert nothing is minted.
        self._default_dir = None
        try:
            from geodbplug.managers.storage_manager import StorageManager  # noqa
            self._default_dir = StorageManager().get_default_directory()
        except Exception:
            self._default_dir = None

    def _state(self, geopackage_path=None, layer_source=object()):
        s = _StateWithLayer()
        s.geopackage_path = geopackage_path
        if layer_source is None:
            s._fake_layer = None
        elif isinstance(layer_source, str):
            s._fake_layer = _FakeLayer(layer_source)
        else:
            s._fake_layer = None  # default: no layer
        return s

    # -- case 1: in-memory path wins, untouched -----------------------------
    def test_uses_inmemory_path_when_set(self):
        s = self._state(geopackage_path='/already/set/PATH_claims.gpkg')
        self.assertEqual(
            s.resolve_existing_geopackage(),
            '/already/set/PATH_claims.gpkg',
        )

    # -- case 2: THE FIX — recover from a gpkg-backed claims layer -----------
    def test_recovers_from_gpkg_backed_claims_layer(self):
        s = self._state(
            geopackage_path=None,
            layer_source=f'{self.gpkg}|layername=initial_layout',
        )
        got = s.resolve_existing_geopackage()
        self.assertEqual(got, self.gpkg)
        # and it is cached back onto the state for the rest of the wizard
        self.assertEqual(s.geopackage_path, self.gpkg)

    def test_recovers_from_gpkg_source_without_layername(self):
        s = self._state(geopackage_path=None, layer_source=self.gpkg)
        self.assertEqual(s.resolve_existing_geopackage(), self.gpkg)

    def test_recovery_is_case_insensitive_on_extension(self):
        upper = os.path.join(self._dir, 'BEM2.GPKG')
        open(upper, 'w').close()
        s = self._state(geopackage_path=None,
                        layer_source=f'{upper}|layername=x')
        self.assertEqual(s.resolve_existing_geopackage(), upper)

    # -- case 3: no recoverable gpkg -> None (memory-layer fallback) ---------
    def test_memory_backed_layer_returns_none(self):
        s = self._state(geopackage_path=None,
                        layer_source='Point?crs=EPSG:26912')
        self.assertIsNone(s.resolve_existing_geopackage())

    def test_missing_gpkg_file_returns_none(self):
        s = self._state(geopackage_path=None,
                        layer_source='/nope/GONE_claims.gpkg|layername=x')
        self.assertIsNone(s.resolve_existing_geopackage())

    def test_non_gpkg_source_returns_none(self):
        s = self._state(geopackage_path=None,
                        layer_source='/data/layout.shp')
        self.assertIsNone(s.resolve_existing_geopackage())

    def test_no_claims_layer_returns_none(self):
        s = self._state(geopackage_path=None, layer_source=None)
        self.assertIsNone(s.resolve_existing_geopackage())

    # -- the regression guard: NEVER mint a new file ------------------------
    def test_never_creates_a_file_when_none_exists(self):
        s = self._state(geopackage_path=None, layer_source=None)
        before = set(os.listdir(self._default_dir)) if self._default_dir and os.path.isdir(self._default_dir) else None
        self.assertIsNone(s.resolve_existing_geopackage())
        # No file was written into the default GeodbData directory.
        if before is not None:
            after = set(os.listdir(self._default_dir))
            self.assertEqual(
                before, after,
                "resolve_existing_geopackage must NOT create a GeoPackage "
                "(that was the v2.28.0 split-file regression).",
            )
        # And it must not have invented a path on the state either.
        self.assertIsNone(s.geopackage_path)


if __name__ == '__main__':
    unittest.main(verbosity=2)
