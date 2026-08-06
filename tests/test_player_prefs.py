"""Unit tests for player preference helpers (no GUI launch)."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from PySide6.QtCore import QSettings

from src.gui.preferences import (
    PLAYER_MODE_CUSTOM,
    PLAYER_MODE_SYSTEM,
    open_video_with_player,
    player_mode,
    player_path,
    set_player_prefs,
)


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    s = QSettings(str(tmp_path / "prefs.ini"), QSettings.Format.IniFormat)
    return s


def test_defaults_are_system(settings):
    assert player_mode(settings) == PLAYER_MODE_SYSTEM
    assert player_path(settings) == ""


def test_set_and_load_custom(settings):
    set_player_prefs(settings, PLAYER_MODE_CUSTOM, "  /Applications/IINA.app  ")
    assert player_mode(settings) == PLAYER_MODE_CUSTOM
    assert player_path(settings) == "/Applications/IINA.app"


def test_invalid_mode_falls_back_to_system(settings):
    settings.setValue("player/mode", "nope")
    assert player_mode(settings) == PLAYER_MODE_SYSTEM


def test_open_system_default(settings, tmp_path):
    media = tmp_path / "out.mov"
    media.write_bytes(b"x")
    set_player_prefs(settings, PLAYER_MODE_SYSTEM, "")
    with patch("src.gui.preferences.QDesktopServices.openUrl") as open_url:
        msg = open_video_with_player(media, settings)
    assert "system default" in msg
    open_url.assert_called_once()


def test_open_custom_cli(settings, tmp_path):
    media = tmp_path / "out.mov"
    media.write_bytes(b"x")
    player = tmp_path / "fakeplayer"
    player.write_text("#!/bin/sh\n")
    player.chmod(0o755)
    set_player_prefs(settings, PLAYER_MODE_CUSTOM, str(player))
    with patch("src.gui.preferences.subprocess.Popen") as popen:
        msg = open_video_with_player(media, settings)
    assert "fakeplayer" in msg
    args = popen.call_args[0][0]
    assert args[0] == str(player)
    assert args[1] == str(media)


def test_open_custom_missing_falls_back(settings, tmp_path):
    media = tmp_path / "out.mov"
    media.write_bytes(b"x")
    set_player_prefs(settings, PLAYER_MODE_CUSTOM, str(tmp_path / "missing-player"))
    with patch("src.gui.preferences.QDesktopServices.openUrl") as open_url:
        msg = open_video_with_player(media, settings)
    assert "system default" in msg
    open_url.assert_called_once()


def test_open_missing_media_raises(settings, tmp_path):
    set_player_prefs(settings, PLAYER_MODE_SYSTEM, "")
    with pytest.raises(FileNotFoundError):
        open_video_with_player(tmp_path / "nope.mov", settings)


@pytest.mark.skipif(__import__("sys").platform != "darwin", reason="macOS .app launch")
def test_open_custom_app_bundle(settings, tmp_path):
    media = tmp_path / "out.mov"
    media.write_bytes(b"x")
    app = tmp_path / "FakePlayer.app"
    (app / "Contents").mkdir(parents=True)
    set_player_prefs(settings, PLAYER_MODE_CUSTOM, str(app))
    with patch("src.gui.preferences.subprocess.Popen") as popen:
        msg = open_video_with_player(media, settings)
    assert "FakePlayer" in msg
    args = popen.call_args[0][0]
    assert args[:3] == ["open", "-a", str(app)]
    assert args[3] == str(media)
