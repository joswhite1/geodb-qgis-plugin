# -*- coding: utf-8 -*-
"""
Theme tokens for light / dark mode compliance (QGIS 3 + QGIS 4 / Qt6).

WHY THIS EXISTS
---------------
The plugin's widgets hardcoded a light-theme Tailwind palette inline in ~480
``setStyleSheet`` calls (white card backgrounds, dark gray text). On QGIS 4's
dark theme that produced unreadable or invisible UI — most visibly the login
form, where input boxes set a white background but no text color, so Qt's
dark-theme default light text rendered white-on-white (visible only when
selected).

The fix is to stop hardcoding raw hex and instead pull every color from one
set of *semantic tokens* defined here. Each token resolves at runtime to the
appropriate hex for the active theme:

  - LIGHT mode reproduces the previous design pixel-for-pixel (same Tailwind
    shades the plugin already used), so there is no light-mode visual change.
  - DARK mode maps each token to its Tailwind dark-mode counterpart, so text
    stays high-contrast on QGIS 4's dark panels.

Centralizing here matches the workspace "no duplicate paths" rule: color lives
in exactly one place, so future theme fixes touch one file, not 27.

USAGE
-----
    from ..utils.theme import T

    label.setStyleSheet(f"color: {T.TEXT_PRIMARY};")

    edit.setStyleSheet(f'''
        QLineEdit {{
            background-color: {T.INPUT_BG};
            color: {T.TEXT_PRIMARY};          # <- the property that was missing
            border: 1px solid {T.BORDER};
        }}
    ''')

``T`` is a module-level singleton resolved once at import. Because a QGIS user
can switch theme without restarting, call ``refresh_theme()`` (or read tokens
through ``current_tokens()``) when you want to re-resolve; widgets that build
their stylesheet in ``__init__`` pick up the theme that was active when the
dialog opened, which is the common case and matches how the plugin already
behaves.
"""

from qgis.PyQt.QtGui import QColor, QPalette
from qgis.PyQt.QtWidgets import QApplication


# ============================================================
# Dark-mode detection
# ============================================================
def is_dark_theme() -> bool:
    """
    Return True when the host (QGIS / OS) is rendering a dark UI theme.

    Detected from the application palette's Window color luminance, which is
    reliable on both Qt5 (QGIS 3) and Qt6 (QGIS 4) and honors the user's QGIS
    "UI Theme" setting as well as OS-level dark mode. Falls back to False
    (light) if no QApplication exists yet (e.g. during headless import).
    """
    app = QApplication.instance()
    if app is None:
        return False
    try:
        window = app.palette().color(QPalette.ColorRole.Window)
    except AttributeError:
        # Qt5 unscoped enum
        window = app.palette().color(QPalette.Window)
    # Perceived luminance (Rec. 601). Below ~0.5 => dark surface.
    r, g, b = window.redF(), window.greenF(), window.blueF()
    luminance = 0.299 * r + 0.587 * g + 0.114 * b
    return luminance < 0.5


# ============================================================
# Token tables
# ============================================================
# Semantic name -> hex. LIGHT values are the exact Tailwind shades the plugin
# already used, so light mode is unchanged. DARK values are the Tailwind
# dark-mode counterparts (lighter text, darker surfaces, accents nudged
# brighter for contrast on dark backgrounds).

_LIGHT = {
    # --- Text ---
    "TEXT_PRIMARY":   "#374151",  # gray-700  (body text, labels)
    "TEXT_STRONG":    "#1f2937",  # gray-800  (headings)
    "TEXT_MUTED":     "#6b7280",  # gray-500  (secondary text)
    "TEXT_FAINT":     "#9ca3af",  # gray-400  (placeholder / footer / disabled)
    "TEXT_ON_ACCENT": "#ffffff",  # text on a colored button

    # --- Surfaces ---
    "SURFACE":        "#ffffff",  # card / dialog background
    "SURFACE_SUBTLE": "#f9fafb",  # gray-50   (hover / subtle panel)
    "SURFACE_SUNKEN": "#f3f4f6",  # gray-100  (disabled / inset)
    "INPUT_BG":       "#ffffff",  # QLineEdit / QComboBox background
    "INPUT_BG_DISABLED": "#f3f4f6",

    # --- Borders / dividers ---
    "BORDER":         "#d1d5db",  # gray-300  (input borders)
    "BORDER_SUBTLE":  "#e5e7eb",  # gray-200  (dividers / separators)

    # --- Accent (primary blue) ---
    "ACCENT":         "#2563eb",  # blue-600
    "ACCENT_HOVER":   "#1d4ed8",  # blue-700
    "ACCENT_ACTIVE":  "#1e40af",  # blue-800
    "ACCENT_DISABLED": "#93c5fd", # blue-300
    "ACCENT_TEXT":    "#2563eb",  # accent used as text (links / brand)

    # --- Status ---
    "SUCCESS":        "#059669",  # green-600
    "SUCCESS_TEXT":   "#047857",  # green-700
    "SUCCESS_BG":     "#d1fae5",  # green-100
    "WARNING":        "#f59e0b",  # amber-500
    "WARNING_TEXT":   "#b45309",  # amber-700
    "WARNING_BG":     "#fef3c7",  # amber-100
    "DANGER":         "#dc2626",  # red-600
    "DANGER_TEXT":    "#b91c1c",  # red-700
    "DANGER_BG":      "#fef2f2",  # red-50
    "INFO":           "#0891b2",  # cyan-600
    "INFO_TEXT":      "#0369a1",  # sky-700
    "INFO_BG":        "#f0f9ff",  # sky-50

    # --- Slate family (used by claims wizard chrome) ---
    "SLATE_TEXT":     "#475569",  # slate-600
    "SLATE_MUTED":    "#64748b",  # slate-500
    "SLATE_FAINT":    "#94a3b8",  # slate-400
    "SLATE_BORDER":   "#cbd5e1",  # slate-300
    "SLATE_BORDER_SUBTLE": "#e2e8f0",  # slate-200
    "SLATE_SURFACE":  "#f8fafc",  # slate-50
    "SLATE_SUNKEN":   "#f1f5f9",  # slate-100
    "SLATE_STRONG":   "#1e293b",  # slate-800
}

