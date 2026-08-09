"""Named convert presets (versioned JSON under AppData).

Presets are **document recipes** (color, codec, scale, …) — not window
geometry, not player prefs, not I/O paths. Those live in QSettings via
:mod:`app_settings`.

Schema
------
``schema_version`` (int) + flat keys for backwards compatibility with
pre-version files produced by :meth:`MainWindow.snapshot_state`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from PySide6.QtCore import QStandardPaths

# Bump when the preset payload shape changes (add migration in normalize_preset).
SCHEMA_VERSION = 1

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


def normalize_preset(data: dict[str, Any]) -> dict[str, Any]:
    """Coerce a raw preset dict to the current schema.

    Unknown keys are preserved (forward compatibility). Missing
    ``schema_version`` is treated as legacy v0 (same flat keys).
    """
    if not isinstance(data, dict):
        raise ValueError("Preset must be a JSON object")
    out = dict(data)
    ver = out.get("schema_version")
    try:
        ver_i = int(ver) if ver is not None else 0
    except (TypeError, ValueError):
        ver_i = 0
    if ver_i > SCHEMA_VERSION:
        # Newer file — keep payload; caller may ignore unknown fields.
        out["schema_version"] = ver_i
        return out
    out["schema_version"] = SCHEMA_VERSION
    out.setdefault("kind", "convert_preset")
    # Drop accidental path contamination if old code ever saved them.
    out.pop("input", None)
    out.pop("output", None)
    out.pop("v2e_input", None)
    out.pop("v2e_output", None)
    out.pop("e2v_input", None)
    out.pop("e2v_output", None)
    return out


def save_preset(name: str, state: dict) -> Path:
    """Write a versioned preset. *state* should not include I/O paths."""
    path = _preset_path(name)
    payload = normalize_preset(dict(state))
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


def load_preset(name: str) -> dict:
    """Load and normalize a preset. Raises on missing file / bad JSON."""
    path = _preset_path(name)
    if not path.is_file():
        raise FileNotFoundError(f"Preset not found: {name}")
    raw = json.loads(path.read_text(encoding="utf-8"))
    return normalize_preset(raw)


def delete_preset(name: str) -> None:
    path = _preset_path(name)
    if path.exists():
        path.unlink()


__all__ = [
    "SCHEMA_VERSION",
    "delete_preset",
    "list_presets",
    "load_preset",
    "normalize_preset",
    "save_preset",
    "validate_preset_name",
]
