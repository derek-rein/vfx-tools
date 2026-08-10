#!/usr/bin/env python3
"""Repair OpenColorIO linkage inside a Nuitka standalone / app bundle.

Nuitka often collides the two OCIO shared libraries we ship:

* ``PyOpenColorIO`` (opencolorio wheel) → **2.5.x**  — required for the
  bundled ACES Studio v4 config (profile 2.5) and modern Foundry Nuke configs.
* ``OpenImageIO/.dylibs/libOpenColorIO.2.4.0`` (oiio-python) → **2.4** —
  used only by OIIO.

Nuitka rewrites ``PyOpenColorIO.so`` to load the 2.4 dylib at
``@executable_path`` and drops the 2.5 library. The GUI can still show a green
status (builtin fallback) while convert fails on the real 2.5 config.

This script:

1. Requires a build-env ``PyOpenColorIO`` whose runtime is >= 2.5.
2. Copies the correct OCIO shared lib next to ``PyOpenColorIO.so`` / ``.pyd``.
3. Relinks the extension to that library (``@loader_path`` / ``$ORIGIN``).
4. Leaves OIIO on 2.4 — it does not need profile-2.5 configs.

Usage::

    uv run python scripts/fix_bundle_ocio.py "dist/EXR Converter.app"
    uv run python scripts/fix_bundle_ocio.py dist/main.dist
"""

from __future__ import annotations

import argparse
import platform
import shutil
import subprocess
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from oiio_ocio24 import OIIO_OCIO24_DLL, materialize_ocio24_dll, read_ocio24_dll_bytes  # noqa: E402

REQUIRED = (2, 5, 0)


def _parse_version(text: str) -> tuple[int, ...]:
    import re

    nums = re.findall(r"\d+", text)
    return tuple(int(n) for n in nums[:3]) if nums else (0,)


def _site_pyocio_dir() -> Path:
    import PyOpenColorIO as ocio

    ver = ocio.GetVersion()
    if _parse_version(ver) < REQUIRED:
        raise SystemExit(
            f"Build-env OpenColorIO is {ver!r}; need >= 2.5.0 before fixing a "
            f"bundle. Run: make ensure-ocio"
        )
    return Path(ocio.__file__).resolve().parent


def _find_extension(root: Path) -> Path:
    for name in ("PyOpenColorIO.so", "PyOpenColorIO.pyd"):
        hits = list(root.rglob(name))
        if hits:
            # Prefer the one under a PyOpenColorIO package dir.
            hits.sort(key=lambda p: (0 if p.parent.name == "PyOpenColorIO" else 1, str(p)))
            return hits[0]
    raise SystemExit(f"No PyOpenColorIO.so/.pyd under {root}")


def _source_lib(site_dir: Path) -> Path:
    system = platform.system()
    candidates: list[Path] = []
    if system == "Darwin":
        candidates = [site_dir / "libOpenColorIO.dylib"]
    elif system == "Windows":
        candidates = [
            site_dir / "bin" / "OpenColorIO_2_5.dll",
            site_dir / "OpenColorIO_2_5.dll",
            site_dir / "bin" / "OpenColorIO.dll",
            site_dir / "OpenColorIO.dll",
        ]
    else:
        candidates = [
            site_dir / "libOpenColorIO.so",
            *sorted(site_dir.glob("libOpenColorIO.so.*")),
        ]
    for c in candidates:
        if c.is_file():
            return c
    raise SystemExit(
        f"Could not find OCIO 2.5 shared library next to {site_dir}. "
        f"Tried: {', '.join(str(c) for c in candidates)}"
    )


def _source_extension(site_dir: Path) -> Path:
    """Healthy build-env PyOpenColorIO extension (2.5 ABI)."""
    for name in ("PyOpenColorIO.so", "PyOpenColorIO.pyd"):
        p = site_dir / name
        if p.is_file():
            return p
    raise SystemExit(f"No PyOpenColorIO extension in {site_dir}")


def _replace_extension(dest_ext: Path, src_ext: Path) -> Path:
    """Overwrite the bundled extension with the build-env 2.5 module.

    Nuitka has been observed to ship a *different, smaller* ``.so`` that
    references the OpenColorIO **v2_4** C++ ABI and links oiio’s 2.4 dylib.
    Only swapping the dylib then fails with missing symbols; the extension
    itself must be restored too.
    """
    shutil.copy2(src_ext, dest_ext)
    print(
        f"fix_bundle_ocio: restored {dest_ext.name} from build-env ({src_ext.stat().st_size} bytes)"
    )
    return dest_ext


