"""Watermark overlay renderer using QPainter.

A watermark is a single-line of text (typically ``FOR REVIEW ONLY`` plus
user / date) drawn diagonally across the centre of every frame.  It's
drawn *post*-OCIO display transform and *post* gain/gamma so the text
stays legible regardless of the viewer state.

Public API mirrors :mod:`burnin`:

- :func:`render_watermark_overlay` produces a uint8 RGBA image.
- :func:`composite_watermark` alpha-overs that onto a uint16 frame.
- :func:`watermark_params_from_slate` derives default params from slate data.
"""

from __future__ import annotations

import datetime
import math
from typing import Any

import numpy as np
from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontMetricsF, QImage, QPainter

# Default values surface on every preview/conversion when the user hasn't
# explicitly set anything yet.  Anglе is a typical 30° upward sweep across
# the frame; opacity / size are tuned to match the muted look of common
# review stamps.
_DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "text": "FOR REVIEW ONLY",
    "opacity": 40,
    "size_pct": 9.0,
    "angle": 30.0,
    "tiled": True,
}


def watermark_params_from_slate(slate_data: dict | None = None) -> dict:
    """Build a default watermark param dict, optionally seeded from slate data.

    Adds ``{user}@{date}`` as a second line when an artist is set, so a
    fresh watermark says something useful out of the box.
    """
    params = dict(_DEFAULTS)
    if slate_data:
        artist = (slate_data.get("artist") or "").strip()
        if artist and artist != "\u2014":
            params["text"] = (
                f"FOR REVIEW ONLY  \u00b7  {artist}  \u00b7  {datetime.date.today().isoformat()}"
            )
    return params


def render_watermark_overlay(
    width: int,
    height: int,
    params: dict | None = None,
) -> np.ndarray:
    """Paint the watermark and return a uint8 RGBA buffer ``(h, w, 4)``.

    Returns a fully-transparent buffer when watermarking is disabled or the
    text is blank — callers can blindly composite the result.
    """
    p = dict(_DEFAULTS)
    if params:
        p.update(params)

    img = QImage(int(width), int(height), QImage.Format.Format_RGBA8888)
    img.fill(QColor(0, 0, 0, 0))

    if p.get("enabled") and (p.get("text") or "").strip():
        painter = QPainter(img)
        try:
            _paint_watermark(painter, int(width), int(height), p)
        finally:
            painter.end()

    img = img.convertToFormat(QImage.Format.Format_RGBA8888)
    ptr = img.constBits()
    return np.frombuffer(ptr, dtype=np.uint8).reshape((int(height), int(width), 4)).copy()


def composite_watermark(
    frame_u16: np.ndarray,
    overlay_u8: np.ndarray,
) -> np.ndarray:
    """Alpha-over a uint8 RGBA watermark onto a uint16 RGB frame.

    Uses the same OIIO ``ImageBufAlgo.over`` path as :mod:`burnin` so the
    blend matches what the conversion pipeline produces.
    """
    import OpenImageIO as oiio

    h, w = frame_u16.shape[:2]

    fg_spec = oiio.ImageSpec(w, h, 4, oiio.FLOAT)
    fg_buf = oiio.ImageBuf(fg_spec)
    fg_buf.set_pixels(oiio.ROI.All, overlay_u8.astype(np.float32) / 255.0)

    bg_spec = oiio.ImageSpec(w, h, 4, oiio.FLOAT)
    bg_buf = oiio.ImageBuf(bg_spec)
    frame_f = frame_u16.astype(np.float32) / 65535.0
    rgba = np.concatenate([frame_f, np.ones((h, w, 1), dtype=np.float32)], axis=-1)
    bg_buf.set_pixels(oiio.ROI.All, rgba)

    result_buf = oiio.ImageBufAlgo.over(fg_buf, bg_buf)
    pixels = result_buf.get_pixels(oiio.FLOAT)
    rgb = pixels[:, :, :3]
    return np.clip(rgb * 65535.0, 0.0, 65535.0).astype(np.uint16)


# ---------------------------------------------------------------------------
# Painter
# ---------------------------------------------------------------------------


def _watermark_font(px: float) -> QFont:
    f = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
    f.setPixelSize(max(1, int(round(px))))
    f.setBold(True)
    f.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 110.0)
    return f


def _paint_watermark(p: QPainter, width: int, height: int, params: dict) -> None:
    """Paint the watermark text on the canvas.

    Draws a single centred line by default, or a repeating diagonal grid that
    covers the whole frame when ``tiled`` is set.  Tiling stays inside the one
    overlay buffer — the text is repeatedly stamped via QPainter rather than
    materialising a large pre-tiled image, so memory stays flat.
    """
    text = str(params.get("text") or "").strip()
    if not text:
        return

    p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)

    size_pct = float(params.get("size_pct") or _DEFAULTS["size_pct"])
    angle = float(params.get("angle") or 0.0)
    opacity = max(0.0, min(1.0, float(params.get("opacity") or 0) / 100.0))

    px = max(8.0, height * size_pct / 100.0)
    font = _watermark_font(px)
    p.setFont(font)
    fm = QFontMetricsF(font)

    # Translate to the centre, rotate, then draw with the text origin at (0, 0)
    # so the line is naturally centred regardless of text width.
    p.translate(width / 2.0, height / 2.0)
    p.rotate(angle)

    text_w = fm.horizontalAdvance(text)
    half_w = text_w / 2.0

    if params.get("tiled"):
        _paint_tiled(p, width, height, text, text_w, fm, px, opacity)
        p.resetTransform()
        return

    # Subtle dark backing makes the text legible on bright frames; alpha is
    # tied to the user's opacity so a low-opacity watermark stays light.
    pad_x = px * 0.6
    pad_y = px * 0.25
    bg_rect = (
        -half_w - pad_x,
        -fm.ascent() - pad_y,
        text_w + 2 * pad_x,
        fm.height() + 2 * pad_y,
    )
    p.fillRect(*bg_rect, QColor(0, 0, 0, int(round(opacity * 130))))

    fg = QColor(255, 255, 255, int(round(opacity * 255)))
    p.setPen(fg)
    p.drawText(QPointF(-half_w, 0), text)
    p.resetTransform()


def _paint_tiled(
    p: QPainter,
    width: int,
    height: int,
    text: str,
    text_w: float,
    fm: QFontMetricsF,
    px: float,
    opacity: float,
) -> None:
    """Stamp ``text`` in a brick-offset grid covering the whole rotated frame.

    The painter is already translated to the centre and rotated.  We tile over
    a square that spans the frame's half-diagonal in every direction so the
    pattern fully covers the image at any rotation angle, then rely on the
    painter clip to discard anything outside the canvas.
    """
    if text_w <= 0:
        return

    fg = QColor(255, 255, 255, int(round(opacity * 255)))
    p.setPen(fg)

    radius = 0.5 * math.hypot(width, height)
    step_x = text_w + px * 1.6
    step_y = fm.height() + px * 1.1

    row = 0
    y = -radius
    while y <= radius:
        # Offset alternate rows by half a tile for a less mechanical pattern.
        x_offset = (step_x / 2.0) if (row % 2) else 0.0
        x = -radius - step_x + x_offset
        while x <= radius + step_x:
            p.drawText(QPointF(x - text_w / 2.0, y), text)
            x += step_x
        y += step_y
        row += 1


__all__ = [
    "composite_watermark",
    "render_watermark_overlay",
    "watermark_params_from_slate",
]
