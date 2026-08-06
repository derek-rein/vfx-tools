#!/usr/bin/env python3
"""Ensure the runtime OpenColorIO library is 2.5+ (matches the bundled config).

``oiio-python`` sometimes rewrites ``PyOpenColorIO`` to link against its
vendored OpenColorIO **2.4** (macOS: ``OpenImageIO/.dylibs/libOpenColorIO.2.4``;
Windows: ``PyOpenColorIO/OpenColorIO_2_4.dll`` bundled inside the oiio wheel).
That makes ``PyOpenColorIO.GetVersion()`` report 2.4.0 and the bundled ACES
Studio v4 config (profile 2.5) fails to load.

This script reinstalls ``opencolorio`` so PyOpenColorIO is 2.5+ again.

**Windows caveat:** reinstalling opencolorio overwrites the oiio-shipped
``PyOpenColorIO/`` tree and **deletes** ``OpenColorIO_2_4.dll``. OpenImageIO's
native DLL still depends on that 2.4 library, so we preserve/restore it next
to ``OpenImageIO.pyd`` for packaging and runtime.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path

REQUIRED = (2, 5, 0)
PACKAGE = "opencolorio>=2.5.1"
OIIO_OCIO24_DLL = "OpenColorIO_2_4.dll"


def _parse_version(text: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", text)
    return tuple(int(n) for n in nums[:3]) if nums else (0,)


def _runtime_version_fresh() -> str:
    """Import OCIO in a clean interpreter so .so linkage is re-read from disk."""
    code = (
        "import PyOpenColorIO as o; print(o.GetVersion(), end=''); "
        "print('\\n' + o.__file__, end='')"
    )
    out = subprocess.check_output([sys.executable, "-c", code], text=True)
    return out.splitlines()[0].strip()


def _is_broken() -> tuple[bool, str]:
    try:
        ver = _runtime_version_fresh()
    except Exception as e:
        return True, f"import failed: {e}"
    if _parse_version(ver) < REQUIRED:
        return True, ver
    return False, ver


def _oiio_dir() -> Path | None:
    try:
        import OpenImageIO as oiio

        return Path(oiio.__file__).resolve().parent
    except Exception:
        return None


def _find_oiio_wheel() -> Path | None:
    """Locate a cached oiio_python wheel (uv/pip) containing the 2.4 OCIO DLL."""
    roots: list[Path] = []
    for key in ("UV_CACHE_DIR", "PIP_CACHE_DIR"):
        v = os.environ.get(key)
        if v:
            roots.append(Path(v))
    roots.extend(
        [
            Path.home() / ".cache" / "uv",
            Path.home() / ".cache" / "pip",
            Path(os.environ.get("LOCALAPPDATA", "")) / "uv" / "cache",
            Path(os.environ.get("LOCALAPPDATA", "")) / "pip" / "Cache",
        ]
    )
    hits: list[Path] = []
    for root in roots:
        if not root or not root.is_dir():
            continue
        try:
            hits.extend(root.rglob("oiio_python-*.whl"))
            hits.extend(root.rglob("oiio-python-*.whl"))
        except OSError:
            continue
    if not hits:
        return None
    hits.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return hits[0]


def _read_ocio24_dll_bytes() -> bytes | None:
    """Return OpenColorIO_2_4.dll bytes from disk or the oiio wheel cache."""
    # Any remaining copy under the env
    for base in {Path(sys.prefix), Path(sys.base_prefix)}:
        try:
            for p in base.rglob(OIIO_OCIO24_DLL):
                if p.is_file():
                    return p.read_bytes()
        except OSError:
            continue

    whl = _find_oiio_wheel()
    if whl is None:
        return None
    try:
        with zipfile.ZipFile(whl) as zf:
            for name in zf.namelist():
                if name.endswith(OIIO_OCIO24_DLL) or name.endswith("/" + OIIO_OCIO24_DLL):
                    print(f"ensure_ocio: extracting {name} from {whl.name}")
                    return zf.read(name)
    except (OSError, zipfile.BadZipFile) as e:
        print(f"ensure_ocio: could not read {whl}: {e}", file=sys.stderr)
    return None


def _preserve_oiio_ocio24_dll() -> None:
    """Keep OpenColorIO_2_4.dll next to OpenImageIO for Windows LoadLibrary.

    Safe no-op on platforms / installs that do not need it.
    """
    oiio = _oiio_dir()
    if oiio is None:
        return

    dest = oiio / OIIO_OCIO24_DLL
    if dest.is_file():
        print(f"ensure_ocio: OIIO OCIO 2.4 present at {dest}")
        return

    data = _read_ocio24_dll_bytes()
    if not data:
        # Non-Windows oiio wheels often vendor OCIO under .dylibs instead.
        if sys.platform == "win32":
            print(
                "ensure_ocio: WARNING — could not restore OpenColorIO_2_4.dll; "
                "Windows OpenImageIO.pyd may fail LoadLibrary in the Nuitka bundle.",
                file=sys.stderr,
            )
        return

    dest.write_bytes(data)
    print(f"ensure_ocio: wrote {dest} ({len(data)} bytes) for OIIO")


def _reinstall() -> int:
    # Snapshot 2.4 DLL before opencolorio clobbers oiio's PyOpenColorIO tree.
    saved = _read_ocio24_dll_bytes()

    cmds = [
        [
            "uv",
            "pip",
            "install",
            "--python",
            sys.executable,
            "--reinstall-package",
            "opencolorio",
            PACKAGE,
        ],
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--force-reinstall",
            "--no-deps",
            PACKAGE,
        ],
    ]
    for cmd in cmds:
        try:
            print(f"ensure_ocio: running {' '.join(cmd)}")
            subprocess.check_call(cmd)
            if saved:
                oiio = _oiio_dir()
                if oiio is not None:
                    (oiio / OIIO_OCIO24_DLL).write_bytes(saved)
                    print(f"ensure_ocio: restored {OIIO_OCIO24_DLL} under {oiio}")
            return 0
        except FileNotFoundError:
            continue
        except subprocess.CalledProcessError as e:
            print(
                f"ensure_ocio: command failed ({e.returncode}): {' '.join(cmd)}",
                file=sys.stderr,
            )
            return e.returncode
    print("ensure_ocio: neither uv nor pip available", file=sys.stderr)
    return 1


def main() -> int:
    broken, ver = _is_broken()
    if not broken:
        print(f"ensure_ocio: OK — OpenColorIO {ver}")
        _preserve_oiio_ocio24_dll()
        return 0

    print(
        f"ensure_ocio: OpenColorIO runtime is {ver!r}, need >= {'.'.join(map(str, REQUIRED))}.\n"
        "  (oiio-python often rewires PyOpenColorIO to its vendored 2.4 library.)\n"
        f"  Reinstalling {PACKAGE} …",
        file=sys.stderr,
    )
    rc = _reinstall()
    if rc != 0:
        return rc

    broken, ver = _is_broken()
    if broken:
        print(f"ensure_ocio: still broken after reinstall ({ver})", file=sys.stderr)
        return 2

    print(f"ensure_ocio: repaired — OpenColorIO {ver}")
    _preserve_oiio_ocio24_dll()

    try:
        code = "import PyOpenColorIO as o; print(o.__file__)"
        mod = subprocess.check_output([sys.executable, "-c", code], text=True).strip()
        so = Path(mod).resolve().parent / "PyOpenColorIO.so"
        if so.is_file():
            out = subprocess.check_output(["otool", "-L", str(so)], text=True)
            first = next((ln.strip() for ln in out.splitlines()[1:] if ln.strip()), "")
            if first:
                print(f"ensure_ocio: link → {first.split()[0]}")
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
