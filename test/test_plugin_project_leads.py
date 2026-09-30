# -*- coding: utf-8 -*-
"""The plugin LEADS the server's active project (2026-09-30).

Runs WITHOUT QGIS: qgis/Qt modules are stubbed before import, so run it with
plain ``python3 test/test_plugin_project_leads.py`` from the repo root. (Do not
run via ``-m unittest test.<name>`` -- the test package __init__ imports the
real qgis for the QGIS-environment suites.)

The incident: a claims wizard run was started on one customer's project, the
project was switched in the browser, and on returning to QGIS the plugin had
adopted the browser's project -- every sign-in / restore / renewal / Refresh
re-read the server's single shared active project. The package, 54 claims, 84
stakes and the documents landed in another company's project.

What this pins:
* ProjectManager keeps the plugin's own project across a context reload and
  re-asserts it on the server; it adopts the server's only when it has none
  (or lost access), and an explicit company switch takes the server's answer.
* APIClient re-asserts the pinned project before requests (throttled, never
  on the auth / me / set-active endpoints, never recursively).
* ClaimsWizardState binds a GeoPackage to a project and names both projects
  when the plugin is on a different one.
"""

import importlib.util
import os
import sys
import types
import unittest


class _Dummy:
    """Truthy, callable, attribute-permissive stand-in for Qt/QGIS objects."""

    def __init__(self, *args, **kwargs):
        pass

    def __call__(self, *args, **kwargs):
        return _Dummy()

    def __getattr__(self, name):
        return _Dummy()

    def __iter__(self):
        raise TypeError('qgis-stub is not iterable')

    def __repr__(self):
        return '<qgis-stub>'


def _stub_module(name):
    mod = types.ModuleType(name)
    mod.__getattr__ = lambda attr: _Dummy()  # PEP 562
    sys.modules[name] = mod
    return mod


_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(dotted, relpath):
    for name in ('qgis', 'qgis.core', 'qgis.utils', 'qgis.PyQt', 'qgis.PyQt.QtCore',
                 'qgis.PyQt.QtWidgets', 'qgis.PyQt.QtGui', 'qgis.PyQt.QtNetwork'):
        if name not in sys.modules:
            _stub_module(name)
    pkg = types.ModuleType('geodbplug')
    pkg.__path__ = [_REPO]
    sys.modules.setdefault('geodbplug', pkg)
    for sub in ('api', 'managers', 'models', 'utils', 'ui'):
        m = types.ModuleType(f'geodbplug.{sub}')
        m.__path__ = [os.path.join(_REPO, sub)]
        sys.modules.setdefault(f'geodbplug.{sub}', m)
    spec = importlib.util.spec_from_file_location(
        f'geodbplug.{dotted}', os.path.join(_REPO, relpath))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


client_mod = _load('api.client', 'api/client.py')
pm_mod = _load('managers.project_manager', 'managers/project_manager.py')
auth_mod = sys.modules['geodbplug.models.auth']
state_mod = _load('ui.claims_wizard_state', 'ui/claims_wizard_state.py')

ME = 'https://api.test/me/'
SET_PROJECT = 'https://api.test/me/set-active-project/'


class _FakeConfig:
    endpoints = {
        'login': 'https://api.test/api-token-auth/', 'logout': 'https://api.test/api-logout/',
        'check_token': 'https://api.test/check-token/', 'me': ME,
        'set_active_company': 'https://api.test/me/set-active-company/',
        'set_active_project': SET_PROJECT,
    }


class _RecordingClient(client_mod.APIClient):
    """APIClient whose network layer records instead of sending."""

    def __init__(self):
        super().__init__(_FakeConfig(), token='tok')
        self.sent = []

    def _execute_request(self, method, request, data):
        return {}

    def _make_request(self, method, url, data=None, headers=None, progress_callback=None):
        self._reassert_project(url)
        self.sent.append((method, url, data))
        return {'active_project': {'id': (data or {}).get('project_id'), 'name': 'x'}}


