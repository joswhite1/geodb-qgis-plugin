# ProposedMiningClaim Integration Plan

**STATUS: IMPLEMENTED** (2026-01-29)

## Overview

This plan integrates admin-uploaded proposed claim blocks (`ProposedMiningClaim`) into the QGIS plugin workflow for staff users. The goal is to allow staff to:

1. View proposed claims that admins have uploaded for clients
2. Pull approved (or pending) proposed claims into QGIS
3. Run them through the full QClaims processing workflow
4. Generate documents and push as `LandHolding` records

## Current State

### Server Side (Already Exists)
- `ProposedMiningClaim` model stores admin-uploaded claim polygons
- Admin uploads via `/api/admin/upload-proposed-claims/` (KML, GPKG, GeoJSON)
- Shared maps allow client review and approval
- API endpoint exists: `GET /api/project-proposed-claims/{project_id}/` returns GeoJSON

### QGIS Plugin (Current)
- Staff Orders Dialog shows pending `ClaimOrder` and `ClaimPurchaseOrder`
- No visibility into `ProposedMiningClaim` records
- Claims workflow expects polygons from QGIS layers
- **BUG**: When pulling pending orders, `project_id` from order is NOT used to set plugin context

---

## Critical Issue: Project/Company Context

### The Problem

The plugin tracks project context via `_current_project_id` and `_current_company_id`, which are set when the user selects a project in the main dialog. However:

1. **Pending Orders** include `project_id` but NOT `company_id` in the API response
2. **When staff pulls an order**, the code does NOT update `_current_project_id`
3. This means the plugin may use the wrong project context for API calls

### The Solution (Option 1: Auto-Switch Project Context)

When staff pulls an order or proposed claims, automatically set the project/company context from the pulled data:

1. **Server**: Add `company_id` to all relevant API responses
2. **Plugin**: When pulling, call `set_project(project_id, company_id)` to update context
3. **Plugin**: Notify user that project context has been switched

---

## Proposed Integration

### Extend Staff Orders Dialog

Add a **second tab** to the existing `StaffOrdersDialog` that shows proposed claims by project. This keeps all staff-accessible claim sources in one place.

```
┌─────────────────────────────────────────────────────────────────────┐
│  Staff - Claim Sources                                        [X]  │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌──────────────────┬───────────────────┐                          │
│  │  Pending Orders  │  Proposed Claims  │                          │
│  └──────────────────┴───────────────────┘                          │
│  ═══════════════════════════════════════                           │
│                                                                     │
│  [Content for selected tab...]                                      │
│                                                                     │
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │ ⚠ Pulling will switch project context to: "Gold Mountain"   │   │
│  │   Company: "ACME Mining Corp"                                │   │
│  └─────────────────────────────────────────────────────────────┘   │
│                                                                     │
│     [Pull Selected]                    [Pull All]          [Close] │
└─────────────────────────────────────────────────────────────────────┘
```

### Workflow After Pulling

1. **Auto-switch project context** → `set_project(project_id, company_id)`
2. **Create memory layer** with claim polygons
3. **Layer auto-selected** in Claims Selection group
4. **Normal QClaims workflow** proceeds:
   - Process Claims (corners, monuments, PLSS lookup)
   - Generate Documents
   - Push to Server → Creates `LandHolding` records

---

## Implementation Tasks

### Phase 1: Server-Side Changes

#### 1.1 Add `company_id` to Pending Orders API

**File**: `deploy/geodb/services/api/claims_processing.py`

Update `staff_pending_orders()` to include `company_id`:

```python
# In the orders.append() for ClaimPurchaseOrder:
orders.append({
    'id': order.pk,
    'order_type': 'claim_purchase',
    # ... existing fields ...
    'project_id': order.project_id,
    'project_name': order.project.name,
    'company_id': order.company_id,          # ADD THIS
    'company_name': order.company.name,
    # ...
})

# Same for ClaimOrder:
orders.append({
    'id': order.pk,
    'order_type': 'claim_order',
    # ... existing fields ...
    'project_id': order.project_id,
    'project_name': order.project.name,
    'company_id': order.company_id,          # ADD THIS
    'company_name': order.company.name,
    # ...
})
```

#### 1.2 Add `company_id` to Proposed Claims API

**File**: `deploy/geodb/services/api/project_proposed_claims.py`

Update `get_project_proposed_claims()` to include company info:

```python
return JsonResponse({
    'success': True,
    'project': {
        'id': project.id,
        'name': project.name,
        'company_id': project.company_id,      # ADD THIS
        'company_name': project.company.name,  # ADD THIS
    },
    'proposed_claims': features,
    'counts': {
        'total': len(features),
        'approved': approved_count,
        'pending': pending_count
    }
})
```

