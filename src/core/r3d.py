"""Optional RED R3D / N-RAW decode via the local R3D SDK bridge.

The proprietary RED R3D SDK is **not** bundled in source form. When a developer
(or release packager) builds ``libr3d_bridge`` with ``scripts/build_r3d_bridge.py``
and places the RED Redistributable dynamic libraries next to it, this module
loads them and exposes probe/decode helpers for video→EXR.

Without the bridge, :func:`is_available` is False and R3D paths raise a clear
error. See ``docs/r3d.md`` for license, EULA, and packaging constraints.
"""

from __future__ import annotations

import atexit
import ctypes
import logging
import os
import platform
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

# Extensions handled by the R3D SDK (not PyAV).
R3D_SUFFIXES: frozenset[str] = frozenset({".r3d", ".nev"})

# OCIO source-space candidates when decoding IPP2 primary development.
R3D_SRC_COLORSPACE_CANDIDATES: tuple[str, ...] = (
    "Log3G10 REDWideGamutRGB",
    "Input - RED - Log3G10",
    "RED Log3G10",
    "Log3G10",
    "REDWideGamutRGB",
)

# Decode ladder (matches native/r3d/r3d_bridge.h).
DECODE_FULL_PREMIUM = 0
DECODE_HALF_PREMIUM = 1
DECODE_HALF_GOOD = 2
DECODE_QUARTER_GOOD = 3
DECODE_EIGHTH_GOOD = 4
DECODE_SIXTEENTH_GOOD = 5

PIPELINE_PRIMARY_LOG3G10 = 0
PIPELINE_CLIP_DEFAULT = 1

# Preview / thumbnail defaults (convert uses full premium via decode_mode_for_scale).
DECODE_PREVIEW = DECODE_HALF_GOOD
DECODE_THUMBNAIL = DECODE_SIXTEENTH_GOOD

# Clip-level RMD keys we copy into EXR attributes (when present).
_CLIP_META_KEYS: tuple[str, ...] = (
    "camera_model",
    "camera_id",
    "camera_pin",
    "camera_firmware_version",
    "clip_id",
    "clip_uuid",
    "iso",
    "exposure_time",
    "exposure_compensation",
    "exposure_adjust",
    "framerate",
    "framerate_numerator",
    "framerate_denominator",
    "image_width",
    "image_height",
    "lens_name",
    "lens_brand",
    "lens_focal_length",
    "lens_aperture_label",
    "lens_mount",
    "gmt_date",
    "gmt_time",
    "hdr_mode",
    "sensor_name",
    "reel_id",
    "reel_no",
    "camera_network_name",
)

_lib: ctypes.CDLL | None = None
_init_attempted = False
_init_ok = False
_init_error = ""


class R3DUnavailableError(RuntimeError):
    """Raised when R3D support is requested but the bridge/SDK is missing."""


class R3DError(RuntimeError):
    """R3D SDK / bridge operation failed."""


@dataclass(frozen=True)
class R3DClipInfo:
    width: int
    height: int
    frame_count: int
    fps: float
    colorspace_hint: str
    sdk_version: str


class _ClipInfoStruct(ctypes.Structure):
    _fields_ = [
        ("width", ctypes.c_uint32),
        ("height", ctypes.c_uint32),
        ("frame_count", ctypes.c_uint32),
        ("fps", ctypes.c_float),
        ("file_id", ctypes.c_int),
        ("sdk_version", ctypes.c_char * 256),
        ("colorspace_hint", ctypes.c_char * 64),
    ]


def is_r3d_path(path: str | Path) -> bool:
    """True if *path* looks like an R3D / N-RAW file by extension."""
    return Path(path).suffix.lower() in R3D_SUFFIXES


def _bridge_names() -> tuple[str, ...]:
    system = platform.system()
    if system == "Darwin":
        return ("libr3d_bridge.dylib",)
    if system == "Windows":
        return ("libr3d_bridge.dll", "r3d_bridge.dll")
    return ("libr3d_bridge.so",)


def _is_frozen_app() -> bool:
    """True inside a Nuitka / PyInstaller-style binary (not a source checkout)."""
    if getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS"):
        return True
    # Nuitka marks compiled modules with ``__compiled__``.
    return globals().get("__compiled__") is not None


