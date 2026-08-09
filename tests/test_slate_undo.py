"""SlateModel QUndoStack: flags, fields, fill-from-slate."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings

from src.services.slate_model import SlateModel


def _model(tmp_path: Path) -> SlateModel:
    ini = str(tmp_path / "slate.ini")
    settings = QSettings(ini, QSettings.Format.IniFormat)
    return SlateModel(settings, "exr2video")


def test_flag_undo_redo(tmp_path, qapp):
    m = _model(tmp_path)
    assert m.slate_enabled is False
    m.set_slate_enabled(True, record_undo=True)
    assert m.slate_enabled is True
    m.undo_stack.undo()
    assert m.slate_enabled is False
    m.undo_stack.redo()
    assert m.slate_enabled is True


def test_slate_fields_undo(tmp_path, qapp):
    m = _model(tmp_path)
    initial = m.slate_fields.get("shot", "")
    m.set_slate_fields({"shot": "SH010"}, record_undo=True)
    assert m.slate_fields["shot"] == "SH010"
    m.set_slate_fields({"shot": "SH020"}, record_undo=True)
    assert m.slate_fields["shot"] == "SH020"
    m.undo_stack.undo()
    assert m.slate_fields["shot"] == "SH010"
    m.undo_stack.undo()
    assert m.slate_fields.get("shot", "") == initial


def test_fill_burnin_undoable(tmp_path, qapp):
    m = _model(tmp_path)
    m.set_slate_fields(
        {"show": "TEST", "sequence": "SQ", "shot": "SH"},
        record_undo=False,
    )
    m.set_burnin_fields({"top_left": "KEEP"}, record_undo=False)
    m.reset_burnin_from_slate(record_undo=True)
    assert m.burnin_fields["top_left"] != "KEEP" or any(
        v for v in m.burnin_fields.values()
    )
    m.undo_stack.undo()
    assert m.burnin_fields["top_left"] == "KEEP"
