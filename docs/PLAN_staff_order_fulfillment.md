# Plan: Staff Order Fulfillment Workflow

**Status: IMPLEMENTED** (2026-01-19)

## Summary

Enable staff users to fulfill claim package orders (from QPlugin or BLM-Map) directly within QGIS by:
1. Viewing and pulling pending orders
2. Running the normal claims workflow
3. Having generated documents and stakes automatically link back to the originating order/ClaimPackage

The key insight: **we don't need a separate workflow** - staff just needs to pull an order, work normally, and have the system track which order the work came from.

## Current State

### Order Models (Server)
- **ClaimPurchaseOrder** (`services/models/claim_purchases.py`) - BLM Map purchases
  - Has `work_package` FileField for manual uploads
  - Has `generated_claims_data` JSON with claim geometries
  - Links to Project and Company

- **ClaimOrder** (`services/models/claims_config.py`) - QPlugin pay-per-claim orders
  - Has `claim_polygons` JSON with submitted geometries
  - Has `claimant_info` JSON
  - Links to Project and Company

### ClaimPackage Model
- Central hub for all claim job artifacts
- Can link to `ClaimPurchaseOrder` or `ClaimStakingRequest` via OneToOne
- M2M to `LandHolding` (claims)
- Has `ClaimPackageDocument` for linking documents

### Document Generation API
- `POST /api/v2/claims/documents/` - `claims_documents.py`
- Creates `ClaimPackage` when `save_to_project=true`
- Links documents via `ClaimPackageDocument`
- Matches LandHoldings by name to link to package

### QGIS Plugin
- `ClaimsManager.generate_documents()` calls the documents API
- No awareness of order context currently
- Claims wizard state tracks current workflow but not order linkage

## Design Approach

**Minimal invasive change**: Pass the order context through the existing workflow rather than creating a parallel workflow.

### Data Flow

```
1. Staff pulls order from server
   ↓
2. Polygons loaded into QGIS with order_id in layer metadata
   ↓
3. Staff runs normal claims workflow (grid gen, processing, etc.)
   ↓
4. Claims wizard state carries order_id
   ↓
5. Document generation includes order_id in API request
   ↓
6. Server links everything to existing ClaimPackage (or creates one linked to order)
```

## Implementation Plan

### Phase 1: Server - Staff Orders API

**New endpoint**: `GET /api/v2/claims/staff/pending-orders/`

Returns orders needing fulfillment (staff only):

```json
{
  "orders": [
    {
      "id": 123,
      "order_type": "claim_purchase",  // or "claim_order"
      "order_number": "CPO-2026-0001",
      "status": "processing",
      "created_at": "2026-01-15T10:00:00Z",
      "customer_email": "customer@example.com",
      "company_name": "Gold Mining Co",
      "project_id": 456,
      "project_name": "Nevada Claims",
      "claim_count": 8,
      "claim_polygons": {...},  // GeoJSON
      "claimant_info": {...},
      "claim_package_id": null,  // null if not yet created
      "staking_service": false,
      "expedited_delivery": false
    }
  ]
}
```

**Files to modify:**
- `services/api/claims_processing.py` - Add `staff_pending_orders()` view
- `services/api/urls.py` - Add URL route

### Phase 2: Server - Link Documents to Order

**Modify**: `POST /api/v2/claims/documents/`

Add optional `order_id` and `order_type` parameters:

```json
{
  "claims": [...],
  "project_id": 123,
  "save_to_project": true,
  "order_id": 456,           // NEW
  "order_type": "claim_purchase"  // NEW: "claim_purchase" or "claim_order"
}
```

When provided:
1. Look up existing ClaimPackage linked to order, OR create one and link it
2. Link all generated documents to that package
3. Update order status to "completed" if appropriate

**Files to modify:**
- `services/api/claims_documents.py` - Modify `generate_documents()` and `_create_claim_package()`

### Phase 3: Plugin - Staff Orders UI

**New tab or dialog**: Staff Orders view (only visible to is_staff users)

Simple list showing pending orders:
- Order number, customer, claim count, date
- "Pull into QGIS" button

When pulled:
- Loads claim polygons into a memory layer
- Stores `order_id` and `order_type` in layer custom properties
- Stores claimant_info in wizard state for later use

**Files to create/modify:**
- `ui/staff_orders_dialog.py` - New dialog for viewing/pulling orders
- `managers/claims_manager.py` - Add `get_pending_orders()` and `pull_order_claims()`
- `ui/geodb_modern_dialog.py` - Add staff orders button (staff only)

### Phase 4: Plugin - Track Order Through Workflow

**Modify claims wizard state** to carry order context:

