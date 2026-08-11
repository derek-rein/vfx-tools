"""Unit tests for shared browser path paste helpers."""

from __future__ import annotations

from pathlib import Path

from src.gui.browser_path import (
    clean_path_string,
    folder_path_for_copy,
    resolve_sequence_browser_path,
    resolve_video_browser_path,
)

_VIDEO = frozenset({".mov", ".mp4", ".r3d", ".nev"})


def test_clean_path_string_strips_quotes_and_file_url() -> None:
    assert clean_path_string('  "/tmp/clip.mov"  ') == "/tmp/clip.mov"
    assert clean_path_string("file:///tmp/seq.####.exr") == "/tmp/seq.####.exr"
    assert clean_path_string("file://localhost/tmp/a.mov") == "/tmp/a.mov"


def test_folder_path_for_copy_sequence_and_file(tmp_path: Path) -> None:
    f = tmp_path / "plate.1001.exr"
    f.write_bytes(b"x")
    assert folder_path_for_copy(str(f)) == str(tmp_path)
    assert folder_path_for_copy(str(tmp_path / "name.####.exr")) == str(tmp_path)
    assert folder_path_for_copy(str(tmp_path)) == str(tmp_path)


def test_resolve_sequence_browser_path_pattern(tmp_path: Path) -> None:
    (tmp_path / "shot.1001.exr").write_bytes(b"x")
    (tmp_path / "shot.1002.exr").write_bytes(b"x")
    got = resolve_sequence_browser_path(str(tmp_path / "shot.####.exr"))
    assert got is not None
    directory, select_name = got
    assert Path(directory) == tmp_path
    assert select_name == "shot"


def test_resolve_video_browser_path_file(tmp_path: Path) -> None:
    clip = tmp_path / "cam.R3D"
    clip.write_bytes(b"x")
    got = resolve_video_browser_path(str(clip), _VIDEO)
    assert got is not None
    directory, select_path = got
    assert Path(directory) == tmp_path
    assert Path(select_path) == clip


def test_resolve_video_browser_path_skips_appledouble(tmp_path: Path) -> None:
    """macOS ``._*.R3D`` sidecars must not auto-select as video."""
    junk = tmp_path / "._cam.R3D"
    junk.write_bytes(b"x")
    got = resolve_video_browser_path(str(junk), _VIDEO)
    assert got is not None
    directory, select_path = got
    assert Path(directory) == tmp_path
    assert select_path == ""
