# Alaska Mining Claims Support - Implementation Plan

> **⚠️ SUPERSEDED 2026-05-13.** This plan used an elif-branch-into-lode-pipeline architecture. After review, the architecture was reworked into a **parallel pipeline** (`services/claims/plss/`) shared by AK MTRSC, federal placer, and ID state mineral leases. The lode pipeline stays byte-identical. Reasoning: better isolation (no shared code paths to regress), and the subdivision/legal-description math is reused across three future claim types.
>
> **Canonical plan:** [geodb/geodb/progress_docs/ToDo/plss_claims_pipeline.md](../../geodb/geodb/progress_docs/ToDo/plss_claims_pipeline.md)
>
> This document is kept for historical reference. Two sections remain load-bearing for the new plan:
> - **Task 10 detail** — MTRSC Certificate of Location (DNR Form 10-162V rev 9/23) field map
> - **Task 11a detail** — Filing map requirements (11 AAC 86.215)
>
> Everything else (the task tables, isolation rules, regression protocol) is reframed in the canonical plan.

---

## Context

A client wants Alaska state mining claims support. Currently the system only handles federal lode claims (600x1500 ft), which *partially* work in Alaska already. The major gap is **MTRSC (Meridian, Township, Range, Section, Corner) state claims** — Alaska's unique 40/160-acre claims that snap to the PLSS section grid. This is a fundamentally different claim type from federal lode.

**Good news:** The server already has significant Alaska infrastructure — AK is in supported states, PLSS meridian codes are mapped, document generator has AK requirements, and the processor handles AK corner-only monument rules.

---

## Code Isolation Strategy

MTRSC is a fundamentally different claim type from lode. All new work **must be additive** — existing lode/placer workflows must continue working unchanged during and after development. This section documents the architecture review findings and the rules for safe integration.

### Architecture Review Summary

Both the server and plugin have **good extension points** for adding MTRSC without modifying existing lode code:

#### Server — Existing Extension Points

| Component | File | Current Pattern | MTRSC Approach |
|-----------|------|----------------|----------------|
| Claim type enum | `services/models/mining_claims.py` | `CLAIM_TYPES = [('lode', ...), ('placer', ...)]` | Add `('mtrsc', 'MTRSC Claim')` tuple — no existing code affected |
| Grid generation | `services/claims/grid_generator.py` | Separate methods: `generate_lode_grid()`, `generate_placer_grid()` | Add new `generate_mtrsc_grid()` method — existing methods untouched |
| Monument dispatch | `services/claims/processor.py` | `if monument_type == 'sideline': ... elif 'endline': ... elif 'both': ...` | Add `elif monument_type == 'mtrsc':` branch + new `_calculate_mtrsc_monuments()` method |
| Document generation | `services/claims/fulfillment.py` | `if first_state == 'NV': ... elif 'WY': ... elif 'AZ': ...` | Add `elif first_state == 'AK' and claim_type == 'mtrsc':` branch + new `generate_mtrsc_certificates()` method |
| State requirements | `StateClaimRequirements` model | JSON config per state, queried by `state_reqs.get('monument_type')` | Add AK MTRSC config record — existing state configs unchanged |
| API endpoints | `api/views/claims_views.py` | Already accept `claim_type` in `claimant_info` | No endpoint changes needed — MTRSC flows through existing plumbing |

**Estimated server changes:** ~640 lines of new code, ~40 lines of modifications (elif branches only).

#### Plugin — Existing Extension Points

| Component | File | Current Pattern | MTRSC Approach |
|-----------|------|----------------|----------------|
| Claim type enum | `processors/grid_generator.py` | `ClaimType(Enum): LODE, PLACER` | Add `MTRSC = 'mtrsc'` — existing enum values unchanged |
| Grid generation | `processors/grid_generator.py` | Separate methods per type, all call server API | Add `generate_mtrsc_grid()` — existing methods untouched |
| GPX export | `processors/gpx_exporter.py` | Generic: takes any waypoint list | No changes needed — already works for any claim type |
| Storage | `managers/claims_storage_manager.py` | Generic table names: `processed_claims`, `corner_points` | No changes needed — MTRSC claims store in same tables with `claim_type` attribute |
| Wizard state | `ui/claims_wizard_state.py` | No `claim_type` field currently | Add `claim_type` field + MTRSC-specific fields. Default to `'lode'` so existing behavior unchanged |

#### Plugin — Areas Requiring Careful Modification

These files need conditional branching. The rule is: **default path must remain lode behavior**.

