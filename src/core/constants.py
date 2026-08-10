from __future__ import annotations

import sys
from typing import NamedTuple

APP_ORG = "VFXTools"
APP_NAME = "EXRConverter"
APP_VERSION = "0.9.2"

GITHUB_REPO = "derek-rein/exr-converter"

DEFAULT_SRC_V2E = "Output - Rec.709"
DEFAULT_DST_V2E = "ACEScg"
DEFAULT_SRC_E2V = "scene_linear"
DEFAULT_DST_E2V = "Output - Rec.709"

COMMON_FPS = [23.976, 24, 25, 29.97, 30, 48, 50, 59.94, 60]

OCIO_SOURCE_ENV = "__env__"
OCIO_SOURCE_FILE = "__file__"
OCIO_SOURCE_BUNDLED = "__bundled__"

# Internal key for our super-awesome bundled ACES studio config (official,
# redistributable, rich camera IDTs from ASWF OpenColorIO-Config-ACES).
BUNDLED_ACES_STUDIO_KEY = "bundled-aces-studio-v4"

# Local Foundry Nuke installs (discovered on disk; never redistributed).
# Full keys look like ``nuke:17.0v3:fn-nuke_studio-config-…``.
NUKE_SOURCE_PREFIX = "nuke:"

EXR_COMPRESSIONS = [
    "none",
    "rle",
    "zip",
    "zips",
    "piz",
    "pxr24",
    "b44",
    "b44a",
    "dwaa",
    "dwab",
]
DEFAULT_EXR_COMPRESSION = "dwaa"

DEFAULT_FRAME_PADDING = 4
DEFAULT_START_FRAME = 1001

# Image-sequence extensions accepted as input for EXR→video (and the sequence
# browser). OpenEXR remains the primary path; DPX is the other production still
# format; PNG / JPEG / WebP cover common display stills.
#
# Deliberately excluded:
# - BMP / TGA — little production value for sequences
# - GIF — animation containers vs frame sequences are a common footgun
# - HEIC / AVIF — not reliable in our OIIO/PyAV wheels
# - Cineon / multi-part EXR — add deliberately if needed
IMAGE_SEQUENCE_EXTS: frozenset[str] = frozenset(
    {
        ".exr",
        ".dpx",
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
    }
)

# Formats treated as scene-referred / linear by default for OCIO src.
# Everything else in IMAGE_SEQUENCE_EXTS is assumed display-encoded (sRGB-ish).
# DPX is film/pipeline data (often log or linear) — not sRGB display stills.
SCENE_REFERRED_IMAGE_EXTS: frozenset[str] = frozenset({".exr", ".dpx"})

# Prefer EXR when a folder contains mixed sequences, then DPX, then display stills.
_IMAGE_SEQ_EXT_PRIORITY: dict[str, int] = {
    ".exr": 0,
    ".dpx": 1,
    ".png": 2,
    ".jpg": 3,
    ".jpeg": 3,
    ".webp": 4,
}


def is_image_sequence_ext(ext: str) -> bool:
    """True if *ext* (with or without leading dot) is an accepted sequence format."""
    e = ext.lower() if ext.startswith(".") else f".{ext.lower()}"
    return e in IMAGE_SEQUENCE_EXTS


def is_scene_referred_image_ext(ext: str) -> bool:
    """True when the format is expected to hold scene-linear data (EXR)."""
    e = ext.lower() if ext.startswith(".") else f".{ext.lower()}"
    return e in SCENE_REFERRED_IMAGE_EXTS


def image_sequence_ext_priority(ext: str) -> int:
    """Sort key: lower = preferred when scanning a mixed folder."""
    e = ext.lower() if ext.startswith(".") else f".{ext.lower()}"
    return _IMAGE_SEQ_EXT_PRIORITY.get(e, 50)


SCALE_OPTIONS = [
    (1.0, "100%"),
    (0.75, "75%"),
    (0.5, "50%"),
    (0.25, "25%"),
]
DEFAULT_SCALE = 1.0


class VideoCodecSpec(NamedTuple):
    """One selectable EXR→video codec preset.

    Bit depth, chroma, and pix_fmt are first-class so the UI never implies a
    higher precision than FFmpeg actually encodes (a common VFX gotcha).

    *platforms* empty = all OSes; otherwise e.g. ``("Darwin",)`` for macOS-only
    encoders such as VideoToolbox ProRes.
    """

    key: str
    display_name: str
    libav_codec: str
    pix_fmt: str
    bit_depth: int
    chroma: str  # e.g. "4:2:0", "4:2:2", "4:4:4", "RGB"
    platforms: tuple[str, ...] = ()  # empty = everywhere

    @property
    def format_label(self) -> str:
        """Short encode-format line for dialogs: ``10-bit · 4:2:2 · yuv422p10le``."""
        return f"{self.bit_depth}-bit · {self.chroma} · pix_fmt {self.pix_fmt}"

    def is_available(self) -> bool:
        if not self.platforms:
            return True
        return sys.platform in self.platforms


