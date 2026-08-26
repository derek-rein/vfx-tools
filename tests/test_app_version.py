"""App version is pyproject.toml only — Help / About / CLI read APP_VERSION."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QLabel

from src.core.constants import APP_VERSION
from src.gui.window import AboutDialog


def _pyproject_version() -> str:
    data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    ver = data["project"]["version"]
    assert isinstance(ver, str)
    return ver


def test_app_version_is_pyproject_version() -> None:
    assert APP_VERSION == _pyproject_version()


def test_constants_has_no_hardcoded_app_version() -> None:
    text = Path("src/core/constants.py").read_text(encoding="utf-8")
    assert "APP_VERSION = _load_project_version()" in text
    assert 'APP_VERSION = "' not in text


def test_cli_version_flag_prints_pyproject_version(capsys: pytest.CaptureFixture[str]) -> None:
    from src.cli import build_parser

    with pytest.raises(SystemExit) as ei:
        build_parser().parse_args(["--version"])
    assert ei.value.code == 0
    out = capsys.readouterr().out
    assert APP_VERSION in out
    assert _pyproject_version() in out


def test_about_dialog_shows_pyproject_version(qapp: QApplication) -> None:
    dlg = AboutDialog()
    try:
        labels = [w.text() for w in dlg.findChildren(QLabel)]
        assert any("Version" in t and APP_VERSION in t for t in labels), labels
    finally:
        dlg.close()