| Component | File | Risk | Change Required |
|-----------|------|------|-----------------|
| Step 2 (Claim Layout) | `ui/claims_step_widgets/step2_claim_layout.py` | Medium | Currently hardcoded to `generate_lode_grid()`. Add `if claim_type == 'mtrsc':` branch to call `generate_mtrsc_grid()`. Default (no claim_type set) must call lode. |
| Layer generator | `processors/claims_layer_generator.py` | Medium | Expects `'lode_claims'` key in server response, hardcodes `"Lode_Azimuth"` field. Add conditional to handle `'mtrsc_claims'` key. Must fall back to `'lode_claims'` when key absent. |
| Step 4 (Monuments) | `ui/claims_step_widgets/step4_monument.py` | Low | MTRSC has no monument inset. Add `if claim_type == 'mtrsc': show_info_message()` branch. Lode path unchanged. |
| Step 5 (Adjust) | `ui/claims_step_widgets/step5_adjust.py` | Low | Similar to Step 4 — skip or simplify for MTRSC. |
| Step 1 (Setup) | `ui/claims_step_widgets/step1_project_setup.py` | Low | Add claim type QComboBox. Default selection = "Federal Lode" so existing workflow is the default. |

### Isolation Rules

1. **New methods, not modified methods.** Add `generate_mtrsc_grid()`, `_process_mtrsc_claim()`, `generate_mtrsc_certificates()` as new methods alongside existing ones. Never modify the body of `generate_lode_grid()`, `_process_lode_claim()`, etc.

2. **elif branches, not refactored logic.** Where branching is needed (processor, fulfillment, layer generator), add `elif claim_type == 'mtrsc':` branches. Do not refactor existing if/elif chains into polymorphic dispatch, strategy pattern, or other abstractions — that risks breaking working code for no immediate benefit.

3. **Default to lode.** Anywhere `claim_type` is read, the default must be `'lode'`: `claim_type = state.get('claim_type', 'lode')`. This ensures that existing saved projects, API calls without claim_type, and any code path that doesn't set claim_type will continue to produce lode claims.

4. **Server response backward compatibility.** The server should return MTRSC data under a new key (`'mtrsc_claims'`) rather than repurposing the `'lode_claims'` key. The plugin layer generator should check for `'mtrsc_claims'` first, fall back to `'lode_claims'`. This way older plugin versions talking to an updated server still work.

5. **Separate MTRSC widget class.** The MTRSC section picker UI (Step 2) should be a new `MtrscLayoutWidget` class in its own file or clearly separated within `step2_claim_layout.py`. Do not add MTRSC selection logic into the existing lode grid layout widget — swap the entire widget based on claim type.

6. **New StateClaimRequirements record.** Add an AK MTRSC config via database migration, not by modifying existing state configs. Existing AK lode config (if any) stays as-is.

### Regression Testing Protocol

Before merging any MTRSC work, run the full existing claims workflow for these states to confirm no regressions:

| State | Claim Type | What to Verify |
|-------|-----------|----------------|
| Nevada | Lode | Standard centerline discovery monument, NV location notice |
| Arizona | Lode | Endline monuments, AZ-specific filing |
| Wyoming | Lode | Sideline monuments, WY-specific filing |
| Idaho | Lode | Corner-only (LM as Corner 1), ID filing |
| Alaska | Federal Lode | NE corner numbering, 45-day deadline, recording district (Phase 1 must pass before Phase 2) |

For each state: generate grid → process claims → verify monuments → export GPX → generate documents. All must produce identical output to pre-MTRSC codebase.

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
| 10 | MTRSC Certificate of Location template | `services/claims/document_generator.py` | Must match DNR Form 10-162V fields — see details below |
| 11 | Generate-grid endpoint update | `api/views/claims_views.py` | Accept `claim_type: 'mtrsc'` with new parameters |
| 11a | Filing map generator | `services/claims/document_generator.py` or new | Generate 8.5x11" B&W map per 11 AAC 86.215 — see map requirements below |
| 11b | Statement of Annual Labor template | `services/claims/document_generator.py` | Template for annual filing per 11 AAC 86.220 |
| 11c | Rental Calculation Worksheet | `services/claims/document_generator.py` | DNR form for submitting first rental with certificate at Recorder's Office (see `mining-rental-worksheet-rentupdate.pdf`) |

#### Task 10 Detail: MTRSC Certificate of Location (DNR Form 10-162V, Revised 9/23)

**Verified against actual DNR form PDF.** The generated document must reproduce the official form layout. The same form serves dual purpose: Location Notice (posted on NE corner) and Certificate of Location (filed with recorder).

