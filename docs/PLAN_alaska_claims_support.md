# Alaska Mining Claims Support - Research & Implementation Plan

**Date:** 2026-03-18
**Status:** Research / Planning
**Audience:** Internal development reference

---

## 1. Overview

A client is interested in Alaska state mining claim support. This document captures research findings on Alaska's claim system and outlines what would be needed to add Alaska as a supported state in the QClaims wizard.

### Current State of the Plugin

The claims wizard already supports multiple US states with state-specific logic:
- **Idaho / New Mexico** — Monument-as-corner system (LM is Corner 1)
- **Wyoming** — Sideline monuments (midpoints of 1,500' sides)
- **Arizona** — Endline monuments (midpoints of 600' sides)
- **Nevada, California, etc.** — Standard lode with centerline discovery monument

Architecture is server-driven: plugin sends parameters to `/api/v2/claims/` endpoints, server returns processed claims with PLSS locations, corners, monuments. Plugin renders layers and exports GPX.

---

## 2. Alaska's Mining Claim System

### 2.1 State Claims (Alaska DNR)

Alaska state claims are fundamentally different from federal claims:

- **No lode/placer distinction** on state land. A single state mining claim covers both lode and placer mineral deposits.
- **Three location types:** Mining claims, leasehold locations, and prospecting sites.
- **Filing deadline:** 45 days from posting notice in the field.

#### Two Staking Methods

| Method | Size Options | Shape | Notes |
|--------|-------------|-------|-------|
| **MTRSC** (Meridian, Township, Range, Section, Corner) | 160 acres (quarter section) or 40 acres (quarter-quarter section) | Snaps to PLSS grid | GPS-located section corners |
| **Traditional** | Up to 1,320 x 1,320 ft (~40 acres) | Cardinal-aligned rectangle | Physical corner posts required |

**MTRSC claims** are the modern preferred method. The claimant identifies the PLSS section, then locates quarter or quarter-quarter section corners via GPS. Unlike federal association placer claims (which require 8+ persons for 160 acres), a single individual can locate a 160-acre MTRSC claim.

**Traditional claims** require four corner posts (2.5" minimum diameter, 3 ft high), lines running in cardinal directions, no two adjacent corners more than 1,320 ft apart. Acceptable materials: plastic PVC pipe, 4x4 post, rebar with 4x4 post on top, blazed tree, or rock cairn.

### 2.2 Federal Claims (BLM Land in Alaska)

Federal claims on BLM-administered land follow the General Mining Law of 1872:

| Claim Type | Max Size | Notes |
|-----------|----------|-------|
| Lode | 1,500 x 600 ft (~20 acres) | Along the vein — **already supported by our system** |
| Placer (individual) | 20 acres | Single locator |
| Association Placer | 160 acres | Requires 8+ persons |
| Mill Site | 5 acres | Processing facilities on non-mineral land |

**Federal claims can be converted to state claims** using a conversion location process.

### 2.3 Alaska's Five Principal Meridians

Alaska uses five PLSS meridians (more than any other state):

| Meridian | Year | Region |
|----------|------|--------|
| **Copper River** | 1905 | Southeast interior (near Copper Center) |
| **Seward** | 1911 | Southern Alaska, Anchorage, Aleutians |
| **Fairbanks** | 1910 | East-central Alaska to Canadian border |
| **Kateel River** | 1956 | Western central, Yukon River to Nome |
| **Umiat** | 1956 | North Slope, Arctic regions |

**Critical caveat:** Much of Alaska is **unsurveyed**. The PLSS grid is protracted (projected from survey data) rather than physically surveyed, which makes GPS-based MTRSC staking practical even where monuments don't exist on the ground.

### 2.4 Monument & Corner Post Requirements (11 AAC 86.205-210)

All Alaska state claims require physical monuments at each corner:

- **Minimum specs:** 2.5" diameter, 3 ft high
- **Materials:** PVC pipe, 4x4 post, rebar with 4x4 post on top, blazed tree, or rock cairn
- **Corner numbering:** Clockwise from NE — Corner #1 (NE), #2 (SE), #3 (SW), #4 (NW)
- **Each post marked with:** Name of location + corner number
- **Location Notice** must be posted on Corner #1 (NE corner monument)

#### MTRSC Corner Positioning (from staking fact sheet)

For MTRSC locations, the 4 posts must be positioned "at the aliquot corner locations for either a 1/4 section location or a 1/4-1/4 section location. These aliquot corner locations must be based on precise latitude/longitude coordinates (GPS) or topographic map quadrants within a section."

#### Traditional Corner Positioning (from staking fact sheet)

For traditional claims, "you must stake a 4-posted claim around your discovery so that the inferred lines connecting the four corners run in the cardinal directions; e.g. north-south, and east-west; and you must be certain that any two adjacent corner posts do not exceed 1,320 feet apart."

#### Witness Posts (for locations overlapping private land)

"Do not enter on private land when posting locations without the landowners permission." Witness posts are placed on nearby state land indicating the direction and distance to where a corner post should be. Must contain the same information required on the actual corner post (11 AAC 86.210).

#### Location Notice Requirements (posted on NE corner)

The location notice ("posting") on the NE corner must contain (from staking fact sheet, AS 38.05.195):
1. The name or number of the location
2. The date of posting the notice of location
3. If the mining claim is:
   - A) a traditional location: the length and width in feet
   - B) a MTRSC location: the meridian, township, range, section, and quarter section or quarter-quarter section
4. A sketch depicting, to the best of the locator's knowledge, the relationship of the location to adjoining or contiguous locations
5. The name and current mailing address of each locator

### 2.5 Certificate of Location (Filed Document)

The Certificate of Location must be recorded within **45 days** of posting the location notice. Failure to record = **automatic abandonment** (AS 38.05.265). Additionally, "The locator of an abandoned location or a successor in interest may not relocate the location until one year after the abandonment."

The certificate must be recorded at the **District Recorder's Office** for the area in which the claim is located. Recording fees change from time to time — check at https://dnr.alaska.gov/ssd/recoff/fees. The recorder's office forwards copies to DNR Division of Mining, Land & Water.

#### Certificate of Location Required Content (from staking fact sheet, AS 38.05.195(c), 11 AAC 86.215)

1. The name or number of the location
2. The dates (month, day, year) of both the locator's discovery and posting of the notice of location
3. For traditional: length and width in feet, and each meridian/township/range/section/QQ in which situated; For MTRSC: meridian, township, range, section, quarter-section, and if applicable quarter-quarter-section
4. For an MTRSC location: the meridian, township, range, section, quarter-section, and if applicable the quarter-quarter-section
5. The name and current mailing address of each locator, the signature of each locator or agent; if a trust, at least one trustee per AS 38.05.190(a)(1)
6. The name of the recording district
7. An attached map (see Map/Sketch Requirements below)

#### First Rental Payment (from staking fact sheet)

The first rental payment must be received by DNR **within 45 days of posting**, otherwise deemed abandoned. The first rental covers the period from the date of posting to the next September 1 (except prospecting sites which cover the full 2-year term).

| Location Type | First Rental |
|--------------|-------------|
| Traditional claim | $40 |
| MTRSC Quarter-Quarter Section (~40 ac) | $40 |
| MTRSC Quarter Section (~160 ac) | $165 |
| Prospecting Site | $305 (covers full 2-year term) |

**Three payment methods:**
1. In person at Anchorage PIC (550 W. 7th Ave, Suite 1360) or Fairbanks PIC (3700 Airport Way) — cash, check, Visa, or Mastercard
2. By mail to DNR Support Services Division, 550 W. 7th Ave, Suite 1410, Anchorage AK 99501-3561
3. At the Recorder's Office when recording the certificate — IF accompanied by a completed **Rental Calculation Worksheet**

#### Dual-Purpose Form

**Important:** The same DNR form (10-162V) serves as both the **Location Notice** (posted on the NE corner monument in the field) and the **Certificate of Location** (filed with the District Recorder's Office). When used as a Location Notice, it must include a sketch map. When used as a Certificate of Location, it must contain ALL fields plus an attached filing map.

#### MTRSC Certificate of Location (DNR Form 10-162V, Revised 9/23)

**Verified against actual DNR form.** Required fields on the official form:

| Field | Notes |
|-------|-------|
| Location Name/Number | |
| Owner's Name (1) | "The locator is the owner" |
| Mailing Address (1) | Where correspondence should be sent |
| City, State, Zip (1) | |
| *Contact Phone (1) | Optional (asterisk items) |
| *Email (1) | Optional |
| Owner Name (2) | Additional locator/owner |
| Mailing Address (2) | |
| City, State, Zip (2) | |
| *Contact Phone (2) | Optional |
| *Email (2) | Optional |
| Owner/Agent Signature (1) | "All owners or their agents must sign" |
| Owner/Agent Signature (2) | |
| Agent's Name | If agent signs on behalf of owner |
| Discovery Date | |
| Posting Date | |
| Size of Location | Checkbox: ☐ Full Quarter Section (160 acres) / ☐ Quarter-Quarter Section (40 acres) |
| Recording District | |
| Meridian | Complete Legal Description section |
| Township | |
| Range | |
| Section | |
| Quarter Section | |
| Qtr-Qtr Section (if 40 acres) | "_______ of _______" format |
| Excludes | Free text |
| Location Sketch | Checkbox: ☐ Attached to this certificate / ☐ Attached to the certificate for the following locations: _______ |
| First Rental | Checkbox: ☐ $165.00 Qtr Section / ☐ $40.00 Qtr-Qtr Section. "Due Within 45 Days of Posting" |
| ADL, if Available | Alaska Division of Lands case number |
| Receipt Type | Pre-printed: "80" |

**Verification statement (exact wording from form):**
> "I hereby verify that the owner(s) listed above are qualified to hold mineral rights in Alaska per the requirements of AS 38.05.190, and as of the date above, a location notice was posted on the monument at the NE corner of this claim and to the best of my knowledge, in accordance with applicable statutes and regulations."

**Public records notice (bottom of form):**
> "The information provided on this form is made a part of the state public land records and becomes public information under AS 40.25.110 and 40.25.120."

**Back of form (general instructions — "DO NOT RECORD THIS SIDE"):**
- Includes AS 38.05.196(b)(1) quote: corners "marked on the ground of a claim established in accordance with this paragraph and regulations of the commissioner control in the event of a conflict over boundaries"
- Recording instructions: submit original to District Recorder's Office within 45 days
- First rental can be paid at Recorder's Office IF accompanied by a **Rental Calculation Worksheet**
- Contact: Mineral Property Management Office at 907-269-8642 or dnr.dmlw.mpm@alaska.gov

**Note:** Form supports "Attach an extra sheet for Additional Owners and Signatures" beyond the 2 owner slots.

#### Traditional Certificate of Location (DNR Form 10-162V, Revised 8/23)

**Verified against actual DNR form.** Same structure as MTRSC except:
- **Location Dimension** replaces Size checkboxes:
  - "Feet Long in N-S Direction: ________________"
  - "Feet Wide in E-W Direction: ________________"
- **Legal Description** is free-text: "List all Meridian, Township, Range, Section and Qtr-Qtr Sections that apply to this location: example (Meridian: Fairbanks T: 10N R: 5E S: 7 NW Qtr of SE Qtr)"
- **First Rental** is flat: ☐ $40. No size options.
- Back of form quotes AS 38.05.195(b)(2): "A locator may locate a claim based on the staking of a ground location in which the claim may not exceed 1,320 feet in its longest dimension, and its boundaries shall run in the four cardinal directions."

#### Map/Sketch Requirements (11 AAC 86.215)

**Verified against actual DNR form instructions and staking fact sheet.** A separate map must be attached to the Certificate of Location:
- **Size:** 8.5" x 11" maximum (per form back instructions)
- **Color:** Black and white
- **Scale:** 1:63,360 (one inch = one mile) or more detailed, with indicated scale
- **Must include:**
  - Claim boundaries
  - Scale bar ("a scale")
  - North arrow ("a direction (North) arrow")
  - Dominant physical features of the land
  - Surveyed section lines (or protracted section lines if unavailable)
  - Relationship to adjacent/contiguous mining claims, leasehold locations, mining leases, prospecting sites, mineral orders, and non-state land
- **Contiguous claims:** If recording simultaneously, a single map may be attached to one certificate with cross-references on each other certificate to which the map applies

### 2.6 Annual Costs

#### Annual Rental (AS 38.05.211, 11 AAC 86.221)

State claim rental rates escalate over time:

| Years Held | QQ Section (40 ac) | Q Section (160 ac) | Traditional (≤40 ac) |
|-----------|--------------------|--------------------|---------------------|
| 0-5 | $40 | $165 | $40 |
| 6-10 | $85 | $330 | $85 |
| 11+ | $205 | $825 | $205 |

- Rental year = "Mining Year" = September 1 noon to September 1 noon
- Payments due by November 30
- Late payment = automatic abandonment
- Rates adjusted every 10 years based on Anchorage CPI (last adjusted August 2019)

Federal claims: $165/claim maintenance fee, due September 1.

#### Annual Labor (AS 38.05.210, 11 AAC 86.220)

| Location Type | Required Value |
|--------------|---------------|
| Traditional (≤40 ac) | $100 |
| MTRSC QQ Section (40 ac) | $100 |
| MTRSC Q Section (160 ac) | $400 |

- Statement of Annual Labor must be signed, notarized, and recorded by November 30
- Cash-in-lieu payment option available for up to 5 consecutive years (must be received by September 1)
- Excess labor may be carried forward up to 4 subsequent years
- Federal claims: $100/claim assessment work

#### Statement of Annual Labor Required Fields (11 AAC 86.220)

1. Assessment work year
2. Name and ADL number for each claim/lease
3. Meridian, township, range, section for each location
4. Recording district
5. Total amount of work required
6. Description of labor performed
7. Value breakdown: labor performed, excess from prior years, cash payment
8. Name and mailing address of owner designated for notices

### 2.7 Prospecting Sites (AS 38.05.245)

A fourth location type unique to Alaska that does **not** require a mineral discovery:
- **Purpose:** Grants exclusive prospecting and conversion rights for exploration
- **Term:** Fixed 2-year term, non-extendable, non-renewable by same locator until 1 year after expiration
- **Method:** MTRSC legal description only (no Traditional option)
- **Fee:** $305 one-time payment (covers entire 2-year term)
- **Conversion:** Can be converted to a mining claim if discovery is made during the term
- **Form:** DNR Prospecting Site Certificate (separate form from mining claim certificates)

### 2.8 Ground Staking Requirement

**Alaska still requires physical ground staking.** Unlike BC (fully electronic via Mineral Titles Online), there is no electronic filing system for claim acquisition. The GIS tool's role is to:
- Help plan claim layouts before going to the field
- Generate GPS waypoints for navigation to staking locations
- Produce documentation for filing with the State Recording District Office

---

## 3. GIS Data Sources

| Resource | URL | Content |
|----------|-----|---------|
| Alaska DNR Open Data | `data-soa-dnr.opendata.arcgis.com` | Active/pending/closed state mining claims |
| Alaska DNR Mapper | `mapper.dnr.alaska.gov` | Interactive map of all DNR data |
| Alaska Mining Claims Mapper | `akmining.info` | Focused mining claims viewer |
| BLM SDMS | `sdms.ak.blm.gov` | PLSS data, federal claims |
| GIS Data Alaska | `gis.data.alaska.gov` | Shapefiles, WFS, ArcGIS REST |

**Standard CRS:** NAD83 / Alaska Albers (EPSG:3338) for statewide display. Individual Alaska State Plane zones (EPSG:3469-3477) or UTM zones for local accuracy.

---

## 4. Key Differences from Currently Supported States

| Aspect | Current States (NV, AZ, WY, etc.) | Alaska |
|--------|------------------------------------|--------|
| Claim types | Lode (600x1500 ft) or Placer (20 ac) | MTRSC (40/160 ac) or Traditional (≤1320x1320 ft) or Federal lode/placer |
| Grid basis | User-defined start point + azimuth | MTRSC snaps to PLSS section grid; Traditional is cardinal-aligned |
| Rotation | Azimuth supported (0-360°) | MTRSC: none (follows PLSS); Traditional: cardinal only |
| Mineral type | Separate lode vs placer | State claims: unified (both); Federal: separate |
| Monument rules | State-specific (centerline, sideline, endline) | Corner-only: 2.5" dia, 3 ft high, NE corner = #1 (clockwise), location notice on NE post |
| PLSS meridians | Standard (varies by state) | Five separate meridians, much unsurveyed |
| Claim size | 600x1500 ft (lode) or up to 20 ac (placer) | 40-160 acres (state) — significantly larger |

---

## 5. Implementation Considerations

### 5.1 Server-Side Work (API)

1. **PLSS grid data for Alaska** — Ingest section/township/range boundaries for all 5 meridians from BLM SDMS data
2. **MTRSC grid snapping** — New endpoint or extension of `generate-grid/` to snap claims to quarter/quarter-quarter section boundaries
3. **State info for Alaska** — Add Alaska to `state-info/{state}/` endpoint with:
   - Supported claim types (MTRSC, Traditional, Federal Lode, Federal Placer)
   - Monument requirements
   - Filing deadlines and costs
   - Required documentation templates
4. **Traditional claim grid** — Cardinal-aligned rectangles up to 1,320 x 1,320 ft (simpler than lode rotation)
5. **Process claims for Alaska** — PLSS location lookup, corner coordinates, monument placement rules

### 5.2 Plugin-Side Work

1. **Add Alaska to wizard Step 1** — State selection, show Alaska-specific options
2. **New claim type selector** — MTRSC (40 ac / 160 ac) vs Traditional vs Federal Lode/Placer
3. **MTRSC grid UI** — Instead of rows/cols with azimuth, user selects township/range/section on the map and chooses quarter or quarter-quarter subdivisions
4. **Traditional claim UI** — Similar to current lode but cardinal-only (no azimuth), max 1,320 ft sides
5. **CRS handling** — Default to EPSG:3338 (Alaska Albers) or detect appropriate Alaska UTM zone
6. **GPX export** — Add Alaska-specific monument/waypoint rules
7. **Existing claims overlay** (nice-to-have) — Pull active claims from DNR Open Data to show conflicts

### 5.3 Open Questions

- [x] ~~What are Alaska's specific monument requirements?~~ — **Resolved:** 2.5" dia, 3 ft high, NE corner = #1, clockwise numbering. See Section 2.4.
- [ ] Does the server already have PLSS data for Alaska, or does it need to be ingested?
- [ ] Should we support all three claim types (MTRSC, Traditional, Federal) or prioritize one?
- [x] ~~What documentation does Alaska require for filing?~~ — **Resolved:** Certificate of Location (DNR 10-162V) + attached B&W map at 1:63,360 scale + Statement of Annual Labor. See Sections 2.5 and 2.6.
- [ ] Do we need to handle the "unsurveyed but protracted" PLSS areas differently?
- [ ] What is the client's specific use case — state claims, federal claims, or both?
- [ ] Payment model — would Alaska claims be Enterprise, pay-per-claim, or both?
- [ ] Should we support Prospecting Sites (no discovery required, 2-year term)? See Section 2.7.
- [ ] Should the plugin generate the 8.5x11 B&W filing map, or just the GPX export? (DNR forms confirm the map is required for recording — strongly recommend generating it)
- [ ] Should we generate a Statement of Annual Labor template as a document deliverable?
- [x] ~~What are the exact form fields and wording?~~ — **Resolved:** Extracted verbatim from DNR Form 10-162V (MTRSC rev 9/23, Traditional rev 8/23). See Section 2.5.
- [x] ~~What are the first rental amounts and payment methods?~~ — **Resolved:** $40 QQ, $165 Q, $305 prospecting. Three payment methods including at Recorder's Office with Rental Calculation Worksheet. See Section 2.5.
- [x] ~~What is the rental escalation schedule?~~ — **Resolved:** Verified against Key Dates fact sheet. See Section 2.6.

### 5.4 Estimated Effort

| Component | Complexity | Notes |
|-----------|-----------|-------|
| Server: PLSS data ingestion | High | Large dataset, 5 meridians, many unsurveyed areas |
| Server: MTRSC grid logic | Medium | Section subdivision is well-defined math |
| Server: Traditional claim logic | Low | Simpler than lode (no rotation, cardinal only) |
| Server: State info endpoint | Low | Configuration data |
| Plugin: Wizard state selection | Low | Add Alaska to dropdown |
| Plugin: MTRSC claim type UI | Medium | New interaction pattern (section selection vs row/col grid) |
| Plugin: Traditional claim UI | Low | Simplified version of existing lode UI |
| Plugin: GPX export for Alaska | Low | Add state to monument rules |
| Plugin: Existing claims overlay | Medium | New feature, depends on DNR data format |
| Server: Filing map generator | Medium | 8.5x11 B&W map at 1:63,360 scale per 11 AAC 86.215 |
| Server: Statement of Annual Labor template | Low | Required annual filing document per 11 AAC 86.220 |

---

## 6. Key Deadlines Summary (Verified Against DNR Key Dates Fact Sheet, July 2021)

| Action | Time Period Covered | Deadline | Where |
|--------|-------------------|----------|-------|
| Record Certificate of Location | — | Within 45 days after posting date | District Recorder's Office in which claim is located |
| Pay first rental ($40 QQ/$165 Q/$305 prospecting) | Claims: posting date through Aug 31. Prospecting: full 2-year term | Within 45 days after posting date | DNR PIC offices, Anchorage Financial Services, or Recording Office with worksheet |
| Pay annual rental (years 0-5: $40/$165; 6-10: $85/$330; 11+: $205/$825) | Rental Years begin at noon Sept 1 | Due Sept 1, payable no later than Nov 30 | Per courtesy billing notice from DNR |
| Record Statement of Annual Labor ($100/40-ac unit, $400/160-ac unit) OR pay cash-in-lieu | Labor Year: noon Sept 1 through noon Sept 1 | Statement recorded by Nov 30; cash payment received by Sept 1 | District Recorder's Office (statement); DNR (cash payment) |
| Mining License Tax | Tax Year | April 30 | Dept of Revenue |
| Production Royalty Return | Calendar Year | May 1 | DMLW |

**State-Selected Land:** Annual Labor is not due until the State receives tentative approval or patent from the federal government. The Labor Year begins at noon on the first Sept 1 after conveyance. Annual rental begins on date of conveyance and must be received within 90 days.

---

## 7. DNR Forms & Fact Sheets

### Official Forms (dnr.alaska.gov/mlw/forms/)

| Form | Filename |
|------|----------|
| MTRSC Certificate of Location | `State-MTRSC-Location-Cert.pdf` |
| Traditional Certificate of Location | `State-Mining-Location-Notice-Certificate-Traditional-Location-Only.pdf` |
| Amended MTRSC Certificate | `Amended-State-Mining-Location-Notice-Cert-MTRSC-Locations-Only.pdf` |
| Amended Traditional Certificate | `Amended-State-Traditional-Site-Cert.pdf` |
| Prospecting Site Certificate | `State-Mining-Location-Notice-Cert-for-Prospecting-Sites.pdf` |
| Conversion to MTRSC | `Mining-Claims-Amended-For-Conversion-To-MTRSC.pdf` |
| Statement of Annual Labor | `Statement-of-Annual-Labor-2021-7-2.pdf` |
| Rental Calculation Worksheet | `mining-rental-worksheet-rentupdate.pdf` |
| Mining Quitclaim Deed | `Mining-Quitclaim-Deed-May24.pdf` |

### Fact Sheets (dnr.alaska.gov/mlw/cdn/pdf/factsheets/)

| Topic | Filename |
|-------|----------|
| Staking Requirements | `staking-requirements-for-mineral-locations-on-state-land.pdf` |
| Annual Rent | `annual-rent.pdf` |
| Annual Labor | `annual-labor.pdf` |
| Key Dates | `keydates-for-miners-on-state-land.pdf` |
| MTRSC Prospecting Sites | `mtrsc-prospecting-site-locations.pdf` |
| Leasehold Locations | `upland-mining-leasehold-locations.pdf` |

### Relevant Statutes & Regulations

| Code | Topic |
|------|-------|
| AS 38.05.185 | Mining Rights |
| AS 38.05.190 | Who may locate |
| AS 38.05.195 | Mining claims (location methods, MTRSC, certificate requirements) |
| AS 38.05.196 | MTRSC location specifics |
| AS 38.05.210 | Annual labor |
| AS 38.05.211 | Annual rental |
| AS 38.05.240 | Definition of "labor" |
| AS 38.05.245 | Prospecting sites |
| AS 38.05.265 | Abandonment |
| 11 AAC 86.205 | Staking requirements |
| 11 AAC 86.210 | Monument/corner post requirements |
| 11 AAC 86.215 | Recording requirements / certificate of location |
| 11 AAC 86.220 | Statement of annual labor |
| 11 AAC 86.221 | Annual rental |
| 11 AAC 86.400-435 | Prospecting sites |

---

## 8. References

- [Alaska DNR Mineral Property Management](https://dnr.alaska.gov/mlw/mining/mpm/)
- [Alaska DNR Staking Requirements Fact Sheet](https://dnr.alaska.gov/mlw/cdn/pdf/factsheets/staking-requirements-for-mineral-locations-on-state-land.pdf)
- [Alaska DNR MTRSC Prospecting Site Fact Sheet](https://dnr.alaska.gov/mlw/cdn/pdf/factsheets/mtrsc-prospecting-site-locations.pdf)
- [2024 Alaska Statutes - Mining Claims (AS 38.05.195)](https://law.justia.com/codes/alaska/title-38/chapter-05/article-8/section-38-05-195/)
- [BLM Alaska Federal Mining Claims Guide (2019)](https://www.blm.gov/sites/default/files/documents/files/Alaska_Mining_2019-Mining-Claim-Info-Guide.pdf)
- [BLM Mining Claims and Sites on Federal Lands](https://www.blm.gov/sites/blm.gov/files/MiningClaims.pdf)
- [Principal Meridians of Alaska - Wikipedia](https://en.wikipedia.org/wiki/Principal_meridians_of_Alaska)
- [Alaska DNR Open Data - State Mining Claims](https://data-soa-dnr.opendata.arcgis.com/maps/SOA-DNR::state-mining-claim-1/about)
- [Alaska Mining Claims Mapper](https://akmining.info/)
- [Staking Claims in Alaska: A Primer - Burgex Mining Consultants](https://www.burgex.com/2016/02/05/staking-claims-in-alaska-a-primer/)
- [Alaska DNR Annual Rent Fact Sheet](https://dnr.alaska.gov/mlw/cdn/pdf/factsheets/annual-rent.pdf)
- [Alaska DNR Annual Labor Fact Sheet](https://dnr.alaska.gov/mlw/cdn/pdf/factsheets/annual-labor.pdf)
- [Alaska DNR Forms Page](https://dnr.alaska.gov/mlw/forms/)
- [Alaska DNR Key Dates Fact Sheet](https://dnr.alaska.gov/mlw/cdn/pdf/factsheets/keydates-for-miners-on-state-land.pdf)
- [Alaska Mining Laws & Regulations Book (PDF)](https://dnr.alaska.gov/mlw/mining/pdf/Mining_Statute_and_Regulation_Book.pdf)
- [Alaska DNR Mineral Property Management Contact](https://dnr.alaska.gov/mlw/mining/mpm/) — (907) 269-8642 / dnr.dmlw.mpm@alaska.gov
