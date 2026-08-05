"""Load the compiled QSS stylesheet from Qt resources with variable substitution.

The .qss file uses ``@VARNAME`` placeholders that are replaced at load time
from the palette dict below.  This is the standard workaround for QSS not
supporting CSS custom properties.
"""

from __future__ import annotations

import re

from PySide6.QtCore import QFile, QIODevice

# ---------------------------------------------------------------------------
# Nuke-inspired palette  (edit colours here — they propagate everywhere)
# ---------------------------------------------------------------------------
_PALETTE: dict[str, str] = {
    "BG": "#282828",
    "BG_ALT": "#303030",
    "BG_INPUT": "#1e1e1e",
    "BG_BTN": "#3a3a3a",
    "BG_BTN_HOVER": "#454545",
    "BG_BTN_PRESSED": "#505050",
    "FG": "#d4d4d4",
    "FG_DIM": "#888888",
    "FG_DISABLED": "#606060",
    "BORDER": "#3c3c3c",
    "ACCENT": "#c87828",  # Nuke orange
    "ACCENT_HOVER": "#da8a30",
    "ACCENT_DARK": "#a06020",
    "SEL_FG": "#ffffff",
    "ERROR": "#cc4444",
    "SUCCESS": "#55aa55",
    "SCROLLBAR": "#505050",
    "SCROLLBAR_HOVER": "#606060",
}

_VAR_RE = re.compile(r"@([A-Z_]+)")

# ---------------------------------------------------------------------------
# Inline style snippets for dynamic status labels (not in the .qss)
# ---------------------------------------------------------------------------
STATUS_OK = f"color: {_PALETTE['SUCCESS']}; font-size: 11px; padding: 2px 0;"
STATUS_ERR = f"color: {_PALETTE['ERROR']}; font-size: 11px; padding: 2px 0;"
STATUS_DIM = f"font-size: 11px; padding: 2px 0; color: {_PALETTE['FG_DIM']};"
HINT_STYLE = f"font-size: 10px; color: {_PALETTE['FG_DIM']};"
DESC_STYLE = f"color: {_PALETTE['FG_DIM']}; font-size: 11px; padding: 4px 0;"

_cache: str | None = None


def load_stylesheet() -> str:
    """Read :/style.qss, substitute ``@VAR`` tokens, and return CSS text.

    The result is cached after the first call.
    """
    global _cache  # noqa: PLW0603
    if _cache is not None:
        return _cache
    f = QFile(":/style.qss")
    if not f.open(QIODevice.OpenModeFlag.ReadOnly | QIODevice.OpenModeFlag.Text):
        return ""
    # QByteArray → bytes: use .data() for a clean buffer view on all PySide versions.
    raw = bytes(f.readAll().data()).decode("utf-8")
    f.close()
    _cache = _VAR_RE.sub(lambda m: _PALETTE.get(m.group(1), m.group(0)), raw)
    return _cache
