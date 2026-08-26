"""Optional oxideav-prores PyO3 bindings for true 12-bit ProRes-compatible MOV.

The native extension (``exr_prores``) links pure-Rust oxideav-prores and writes
``.mov`` in-process — no subprocess sidecar. When the extension is not built
(dev without Rust / maturin), presets are hidden and CLI reports unavailable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

_IMPORT_ERROR: str | None
try:
    import exr_prores as _native

    _IMPORT_ERROR = None
except ImportError as exc:  # pragma: no cover - depends on local build
    _native = None  # type: ignore[assignment]
    _IMPORT_ERROR = str(exc)


def is_available() -> bool:
    """True when the ``exr_prores`` extension module imports successfully."""
    return _native is not None


def unavailable_reason() -> str:
    if is_available():
        return ""
    return (
        "oxideav ProRes extension not built "
        f"({_IMPORT_ERROR or 'import failed'}). "
        "Run: make oxideav-prores"
    )


def extension_version() -> str:
    if not is_available():
        return ""
    return str(_native.version())


def profile_for_codec_key(codec_key: str) -> str:
    """Map preset key → oxideav profile (proxy / lt / 422 / hq / 4444 / xq)."""
    key = codec_key.strip().lower()
    mapping = {
        "prores_ox_proxy": "proxy",
        "proxy": "proxy",
        "apco": "proxy",
        "prores_ox_lt": "lt",
        "lt": "lt",
        "apcs": "lt",
        "prores_ox_422": "422",
        "422": "422",
        "standard": "422",
        "apcn": "422",
        "prores_ox_hq": "hq",
        "hq": "hq",
        "apch": "hq",
        "prores_ox_4444": "4444",
        "4444": "4444",
        "ap4h": "4444",
        "prores_ox_xq": "xq",
        "xq": "xq",
        "ap4x": "xq",
    }
    if key in mapping:
        return mapping[key]
    raise ValueError(f"not an oxideav ProRes codec key: {codec_key!r}")


def open_writer(
    path: str | Path,
    width: int,
    height: int,
    fps_num: int,
    fps_den: int,
    codec_key: str,
) -> Any:
    """Open an in-process ProRes MOV writer for *codec_key*."""
    if not is_available():
        raise RuntimeError(unavailable_reason())
    profile = profile_for_codec_key(codec_key)
    return _native.ProResMovWriter(
        str(path),
        int(width),
        int(height),
        int(fps_num),
        int(fps_den),
        profile=profile,
    )


def write_rgb48_frame(writer: Any, rgb_u16: np.ndarray) -> None:
    """Write one HxWx3 uint16 full-range RGB frame."""
    arr = np.ascontiguousarray(rgb_u16, dtype=np.uint16)
    if arr.ndim != 3 or arr.shape[2] != 3:
        raise ValueError(f"expected HxWx3 uint16 RGB, got shape {arr.shape}")
    writer.write_rgb48(arr)
