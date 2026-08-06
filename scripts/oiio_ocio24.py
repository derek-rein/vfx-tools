"""Locate / fetch oiio-python's OpenColorIO_2_4.dll (Windows OIIO dependency).

oiio-python's Windows wheel vendors ``PyOpenColorIO/OpenColorIO_2_4.dll``.
Reinstalling the standalone ``opencolorio`` 2.5 package overwrites that tree and
drops the DLL, so Nuitka builds then fail to LoadLibrary OpenImageIO.pyd.
"""

from __future__ import annotations

import io
import json
import os
import sys
import zipfile
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

OIIO_OCIO24_DLL = "OpenColorIO_2_4.dll"


def oiio_package_dir() -> Path | None:
    try:
        import OpenImageIO as oiio

        return Path(oiio.__file__).resolve().parent
    except Exception:
        return None


def _find_cached_oiio_wheel() -> Path | None:
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


def _dll_from_zip(zf: zipfile.ZipFile) -> bytes | None:
    for name in zf.namelist():
        base = name.rsplit("/", 1)[-1]
        if base.lower() == OIIO_OCIO24_DLL.lower():
            return zf.read(name)
    return None


def _download_oiio_win_wheel_dll() -> bytes | None:
    """Download the matching oiio-python Windows wheel from PyPI and extract the DLL."""
    try:
        import importlib.metadata as md

        ver = md.version("oiio-python")
    except Exception:
        ver = "3.0.10.0.1"

    try:
        with urlopen(f"https://pypi.org/pypi/oiio-python/{ver}/json", timeout=60) as resp:
            meta = json.load(resp)
    except (URLError, OSError, json.JSONDecodeError) as e:
        print(f"oiio_ocio24: PyPI metadata failed: {e}", file=sys.stderr)
        return None

    # Prefer cp313 win_amd64; fall back to any win_amd64 wheel for this version.
    urls = meta.get("urls") or []
    chosen = None
    for u in urls:
        fn = u.get("filename", "")
        if "win_amd64" in fn and "cp313" in fn and fn.endswith(".whl"):
            chosen = u
            break
    if chosen is None:
        for u in urls:
            fn = u.get("filename", "")
            if "win_amd64" in fn and fn.endswith(".whl"):
                chosen = u
                break
    if chosen is None:
        print(f"oiio_ocio24: no win_amd64 wheel for oiio-python {ver}", file=sys.stderr)
        return None

    url = chosen["url"]
    print(f"oiio_ocio24: downloading {chosen['filename']}")
    try:
        with urlopen(url, timeout=120) as resp:
            data = resp.read()
    except (URLError, OSError) as e:
        print(f"oiio_ocio24: download failed: {e}", file=sys.stderr)
        return None

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            dll = _dll_from_zip(zf)
            if dll is None:
                print("oiio_ocio24: DLL not inside wheel", file=sys.stderr)
            return dll
    except zipfile.BadZipFile as e:
        print(f"oiio_ocio24: bad wheel: {e}", file=sys.stderr)
        return None


def read_ocio24_dll_bytes() -> bytes | None:
    """Return OpenColorIO_2_4.dll contents, or None if unavailable."""
    # 1) Already on disk under the env
    for base in {Path(sys.prefix), Path(sys.base_prefix)}:
        try:
            for p in base.rglob(OIIO_OCIO24_DLL):
                if p.is_file():
                    return p.read_bytes()
            # Case-insensitive fallback (Windows FS may store different casing)
            for p in base.rglob("*"):
                if p.is_file() and p.name.lower() == OIIO_OCIO24_DLL.lower():
                    return p.read_bytes()
        except OSError:
            continue

    # 2) uv/pip wheel cache
    whl = _find_cached_oiio_wheel()
    if whl is not None:
        try:
            with zipfile.ZipFile(whl) as zf:
                dll = _dll_from_zip(zf)
                if dll is not None:
                    print(f"oiio_ocio24: extracted from cache {whl.name}")
                    return dll
        except (OSError, zipfile.BadZipFile) as e:
            print(f"oiio_ocio24: cache wheel unreadable ({whl}): {e}", file=sys.stderr)

    # 3) Download Windows wheel from PyPI (works on any host OS during CI)
    return _download_oiio_win_wheel_dll()


def materialize_ocio24_dll(dest_dir: Path) -> Path | None:
    """Write OpenColorIO_2_4.dll into *dest_dir*; return the path or None."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / OIIO_OCIO24_DLL
    if dest.is_file() and dest.stat().st_size > 0:
        return dest
    data = read_ocio24_dll_bytes()
    if not data:
        return None
    dest.write_bytes(data)
    print(f"oiio_ocio24: wrote {dest} ({len(data)} bytes)")
    return dest