#### 1.3 Add Projects-with-Proposed-Claims Endpoint

**File**: `deploy/geodb/services/views/admin_claims.py`

```python
@login_required
@staff_member_required
def get_projects_with_proposed_claims(request):
    """
    Get list of projects that have proposed claims.

    Staff-only endpoint for populating project dropdown in QGIS plugin.

    Returns:
        {
            "projects": [
                {
                    "id": 123,
                    "name": "Gold Mountain",
                    "company_id": 456,
                    "company_name": "ACME Mining",
                    "claim_count": 8,
                    "approved_count": 5,
                    "pending_count": 3
                }
            ]
        }
    """
    from django.db.models import Count, Q

    projects = Project.objects.filter(
        proposedminingclaim__isnull=False
    ).annotate(
        claim_count=Count('proposedminingclaim'),
        approved_count=Count('proposedminingclaim', filter=Q(proposedminingclaim__approved_by_user=True)),
        pending_count=Count('proposedminingclaim', filter=Q(proposedminingclaim__approved_by_user=False))
    ).select_related('company').distinct()

    return JsonResponse({
        'projects': [
            {
                'id': p.id,
                'name': p.name,
                'company_id': p.company_id,
                'company_name': p.company.name,
                'claim_count': p.claim_count,
                'approved_count': p.approved_count,
                'pending_count': p.pending_count,
            }
            for p in projects
        ]
    })
```

**File**: `deploy/geodb/services/urls.py`

```python
path('api/admin/projects-with-proposed-claims/', admin_claims.get_projects_with_proposed_claims, name='get_projects_with_proposed_claims'),
```

---

### Phase 2: Plugin - Claims Manager Methods

**File**: `devel/geodb/managers/claims_manager.py`

#### 2.1 Add `get_projects_with_proposed_claims()`

```python
def get_projects_with_proposed_claims(self) -> List[Dict[str, Any]]:
    """
    Get list of projects that have proposed claims (staff only).

    Returns:
        List of project dicts with id, name, company_id, company_name, counts

    Raises:
        PermissionError: If user is not staff
    """
    access = self.check_access()
    if not access.get('is_staff'):
        raise PermissionError("Staff access required")

    url = self._get_admin_endpoint('projects-with-proposed-claims/')
    result = self.api._make_request('GET', url)
    return result.get('projects', [])

def _get_admin_endpoint(self, path: str) -> str:
    """Build URL for admin endpoint."""
    base = self.config.base_url
    # Admin endpoints are at /api/admin/, not /api/v2/
    if '/v2' in base:
        base = base.replace('/api/v2', '/api/admin')
    elif '/v1' in base:
        base = base.replace('/api/v1', '/api/admin')
    else:
        base = base.rstrip('/') + '/api/admin'
    return f"{base}/{path}"
```

#### 2.2 Add `get_proposed_claims()`

```python
def get_proposed_claims(self, project_id: int) -> Dict[str, Any]:
    """
    Get proposed mining claims for a project (staff only).

    Args:
        project_id: Project ID to fetch claims for

    Returns:
        Dict with:
            - success: bool
            - project: dict with id, name, company_id, company_name
            - proposed_claims: list of GeoJSON features
            - counts: dict with total, approved, pending

    Raises:
        PermissionError: If user is not staff
    """
    access = self.check_access()
    if not access.get('is_staff'):
        raise PermissionError("Staff access required")

    # Use the existing public endpoint (works for staff)
    base = self.config.base_url
    if '/v2' in base:
        base = base.replace('/api/v2', '')
    elif '/v1' in base:
        base = base.replace('/api/v1', '')

    url = f"{base}/project-proposed-claims/{project_id}/"
    return self.api._make_request('GET', url)
```

---

### Phase 3: Plugin - Update Staff Orders Dialog

**File**: `devel/geodb/ui/staff_orders_dialog.py`

#### 3.1 Add Tab Widget

Convert the dialog to use tabs:

```python
def _setup_ui(self):
    # ... existing setup ...

    # Create tab widget
    self.tab_widget = QTabWidget()

    # Tab 1: Pending Orders (existing content)
    self.orders_tab = QWidget()
    self._setup_orders_tab()
    self.tab_widget.addTab(self.orders_tab, "Pending Orders")

    # Tab 2: Proposed Claims (new)
    self.proposed_tab = QWidget()
    self._setup_proposed_tab()
    self.tab_widget.addTab(self.proposed_tab, "Proposed Claims")

    layout.addWidget(self.tab_widget)
```

#### 3.2 Add Proposed Claims Tab

