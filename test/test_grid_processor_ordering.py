# -*- coding: utf-8 -*-
"""Ordering tests for GridProcessor (Track D2, 2026-07-11).

Runs WITHOUT QGIS: qgis/Qt modules are stubbed before import so the
ordering logic (server wire contract + simple local fallback) can be
verified with plain ``python3 test/test_grid_processor_ordering.py`` from
the repo root. (Do not run via ``-m unittest test.<name>`` — the test
package __init__ imports the real qgis for the QGIS-environment suites.)

Covers:
* the server path's wire contract — sends ``direction`` (not
  ``sort_direction``), parses ``claims`` (not ``ordered_claims``), joins by
  index-as-name, sorts by the returned ``order`` field, and RAISES on
  empty / mismatched / malformed responses so the caller falls back;
* the local fallback — deliberately simple strict sort: book order on an
  UNROTATED block (the fallback's supported case; rotated blocks are the
  server rule's job);
* the loud warn-and-proceed helper survives headless contexts.
"""

import importlib.util
import os
import sys
import types
import unittest


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

    def __repr__(self):
        return '<qgis-stub>'


def _stub_module(name):
    mod = types.ModuleType(name)
    mod.__getattr__ = lambda attr: _Dummy()  # PEP 562
    sys.modules[name] = mod
    return mod


_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_grid_processor():
    for name in ('qgis', 'qgis.core', 'qgis.utils', 'qgis.PyQt',
                 'qgis.PyQt.QtCore', 'qgis.PyQt.QtWidgets', 'qgis.PyQt.QtGui'):
        if name not in sys.modules:
            _stub_module(name)

    # Synthetic parent packages so grid_processor's relative imports resolve
    # without executing the package __init__ chain (which imports Qt-heavy
    # sibling processors).
    pkg = types.ModuleType('geodbplug')
    pkg.__path__ = [_REPO]
    sys.modules.setdefault('geodbplug', pkg)
    for sub, path in (('processors', 'processors'), ('utils', 'utils')):
        m = types.ModuleType(f'geodbplug.{sub}')
        m.__path__ = [os.path.join(_REPO, path)]
        sys.modules.setdefault(f'geodbplug.{sub}', m)

    spec = importlib.util.spec_from_file_location(
        'geodbplug.processors.grid_processor',
        os.path.join(_REPO, 'processors', 'grid_processor.py'))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


grid_processor = _load_grid_processor()


class _Point:
    """Duck-typed QgsPointXY."""

    def __init__(self, x, y):
        self._x, self._y = float(x), float(y)

    def x(self):
        return self._x

    def y(self):
        return self._y


def _grid_claims(rows, cols, spacing=200.0):
    """Unrotated rows x cols grid, named r{row}c{col} (row 0 = north)."""
    claims = []
    for row in range(rows):
        for col in range(cols):
            claims.append({
                'feature_id': row * cols + col,
                'name': f'r{row}c{col}',
                'centroid': _Point(col * spacing, -row * spacing),
                'area': spacing * spacing,
            })
    return claims


class _FakeConfig:
    def get_claims_url(self, suffix):
        return f'https://api.test/v2/claims/{suffix}'


class _FakeApiClient:
    """Captures the request and replays a canned response."""

    def __init__(self, response):
        self.config = _FakeConfig()
        self.response = response
        self.last_request = None

    def _make_request(self, method, endpoint, data=None):
        self.last_request = {'method': method, 'endpoint': endpoint, 'data': data}
        return self.response


def _server_echo_response(claims_payload, order_by_index):
    """Build the REAL server response shape: geodb GridValidator returns the
    input claims (in INPUT order) with an ``order`` field added, under the
    ``claims`` key."""
    out = []
    for i, claim in enumerate(claims_payload):
        out.append({**claim, 'order': order_by_index[i]})
    return {'claims': out, 'statistics': {'claim_count': len(out)}}