def _runtime_exe_dirs() -> list[Path]:
    """Directories that may sit next to the real application binary.

    Always consider ``sys.executable`` / ``argv[0]`` — Nuitka standalone often
    does **not** set ``sys.frozen``, so relying only on that misses the
    private ``r3d/`` folder we install next to the launcher.
    """
    dirs: list[Path] = []
    for raw in (sys.executable, sys.argv[0] if sys.argv else ""):
        if not raw:
            continue
        try:
            p = Path(raw).resolve()
        except OSError:
            continue
        if p.is_file():
            dirs.append(p.parent)
        elif p.is_dir():
            dirs.append(p)
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        dirs.append(Path(meipass))
    # macOS .app: private redistributables live under Contents/MacOS/r3d/.
    for d in list(dirs):
        if d.name == "MacOS" and d.parent.name == "Contents":
            dirs.append(d.parent / "Resources")
        elif (d / "MacOS").is_dir():
            dirs.append(d / "MacOS")
            dirs.append(d / "Resources")
    return dirs


def _bridge_candidates() -> list[Path]:
    """Search paths for libr3d_bridge.*"""
    names = _bridge_names()
    dirs: list[Path] = []
    files: list[Path] = []

    env = os.environ.get("EXR_CONVERTER_R3D_BRIDGE", "").strip()
    if env:
        p = Path(env).expanduser()
        if p.suffix.lower() in {".dylib", ".so", ".dll"}:
            files.append(p)
        else:
            dirs.append(p)

    # Prefer private app folder next to the binary (packaged + frozen).
    for exe_dir in _runtime_exe_dirs():
        dirs.append(exe_dir / "r3d")
        dirs.append(exe_dir)

    # Dev / source checkout layouts.
    try:
        pkg_root = Path(__file__).resolve().parents[2]
    except Exception:
        pkg_root = Path.cwd()
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


def _redistributable_candidates(bridge_path: Path | None) -> list[Path]:
    """Folders that may contain REDR3D.dylib / .so / .dll."""
    system = platform.system()
    if system == "Darwin":
        sub = "mac"
        marker = "REDR3D.dylib"
    elif system == "Windows":
        sub = "win"
        marker = "REDR3D-x64.dll"
    else:
        sub = "linux"
        marker = "REDR3D-x64.so"

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

    for exe_dir in _runtime_exe_dirs():
        roots.append(exe_dir / "r3d")
        roots.append(exe_dir)

    try:
        pkg_root = Path(__file__).resolve().parents[2]
    except Exception:
        pkg_root = Path.cwd()
    roots.append(pkg_root / "build" / "r3d" / "redistributable")
    roots.append(pkg_root / "resources" / "r3d")
    # Local SDK drop (developer machine only; gitignored).
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


def _load_library() -> ctypes.CDLL | None:
    global _lib
    if _lib is not None:
        return _lib
    for path in _bridge_candidates():
        if not path.is_file():
            continue
        try:
            lib = ctypes.CDLL(str(path))
        except OSError as e:
            log.debug("Failed to load R3D bridge %s: %s", path, e)
            continue
        _bind(lib)
        lib._bridge_path = path  # type: ignore[attr-defined]
        _lib = lib
        log.info("Loaded R3D bridge: %s", path)
        return _lib
    return None