| Field | Source | Exact Label on Form |
|-------|--------|-------------------|
| Location Name/Number | User input (claim name) | "Location Name/Number:" |
| Owner's Name (1) | User profile / input | "1. Owner's Name:" — note: "The locator is the owner" |
| Mailing Address (1) | User profile / input | "Mailing Address:" |
| City, State, Zip (1) | User profile / input | "City, State, Zip:" |
| *Contact Phone (1) | User profile / input | "*Contact Phone:" (asterisk = optional) |
| *Email (1) | User profile / input | "*Email:" |
| Owner Name (2) | User input | "2. Owner Name:" under "Additional Locator/Owner: (please print)" |
| Mailing Address (2) | User input | |
| City, State, Zip (2) | User input | |
| *Contact Phone (2) | User input | Optional |
| *Email (2) | User input | Optional |
| Owner/Agent Signature (1) | Signature field | "1. Owner/Agent:" under "All owners or their agents must sign:" |
| Owner/Agent Signature (2) | Signature field | "2. Owner/Agent:" |
| Agent's Name | User input | "Agent's Name:" |
| Discovery Date | User input | "Discovery Date:" |
| Posting Date | User input | "Posting Date:" |
| Size of Location | From quarter_type | Checkboxes: "☐ Full Quarter Section (160 acres)" / "☐ Quarter-Quarter Section (40 acres)" |
| Recording District | From PLSS lookup | "Recording District:" |
| Meridian | From PLSS data | "Meridian:" under "Complete Legal Description:" |
| Township | From PLSS data | "Township:" |
| Range | From PLSS data | "Range:" |
| Section | From PLSS data | "Section:" |
| Quarter Section | From PLSS data | "Quarter Section:" |
| Qtr-Qtr Section | From PLSS data (if 40 ac) | "Qtr-Qtr Section (if 40 acres) _______ of _______" |
| Excludes | User input | "Excludes:" |
| Location Sketch | Generated map (see 11a) | Checkboxes: "☐ Attached to this certificate" / "☐ Attached to the certificate for the following locations:" with "Location name or number" field |
| First Rental | Computed | Checkboxes: "☐ $165.00 Qtr Section of a Township First Rental." / "☐ $40.00 Qtr-Qtr Section of a Township First Rental." |
| ADL | If available | "ADL, if Available _______________" |
| Receipt Type | Pre-printed | "Receipt Type: 80" |

**Verification statement (exact wording — must be reproduced verbatim):**
> "I hereby verify that the owner(s) listed above are qualified to hold mineral rights in Alaska per the requirements of AS 38.05.190, and as of the date above, a location notice was posted on the monument at the NE corner of this claim and to the best of my knowledge, in accordance with applicable statutes and regulations."

**Public records disclaimer (bottom of form):**
> "The information provided on this form is made a part of the state public land records and becomes public information under AS 40.25.110 and 40.25.120. Public information is open to inspection by you or any member of the public. A person who is the subject of the information may challenge its accuracy or completeness under AS 40.25.310, by giving a written description of the challenged information, the changes needed to correct it, and a name and address where the person can be reached."

**Additional form notes:**
- "Due Within 45 Days of Posting" printed next to rental checkboxes
- "Attach an extra sheet for Additional Owners and Signatures" — form supports >2 owners
- Back of form is marked "GENERAL INSTRUCTIONS ('DO NOT RECORD THIS SIDE')" — should NOT be included in recorded document

#### Task 10a Detail: Traditional Certificate of Location (DNR Form 10-162V, Revised 8/23)

Same form structure as MTRSC with these differences:

| Difference | MTRSC Form | Traditional Form |
|-----------|-----------|-----------------|
| Size/Dimensions | Checkboxes: 160 ac / 40 ac | "Feet Long in N-S Direction: ____" and "Feet Wide in E-W Direction: ____" |
| Legal Description | Structured fields (Meridian/T/R/S/Q/QQ) | Free text: "List all Meridian, Township, Range, Section and Qtr-Qtr Sections that apply" with example format |
| First Rental | $165 or $40 (two checkboxes) | Flat $40 (single checkbox) |
| Statute quoted on back | AS 38.05.196(b)(1) — MTRSC corners control | AS 38.05.195(b)(2) — "may not exceed 1,320 feet in its longest dimension, and its boundaries shall run in the four cardinal directions" |

#### Task 11a Detail: Filing Map Requirements (11 AAC 86.215)

The map attached to the Certificate of Location must meet these specifications:

- **Size:** 8.5" x 11" maximum
- **Color:** Black and white only
- **Scale:** 1:63,360 (1 inch = 1 mile) or more detailed
- **Required elements:**
  - Claim boundaries
  - Scale bar and North arrow
  - Dominant physical features of the land
  - Surveyed section lines (or protracted lines if unsurveyed)
  - Adjacent/contiguous mining claims, leasehold locations, mining leases, prospecting sites, mineral orders, and non-state land
- **Contiguous claims:** A single map may serve multiple certificates if recorded simultaneously, with cross-references on the others

#### Task 11b Detail: Statement of Annual Labor (11 AAC 86.220)

Required fields for this annual filing document:

