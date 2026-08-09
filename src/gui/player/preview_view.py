"""CPU pan/zoom image preview (fallback when GPU OCIO is unavailable)."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QWheelEvent
from PySide6.QtWidgets import QGraphicsScene, QGraphicsView

ZOOM_MIN = 0.05
ZOOM_MAX = 5.0


class ImagePreviewView(QGraphicsView):
    """Single QGraphicsView with Nuke-style pan/zoom for sequence playback.

    Controls:
      - LMB / MMB drag: pan
      - Scroll wheel: zoom to cursor
      - RMB drag: zoom (horizontal, anchored at press)
      - F key: fit in view
    """

    WHEEL_SCALE_FACTOR = 1.0 / 4.0

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)

        # Nearest/raw pixels when zooming (matches GPU path).
        self.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setBackgroundBrush(QBrush(QColor("#323232")))

        self._scene.setSceneRect(-1e6, -1e6, 2e6, 2e6)

        self._frame_rect = QRectF(0, 0, 1920, 1080)

        self._panning = False
        self._zooming = False
        self._last_pos: QPointF | None = None
        self._zoom_anchor_view: QPointF | None = None

    def set_frame_size(self, w: int, h: int) -> None:
        """Set the logical fit rect (maps source aspect to a 1080-tall canvas)."""
        preview_h = 1080
        preview_w = int(preview_h * w / max(h, 1))
        self._frame_rect = QRectF(0, 0, preview_w, preview_h)

    # Back-compat alias used by older slate code.
    def set_slate_size(self, w: int, h: int) -> None:
        self.set_frame_size(w, h)

    def fit_in_view(self) -> None:
        self.fitInView(self._frame_rect, Qt.AspectRatioMode.KeepAspectRatio)

    def _scale_at(self, factor: float, view_anchor: QPointF) -> None:
        cur = self.transform().m11()
        target = max(ZOOM_MIN, min(ZOOM_MAX, cur * factor))
        s = target / cur
        if abs(s - 1.0) < 1e-7:
            return
        scene_pt = self.mapToScene(view_anchor.toPoint())
        cx, cy = scene_pt.x(), scene_pt.y()
        t = self.transform()
        t.translate(cx, cy)
        t.scale(s, s)
        t.translate(-cx, -cy)
        self.setTransform(t)

    def _translate_view(self, dx: float, dy: float) -> None:
        t = self.transform()
        s = t.m11()
        t.translate(dx / s, dy / s)
        self.setTransform(t)

    def mousePressEvent(self, event):  # noqa: N802
        btn = event.button()
        if btn in (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton):
            self._panning = True
            self._last_pos = event.position()
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if btn == Qt.MouseButton.RightButton:
            self._zooming = True
            self._last_pos = event.position()
            self._zoom_anchor_view = event.position()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):  # noqa: N802
        pos = event.position()
        if self._panning and self._last_pos is not None:
            delta = pos - self._last_pos
            self._last_pos = pos
            self._translate_view(delta.x(), delta.y())
            event.accept()
            return
        if self._zooming and self._last_pos is not None:
            dx = pos.x() - self._last_pos.x()
            self._last_pos = pos
            s = 1.02 ** (dx * 0.5)
            anchor = self._zoom_anchor_view
            if anchor is not None:
                self._scale_at(s, anchor)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802
        btn = event.button()
        if btn in (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton) and self._panning:
            self._panning = False
            self._last_pos = None
            self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
            event.accept()
            return
        if btn == Qt.MouseButton.RightButton and self._zooming:
            self._zooming = False
            self._last_pos = None
            self._zoom_anchor_view = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event: QWheelEvent):  # noqa: N802
        delta = event.angleDelta().y()
        if delta == 0:
            return
        s = 1.02 ** (delta * self.WHEEL_SCALE_FACTOR)
        self._scale_at(s, event.position())
        event.accept()

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() == Qt.Key.Key_F and not event.modifiers():
            self.fit_in_view()
            event.accept()
            return
        super().keyPressEvent(event)


__all__ = ["ImagePreviewView", "ZOOM_MAX", "ZOOM_MIN"]