class ReassertTests(unittest.TestCase):

    def setUp(self):
        self.c = _RecordingClient()

    def test_nothing_pinned_sends_no_reassert(self):
        self.c._make_request('GET', 'https://api.test/drill-collars/')
        self.assertEqual([u for _, u, _ in self.c.sent], ['https://api.test/drill-collars/'])

    def test_stale_pin_reasserts_once_before_the_request(self):
        self.c.pin_project(242, asserted=False)
        self.c._make_request('POST', 'https://api.test/landholdings/')
        self.c._make_request('GET', 'https://api.test/drill-collars/')
        self.assertEqual(self.c.sent[0], ('POST', SET_PROJECT, {'project_id': 242}))
        self.assertEqual([u for _, u, _ in self.c.sent[1:]],
                         ['https://api.test/landholdings/', 'https://api.test/drill-collars/'])

    def test_fresh_pin_waits_for_the_throttle_window(self):
        self.c.pin_project(242)  # just set on the server
        self.c._make_request('GET', 'https://api.test/drill-collars/')
        self.assertNotIn(SET_PROJECT, [u for _, u, _ in self.c.sent])
        self.c._project_asserted_at -= self.c.PROJECT_REASSERT_SECONDS + 1
        self.c._make_request('GET', 'https://api.test/drill-collars/')
        self.assertIn(SET_PROJECT, [u for _, u, _ in self.c.sent])

    def test_auth_and_context_endpoints_are_exempt(self):
        self.c.pin_project(242, asserted=False)
        self.c._make_request('GET', ME)
        self.assertEqual([u for _, u, _ in self.c.sent], [ME])

    def test_a_failed_reassert_stops_the_request(self):
        self.c.pin_project(242, asserted=False)

        def refuse(project_id):
            raise client_mod.APIException('no access')
        self.c.set_active_project = refuse
        with self.assertRaises(client_mod.APIException):
            self.c._make_request('POST', 'https://api.test/landholdings/')
        self.assertEqual(self.c.sent, [])


def _ctx(active_project_id, projects, user_status='manager'):
    """A UserContext with companies 4 + 77 and the given accessible projects."""
    companies = [auth_mod.Company(id=4, name='Lightning Creek'),
                 auth_mod.Company(id=77, name='Strata')]
    names = {4: 'Lightning Creek', 77: 'Strata'}
    accessible = [auth_mod.Project(id=pid, name=pname, company_name=names[cid])
                  for pid, pname, cid in projects]
    active = next((p for p in accessible if p.id == active_project_id), None)
    ctx = types.SimpleNamespace(
        accessible_companies=companies, accessible_projects=accessible,
        user_status=user_status, can_create=True, active_project=active,
        active_company=next((c for c in companies if active and c.name == active.company_name), None),
    )
    return ctx


PROJECTS = [(12, 'Auxer', 4), (242, 'Missile', 77)]


class _PMClient:
    def __init__(self):
        self.pins = []
        self.set_calls = []

    def pin_project(self, project_id, asserted=True):
        self.pins.append((project_id, asserted))

    def set_active_project(self, project_id):
        self.set_calls.append(project_id)
        return {'user_status': 'owner', 'can_create': True}