def _bind(lib: ctypes.CDLL) -> None:
    lib.r3d_bridge_available.restype = ctypes.c_int
    lib.r3d_bridge_available.argtypes = []

    lib.r3d_bridge_initialize.restype = ctypes.c_int
    lib.r3d_bridge_initialize.argtypes = [ctypes.c_char_p]

    lib.r3d_bridge_finalize.restype = None
    lib.r3d_bridge_finalize.argtypes = []

    lib.r3d_bridge_is_initialized.restype = ctypes.c_int
    lib.r3d_bridge_is_initialized.argtypes = []

    lib.r3d_bridge_sdk_version.restype = None
    lib.r3d_bridge_sdk_version.argtypes = [ctypes.c_char_p, ctypes.c_size_t]

    lib.r3d_bridge_identify.restype = ctypes.c_int
    lib.r3d_bridge_identify.argtypes = [ctypes.c_char_p]

    lib.r3d_bridge_open.restype = ctypes.c_void_p
    lib.r3d_bridge_open.argtypes = [ctypes.c_char_p]

    lib.r3d_bridge_close.restype = None
    lib.r3d_bridge_close.argtypes = [ctypes.c_void_p]

    lib.r3d_bridge_clip_info.restype = ctypes.c_int
    lib.r3d_bridge_clip_info.argtypes = [ctypes.c_void_p, ctypes.POINTER(_ClipInfoStruct)]

    lib.r3d_bridge_decode_frame.restype = ctypes.c_int
    lib.r3d_bridge_decode_frame.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_float),
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_uint32),
    ]

    lib.r3d_bridge_decode_buffer_bytes.restype = ctypes.c_size_t
    lib.r3d_bridge_decode_buffer_bytes.argtypes = [
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.POINTER(ctypes.c_uint32),
    ]

    lib.r3d_bridge_last_error.restype = ctypes.c_char_p
    lib.r3d_bridge_last_error.argtypes = []

    lib.r3d_bridge_metadata_string.restype = ctypes.c_int
    lib.r3d_bridge_metadata_string.argtypes = [
        ctypes.c_void_p,
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_size_t,
    ]

    lib.r3d_bridge_absolute_timecode.restype = ctypes.c_int
    lib.r3d_bridge_absolute_timecode.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_char_p,
        ctypes.c_size_t,
    ]

    lib.r3d_bridge_edge_timecode.restype = ctypes.c_int
    lib.r3d_bridge_edge_timecode.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_char_p,
        ctypes.c_size_t,
    ]


def _last_error(lib: ctypes.CDLL) -> str:
    try:
        raw = lib.r3d_bridge_last_error()
        if raw:
            return raw.decode("utf-8", errors="replace")
    except Exception:
        pass
    return ""


def ensure_initialized() -> bool:
    """Load bridge + InitializeSdk if possible. Idempotent."""
    global _init_attempted, _init_ok, _init_error
    if _init_attempted:
        return _init_ok
    _init_attempted = True

    lib = _load_library()
    if lib is None:
        tried = ", ".join(str(p) for p in _bridge_candidates()[:12])
        _init_error = (
            "R3D bridge library not found. Build with "
            "`python3 scripts/build_r3d_bridge.py` after installing the RED R3D SDK "
            f"(see docs/r3d.md). Tried: {tried}"
        )
        log.warning("%s (frozen=%s exe=%s)", _init_error, _is_frozen_app(), sys.executable)
        return False

    bridge_path = getattr(lib, "_bridge_path", None)
    libs_path: Path | None = None
    for cand in _redistributable_candidates(bridge_path):
        # Prefer a directory that actually contains the main RED library.
        system = platform.system()
        markers = {
            "Darwin": "REDR3D.dylib",
            "Windows": "REDR3D-x64.dll",
            "Linux": "REDR3D-x64.so",
        }
        marker = markers.get(system, "REDR3D.dylib")
        if (cand / marker).is_file():
            libs_path = cand
            break
        # On Windows ARM, name differs — accept any REDR3D*
        if any(cand.glob("REDR3D*")):
            libs_path = cand
            break

    if libs_path is None:
        _init_error = (
            "RED Redistributable libraries not found (REDR3D.*). "
            "Set EXR_CONVERTER_R3D_LIBS to the Redistributable/{mac,linux,win} folder."
        )
        log.debug(_init_error)
        return False

    rc = lib.r3d_bridge_initialize(str(libs_path).encode("utf-8"))
    if rc != 0:
        _init_error = _last_error(lib) or f"InitializeSdk failed ({rc})"
        log.warning("R3D SDK init failed: %s", _init_error)
        return False

    _init_ok = True
    atexit.register(_finalize_safe)
    ver = sdk_version()
    log.info("R3D SDK initialized (%s) libs=%s", ver, libs_path)
    return True


def _finalize_safe() -> None:
    global _init_ok
    lib = _lib
    if lib is None or not _init_ok:
        return
    try:
        lib.r3d_bridge_finalize()
    except Exception:
        pass
    _init_ok = False


def is_available() -> bool:
    """True if the bridge loads and the R3D SDK initializes successfully."""
    return ensure_initialized()


def unavailable_reason() -> str:
    """Human-readable reason when :func:`is_available` is False."""
    if ensure_initialized():
        return ""
    return _init_error or "R3D support unavailable"


