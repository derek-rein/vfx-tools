"""Smoke tests for SequencePlayer (offscreen Qt)."""

from __future__ import annotations

from PySide6.QtCore import QSettings

from src.gui.player.sequence_player import SequencePlayer


def test_sequence_player_constructs_offscreen(qapp, settings: QSettings) -> None:
    player = SequencePlayer(settings=settings, prefer_gpu=False, show_cache_ui=False)
    try:
        assert player is not None
        assert player._current_frame == 1
        assert player._fps == 24.0
        # Empty media: set_frame should not crash.
        player.set_frame(1)
    finally:
        player.close()
        player.deleteLater()
        qapp.processEvents()
