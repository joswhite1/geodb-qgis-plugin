# GeodbIO QGIS Plugin v2.0

> **Modern, maintainable architecture for geospatial data synchronization**

[![QGIS](https://img.shields.io/badge/QGIS-3.0+-green.svg)](https://qgis.org)
[![Python](https://img.shields.io/badge/Python-3.6+-blue.svg)](https://python.org)
[![License](https://img.shields.io/badge/License-GPL%20v2%2B-blue.svg)](LICENSE)

## Overview

GeodbIO connects QGIS to the geodb.io platform, enabling seamless synchronization of geospatial datasets including land holdings, drill collars, point samples, and more.

**Version 2.0** represents a complete architectural rebuild focused on:
- 🏗️ **Modularity**: Clean separation of concerns
- 🛡️ **Reliability**: Comprehensive error handling
- 📊 **Visibility**: Progress tracking and detailed logging
- ⚡ **Performance**: Incremental synchronization
- 🔧 **Maintainability**: Type-safe, well-documented code

---

## Quick Start

### Installation

1. **Download** the plugin to your QGIS plugins folder:
   ```
   Windows: C:\Users\<you>\AppData\Roaming\QGIS\QGIS3\profiles\default\python\plugins\geodb\
   Linux: ~/.local/share/QGIS/QGIS3/profiles/default/python/plugins/geodb/
   Mac: ~/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/geodb/
   ```

2. **Enable** the plugin in QGIS:
   - Plugins → Manage and Install Plugins → Installed
   - Check "Geodb.io Connection"

3. **Access** from menu:
   - Database → Geodb.io Connection → connect now

### First Use

1. **Login** with your geodb.io credentials
2. **Select** a company and project
3. **Pull** data for any model (LandHolding, DrillCollar, etc.)
4. **Edit** features in QGIS
5. **Push** changes back to geodb.io

---

## Architecture

### Component Structure

```
┌─────────────────────────────────────────────────────────┐
│                      UI Layer                            │
│                   (geodb_dialog.py)                      │
└─────────────────────┬───────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────┐
│                Business Logic Layer                      │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  │
│  │AuthManager   │  │ProjectManager│  │DataManager   │  │
│  └──────────────┘  └──────────────┘  └──────────────┘  │
│  ┌──────────────────────────────────────────────────┐  │
│  │              SyncManager                          │  │
│  └──────────────────────────────────────────────────┘  │
└─────────────────────┬───────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────┐
│              Data Processing Layer                       │
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐       │
│  │Geometry     │ │Field        │ │Layer        │       │
│  │Processor    │ │Processor    │ │Processor    │       │
│  └─────────────┘ └─────────────┘ └─────────────┘       │
└─────────────────────┬───────────────────────────────────┘
                      │
┌─────────────────────▼───────────────────────────────────┐
│                   API Layer                              │
│              (APIClient + Exceptions)                    │
└─────────────────────┬───────────────────────────────────┘
                      │
                ┌─────▼──────┐
                │ geodb.io   │
                │    API     │
                └────────────┘
```

### Key Components

| Component | Responsibility | Location |
|-----------|---------------|----------|
| **APIClient** | HTTP communication | `api/client.py` |
| **AuthManager** | Authentication & credentials | `managers/auth_manager.py` |
| **ProjectManager** | Company/project selection | `managers/project_manager.py` |
| **DataManager** | High-level data operations | `managers/data_manager.py` |
| **SyncManager** | Low-level synchronization | `managers/sync_manager.py` |
| **GeometryProcessor** | Geometry conversion | `processors/geometry_processor.py` |
| **FieldProcessor** | Field mapping | `processors/field_processor.py` |
| **LayerProcessor** | QGIS layer operations | `processors/layer_processor.py` |

---

## Features

### Authentication
- ✅ Secure token-based authentication
- ✅ Credential storage via QGIS Authentication Manager
- ✅ Automatic session restoration
- ✅ Token validation

### Project Management
- ✅ Multi-company support
- ✅ Project selection with permission checking
- ✅ Role-based access (admin/editor/viewer)
- ✅ Project persistence across sessions

### Data Synchronization
- ✅ **Pull**: Download data from geodb.io to QGIS
  - Incremental sync (only changed records)
  - Full sync option
  - Progress feedback
- ✅ **Push**: Upload local changes to geodb.io
  - Validation before upload
  - Conflict detection
  - Error reporting
- ✅ **Supported Models**:
  - LandHolding
  - DrillCollar
  - PointSample
  - DrillSample
  - FieldNote

### Data Processing
- ✅ Automatic geometry conversion (WKT ↔ QGIS)
- ✅ Coordinate precision handling (6 decimals)
- ✅ Field type mapping (API ↔ QGIS)
- ✅ Read-only field protection
- ✅ Change tracking

### Error Handling
- ✅ Specific exception types for different errors
- ✅ User-friendly error messages
- ✅ Automatic retry for network issues
- ✅ Detailed error logging

### Configuration
- ✅ Customizable settings via JSON file
- ✅ Production/local API switching
- ✅ Configurable timeouts and retries
- ✅ Logging level control

---

## Usage Examples

### Login and Load Projects

```python
from utils.config import Config
from api.client import APIClient
from managers.auth_manager import AuthManager
from managers.project_manager import ProjectManager

# Initialize
config = Config()
api_client = APIClient(config)
auth_manager = AuthManager(config, api_client)

# Login
session = auth_manager.login('username', 'password')
print(f"Logged in as: {session.user.full_name}")

# Load projects
project_manager = ProjectManager(config, api_client)
companies = project_manager.load_companies()

# Select first project
project = companies[0].projects[0]
project_manager.select_project(project)
```

### Pull Data with Progress

```python
from managers.data_manager import DataManager
from managers.sync_manager import SyncManager

# Initialize
sync_manager = SyncManager(config)
data_manager = DataManager(config, api_client, project_manager, sync_manager)

# Define progress callback
def on_progress(percent, message):
    print(f"[{percent}%] {message}")

# Pull data
result = data_manager.pull_model_data(
    model_name='LandHolding',
    incremental=True,
    progress_callback=on_progress
)

print(f"Pull complete:")
print(f"  Added: {result['added']}")
print(f"  Updated: {result['updated']}")
print(f"  Total: {result['total']}")
```

### Push Data

```python
# Push with progress
result = data_manager.push_model_data(
    model_name='LandHolding',
    progress_callback=on_progress
)

print(f"Pushed {result['pushed']} features")
if result['errors'] > 0:
    print(f"  {result['errors']} errors occurred")
```

### Check Permissions

```python
# Check what user can do
if project_manager.can_view('DrillCollar'):
    print("Can view drill collars")

if project_manager.can_edit('DrillCollar'):
    print("Can edit drill collars")
    
if project_manager.can_admin('DrillCollar'):
    print("Can administer drill collars")
```

### Geometry Processing

```python
from processors.geometry_processor import GeometryProcessor

geom_processor = GeometryProcessor()

# Convert QGIS geometry to WKT
wkt = geom_processor.qgs_to_wkt(qgs_geometry, precision=6)

# Convert WKT to QGIS geometry
qgs_geometry = geom_processor.wkt_to_qgs(wkt)

# Compare geometries with tolerance
equal = geom_processor.geometries_equal(geom1, geom2, precision=6)
```

---

## Configuration

### Configuration File

Location: `~/.qgis3/geodb_plugin_config.json`

```json
{
  "api": {
    "base_url": "https://geodb.io/api/v1",
    "local_base_url": "http://localhost:8000/api/v1",
    "use_local": false,
    "timeout": 30,
    "retry_attempts": 3
  },
  "data": {
    "coordinate_precision": 6,
    "default_crs": "EPSG:4326"
  },
  "ui": {
    "show_progress_dialog": true,
    "auto_save_qgis_project": true
  },
  "logging": {
    "level": "INFO",
    "enabled": true
  }
}
```

### Programmatic Access

```python
from utils.config import Config

config = Config()

# Get values
timeout = config.get('api.timeout', 30)
precision = config.get('data.coordinate_precision', 6)

# Set values
config.set('api.use_local', True)
config.set('logging.level', 'DEBUG')

# Toggle local development mode
config.toggle_local_mode(enabled=True)
```

---

## Logging

### Log Location

- **File**: `~/.qgis3/logs/geodb_plugin.log`
- **Console**: QGIS Python console

### Log Levels

```python
from utils.logger import PluginLogger

logger = PluginLogger.get_logger()

logger.debug("Detailed debug information")
logger.info("General information")
logger.warning("Warning message")
logger.error("Error message", exc_info=True)
```

### Sample Log Output

```
2024-12-20 10:30:45,123 - GeodbIO - INFO - auth_manager:login:45 - Attempting login for user: john
2024-12-20 10:30:46,234 - GeodbIO - INFO - auth_manager:login:78 - Login successful for user: john
2024-12-20 10:30:47,345 - GeodbIO - INFO - project_manager:load_companies:34 - Loading companies and projects
2024-12-20 10:30:48,456 - GeodbIO - INFO - project_manager:load_companies:54 - Loaded 2 companies
2024-12-20 10:30:50,567 - GeodbIO - INFO - data_manager:pull_model_data:28 - Pulling data for model: LandHolding
2024-12-20 10:30:55,678 - GeodbIO - INFO - sync_manager:sync_pull_to_layer:85 - Sync complete: {'added': 15, 'updated': 3, 'deleted': 0, 'total': 18}
```

---

## Error Handling

### Exception Hierarchy

```
GeodbException
├── APIException
│   ├── AuthenticationError    # Login failed, token invalid
│   ├── PermissionError         # Insufficient permissions
│   ├── NetworkError            # Connection issues
│   ├── ServerError             # Server returned 5xx
│   └── ValidationError         # Invalid request data
├── DataException
│   ├── GeometryError           # Geometry processing failed
│   ├── FieldMappingError       # Field conversion failed
│   └── LayerError              # Layer operation failed
└── ConfigException             # Configuration invalid
```

### Exception Handling Example

```python
from api.exceptions import (
    AuthenticationError,
    PermissionError,
    NetworkError
)

try:
    result = data_manager.pull_model_data('LandHolding')
except AuthenticationError:
    print("Please log in again")
except PermissionError:
    print("You don't have permission to view this data")
except NetworkError:
    print("Check your internet connection")
except Exception as e:
    print(f"Unexpected error: {e}")
```

---

## Development

### Project Structure

```
geodb_qgis_dev/
├── api/                    # API communication
│   ├── client.py
│   └── exceptions.py
├── managers/               # Business logic
│   ├── auth_manager.py
│   ├── project_manager.py
│   ├── data_manager.py
│   └── sync_manager.py
├── processors/             # Data processing
│   ├── geometry_processor.py
│   ├── field_processor.py
│   └── layer_processor.py
├── models/                 # Data models
│   ├── auth.py
│   ├── project.py
│   └── api_response.py
├── utils/                  # Utilities
│   ├── config.py
│   └── logger.py
├── ui/                     # User interface
│   └── (to be migrated)
├── docs/                   # Documentation
│   ├── API_REFERENCE.md
│   ├── ARCHITECTURE.md
│   ├── IMPLEMENTATION_SUMMARY.md
│   └── MIGRATION_GUIDE.md
├── geodb.py               # Plugin main class (to be updated)
├── geodb_dialog.py        # Dialog (to be updated)
├── __init__.py            # Plugin entry point
├── metadata.txt           # Plugin metadata
├── requirements.txt       # Python dependencies
└── README_v2.md          # This file
```

### Running Tests

```bash
# Install dependencies
pip install -r requirements.txt

# Run tests (when implemented)
pytest tests/
```

### Local Development

1. **Enable local mode** in config:
   ```python
   config.toggle_local_mode(enabled=True)
   ```

2. **Start local API server**:
   ```bash
   # In your Django project
   python manage.py runserver
   ```

3. **Use plugin** - it will now connect to `http://localhost:8000/api/v1/`

---

## API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/v1/check-token/` | POST | Validate token |
| `/api/v1/api-token-auth/` | POST | Login |
| `/api/v1/api-logout/` | POST | Logout |
| `/api/v1/api-projects/` | GET | Get companies & projects |
| `/api/v1/api-permissions/` | POST | Get user permissions |
| `/api/v1/api-data/` | POST | Pull/push data |

See `docs/API_REFERENCE.md` for detailed documentation.

---

## Migration from v0.1

If you're upgrading from the legacy version, see **`docs/MIGRATION_GUIDE.md`** for:
- Architecture comparison
- Code migration examples
- Breaking changes
- Backward compatibility notes

---

## Troubleshooting

### Plugin doesn't appear in menu
- Check folder name is `geodb` (not `geodb_qgis_dev`)
- Verify `metadata.txt` is valid
- Restart QGIS

### Login fails
- Check credentials
- Verify internet connection
- Check logs at `~/.qgis3/logs/geodb_plugin.log`
- Try enabling DEBUG logging in config

### Data not syncing
- Verify project is selected
- Check permissions for the model
- Review error messages
- Check logs for detailed error trace

### Icons don't show
- Ensure `resources.py` is compiled from `resources.qrc`
- Run: `pyrcc5 -o resources.py resources.qrc`

### Import errors
- Ensure all files have `__init__.py`
- Check QGIS Python environment has access to plugin folder
- Verify no circular imports

---

## Contributing

### Code Style
- Follow PEP 8
- Use type hints
- Write docstrings for all public methods
- Keep functions focused and small

### Before Committing
- Update documentation
- Add/update tests
- Check logs don't contain sensitive data
- Update CHANGELOG

---

## Support

- **Documentation**: `docs/` folder
- **Email**: admin@geodb.io
- **Website**: https://geodb.io
- **Logs**: `~/.qgis3/logs/geodb_plugin.log`

---

## License

This program is free software; you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation; either version 2 of the License, or (at your option) any later version.

See [LICENSE](LICENSE) for details.

---

## Changelog

### 2.0.0 (2024-12)
- **Major architectural rebuild**
- Modular design with separation of concerns
- Comprehensive error handling and logging
- Progress feedback for operations
- Incremental synchronization
- Type-safe code with full type hints
- Detailed documentation

### 0.1 (Initial)
- Basic authentication
- Project selection
- Data pull/push
- Monolithic architecture

---

**Built with ❤️ for the geospatial community**