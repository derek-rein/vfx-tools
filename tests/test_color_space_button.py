"""ColorSpaceButton auto-detect flash vs normal selection styling."""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from src.gui.widgets import ColorSpaceButton


def _app() -> QApplication:
    existing = QApplication.instance()
    if existing is not None:
        return existing  # type: ignore[return-value]
    return QApplication([])


def _populate_simple(btn: ColorSpaceButton, *names: str, select: str = "") -> None:
    families = {"Utility": list(names)}
    btn.populate(families, select=select)


class TestColorSpaceAutoFlash:
    def test_manual_select_has_no_auto_style(self) -> None:
        _app()
        btn = ColorSpaceButton()
        _populate_simple(btn, "ACEScg", "sRGB", select="ACEScg")
        assert btn.is_valid()
        assert not btn.is_auto_flash()
        assert btn.styleSheet() == ""

    def test_auto_select_flashes_then_clears(self) -> None:
        app = _app()
        btn = ColorSpaceButton()
        # Short flash for a fast test.
        btn._AUTO_FLASH_MS = 50  # type: ignore[misc]
        _populate_simple(btn, "ACEScg", "sRGB")
        assert btn.try_select("sRGB", auto=True)
        assert btn.current_space() == "sRGB"
        assert btn.is_auto_flash()
        assert "3a3020" in btn.styleSheet()

        # Drive the single-shot timer to completion.
        deadline = 500
        elapsed = 0
        while btn.is_auto_flash() and elapsed < deadline:
            app.processEvents()
            QTimer.singleShot(20, app.quit)
            app.exec()
            elapsed += 20

        assert not btn.is_auto_flash()
        assert btn.styleSheet() == ""
        assert btn.is_valid()
        assert btn.current_space() == "sRGB"

    def test_manual_pick_clears_auto_flash(self) -> None:
        _app()
        btn = ColorSpaceButton()
        btn._AUTO_FLASH_MS = 60_000  # type: ignore[misc]  # would stick if not cleared
        _populate_simple(btn, "ACEScg", "sRGB")
        assert btn.try_select("ACEScg", auto=True)
        assert btn.is_auto_flash()

        assert btn.try_select("sRGB", auto=False)
        assert not btn.is_auto_flash()
        assert btn.styleSheet() == ""
        assert btn.current_space() == "sRGB"

    def test_set_current_space_clears_auto_flash(self) -> None:
        _app()
        btn = ColorSpaceButton()
        btn._AUTO_FLASH_MS = 60_000  # type: ignore[misc]
        _populate_simple(btn, "ACEScg", "sRGB")
        btn.try_select("ACEScg", auto=True)
        assert btn.is_auto_flash()
        btn.set_current_space("sRGB")
        assert not btn.is_auto_flash()
        assert btn.styleSheet() == ""