```python
# In claims_wizard_state.py
class ClaimsWizardState:
    # ... existing fields ...

    # Order fulfillment context (staff only)
    fulfillment_order_id: Optional[int] = None
    fulfillment_order_type: Optional[str] = None  # "claim_purchase" or "claim_order"
    fulfillment_claimant_info: Optional[Dict] = None
```

When a staff user pulls an order:
1. State is initialized with order context
2. Claimant info is pre-populated from order
3. Order ID flows through to document generation

**Files to modify:**
- `ui/claims_wizard_state.py` - Add order context fields
- `ui/claims_step_widgets/step1_import.py` - Load polygons from order
- `ui/claims_step_widgets/step5_claimant.py` - Pre-populate from order claimant_info

### Phase 5: Plugin - Pass Order to Document Generation

**Modify document generation** to include order context:

```python
# In claims_manager.py
def generate_documents(
    self,
    claims: List[Dict],
    # ... existing params ...
    order_id: Optional[int] = None,      # NEW
    order_type: Optional[str] = None     # NEW
) -> Dict[str, Any]:
```

**Modify step 6 (Finalize)** to pass order context when generating documents.

**Files to modify:**
- `managers/claims_manager.py` - Add order params to `generate_documents()`
- `ui/claims_step_widgets/step6_finalize.py` - Pass order context from state

## File Changes Summary

### Server (deploy/geodb/)

| File | Change |
|------|--------|
| `services/api/claims_processing.py` | Add `staff_pending_orders()` view |
| `services/api/claims_documents.py` | Add `order_id`/`order_type` params, link to existing package |
| `services/api/urls.py` | Add route for staff pending orders |

### Plugin (QPlugin/devel/geodb/)

| File | Change |
|------|--------|
| `ui/staff_orders_dialog.py` | **NEW** - Dialog for viewing/pulling orders |
| `managers/claims_manager.py` | Add `get_pending_orders()`, `pull_order_claims()`, update `generate_documents()` |
| `ui/geodb_modern_dialog.py` | Add staff orders button (staff only) |
| `ui/claims_wizard_state.py` | Add order context fields |
| `ui/claims_step_widgets/step1_import.py` | Support loading from order |
| `ui/claims_step_widgets/step5_claimant.py` | Pre-populate from order |
| `ui/claims_step_widgets/step6_finalize.py` | Pass order context to doc generation |

## Detailed Implementation

### Server: staff_pending_orders()

```python
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def staff_pending_orders(request):
    """
    Get pending claim orders for staff fulfillment.

    GET /api/v2/claims/staff/pending-orders/

    Returns orders that:
    - Are in 'processing' or 'paid' status
    - Don't have a completed ClaimPackage yet

    Staff only.
    """
    if not request.user.is_staff:
        return Response(
            {"error": "Staff access required"},
            status=status.HTTP_403_FORBIDDEN
        )

    orders = []

    # ClaimPurchaseOrders (BLM Map)
    from services.models import ClaimPurchaseOrder
    cpo_qs = ClaimPurchaseOrder.objects.filter(
        status__in=['processing', 'pending']
    ).select_related('project', 'company', 'user')

    for order in cpo_qs:
        # Check if already has completed package
        if hasattr(order, 'claim_package') and order.claim_package:
            if order.claim_package.status == 'complete':
                continue

        orders.append({
            'id': order.pk,
            'order_type': 'claim_purchase',
            'order_number': order.order_number,
            'status': order.status,
            'created_at': order.created_at.isoformat(),
            'customer_email': order.user.email,
            'company_name': order.company.name,
            'project_id': order.project_id,
            'project_name': order.project.name,
            'claim_count': order.claim_count,
            'claim_polygons': order.generated_claims_data,
            'claimant_info': None,  # CPO doesn't have this
            'claim_package_id': order.claim_package.pk if hasattr(order, 'claim_package') and order.claim_package else None,
            'staking_service': order.staking_service,
            'expedited_delivery': order.expedited_delivery,
        })

    # ClaimOrders (QPlugin pay-per-claim)
    from services.models import ClaimOrder
    co_qs = ClaimOrder.objects.filter(
        status__in=['paid', 'approved']
    ).select_related('project', 'company')

    for order in co_qs:
        orders.append({
            'id': order.pk,
            'order_type': 'claim_order',
            'order_number': f"CO-{order.pk}",
            'status': order.status,
            'created_at': order.created_at.isoformat(),
            'customer_email': order.stripe_customer.email,
            'company_name': order.company.name,
            'project_id': order.project_id,
            'project_name': order.project.name,
            'claim_count': order.claim_count,
            'claim_polygons': order.claim_polygons,
            'claimant_info': order.claimant_info,
            'claim_package_id': None,  # CO uses ClaimOrderDocument, not ClaimPackage
            'staking_service': False,
            'expedited_delivery': False,
        })

    # Sort by created_at (oldest first - FIFO)
    orders.sort(key=lambda x: x['created_at'])

    return Response({'orders': orders})
```

