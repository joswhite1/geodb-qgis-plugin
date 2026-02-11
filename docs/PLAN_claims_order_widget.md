# Plan: Claims Order Widget for Pay-Per-Claim Users

## Overview

Create a separate, simplified "Order Claims" workflow for pay-per-claim users that:
1. Collects claimant information
2. Allows grid layout (generate, position, rotate)
3. Submits order with payment redirect to Stripe
4. Server/admin handles processing and document generation after payment

The existing Claims Wizard (7 steps) remains unchanged for staff/enterprise users.

## User Flow

```
Pay-Per-Claim User:
1. Opens Claims tab → sees ClaimsOrderWidget (not wizard)
2. Enters claimant info (name, address, district, monument type)
3. Sets UTM zone
4. Generates/layouts claim grid
5. Reviews pricing summary
6. Accepts disclaimer checkbox
7. Clicks "Submit & Pay" → Stripe checkout opens in browser
8. After payment → server creates LandHoldings, admin generates documents
9. User receives email when complete
```

## Server Changes

### 1. Update `submit_order` API

**File**: `deploy/geodb/services/api/claims_processing.py`

Update the `submit_order` function to accept `claimant_info`:

```python
# Current input:
{
    "claims": [...],
    "project_id": 123,
    "service_type": "self_service"
}

# New input:
{
    "claims": [...],
    "project_id": 123,
    "service_type": "self_service",
    "claimant_info": {
        "claimant_name": "John Smith",
        "address_1": "123 Main St",
        "address_2": "Anytown, NV 89001",
        "address_3": "",
        "district": "Elko Mining District",
        "monument_type": "2' wooden post"
    }
}
```

Changes:
- Add `claimant_info = request.data.get('claimant_info', {})`
- Pass to `create_order()` call

### 2. Update `create_order` function

**File**: `deploy/geodb/services/claims/order_workflow.py`

Add `claimant_info` parameter:

```python
def create_order(
    stripe_customer,
    company,
    project,
    claim_polygons: list,
    service_type: str = 'self_service',
    claimant_info: dict = None  # NEW
) -> ClaimOrder:
```

Store on order:
```python
order = ClaimOrder.objects.create(
    ...
    claimant_info=claimant_info,  # NEW
    ...
)
```

### 3. TOS Handling

Keep TOS check in API but the plugin will handle acceptance via a simple inline disclaimer checkbox (not the full TOS dialog). The disclaimer will trigger TOS acceptance via existing `accept_tos` API before submitting the order.

## Plugin Changes

### 1. Create `ClaimsOrderWidget`

**File**: `devel/geodb/ui/claims_order_widget.py`

A single-page widget (no wizard/steps) with these sections:

#### Section: QClaims Access
- Display "Pay-Per-Claim" status
- Show pricing (e.g., "$1.50 per claim")
- Refresh button

#### Section: Claimant Information
- Claimant name (required)
- Address line 1 (required)
- Address line 2
- Address line 3
- Mining district
- Monument type (default: "2' wooden post")

#### Section: Coordinate System
- Current CRS display
- "Auto-Detect UTM Zone" button
- UTM zone info label

#### Section: Claim Layout
- Grid generator controls:
  - Rows (spinbox)
  - Columns (spinbox)
  - Name prefix (text)
  - Azimuth (double spinbox)
- "Generate Grid" button
- Layer selector combo (for existing layers)
- Grid tools:
  - Auto-number button
  - Rename button
- Claim count display

#### Section: Submit Order
- Pricing summary:
  - Claim count
  - Price per claim
  - Total price
- Disclaimer checkbox: "I understand that by submitting this order, I am requesting claim processing services. Payment is required to complete the order. Claims will be processed by geodb.io staff after payment."
- "Submit & Pay" button (disabled until disclaimer checked)

### 2. Update `ClaimsManager.submit_order()`

**File**: `devel/geodb/managers/claims_manager.py`

Add `claimant_info` parameter:

```python
def submit_order(
    self,
    claims: List[Dict],
    project_id: int,
    company_id: int,
    service_type: str = 'self_service',
    claimant_info: Dict[str, str] = None  # NEW
) -> Dict[str, Any]:
```

