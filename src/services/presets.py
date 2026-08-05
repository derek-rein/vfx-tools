from __future__ import annotations

import json
import re
from pathlib import Path

from PySide6.QtCore import QStandardPaths

# Safe preset filenames only — no path separators or traversal.
_PRESET_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,63}$")


def _preset_dir() -> Path:
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    d = Path(base) / "presets"
    d.mkdir(parents=True, exist_ok=True)
    return d


def validate_preset_name(name: str) -> str:
    """Return a cleaned preset name or raise ``ValueError``."""
    cleaned = name.strip()
    if not cleaned or not _PRESET_NAME_RE.match(cleaned):
        raise ValueError(
            "Preset name must be 1–64 chars of letters, digits, spaces, "
            "dots, underscores, or hyphens (no path separators)."
        )
    if ".." in cleaned:
        raise ValueError("Preset name must not contain '..'")
    return cleaned


def _preset_path(name: str) -> Path:
    safe = validate_preset_name(name)
    root = _preset_dir().resolve()
    path = (root / f"{safe}.json").resolve()
    if path.parent != root:
        raise ValueError("Invalid preset path")
    return path


def list_presets() -> list[str]:
    d = _preset_dir()
    return sorted(p.stem for p in d.glob("*.json") if _PRESET_NAME_RE.match(p.stem))


def save_preset(name: str, state: dict) -> Path:
    path = _preset_path(name)
    path.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
    return path


def load_preset(name: str) -> dict:
    path = _preset_path(name)
    return json.loads(path.read_text(encoding="utf-8"))


def delete_preset(name: str) -> None:
    path = _preset_path(name)
    if path.exists():
        path.unlink()