### Server: Modified _create_claim_package()

```python
def _create_claim_package(
    project,
    stripe_customer,
    claim_prefix,
    documents,
    claim_names=None,
    order_id=None,       # NEW
    order_type=None      # NEW
):
    """
    Create a ClaimPackage and link all generated documents to it.

    If order_id is provided, link the package to the order.
    If the order already has a package, use that instead of creating new.
    """
    from geodata.models import ClaimPackage, ClaimPackageDocument, LandHolding
    from services.models import ClaimPurchaseOrder, ClaimOrder

    try:
        package = None

        # Check if order already has a package
        if order_id and order_type:
            if order_type == 'claim_purchase':
                try:
                    cpo = ClaimPurchaseOrder.objects.get(pk=order_id)
                    if hasattr(cpo, 'claim_package') and cpo.claim_package:
                        package = cpo.claim_package
                        logger.info(f"[QCLAIMS DOCS] Using existing package {package.package_number} from order {order_id}")
                except ClaimPurchaseOrder.DoesNotExist:
                    pass

        # Create new package if needed
        if not package:
            if claim_prefix:
                package_name = f"{claim_prefix} Claims Documents"
            else:
                package_name = "QClaims Documents"

            package = ClaimPackage.objects.create(
                name=package_name,
                description=f"Documents generated via QClaims plugin",
                project=project,
                company=project.company,
                source='qclaims',
                status='draft',
                created_by=stripe_customer,
                last_edited_by=stripe_customer,
            )
            logger.info(f"[QCLAIMS DOCS] Created ClaimPackage {package.package_number}")

            # Link to order if provided
            if order_id and order_type == 'claim_purchase':
                try:
                    cpo = ClaimPurchaseOrder.objects.get(pk=order_id)
                    package.claim_purchase_order = cpo
                    package.save(update_fields=['claim_purchase_order'])
                    logger.info(f"[QCLAIMS DOCS] Linked package to ClaimPurchaseOrder {order_id}")
                except ClaimPurchaseOrder.DoesNotExist:
                    pass

        # Link documents (same as before)
        for doc in documents:
            geodb_doc = doc.get('geodb_document')
            if geodb_doc:
                doc_type = PACKAGE_DOCUMENT_TYPE_MAP.get(doc.get('document_type'), 'other')
                ClaimPackageDocument.objects.create(
                    package=package,
                    document_type=doc_type,
                    linked_document=geodb_doc,
                    title=doc.get('filename', 'Document'),
                    uploaded_by=stripe_customer,
                )

        # Link LandHoldings (same as before)
        if claim_names:
            matched_claims = LandHolding.objects.filter(
                project=project,
                name__in=claim_names,
                mark_deleted=False
            )
            if matched_claims.exists():
                package.land_holdings.add(*matched_claims)

        return package

    except Exception as e:
        logger.error(f"[QCLAIMS DOCS] Error creating ClaimPackage: {e}", exc_info=True)
        return None
```

## Testing Plan

1. **Staff pending orders API**
   - Create test ClaimPurchaseOrder with status='processing'
   - Verify it appears in staff pending orders list
   - Verify non-staff users get 403

2. **Pull order into QGIS**
   - Pull order, verify polygons load correctly
   - Verify order context stored in wizard state
   - Verify claimant info pre-populated

3. **Document generation with order**
   - Generate documents with order context
   - Verify ClaimPackage linked to order
   - Verify documents linked to package
   - Verify LandHoldings linked to package

4. **Order status update**
   - After document generation, verify order status updated
   - Verify ClaimPackage status reflects completion

## Questions/Decisions

1. **Order status after fulfillment**: Should we auto-update order status to 'completed' after documents are generated, or leave that for a separate step?
   - **Recommendation**: Auto-update to 'completed' - the staff user pulled and processed, that's fulfillment.

2. **Multiple document generations**: If staff generates documents multiple times for the same order, should we add to existing package or error?
   - **Recommendation**: Add to existing package (idempotent-ish behavior).

3. **ClaimStake creation**: The existing flow creates ClaimStakes during processing. These should also link to the package.
   - The `claim_stake_linking.py` utility already handles this via signals.

4. **Notification to customer**: Should we send email when order is fulfilled?
   - Out of scope for this plan, but worth considering as follow-up.
