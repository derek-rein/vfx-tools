#!/usr/bin/env python3
"""Ensure the runtime OpenColorIO library is 2.5+ (matches the bundled config).

``oiio-python`` sometimes rewrites ``PyOpenColorIO.so`` to link against the
OpenColorIO 2.4.0 dylib it vendors under ``OpenImageIO/.dylibs/``.  That makes
``PyOpenColorIO.GetVersion()`` report 2.4.0 even when the ``opencolorio``
package metadata says 2.5.1, and the bundled ACES Studio v4 config
(``ocio_profile_version: 2.5``) then fails to load.

This script detects that mismatch and reinstalls ``opencolorio`` so the
extension links its own ``@loader_path/libOpenColorIO.dylib`` again.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


REQUIRED = (2, 5, 0)
PACKAGE = "opencolorio>=2.5.1"


def _parse_version(text: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", text)
    return tuple(int(n) for n in nums[:3]) if nums else (0,)


def _runtime_version() -> str:
    import PyOpenColorIO as ocio

    return str(ocio.GetVersion())


def _is_broken() -> tuple[bool, str]:
    try:
        ver = _runtime_version()
    except Exception as e:
        return True, f"import failed: {e}"
    if _parse_version(ver) < REQUIRED:
        return True, ver
    return False, ver


def _reinstall() -> int:
    # Prefer uv when available (project venv), fall back to the active pip.
    cmds = [
        ["uv", "pip", "install", "--reinstall-package", "opencolorio", PACKAGE],
        [sys.executable, "-m", "pip", "install", "--force-reinstall", "--no-deps", PACKAGE],
    ]
    for cmd in cmds:
        try:
            print(f"ensure_ocio: running {' '.join(cmd)}")
            subprocess.check_call(cmd)
            return 0
        except FileNotFoundError:
            continue
        except subprocess.CalledProcessError as e:
            print(f"ensure_ocio: command failed ({e.returncode}): {' '.join(cmd)}", file=sys.stderr)
            return e.returncode
    print("ensure_ocio: neither uv nor pip available", file=sys.stderr)
    return 1


def main() -> int:
    broken, ver = _is_broken()
    if not broken:
        print(f"ensure_ocio: OK — OpenColorIO {ver}")
        return 0

    print(
        f"ensure_ocio: OpenColorIO runtime is {ver!r}, need >= {'.'.join(map(str, REQUIRED))}.\n"
        "  (oiio-python often rewires PyOpenColorIO to its vendored 2.4 dylib.)\n"
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
    # Spot-check the macOS install name when otool is available.
    try:
        import PyOpenColorIO as ocio

        so = Path(ocio.__file__).resolve().parent / "PyOpenColorIO.so"
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
