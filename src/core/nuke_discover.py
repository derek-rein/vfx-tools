"""Discover local Foundry Nuke installs and their on-disk OCIO configs.

We never redistribute Nuke files — we only *reference* configs that already
exist on the user's machine (under their licensed Nuke installation).
"""

from __future__ import annotations

import os
import platform
import re
from dataclasses import dataclass
from pathlib import Path

# Source-combo / settings key prefix. Full key: ``nuke:<version>:<stem>``
NUKE_SOURCE_PREFIX = "nuke:"

# Prefer modern Foundry ACES studio, then CG, then legacy nuke-default.
_KIND_RANK = {
    "studio_aces2": 0,
    "studio_aces1": 1,
    "cg_aces2": 2,
    "cg_aces1": 3,
    "nuke_default": 4,
    "other": 5,
}


@dataclass(frozen=True, slots=True)
class NukeOcioConfig:
    """One OCIO config found under a Nuke install."""

    key: str
    version: str  # e.g. "17.0v3"
    label: str
    path: Path
    kind: str

    @property
    def path_str(self) -> str:
        return str(self.path)


def _version_sort_key(version: str) -> tuple:
    """Sort Nuke version strings newest-first (17.0v3 > 16.0v5 > 15.1v2)."""
    m = re.match(r"(\d+)\.(\d+)v(\d+)", version, re.I)
    if m:
        return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    nums = [int(x) for x in re.findall(r"\d+", version)]
    return tuple(nums) if nums else (0,)


def _classify(name: str) -> tuple[str, str]:
    """Return (kind, short_label) from a config file/folder name."""
    lower = name.lower()
    if lower == "nuke-default" or lower == "config.ocio":
        return "nuke_default", "nuke-default (legacy)"
    if "studio" in lower and "aces-v2" in lower:
        return "studio_aces2", "Studio ACES 2.0"
    if "studio" in lower and "aces-v1" in lower:
        return "studio_aces1", "Studio ACES 1.3"
    if "studio" in lower:
        return "studio_aces1", "Studio ACES"
    if re.search(r"(^|[_-])cg([_-]|$)", lower) and "aces-v2" in lower:
        return "cg_aces2", "CG ACES 2.0"
    if re.search(r"(^|[_-])cg([_-]|$)", lower) and "aces-v1" in lower:
        return "cg_aces1", "CG ACES 1.3"
    if re.search(r"(^|[_-])cg([_-]|$)", lower):
        return "cg_aces1", "CG ACES"
    # Strip Foundry prefixes for a readable leftover name.
    pretty = re.sub(r"^fn-nuke[_-]?", "", name, flags=re.I)
    pretty = re.sub(r"\.ocio$", "", pretty, flags=re.I)
    return "other", pretty or name


def _make_key(version: str, stem: str) -> str:
    return f"{NUKE_SOURCE_PREFIX}{version}:{stem}"


def _configs_dir_candidates(root: Path) -> list[Path]:
    """Possible OCIOConfigs/configs directories under a Nuke product root."""
    out: list[Path] = []
    # macOS .app bundle
    for app in sorted(root.glob("Nuke*.app")):
        out.append(app / "Contents" / "Resources" / "OCIOConfigs" / "configs")
    # Flat layouts (Windows / Linux / some macOS unpacks)
    out.append(root / "Resources" / "OCIOConfigs" / "configs")
    out.append(root / "plugins" / "OCIOConfigs" / "configs")
    out.append(root / "OCIOConfigs" / "configs")
    # Sometimes the product root *is* the .app
    if root.name.endswith(".app"):
        out.append(root / "Contents" / "Resources" / "OCIOConfigs" / "configs")
    return out


def _parse_version_from_path(path: Path) -> str:
    for part in path.parts:
        m = re.match(r"Nuke(\d+\.\d+v\d+)", part, re.I)
        if m:
            return m.group(1)
        m = re.match(r"Nuke(\d+\.\d+)", part, re.I)
        if m:
            return m.group(1)
    return path.name


