# Alaska Mining Claims Support - Implementation Plan

## Context

A client wants Alaska state mining claims support. Currently the system only handles federal lode claims (600x1500 ft), which *partially* work in Alaska already. The major gap is **MTRSC (Meridian, Township, Range, Section, Corner) state claims** — Alaska's unique 40/160-acre claims that snap to the PLSS section grid. This is a fundamentally different claim type from federal lode.

**Good news:** The server already has significant Alaska infrastructure — AK is in supported states, PLSS meridian codes are mapped, document generator has AK requirements, and the processor handles AK corner-only monument rules.

---

## Phase 1: Federal Lode Claims in Alaska (Verify & Fix)

**Goal:** Confirm existing lode workflow works end-to-end for Alaska. Estimated: ~3 days.

### Server (`deploy/geodb/`)

| # | Task | File | Details |
|---|------|------|---------|
| 1 | Verify USCounty has Alaska boroughs/census areas | `services/models/` + DB query | If missing, ingest from Census TIGER/Line |
| 2 | Fix NE corner numbering for AK | `services/claims/processor.py` | `corner_numbering == 'NE'` is detected but not applied — need to rotate Corner 1 to NE corner |
| 3 | Verify PLSS data coverage for AK | DB query | Check `PLSSSection` and `PLSSTownship` counts for state_code='AK' |
| 4 | Verify "Recording District" in location notices | `services/claims/document_generator.py` | Ensure DOCX says "recording district" not "county" for AK |

### Plugin (`devel/geodb/`)

| # | Task | File | Details |
|---|------|------|---------|
| 5 | Update Step 4/5 UI text | `ui/claims_step_widgets/step4_monument.py`, `step5_adjust.py` | Change "Idaho/New Mexico" to "Idaho, New Mexico, and Alaska" for corner-only states |

### Testing
- Place lode claims near Fairbanks (Fairbanks Meridian area) and run full wizard
- Verify: state auto-detection → AK, NE corner numbering, 45-day deadline, PLSS lookup, GPX export, location notice content

---

## Phase 2: MTRSC State Claims (Major Feature)

**Goal:** Allow users to select PLSS quarter-sections on a map and create Alaska state mining claims. Estimated: ~3-4 weeks.

### Architecture Decision

**Extend existing wizard with claim type branching** (not a separate wizard). Add type selector in Step 1; branch the UI at Step 2.

### Server Changes

| # | Task | File | Details |
|---|------|------|---------|
| 6 | PLSS section subdivisions endpoint | New: `api/views/plss_views.py` or extend existing | `GET /api/v2/plss/sections/{id}/subdivisions/` — returns Q and QQ boundaries computed from section geometry |
| 7 | PLSS sections-in-bbox endpoint | Extend existing PLSS API | `GET /api/v2/plss/sections/?bbox=...&state=AK` — for map picker |
| 8 | MTRSC grid generation | `services/claims/grid_generator.py` | New `generate_mtrsc_grid(section_ids, quarter_type, selected_quarters, name_prefix)` method |
| 9 | MTRSC processing path | `services/claims/processor.py` | New `_process_mtrsc_claim()` — skip monument inset, use section corners, MTRSC legal description format |
| 10 | MTRSC document template | `services/claims/document_generator.py` | Certificate of Location template referencing AS Title 38, MTRSC legal description, state filing |
| 11 | Generate-grid endpoint update | `api/views/claims_views.py` | Accept `claim_type: 'mtrsc'` with new parameters |

### Plugin Changes

