#!/usr/bin/env python3
"""Copy optional R3D bridge + RED Redistributable libs into a Nuitka dist.

Layout written (private app directory — RED license)::

  <bundle>/r3d/libr3d_bridge.{dylib,so,dll}
  <bundle>/r3d/REDR3D.* …
  <bundle>/r3d/… (other redistributables)

On macOS app bundles, *bundle* is ``Contents/MacOS`` (next to the executable).

Usage::

  python3 scripts/install_r3d_into_bundle.py "dist/EXR Converter.app"
  python3 scripts/install_r3d_into_bundle.py dist/main.dist

If ``build/r3d/libr3d_bridge.*`` is missing, exits 0 with a skip message
(optional feature).
"""

from __future__ import annotations

import argparse
import platform
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build" / "r3d"


def _bridge_name() -> str:
    system = platform.system()
    if system == "Darwin":
        return "libr3d_bridge.dylib"
    if system == "Windows":
        return "libr3d_bridge.dll"
    return "libr3d_bridge.so"


def _macos_exec_dir(app: Path) -> Path:
    mac_os = app / "Contents" / "MacOS"
    if mac_os.is_dir():
        return mac_os
    return app


def resolve_install_root(target: Path) -> Path:
    target = target.resolve()
    if target.suffix == ".app" or target.name.endswith(".app"):
        return _macos_exec_dir(target)
    return target


def install(target: Path, build_dir: Path = BUILD) -> Path | None:
    bridge = build_dir / _bridge_name()
    # Windows may also produce r3d_bridge.dll
    if not bridge.is_file():
        alt = build_dir / "r3d_bridge.dll"
        if alt.is_file():
            bridge = alt
    if not bridge.is_file():
        print(f"skip: no bridge at {bridge} (R3D optional)", file=sys.stderr)
        return None

    redist = build_dir / "redistributable"
    if not redist.is_dir():
        print(f"ERROR: missing redistributable dir {redist}", file=sys.stderr)
        raise SystemExit(2)

    root = resolve_install_root(target)
    dest = root / "r3d"
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)

    shutil.copy2(bridge, dest / bridge.name)
    for item in redist.iterdir():
        if item.is_file():
            shutil.copy2(item, dest / item.name)
        elif item.is_dir():
            shutil.copytree(item, dest / item.name)

    print(f"Installed R3D runtime → {dest}")
    return dest


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("bundle", type=Path, help="App bundle, main.dist, or install root")
    ap.add_argument(
        "--build-dir",
        type=Path,
        default=BUILD,
        help=f"bridge build output (default {BUILD})",
    )
    args = ap.parse_args()
    if not args.bundle.exists():
        raise SystemExit(f"bundle path not found: {args.bundle}")
    install(args.bundle, args.build_dir.resolve())


if __name__ == "__main__":
    main()