def sdk_version() -> str:
    if not ensure_initialized() or _lib is None:
        return ""
    buf = ctypes.create_string_buffer(256)
    _lib.r3d_bridge_sdk_version(buf, 256)
    return buf.value.decode("utf-8", errors="replace")


def decode_mode_for_scale(scale: float) -> int:
    """Map a convert *scale* factor to a native R3D decode mode."""
    if scale >= 0.99:
        return DECODE_FULL_PREMIUM
    if scale >= 0.49:
        return DECODE_HALF_PREMIUM
    if scale >= 0.24:
        return DECODE_QUARTER_GOOD
    if scale >= 0.12:
        return DECODE_EIGHTH_GOOD
    return DECODE_SIXTEENTH_GOOD


class R3DClip:
    """Open R3D / N-RAW clip (context manager).

    Decode is safe from one worker at a time (player prefetch holds a lock).
    Do not share one instance across concurrent decoders without external locking.
    """

    def __init__(self, path: str | Path) -> None:
        if not ensure_initialized() or _lib is None:
            raise R3DUnavailableError(unavailable_reason())
        self.path = str(Path(path).resolve())
        self._lib = _lib
        handle = self._lib.r3d_bridge_open(self.path.encode("utf-8"))
        if not handle:
            raise R3DError(_last_error(self._lib) or f"Failed to open {self.path}")
        self._handle: Any = handle
        self._info = self._read_info()

    def _read_info(self) -> R3DClipInfo:
        info = _ClipInfoStruct()
        rc = self._lib.r3d_bridge_clip_info(self._handle, ctypes.byref(info))
        if rc != 0:
            raise R3DError(_last_error(self._lib) or "clip_info failed")
        hint = info.colorspace_hint.decode("utf-8", errors="replace").strip()
        ver = info.sdk_version.decode("utf-8", errors="replace").strip()
        return R3DClipInfo(
            width=int(info.width),
            height=int(info.height),
            frame_count=int(info.frame_count),
            fps=float(info.fps) if info.fps > 0 else 24.0,
            colorspace_hint=hint or R3D_SRC_COLORSPACE_CANDIDATES[0],
            sdk_version=ver,
        )

    @property
    def info(self) -> R3DClipInfo:
        return self._info

    def close(self) -> None:
        if self._handle and self._lib is not None:
            self._lib.r3d_bridge_close(self._handle)
            self._handle = None

    def __enter__(self) -> R3DClip:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def decode_frame(
        self,
        frame_index: int,
        *,
        mode: int = DECODE_FULL_PREMIUM,
        pipeline: int = PIPELINE_PRIMARY_LOG3G10,
    ) -> np.ndarray:
        """Decode 0-based *frame_index* → float32 RGB ``(H, W, 3)`` in 0–1."""
        if not self._handle:
            raise R3DError("Clip is closed")
        w = ctypes.c_uint32(0)
        h = ctypes.c_uint32(0)
        nbytes = self._lib.r3d_bridge_decode_buffer_bytes(
            self._handle, int(mode), ctypes.byref(w), ctypes.byref(h)
        )
        if nbytes == 0 or w.value == 0 or h.value == 0:
            raise R3DError(_last_error(self._lib) or "invalid decode buffer size")

        # float32 RGB output
        arr = np.empty((int(h.value), int(w.value), 3), dtype=np.float32)
        flat = arr.reshape(-1)
        rc = self._lib.r3d_bridge_decode_frame(
            self._handle,
            ctypes.c_uint32(frame_index),
            int(mode),
            int(pipeline),
            flat.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
            flat.nbytes,
            ctypes.byref(w),
            ctypes.byref(h),
        )
        if rc != 0:
            raise R3DError(_last_error(self._lib) or f"decode failed frame={frame_index} rc={rc}")
        # If decoder returned different dims, reshape (should match).
        if int(w.value) != arr.shape[1] or int(h.value) != arr.shape[0]:
            arr = flat[: int(h.value) * int(w.value) * 3].reshape((int(h.value), int(w.value), 3))
        return arr

    def metadata_string(self, key: str) -> str:
        """Return clip-level metadata *key* as string, or empty if missing."""
        if not self._handle:
            return ""
        buf = ctypes.create_string_buffer(1024)
        rc = self._lib.r3d_bridge_metadata_string(self._handle, key.encode("utf-8"), buf, 1024)
        if rc != 1:
            return ""
        return buf.value.decode("utf-8", errors="replace").strip()

    def absolute_timecode(self, frame_index: int = 0) -> str:
        if not self._handle:
            return ""
        buf = ctypes.create_string_buffer(64)
        rc = self._lib.r3d_bridge_absolute_timecode(
            self._handle, ctypes.c_uint32(frame_index), buf, 64
        )
        if rc != 1:
            return ""
        return buf.value.decode("utf-8", errors="replace").strip()

    def edge_timecode(self, frame_index: int = 0) -> str:
        if not self._handle:
            return ""
        buf = ctypes.create_string_buffer(64)
        rc = self._lib.r3d_bridge_edge_timecode(self._handle, ctypes.c_uint32(frame_index), buf, 64)
        if rc != 1:
            return ""
        return buf.value.decode("utf-8", errors="replace").strip()

    def clip_metadata_dict(self) -> dict[str, str]:
        """Stable clip-level attributes for EXR headers / inspect."""
        out: dict[str, str] = {}
        for key in _CLIP_META_KEYS:
            val = self.metadata_string(key)
            if val:
                out[key] = val
        info = self._info
        out.setdefault("image_width", str(info.width))
        out.setdefault("image_height", str(info.height))
        out.setdefault("framerate", f"{info.fps:.6f}".rstrip("0").rstrip("."))
        if info.sdk_version:
            out["sdk_version"] = info.sdk_version
        tc = self.absolute_timecode(0)
        if tc:
            out["start_absolute_timecode"] = tc
        etc = self.edge_timecode(0)
        if etc:
            out["start_edge_timecode"] = etc
        return out


