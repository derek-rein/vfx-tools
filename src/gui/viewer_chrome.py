"""Shared Nuke-style viewer HUD (format label under the frame)."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QColor, QFont, QPainter

_FORMAT_HUD_PX = 11
_FORMAT_HUD_GAP = 4
_FORMAT_HUD_COLOR = QColor("#dddddd")


def format_label_baseline(
    frame: QRectF,
    text_width: float,
    ascent: float,
    *,
    gap: float = _FORMAT_HUD_GAP,
) -> QPointF:
    """Right-justified under the frame's bottom-right corner (text baseline)."""
    return QPointF(frame.right() - text_width, frame.bottom() + gap + ascent)


def paint_format_label(painter: QPainter, frame: QRectF, text: str) -> None:
    if not text:
        return
    font = QFont(painter.font())
    font.setPixelSize(_FORMAT_HUD_PX)
    painter.setFont(font)
    painter.setPen(_FORMAT_HUD_COLOR)
    fm = painter.fontMetrics()
    pos = format_label_baseline(frame, fm.horizontalAdvance(text), fm.ascent())
    painter.drawText(pos, text)
