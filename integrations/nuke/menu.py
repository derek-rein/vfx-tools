"""Nuke menu registration for EXR Converter.

Copy to ~/.nuke/exr_converter_menu.py (and import from ~/.nuke/menu.py),
or place on NUKE_PATH as menu.py next to exr_converter_nuke.py.

See docs/nuke.md.
"""

from __future__ import annotations

import nuke

try:
    import exr_converter_nuke as exr_converter_nuke
except ImportError:
    # Same folder load when this file is named menu.py on NUKE_PATH
    import os
    import sys

    _here = os.path.dirname(os.path.abspath(__file__))
    if _here not in sys.path:
        sys.path.insert(0, _here)
    import exr_converter_nuke as exr_converter_nuke  # type: ignore

# Keep module bound so Nuke menu callbacks resolve the name.
assert exr_converter_nuke is not None


def _register() -> None:
    menubar = nuke.menu("Nuke")
    m = menubar.addMenu("EXR Converter")
    m.addCommand(
        "Open selected Read…",
        "exr_converter_nuke.open_selected_read()",
        shortcut="",
    )
    m.addCommand(
        "Open EXR Converter only…",
        "exr_converter_nuke.launch()",
    )

    # Nodes menu (discoverability next to other tools)
    try:
        nuke.menu("Nodes").addCommand(
            "EXR Converter/Open selected Read…",
            "exr_converter_nuke.open_selected_read()",
        )
    except Exception:
        pass


_register()
