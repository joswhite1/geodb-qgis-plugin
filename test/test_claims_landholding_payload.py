# -*- coding: utf-8 -*-
"""Payload tests for ClaimsManager._format_landholding (claim-type axis, 2026-08-31).

Runs WITHOUT QGIS: qgis/Qt modules are stubbed before import so the payload
builder can be verified with plain ``python3 test/test_claims_landholding_payload.py``
from the repo root. (Do not run via ``-m unittest test.<name>`` — the test
package __init__ imports the real qgis for the QGIS-environment suites.)

What this pins
--------------

The wizard has always KNOWN the claim type and never told the server. The push
payload carried no ``land_status`` at all, so every claim — lode, placer, mill
site alike — landed on the server serializer's blanket default, which resolves
the company's LODE type. A mill site pushed from QGIS became a Lode Claim, and
after Sept 1 the maintenance-fee surfaces believed it.

The server field (`NaturalKeyLandHoldingTypeField`) is strict and that shapes
the whole design:

* it REQUIRES a dict with both ``name`` and ``company`` — a bare string is a
  400 that fails the entire push, all 700 claims;
* it does NOT auto-create — an unknown name is a 400 too.

So the resolver only ever emits a name the SERVER told us exists (fetched via
``get_landholding_types``), and returns None for everything else. Omitting the
key is the safe answer: the server default then applies, which is exactly what
happened before this change. A guess would break the push.
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


def _load_claims_manager():
    for name in ('qgis', 'qgis.core', 'qgis.utils', 'qgis.gui', 'qgis.PyQt',
                 'qgis.PyQt.QtCore', 'qgis.PyQt.QtWidgets',
                 'qgis.PyQt.QtGui', 'qgis.PyQt.QtNetwork',
                 'qgis.PyQt.QtXml'):
        if name not in sys.modules:
            _stub_module(name)

    pkg = types.ModuleType('geodbplug')
    pkg.__path__ = [_REPO]
    sys.modules.setdefault('geodbplug', pkg)
    for sub in ('managers', 'api', 'utils', 'models'):
        m = types.ModuleType(f'geodbplug.{sub}')
        m.__path__ = [os.path.join(_REPO, sub)]
        sys.modules.setdefault(f'geodbplug.{sub}', m)

    for sub, filename in (
        ('api', 'exceptions.py'), ('api', 'client.py'),
        ('utils', 'config.py'), ('utils', 'logger.py'),
        ('models', 'schemas.py'),
    ):
        name = f'geodbplug.{sub}.{filename[:-3]}'
        if name in sys.modules:
            continue
        spec = importlib.util.spec_from_file_location(
            name, os.path.join(_REPO, sub, filename))
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        spec.loader.exec_module(mod)

    spec = importlib.util.spec_from_file_location(
        'geodbplug.managers.claims_manager',
        os.path.join(_REPO, 'managers', 'claims_manager.py'))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


claims_manager = _load_claims_manager()


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
_SEEDED = [
    {'name': 'Lode Claim', 'company': {'name': 'Test Mining Co'}},
    {'name': 'Placer Claim', 'company': {'name': 'Test Mining Co'}},
    {'name': 'Mill Site', 'company': {'name': 'Test Mining Co'}},
    {'name': 'Tunnel Site', 'company': {'name': 'Test Mining Co'}},
]


class _FakeAPI:
    """Stands in for APIClient — records calls, serves a type list."""

    def __init__(self, types_payload=None, raises=False):
        self._types = _SEEDED if types_payload is None else types_payload
        self._raises = raises
        self.type_calls = []

    def get_landholding_types(self, company_id):
        self.type_calls.append(company_id)
        if self._raises:
            raise RuntimeError('network down')
        return self._types


def _claim(name='TST 1', claim_type=None):
    """A processed claim as Step 6 hands it to the push."""
    out = {
        'name': name,
        'state': 'NV',
        'county': 'Elko',
        'rotated_geometry': {
            'type': 'Polygon',
            'coordinates': [[[-117.0, 40.0], [-116.99, 40.0],
                             [-116.99, 40.01], [-117.0, 40.01],
                             [-117.0, 40.0]]],
        },
    }
    if claim_type is not None:
        out['claim_type'] = claim_type
    return out


class _Base(unittest.TestCase):

    def _manager(self, api=None):
        mgr = claims_manager.ClaimsManager.__new__(
            claims_manager.ClaimsManager)
        mgr.api = api or _FakeAPI()
        mgr.config = _Dummy()
        mgr.logger = _Dummy()
        mgr._landholding_type_cache = {}
        return mgr

    def _format(self, claim, company_id=42, api=None):
        mgr = self._manager(api)
        return mgr._format_landholding(claim, 7, 26911, None, company_id)


class LandStatusEmittedTests(_Base):
    """THE REGRESSION — the payload used to carry no land_status at all."""

    def test_a_lode_claim_emits_the_natural_key(self):
        payload = self._format(_claim(claim_type='lode'))

        self.assertEqual(payload['land_status'],
                         {'name': 'Lode Claim', 'company': 'Test Mining Co'})

    def test_a_mill_site_is_not_flattened_to_lode(self):
        """The claim this whole change exists for."""
        payload = self._format(_claim(claim_type='mill'))

        self.assertEqual(payload['land_status']['name'], 'Mill Site')

    def test_a_placer_claim_emits_placer(self):
        payload = self._format(_claim(claim_type='placer'))

        self.assertEqual(payload['land_status']['name'], 'Placer Claim')

    def test_both_millsite_spellings_resolve(self):
        for spelling in ('mill', 'mill_site', 'millsite', 'MILL_SITE'):
            with self.subTest(spelling=spelling):
                payload = self._format(_claim(claim_type=spelling))
                self.assertEqual(payload['land_status']['name'], 'Mill Site')

    def test_both_tunnel_spellings_resolve(self):
        for spelling in ('tunnel', 'tunnel_site'):
            with self.subTest(spelling=spelling):
                payload = self._format(_claim(claim_type=spelling))
                self.assertEqual(payload['land_status']['name'], 'Tunnel Site')

    def test_the_key_is_a_dict_with_both_required_fields(self):
        """A bare string 400s the push. Shape is load-bearing."""
        payload = self._format(_claim(claim_type='lode'))
        key = payload['land_status']

        self.assertIsInstance(key, dict)
        self.assertEqual(set(key), {'name', 'company'})
        self.assertIsInstance(key['name'], str)
        self.assertIsInstance(key['company'], str)

    def test_the_company_name_comes_from_the_server_not_invented(self):
        api = _FakeAPI([
            {'name': 'Lode Claim', 'company': {'name': 'Renamed Holdings'}},
        ])
        payload = self._format(_claim(claim_type='lode'), api=api)

        self.assertEqual(payload['land_status']['company'], 'Renamed Holdings')

    def test_a_flat_company_string_is_accepted(self):
        """The endpoint has been seen serving company as a plain string."""
        api = _FakeAPI([{'name': 'Lode Claim', 'company': 'Flat Co'}])
        payload = self._format(_claim(claim_type='lode'), api=api)

        self.assertEqual(payload['land_status']['company'], 'Flat Co')


class OmitsRatherThanGuessesTests(_Base):
    """Every path that cannot resolve must OMIT — never send a guess."""

    def test_no_claim_type_omits_land_status(self):
        """The pre-change behaviour, preserved exactly."""
        payload = self._format(_claim())

        self.assertNotIn('land_status', payload)

    def test_an_empty_claim_type_omits_land_status(self):
        payload = self._format(_claim(claim_type=''))

        self.assertNotIn('land_status', payload)

    def test_an_unrecognised_claim_type_omits_land_status(self):
        payload = self._format(_claim(claim_type='homestead'))

        self.assertNotIn('land_status', payload)

    def test_no_company_id_omits_land_status(self):
        """Without a company there is no natural key to build."""
        payload = self._format(_claim(claim_type='lode'), company_id=None)

        self.assertNotIn('land_status', payload)

    def test_a_type_the_company_does_not_have_omits_land_status(self):
        """Never send a name the server has not confirmed — it 400s."""
        api = _FakeAPI([
            {'name': 'Lode Claim', 'company': {'name': 'Test Mining Co'}},
        ])
        payload = self._format(_claim(claim_type='tunnel'), api=api)

        self.assertNotIn('land_status', payload)

    def test_a_types_lookup_failure_omits_and_never_raises(self):
        """A push of 700 claims must not die because a lookup timed out."""
        api = _FakeAPI(raises=True)

        payload = self._format(_claim(claim_type='lode'), api=api)

        self.assertNotIn('land_status', payload)


class CachingTests(_Base):
    """One types call per company, not one per claim."""

    def test_the_type_list_is_fetched_once_for_many_claims(self):
        api = _FakeAPI()
        mgr = self._manager(api)

        for i in range(25):
            mgr._format_landholding(
                _claim(f'TST {i}', 'lode'), 7, 26911, None, 42)

        self.assertEqual(api.type_calls, [42])

    def test_a_failed_lookup_is_not_retried_per_claim(self):
        api = _FakeAPI(raises=True)
        mgr = self._manager(api)

        for i in range(10):
            mgr._format_landholding(
                _claim(f'TST {i}', 'lode'), 7, 26911, None, 42)

        self.assertEqual(len(api.type_calls), 1)


class UnchangedPayloadTests(_Base):
    """Nothing else about the payload moved."""

    def test_the_rest_of_the_payload_is_untouched(self):
        payload = self._format(_claim(claim_type='lode'))

        self.assertEqual(payload['name'], 'TST 1')
        self.assertEqual(payload['project'], 7)
        self.assertEqual(payload['source'], 'qclaims')
        self.assertEqual(payload['claim_status'], 'PL')
        self.assertEqual(payload['state'], 'NV')
        self.assertEqual(payload['county'], 'Elko')
        self.assertIsNotNone(payload['geometry'])

    def test_epsg_is_still_stripped_by_the_schema_filter(self):
        """PRE-EXISTING and out of scope, pinned so it is not mistaken for
        fallout from this change: `epsg` is set on the payload dict and then
        removed by `filter_for_push`, because LAND_HOLDING_SCHEMA does not
        declare an `epsg` field. Verified against the pre-change tree.
        The server therefore derives EPSG from the WKT/manual_geometry rather
        than being told. Not touched here."""
        payload = self._format(_claim(claim_type='lode'))

        self.assertNotIn('epsg', payload)

    def test_land_status_survives_the_schema_push_filter(self):
        """`filter_for_push` drops unknown keys — land_status must not be
        one. It is declared writable on LAND_HOLDING_SCHEMA; this pins that
        the declaration and the emitter agree."""
        payload = self._format(_claim(claim_type='lode'))

        self.assertIn(
            'land_status', payload,
            "filter_for_push stripped land_status — check the schema")


if __name__ == '__main__':
    unittest.main()