def _prores_ks(
    key: str,
    label: str,
    profile: str,
    pix_fmt: str,
    bit_depth: int,
    chroma: str,
) -> VideoCodecSpec:
    """Software ProRes via FFmpeg ``prores_ks`` (cross-platform)."""
    return VideoCodecSpec(
        key,
        f"{label} · {bit_depth}-bit {chroma}",
        "prores_ks",
        pix_fmt,
        bit_depth,
        chroma,
    )


def _prores_vt(
    key: str,
    label: str,
    pix_fmt: str,
    bit_depth: int,
    chroma: str,
) -> VideoCodecSpec:
    """Hardware ProRes via VideoToolbox — **macOS only**."""
    return VideoCodecSpec(
        key,
        f"{label} · {bit_depth}-bit {chroma} · VideoToolbox (macOS)",
        "prores_videotoolbox",
        pix_fmt,
        bit_depth,
        chroma,
        platforms=("darwin",),
    )


def _dnxhr(
    key: str,
    label: str,
    pix_fmt: str,
    bit_depth: int,
    chroma: str,
) -> VideoCodecSpec:
    return VideoCodecSpec(
        key,
        f"{label} · {bit_depth}-bit {chroma}",
        "dnxhd",
        pix_fmt,
        bit_depth,
        chroma,
    )


# Display names include bit depth + chroma. Pipeline: rgb48le → reformat(pix_fmt).
#
# FFmpeg bit-depth truth (verified with solid-patch mid-bin roundtrips):
# - prores_ks: only accepts yuv422p10le / yuv444p10le / yuva444p10le.
#   Profiles 0–5 all encode at 10-bit. 4444/XQ may *probe* as yuva444p12le
#   after decode (FFmpeg presentation), but +32 mid-bin vs 10-bit lattice
#   collapses to delta 0 — true encode precision is 10-bit.
# - prores_videotoolbox 422: 10-bit (p210le). 4444/XQ with ayuv64le preserve
#   mid-bin steps beyond 10-bit (Apple HW ~12-bit class).
# - DNxHR LB/SQ/HQ → 8-bit 4:2:2; HQX → 10-bit 4:2:2; 444 → 10-bit 4:4:4.
# - cfhd: yuv422p10le (10) / gbrp12le (true 12).
# - libx265: 8 / 10 / 12 via pix_fmt (Main / Main 10 / Main 12).
# - ffv1: lossless 10 or 12 via pix_fmt.
# - libx264: max 10-bit (High 10); no 12-bit.
VIDEO_CODECS: list[VideoCodecSpec] = [
    # ── ProRes (software, cross-platform) — all 10-bit encode ─────────────
    _prores_ks("prores_proxy", "Apple ProRes 422 Proxy", "0", "yuv422p10le", 10, "4:2:2"),
    _prores_ks("prores_lt", "Apple ProRes 422 LT", "1", "yuv422p10le", 10, "4:2:2"),
    _prores_ks("prores_422", "Apple ProRes 422", "2", "yuv422p10le", 10, "4:2:2"),
    # Legacy key ``prores`` = HQ (presets / CLI compatibility).
    _prores_ks("prores", "Apple ProRes 422 HQ", "3", "yuv422p10le", 10, "4:2:2"),
    _prores_ks("prores_4444", "Apple ProRes 4444", "4", "yuva444p10le", 10, "4:4:4:4"),
    _prores_ks("prores_xq", "Apple ProRes 4444 XQ", "5", "yuva444p10le", 10, "4:4:4:4"),
    # ── ProRes VideoToolbox (macOS only) ──────────────────────────────────
    _prores_vt("prores_vt_proxy", "Apple ProRes 422 Proxy", "p210le", 10, "4:2:2"),
    _prores_vt("prores_vt_lt", "Apple ProRes 422 LT", "p210le", 10, "4:2:2"),
    _prores_vt("prores_vt_422", "Apple ProRes 422", "p210le", 10, "4:2:2"),
    _prores_vt("prores_vt_hq", "Apple ProRes 422 HQ", "p210le", 10, "4:2:2"),
    # VT 4444/XQ: ayuv64le intermediate; HW keeps ~12-bit mid-bin (unlike prores_ks).
    _prores_vt("prores_vt_4444", "Apple ProRes 4444", "ayuv64le", 12, "4:4:4:4"),
    _prores_vt("prores_vt_xq", "Apple ProRes 4444 XQ", "ayuv64le", 12, "4:4:4:4"),
    # ── CineForm ──────────────────────────────────────────────────────────
    VideoCodecSpec(
        "cineform",
        "GoPro CineForm · 10-bit 4:2:2",
        "cfhd",
        "yuv422p10le",
        10,
        "4:2:2",
    ),
    VideoCodecSpec(
        "cineform_rgb",
        "GoPro CineForm RGB · 12-bit 4:4:4",
        "cfhd",
        "gbrp12le",
        12,
        "RGB",
    ),
    # ── DNxHR full ladder ─────────────────────────────────────────────────
    _dnxhr("dnxhr_lb", "DNxHR LB", "yuv422p", 8, "4:2:2"),
    _dnxhr("dnxhr_sq", "DNxHR SQ", "yuv422p", 8, "4:2:2"),
    _dnxhr("dnxhr_hq", "DNxHR HQ", "yuv422p", 8, "4:2:2"),
    _dnxhr("dnxhr_hqx", "DNxHR HQX", "yuv422p10le", 10, "4:2:2"),
    _dnxhr("dnxhr_444", "DNxHR 444", "yuv444p10le", 10, "4:4:4"),
    # ── Delivery ──────────────────────────────────────────────────────────
    VideoCodecSpec(
        "h264",
        "H.264 · 8-bit 4:2:0",
        "libx264",
        "yuv420p",
        8,
        "4:2:0",
    ),
    VideoCodecSpec(
        "hevc",
        "H.265 / HEVC · 10-bit 4:2:0",
        "libx265",
        "yuv420p10le",
        10,
        "4:2:0",
    ),
    VideoCodecSpec(
        "hevc_12",
        "H.265 / HEVC · 12-bit 4:2:0",
        "libx265",
        "yuv420p12le",
        12,
        "4:2:0",
    ),
    VideoCodecSpec(
        "hevc_8",
        "H.265 / HEVC · 8-bit 4:2:0",
        "libx265",
        "yuv420p",
        8,
        "4:2:0",
    ),
    VideoCodecSpec(
        "ffv1",
        "FFV1 (lossless) · 10-bit 4:4:4",
        "ffv1",
        "yuv444p10le",
        10,
        "4:4:4",
    ),
    VideoCodecSpec(
        "ffv1_12",
        "FFV1 (lossless) · 12-bit 4:4:4",
        "ffv1",
        "yuv444p12le",
        12,
        "4:4:4",
    ),
]