```python
def _setup_proposed_tab(self):
    """Set up the Proposed Claims tab."""
    layout = QVBoxLayout(self.proposed_tab)

    # Project selector
    project_layout = QHBoxLayout()
    project_layout.addWidget(QLabel("Project:"))

    self.project_combo = QComboBox()
    self.project_combo.setMinimumWidth(300)
    self.project_combo.currentIndexChanged.connect(self._on_project_changed)
    project_layout.addWidget(self.project_combo)

    self.refresh_projects_btn = QPushButton("Refresh")
    self.refresh_projects_btn.clicked.connect(self._load_projects)
    project_layout.addWidget(self.refresh_projects_btn)

    project_layout.addStretch()
    layout.addLayout(project_layout)

    # Claims table
    self.proposed_table = QTableWidget()
    self.proposed_table.setColumnCount(6)
    self.proposed_table.setHorizontalHeaderLabels([
        "Select", "Claim Name", "Type", "Acreage", "Approved", "Version"
    ])
    # ... configure table ...
    layout.addWidget(self.proposed_table)

    # Filter checkbox
    self.approved_only_checkbox = QCheckBox("Pull only approved claims")
    self.approved_only_checkbox.stateChanged.connect(self._filter_proposed_claims)
    layout.addWidget(self.approved_only_checkbox)

    # Context info (shows what project will be switched to)
    self.context_info = QLabel("")
    self.context_info.setStyleSheet("""
        QLabel {
            background-color: #fef3c7;
            border: 1px solid #f59e0b;
            border-radius: 4px;
            padding: 8px;
            color: #92400e;
        }
    """)
    layout.addWidget(self.context_info)

    # Pull buttons
    btn_layout = QHBoxLayout()
    self.pull_selected_btn = QPushButton("Pull Selected")
    self.pull_selected_btn.clicked.connect(self._pull_selected_proposed)
    btn_layout.addWidget(self.pull_selected_btn)

    self.pull_all_btn = QPushButton("Pull All")
    self.pull_all_btn.clicked.connect(self._pull_all_proposed)
    btn_layout.addWidget(self.pull_all_btn)

    btn_layout.addStretch()
    layout.addLayout(btn_layout)
```

#### 3.3 Add Signal for Project Context

```python
# Add new signal
project_context_changed = pyqtSignal(int, int, str, str)  # project_id, company_id, project_name, company_name

# Emit when pulling
def _pull_selected_proposed(self):
    project_data = self._get_selected_project_data()
    if project_data:
        self.project_context_changed.emit(
            project_data['id'],
            project_data['company_id'],
            project_data['name'],
            project_data['company_name']
        )
    # ... rest of pull logic ...
```

---

### Phase 4: Plugin - Update Claims Widget

**File**: `devel/geodb/ui/claims_widget.py`

#### 4.1 Fix Pending Orders Project Context

Update `_on_staff_order_selected()` to set project context:

```python
def _on_staff_order_selected(self, order_data: dict):
    """Handle staff selecting an order for fulfillment."""
    try:
        # ... existing validation ...

        # AUTO-SWITCH PROJECT CONTEXT
        project_id = order_data.get('project_id')
        company_id = order_data.get('company_id')
        project_name = order_data.get('project_name', 'Unknown')
        company_name = order_data.get('company_name', 'Unknown')

        if project_id and company_id:
            # Update plugin context
            self._current_project_id = project_id
            self._current_company_id = company_id

            # Notify parent dialog to update UI
            self.project_context_switched.emit(
                project_id, company_id, project_name, company_name
            )

            self.logger.info(
                f"[QCLAIMS UI] Switched to project {project_name} (ID: {project_id})"
            )

        # ... rest of existing code ...
```

#### 4.2 Add Signal for Context Switch

```python
# Add to class definition
project_context_switched = pyqtSignal(int, int, str, str)  # project_id, company_id, project_name, company_name
```

#### 4.3 Handle Proposed Claims Pull

Add handler for proposed claims (similar to orders):

```python
def _on_proposed_claims_selected(self, claims_data: dict, project_data: dict):
    """
    Handle staff selecting proposed claims for processing.

    Args:
        claims_data: List of GeoJSON features
        project_data: Dict with project_id, company_id, project_name, company_name
    """
    try:
        # AUTO-SWITCH PROJECT CONTEXT
        project_id = project_data.get('id')
        company_id = project_data.get('company_id')
        project_name = project_data.get('name', 'Unknown')
        company_name = project_data.get('company_name', 'Unknown')

        if project_id and company_id:
            self._current_project_id = project_id
            self._current_company_id = company_id

            self.project_context_switched.emit(
                project_id, company_id, project_name, company_name
            )

        # Create layer from GeoJSON
        self._load_proposed_claims_to_layer(claims_data, project_name)

        # Store proposed claim IDs for tracking
        self._proposed_claim_ids = [c.get('id') for c in claims_data]

        self.status_message.emit(
            f"Loaded {len(claims_data)} proposed claims from {project_name}",
            "info"
        )

    except Exception as e:
        self.logger.error(f"[QCLAIMS UI] Failed to load proposed claims: {e}")
        QMessageBox.critical(self, "Error", f"Failed to load claims: {e}")
```

