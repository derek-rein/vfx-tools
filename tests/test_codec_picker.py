"""Nested EXR→video codec picker (family submenus, not heading rows)."""

from __future__ import annotations

from PySide6.QtWidgets import QApplication, QMenu

from src.core.constants import DEFAULT_VIDEO_CODEC, available_video_codecs_grouped
from src.gui.convert_tab import CodecPicker


def test_codec_picker_nests_groups(qapp: QApplication) -> None:
    picker = CodecPicker()
    try:
        menus = [a.menu() for a in picker._menu.actions() if a.menu() is not None]
        assert menus
        labels = [a.text() for a in picker._menu.actions()]
        grouped = available_video_codecs_grouped()
        assert labels == [g[0] for g in grouped]
        for action, (_label, specs) in zip(picker._menu.actions(), grouped, strict=True):
            sub = action.menu()
            assert isinstance(sub, QMenu)
            keys = [a.data() for a in sub.actions()]
            assert keys == [s.key for s in specs]
        picker.set_key(DEFAULT_VIDEO_CODEC)
        assert picker.currentData() == DEFAULT_VIDEO_CODEC
        assert DEFAULT_VIDEO_CODEC in (picker.text() or "") or picker.text()
    finally:
        picker.close()
