# QGIS plugin dark-mode compliance

**Status:** IN PROGRESS — `feature/dark-mode-tokens` worktree
**Trigger:** QGIS 4 ships a dark UI theme; the plugin hardcoded a light-theme
Tailwind palette in ~480 inline `setStyleSheet` calls. Most visible symptom:
the login form's input text was invisible (white-on-white) — a white input
background with no text color, so Qt 4's dark-theme default light text
disappeared until selected.

## Approach (decided with Joshua 2026-06-15)

**A — palette-aware design tokens.** Light mode reproduces the previous look
pixel-for-pixel (same Tailwind shades); dark mode maps each semantic token to
its Tailwind dark-mode counterpart. Chosen over a fixed self-consistent theme
so the plugin looks native in both QGIS themes, and over "match QGIS chrome"
so the existing light look is preserved exactly.

## Architecture

- **`utils/theme.py`** — the one source of truth. `is_dark_theme()` reads the
  app `QPalette` Window luminance (works Qt5/QGIS3 + Qt6/QGIS4, honors UI-theme
  + OS dark mode). Singleton `T` exposes semantic tokens (`TEXT_PRIMARY`,
  `SURFACE`, `BORDER`, `ACCENT`, `SUCCESS`/`WARNING`/`DANGER`/`INFO`, a
  `SLATE_*` family for the claims wizard) that resolve per-theme.
- Every widget `setStyleSheet` becomes an f-string pulling `T.*` instead of raw
  hex. Inputs always set BOTH background and `color` (the bug fix).
- Top-level `QDialog`s get a `T.SURFACE` background so the card reads on dark.
- **Semantic data colors are NOT tokenized** — commodity/mineral swatches
  (gold, copper, platinum), map-feature fills, category colors keep their raw
  hex (they mean the same thing in both themes).

This centralizes color in one file — matches the workspace "no duplicate paths"
rule. Future theme tweaks touch `theme.py`, not 27 files.

## Verification

- `utils/theme.py` token logic unit-tested headless (stubbed QApplication):
  light preserves original hex, dark resolves to contrast values, missing token
  raises, no-app defaults to light. ✅
- **`scripts/check_theme_contrast.py`** — headless guard for the bug class:
  resolves every stylesheet's tokens in BOTH themes and flags any block that
  sets a background with no text color, or fg/bg contrast < 3.0 (WCAG
  AA-large). This is the automated stand-in for visual QA.
- **VISUAL QA must happen in real QGIS** — the VM has no Qt binding installed,
  so widgets cannot be rendered/screenshotted here. Joshua (or a QGIS 4 box)
  needs to open the plugin in dark mode and eyeball the login form, claims
  wizard, basemaps tab, and project-files tabs. This is the one gap static
  checks can't close.

## Files

- Foundation + reference: `utils/theme.py`, `ui/login_dialog.py` (commit c2fd5bd)
- 26 remaining UI files converted via parallel agents (workflow), one per file.

## Ship

Version bump to 2.21.2 + changelog entry, commit on `feature/dark-mode-tokens`,
merge to `v2.1` (Joshua merges locally), rebuild the install zip, send to
customers on QGIS 4.
