"""ctypes load + InitializeSdk lifecycle for the R3D bridge library."""

from __future__ import annotations

import atexit
import ctypes
import logging
import sys

from ..app_paths import is_frozen_app
from .paths import bridge_candidates, find_redistributable_dir

log = logging.getLogger(__name__)

_lib: ctypes.CDLL | None = None
_init_attempted = False
_init_ok = False
_init_error = ""


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


def _load_library() -> ctypes.CDLL | None:
    global _lib
    if _lib is not None:
        return _lib
    for path in bridge_candidates():
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


def last_error(lib: ctypes.CDLL | None = None) -> str:
    lib = lib or _lib
    if lib is None:
        return ""
    try:
        raw = lib.r3d_bridge_last_error()
        if raw:
            return raw.decode("utf-8", errors="replace")
    except Exception:
        pass
    return ""


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


def ensure_initialized() -> bool:
    """Load bridge + InitializeSdk if possible. Idempotent."""
    global _init_attempted, _init_ok, _init_error
    if _init_attempted:
        return _init_ok
    _init_attempted = True

    lib = _load_library()
    if lib is None:
        tried = ", ".join(str(p) for p in bridge_candidates()[:12])
        _init_error = (
            "R3D bridge library not found. Build with "
            "`python3 scripts/build_r3d_bridge.py` after installing the RED R3D SDK "
            f"(see docs/r3d.md). Tried: {tried}"
        )
        log.warning(
            "%s (frozen=%s exe=%s)",
            _init_error,
            is_frozen_app(),
            sys.executable,
        )
        return False

    bridge_path = getattr(lib, "_bridge_path", None)
    libs_path = find_redistributable_dir(bridge_path)
    if libs_path is None:
        _init_error = (
            "RED Redistributable libraries not found (REDR3D.*). "
            "Set EXR_CONVERTER_R3D_LIBS to the Redistributable/{mac,linux,win} folder."
        )
        log.debug(_init_error)
        return False

    rc = lib.r3d_bridge_initialize(str(libs_path).encode("utf-8"))
    if rc != 0:
        _init_error = last_error(lib) or f"InitializeSdk failed ({rc})"
        log.warning("R3D SDK init failed: %s", _init_error)
        return False

    _init_ok = True
    atexit.register(_finalize_safe)
    log.info("R3D SDK initialized libs=%s", libs_path)
    return True


def is_available() -> bool:
    return ensure_initialized()


def unavailable_reason() -> str:
    if ensure_initialized():
        return ""
    return _init_error or "R3D support unavailable"


def sdk_version() -> str:
    if not ensure_initialized() or _lib is None:
        return ""
    buf = ctypes.create_string_buffer(256)
    _lib.r3d_bridge_sdk_version(buf, 256)
    return buf.value.decode("utf-8", errors="replace")


def get_lib() -> ctypes.CDLL | None:
    """Return the loaded CDLL after successful init, else None."""
    if not ensure_initialized():
        return None
    return _lib


def clip_info_struct_type() -> type:
    return _ClipInfoStruct