_DARK = {
    # --- Text ---  (lighter so it reads on dark panels)
    "TEXT_PRIMARY":   "#e5e7eb",  # gray-200
    "TEXT_STRONG":    "#f9fafb",  # gray-50
    "TEXT_MUTED":     "#9ca3af",  # gray-400
    "TEXT_FAINT":     "#6b7280",  # gray-500
    "TEXT_ON_ACCENT": "#ffffff",

    # --- Surfaces ---  (dark gray, not pure black, to layer cleanly on QGIS chrome)
    "SURFACE":        "#1f2937",  # gray-800
    "SURFACE_SUBTLE": "#283342",  # between gray-800 and gray-700
    "SURFACE_SUNKEN": "#111827",  # gray-900
    "INPUT_BG":       "#111827",  # gray-900  (inputs slightly darker than card)
    "INPUT_BG_DISABLED": "#1f2937",

    # --- Borders / dividers ---
    "BORDER":         "#4b5563",  # gray-600
    "BORDER_SUBTLE":  "#374151",  # gray-700

    # --- Accent (brighter blue for dark bg) ---
    "ACCENT":         "#3b82f6",  # blue-500
    "ACCENT_HOVER":   "#60a5fa",  # blue-400
    "ACCENT_ACTIVE":  "#2563eb",  # blue-600
    "ACCENT_DISABLED": "#1e3a8a", # blue-900
    "ACCENT_TEXT":    "#60a5fa",  # blue-400 (links readable on dark)

    # --- Status (brighter variants) ---
    "SUCCESS":        "#10b981",  # green-500
    "SUCCESS_TEXT":   "#34d399",  # green-400
    "SUCCESS_BG":     "#064e3b",  # green-900
    "WARNING":        "#fbbf24",  # amber-400
    "WARNING_TEXT":   "#fcd34d",  # amber-300
    "WARNING_BG":     "#451a03",  # amber-950
    "DANGER":         "#ef4444",  # red-500
    "DANGER_TEXT":    "#f87171",  # red-400
    "DANGER_BG":      "#450a0a",  # red-950
    "INFO":           "#22d3ee",  # cyan-400
    "INFO_TEXT":      "#67e8f9",  # cyan-300
    "INFO_BG":        "#083344",  # cyan-950

    # --- Slate family (mapped onto the dark gray ramp) ---
    "SLATE_TEXT":     "#cbd5e1",  # slate-300 (was slate-600 in light)
    "SLATE_MUTED":    "#94a3b8",  # slate-400
    "SLATE_FAINT":    "#64748b",  # slate-500
    "SLATE_BORDER":   "#475569",  # slate-600
    "SLATE_BORDER_SUBTLE": "#374151",  # gray-700
    "SLATE_SURFACE":  "#1e293b",  # slate-800
    "SLATE_SUNKEN":   "#0f172a",  # slate-900
    "SLATE_STRONG":   "#f1f5f9",  # slate-100 (was slate-800 in light)
}


class _Tokens:
    """Attribute access over the active token table (``T.TEXT_PRIMARY``)."""

    def __init__(self):
        self._dark = False
        self._table = _LIGHT
        self.refresh()

    def refresh(self):
        """Re-resolve against the currently active theme."""
        self._dark = is_dark_theme()
        self._table = _DARK if self._dark else _LIGHT
        return self

    @property
    def is_dark(self) -> bool:
        return self._dark

    def __getattr__(self, name):
        # Only reached for names not found as instance attributes.
        try:
            return object.__getattribute__(self, "_table")[name]
        except KeyError:
            raise AttributeError(
                f"theme token {name!r} is not defined; add it to _LIGHT/_DARK "
                f"in utils/theme.py"
            )


# Module-level singleton, resolved at import.
T = _Tokens()


def refresh_theme() -> _Tokens:
    """Re-resolve the shared token singleton against the active theme."""
    return T.refresh()


def current_tokens() -> dict:
    """Return a copy of the active token table (name -> hex)."""
    return dict(T._table)
