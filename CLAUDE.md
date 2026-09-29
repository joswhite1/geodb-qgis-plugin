# geoDB QGIS plugin — Claude Code notes

QGIS plugin for claims management + data sync against the geoDB DRF API (`api.geodb.io`, Knox token auth). Talks to the same API surface as the mobile and Blender clients, so backend changes in `geodb/api/` can affect this plugin.

## Packaging + release rules (LOAD-BEARING — full detail in the root workspace CLAUDE.md)

These bite repeatedly; the authoritative version is in `~/workspace/CLAUDE.md` → "QGIS plugin packaging gotchas". Summary:

- **Commit to `v2.1`, never branch** (workspace-wide never-branch rule; branches only in worktrees).
- Bump `metadata.txt` `version=` for any user-facing change; prepend a `changelog=` entry.
- **Escape every literal `%` as `%%`** in `metadata.txt` — `configparser` interpolation on plugins.qgis.org fails the upload otherwise. Validate before packaging: `python3 -c "import configparser as c; p=c.ConfigParser(); p.read('metadata.txt'); _=p['general']['changelog']"`.
- Package via `git archive --prefix=geodb/ -o ~/workspace/dist/geodb-<ver>.zip HEAD` (single top-level `geodb/` folder). Verify with Python `zipfile` (no `unzip` on the VM). `make package` fails here (no `pyrcc5`) but `resources_rc.py` is already committed, so recompiling is unnecessary.

## ⛔ Claims logic lives on the SERVER: this plugin is a thin client (2026-09-29)

This repository is public, and the server-side algorithms are the IP. Grid generation, numbering (`order-claims/`), corner alignment (`align-corners/`) and grid validation (`qc/`, the same check that gates documents) all run on geodb.io. The plugin reads the layer, sends it, and draws what comes back.

- **No local fallback, ever.** Every old fallback fired on ANY server error, not only offline, and silently produced a different answer. Without a client, the processors raise `processors/server_required.require_server` (one message, names the remedy). Don't restore a local copy "just for offline".
- **Every UI construction of `GridGenerator` / `GridProcessor` / `CornerAlignmentProcessor` must pass `api_client=self.claims_manager.api`**, refreshed on each use. A fallback hid the missing client for months: the wizard's Layout step only ever worked via the local grid copy, and Finalize only ever ran the local alignment/validation. Pinned by `test/test_claim_block_roundtrip.py` `ProcessorCallersPassApiClientTests`.
- **New claims logic = a server endpoint + a thin caller here**, gated by the server's `require_claims_tos` (`geodb/services/api/utils.py`).
- Tests run WITHOUT QGIS: `python3 test/test_claim_block_roundtrip.py`, `python3 test/test_grid_processor_ordering.py` (never `-m unittest test.<name>`: the package `__init__` imports real qgis).

## Key registries (where a new synced model must be added)

A new drill model gets wired at: `managers/data_manager.py:20` `SUPPORTED_MODELS`, `managers/storage_manager.py` (two registries), `utils/config.py` (endpoints + the model→endpoint map ~`:298`), `api/client.py` (a `get_<lookup>` fetch), `models/schemas.py` (a `*_SCHEMA` + the registry map ~`:917`), and `managers/sync_manager.py` (the field-dropdown builder). Forgetting one fails silently — they're the drift risk.

## Planned work

| Plan | Doc |
|---|---|
| 📋 **Custom drill intervals** — add the generic runtime-defined interval family (redox/weathering/numeric) to the plugin. SMALL (~½–1 day): one `SUPPORTED_MODELS` entry + API-driven type discovery via the new `custom-interval-types/` endpoint (mirrors `get_alterations`). Server-side LIVE on `geodb` main (merge `370bb09e`, 2026-06-24). | [progress_docs/custom_drill_intervals.md](progress_docs/custom_drill_intervals.md) |
