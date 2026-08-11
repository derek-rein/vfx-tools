#!/usr/bin/env python3
"""Build the optional R3D SDK C ABI bridge shared library.

Requires a local copy of the official RED R3D SDK (headers + static lib +
Redistributable dynamic libraries). The SDK is proprietary — do not commit it.

Usage:
  R3D_SDK_ROOT=/path/to/R3DSDKv9_2_1 python3 scripts/build_r3d_bridge.py
  # or place the SDK at ./R3DSDKv9_2_1 and run:
  python3 scripts/build_r3d_bridge.py

Outputs:
  build/r3d/libr3d_bridge.{dylib,so,dll}
  build/r3d/redistributable/   (copied RED dynamic libs for runtime)

See docs/r3d.md for license and packaging notes.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from packaging_util import ignore_macos_junk, is_macos_junk_name, safe_print  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "native" / "r3d" / "r3d_bridge.cpp"
HDR_DIR = ROOT / "native" / "r3d"
OUT_DIR = ROOT / "build" / "r3d"


def _find_sdk(explicit: str | None) -> Path:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit).expanduser().resolve())
    env = os.environ.get("R3D_SDK_ROOT", "").strip()
    if env:
        candidates.append(Path(env).expanduser().resolve())
    # Private stash (recommended local layout) + CI fetch cache.
    candidates.append((Path.home() / "code" / "r3d-sdk-private" / "R3DSDKv9_2_1").resolve())
    candidates.append((ROOT / ".r3d-sdk" / "R3DSDKv9_2_1").resolve())
    candidates.append((ROOT / ".r3d-sdk").resolve())
    # Common local drop locations (gitignored — never commit the SDK).
    for name in ("R3DSDKv9_2_1", "R3DSDK", "r3d_sdk"):
        candidates.append((ROOT / name).resolve())
        candidates.append((Path.home() / "sdk" / name).resolve())
        candidates.append((Path.home() / "code" / "r3d-sdk-private" / name).resolve())

    for c in candidates:
        if (c / "Include" / "R3DSDK.h").is_file():
            return c
    raise SystemExit(
        "R3D SDK not found. Set R3D_SDK_ROOT, or place the SDK at "
        "~/code/r3d-sdk-private/R3DSDKv9_2_1, or run: "
        "python3 scripts/fetch_r3d_sdk.py  (private CI feed). "
        "See docs/r3d.md."
    )


def _platform_bits() -> tuple[str, str, str, list[str]]:
    """Return (lib_subdir, redistrib_subdir, shared_ext, extra_link_args)."""
    system = platform.system()
    machine = platform.machine().lower()
    if system == "Darwin":
        # Universal static lib covers arm64 + x86_64.
        return (
            "mac64",
            "mac",
            "dylib",
            [
                "-framework",
                "Foundation",
                "-framework",
                "CoreFoundation",
                "-framework",
                "IOKit",
                "-framework",
                "Metal",
                "-framework",
                "AppKit",
                "-lc++",
            ],
        )
    if system == "Linux":
        return "linux64", "linux", "so", ["-ldl", "-lpthread", "-luuid", "-lstdc++"]
    if system == "Windows":
        # Prefer VS2017 MD release static lib (compatible with VS2019+).
        return "win64", "win", "dll", []
    raise SystemExit(f"Unsupported platform: {system} {machine}")


def _static_lib(sdk: Path, lib_subdir: str) -> Path:
    system = platform.system()
    if system == "Darwin":
        p = sdk / "Lib" / lib_subdir / "libR3DSDK-libcpp.a"
        if p.is_file():
            return p
    elif system == "Linux":
        for name in ("libR3DSDKPIC-cpp11.a", "libR3DSDK-cpp11.a", "libR3DSDKPIC.a", "libR3DSDK.a"):
            p = sdk / "Lib" / lib_subdir / name
            if p.is_file():
                return p
    else:
        # Windows: pick 2017 MD release.
        for name in (
            "R3DSDK-2017MD.lib",
            "R3DSDK-2015MD.lib",
            "R3DSDK-2013MD.lib",
        ):
            p = sdk / "Lib" / lib_subdir / name
            if p.is_file():
                return p
    raise SystemExit(f"No suitable static library under {sdk / 'Lib' / lib_subdir}")


def build(sdk: Path, out_dir: Path, verbose: bool) -> Path:
    lib_subdir, redistrib_subdir, ext, extra = _platform_bits()
    static = _static_lib(sdk, lib_subdir)
    redistrib_src = sdk / "Redistributable" / redistrib_subdir
    if not redistrib_src.is_dir():
        raise SystemExit(f"Missing Redistributable folder: {redistrib_src}")

    out_dir.mkdir(parents=True, exist_ok=True)
    out_lib = out_dir / f"libr3d_bridge.{ext}"

    include = sdk / "Include"
    if not SRC.is_file():
        raise SystemExit(f"Missing bridge source: {SRC}")

    system = platform.system()
    if system == "Windows":
        # Expect cl.exe on PATH (Developer Command Prompt / msvc-dev-cmd).
        # Must match the RED static lib CRT: R3DSDK-*-MD.lib → /MD (DLL runtime).
        # Without explicit /MD, some CI images default to /MT and LNK2038.
        # Prefer /Fo + /Fe into out_dir so intermediates stay out of the source tree.
        obj = out_dir / "r3d_bridge.obj"
        cmd = [
            "cl.exe",
            "/nologo",
            "/O2",
            "/MD",
            "/EHsc",
            "/std:c++17",
            f"/I{include}",
            f"/I{HDR_DIR}",
            "/DR3D_BRIDGE_EXPORTS",
            "/LD",
            str(SRC),
            str(static),
            f"/Fo{obj}",
            f"/Fe{out_lib}",
        ]
    else:
        cxx = os.environ.get("CXX", "c++")
        cmd = [
            cxx,
            "-std=c++17",
            "-O2",
            "-fPIC",
            "-shared",
            f"-I{include}",
            f"-I{HDR_DIR}",
            "-DR3D_BRIDGE_EXPORTS",
            str(SRC),
            str(static),
            "-o",
            str(out_lib),
            *extra,
        ]
        if system == "Darwin":
            # Allow loading RED dylibs from same folder as the bridge at runtime.
            cmd.extend(
                [
                    "-Wl,-rpath,@loader_path",
                    "-Wl,-rpath,@loader_path/redistributable",
                ]
            )
        elif system == "Linux":
            cmd.extend(["-Wl,-rpath,$ORIGIN", "-Wl,-rpath,$ORIGIN/redistributable"])

    if verbose:
        print(" ".join(cmd), file=sys.stderr)

    subprocess.check_call(cmd)

    # Copy only Redistributable dynamic libraries (allowed by RED license).
    # Skip macOS AppleDouble (._*) / .DS_Store junk that can ride along in tarballs
    # and break Linux `strip` in packaging.
    dest_redist = out_dir / "redistributable"
    if dest_redist.exists():
        shutil.rmtree(dest_redist)

    shutil.copytree(redistrib_src, dest_redist, ignore=ignore_macos_junk)
    # Also drop any junk already nested if ignore missed something.
    for p in dest_redist.rglob("*"):
        if p.is_file() and is_macos_junk_name(p.name):
            p.unlink(missing_ok=True)

    # ASCII-only logs: Windows CI runners often use cp1252 and choke on arrows.
    safe_print(f"Built {out_lib}")
    safe_print(f"Redistributables -> {dest_redist}")
    safe_print(f"SDK version root: {sdk}")
    return out_lib


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--sdk",
        default=None,
        help="Path to unpacked R3D SDK root (or set R3D_SDK_ROOT)",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=OUT_DIR,
        help=f"Output directory (default: {OUT_DIR})",
    )
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    sdk = _find_sdk(args.sdk)
    build(sdk, args.out.resolve(), args.verbose)


if __name__ == "__main__":
    main()