def probe_r3d(path: str | Path) -> tuple[int, int, float, int]:
    """Return ``(width, height, fps, frame_count)`` for an R3D/N-RAW path."""
    with R3DClip(path) as clip:
        i = clip.info
        return i.width, i.height, i.fps, max(1, i.frame_count)


def r3d_exr_attributes(path: str | Path, *, frame_index: int = 0) -> dict[str, str]:
    """Build EXR attribute map from an R3D/N-RAW clip.

    Keys are prefixed ``exrconverter:r3d:``. Includes per-frame timecode when
    *frame_index* (0-based) is available.
    """
    with R3DClip(path) as clip:
        meta = clip.clip_metadata_dict()
        tc = clip.absolute_timecode(frame_index)
        if tc:
            meta["absolute_timecode"] = tc
        etc = clip.edge_timecode(frame_index)
        if etc:
            meta["edge_timecode"] = etc
    return {f"exrconverter:r3d:{k}": v for k, v in meta.items() if v}


def r3d_src_colorspace_candidates(path: str | Path = "") -> list[str]:
    """OCIO source-space candidates for R3D primary Log3G10 decode."""
    # path reserved for future per-clip metadata; always primary log for now.
    _ = path
    return list(R3D_SRC_COLORSPACE_CANDIDATES)


# Required end-user notice when RED Redistributable libraries ship with the app
# (summary; full terms are in the official R3D SDK License Agreement).
RED_REDISTRIBUTABLE_NOTICE = """\
RED R3D / N-RAW decoding uses proprietary software from RED.COM, LLC / Nikon
(the “R3D SDK”). When this application includes RED Redistributable dynamic
libraries, those libraries remain the property of RED and are licensed to you
only under the R3D SDK License Agreement and the following conditions:

• You may use the R3D functionality solely as integrated in this application
  to decode R3D / N-RAW media for your own projects.
• You may not reverse engineer, decompile, disassemble, or otherwise attempt
  to derive the source code or file formats of the RED libraries or SDK.
• You may not redistribute the RED libraries separately, modify them, or
  place them in a shared system location for use by other software.
• You may not claim that this product is certified by RED or use RED
  trademarks without written permission from RED.
• THE RED LIBRARIES AND RELATED MATERIALS ARE PROVIDED “AS IS” WITHOUT
  WARRANTY OF ANY KIND. TO THE MAXIMUM EXTENT PERMITTED BY LAW, RED AND ITS
  LICENSORS DISCLAIM ALL WARRANTIES AND LIMIT LIABILITY AS SET OUT IN THE
  R3D SDK LICENSE AGREEMENT (INCLUDING AN AGGREGATE LIABILITY CAP).

Obtain the current license from the official R3D SDK package. For questions:
RED-r3dsdk@nikon.com
"""