def _macos_fix(ext: Path, src_ext: Path, src_lib: Path) -> None:
    ext = _replace_extension(ext, src_ext)
    dest_lib = ext.parent / "libOpenColorIO.dylib"
    shutil.copy2(src_lib, dest_lib)
    # Stable install name relative to the extension module.
    subprocess.check_call(
        ["install_name_tool", "-id", "@loader_path/libOpenColorIO.dylib", str(dest_lib)]
    )
    # Ensure the extension loads the dylib sitting next to it (Nuitka may have
    # rewritten the original to @executable_path/libOpenColorIO.2.4.0.dylib).
    out = subprocess.check_output(["otool", "-L", str(ext)], text=True)
    for line in out.splitlines()[1:]:
        dep = line.strip().split(" ", 1)[0]
        if "OpenColorIO" in dep or "libOpenColorIO" in dep:
            if dep == "@loader_path/libOpenColorIO.dylib":
                continue
            subprocess.check_call(
                [
                    "install_name_tool",
                    "-change",
                    dep,
                    "@loader_path/libOpenColorIO.dylib",
                    str(ext),
                ]
            )
    out2 = subprocess.check_output(["otool", "-L", str(ext)], text=True)
    if "libOpenColorIO.2.4" in out2:
        raise SystemExit(
            f"Still linked to 2.4 after rewrite:\n{out2}\n"
            "install_name_tool failed to retarget PyOpenColorIO.so"
        )
    if "@loader_path/libOpenColorIO.dylib" not in out2:
        raise SystemExit(f"Expected @loader_path/libOpenColorIO.dylib on {ext}:\n{out2}")
    # ABI sanity: restored module must reference v2_5 symbols, not v2_4.
    undef = subprocess.check_output(["nm", "-u", str(ext)], text=True, stderr=subprocess.DEVNULL)
    if "OpenColorIO_v2_4" in undef and "OpenColorIO_v2_5" not in undef:
        raise SystemExit(
            f"{ext} still has only OpenColorIO_v2_4 undefined symbols — "
            f"build-env extension was not restored correctly"
        )
    print(f"fix_bundle_ocio: macOS - {ext.name} -> @loader_path/libOpenColorIO.dylib")
    print(f"fix_bundle_ocio: copied {dest_lib} ({dest_lib.stat().st_size} bytes)")