def _iter_install_roots() -> list[Path]:
    """Candidate Nuke install roots to scan (may not all exist)."""
    roots: list[Path] = []
    system = platform.system()

    env_roots = os.environ.get("EXR_CONVERTER_NUKE_ROOTS", "")
    for part in env_roots.split(os.pathsep):
        part = part.strip()
        if part:
            roots.append(Path(part).expanduser())

    # Common facility override
    nuke_path = os.environ.get("NUKE_PATH", "")
    if nuke_path:
        # NUKE_PATH is plugin path(s); walk up a few levels looking for installs.
        for part in nuke_path.split(os.pathsep):
            p = Path(part).expanduser()
            for _ in range(4):
                if p.name.lower().startswith("nuke") or (p / "Nuke").exists():
                    roots.append(p)
                p = p.parent

    if system == "Darwin":
        apps = Path("/Applications")
        if apps.is_dir():
            roots.extend(sorted(apps.glob("Nuke*")))
            # Nested: /Applications/Nuke17.0v3/Nuke17.0v3.app already covered
            # by glob Nuke*
        home_apps = Path.home() / "Applications"
        if home_apps.is_dir():
            roots.extend(sorted(home_apps.glob("Nuke*")))
    elif system == "Windows":
        for base_env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMW6432"):
            base = os.environ.get(base_env)
            if not base:
                continue
            bp = Path(base)
            if bp.is_dir():
                roots.extend(sorted(bp.glob("Nuke*")))
        # Local AppData installs (rarer)
        local = os.environ.get("LOCALAPPDATA")
        if local:
            roots.extend(sorted(Path(local).glob("Nuke*")))
    else:
        for base in (
            Path("/usr/local"),
            Path("/opt"),
            Path("/usr"),
            Path.home(),
            Path.home() / "Nuke",
            Path.home() / "opt",
        ):
            if base.is_dir():
                roots.extend(sorted(base.glob("Nuke*")))

    # De-dupe while preserving order
    seen: set[str] = set()
    unique: list[Path] = []
    for r in roots:
        try:
            key = str(r.resolve()) if r.exists() else str(r)
        except OSError:
            key = str(r)
        if key in seen:
            continue
        seen.add(key)
        unique.append(r)
    return unique


def _collect_configs_from_dir(configs_dir: Path, version: str) -> list[NukeOcioConfig]:
    found: list[NukeOcioConfig] = []
    if not configs_dir.is_dir():
        return found

    for entry in sorted(configs_dir.iterdir()):
        if entry.is_file() and entry.suffix.lower() == ".ocio":
            stem = entry.stem
            kind, short = _classify(entry.name)
            key = _make_key(version, stem)
            label = f"Nuke {version} · {short}"
            found.append(
                NukeOcioConfig(key=key, version=version, label=label, path=entry, kind=kind)
            )
        elif entry.is_dir():
            # e.g. nuke-default/config.ocio
            cfg = entry / "config.ocio"
            if cfg.is_file():
                kind, short = _classify(entry.name)
                key = _make_key(version, entry.name)
                label = f"Nuke {version} · {short}"
                found.append(
                    NukeOcioConfig(key=key, version=version, label=label, path=cfg, kind=kind)
                )
    return found


def find_nuke_ocio_configs() -> list[NukeOcioConfig]:
    """Scan the system for Nuke OCIO configs. Empty list if Nuke is not installed.

    Results are sorted: newest Nuke version first, then preferred config kind
    (Studio ACES 2.0 → CG → nuke-default).
    """
    results: list[NukeOcioConfig] = []
    seen_paths: set[str] = set()

    for root in _iter_install_roots():
        if not root.exists():
            continue
        version = _parse_version_from_path(root)
        for cdir in _configs_dir_candidates(root):
            for item in _collect_configs_from_dir(cdir, version):
                try:
                    resolved = str(item.path.resolve())
                except OSError:
                    resolved = str(item.path)
                if resolved in seen_paths:
                    continue
                if not item.path.is_file():
                    continue
                seen_paths.add(resolved)
                results.append(item)

    results.sort(
        key=lambda c: (
            # newest version first
            tuple(-x for x in _version_sort_key(c.version)),
            _KIND_RANK.get(c.kind, 99),
            c.label.lower(),
        )
    )
    return results


def is_nuke_source_key(key: str) -> bool:
    return bool(key) and key.startswith(NUKE_SOURCE_PREFIX)


def resolve_nuke_config_path(key: str) -> Path | None:
    """Resolve a ``nuke:…`` source key to an on-disk ``.ocio`` path, or None."""
    if not is_nuke_source_key(key):
        return None
    for cfg in find_nuke_ocio_configs():
        if cfg.key == key:
            return cfg.path if cfg.path.is_file() else None
    return None


def nuke_source_label(key: str) -> str:
    for cfg in find_nuke_ocio_configs():
        if cfg.key == key:
            return cfg.label
    return key