class LocalFallbackTests(unittest.TestCase):
    """The deliberately simple offline ordering (divergence ruled 2026-07-11)."""

    def setUp(self):
        self.gp = grid_processor.GridProcessor(api_client=None)

    def test_unrotated_block_book_order(self):
        claims = _grid_claims(2, 3)
        ordered = self.gp._order_claims_local(claims, 'left_to_right_top_to_bottom')
        names = [c['name'] for c in ordered]
        self.assertEqual(
            names, ['r0c0', 'r0c1', 'r0c2', 'r1c0', 'r1c1', 'r1c2'])
        self.assertEqual([c['order'] for c in ordered], [1, 2, 3, 4, 5, 6])

    def test_column_first_direction(self):
        claims = _grid_claims(2, 2)
        ordered = self.gp._order_claims_local(claims, 'top_to_bottom_left_to_right')
        names = [c['name'] for c in ordered]
        self.assertEqual(names, ['r0c0', 'r1c0', 'r0c1', 'r1c1'])

    def test_banding_port_removed(self):
        """The v2.22.1 banding port must stay removed (server-side only)."""
        self.assertFalse(hasattr(self.gp, '_band_by_rows'))
        self.assertFalse(hasattr(self.gp, '_band_into_rows'))


class ServerPathTests(unittest.TestCase):
    """Wire contract with the geodb order-claims/ endpoint."""

    def _run(self, response, n=4):
        claims = _grid_claims(2, 2) if n == 4 else _grid_claims(1, n)
        api = _FakeApiClient(response)
        gp = grid_processor.GridProcessor(api_client=api)
        ordered = gp._order_claims_server(claims, 'left_to_right_top_to_bottom')
        return api, claims, ordered

    def test_sends_direction_and_index_names(self):
        payload_claims = [
            {'name': str(i), 'centroid': {'easting': float(i), 'northing': 0.0}}
            for i in range(4)
        ]
        api, claims, _ = self._run(_server_echo_response(payload_claims, [1, 2, 3, 4]))
        sent = api.last_request['data']
        self.assertIn('direction', sent)
        self.assertNotIn('sort_direction', sent)
        self.assertEqual([c['name'] for c in sent['claims']],
                         ['0', '1', '2', '3'])
        self.assertTrue(api.last_request['endpoint'].endswith('order-claims/'))

    def test_parses_claims_key_and_sorts_by_order(self):
        # Server returns input order with 'order' ranks; reversed ranks must
        # come back reversed.
        payload_claims = [
            {'name': str(i), 'centroid': {'easting': 0.0, 'northing': 0.0}}
            for i in range(4)
        ]
        response = _server_echo_response(payload_claims, [4, 3, 2, 1])
        _, claims, ordered = self._run(response)
        self.assertEqual([c['name'] for c in ordered],
                         ['r1c1', 'r1c0', 'r0c1', 'r0c0'])
        self.assertEqual([c['order'] for c in ordered], [1, 2, 3, 4])

    def test_empty_response_raises(self):
        with self.assertRaises(ValueError):
            self._run({'claims': [], 'statistics': {}})

    def test_legacy_shape_missing_claims_key_raises(self):
        """The old silent no-op: a response without 'claims' must raise now."""
        with self.assertRaises(ValueError):
            self._run({'ordered_claims': [{'name': '0', 'order': 1}]})

    def test_mismatched_count_raises(self):
        payload_claims = [
            {'name': '0', 'centroid': {'easting': 0.0, 'northing': 0.0}}]
        with self.assertRaises(ValueError):
            self._run(_server_echo_response(payload_claims, [1]))

    def test_malformed_item_raises(self):
        response = {'claims': [{'name': 'not-an-index', 'order': 1}] * 4}
        with self.assertRaises(ValueError):
            self._run(response)

    def test_error_response_raises(self):
        with self.assertRaises(ValueError):
            self._run({'error': 'boom'})


class LoudFallbackTests(unittest.TestCase):
    def test_warn_helper_survives_headless(self):
        gp = grid_processor.GridProcessor(api_client=None)
        # Must not raise even with stubbed/absent QGIS UI
        gp._warn_simple_ordering_applied('working offline')


if __name__ == '__main__':
    unittest.main()