| # | Task | File | Details |
|---|------|------|---------|
| 12 | Add claim_type to wizard state | `ui/claims_wizard_state.py` | New fields: `claim_type`, `selected_plss_sections`, `mtrsc_quarter_type`, `mtrsc_selected_quarters` + GeoPackage persistence |
| 13 | Claim type selector in Step 1 | `ui/claims_step_widgets/step1_project_setup.py` | QComboBox: "Federal Lode" / "Alaska MTRSC (State)" / "Traditional". MTRSC enabled only for Alaska UTM zones |
| 14 | MTRSC section picker (Step 2) | `ui/claims_step_widgets/step2_claim_layout.py` | New `MtrscLayoutWidget` class: load PLSS sections for map extent, show section grid layer, checkbox selection, Q/QQ radio, generate claims button |
| 15 | MTRSC layer generation | `processors/claims_layer_generator.py` | New layer types: MTRSC Claims (polygon), Section Corners (point), Section Grid (polygon). No centerlines/monuments |
| 16 | Skip/simplify Step 4 for MTRSC | `ui/claims_step_widgets/step4_monument.py` | MTRSC has no monument inset — show info message or skip step |
| 17 | MTRSC GPX export | `processors/gpx_exporter.py` | Export section corner points as waypoints (GPS targets for field staking) |
| 18 | API methods in claims manager | `managers/claims_manager.py` | `generate_mtrsc_grid()`, `get_plss_sections(bbox)`, `get_section_subdivisions(section_id)` |

### MTRSC Section Picker UI Concept (Step 2)

```
┌─────────────────────────────────────────┐
│ Claim Type: [Alaska MTRSC (State)]      │
│                                         │
│ Quarter Type: ○ 160 acres (Q)           │
│               ● 40 acres (QQ)           │
│                                         │
│ [Load Sections for Map Extent]          │
│                                         │
│ Available Sections:                     │
│ ┌───────────────────────────────────┐   │
│ │ ☑ T23N R06E Sec 18 (Fairbanks)   │   │
│ │   Quarters: ☑NE ☑NW ☐SE ☐SW     │   │
│ │ ☑ T23N R06E Sec 7 (Fairbanks)    │   │
│ │   Quarters: ☑NE ☐NW ☐SE ☐SW     │   │
│ └───────────────────────────────────┘   │
│                                         │
│ Name Prefix: [AK    ]                  │
│ Selected: 3 claims (~120 acres)         │
│                                         │
│ [Generate MTRSC Claims]                │
└─────────────────────────────────────────┘
```

---

## Phase 3: Traditional Claims (Lower Priority)

**Goal:** Support Alaska's traditional rectangular claims (up to 1320x1320 ft, cardinal-aligned). Estimated: ~1 week.

Largely reuses existing lode patterns with constraints:

| # | Task | Details |
|---|------|---------|
| 19 | Traditional grid generator | Like lode but max 1320x1320 ft, azimuth locked to 0 |
| 20 | Traditional processor path | Validate dimensions ≤ 1320 ft each side, validate cardinal alignment |
| 21 | Traditional document template | References AK state statutes, physical corner post specs (3" dia, 3 ft high) |
| 22 | Step 2 traditional mode | Similar to lode grid but with constrained inputs |

---

## Key Risks

1. **PLSS coverage gaps** — Much of Alaska is unsurveyed. MTRSC only works where PLSS data exists. Show clear warnings when no sections found.
2. **Section geometry irregularity** — PLSS sections aren't perfect rectangles. Quarter-section subdivision must handle irregular polygons (use centroid-based subdivision initially).
3. **State vs Federal jurisdiction** — Some AK land is BLM (federal claims), some is state (MTRSC). Consider adding a land status indicator in future.
4. **UI complexity** — MTRSC section picker is a fundamentally different interaction from lode grid generator. Isolate it as a separate widget class.

---

## Verification Plan

### Phase 1 Testing
- Database queries to verify AK borough/PLSS coverage
- Place lode claims near Fairbanks, run full wizard through Step 7
- Verify location notice DOCX content for AK-specific requirements

### Phase 2 Testing
- Unit tests: MTRSC grid generation from known section geometries
- Unit tests: Quarter-section subdivision algorithm
- Integration test: Full MTRSC API flow (generate grid → process → documents)
- Manual QGIS test: MTRSC wizard in Fairbanks area with known PLSS sections
- Edge cases: sections at township boundaries, fractional sections, unsurveyed areas

### Phase 3 Testing
- Manual test: Traditional claims with various dimensions up to 1320x1320 ft
- Verify azimuth constraint enforced
- Verify document references AK state statutes