1. Assessment work year (Mining Year: Sept 1 to Sept 1)
2. Name and ADL number for each claim/lease
3. Meridian, township, range, section for each location
4. Recording district
5. Total amount of work required ($100/40-ac unit, $400/160-ac unit)
6. Description of labor performed
7. Value breakdown: labor performed, excess from prior years, cash-in-lieu payment
8. Name and mailing address of owner designated for notices

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
| 18a | Filing map export | `processors/` (new or extend existing) | Generate 8.5x11" B&W PDF map at 1:63,360 scale per 11 AAC 86.215 for attaching to Certificate of Location |

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
| 21 | Traditional document template | Must match DNR Traditional form (10-162V rev 8/23) — see Task 10a detail above. Key differences: "Feet Long in N-S Direction" / "Feet Wide in E-W Direction" fields, free-text legal description, flat $40 rental, AS 38.05.195(b)(2) quote on back |
| 22 | Step 2 traditional mode | Similar to lode grid but with constrained inputs |

---

## Phase 4: Prospecting Sites (Optional / Future)

**Goal:** Support Alaska's unique Prospecting Site location type — no mineral discovery required, 2-year fixed term, MTRSC-only. Estimated: ~3-5 days (reuses Phase 2 MTRSC infrastructure).

| # | Task | Details |
|---|------|---------|
| 23 | Prospecting Site certificate template | DNR has a separate form. Fixed 2-year term, $305 one-time fee, no annual rent/labor |
| 24 | Claim type option in wizard | Add "Alaska Prospecting Site" to Step 1 type selector (MTRSC-only, no Traditional) |
| 25 | Prospecting site API path | Separate processing — no discovery date required, different fee structure, non-renewable |
| 26 | Conversion workflow | Allow converting a prospecting site to a mining claim if discovery is made during the term |

**Key differences from MTRSC mining claims:**
- No mineral discovery required (exploration-only right)
- Non-extendable, non-renewable by same locator until 1 year after expiration
- No annual rent or labor — single $305 payment covers the full term
- Can be converted to mining claim upon discovery

---

## Key Risks

1. **PLSS coverage gaps** — Much of Alaska is unsurveyed. MTRSC only works where PLSS data exists. Show clear warnings when no sections found.
2. **Section geometry irregularity** — PLSS sections aren't perfect rectangles. Quarter-section subdivision must handle irregular polygons (use centroid-based subdivision initially).
3. **State vs Federal jurisdiction** — Some AK land is BLM (federal claims), some is state (MTRSC). Consider adding a land status indicator in future.
4. **UI complexity** — MTRSC section picker is a fundamentally different interaction from lode grid generator. Isolate as a separate `MtrscLayoutWidget` class (see Isolation Rule #5).
5. **Lode regression** — Modifying shared code paths (Step 2, layer generator) risks breaking existing lode workflow. Mitigate by following Isolation Rules and running Regression Testing Protocol before merging.
6. **Layer generator hardcoding** — `claims_layer_generator.py` hardcodes `'lode_claims'` key and `"Lode_Azimuth"` field name from server responses. Server must return MTRSC data under a separate `'mtrsc_claims'` key to avoid collision. Plugin must handle both keys with fallback (see Isolation Rule #4).

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
- **Document validation:** Compare generated Certificate of Location field-by-field against extracted DNR Form 10-162V (MTRSC rev 9/23). Verify: exact field labels, verification statement wording, public records disclaimer, checkbox layout, "Receipt Type: 80", "Due Within 45 Days of Posting"
- **Dual-purpose check:** Verify the same document works as both Location Notice (with sketch) and Certificate of Location (with all fields + attached map)
- **Map validation:** Verify filing map meets 11 AAC 86.215 specs (8.5x11 max, B&W, indicated scale of 1:63,360 or better, scale bar, North arrow, claim boundaries, section lines, adjacent claims)
- **Rental calculation:** Verify first rental amount matches size ($40 QQ / $165 Q)
- **Contiguous claims map:** Verify single map can be cross-referenced across multiple certificates when recording simultaneously
- **Back page exclusion:** Verify "GENERAL INSTRUCTIONS" page is NOT included in the recorded document output

### Regression Testing (All Phases)

Run before merging any phase. See Regression Testing Protocol in Code Isolation Strategy section.

- **NV lode:** Full wizard → verify centerline monument, NV location notice
- **AZ lode:** Full wizard → verify endline monuments, AZ filing
- **WY lode:** Full wizard → verify sideline monuments, WY filing
- **ID lode:** Full wizard → verify corner-only (LM as Corner 1)
- **AK federal lode:** Full wizard → verify NE corner numbering, recording district, 45-day deadline
- Compare GPX and DOCX output against pre-MTRSC baseline for each state

### Phase 3 Testing
- Manual test: Traditional claims with various dimensions up to 1320x1320 ft
- Verify azimuth constraint enforced
- Verify document references AK state statutes
