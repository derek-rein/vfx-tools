"""Unit tests for file-browser QSettings helpers (no dialog GUI required)."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QByteArray, QSettings

from src.gui.browser_state import (
    BROWSER_GEOMETRY_KEY,
    SEQ_BROWSER_KEYS,
    VID_BROWSER_KEYS,
    VIEW_GRID,
    VIEW_LIST,
    VIEW_PREVIEW,
    browser_qsettings,
    coerce_view_mode,
    dirs_equal,
    load_shared_geometry,
    normalize_dir,
    parse_int_list,
    parse_str_list,
    save_shared_geometry,
    settings_bool,
)


@pytest.fixture
def isolated_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> QSettings:
    """Point QSettings at a temp Ini file so tests do not touch user prefs."""
    ini = tmp_path / "test.ini"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    # Force org/app path via IniFormat file for isolation.
    s = QSettings(str(ini), QSettings.Format.IniFormat)
    yield s
    s.sync()


def test_keys_are_separate_for_video_and_sequence() -> None:
    assert SEQ_BROWSER_KEYS.kind == "sequence"
    assert VID_BROWSER_KEYS.kind == "video"
    assert SEQ_BROWSER_KEYS.view != VID_BROWSER_KEYS.view
    assert SEQ_BROWSER_KEYS.inspect != VID_BROWSER_KEYS.inspect
    assert SEQ_BROWSER_KEYS.tree_expanded != VID_BROWSER_KEYS.tree_expanded
    assert SEQ_BROWSER_KEYS.view == "ui/sequence_browser_view"
    assert VID_BROWSER_KEYS.view == "ui/video_browser_view"
    # Geometry is intentionally not per-mode.
    assert not hasattr(SEQ_BROWSER_KEYS, "geometry")


def test_normalize_and_dirs_equal(tmp_path: Path) -> None:
    a = tmp_path / "shots" / "sc01"
    a.mkdir(parents=True)
    assert dirs_equal(str(a), str(a.resolve()))
    assert dirs_equal(str(a), a / ".")
    assert not dirs_equal(str(a), tmp_path)
    # File path normalizes to parent for equality of containing folder.
    f = a / "clip.mov"
    f.write_bytes(b"x")
    assert normalize_dir(f) == normalize_dir(a)
    assert normalize_dir("") == ""
    assert normalize_dir(None) == ""


def test_coerce_view_mode() -> None:
    assert coerce_view_mode("grid") == VIEW_GRID
    assert coerce_view_mode("preview") == VIEW_PREVIEW
    assert coerce_view_mode("preview", allow_preview=False) == VIEW_LIST
    assert coerce_view_mode("nope") == VIEW_LIST
    assert coerce_view_mode(None) == VIEW_LIST


def test_parse_helpers() -> None:
    assert parse_int_list([1, "2", 3]) == [1, 2, 3]
    assert parse_int_list("bad") is None
    assert parse_int_list(None) is None
    assert parse_str_list(["a", "b"]) == ["a", "b"]
    assert parse_str_list("solo") == ["solo"]
    assert parse_str_list(None) == []


def test_settings_bool(isolated_settings: QSettings) -> None:
    s = isolated_settings
    assert settings_bool(s, "missing", True) is True
    s.setValue("k", "false")
    assert settings_bool(s, "k", True) is False
    s.setValue("k", "1")
    assert settings_bool(s, "k", False) is True
    s.setValue("k", True)
    assert settings_bool(s, "k", False) is True


def test_shared_geometry_roundtrip_and_legacy_migrate(
    isolated_settings: QSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    s = isolated_settings
    blob = QByteArray(b"fake-geometry-bytes")

    # Patch browser_qsettings to use our isolated store when helpers default.
    monkeypatch.setattr("src.gui.browser_state.browser_qsettings", lambda: s)

    assert load_shared_geometry(s) is None
    # Legacy sequence-only key is promoted.
    s.setValue("ui/sequence_browser_geometry", blob)
    loaded = load_shared_geometry(s)
    assert loaded == blob
    assert s.value(BROWSER_GEOMETRY_KEY) == blob

    blob2 = QByteArray(b"new-geo")
    save_shared_geometry(blob2, s)
    assert load_shared_geometry(s) == blob2


def test_browser_qsettings_org_app() -> None:
    s = browser_qsettings()
    assert s.organizationName() == "VFXTools"
    assert s.applicationName() == "EXRConverter"