def _linux_fix(ext: Path, src_ext: Path, src_lib: Path) -> None:
    ext = _replace_extension(ext, src_ext)
    plain = ext.parent / "libOpenColorIO.so"
    shutil.copy2(src_lib, plain)
    if src_lib.name != plain.name:
        # Keep versioned soname copy too when present
        shutil.copy2(src_lib, ext.parent / src_lib.name)

    try:
        subprocess.check_call(
            ["patchelf", "--set-rpath", "$ORIGIN", str(ext)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        print(
            "fix_bundle_ocio: warning — patchelf not found; "
            "relying on Nuitka rpath / LD_LIBRARY_PATH",
            file=sys.stderr,
        )
    except subprocess.CalledProcessError as e:
        print(f"fix_bundle_ocio: patchelf failed ({e.returncode})", file=sys.stderr)

    try:
        needed = subprocess.check_output(["patchelf", "--print-needed", str(ext)], text=True)
        for line in needed.splitlines():
            if "OpenColorIO" in line and line.strip() != "libOpenColorIO.so":
                subprocess.check_call(
                    ["patchelf", "--replace-needed", line.strip(), "libOpenColorIO.so", str(ext)]
                )
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass

    print(f"fix_bundle_ocio: Linux — ensured {plain}")


def _windows_copy_dll(src: Path, dests: list[Path]) -> None:
    written: set[Path] = set()
    for dest in dests:
        dest = dest.resolve()
        if dest in written:
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        written.add(dest)
        print(f"fix_bundle_ocio: Windows — copied {dest}")


def _windows_materialize_ocio24(work_dir: Path) -> Path:
    """Ensure OpenColorIO_2_4.dll exists on disk; download from PyPI if needed."""
    path = materialize_ocio24_dll(work_dir)
    if path is not None and path.is_file():
        return path
    # Last resort: write into a temp file under work_dir via raw bytes
    data = read_ocio24_dll_bytes()
    if not data:
        raise SystemExit(
            "fix_bundle_ocio: cannot obtain OpenColorIO_2_4.dll "
            "(not in env, uv cache, or PyPI oiio-python wheel). "
            "Windows OpenImageIO.pyd will fail LoadLibrary."
        )
    dest = work_dir / OIIO_OCIO24_DLL
    dest.write_bytes(data)
    print(f"fix_bundle_ocio: wrote {dest} ({len(data)} bytes)")
    return dest


def _windows_dist_root(ext: Path) -> Path:
    """Best-effort Nuitka dist root (folder with exr_converter.exe or *.dist)."""
    cur = ext.parent
    for _ in range(8):
        if (cur / "exr_converter.exe").is_file() or cur.name.endswith(".dist"):
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    return ext.parent.parent


def _windows_fix(ext: Path, src_ext: Path, src_lib: Path) -> None:
    """Restore PyOpenColorIO 2.5 *and* keep OIIO's OpenColorIO 2.4 DLL.

    oiio-python's Windows wheel ships ``OpenColorIO_2_4.dll`` under
    ``PyOpenColorIO/``. Reinstalling opencolorio 2.5 for the app removes that
    file; OpenImageIO.pyd then fails with LoadLibraryExW "module not found".
    """
    ext = _replace_extension(ext, src_ext)
    dist_root = _windows_dist_root(ext)

    # App OCIO 2.5 — next to PyOpenColorIO.pyd and dist root.
    # Keep the exact filename from the opencolorio wheel (OpenColorIO_2_5.dll).
    _windows_copy_dll(
        src_lib,
        [
            ext.parent / src_lib.name,
            dist_root / src_lib.name,
        ],
    )

    # OIIO OCIO 2.4 — required for OpenImageIO.pyd LoadLibrary on Windows.
    # Fetch from env / uv cache / PyPI if ensure_ocio did not already place it.
    staging = dist_root / "_ocio24_staging"
    ocio24 = _windows_materialize_ocio24(staging)

    dests_24 = [
        dist_root / OIIO_OCIO24_DLL,
        dist_root / "OpenImageIO" / OIIO_OCIO24_DLL,
    ]
    for oiio_pyd in dist_root.rglob("OpenImageIO*.pyd"):
        dests_24.append(oiio_pyd.parent / OIIO_OCIO24_DLL)
    # Also next to flattened openimageio.dll (Nuitka lowercases some names)
    for dll in dist_root.glob("[Oo]pen[Ii]mage[Ii][Oo]*.dll"):
        dests_24.append(dll.parent / OIIO_OCIO24_DLL)

    _windows_copy_dll(ocio24, dests_24)

    # Hard fail if still missing (case-insensitive check on Windows).
    found = any(
        p.is_file()
        for p in dist_root.rglob("*")
        if p.is_file() and p.name.lower() == OIIO_OCIO24_DLL.lower()
    )
    if not found:
        raise SystemExit(f"fix_bundle_ocio: {OIIO_OCIO24_DLL} still missing under {dist_root}")
    print(f"fix_bundle_ocio: verified {OIIO_OCIO24_DLL} present in Windows bundle")

    # Cleanup staging dir if empty of other files
    try:
        if staging.is_dir():
            for p in staging.iterdir():
                p.unlink(missing_ok=True)
            staging.rmdir()
    except OSError:
        pass


def fix_bundle(root: Path) -> None:
    root = root.resolve()
    if not root.exists():
        raise SystemExit(f"Bundle path does not exist: {root}")

    # Accept .app bundle or standalone dist dir
    search_root = root
    if root.suffix == ".app" or root.name.endswith(".app"):
        macos = root / "Contents" / "MacOS"
        if macos.is_dir():
            search_root = macos

    site = _site_pyocio_dir()
    src_lib = _source_lib(site)
    src_ext = _source_extension(site)
    ext = _find_extension(search_root)
    print(f"fix_bundle_ocio: extension {ext}")
    print(f"fix_bundle_ocio: source ext {src_ext} ({src_ext.stat().st_size} bytes)")
    print(f"fix_bundle_ocio: source lib {src_lib} (from build-env PyOpenColorIO)")

    system = platform.system()
    if system == "Darwin":
        _macos_fix(ext, src_ext, src_lib)
    elif system == "Windows":
        _windows_fix(ext, src_ext, src_lib)
    else:
        _linux_fix(ext, src_ext, src_lib)

    print("fix_bundle_ocio: done")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "bundle",
        type=Path,
        help='Path to "EXR Converter.app" or Nuitka dist/main.dist',
    )
    args = ap.parse_args()
    fix_bundle(args.bundle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
