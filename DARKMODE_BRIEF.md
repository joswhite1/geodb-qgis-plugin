# Dark-mode tokenization brief (for refactor agents)

Goal: make the geoDB QGIS plugin render correctly on **both** QGIS 3 (light)
and QGIS 4 (dark). The mechanism is already built: `utils/theme.py` exposes a
singleton `T` of semantic color tokens that resolve to light- or dark-mode hex
at runtime. **Reference implementation: `ui/login_dialog.py`** — match its style.

## Your job, per file

1. Add the import near the other `..utils` imports:
   `from ..utils.theme import T`
   (For files in `ui/claims_step_widgets/`, the path is `from ...utils.theme import T`
   — three dots, since they are one level deeper. Verify against the file's
   existing relative imports.)

2. Convert every inline color in every `setStyleSheet(...)` call from a raw
   hex literal to the matching token, turning the string into an f-string
   (`"..."` → `f"..."`, and **double every literal `{` and `}`** that is part
   of CSS so the f-string doesn't treat them as fields — see login_dialog.py
   `_get_input_style`).

3. If the widget is a **top-level dialog** (subclasses `QDialog`) and does not
   already paint its own background, add a surface background on the dialog so
   its card reads in dark mode, exactly like login_dialog:
   `self.setStyleSheet(f"ClassName {{ background-color: {T.SURFACE}; }}")`
   Skip this for plain `QWidget` panels embedded in the main dialog (they
   inherit the host background) unless they currently set a white background.

4. **The load-bearing fix:** any `QLineEdit` / `QComboBox` / `QTextEdit` /
   `QPlainTextEdit` / `QSpinBox` style that sets a `background-color` MUST also
   set `color:` (use `T.TEXT_PRIMARY`). The reported bug was a white input
   background with no text color → invisible text on dark. Add the missing
   `color:` wherever it's absent.

## Hex → token mapping (UI chrome)

| Hex (light) | Token |
|---|---|
| `#374151` | `T.TEXT_PRIMARY` |
| `#1f2937` | `T.TEXT_STRONG` |
| `#6b7280` | `T.TEXT_MUTED` |
| `#9ca3af` | `T.TEXT_FAINT` |
| `#ffffff` (as text on accent / button) | `T.TEXT_ON_ACCENT` |
| `white` (as text) | `T.TEXT_ON_ACCENT` |
| `#ffffff` (as surface/card bg) | `T.SURFACE` |
| `#f9fafb` | `T.SURFACE_SUBTLE` |
| `#f3f4f6` | `T.SURFACE_SUNKEN` |
| `#ffffff` (as input bg) | `T.INPUT_BG` |
| `#d1d5db` | `T.BORDER` |
| `#e5e7eb` | `T.BORDER_SUBTLE` |
| `#2563eb` | `T.ACCENT` (or `T.ACCENT_TEXT` when used as text/brand color) |
| `#1d4ed8` | `T.ACCENT_HOVER` |
| `#1e40af` / `#1e3a8a` | `T.ACCENT_ACTIVE` |
| `#93c5fd` | `T.ACCENT_DISABLED` |
| `#3b82f6` | `T.ACCENT` |
| `#059669` / `#10b981` / `#4caf50` / `#2e7d32` | `T.SUCCESS` |
| `#047857` / `#065f46` | `T.SUCCESS_TEXT` |
| `#d1fae5` / `#a7f3d0` / `#ecfdf5` | `T.SUCCESS_BG` |
| `#f59e0b` / `#ff9800` / `#fbbf24` | `T.WARNING` |
| `#b45309` / `#854d0e` / `#92400e` / `#d97706` | `T.WARNING_TEXT` |
| `#fef3c7` / `#fefce8` / `#fffbeb` | `T.WARNING_BG` |
| `#dc2626` / `#ef4444` / `#d32f2f` / `#e74c3c` / `#cc0000` / `#c0392b` | `T.DANGER` |
| `#b91c1c` / `#991b1b` / `#8b0000` | `T.DANGER_TEXT` |
| `#fef2f2` / `#fee2e2` / `#fecaca` | `T.DANGER_BG` |
| `#0891b2` / `#06b6d4` / `#0284c7` / `#03a9f4` / `#00bcd4` / `#3498db` / `#2980b9` | `T.INFO` |
| `#0369a1` / `#0277bd` / `#0e7490` / `#00838f` | `T.INFO_TEXT` |
| `#f0f9ff` / `#eff6ff` / `#dbeafe` / `#bae6fd` / `#bfdbfe` | `T.INFO_BG` |
| `#475569` | `T.SLATE_TEXT` |
| `#64748b` | `T.SLATE_MUTED` |
| `#94a3b8` | `T.SLATE_FAINT` |
| `#cbd5e1` | `T.SLATE_BORDER` |
| `#e2e8f0` | `T.SLATE_BORDER_SUBTLE` |
| `#f8fafc` / `#fafafa` / `#f0f0f0` | `T.SLATE_SURFACE` |
| `#f1f5f9` | `T.SLATE_SUNKEN` |
| `#1e293b` | `T.SLATE_STRONG` |
| neutral grays `#000000`,`#333333`,`#666666`,`#808080`,`#cccccc`,`#eeeeee`,`#9e9e9e`,`#757575`,`#616161`,`#424242` used as **text** | nearest of `T.TEXT_STRONG`/`T.TEXT_PRIMARY`/`T.TEXT_MUTED`/`T.TEXT_FAINT` by darkness |

## Leave these ALONE (do NOT tokenize) — semantic data colors

These encode meaning that is identical in both themes; tokenizing them would be
wrong. Keep the raw hex:

- **Commodity / mineral colors:** gold `#ffd700`/`#f1c40f`, copper `#b87333`/`#cc6600`,
  platinum `#e5e4e2`, silver `#c0c0c0`, and similar metal/element colors.
- **Map feature / claim-type fills and outlines** passed to QGIS symbol layers,
  `QgsSymbol`, renderer categories, or written into layer styling (not widget
  chrome). If the color is going to a *map layer* rather than a *Qt widget
  stylesheet*, leave it.
- Purple/violet category swatches (`#8b5cf6`,`#7c3aed`,`#9c27b0`,`#673ab7`, etc.)
  when they are **data category** colors. If purple is used as UI chrome
  (rare), map to nearest token; when unsure, LEAVE IT.

If a color does not clearly map to a chrome token and isn't obviously data,
**leave it unchanged** and note it — do not guess.

## Output

Edit the file in place. At the end, report: file path, number of
`setStyleSheet` calls converted, any colors you deliberately left as raw hex
(with one-line reason), and whether you added a dialog surface background.
Do NOT run git. Do NOT edit any file other than the one assigned to you.
