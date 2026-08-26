"""Sequence preview canvas chrome."""

from __future__ import annotations

from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from src.gui.ocio_gpu_plane import OcioGpuImagePlane
from src.gui.player.preview_view import ImagePreviewView
from src.gui.viewer_chrome import format_label_baseline


def test_preview_canvas_background_is_black(qapp: QApplication) -> None:
    view = ImagePreviewView()
    try:
        assert view.backgroundBrush().color() == QColor("#000000")
        assert view.scene().backgroundBrush().color() == QColor("#000000")
    finally:
        view.close()


def test_format_label_sits_under_frame_bottom_right() -> None:
    frame = QRectF(10.0, 20.0, 200.0, 100.0)
    pos = format_label_baseline(frame, text_width=50.0, ascent=10.0, gap=4.0)
    assert pos.x() == 160.0
    assert pos.y() == 134.0


def test_preview_format_box_and_hud(qapp: QApplication) -> None:
    view = ImagePreviewView()
    try:
        view.set_frame_size(1920, 1080)
        assert view.format_text() == "1920 \u00d7 1080"
        assert view._border is not None
        assert view._border.pen().color() == QColor("#ffffff")
        assert view._border.pen().isCosmetic()
        view.set_frame_size(3840, 2160)
        assert view.format_text() == "3840 \u00d7 2160"
        assert view._border.rect() == view._frame_rect
    finally:
        view.close()


def test_gpu_plane_format_hud_text(qapp: QApplication) -> None:
    plane = OcioGpuImagePlane()
    try:
        assert plane.format_text() == ""
        plane.set_format(2048, 1152)
        assert plane.format_text() == "2048 \u00d7 1152"
    finally:
        plane.close()
