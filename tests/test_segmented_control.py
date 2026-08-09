"""Unit tests for :class:`SegmentedControl`."""

from __future__ import annotations

from PySide6.QtWidgets import QApplication

from src.gui.segmented_control import SegmentedControl


def _app() -> QApplication:
    existing = QApplication.instance()
    if existing is not None:
        return existing  # type: ignore[return-value]
    return QApplication([])


class TestSegmentedControl:
    def test_items_and_selection(self) -> None:
        _app()
        seg = SegmentedControl([("List", "list"), ("Grid", "grid")])
        assert seg.count() == 2
        assert seg.currentIndex() == 0
        assert seg.currentText() == "List"
        assert seg.currentData() == "list"

        seen: list[int] = []
        seg.currentIndexChanged.connect(seen.append)
        seg.setCurrentData("grid")
        assert seg.currentIndex() == 1
        assert seg.currentData() == "grid"
        assert seen == [1]

        seg.setCurrentIndex(0)
        assert seg.currentText() == "List"
        assert seen[-1] == 0

    def test_add_clear(self) -> None:
        _app()
        seg = SegmentedControl()
        assert seg.count() == 0
        seg.addItem("A", data=1)
        seg.addItem("B", data=2, tooltip="bee")
        assert seg.count() == 2
        assert seg.itemData(1) == 2
        seg.clear()
        assert seg.count() == 0
        assert seg.currentIndex() == -1
