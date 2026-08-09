"""Timeline scrubber widget with Nuke-style pan/zoom on the X axis.

Adapted from the triton ``TimelineSlider`` for the slate-editor preview.
Supports:

- LMB drag — set/move the playhead
- MMB drag (or Alt+LMB) — pan visible range
- RMB drag — zoom (drag right to zoom in)
- Scroll wheel — zoom anchored at the cursor
- ``F`` key — fit the full frame range into view
- ``set_cached_frames(frames)`` to highlight cached frames in green
- ``set_marker_frames({frame: label})`` to mark special frames (e.g. the
  slate position); markers are drawn as a thin tinted band with a label.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QRect, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFontDatabase,
    QFontMetrics,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QResizeEvent,
    QWheelEvent,
)
from PySide6.QtWidgets import QWidget

# -- Palette ---------------------------------------------------------------
_BG = QColor(30, 30, 30)
_GROOVE = QColor(42, 42, 42)
_TICK_MAJOR = QColor(100, 100, 100)
_TICK_MINOR = QColor(60, 60, 60)
_LABEL_COLOR = QColor(140, 140, 140)
_CACHED = QColor(76, 175, 80, 180)
_MARKER = QColor(200, 120, 40, 110)
_MARKER_LABEL = QColor(220, 160, 90)
_PLAYHEAD = QColor(200, 120, 40)
_PLAYHEAD_LINE = QColor(220, 140, 50)

# -- Layout (absolute pixels from bottom) ----------------------------------
_MARGIN_LEFT = 6
_MARGIN_RIGHT = 6
_GROOVE_HEIGHT = 6
_CACHE_HEIGHT = 4
_BOTTOM_PAD = 4
_TICK_GAP = 2
_MAJOR_TICK_H = 8
_MINOR_TICK_H = 4
_LABEL_PAD = 2
_PLAYHEAD_WIDTH = 2
_HEAD_RADIUS = 5

# -- Zoom limits ------------------------------------------------------------
_ZOOM_MIN_FRAMES = 4
_ZOOM_SPEED = 0.003
_RMB_ZOOM_SPEED = 0.005

# Nice tick step candidates (frames)
_NICE_STEPS = (
    1,
    2,
    5,
    10,
    20,
    25,
    50,
    100,
    200,
    250,
    500,
    1000,
    2000,
    5000,
    10000,
)


class TimelineSlider(QWidget):
    """Custom-painted timeline with pan/zoom and a draggable playhead.

    Signals
    -------
    value_changed(int)
        Emitted while the user drags the playhead or clicks a position.
    """

    value_changed = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self._first: int = 1
        self._last: int = 100
        self._vis_start: float = 1.0
        self._vis_end: float = 100.0
        self._value: int = 1

        self._cached_frames: set[int] = set()
        self._marker_frames: dict[int, str] = {}

        self._dragging_playhead: bool = False
        self._panning: bool = False
        self._zooming: bool = False
        self._last_mouse_x: float = 0.0
        self._zoom_anchor_x: float = 0.0

        self._tick_font = QFontDatabase.systemFont(QFontDatabase.SystemFont.GeneralFont)
        self._tick_font.setPointSize(8)
        self._tick_fm = QFontMetrics(self._tick_font)
        self._font_h = self._tick_fm.height()
        self._font_ascent = self._tick_fm.ascent()

        self.setMinimumHeight(self._ideal_height())
        self.setMinimumWidth(120)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

    def _ideal_height(self) -> int:
        return (
            self._font_ascent
            + _LABEL_PAD
            + _MAJOR_TICK_H
            + _TICK_GAP
            + _GROOVE_HEIGHT
            + _BOTTOM_PAD
            + 4
        )

    # ---- Derived Y positions ----

    @property
    def _groove_top(self) -> int:
        return self.height() - _BOTTOM_PAD - _GROOVE_HEIGHT

    @property
    def _tick_bottom(self) -> int:
        return self._groove_top - _TICK_GAP

    # ---- Public API ----

    def set_range(self, first: int, last: int) -> None:
        self._first = first
        self._last = max(first, last)
        self._value = max(first, min(self._value, self._last))
        self._vis_start = float(first)
        self._vis_end = float(self._last)
        self.update()

    def set_value(self, v: int) -> None:
        v = max(self._first, min(v, self._last))
        if v != self._value:
            self._value = v
            self.update()

    def value(self) -> int:
        return self._value

    @property
    def first_frame(self) -> int:
        """Inclusive timeline start (lowest scrubbable frame)."""
        return self._first

    @property
    def last_frame(self) -> int:
        """Inclusive timeline end (highest scrubbable frame)."""
        return self._last

    @property
    def is_dragging_playhead(self) -> bool:
        """True while the user is dragging the playhead (not programmatic set)."""
        return self._dragging_playhead

    def set_cached_frames(self, frames: set[int]) -> None:
        self._cached_frames = set(frames)
        self.update()

    def set_marker_frames(self, markers: dict[int, str]) -> None:
        """Set frames that should be drawn as a tinted band with a label."""
        self._marker_frames = dict(markers)
        self.update()

    # ---- Geometry helpers ----

    @property
    def _track_left(self) -> int:
        return _MARGIN_LEFT

    @property
    def _track_right(self) -> int:
        return self.width() - _MARGIN_RIGHT

    @property
    def _track_width(self) -> int:
        return max(1, self._track_right - self._track_left)

    @property
    def _vis_span(self) -> float:
        return max(0.001, self._vis_end - self._vis_start)

    def _frame_to_x(self, frame: float) -> float:
        frac = (frame - self._vis_start) / self._vis_span
        return self._track_left + frac * self._track_width

    def _x_to_frame(self, x: float) -> float:
        frac = (x - self._track_left) / self._track_width
        return self._vis_start + frac * self._vis_span

    def _x_to_frame_clamped(self, x: float) -> int:
        f = self._x_to_frame(x)
        return max(self._first, min(self._last, round(f)))

    # ---- Visible-range manipulation ----

    def _clamp_visible(self) -> None:
        total = float(self._last - self._first)
        span = self._vis_end - self._vis_start

        min_span = min(float(_ZOOM_MIN_FRAMES), total + 1.0)
        if span < min_span:
            mid = (self._vis_start + self._vis_end) * 0.5
            self._vis_start = mid - min_span * 0.5
            self._vis_end = mid + min_span * 0.5

        max_span = total + 2.0
        if span > max_span:
            mid = (self._vis_start + self._vis_end) * 0.5
            self._vis_start = mid - max_span * 0.5
            self._vis_end = mid + max_span * 0.5

        if self._vis_start < self._first - 1.0:
            shift = (self._first - 1.0) - self._vis_start
            self._vis_start += shift
            self._vis_end += shift
        if self._vis_end > self._last + 1.0:
            shift = self._vis_end - (self._last + 1.0)
            self._vis_start -= shift
            self._vis_end -= shift

    def _zoom_at(self, factor: float, anchor_x: float) -> None:
        cur_span = self._vis_end - self._vis_start
        total = float(self._last - self._first)
        min_span = min(float(_ZOOM_MIN_FRAMES), total + 1.0)
        max_span = total + 2.0

        target = cur_span * factor
        if target < min_span:
            if cur_span <= min_span:
                return
            factor = min_span / cur_span
        elif target > max_span:
            if cur_span >= max_span:
                return
            factor = max_span / cur_span

        anchor_frame = self._x_to_frame(anchor_x)
        self._vis_start = anchor_frame + (self._vis_start - anchor_frame) * factor
        self._vis_end = anchor_frame + (self._vis_end - anchor_frame) * factor
        self._clamp_visible()
        self.update()

    def _pan_by_pixels(self, dx: float) -> None:
        frames_per_px = self._vis_span / self._track_width
        shift = -dx * frames_per_px
        self._vis_start += shift
        self._vis_end += shift
        self._clamp_visible()
        self.update()

    def fit_range(self) -> None:
        self._vis_start = float(self._first)
        self._vis_end = float(self._last)
        self._clamp_visible()
        self.update()

    # ---- Tick spacing ----

    def _tick_step(self) -> tuple[int, int]:
        px_per_frame = self._track_width / max(1.0, self._vis_span)
        label_w = self._tick_fm.horizontalAdvance("00000") + 12
        for major in _NICE_STEPS:
            if major * px_per_frame >= label_w:
                minor = max(1, major // 5) if major >= 5 else 1
                return major, minor
        return int(self._vis_span), max(1, int(self._vis_span) // 10)

    # ---- Paint ----

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w = self.width()
        h = self.height()

        p.fillRect(0, 0, w, h, _BG)

        gt = self._groove_top
        groove_rect = QRect(self._track_left, gt, self._track_width, _GROOVE_HEIGHT)
        p.setClipRect(QRectF(self._track_left, 0, self._track_width, h))
        p.fillRect(groove_rect, _GROOVE)

        self._paint_markers(p, gt)
        self._paint_cache(p, gt)
        self._paint_ticks(p)

        p.setClipping(False)
        self._paint_playhead(p, gt)
        p.end()

    def _paint_markers(self, p: QPainter, groove_top: int) -> None:
        if not self._marker_frames:
            return
        ppf = self._track_width / max(1.0, self._vis_span)
        band_w = max(2, int(round(ppf)))
        vis_first = int(math.floor(self._vis_start)) - 1
        vis_last = int(math.ceil(self._vis_end)) + 1
        h = self.height()
        p.setFont(self._tick_font)
        for frame, label in self._marker_frames.items():
            if frame < vis_first or frame > vis_last:
                continue
            x = int(self._frame_to_x(frame))
            p.fillRect(x - band_w // 2, 0, band_w, h, _MARKER)
            if label:
                lw = self._tick_fm.horizontalAdvance(label)
                p.setPen(_MARKER_LABEL)
                lx = max(self._track_left, min(self._track_right - lw, x - lw // 2))
                p.drawText(lx, self._font_ascent + 1, label)

    def _paint_cache(self, p: QPainter, groove_top: int) -> None:
        if not self._cached_frames:
            return
        ppf = self._track_width / max(1.0, self._vis_span)
        cache_y = groove_top + _GROOVE_HEIGHT - _CACHE_HEIGHT
        vis_first = int(math.floor(self._vis_start))
        vis_last = int(math.ceil(self._vis_end))

        if ppf >= 1.0:
            for frame in self._cached_frames:
                if frame < vis_first or frame > vis_last:
                    continue
                x = int(self._frame_to_x(frame))
                fw = max(1, int(ppf))
                p.fillRect(x, cache_y, fw, _CACHE_HEIGHT, _CACHED)
        else:
            tw = self._track_width
            buckets = bytearray(tw)
            for frame in self._cached_frames:
                if frame < vis_first or frame > vis_last:
                    continue
                col = int(self._frame_to_x(frame)) - self._track_left
                col = max(0, min(col, tw - 1))
                buckets[col] = 1
            for x, filled in enumerate(buckets):
                if filled:
                    p.fillRect(self._track_left + x, cache_y, 1, _CACHE_HEIGHT, _CACHED)

    def _paint_ticks(self, p: QPainter) -> None:
        major_step, minor_step = self._tick_step()
        p.setFont(self._tick_font)
        tb = self._tick_bottom
        vis_first = int(math.floor(self._vis_start)) - 1
        vis_last = int(math.ceil(self._vis_end)) + 1

        p.setPen(QPen(_TICK_MINOR, 1))
        f = vis_first - (vis_first % minor_step)
        while f <= vis_last:
            if self._first <= f <= self._last:
                x = int(self._frame_to_x(f))
                p.drawLine(x, tb - _MINOR_TICK_H, x, tb)
            f += minor_step

        pen_major = QPen(_TICK_MAJOR, 1)
        f = vis_first - (vis_first % major_step)
        while f <= vis_last:
            if self._first <= f <= self._last:
                x = int(self._frame_to_x(f))
                p.setPen(pen_major)
                p.drawLine(x, tb - _MAJOR_TICK_H, x, tb)
                label = str(f)
                lw = self._tick_fm.horizontalAdvance(label)
                p.setPen(QPen(_LABEL_COLOR))
                p.drawText(x - lw // 2, tb - _MAJOR_TICK_H - _LABEL_PAD, label)
            f += major_step

    def _paint_playhead(self, p: QPainter, groove_top: int) -> None:
        x = int(self._frame_to_x(self._value))
        groove_bottom = groove_top + _GROOVE_HEIGHT
        p.setPen(QPen(_PLAYHEAD_LINE, _PLAYHEAD_WIDTH))
        p.drawLine(x, self._tick_bottom - _MAJOR_TICK_H, x, groove_bottom)
        p.setBrush(_PLAYHEAD)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(
            x - _HEAD_RADIUS,
            groove_top - _HEAD_RADIUS,
            _HEAD_RADIUS * 2,
            _HEAD_RADIUS * 2,
        )

    # ---- Mouse / key interaction ----

    def mousePressEvent(self, event: QMouseEvent) -> None:
        btn = event.button()
        mods = event.modifiers()
        if btn == Qt.MouseButton.MiddleButton or (
            btn == Qt.MouseButton.LeftButton and mods & Qt.KeyboardModifier.AltModifier
        ):
            self._panning = True
            self._last_mouse_x = event.position().x()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if btn == Qt.MouseButton.RightButton:
            self._zooming = True
            self._last_mouse_x = event.position().x()
            self._zoom_anchor_x = event.position().x()
            event.accept()
            return
        if btn == Qt.MouseButton.LeftButton:
            self._dragging_playhead = True
            self._set_from_mouse(event.position().x())
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        x = event.position().x()
        if self._panning:
            dx = x - self._last_mouse_x
            self._last_mouse_x = x
            self._pan_by_pixels(dx)
            event.accept()
            return
        if self._zooming:
            dx = x - self._last_mouse_x
            self._last_mouse_x = x
            factor = max(0.5, min(2.0, 1.0 - dx * _RMB_ZOOM_SPEED))
            self._zoom_at(factor, self._zoom_anchor_x)
            event.accept()
            return
        if self._dragging_playhead:
            self._set_from_mouse(x)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        btn = event.button()
        if btn in (Qt.MouseButton.MiddleButton, Qt.MouseButton.LeftButton) and self._panning:
            self._panning = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            event.accept()
            return
        if btn == Qt.MouseButton.RightButton and self._zooming:
            self._zooming = False
            event.accept()
            return
        if btn == Qt.MouseButton.LeftButton and self._dragging_playhead:
            self._dragging_playhead = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y()
        if delta == 0:
            event.ignore()
            return
        factor = max(0.5, min(2.0, 1.0 - delta * _ZOOM_SPEED))
        self._zoom_at(factor, event.position().x())
        event.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_F and not event.modifiers():
            self.fit_range()
            event.accept()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.update()

    def _set_from_mouse(self, x: float) -> None:
        frame = self._x_to_frame_clamped(x)
        if frame != self._value:
            self._value = frame
            self.update()
            self.value_changed.emit(self._value)
