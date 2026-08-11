"""Shared discovery of app roots (source checkout vs frozen / Nuitka binary)."""

from __future__ import annotations

import sys
from pathlib import Path


def is_frozen_app() -> bool:
    """True inside a Nuitka / PyInstaller-style binary (not a source venv run)."""
    if getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS"):
        return True
    # Nuitka marks compiled modules; caller modules may not share globals.
    return False


def package_root_from_file(file_path: str | Path, *, parents: int = 2) -> Path:
    """Repo / install root from a module ``__file__`` (``parents=2`` → src/core → root)."""
    try:
        p = Path(file_path).resolve()
        for _ in range(max(0, parents)):
            p = p.parent
        return p
    except OSError:
        return Path.cwd()


def runtime_exe_dirs() -> list[Path]:
    """Directories that may sit next to the real application binary.

    Always consider ``sys.executable`` / ``argv[0]`` — Nuitka standalone often
    does **not** set ``sys.frozen``, so frozen-only checks miss private app
    data folders (e.g. ``Contents/MacOS/r3d/``).
    """
    dirs: list[Path] = []
    for raw in (sys.executable, sys.argv[0] if sys.argv else ""):
        if not raw:
            continue
        try:
            p = Path(raw).resolve()
        except OSError:
            continue
        if p.is_file():
            dirs.append(p.parent)
        elif p.is_dir():
            dirs.append(p)
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        dirs.append(Path(meipass))
    # macOS .app: private data often under Contents/MacOS or Contents/Resources.
    for d in list(dirs):
        if d.name == "MacOS" and d.parent.name == "Contents":
            dirs.append(d.parent / "Resources")
        elif (d / "MacOS").is_dir():
            dirs.append(d / "MacOS")
            dirs.append(d / "Resources")
    # Deduplicate while preserving order.
    out: list[Path] = []
    seen: set[str] = set()
    for d in dirs:
        try:
            key = str(d.resolve())
        except OSError:
            key = str(d)
        if key in seen:
            continue
        seen.add(key)
        out.append(d)
    return out
