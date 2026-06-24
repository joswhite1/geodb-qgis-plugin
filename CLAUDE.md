# geoDB QGIS plugin — Claude Code notes

QGIS plugin for claims management + data sync against the geoDB DRF API (`api.geodb.io`, Knox token auth). Talks to the same API surface as the mobile and Blender clients, so backend changes in `geodb/api/` can affect this plugin.

## Packaging + release rules (LOAD-BEARING — full detail in the root workspace CLAUDE.md)

These bite repeatedly; the authoritative version is in `~/workspace/CLAUDE.md` → "QGIS plugin packaging gotchas". Summary:

- **Commit to `v2.1`, never branch** (workspace-wide never-branch rule; branches only in worktrees).
- Bump `metadata.txt` `version=` (currently `2.22.6`) for any user-facing change; prepend a `changelog=` entry.
- **Escape every literal `%` as `%%`** in `metadata.txt` — `configparser` interpolation on plugins.qgis.org fails the upload otherwise. Validate before packaging: `python3 -c "import configparser as c; p=c.ConfigParser(); p.read('metadata.txt'); _=p['general']['changelog']"`.
- Package via `git archive --prefix=geodb/ -o ~/workspace/dist/geodb-<ver>.zip HEAD` (single top-level `geodb/` folder). Verify with Python `zipfile` (no `unzip` on the VM). `make package` fails here (no `pyrcc5`) but `resources_rc.py` is already committed, so recompiling is unnecessary.

## Key registries (where a new synced model must be added)

A new drill model gets wired at: `managers/data_manager.py:20` `SUPPORTED_MODELS`, `managers/storage_manager.py` (two registries), `utils/config.py` (endpoints + the model→endpoint map ~`:298`), `api/client.py` (a `get_<lookup>` fetch), `models/schemas.py` (a `*_SCHEMA` + the registry map ~`:917`), and `managers/sync_manager.py` (the field-dropdown builder). Forgetting one fails silently — they're the drift risk.

## Planned work

| Plan | Doc |
|---|---|
| 📋 **Custom drill intervals** — add the generic runtime-defined interval family (redox/weathering/numeric) to the plugin. SMALL (~½–1 day): one `SUPPORTED_MODELS` entry + API-driven type discovery via the new `custom-interval-types/` endpoint (mirrors `get_alterations`). Server-side LIVE on `geodb` main (merge `370bb09e`, 2026-06-24). | [progress_docs/custom_drill_intervals.md](progress_docs/custom_drill_intervals.md) |
