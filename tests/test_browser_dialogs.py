"""File-browser dialog behavior (close / reject / context menus)."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication, QKeyEvent
from PySide6.QtWidgets import QDialog, QMenu

from src.gui.browser_state import VIEW_LIST, VIEW_PREVIEW
from src.gui.widgets import (
    SequenceBrowserDialog,
    VideoBrowserDialog,
    _add_copy_path_actions,
    _folder_path_for_copy,
)


@pytest.fixture
def empty_dir(tmp_path: Path) -> Path:
    d = tmp_path / "browse"
    d.mkdir()
    return d


class TestBrowserRejectWhilePreviewing:
    """Window chrome / Cancel must close even when Preview is active.

    Escape leaves Preview via key handlers; ``reject()`` used to intercept and
    only switch back to List/Grid, so the first X/Cancel left the dialog open.
    """

    def test_sequence_reject_closes_from_preview(
        self, qapp, empty_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            SequenceBrowserDialog,
            "_load_preview_sequence",
            lambda self: None,
        )
        monkeypatch.setattr(
            SequenceBrowserDialog,
            "_shutdown_browser_workers",
            lambda self: None,
        )
        monkeypatch.setattr(
            SequenceBrowserDialog,
            "_save_browser_layout",
            lambda self: None,
        )
        dlg = SequenceBrowserDialog(start_dir=str(empty_dir))
        try:
            dlg._view_seg.blockSignals(True)
            dlg._view_seg.setCurrentData(VIEW_PREVIEW)
            dlg._view_seg.blockSignals(False)
            dlg._previewing = True
            assert dlg._view_seg.currentData() == VIEW_PREVIEW

            dlg.reject()

            assert dlg.result() == QDialog.DialogCode.Rejected
            # Must not have been demoted to list/grid while still "open".
            assert dlg.result() != QDialog.DialogCode.Accepted
        finally:
            dlg.close()
            dlg.deleteLater()
            qapp.processEvents()

    def test_video_reject_closes_from_preview(
        self, qapp, empty_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            VideoBrowserDialog,
            "_load_preview_video",
            lambda self: None,
        )
        monkeypatch.setattr(
            VideoBrowserDialog,
            "_shutdown_player",
            lambda self: None,
        )
        monkeypatch.setattr(
            VideoBrowserDialog,
            "_save_browser_layout",
            lambda self: None,
        )
        # Avoid filesystem scan noise during construct/navigate.
        monkeypatch.setattr(
            VideoBrowserDialog,
            "_scan_directory",
            lambda self, directory: None,
        )
        dlg = VideoBrowserDialog(start_dir=str(empty_dir))
        try:
            dlg._view_seg.blockSignals(True)
            dlg._view_seg.setCurrentData(VIEW_PREVIEW)
            dlg._view_seg.blockSignals(False)
            dlg._previewing = True
            assert dlg._view_seg.currentData() == VIEW_PREVIEW

            dlg.reject()

            assert dlg.result() == QDialog.DialogCode.Rejected
        finally:
            dlg.close()
            dlg.deleteLater()
            qapp.processEvents()

    def test_sequence_escape_still_leaves_preview(
        self, qapp, empty_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Escape should return to the last browse mode, not close."""
        monkeypatch.setattr(
            SequenceBrowserDialog,
            "_load_preview_sequence",
            lambda self: None,
        )
        monkeypatch.setattr(
            SequenceBrowserDialog,
            "_stop_preview_playback",
            lambda self, **kw: None,
        )
        monkeypatch.setattr(
            SequenceBrowserDialog,
            "_save_browser_layout",
            lambda self: None,
        )
        monkeypatch.setattr(
            SequenceBrowserDialog,
            "_scan_directory",
            lambda self, directory: None,
        )
        dlg = SequenceBrowserDialog(start_dir=str(empty_dir))
        try:
            dlg._last_browse_mode = VIEW_LIST
            dlg._view_seg.setCurrentData(VIEW_PREVIEW)
            assert dlg._view_seg.currentData() == VIEW_PREVIEW

            ev = QKeyEvent(
                QKeyEvent.Type.KeyPress,
                Qt.Key.Key_Escape,
                Qt.KeyboardModifier.NoModifier,
            )
            dlg.keyPressEvent(ev)

            assert dlg.result() == 0  # not finished
            assert dlg._view_seg.currentData() == VIEW_LIST
        finally:
            dlg.close()
            dlg.deleteLater()
            qapp.processEvents()


class TestCopyPathHelpers:
    def test_folder_path_for_file(self, tmp_path: Path) -> None:
        f = tmp_path / "shot.mov"
        f.write_bytes(b"x")
        assert _folder_path_for_copy(str(f)) == str(tmp_path)

    def test_folder_path_for_sequence_pattern(self, tmp_path: Path) -> None:
        pat = str(tmp_path / "plate.####.exr")
        assert _folder_path_for_copy(pat) == str(tmp_path)

    def test_folder_path_for_directory(self, tmp_path: Path) -> None:
        assert _folder_path_for_copy(str(tmp_path)) == str(tmp_path)


class TestBrowserCopyPathMenu:
    def test_sequence_menu_has_copy_actions(
        self, qapp, empty_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(SequenceBrowserDialog, "_scan_directory", lambda self, d: None)
        monkeypatch.setattr(SequenceBrowserDialog, "_save_browser_layout", lambda self: None)
        monkeypatch.setattr(SequenceBrowserDialog, "_shutdown_browser_workers", lambda self: None)
        dlg = SequenceBrowserDialog(start_dir=str(empty_dir))
        try:
            frame = str(empty_dir / "shot.1001.exr")
            dlg._selected_frame_path = frame
            dlg._selected_dir = str(empty_dir)
            # Build the same actions the context menu would; avoid QMenu.exec.
            menu = QMenu(dlg)
            _add_copy_path_actions(
                menu,
                file_path=dlg._selected_frame_path,
                folder_path=dlg._selected_dir,
            )
            labels = [a.text() for a in menu.actions()]
            assert "Copy File Path" in labels
            assert "Copy Folder Path" in labels
            by_text = {a.text(): a for a in menu.actions()}
            assert by_text["Copy File Path"].isEnabled()
            assert by_text["Copy Folder Path"].isEnabled()

            clip = QGuiApplication.clipboard()
            by_text["Copy File Path"].trigger()
            assert clip.text() == frame
            by_text["Copy Folder Path"].trigger()
            assert clip.text() == str(empty_dir)
        finally:
            dlg.close()
            dlg.deleteLater()
            qapp.processEvents()

    def test_video_menu_has_copy_actions(
        self, qapp, empty_dir: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(VideoBrowserDialog, "_scan_directory", lambda self, d: None)
        monkeypatch.setattr(VideoBrowserDialog, "_save_browser_layout", lambda self: None)
        monkeypatch.setattr(VideoBrowserDialog, "_shutdown_player", lambda self: None)
        dlg = VideoBrowserDialog(start_dir=str(empty_dir))
        try:
            vid = str(empty_dir / "clip.mov")
            dlg._selected_path = vid
            menu = QMenu(dlg)
            _add_copy_path_actions(menu, file_path=dlg._selected_path)
            by_text = {a.text(): a for a in menu.actions()}
            assert by_text["Copy File Path"].isEnabled()
            assert by_text["Copy Folder Path"].isEnabled()
            by_text["Copy File Path"].trigger()
            assert QGuiApplication.clipboard().text() == vid
            by_text["Copy Folder Path"].trigger()
            assert QGuiApplication.clipboard().text() == str(empty_dir)
        finally:
            dlg.close()
            dlg.deleteLater()
            qapp.processEvents()