---

### Phase 5: Plugin - Update Main Dialog

**File**: `devel/geodb/ui/geodb_modern_dialog.py`

Connect the context switch signal to update the project dropdown:

```python
def _setup_claims_widget_connections(self):
    # ... existing connections ...

    # Handle project context switch from staff orders/proposed claims
    if hasattr(self.claims_widget, 'project_context_switched'):
        self.claims_widget.project_context_switched.connect(
            self._on_claims_project_context_switched
        )

def _on_claims_project_context_switched(
    self, project_id: int, company_id: int,
    project_name: str, company_name: str
):
    """
    Handle project context switch from claims workflow.

    Updates the main project dropdown to match the pulled order/claims.
    """
    # Find and select the project in the dropdown
    for i in range(self.project_combo.count()):
        item_data = self.project_combo.itemData(i)
        if item_data and item_data.get('id') == project_id:
            self.project_combo.setCurrentIndex(i)
            break

    # Show notification
    self.status_bar.showMessage(
        f"Switched to project: {project_name} ({company_name})",
        5000
    )
```

---

## API Endpoints Summary

### Existing (Need Modifications)

| Endpoint | Method | Change Needed |
|----------|--------|---------------|
| `/api/v2/claims/staff/pending-orders/` | GET | Add `company_id` to response |
| `/api/project-proposed-claims/{project_id}/` | GET | Add `company_id`, `company_name` to project object |

### New Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/admin/projects-with-proposed-claims/` | GET | List projects that have proposed claims (staff only) |

---

## Files to Modify

### Server

| File | Changes |
|------|---------|
| `services/api/claims_processing.py` | Add `company_id` to `staff_pending_orders()` response |
| `services/api/project_proposed_claims.py` | Add `company_id`, `company_name` to project object |
| `services/views/admin_claims.py` | Add `get_projects_with_proposed_claims()` endpoint |
| `services/urls.py` | Add URL route for new endpoint |

### QGIS Plugin

| File | Changes |
|------|---------|
| `managers/claims_manager.py` | Add `get_proposed_claims()`, `get_projects_with_proposed_claims()` |
| `ui/staff_orders_dialog.py` | Add tabs, proposed claims tab, project context signals |
| `ui/claims_widget.py` | Add `project_context_switched` signal, fix order context handling |
| `ui/geodb_modern_dialog.py` | Connect context switch signal, update project dropdown |

---

## Testing Checklist

### Project Context Switching
- [ ] Pulling pending order switches project context correctly
- [ ] Pulling proposed claims switches project context correctly
- [ ] Main dialog project dropdown updates when context switches
- [ ] Status message shows switched project/company
- [ ] Subsequent API calls use correct project_id

### Pending Orders Tab
- [ ] Orders table shows all pending orders
- [ ] `company_id` is included in order data (verify with logging)
- [ ] Pulling order creates layer correctly
- [ ] Claims workflow uses correct project context

### Proposed Claims Tab
- [ ] Projects dropdown populates with projects that have proposed claims
- [ ] Selecting project loads proposed claims into table
- [ ] Checkbox selection works
- [ ] "Pull only approved" filter works
- [ ] Pull creates memory layer with correct geometries
- [ ] Layer auto-selects in Claims Selection
- [ ] Normal QClaims workflow proceeds with correct project

### End-to-End
- [ ] Pull proposed claims → Process → Generate docs → Push to server
- [ ] LandHolding records created with correct project_id
- [ ] Documents saved to correct project

---

## Migration Notes

### Breaking Changes
None - all changes are additive. Existing functionality preserved.

### Backwards Compatibility
- Old plugin versions will continue to work with old API responses
- New plugin will work with old API (just missing `company_id` for context switch)
- Graceful degradation: if `company_id` missing, skip auto-switch

---

## Future Enhancements

1. **View on Map**: Preview proposed claims on map before pulling
2. **Edit Before Pull**: Allow geometry edits in the dialog
3. **Batch Projects**: Pull from multiple projects at once
4. **Status Sync**: Auto-refresh when claims are approved on web
5. **Direct to Wizard**: Jump straight to claims wizard Step 2 after pull
6. **Mark Converted**: Server-side tracking when proposed claims become LandHoldings
