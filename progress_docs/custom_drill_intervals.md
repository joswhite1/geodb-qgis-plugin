# QGIS plugin — custom drill intervals support

**Status:** 📋 PLANNED — server-side LIVE on `origin/main` (merge `370bb09e`, 2026-06-24). Plugin work not started.
**Effort:** SMALL (~½–1 day). This is registry entries + API-driven type discovery, NOT new logic.
**Priority:** FORWARD-THINKING / parity (Joshua, 2026-06-24) — no customer blocked. Small enough to land opportunistically next time the plugin ships anyway; doesn't jump the queue.
**Decoupled:** lands on the plugin's own `v2.1` line (commit, never branch — root workspace rule), independent of any backend deploy. Just needs the server endpoints (already on main) to be deployed to `api.geodb.io` before it's *useful* to customers.

## What this is

The geoDB backend now has a generic **custom drill interval** family: a user mints an interval *type* (redox, weathering, vein density, clay%…) at runtime as pure DATA, and it's supported everywhere on the server. Model trio: `DrillCustomInterval` (the interval rows) + `CustomIntervalType`/`CustomIntervalValue` (the runtime-defined vocabulary) + `DrillCustomIntervalSet`. It mirrors the existing **alteration** family — which is the template for everything below.

The key property: **TYPE IS DATA.** A customer minting "Redox" needs ZERO backend code. The plugin must honor that — add ONE model entry and **discover the project's types at runtime via the API**, exactly like the plugin already fetches `get_alterations(project_id)` to populate the alteration dropdown. Do NOT hardcode per-type anything.

## Server endpoints available (already on main)

- `GET /api/v2/custom-interval-types/?project_id=<id>` — the discovery endpoint. Returns the project's `CustomIntervalType`s with their nested `CustomIntervalValue` rows + each type's `mode` (categorical|numeric) + `unit`. **This is the plugin's analog of `get_alterations()`** — call it to learn what types exist, then build dropdowns dynamically.
- `GET/POST/PUT /api/v1|v2/drill-custom-intervals/` — CRUD for the interval rows.
- `GET/POST/PUT /api/v1|v2/drill-custom-interval-sets/` — CRUD for the sets.

## Wiring checklist (mirror the alteration family at each site)

All anchors below are current as of 2026-06-24. The alteration entry is the template at each.

| Surface | File:line (alteration template) | Add |
|---|---|---|
| Supported-models registry **(MUST-ADD)** | `managers/data_manager.py:20` `SUPPORTED_MODELS` (gate at `:147`) | `'DrillCustomInterval'` |
| Child-records deletion list | `managers/data_manager.py` (alteration in the child-delete list) | `'DrillCustomInterval'` |
| Storage manager registries **(MUST-ADD)** | `managers/storage_manager.py` (two registries hold `'DrillAlteration'`) | `'DrillCustomInterval'` in both |
| Config endpoints | `utils/config.py:215` (`drill_alterations`), `:237` (`alterations`), `:298` (`'DrillAlteration': 'drill_alterations'`) | `drill_custom_intervals`, `custom_interval_types`, and `'DrillCustomInterval': 'drill_custom_intervals'` |
| API client lookup fetch | `api/client.py:1164` `get_alterations(project_id)` | `get_custom_interval_types(project_id)` — hits `custom-interval-types/`, returns types + nested values + mode |
| Model schema | `models/schemas.py:285` `DRILL_ALTERATION_SCHEMA`, registered at `:917` | `DRILL_CUSTOM_INTERVAL_SCHEMA` (fields: bhid, depth_from, depth_to, type, value, measure, set, notes) + register in the `:917` map |
| Sync field-dropdown config | `managers/sync_manager.py` (~989–1009, builds the alteration value dropdown from `get_alterations`) | The custom-interval dropdown is **two-level**: pick a TYPE (from `get_custom_interval_types`), then the datum follows the type's mode — categorical → a value dropdown from that type's nested values; numeric → a number field. This is the one place that differs from alteration (which is single-level). Build it generically off the fetched type list. |

## The one wrinkle vs alteration

Alteration is single-level: one `Alteration` lookup → one dropdown. Custom intervals are **two-level + mode-dual**: the user first picks which *type* (redox vs weathering), then enters a categorical value OR a numeric measure depending on that type's `mode`. The sync-manager dropdown builder (`sync_manager.py`) is the only site that needs genuinely new (not copy-paste) logic — drive it off the `custom-interval-types/` response so a newly-minted type appears with zero plugin change.

## Packaging (root workspace CLAUDE.md — QGIS gotchas)

- **Commit to `v2.1`, never branch.**
- Bump `metadata.txt` `version=` (currently `2.22.6`) for the user-facing change; prepend a `changelog=` entry.
- **Escape every literal `%` as `%%`** in `metadata.txt` (configparser interpolation, or upload fails). Validate: `python3 -c "import configparser as c; p=c.ConfigParser(); p.read('metadata.txt'); _=p['general']['changelog']"`.
- Package via `git archive --prefix=geodb/ -o ~/workspace/dist/geodb-<ver>.zip HEAD`. Verify with Python `zipfile` (no `unzip` on the VM).

## Test

- Load the plugin against a project that has at least one categorical + one numeric custom-interval type defined on the backend; confirm the type dropdown populates from the API, the value/measure field follows the type's mode, and a synced interval round-trips (create in plugin → appears in web).

## Ties

Server design + 18-surface checklist: `geodb/geodb/progress_docs/ToDo/custom_drill_intervals.md` (on main). Memory: `project_custom_drill_intervals`. Sibling mobile plan: `geodb-field/progress_docs/custom_drill_intervals.md`.
