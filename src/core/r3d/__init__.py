"""Optional RED R3D / N-RAW decode via the local R3D SDK bridge.

Public API is stable: ``from src.core.r3d import R3DClip, is_available, …``.
"""

from __future__ import annotations

from pathlib import Path

from .clip import (
    R3DClip,
    R3DClipInfo,
    R3DError,
    R3DUnavailableError,
    probe_r3d,
    r3d_exr_attributes,
)
from .constants import (
    CLIP_META_KEYS,
    DECODE_EIGHTH_GOOD,
    DECODE_FULL_PREMIUM,
    DECODE_HALF_GOOD,
    DECODE_HALF_PREMIUM,
    DECODE_MODE_SCALE,
    DECODE_PREVIEW,
    DECODE_QUARTER_GOOD,
    DECODE_SIXTEENTH_GOOD,
    DECODE_THUMBNAIL,
    PIPELINE_CLIP_DEFAULT,
    PIPELINE_PRIMARY_LOG3G10,
    R3D_SRC_COLORSPACE_CANDIDATES,
    R3D_SUFFIXES,
    RED_REDISTRIBUTABLE_NOTICE,
    decode_mode_for_scale,
    scale_for_decode_mode,
)
from .native import decoder_kind, is_available, sdk_version, unavailable_reason
from .paths import bridge_candidates, bridge_names

# Tests / advanced callers still poke private helpers under these names.
_bridge_candidates = bridge_candidates
_bridge_names = bridge_names


def is_r3d_path(path: str | Path) -> bool:
    """True if *path* looks like an R3D / N-RAW file by extension.

    Rejects macOS AppleDouble sidecars (``._clip.R3D``) that share the media
    extension but are Finder/resource-fork metadata, not decodable clips.
    """
    p = Path(path)
    if p.name.startswith("._"):
        return False
    return p.suffix.lower() in R3D_SUFFIXES


def r3d_src_colorspace_candidates(path: str | Path = "") -> list[str]:
    """OCIO source-space candidates for R3D primary Log3G10 decode."""
    _ = path
    return list(R3D_SRC_COLORSPACE_CANDIDATES)


__all__ = [
    "CLIP_META_KEYS",
    "DECODE_EIGHTH_GOOD",
    "DECODE_FULL_PREMIUM",
    "DECODE_HALF_GOOD",
    "DECODE_HALF_PREMIUM",
    "DECODE_MODE_SCALE",
    "DECODE_PREVIEW",
    "DECODE_QUARTER_GOOD",
    "DECODE_SIXTEENTH_GOOD",
    "DECODE_THUMBNAIL",
    "PIPELINE_CLIP_DEFAULT",
    "PIPELINE_PRIMARY_LOG3G10",
    "RED_REDISTRIBUTABLE_NOTICE",
    "R3DClip",
    "R3DClipInfo",
    "R3DError",
    "R3DUnavailableError",
    "R3D_SRC_COLORSPACE_CANDIDATES",
    "R3D_SUFFIXES",
    "decode_mode_for_scale",
    "decoder_kind",
    "is_available",
    "is_r3d_path",
    "probe_r3d",
    "r3d_exr_attributes",
    "r3d_src_colorspace_candidates",
    "scale_for_decode_mode",
    "sdk_version",
    "unavailable_reason",
]
