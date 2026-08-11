"""Shared helpers for packaging scripts (ASCII logs, macOS junk filters)."""

from __future__ import annotations

import sys
from pathlib import Path


def safe_print(*args: object, file=None, **kwargs) -> None:
    """Print that never crashes on cp1252 Windows CI consoles."""
    out = file if file is not None else sys.stdout
    try:
        print(*args, file=out, **kwargs)
    except UnicodeEncodeError:
        text = " ".join(str(a) for a in args)
        enc = getattr(out, "encoding", None) or "utf-8"
        raw = text.encode(enc, errors="replace").decode(enc, errors="replace")
        print(raw, file=out, **kwargs)


def is_macos_junk_name(name: str) -> bool:
    return name.startswith("._") or name in {".DS_Store", "Thumbs.db"}


def ignore_macos_junk(_dir: str, names: list[str]) -> set[str]:
    """``shutil.copytree`` ignore callback for AppleDouble / Finder junk."""
    return {n for n in names if is_macos_junk_name(n)}


def purge_macos_junk(root: Path) -> None:
    """Delete AppleDouble / .DS_Store files under *root* (in place)."""
    if not root.is_dir():
        return
    for p in root.rglob("*"):
        if p.is_file() and is_macos_junk_name(p.name):
            p.unlink(missing_ok=True)
