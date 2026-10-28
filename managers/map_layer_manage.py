"""Manage geoDB map layers from QGIS (usability Q-4 / Q-5, 2026-10-28).

The words and the rules of the "geoDB map layers" panel and the "Field
sketches" list, with no Qt in them, so the panel stays a thin view and the
headless e2e can drive the same functions. Every action is the SERVER's
(``vector-layers/<id>/<verb>/`` over the web Map layers card's own helpers);
this module only decides which buttons a row offers and calls them.

Status words a person reads: live (on the web map and crew phones), draft
(only in this list until published), archived (off every map, kept), in the
Trash (a discarded draft; restore brings it back).
"""
from typing import Any, Dict, List, Optional

STATUS_WORDS = {
    'active': 'Live',
    'draft': 'Draft',
    'archived': 'Archived',
}
TRASH_WORD = 'In the Trash'

#: Every action the panel can offer, in button order: (key, label).
ACTIONS = (
    ('rename', 'Rename…'),
    ('kind', 'Change kind…'),
    ('phones_off', 'Hide on crew phones'),
    ('phones_on', 'Show on crew phones'),
    ('publish', 'Publish'),
    ('publish_alongside', 'Publish alongside'),
    ('archive', 'Archive'),
    ('reactivate', 'Make live again'),
    ('discard', 'Discard draft'),
    ('restore', 'Restore'),
)


def status_word(row: Dict[str, Any]) -> str:
    if row.get('in_trash'):
        return TRASH_WORD
    return STATUS_WORDS.get(row.get('status'), row.get('status') or '?')


def actions_for(row: Optional[Dict[str, Any]], can_write: bool) -> List[str]:
    """The action keys one row offers (none when the caller cannot write, or
    nothing is selected). A draft that would replace a live layer offers both
    "Publish" (replaces it; the old one is archived, kept) and "Publish
    alongside"."""
    if not row or not can_write:
        return []
    if row.get('in_trash'):
        return ['restore']
    status = row.get('status')
    keys = ['rename', 'kind', 'phones_off' if row.get('show_on_phones', True) else 'phones_on']
    if status == 'draft':
        keys.append('publish')
        if row.get('replaces'):
            keys.append('publish_alongside')
        keys.append('discard')
    elif status == 'active':
        keys.append('archive')
    elif status == 'archived':
        keys.append('reactivate')
    return keys


def publish_label(row: Dict[str, Any]) -> str:
    """What "Publish" will do, said plainly."""
    replaces = row.get('replaces') or {}
    if replaces:
        return (f"Publish (replaces “{replaces.get('display_name')}”, which is "
                "archived and kept)")
    return "Publish (live on the web map and crew phones)"


def last_edit_text(row: Dict[str, Any]) -> str:
    when = str(row.get('last_edited') or '')[:10]
    who = row.get('last_edited_by') or ''
    return f"{when} {who}".strip() or '—'


def traced_from_text(row: Dict[str, Any]) -> str:
    if row.get('traced_from_name'):
        return row['traced_from_name']
    return 'a field sketch' if row.get('traced_from_id') else '—'


def table_rows(rows: List[Dict[str, Any]]) -> List[List[str]]:
    """The panel's cells per layer: name, status, kind, features, last edit,
    traced from, phones."""
    out = []
    for row in rows:
        out.append([
            row.get('display_name') or row.get('name') or '?',
            status_word(row),
            row.get('kind_label') or row.get('kind') or '',
            f"{row.get('feature_count') or 0:,}",
            last_edit_text(row),
            traced_from_text(row),
            'Shown' if row.get('show_on_phones', True) else 'Hidden',
        ])
    return out


def sort_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Drafts first (what needs a decision), then live, then archived."""
    order = {'draft': 0, 'active': 1, 'archived': 2}
    return sorted(rows, key=lambda r: (order.get(r.get('status'), 3),
                                       str(r.get('display_name') or r.get('name') or '').lower()))


class MapLayerManager:
    """The panel's calls, over :class:`api.client.APIClient`. Each returns the
    server's updated row (``{success, layer}``) or raises
    ``MapLayerActionError`` carrying the server's plain-words refusal."""

    def __init__(self, api_client):
        self.api = api_client

    def list(self, trashed: bool = False) -> Dict[str, Any]:
        body = self.api.get_map_layers_manage(trashed=trashed)
        body['results'] = sort_rows(body.get('results') or [])
        return body

    def rename(self, row, new_name: str):
        name = (new_name or '').strip()
        if not name:
            raise ValueError('Give the map layer a name.')
        return self.api.map_layer_action(row['id'], 'meta', {'display_name': name})

    def change_kind(self, row, kind: str):
        return self.api.map_layer_action(row['id'], 'meta', {'kind': kind})

    def set_show_on_phones(self, row, shown: bool):
        return self.api.map_layer_action(row['id'], 'meta', {'show_on_phones': bool(shown)})

    def publish(self, row, *, alongside: bool = False):
        return self.api.map_layer_action(row['id'], 'publish', {'replace': not alongside})

    def archive(self, row):
        return self.api.map_layer_action(row['id'], 'archive')

    def reactivate(self, row):
        return self.api.map_layer_action(row['id'], 'reactivate')

    def discard(self, row):
        return self.api.map_layer_action(row['id'], 'discard')

    def restore(self, row):
        return self.api.map_layer_action(row['id'], 'restore')


# ---------------------------------------------------------------------------
# Field sketches (Q-5)
# ---------------------------------------------------------------------------

def sketch_rows(sketches: List[Dict[str, Any]]) -> List[List[str]]:
    """Cells per sketch: area, author, date, sheets, lines, traced layers."""
    out = []
    for s in sorted(sketches, key=lambda r: str(r.get('sketched_on') or ''), reverse=True):
        sheets = ', '.join(sh.get('name') or sh.get('type') or '?' for sh in s.get('sheets') or [])
        traced = s.get('traced_layers') or []
        out.append([
            s.get('mapping_area_name') or '?',
            s.get('author') or '—',
            str(s.get('sketched_on') or '')[:10] or '—',
            sheets or '—',
            str(s.get('line_count') or 0) if s.get('has_strokes') else 'image only',
            ', '.join(t.get('name') or '' for t in traced) or '—',
        ])
    return out


def sort_sketches(sketches: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(sketches, key=lambda r: str(r.get('sketched_on') or ''), reverse=True)


def strokes_result_text(result: Dict[str, Any]) -> str:
    """The sentence after "Open strokes as lines"."""
    layers = result.get('layers') or []
    made = [l for l in layers if not l.get('existing')]
    again = [l for l in layers if l.get('existing')]
    parts = []
    if made:
        parts.append(f"{len(made)} draft line layer(s) made from the sketch")
    if again:
        parts.append(f"{len(again)} already made earlier (opened again, not copied)")
    skipped = result.get('skipped') or {}
    notes = []
    if skipped.get('eraser'):
        notes.append(f"{skipped['eraser']} eraser mark(s) not applied: check the sketch image")
    if skipped.get('single_point'):
        notes.append(f"{skipped['single_point']} single tap(s) are not lines")
    if skipped.get('unreadable'):
        notes.append(f"{skipped['unreadable']} stroke(s) could not be read")
    text = '; '.join(parts) or 'No lines'
    text += (". They are DRAFTS: they stay off the web map and crew phones until you "
             "publish them, and they replace nothing on the map.")
    if notes:
        text += ' ' + '; '.join(notes) + '.'
    return text