class ProjectManagerKeepsItsProjectTests(unittest.TestCase):

    def setUp(self):
        self.client = _PMClient()
        self.pm = pm_mod.ProjectManager(_FakeConfig(), self.client)
        self.pm._saved_project_id = lambda: None
        auth_mod.UserContext.from_api_response = staticmethod(
            lambda r: types.SimpleNamespace(user_status=r['user_status'], can_create=r['can_create']))

    def test_first_load_adopts_the_servers_project(self):
        self.pm.load_from_user_context(_ctx(242, PROJECTS))
        self.assertEqual(self.pm.active_project.id, 242)
        self.assertEqual(self.client.set_calls, [])
        self.assertEqual(self.client.pins[-1], (242, True))

    def test_the_incident_a_browser_switch_does_not_move_the_plugin(self):
        self.pm.load_from_user_context(_ctx(242, PROJECTS))          # started on Missile
        self.pm.load_from_user_context(_ctx(12, PROJECTS, 'viewer'))  # browser moved to Auxer
        self.assertEqual(self.pm.active_project.id, 242)
        self.assertEqual(self.pm.active_company.id, 77)
        self.assertEqual(self.client.set_calls, [242])   # server told to follow
        self.assertEqual(self.pm.user_status, 'owner')   # Missile's permission, not Auxer's

    def test_saved_qgis_project_wins_on_first_load(self):
        self.pm._saved_project_id = lambda: 242
        self.pm.load_from_user_context(_ctx(12, PROJECTS))
        self.assertEqual(self.pm.active_project.id, 242)
        self.assertEqual(self.client.set_calls, [242])

    def test_lost_access_falls_back_to_the_servers_project(self):
        self.pm.load_from_user_context(_ctx(242, PROJECTS))
        self.pm.load_from_user_context(_ctx(12, [(12, 'Auxer', 4)]))
        self.assertEqual(self.pm.active_project.id, 12)

    def test_explicit_company_switch_takes_the_servers_answer(self):
        self.pm.load_from_user_context(_ctx(242, PROJECTS))
        self.pm.load_from_user_context(_ctx(12, PROJECTS), keep_selection=False)
        self.assertEqual(self.pm.active_project.id, 12)
        self.assertEqual(self.client.set_calls, [])
        self.assertEqual(self.client.pins[-1], (None, True))  # unpinned until a project is picked

    def test_clear_selection_forgets_the_project(self):
        self.pm.load_from_user_context(_ctx(242, PROJECTS))
        self.pm.clear_selection()
        self.assertIsNone(self.pm.active_project)
        self.pm.load_from_user_context(_ctx(12, PROJECTS))
        self.assertEqual(self.pm.active_project.id, 12)


class GeoPackageBindingTests(unittest.TestCase):

    def _state(self, **kw):
        s = state_mod.ClaimsWizardState()
        s.project_id, s.project_name, s.company_name = 242, 'Missile', 'Strata'
        for k, v in kw.items():
            setattr(s, k, v)
        return s

    def test_unbound_geopackage_is_never_a_mismatch(self):
        self.assertIsNone(self._state().project_mismatch())

    def test_binding_takes_the_current_project_once(self):
        s = self._state()
        s.bind_to_current_project()
        s.project_id, s.project_name, s.company_name = 12, 'Auxer', 'Lightning Creek'
        s.bind_to_current_project()  # already bound: unchanged
        self.assertEqual(s.bound_project_id, 242)
        msg = s.project_mismatch()
        self.assertIn('Missile (Strata)', msg)
        self.assertIn('Auxer (Lightning Creek)', msg)

    def test_force_rebinds_for_a_server_listed_file(self):
        s = self._state(bound_project_id=12, bound_project_name='Auxer')
        s.bind_to_current_project(force=True)
        self.assertEqual(s.bound_project_id, 242)
        self.assertIsNone(s.project_mismatch())

    def test_save_binds_a_fresh_geopackage_but_not_one_with_a_package(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            fresh = self._state(geopackage_path=os.path.join(d, 'a.gpkg'))
            fresh.save_to_geopackage()
            self.assertEqual(fresh.bound_project_id, 242)

            reopened = self._state()
            reopened.project_id = 12
            self.assertTrue(reopened.load_from_geopackage(os.path.join(d, 'a.gpkg')))
            self.assertEqual(reopened.bound_project_id, 242)
            self.assertIsNotNone(reopened.project_mismatch())

            legacy = self._state(geopackage_path=os.path.join(d, 'b.gpkg'), claim_package_id=125)
            legacy.save_to_geopackage()
            self.assertIsNone(legacy.bound_project_id)


if __name__ == '__main__':
    unittest.main()
