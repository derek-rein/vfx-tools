"""R3D decode modes, metadata keys, and end-user redistributable notice."""

from __future__ import annotations

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

# Relative linear scale of each decode mode vs full resolution.
DECODE_MODE_SCALE: dict[int, float] = {
    DECODE_FULL_PREMIUM: 1.0,
    DECODE_HALF_PREMIUM: 0.5,
    DECODE_HALF_GOOD: 0.5,
    DECODE_QUARTER_GOOD: 0.25,
    DECODE_EIGHTH_GOOD: 0.125,
    DECODE_SIXTEENTH_GOOD: 0.0625,
}

# Clip-level RMD keys we copy into EXR attributes (when present).
CLIP_META_KEYS: tuple[str, ...] = (
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

# Required end-user notice when RED Redistributable libraries ship with the app.
RED_REDISTRIBUTABLE_NOTICE = """\
RED R3D / N-RAW decoding uses proprietary software from RED.COM, LLC / Nikon
(the "R3D SDK"). When this application includes RED Redistributable dynamic
libraries, those libraries remain the property of RED and are licensed to you
only under the R3D SDK License Agreement and the following conditions:

* You may use the R3D functionality solely as integrated in this application
  to decode R3D / N-RAW media for your own projects.
* You may not reverse engineer, decompile, disassemble, or otherwise attempt
  to derive the source code or file formats of the RED libraries or SDK.
* You may not redistribute the RED libraries separately, modify them, or
  place them in a shared system location for use by other software.
* You may not claim that this product is certified by RED or use RED
  trademarks without written permission from RED.
* THE RED LIBRARIES AND RELATED MATERIALS ARE PROVIDED "AS IS" WITHOUT
  WARRANTY OF ANY KIND. TO THE MAXIMUM EXTENT PERMITTED BY LAW, RED AND ITS
  LICENSORS DISCLAIM ALL WARRANTIES AND LIMIT LIABILITY AS SET OUT IN THE
  R3D SDK LICENSE AGREEMENT (INCLUDING AN AGGREGATE LIABILITY CAP).

Obtain the current license from the official R3D SDK package. For questions:
RED-r3dsdk@nikon.com
"""


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


def scale_for_decode_mode(mode: int) -> float:
    """Linear resolution scale for *mode* (1.0 = full)."""
    return DECODE_MODE_SCALE.get(int(mode), 1.0)
