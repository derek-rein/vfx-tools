"""Fast browser thumbnails: stills via OpenImageIO, video via PyAV (no OCIO).

Image-sequence grid: decode first frame, box-filter downscale, optional cheap
Rec.709 OETF for scene-referred formats. Video grid: first decoded frame via
PyAV. Returns uint8 RGB for ``QImage``. Safe off the GUI thread.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import OpenImageIO as oiio

from ..core.constants import is_scene_referred_image_ext

# Default max edge for browser icons (speed over fidelity).
DEFAULT_THUMB_EDGE = 160


def _downscale_uint8_rgb(rgb: np.ndarray, max_edge: int) -> np.ndarray:
    """Box-filter *rgb* (H,W,3) float or uint8 to max edge; return uint8 RGB."""
    arr = np.asarray(rgb)
    if arr.ndim == 2:
        arr = np.repeat(arr[:, :, np.newaxis], 3, axis=2)
    if arr.shape[2] > 3:
        arr = arr[:, :, :3]
    if arr.dtype != np.float32 and arr.dtype != np.float64:
        arr = arr.astype(np.float32)
        if arr.max() > 1.5:
            arr = arr * (1.0 / 255.0)
    h, w = int(arr.shape[0]), int(arr.shape[1])
    if h <= 0 or w <= 0:
        return np.zeros((1, 1, 3), dtype=np.uint8)
    edge = max(16, int(max_edge))
    scale = min(1.0, edge / float(max(w, h)))
    tw = max(1, int(round(w * scale)))
    th = max(1, int(round(h * scale)))
    if tw < w or th < h:
        # Nearest-ish box via simple stride sample (fast ID thumbs).
        ys = (np.linspace(0, h - 1, th)).astype(np.int32)
        xs = (np.linspace(0, w - 1, tw)).astype(np.int32)
        arr = arr[ys][:, xs]
    return np.clip(arr * 255.0 + 0.5, 0, 255).astype(np.uint8)


def load_video_thumbnail_rgb(
    path: str,
    *,
    max_edge: int = DEFAULT_THUMB_EDGE,
) -> np.ndarray | None:
    """Return uint8 RGB thumbnail from the first video frame, or ``None``."""
    if not path or not Path(path).is_file():
        return None

    # R3D / N-RAW: sixteenth-res SDK decode (fast ID thumbs; not full premium).
    try:
        from ..core.r3d import (
            DECODE_THUMBNAIL,
            R3DClip,
            is_r3d_path,
        )
        from ..core.r3d import (
            is_available as r3d_available,
        )

        if is_r3d_path(path) and r3d_available():
            with R3DClip(path) as clip:
                rgb = clip.decode_frame(0, mode=DECODE_THUMBNAIL)
            # Log3G10 is not linear display; cheap OETF so thumbs aren't crushed.
            disp = np.clip(np.asarray(rgb, dtype=np.float32), 0.0, 1.0)
            disp = np.power(disp, 1.0 / 2.2)
            return _downscale_uint8_rgb(disp, max_edge)
    except Exception:
        pass

    try:
        import av

        container = av.open(path)
        try:
            from ..core.video import av_frame_to_rgb_ndarray

            for frame in container.decode(video=0):
                rgb = av_frame_to_rgb_ndarray(frame, pix_fmt="rgb24")
                return _downscale_uint8_rgb(rgb, max_edge)
        finally:
            container.close()
    except Exception:
        return None
    return None


def load_browser_thumbnail_rgb(
    path: str,
    *,
    max_edge: int = DEFAULT_THUMB_EDGE,
) -> np.ndarray | None:
    """Return uint8 RGB ``(H, W, 3)`` thumbnail, or ``None`` on failure.

    Scene-referred files (EXR, DPX) get a fast Rec.709-ish OETF so linear plates
    are visible; display stills (PNG/JPEG/WebP) are clipped to 0–1.
    """
    if not path or not Path(path).is_file():
        return None
    try:
        buf = oiio.ImageBuf(path)
        if buf.has_error:
            return None
        spec = buf.spec()
        src_w = spec.full_width if spec.full_width > 0 else spec.width
        src_h = spec.full_height if spec.full_height > 0 else spec.height
        if src_w <= 0 or src_h <= 0:
            return None

        nchan = min(int(spec.nchannels), 3)
        if nchan < 1:
            return None

        edge = max(16, int(max_edge))
        scale = min(1.0, edge / float(max(src_w, src_h)))
        tw = max(1, int(round(src_w * scale)))
        th = max(1, int(round(src_h * scale)))

        # Box filter: fastest reasonable downscale for identification thumbs.
        if tw < src_w or th < src_h:
            dst_spec = oiio.ImageSpec(tw, th, nchan, oiio.FLOAT)
            out = oiio.ImageBuf(dst_spec)
            ok = oiio.ImageBufAlgo.resize(out, buf, filtername="box")
            if not ok or out.has_error:
                return None
            work = out
            rw, rh = tw, th
        else:
            work = buf
            rw, rh = src_w, src_h

        # Prefer display window when reading full-res.
        if work is buf and spec.full_width > 0 and spec.full_height > 0:
            roi = oiio.ROI(
                spec.full_x,
                spec.full_x + src_w,
                spec.full_y,
                spec.full_y + src_h,
                0,
                1,
                0,
                nchan,
            )
        else:
            roi = oiio.ROI(0, rw, 0, rh, 0, 1, 0, nchan)
        pixels = np.ascontiguousarray(work.get_pixels(oiio.FLOAT, roi), dtype=np.float32)
        if pixels is None or pixels.size == 0:
            return None

        if pixels.ndim == 2:
            rgb = np.repeat(pixels[:, :, np.newaxis], 3, axis=2)
        elif pixels.shape[2] == 1:
            rgb = np.repeat(pixels, 3, axis=2)
        else:
            rgb = pixels[:, :, :3]
            if rgb.shape[2] < 3:
                pad = np.repeat(rgb[:, :, :1], 3 - rgb.shape[2], axis=2)
                rgb = np.concatenate([rgb, pad], axis=2)

        if is_scene_referred_image_ext(Path(path).suffix):
            rgb = _linear_to_display_u8_prep(rgb)
        else:
            rgb = np.clip(rgb, 0.0, 1.0)

        return np.clip(rgb * 255.0, 0.0, 255.0).astype(np.uint8)
    except Exception:
        return None


def _linear_to_display_u8_prep(rgb: np.ndarray) -> np.ndarray:
    """Cheap linear → display curve (Rec.709/sRGB OETF), soft-clip highlights."""
    # Soft highlight roll-off so >1.0 linear plates still read.
    lin = np.clip(rgb, 0.0, None)
    # Reinhard-ish compress then OETF — fast identify, not grade-accurate.
    compressed = lin / (1.0 + lin)
    srgb = np.where(
        compressed <= 0.0031308,
        compressed * 12.92,
        1.055 * np.power(compressed, 1.0 / 2.4) - 0.055,
    )
    return np.clip(srgb, 0.0, 1.0)
