"""Helpers for capturing user-moved monument positions as server overrides.

When the user drags a discovery monument in QGIS (Step 5 — Adjust) and then
triggers any server-side regeneration (full preview rebuild or LM-corner
rotation), the moved positions must be sent back to the server as
`monument_overrides` in the processing_options. Without this hand-off the
server's algorithm recomputes a fresh LM and the user's move is silently
clobbered when the new layers are written back.

Server contract (lm-placement-improvements worktree, 2026-05-05):
    processing_options['monument_overrides'] = {
        '<claim_name>': {'lat': float, 'lon': float,
                          'easting': float, 'northing': float},
        ...
    }
The server replaces whichever LM the algorithm produced with the override
position for any claim whose name appears in this dict, and tags the
result `manual_override=True` so downstream emitters know it's user-set.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

try:
    from qgis.core import QgsCoordinateReferenceSystem, QgsCoordinateTransform, QgsProject
except ImportError:  # pragma: no cover — non-QGIS test contexts
    QgsCoordinateReferenceSystem = None  # type: ignore
    QgsCoordinateTransform = None  # type: ignore
    QgsProject = None  # type: ignore

from ..utils.layer_utils import is_layer_valid


def _build_transform_fn(source_crs) -> Optional[Callable[[float, float], tuple]]:
    """Return a (easting, northing) → (lat, lon) callable, or None if the
    transform can't be built (no source CRS, no QGIS, etc.)."""
    if not source_crs or QgsCoordinateReferenceSystem is None:
        return None
    wgs84 = QgsCoordinateReferenceSystem('EPSG:4326')
    transform = QgsCoordinateTransform(source_crs, wgs84, QgsProject.instance())

    def _to_wgs84(easting: float, northing: float) -> tuple:
        point = transform.transform(easting, northing)
        return point.y(), point.x()  # lat, lon

    return _to_wgs84


# States where the LM must be at a claim corner by statute. Discovery-
# monument overrides have no legal use in these states — any "moved
# monument" position would be a non-corner location, which is exactly
# what Idaho and New Mexico regs forbid. Sending an override for an
# ID/NM claim resurrects the regulatorily-invalid Phase-3 placement
# the server-side rip-out (2026-05-06) was designed to prevent.
LM_AT_CORNER_STATES = {'ID', 'NM'}


def _state_for_claim(claims_layer, claim_name: str) -> Optional[str]:
    """Look up a claim's state from the Lode Claims layer's `State` attr.

    Returns the postal abbreviation (e.g. 'ID', 'NM', 'NV') or None if the
    layer is missing, the feature isn't found, or the State attribute is
    blank. Used by `read_monument_overrides_from_state` to filter out
    LM-at-corner-state claims that should never carry an override.
    """
    if not is_layer_valid(claims_layer):
        return None
    fields = claims_layer.fields()
    if 'State' not in fields.names() or 'Name' not in fields.names():
        return None
    for feat in claims_layer.getFeatures():
        try:
            if feat['Name'] == claim_name:
                state = feat['State']
                return (state or '').strip().upper() or None
        except (KeyError, IndexError):
            continue
    return None


def read_monument_overrides_from_state(
    state: Any,
    logger=None,
) -> Dict[str, Dict[str, float]]:
    """Read user-positioned discovery monuments from the current layers.

    Returns a dict keyed by claim name suitable for direct inclusion as
    `processing_options['monument_overrides']` in a server request.

    Empty dict (not None) when no monuments are present or the layer
    references are stale — server treats absent claims as "use the
    algorithm's placement," so this is the correct no-op shape.

    Skips claims whose state is in LM_AT_CORNER_STATES (ID, NM) —
    overrides have no legal use there because the LM must always be at
    a corner. The skip protects against a feedback loop where stale
    Monuments layer features (e.g. from a previous Phase-3 emission) get
    replayed to the server as "overrides," server applies them, and the
    plugin then renders them as fresh "user-moved" monuments next time
    around.

    Currently captures discovery_monument positions only. Sideline/endline
    overrides for AZ/WY/SD aren't part of the server's monument_overrides
    contract — those states keep witness-point handling for adjacency.
    """
    overrides: Dict[str, Dict[str, float]] = {}

    monuments_layer = getattr(state, 'monuments_layer', None)
    if not is_layer_valid(monuments_layer):
        return overrides

    claims_layer = getattr(state, 'claims_layer', None)
    source_crs = claims_layer.crs() if is_layer_valid(claims_layer) else None
    transform = _build_transform_fn(source_crs)
    if transform is None:
        # Without a CRS transform we'd be sending UTM as lat/lon. Better
        # to send nothing — server falls back to the algorithm.
        if logger is not None:
            logger.warning(
                "[CLAIMS] Cannot build CRS transform for monument overrides; "
                "skipping override pass-through (server will recompute LM placement)."
            )
        return overrides

    skipped_id_nm = 0
    for feature in monuments_layer.getFeatures():
        try:
            claim_name = feature['Claim']
        except (KeyError, IndexError):
            continue
        if not claim_name:
            continue
        # ID/NM claims never carry a valid LM override (LM at corner per
        # statute). Stale features for these claims must not be replayed
        # to the server as user moves.
        claim_state = _state_for_claim(claims_layer, claim_name)
        if claim_state in LM_AT_CORNER_STATES:
            skipped_id_nm += 1
            continue
        geom = feature.geometry()
        if geom.isEmpty():
            continue
        point = geom.asPoint()
        lat, lon = transform(point.x(), point.y())
        overrides[claim_name] = {
            'lat': round(lat, 7),
            'lon': round(lon, 7),
            'easting': round(point.x(), 8),
            'northing': round(point.y(), 8),
        }

    if logger is not None:
        if overrides:
            logger.info(
                f"[CLAIMS] Captured {len(overrides)} monument override(s) from "
                f"existing layer geometry — sending as monument_overrides to "
                f"preserve user-moved positions across regeneration."
            )
        if skipped_id_nm:
            logger.info(
                f"[CLAIMS] Skipped {skipped_id_nm} monument feature(s) for ID/NM "
                f"claims — those states require the LM at a corner, so overrides "
                f"have no legal use and stale features must not be replayed."
            )

    return overrides
