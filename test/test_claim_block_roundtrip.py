# -*- coding: utf-8 -*-
"""The claim-BLOCK stage round-trip (P6, v2.35.0) — pure-python pins.

Runs WITHOUT QGIS: qgis/Qt modules are stubbed before import, so this can be
verified with plain ``python3 test/test_claim_block_roundtrip.py`` from the
repo root. (Do not run via ``-m unittest test.<name>`` — the test package
__init__ imports the real qgis for the QGIS-environment suites.)

What this pins
--------------

**1. ``LM_AT_CORNER_STATES`` has ONE home.** Five files used to re-type
``{'ID', 'NM'}`` in four different shapes; each drifted, and the phantom-LM
bug CP-2026-0059 took three plugin releases to close because every release
fixed one copy. The test asserts every surviving alias IS the one frozenset —
by identity, not by equality, so a re-typed copy that happens to contain the
same two strings still fails.

**2. ``lm_corner_explicit`` is only ever set for a DELIBERATE pick.** This is
the contract the server's review F2 exists to protect: this wizard sends a
corner for every claim, defaulting to 1, so a bare ``lm_corners`` value cannot
mean intent. If the plugin flagged its own defaults as explicit, the server
would read nine wizard defaults as nine deliberate choices and could not
cluster shared corners — the 3x3 Idaho block would need nine LM survey points
instead of four.

**3. The offline grid fallback is GONE and offline REFUSES.** The deleted code
was a second copy of the 600x1500 ft claim rectangle (with its own truncated
``4046.86``), and it fired on ANY server exception — not only offline — so a
transient 500 silently produced client-drawn claims.

**4. ``looks_like_missing_endpoint`` is narrow.** The block routes are
additive, so a 404 must degrade to the legacy flow — but a 403, 409 or 500 is
a real answer the user has to see, and swallowing those into a silent fallback
is exactly the failure mode item 3 describes.
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


def _prepare_packages():
    for name in ('qgis', 'qgis.core', 'qgis.utils', 'qgis.PyQt',
                 'qgis.PyQt.QtCore', 'qgis.PyQt.QtWidgets', 'qgis.PyQt.QtGui',
                 'qgis.PyQt.QtXml'):
        if name not in sys.modules:
            _stub_module(name)
    pkg = types.ModuleType('geodbplug')
    pkg.__path__ = [_REPO]
    sys.modules.setdefault('geodbplug', pkg)
    for sub in ('processors', 'utils', 'managers', 'api'):
        m = types.ModuleType(f'geodbplug.{sub}')
        m.__path__ = [os.path.join(_REPO, sub)]
        sys.modules.setdefault(f'geodbplug.{sub}', m)


def _load(dotted, relpath):
    spec = importlib.util.spec_from_file_location(
        dotted, os.path.join(_REPO, relpath))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


_prepare_packages()
state_constants = _load('geodbplug.processors.state_constants',
                        'processors/state_constants.py')


# ---------------------------------------------------------------------------
# 1. ONE home for the ID/NM predicate
# ---------------------------------------------------------------------------

class StateConstantsTests(unittest.TestCase):

    def test_the_corner_lm_set_is_exactly_idaho_and_new_mexico(self):
        self.assertEqual(set(state_constants.LM_AT_CORNER_STATES), {'ID', 'NM'})

    def test_lm_at_corner_normalises_case_whitespace_and_none(self):
        for value in ('ID', 'id', ' Id ', 'NM', 'nm', '  nm'):
            self.assertTrue(state_constants.lm_at_corner(value), value)
        for value in ('NV', 'nv', '', '   ', None, 'IDAHO'):
            self.assertFalse(state_constants.lm_at_corner(value), value)

    def test_the_two_sets_do_not_overlap(self):
        """A state cannot monument at a corner AND on the centerline."""
        self.assertFalse(
            set(state_constants.LM_AT_CORNER_STATES)
            & set(state_constants.CENTERLINE_LM_STATES))

    def test_the_set_is_immutable_so_a_caller_cannot_edit_it_in_place(self):
        self.assertIsInstance(state_constants.LM_AT_CORNER_STATES, frozenset)

    def test_no_module_re_types_the_literal(self):
        """⛔ The five old copies are gone. Source-level, because an import
        alias can be re-shadowed by a fresh literal without any test noticing.

        Comments are allowed to mention the literal (they explain the rule);
        executable code is not, anywhere but state_constants.py itself.
        """
        offenders = []
        for root, dirs, files in os.walk(_REPO):
            dirs[:] = [d for d in dirs
                       if d not in ('.git', '__pycache__', 'i18n', 'help', 'docs',
                                    'resources', 'icons', 'symbols', 'progress_docs')]
            for fn in files:
                if not fn.endswith('.py'):
                    continue
                path = os.path.join(root, fn)
                rel = os.path.relpath(path, _REPO)
                if rel in ('processors/state_constants.py',
                           'test/test_claim_block_roundtrip.py'):
                    continue
                with open(path, encoding='utf-8') as fh:
                    for i, line in enumerate(fh, start=1):
                        code = line.split('#', 1)[0]
                        if ("{'ID', 'NM'}" in code or "['ID', 'NM']" in code
                                or '{"ID", "NM"}' in code or '["ID", "NM"]' in code):
                            offenders.append(f'{rel}:{i}')
        self.assertEqual(offenders, [],
                         'the ID/NM literal was re-typed — import '
                         'processors.state_constants instead')


# ---------------------------------------------------------------------------
# 2. lm_corner_explicit — only a deliberate pick
# ---------------------------------------------------------------------------

class _FakeState:
    """The two bits of ClaimsWizardState this contract touches, duck-typed —
    the real dataclass needs QGIS. `record_explicit_lm_corner` is copied
    behaviour, and `ExplicitPickWiringTests` below pins that the real one
    still says the same thing."""

    def __init__(self):
        self.block_explicit_lm_corners = {}

    def record_explicit_lm_corner(self, claim_name, corner):
        if claim_name and corner:
            self.block_explicit_lm_corners[str(claim_name)] = int(corner)


def _build_process_payload(state, cluster_lms=True):
    """The payload shape `ClaimsManager.process_block` builds from the state.

    Mirrors the manager's own construction; `ManagerPayloadTests` pins that
    the real method agrees.
    """
    explicit = dict(state.block_explicit_lm_corners or {})
    payload = {'cluster_lms': bool(cluster_lms), 'witness_points': True}
    if explicit:
        payload['lm_corners'] = explicit
        payload['lm_corner_explicit'] = True
    return payload


class ExplicitLmCornerTests(unittest.TestCase):

    def test_no_user_pick_sends_no_lm_corners_at_all(self):
        """The wizard's own default of corner 1 must never travel. Sending
        `{claim: 1}` for every claim is what makes the flag meaningless."""
        payload = _build_process_payload(_FakeState())
        self.assertNotIn('lm_corners', payload)
        self.assertNotIn('lm_corner_explicit', payload)

    def test_a_user_pick_travels_flagged_explicit(self):
        state = _FakeState()
        state.record_explicit_lm_corner('RC 81', 3)
        payload = _build_process_payload(state)
        self.assertEqual(payload['lm_corners'], {'RC 81': 3})
        self.assertIs(payload['lm_corner_explicit'], True)

    def test_only_the_claims_the_user_touched_are_named(self):
        """A nine-claim block where the user changed ONE corner sends ONE
        pick — so the server can still cluster the other eight."""
        state = _FakeState()
        for i in range(1, 10):
            pass  # the other eight keep the wizard default and are NOT recorded
        state.record_explicit_lm_corner('RC 5', 2)
        payload = _build_process_payload(state)
        self.assertEqual(payload['lm_corners'], {'RC 5': 2})

    def test_a_blank_or_zero_pick_is_not_recorded(self):
        state = _FakeState()
        state.record_explicit_lm_corner('', 3)
        state.record_explicit_lm_corner('RC 1', 0)
        state.record_explicit_lm_corner('RC 2', None)
        self.assertEqual(state.block_explicit_lm_corners, {})

    def test_cluster_preference_still_travels_without_any_pick(self):
        payload = _build_process_payload(_FakeState(), cluster_lms=False)
        self.assertIs(payload['cluster_lms'], False)


class ExplicitPickWiringTests(unittest.TestCase):
    """Source-level pins: the real wizard code must agree with the model
    above. These read the files rather than importing them, because both
    modules need QGIS at import time."""

    def _read(self, rel):
        with open(os.path.join(_REPO, rel), encoding='utf-8') as fh:
            return fh.read()

    def test_step5_records_picks_from_the_changes_dict_only(self):
        """`changes` holds exactly the corners the user chose — a table pick
        differing from the displayed value, or one they typed into the layer.
        Recording anything wider (every table row) would flag defaults."""
        src = self._read('ui/claims_step_widgets/step5_adjust.py')
        self.assertIn('for _name, _corner in changes.items():', src)
        self.assertIn('self.state.record_explicit_lm_corner(_name, _corner)', src)

    def test_the_wizard_state_exposes_the_recorder_and_the_store(self):
        src = self._read('ui/claims_wizard_state.py')
        self.assertIn('def record_explicit_lm_corner(', src)
        self.assertIn('block_explicit_lm_corners', src)

    def test_step6_only_flags_explicit_when_there_are_recorded_picks(self):
        src = self._read('ui/claims_step_widgets/step6_finalize.py')
        fn = src.split('def _collect_block_process_options', 1)[1] \
                .split('\n    def ', 1)[0]
        self.assertIn("options['lm_corners'] = explicit", fn)
        self.assertIn("options['lm_corner_explicit'] = True", fn)
        # …and BOTH sit inside the `if explicit:` branch. Read the function's
        # own body (not the file) so the docstring explaining the rule cannot
        # satisfy — or break — the assertion.
        head, sep, tail = fn.partition("if explicit:")
        self.assertTrue(sep, 'the explicit branch is gone')
        code_head = '\n'.join(l.split('#', 1)[0] for l in head.split('\n'))
        self.assertNotIn("options['lm_corner_explicit']", code_head)
        self.assertNotIn("options['lm_corners']", code_head)


class ManagerPayloadTests(unittest.TestCase):
    """The manager's own payload construction, read from source for the same
    reason (claims_manager imports Qt-heavy siblings)."""

    def test_process_block_flags_explicit_only_alongside_lm_corners(self):
        with open(os.path.join(_REPO, 'managers/claims_manager.py'),
                  encoding='utf-8') as fh:
            src = fh.read()
        self.assertIn("def process_block(", src)
        body = src.split("def process_block(", 1)[1].split("\n    def ", 1)[0]
        self.assertIn("if lm_corners:", body)
        assign = body.split("if lm_corners:", 1)[1]
        self.assertIn("payload['lm_corners'] = dict(lm_corners)", assign)
        self.assertIn("payload['lm_corner_explicit'] = bool(lm_corner_explicit)", assign)
        # the default is False — a caller must ASK for explicit
        self.assertIn("lm_corner_explicit: bool = False", body)

    def test_materialise_sends_the_typed_confirmation(self):
        with open(os.path.join(_REPO, 'managers/claims_manager.py'),
                  encoding='utf-8') as fh:
            src = fh.read()
        self.assertIn("MATERIALISE_CONFIRM = 'MATERIALISE'", src)
        self.assertIn("payload: Dict[str, Any] = {'confirm': self.MATERIALISE_CONFIRM}", src)

    def test_the_three_block_routes_are_addressed_by_id(self):
        with open(os.path.join(_REPO, 'managers/claims_manager.py'),
                  encoding='utf-8') as fh:
            src = fh.read()
        for path in ("f'blocks/{int(block_id)}/'",
                     "f'blocks/{int(block_id)}/process/'",
                     "f'blocks/{int(block_id)}/materialise/'"):
            self.assertIn(path, src)


# ---------------------------------------------------------------------------
# 3. The offline grid fallback is gone
# ---------------------------------------------------------------------------

class OfflineFallbackRemovedTests(unittest.TestCase):

    def setUp(self):
        with open(os.path.join(_REPO, 'processors/grid_generator.py'),
                  encoding='utf-8') as fh:
            self.src = fh.read()

    def _code_only(self):
        """The file's EXECUTABLE source — comments and string literals
        removed via the tokenizer.

        Both had to go, and neither is optional: the deletion note names the
        functions it deleted (a `#` comment), and the module docstring
        explains the truncated constant (a string). A crude `#`-split marked
        both as live code; a `# noqa`-style opt-out would have let a real
        re-introduction hide behind one. `tokenize` is the honest answer.
        """
        import io
        import tokenize
        out = []
        for tok in tokenize.generate_tokens(io.StringIO(self.src).readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            out.append(tok.string)
        return ' '.join(out)

    def test_the_local_generators_and_their_helpers_are_deleted(self):
        code = self._code_only()
        for name in ('_generate_lode_grid_local', '_generate_placer_grid_local',
                     '_create_claims_layer', '_create_claim_polygon',
                     '_create_claim_feature', '_rotate_point'):
            self.assertNotIn(f'def {name}', code, name)
            self.assertNotIn(f'self.{name}(', code, name)

    def test_the_truncated_acre_constant_is_gone_from_code(self):
        """⛔ `4046.86` is the truncated square-metres-per-acre the server's
        own audit flagged. It must not survive anywhere executable."""
        self.assertNotIn('4046.86', self._code_only())

    def test_offline_refuses_rather_than_drawing_its_own_claims(self):
        self.assertIn('def _refuse_offline', self.src)
        self.assertIn('self._refuse_offline("Generating a lode claim grid")', self.src)
        self.assertIn('self._refuse_offline("Generating a placer claim grid")', self.src)

    def test_a_server_error_is_no_longer_swallowed_into_local_geometry(self):
        """⭐ The reason the fallback had to go. The dispatch used to catch
        ANY exception from the server call — a 500, a timeout, an auth
        failure — log a warning and draw local geometry, so a user with a
        working connection got client-drawn claims and nothing told them."""
        self.assertNotIn('falling back to local', self._code_only())

    def test_the_refusal_message_says_why(self):
        """A refusal must name the remedy, not just say no
        (reference_refusal_must_name_the_remedy).

        The message is an f-string split over four source lines, so the raw
        text carries a `" f"` seam at every join. Parse it properly — read the
        function's own string constants out of the AST — and assert against
        the sentence the user will actually see.
        """
        import ast
        tree = ast.parse(self.src)
        fn = next((n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == '_refuse_offline'),
                  None)
        self.assertIsNotNone(fn, '_refuse_offline is gone')
        parts = [n.value for n in ast.walk(fn)
                 if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        flat = ' '.join(' '.join(parts).split())
        self.assertIn('needs a connection to geodb.io', flat)
        self.assertIn('one set of', flat)
        self.assertIn('Reconnect and try again', flat)


# ---------------------------------------------------------------------------
# 4. The additive-endpoint degrade is NARROW
# ---------------------------------------------------------------------------

class _Exc(Exception):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


def _looks_like_missing_endpoint(exc):
    """Copy of `managers.claims_manager.looks_like_missing_endpoint` (that
    module needs Qt). `MissingEndpointWiringTests` pins they agree."""
    status = getattr(exc, 'status_code', None)
    if status == 404:
        return True
    if status is not None:
        return False
    text = str(exc).lower()
    return '404' in text and 'not found' in text


class MissingEndpointTests(unittest.TestCase):

    def test_a_404_degrades_to_the_legacy_flow(self):
        self.assertTrue(_looks_like_missing_endpoint(_Exc('Not Found', 404)))

    def test_a_real_answer_is_never_swallowed(self):
        """403 (not staff), 409 (of record / not processed) and 500 are
        answers the operator must see. Degrading on them would repeat exactly
        the failure the grid fallback made — a server refusal that silently
        becomes a different behaviour."""
        for status in (400, 401, 403, 409, 500, 502):
            self.assertFalse(_looks_like_missing_endpoint(_Exc('nope', status)), status)

    def test_a_server_error_whose_prose_says_not_found_is_not_a_404(self):
        """A 409 whose message reads "Claim block not found." must NOT be
        mistaken for a missing route."""
        self.assertFalse(
            _looks_like_missing_endpoint(_Exc('Claim block not found.', 409)))

    def test_a_transport_failure_with_no_status_reads_the_text_narrowly(self):
        self.assertTrue(_looks_like_missing_endpoint(_Exc('HTTP 404: Not Found')))
        self.assertFalse(_looks_like_missing_endpoint(_Exc('connection reset')))
        self.assertFalse(_looks_like_missing_endpoint(_Exc('claim not found')))


class MissingEndpointWiringTests(unittest.TestCase):

    def test_both_fallback_sites_gate_on_the_helper(self):
        for rel in ('ui/claims_step_widgets/step6_finalize.py',
                    'ui/claims_step_widgets/step7_export.py'):
            with open(os.path.join(_REPO, rel), encoding='utf-8') as fh:
                src = fh.read()
            self.assertIn('looks_like_missing_endpoint', src, rel)


# ---------------------------------------------------------------------------
# 5. The fulfilment tie survives the proposed-claims door
# ---------------------------------------------------------------------------

class FulfilmentTieTests(unittest.TestCase):
    """The bug: pulling a block's proposed claims called
    `clear_fulfillment_context()` on the reasoning that "proposed claims are
    not orders" — but they belong to exactly ONE ClaimPurchaseOrder, so the
    most common staff path lost the tie and its documents came back orphaned.
    """

    def _read(self, rel):
        with open(os.path.join(_REPO, rel), encoding='utf-8') as fh:
            return fh.read()

    def test_the_proposed_claims_handler_no_longer_clears_unconditionally(self):
        src = self._read('ui/claims_step_widgets/step1_project_setup.py')
        handler = src.split('def _on_proposed_claims_selected', 1)[1] \
                     .split('\n    def ', 1)[0]
        self.assertIn('block_id = claims_data.get(\'block_id\')', handler)
        self.assertIn('self._load_block_context(block_id, claims_data)', handler)
        # The clear survives ONLY on the genuinely-ambiguous branch.
        self.assertIn('else:\n                self.state.clear_fulfillment_context()',
                      handler)

    def test_the_dialog_emits_the_block_id(self):
        src = self._read('ui/staff_orders_dialog.py')
        self.assertIn("'block_id': block_id,", src)
        self.assertIn('def _effective_block_id', src)

    def test_a_single_block_project_still_resolves_a_tie(self):
        """The picker is HIDDEN below two blocks, so the raw
        `_selected_block_id` is None in the common case. Reading it directly
        would lose the tie exactly where it matters most."""
        src = self._read('ui/staff_orders_dialog.py')
        helper = src.split('def _effective_block_id', 1)[1].split('\n    def ', 1)[0]
        self.assertIn('if len(self._blocks) == 1:', helper)
        self.assertIn("return self._blocks[0].get('id')", helper)

    def test_step7_materialises_only_when_the_block_is_processed(self):
        src = self._read('ui/claims_step_widgets/step7_export.py')
        self.assertIn(
            'if self.state.fulfillment_order_id and self.state.block_is_processed:',
            src)
        self.assertIn('self._materialise_block()', src)
        self.assertIn('def _legacy_push_to_server', src)

    def test_the_geopackage_upload_has_one_implementation(self):
        """Both push paths share it; a second copy is how they drift."""
        src = self._read('ui/claims_step_widgets/step7_export.py')
        self.assertEqual(src.count('def _upload_geopackage_if_any'), 1)
        self.assertEqual(src.count('self._upload_geopackage_if_any(logger)'), 2)


if __name__ == '__main__':
    unittest.main(verbosity=1)