# Codec key families used by GUI/CLI option routing.
HEVC_CODEC_KEYS: frozenset[str] = frozenset({"hevc", "hevc_8", "hevc_12"})
FFV1_CODEC_KEYS: frozenset[str] = frozenset({"ffv1", "ffv1_12"})
X26X_CODEC_KEYS: frozenset[str] = frozenset({"h264"}) | HEVC_CODEC_KEYS
DEFAULT_VIDEO_CODEC = "prores"

# prores_ks / prores_videotoolbox profile values keyed by our preset *key*.
PRORES_KS_PROFILE: dict[str, str] = {
    "prores_proxy": "0",
    "prores_lt": "1",
    "prores_422": "2",
    "prores": "3",
    "prores_4444": "4",
    "prores_xq": "5",
}
PRORES_VT_PROFILE: dict[str, str] = {
    "prores_vt_proxy": "0",
    "prores_vt_lt": "1",
    "prores_vt_422": "2",
    "prores_vt_hq": "3",
    "prores_vt_4444": "4",
    "prores_vt_xq": "5",
}
DNXHR_PROFILE: dict[str, str] = {
    "dnxhr_lb": "dnxhr_lb",
    "dnxhr_sq": "dnxhr_sq",
    "dnxhr_hq": "dnxhr_hq",
    "dnxhr_hqx": "dnxhr_hqx",
    "dnxhr_444": "dnxhr_444",
}

# CineForm quality ladder (FFmpeg cfhd private option ``quality``).
CINEFORM_QUALITY_OPTIONS: list[tuple[str, str]] = [
    ("film3+", "Film 3+ (highest)"),
    ("film3", "Film 3"),
    ("film2", "Film 2"),
    ("film1", "Film 1"),
    ("high", "High"),
    ("medium", "Medium"),
    ("low", "Low"),
]
DEFAULT_CINEFORM_QUALITY = "film3"

# x264 / x265 CRF presets (shared names).
X26X_PRESETS = [
    "ultrafast",
    "superfast",
    "veryfast",
    "faster",
    "fast",
    "medium",
    "slow",
    "slower",
    "veryslow",
]


def available_video_codecs() -> list[VideoCodecSpec]:
    """Codecs usable on this OS (filters out VideoToolbox on non-macOS)."""
    return [c for c in VIDEO_CODECS if c.is_available()]


def video_codec_by_key(key: str) -> VideoCodecSpec | None:
    for c in VIDEO_CODECS:
        if c.key == key:
            return c
    return None
