# Repository Overview

- **Project**: GeodbIO — QGIS plugin to connect to geodb.io
- **Type**: QGIS 3 Python plugin (PyQGIS + PyQt)
- **Minimum QGIS**: 3.0
- **Primary entry point**: `__init__.py -> classFactory()` loads `GeodbIO` from `geodb.py`
- **Maintainer**: geodb.io <admin@geodb.io>
- **Plugin metadata file**: `metadata.txt`

## Purpose
- **Goal**: Authenticate with geodb.io and load/manage spatial datasets (e.g., land holdings, drill collars, point samples) via API.
- **Menu**: Appears under QGIS “Database” menu as “Geodb.io Connection”.
- **Action**: Toolbar/menu action labeled “connect now” opens the plugin dialog.

## Tech Stack
- **Language**: Python 3 (PyQGIS runtime)
- **Frameworks/APIs**: QGIS (qgis.core, qgis.PyQt), PyQt5/Qt
- **HTTP**: Uses QGIS network classes and custom JSON POSTs
- **External Python deps**: listed in `requirements.txt`
  - requests==2.32.3 (and transitive: certifi, charset-normalizer, idna, urllib3)
- **Resources**: Qt resource file `resources.qrc` compiled to `resources.py`

## Key Files and Folders
- **geodb.py**: Main plugin logic (GUI registration, login/logout, API requests, UI control, project/company selection).
- **geodb_dialog.py / geodb_dialog_base.ui**: Dialog implementation and UI layout.
- **__init__.py**: QGIS plugin bootstrap (classFactory).
- **metadata.txt**: QGIS plugin metadata (name, version, tags, min QGIS, etc.).
- **resources.qrc / resources.py / icon.png / icons/**: Plugin icons and compiled Qt resources.
- **help/**: Sphinx documentation scaffold; built docs under `help/build/html`.
- **i18n/**: Translation sources (`.ts`), currently empty/placeholder.
- **test/**: Unit tests and QGIS test scaffolding (nose-based in Makefile).
- **Makefile / pb_tool.cfg**: Build/deploy helpers for UI/resource compilation, packaging, and deployment.
- **requirements.txt**: Runtime Python dependencies (non-QGIS).

## Runtime Behavior
- **Authentication**: geodb.io token is stored using QGIS `QgsAuthMethodConfig` (method: Basic; realm stores token).
- **Endpoints** (production vs local):
  - When `GeodbIO.local` is False (default):
    - check: https://geodb.io/api/check-token/
    - login: https://geodb.io/api/api-token-auth/
    - logout: https://geodb.io/api/api-logout/
    - projects: https://geodb.io/api/api-projects/
    - permissions: https://geodb.io/api/api-permissions/
    - data: https://geodb.io/api/api-data/
  - When `GeodbIO.local` is True: same endpoints on http://127.0.0.1:8000
- **Project variables**: Selected company/project is written to QGIS project via `QgsProject.writeEntry("geodb_vars", "selected_project", <name>)`.

## Development Setup
1. Install QGIS 3.x (includes Python, PyQt, PyQGIS).
2. Optional: Create and activate a Python virtual environment for extra deps.
3. Install Python deps (if testing code outside QGIS Python):
   ```bash
   pip install -r requirements.txt
   ```
4. Compile Qt resources (if icons/resources changed):
   ```bash
   pyrcc5 -o resources.py resources.qrc
   ```
5. Optional pb_tool (cross-platform plugin helper):
   ```bash
   pip install pb_tool
   ```

## Running in QGIS (Windows)
1. Determine your QGIS plugins folder, e.g.:
   - `C:\Users\<you>\AppData\Roaming\QGIS\QGIS3\profiles\default\python\plugins`
2. Copy this repo folder to a subfolder named `geodb` under the plugins folder.
3. Start QGIS → Plugins → Manage and Install Plugins → enable “Geodb.io Connection”.
4. Use the toolbar/menu item “connect now”.

Notes:
- The Makefile contains Linux/Unix-oriented `deploy`/`zip` targets; refer to paths if adapting to Windows.
- `pb_tool.cfg` lists deployable files; pb_tool can manage install/update cycles.

## Building and Packaging
- Using Makefile (Unix-like):
  - Compile resources/translations:
    ```bash
    make compile
    make transcompile
    ```
  - Build docs:
    ```bash
    make doc
    ```
  - Package from a git tag/commit:
    ```bash
    make package VERSION=Version_0.1.0
    ```
- Using pb_tool:
  - Configure files in `pb_tool.cfg` (already set with core files).
  - Common commands:
    ```bash
    pb_tool compile   # resources/ui
    pb_tool deploy    # copy to QGIS plugin dir
    pb_tool zip       # build zip for upload
    ```

## Testing
- Tests live in `test/` with nose-style execution hooks in Makefile.
- Example (Unix-like, with appropriate QGIS env sourced):
  ```bash
  make test
  ```
- Helper scripts provided for Linux env setup in `scripts/`.

## Internationalization
- Translations are scaffolded (see `i18n/`).
- To update and compile translations (Unix-like):
  ```bash
  make transup
  make transcompile
  ```

## Documentation
- Sphinx project under `help/` with built HTML at `help/build/html`.
- Rebuild docs:
  ```bash
  make -C help html
  ```

## Configuration and Secrets
- QGIS Authentication Manager stores credentials/token (no plain-text storage in repo).
- For local backend dev, set `GeodbIO.local = True` in `geodb.py` to switch endpoints.

## Known Paths and Names
- **Plugin folder name**: `geodb`
- **QGIS menu**: Database → Geodb.io Connection
- **Primary action**: connect now

## Troubleshooting
- If PyQGIS imports fail in tests: run within a QGIS Python environment or source env scripts under `scripts/`.
- If icons don’t show: ensure `resources.py` is rebuilt from `resources.qrc` and `icon.png` exists.
- If plugin doesn’t appear: check folder naming (`geodb`) and `metadata.txt` validity.

## Maintainer and Links
- **Author**: geodb.io
- **Email**: admin@geodb.io
- **Homepage/Tracker/Repo**: https://geodb.io

---
This file is auto-generated to help tools and contributors understand structure, build steps, and usage. Update as the plugin evolves.