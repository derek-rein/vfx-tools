"""Slate thumbnail frame preference helpers."""

from __future__ import annotations

from PySide6.QtCore import QSettings

from src.gui.preferences import (
    THUMBNAIL_FRAME_FIRST,
    THUMBNAIL_FRAME_LAST,
    THUMBNAIL_FRAME_MID,
    pick_thumbnail_index,
    set_thumbnail_frame_choice,
    thumbnail_frame_choice,
)


def test_pick_thumbnail_index():
    assert pick_thumbnail_index(10, THUMBNAIL_FRAME_FIRST) == 0
    assert pick_thumbnail_index(10, THUMBNAIL_FRAME_MID) == 5
    assert pick_thumbnail_index(10, THUMBNAIL_FRAME_LAST) == 9
    assert pick_thumbnail_index(1, THUMBNAIL_FRAME_LAST) == 0
    assert pick_thumbnail_index(0, THUMBNAIL_FRAME_MID) == 0


def test_thumbnail_frame_choice_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    s = QSettings(str(tmp_path / "t.ini"), QSettings.Format.IniFormat)
    assert thumbnail_frame_choice(s) == THUMBNAIL_FRAME_MID
    set_thumbnail_frame_choice(s, THUMBNAIL_FRAME_LAST)
    assert thumbnail_frame_choice(s) == THUMBNAIL_FRAME_LAST
    set_thumbnail_frame_choice(s, 99)  # invalid → mid
    assert thumbnail_frame_choice(s) == THUMBNAIL_FRAME_MID
