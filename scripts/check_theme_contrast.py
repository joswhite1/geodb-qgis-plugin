#!/usr/bin/env python3
"""
Headless contrast/invisibility checker for the dark-mode token system.

Cannot render real Qt widgets on this VM (no Qt binding installed), so this
verifies the *invariant* that caused the original bug: a widget must never end
up with text whose color is too close to its own background, in either theme.

It does this without QGIS by:
  1. loading utils/theme.py with a stubbed QApplication for each theme,
  2. scanning every setStyleSheet f-string in the ui/ files,
  3. resolving the T.* tokens in each stylesheet for that theme,
  4. for each CSS rule block, flagging any block that sets a background but no
     color, or whose color and background have a contrast ratio below 3.0
     (WCAG AA-large threshold) — i.e. effectively invisible.

Run: python3 scripts/check_theme_contrast.py
Exit code 0 = clean, 1 = problems found.
"""
import os
import re
import sys
import types
import importlib.util

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI_DIR = os.path.join(ROOT, "ui")


# ---- stub qgis.PyQt so theme.py imports headless, parameterized by luminance ----
def _install_qgis_stub(window_luminance):
    qgis = types.ModuleType("qgis")
    pyqt = types.ModuleType("qgis.PyQt")
    qtgui = types.ModuleType("qgis.PyQt.QtGui")
    qtwidgets = types.ModuleType("qgis.PyQt.QtWidgets")

    class _Color:
        def __init__(self, l): self._l = l
        def redF(self): return self._l
        def greenF(self): return self._l
        def blueF(self): return self._l

    class QColor:  # noqa
        pass

    class QPalette:  # noqa
        class ColorRole:
            Window = "Window"
        Window = "Window"

    class _Pal:
        def __init__(self, l): self._l = l
        def color(self, role): return _Color(self._l)

    class _App:
        def __init__(self, l): self._l = l
        def palette(self): return _Pal(self._l)

    class QApplication:  # noqa
        @staticmethod
        def instance(): return _App(window_luminance)

    qtgui.QColor = QColor
    qtgui.QPalette = QPalette
    qtwidgets.QApplication = QApplication
    sys.modules.update({
        "qgis": qgis, "qgis.PyQt": pyqt,
        "qgis.PyQt.QtGui": qtgui, "qgis.PyQt.QtWidgets": qtwidgets,
    })


def _load_theme(luminance):
    for m in list(sys.modules):
        if m == "theme" or m.startswith("qgis"):
            del sys.modules[m]
    _install_qgis_stub(luminance)
    spec = importlib.util.spec_from_file_location(
        "theme", os.path.join(ROOT, "utils", "theme.py"))
    theme = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(theme)
    theme.refresh_theme()
    return theme


def _luminance(hexstr):
    h = hexstr.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    def lin(c):
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def _contrast(c1, c2):
    l1, l2 = _luminance(c1), _luminance(c2)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


TOKEN_RE = re.compile(r"\{T\.([A-Z_]+)\}")
HEX_RE = re.compile(r"#[0-9a-fA-F]{3,6}\b")
# crude CSS-block splitter: capture the body of each "selector {{ ... }}" block,
# along with the selector text that precedes the opening braces so we can tell
# a base rule from a pseudo-state sub-rule.
BLOCK_RE = re.compile(r"([^{};\n]*)\{\{(.*?)\}\}", re.DOTALL)
DECL_RE = re.compile(r"(?:^|;)\s*(background-color|color)\s*:\s*([^;}\n]+)")
# Pseudo-state / sub-element selectors that legitimately inherit text color
# from their base block when they only override the background (e.g. :hover).
PSEUDO_RE = re.compile(r":(hover|pressed|checked|focus|disabled|selected|!?selected)")
# Sub-elements that render no text, so a background without a text color is fine.
TEXTLESS_RE = re.compile(
    r"::(chunk|handle|groove|add-page|sub-page|indicator|"
    r"up-button|down-button|up-arrow|down-arrow|branch|separator|tab-bar)")


def _resolve(token_text, theme):
    """Replace {T.X} with the theme value; leave raw hex as-is."""
    def sub(m):
        return getattr(theme, m.group(1))
    return TOKEN_RE.sub(sub, token_text)


def check_file(path, theme, theme_name, problems):
    src = open(path, encoding="utf-8").read()
    # find every f-string stylesheet body (rough: look at setStyleSheet args)
    for blockm in BLOCK_RE.finditer(src):
        selector = blockm.group(1)
        body = blockm.group(2)
        decls = {}
        for d in DECL_RE.finditer(body):
            prop, val = d.group(1), d.group(2).strip()
            decls[prop] = val
        if "background-color" not in decls:
            continue
        bg_raw = _resolve(decls["background-color"], theme)
        bg = HEX_RE.search(bg_raw)
        if not bg:
            continue  # e.g. 'transparent' or a gradient — skip
        bg = bg.group(0)
        if "color" not in decls:
            # A pseudo-state / sub-element block (:hover, :pressed, ...) that
            # only changes the background inherits the base block's text color
            # — not the bug. Only a BASE block with no text color is the bug.
            if PSEUDO_RE.search(selector) or TEXTLESS_RE.search(selector):
                continue
            problems.append(
                f"[{theme_name}] {os.path.relpath(path, ROOT)}: base block "
                f"({selector.strip() or '?'}) sets background {bg} but NO text color")
            continue
        fg_raw = _resolve(decls["color"], theme)
        fg = HEX_RE.search(fg_raw)
        if not fg:
            continue
        fg = fg.group(0)
        ratio = _contrast(fg, bg)
        if ratio < 3.0:
            problems.append(
                f"[{theme_name}] {os.path.relpath(path, ROOT)}: low contrast "
                f"{ratio:.2f} (fg {fg} on bg {bg})")


def main():
    files = []
    for dirpath, _, names in os.walk(UI_DIR):
        for n in names:
            if n.endswith(".py"):
                files.append(os.path.join(dirpath, n))
    problems = []
    for lum, name in ((0.95, "light"), (0.12, "dark")):
        theme = _load_theme(lum)
        for f in files:
            check_file(f, theme, name, problems)
    if problems:
        print(f"FOUND {len(problems)} potential contrast/invisibility issues:\n")
        for p in problems:
            print("  " + p)
        return 1
    print("OK — no background-without-color blocks and no sub-3.0 contrast "
          "in either theme across", len(files), "files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