Include in request data:
```python
data = {
    'claims': claims,
    'project_id': project_id,
    'service_type': service_type,
    'claimant_info': claimant_info,  # NEW
}
```

### 3. Update `geodb_modern_dialog.py`

**File**: `devel/geodb/ui/geodb_modern_dialog.py`

On login/project change, check access level and swap the Claims tab content:

```python
def _update_claims_tab_for_access(self, access_info: dict):
    """Show appropriate claims widget based on access level."""
    can_process = access_info.get('can_process_immediately', False)
    is_staff = access_info.get('is_staff', False)

    if can_process or is_staff:
        # Full access - show wizard
        self._show_claims_wizard()
    else:
        # Pay-per-claim - show order widget
        self._show_claims_order_widget()
```

### 4. Update imports and __init__.py

**File**: `devel/geodb/ui/__init__.py`

Add export for `ClaimsOrderWidget`.

## Data Flow Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                     QGIS Plugin                                  │
│                                                                  │
│  ClaimsOrderWidget                                               │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  │
│  │ Claimant Info   │  │ Grid Layout     │  │ Submit & Pay    │  │
│  │ - name          │  │ - generate grid │  │ - pricing       │  │
│  │ - address       │  │ - position      │  │ - disclaimer    │  │
│  │ - district      │  │ - rotate        │  │ - submit btn    │  │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘  │
│                              │                                   │
│                              ▼                                   │
│                    ClaimsManager.submit_order()                  │
│                              │                                   │
└──────────────────────────────┼───────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Server API                                   │
│                                                                  │
│  POST /api/v2/claims/submit-order/                              │
│  {                                                               │
│    "claims": [...],                                              │
│    "project_id": 123,                                            │
│    "claimant_info": {                                            │
│      "claimant_name": "...",                                     │
│      "address_1": "...",                                         │
│      ...                                                         │
│    }                                                             │
│  }                                                               │
│                              │                                   │
│                              ▼                                   │
│                    create_order()                                │
│                              │                                   │
│                              ▼                                   │
│                    ClaimOrder created                            │
│                    (with claimant_info)                          │
│                              │                                   │
└──────────────────────────────┼───────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Stripe Checkout                              │
│                                                                  │
│  User completes payment in browser                               │
│                              │                                   │
└──────────────────────────────┼───────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Server Fulfillment                           │
│                                                                  │
│  1. Webhook marks order as paid                                  │
│  2. Server creates LandHoldings from claim_polygons              │
│  3. Admin generates documents using claimant_info                │
│  4. Email sent to user                                           │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

## Files to Create/Modify

### New Files
- `devel/geodb/ui/claims_order_widget.py` - Main order widget

### Modified Files (Plugin)
- `devel/geodb/managers/claims_manager.py` - Add claimant_info to submit_order
- `devel/geodb/ui/geodb_modern_dialog.py` - Swap claims tab based on access
- `devel/geodb/ui/__init__.py` - Export new widget

### Modified Files (Server)
- `deploy/geodb/services/api/claims_processing.py` - Accept claimant_info in submit_order
- `deploy/geodb/services/claims/order_workflow.py` - Pass claimant_info to create_order

## Testing Checklist

- [ ] Pay-per-claim user sees ClaimsOrderWidget (not wizard)
- [ ] Staff/enterprise user sees ClaimsWizardWidget (unchanged)
- [ ] Claimant info form validates required fields
- [ ] Grid generation works (reuses existing GridGenerator)
- [ ] UTM auto-detect works
- [ ] Pricing displays correctly
- [ ] Disclaimer checkbox required before submit
- [ ] TOS acceptance happens via API before order submission
- [ ] Submit creates ClaimOrder with claimant_info on server
- [ ] Stripe checkout opens in browser
- [ ] After payment, LandHoldings are created
- [ ] Admin can see order with claimant_info in admin panel
- [ ] Admin can generate documents with correct claimant details

## Future Considerations

- Order status tracking dialog (check if order is fulfilled)
- Order history view
- Ability to view/download documents after fulfillment
