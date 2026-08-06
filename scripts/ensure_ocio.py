#!/usr/bin/env python3
"""Ensure the runtime OpenColorIO library is 2.5+ (matches the bundled config).

``oiio-python`` sometimes rewires ``PyOpenColorIO`` to its vendored OpenColorIO
**2.4**. This script reinstalls ``opencolorio`` so PyOpenColorIO is 2.5+ again.

**Windows:** reinstalling opencolorio overwrites oiio's ``PyOpenColorIO/`` tree
and drops ``OpenColorIO_2_4.dll``, which OpenImageIO still needs. We restore
that DLL next to the OpenImageIO package for Nuitka packaging / LoadLibrary.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REQUIRED = (2, 5, 0)
PACKAGE = "opencolorio>=2.5.1"

# Shared helper lives next to this script.
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from oiio_ocio24 import materialize_ocio24_dll, oiio_package_dir  # noqa: E402


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


def _preserve_oiio_ocio24_dll() -> None:
    """Keep OpenColorIO_2_4.dll next to OpenImageIO for Windows LoadLibrary."""
    oiio = oiio_package_dir()
    if oiio is None:
        return
    if sys.platform != "win32":
        # Still useful if a Windows wheel was inspected cross-platform; no-op OK.
        pass
    path = materialize_ocio24_dll(oiio)
    if path is None and sys.platform == "win32":
        print(
            "ensure_ocio: WARNING — could not restore OpenColorIO_2_4.dll; "
            "Windows OpenImageIO.pyd may fail LoadLibrary in the Nuitka bundle.",
            file=sys.stderr,
        )
    elif path is not None:
        print(f"ensure_ocio: OIIO OCIO 2.4 ready at {path}")


def _reinstall() -> int:
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
