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

**Traditional claims** require four corner posts (3" diameter, 3 ft high), lines running in cardinal directions, no two adjacent corners more than 1,320 ft apart.

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

### 2.4 Annual Costs

| Fee | State Claims | Federal Claims |
|-----|-------------|---------------|
| Annual rent | $100/40-acre unit; $400/160-acre unit | $165/claim maintenance fee |
| Annual labor | $100/40-acre unit (or cash in lieu) | $100/claim assessment work |
| Due date | November 30 | September 1 |

### 2.5 Ground Staking Requirement

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
| Monument rules | State-specific (centerline, sideline, endline) | TBD — need to research Alaska-specific monument requirements |
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

- [ ] What are Alaska's specific monument requirements? (post size, markings, inscriptions)
- [ ] Does the server already have PLSS data for Alaska, or does it need to be ingested?
- [ ] Should we support all three claim types (MTRSC, Traditional, Federal) or prioritize one?
- [ ] What documentation does Alaska require for filing? (location notice format, affidavit of labor)
- [ ] Do we need to handle the "unsurveyed but protracted" PLSS areas differently?
- [ ] What is the client's specific use case — state claims, federal claims, or both?
- [ ] Payment model — would Alaska claims be Enterprise, pay-per-claim, or both?

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

---

## 6. References

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
