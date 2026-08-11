"""Locate libr3d_bridge and RED Redistributable libraries."""

from __future__ import annotations

import os
import platform
from pathlib import Path

from ..app_paths import package_root_from_file, runtime_exe_dirs


def bridge_names() -> tuple[str, ...]:
    system = platform.system()
    if system == "Darwin":
        return ("libr3d_bridge.dylib",)
    if system == "Windows":
        return ("libr3d_bridge.dll", "r3d_bridge.dll")
    return ("libr3d_bridge.so",)


def redistributable_marker() -> tuple[str, str]:
    """Return ``(redistributable_subdir, primary_library_filename)``."""
    system = platform.system()
    if system == "Darwin":
        return "mac", "REDR3D.dylib"
    if system == "Windows":
        return "win", "REDR3D-x64.dll"
    return "linux", "REDR3D-x64.so"


def bridge_candidates() -> list[Path]:
    """Search paths for libr3d_bridge.*"""
    names = bridge_names()
    dirs: list[Path] = []
    files: list[Path] = []

    env = os.environ.get("EXR_CONVERTER_R3D_BRIDGE", "").strip()
    if env:
        p = Path(env).expanduser()
        if p.suffix.lower() in {".dylib", ".so", ".dll"}:
            files.append(p)
        else:
            dirs.append(p)

    for exe_dir in runtime_exe_dirs():
        dirs.append(exe_dir / "r3d")
        dirs.append(exe_dir)

    # paths.py → r3d → core → src → repo root
    pkg_root = package_root_from_file(__file__, parents=4)
    dirs.extend(
        [
            pkg_root / "build" / "r3d",
            pkg_root / "native" / "r3d",
            pkg_root / "resources" / "r3d",
        ]
    )

    out: list[Path] = []
    seen: set[str] = set()

    def _add(p: Path) -> None:
        try:
            key = str(p.resolve()) if p.exists() else str(p)
        except OSError:
            key = str(p)
        if key in seen:
            return
        seen.add(key)
        out.append(p)

    for f in files:
        _add(f)
    for d in dirs:
        for name in names:
            _add(d / name)
    return out


def redistributable_candidates(bridge_path: Path | None) -> list[Path]:
    """Folders that may contain REDR3D.* (same dir as bridge after install)."""
    sub, marker = redistributable_marker()

    roots: list[Path] = []
    env = os.environ.get("EXR_CONVERTER_R3D_LIBS", "").strip()
    if env:
        roots.append(Path(env).expanduser().resolve())
    env2 = os.environ.get("R3D_SDK_LIBS", "").strip()
    if env2:
        roots.append(Path(env2).expanduser().resolve())
    sdk = os.environ.get("R3D_SDK_ROOT", "").strip()
    if sdk:
        roots.append(Path(sdk).expanduser().resolve() / "Redistributable" / sub)

    if bridge_path is not None:
        roots.append(bridge_path.parent)
        roots.append(bridge_path.parent / "redistributable")

    for exe_dir in runtime_exe_dirs():
        roots.append(exe_dir / "r3d")
        roots.append(exe_dir)

    pkg_root = package_root_from_file(__file__, parents=4)
    roots.append(pkg_root / "build" / "r3d" / "redistributable")
    roots.append(pkg_root / "resources" / "r3d")
    for name in ("R3DSDKv9_2_1", "R3DSDK"):
        roots.append(pkg_root / name / "Redistributable" / sub)

    out: list[Path] = []
    seen: set[Path] = set()
    for r in roots:
        try:
            rp = r.resolve()
        except OSError:
            continue
        if rp in seen:
            continue
        seen.add(rp)
        if (rp / marker).is_file() or rp.is_dir():
            out.append(rp)
    return out


def find_redistributable_dir(bridge_path: Path | None) -> Path | None:
    """Return the first redistributable folder that contains REDR3D.*."""
    _sub, marker = redistributable_marker()
    for cand in redistributable_candidates(bridge_path):
        if (cand / marker).is_file():
            return cand
        if any(cand.glob("REDR3D*")):
            return cand
    return None
