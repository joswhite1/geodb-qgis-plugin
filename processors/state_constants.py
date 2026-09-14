# -*- coding: utf-8 -*-
"""State-law constants for claim monumenting — the plugin's ONE home.

**Mirror of the server's** ``geodb/services/claims/state_constants.py``.
A change to either file is a change to both; the server's docstring names
this file, and this one names it back, so neither side can be edited in
isolation without the other being one grep away.

⛔ Do not re-type ``{'ID', 'NM'}`` anywhere in this plugin. It used to live
as a literal in FIVE files in four different shapes:

* ``processors/monument_overrides.py``   — ``LM_AT_CORNER_STATES = {'ID', 'NM'}``
* ``managers/claims_manager.py``         — ``_LM_AT_CORNER_STATES = frozenset(...)``
* ``ui/claims_step_widgets/step5_adjust.py`` — ``in ['ID', 'NM']``
* ``processors/gpx_exporter.py``         — ``not in ['ID', 'NM']``
* ``processors/claims_map_generator.py`` — ``_NO_LM_DISPLAY_STATES = {'ID', 'NM'}``

Each copy drifted a little, and the phantom-LM bug (CP-2026-0059) took three
plugin releases to close because every release fixed ONE copy. All five now
import from here (the old names are kept as aliases at their original sites,
so existing call sites and tests keep working).

⚠️ The plugin cannot import the server — it ships into QGIS with no Django on
the path — which is why this is a MIRROR and not an import. That is the one
duplicate path in this area that is genuinely warranted, and it is documented
at both ends (CLAUDE.md: "if a parallel path is genuinely warranted, document
the divergence at the entry point of each").
"""

#: States whose statute puts the location monument AT A CORNER of the claim
#: (Idaho Code § 47-602; NMSA § 69-3-1). There is no separate LM stake — the
#: Corner-1 stake carries the LM role, and the server's
#: ``LandHolding.qclaims_data.lm_corner`` is always 1 after the processor
#: rotates the corner list.
#:
#: Consequences across the plugin, all of them this set's:
#: * no ``discovery`` waypoint is exported (``gpx_exporter``);
#: * no LM point is drawn on the map (``claims_map_generator``);
#: * a discovery-monument OVERRIDE is never sent — any "moved monument"
#:   position would be a non-corner location, which is exactly what these
#:   states' regs forbid (``monument_overrides``);
#: * Step 5 offers the corner-designation panel instead of monument drags.
LM_AT_CORNER_STATES = frozenset({'ID', 'NM'})

#: States whose statute puts the location (discovery) monument on the
#: CENTERLINE — the processor emits a separate ``discovery`` waypoint that
#: lands as a ``stake_type='LM'`` row. Complement of the corner-LM set over
#: the supported federal-claim states.
CENTERLINE_LM_STATES = frozenset({
    'CA', 'OR', 'WA', 'MT', 'NV', 'AZ', 'UT', 'WY', 'CO', 'AK', 'SD',
})


def lm_at_corner(state):
    """True when ``state`` (any case, blank-safe, ``None``-safe) monuments the
    location monument at a corner.

    Use this rather than ``state in LM_AT_CORNER_STATES``: the state arrives
    from a server payload, a layer attribute or a combo box, and the four old
    copies disagreed about case and whitespace.
    """
    return (state or '').strip().upper() in LM_AT_CORNER_STATES
