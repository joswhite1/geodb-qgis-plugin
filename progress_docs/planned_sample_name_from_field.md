# Planned-sample names from a QGIS attribute field

**Status:** 📋 PLANNED
**Customer ask (2026-06-30):** A client keeps their planned soil-sample names in a field on a QGIS shapefile layer and wants to use *those* names (e.g. `AK26-1001S`) instead of the plugin's auto-generated `SS-001` sequence. Add "use a field from the layer" as an **opt-in option** alongside the existing auto-generate scheme.

These are `PointSample`s of `sample_type='SL'` (soil), pushed as **planned** (`status='PL'`) via the QGIS plugin → DRF API.

## LOCKED DECISIONS (Joshua, 2026-06-30)

1. **Default is unchanged.** Auto-generate (prefix / start / padding → `sequence_number`, `name` left empty) stays the default, byte-identical to today. The barcode-scan-on-collect flow is untouched.
2. **The opt-in field option maps to `name` — always. No target picker.** A chosen attribute field holds text (`AK26-1001S`); text must go to `name`, never `sequence_number`. (See OTA rationale below.)
3. **Validate up-front, block the push.** Before pushing, scan the chosen field across all features for blank/empty values, duplicates, and values >50 chars. If any exist, show which features are bad and refuse the push until fixed.

## Why the field MUST map to `name` (the OTA constraint)

Investigation of `geodb-field` (mobile):

- Mobile stores `sequence_number` as **`type: 'number'`** (`schema.ts:233/281/1269`, model `@field`). A text value like `AK26-1001S` coerces to `NaN`/null on sync-down → map shows `#NaN`. **Broken.**
- Mobile stores `name` as **`type: 'string'`** (`@text('name')`) on every relevant table. `displayName` already returns `name || "Sample #" + sequenceNumber`; the map marker renders `sample.name || '#'+sequence_number`. So a planned sample carrying `name='AK26-1001S'` **displays correctly with ZERO mobile code change.**
- Changing `sequence_number` to text on mobile = a **WatermelonDB schema migration**, which mobile `CLAUDE.md` explicitly lists as **NOT OTA-safe** (needs a native build). Mapping to `name` avoids the migration entirely.

→ **Field option → `name`. Fully OTA-safe: zero mobile changes, zero server changes.** Collect-page behavior stays display-only — the crew sees the planned name; the separate lab `sampleId` (`labSampleId`) field still gets typed/scanned at collect as today.

## Scope: QGIS-plugin-only. Zero server changes. Zero mobile changes (OTA-safe).

Server already accepts an arbitrary `name`/`sequence_number` on a planned sample (verified):

- `api/serializers/point_sample.py:443` — a `PL` sample needs a **non-empty `sequence_number`** + target coords. Any string accepted.
- `api/viewsets/point_sample.py:619` `_fast_bulk_create_planned` — bulk fast-path is generic.
- Natural-key lookup tries `name` then `sequence_number`; conflict-check/upsert keys on `sequence_number` within the project (`api/client.py:1049`).

**Companion `sequence_number` (invisible to user):** because a `PL` sample still requires a `sequence_number` server-side, when the field maps to `name` the plugin **also auto-generates a `sequence_number`** from the existing prefix/start/padding inputs (default `SS-###`). The user never sees it; the value they care about is in `name`. (Setting `sequence_number = name` would also work but re-introduces the text-in-numeric problem if anything ever reads that scalar on mobile — so we keep the generated numeric-style sequence instead.)

## The two edit points

### 1. `managers/data_manager.py` — `push_planned_samples` (`:1588`)

One new optional param; auto-generate path byte-identical when it's unset (one function, parameterized — no duplicate path):

```python
def push_planned_samples(
    self, source_layer, prefix, start_number, padding, sample_type,
    name_field: Optional[str] = None,          # NEW: attribute to read the sample NAME from
    progress_callback=None, skip_conflict_check=False,
):
```

In the feature loop (`:1683`), always generate the `sequence_number` (unchanged); when `name_field` is set, additionally read `name` from the attribute:

```python
seq_num = f"{prefix}{str(start_number + i).zfill(padding)}"   # unchanged — still the PL natural key
sample_data = { ... 'sequence_number': seq_num, ... }          # unchanged dict

if name_field:
    raw = feature[name_field]
    value = '' if raw is None else str(raw).strip()
    sample_data['name'] = value   # blanks/dups/len already rejected by the up-front validator; defensive
```

Add a validator used by the dialog (and defensively by the push):

```python
def validate_name_field(self, source_layer, name_field) -> Dict[str, Any]:
    """Stringify each feature's value (str(raw).strip()) and report problems.
    Returns {'ok': bool, 'blanks': [fid...], 'duplicates': {value: [fid...]}, 'too_long': [(fid, value)...]}.
    Length check is against the 50-char sequence/name limit."""
```

### 2. `ui/field_work_dialog.py` — `FieldWorkDialog`

- In the "Sample Configuration" (or a new "Sample Name") group, add an **opt-in checkbox**: *"Use a layer field for the sample name"* — **unchecked by default**.
- When checked: reveal a `QgsFieldComboBox` (from `qgis.gui`) bound to the current layer to pick the name field. The Sequence Numbers group (prefix/start/padding) **stays visible and active** — it still drives the behind-the-scenes `sequence_number`. (Optionally add a one-line hint: "Sequence numbers are still generated for navigation; your field sets the sample name.")
- Bind/repopulate the `QgsFieldComboBox` to the layer in `_on_layer_changed` (`:237`) — `self.name_field_combo.setLayer(layer)`.
- `_update_preview` / `_show_full_preview` (`:249`/`:284`): when field mode is on, also preview the **first few actual field values** as the names (alongside, or instead of, the sequence preview).
- `_on_push_clicked` (`:307`): if field mode on, run `data_manager.validate_name_field(...)`; if not `ok`, show blanks/duplicates/too-long and `return` (block). Extend the confirm text to mention field-sourced names.
- `_execute_push` (`:349`): pass `name_field=` (the combo's current field, or `None` when unchecked) into `push_planned_samples`.

## Packaging (per plugin CLAUDE.md)

- Commit to `v2.1` (no branch). Bump `metadata.txt` `version=` (currently `2.22.7`) and prepend a `changelog=` entry — **escape any literal `%` as `%%`**; validate with the configparser one-liner.
- Package: `git archive --prefix=geodb/ -o ~/workspace/dist/geodb-<ver>.zip HEAD`; verify with Python `zipfile`.

## Test

- Point layer with a text attribute (e.g. `sample_id` = `AK26-1001S`, …) → check the box → pick the field → push → confirm planned `PointSample`s land with `name` = those values and an auto `sequence_number` (`SS-001`…). Confirm `name` shows on the web dashboard and (manually, if a device is handy) the mobile map.
- Blank-value row, duplicate-value rows, and a >50-char value → push blocked with a clear message naming the offending features.
- **Default (box unchecked) unchanged** — prefix/start/padding → `sequence_number`, `name` empty (regression).
