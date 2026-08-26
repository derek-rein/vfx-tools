"""Layout contract for the convert form: fields do not squash when short.

QtTest drives resize / splitter / typing. Conversion I/O is covered by
``tests/test_integration_conversions.py``.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QLineEdit,
    QPlainTextEdit,
    QScrollArea,
    QSplitter,
    QWidget,
)

import src.rc_resources  # noqa: F401 — register :/style.qss
from src.core.constants import APP_VERSION
from src.gui.color_widgets import FORM_ROW_MIN_HEIGHT
from src.gui.convert_tab import ConvertTab
from src.gui.style import load_stylesheet
from src.gui.window import CONVERT_TABS_MIN_HEIGHT, LOG_PANE_MIN_HEIGHT, MainWindow
from src.services.app_settings import make_ini_settings, set_app_settings

# Locked row floor. A sliver is ~8px; sizeHint is ~28–33px.
_MIN_FIELD_H = FORM_ROW_MIN_HEIGHT


@pytest.fixture
def main_window(qapp: QApplication, tmp_path) -> Iterator[MainWindow]:
    s = make_ini_settings(str(tmp_path / "ui.ini"))
    set_app_settings(s)
    win = MainWindow()
    win.setStyleSheet(load_stylesheet())
    win.resize(820, 680)
    win.show()
    QTest.qWaitForWindowExposed(win)
    win.activateWindow()
    QTest.qWait(20)
    try:
        yield win
    finally:
        win.close()
        set_app_settings(None)


def _show_tab(win: MainWindow, index: int) -> ConvertTab:
    win._tabs.setCurrentIndex(index)
    QTest.qWait(20)
    return win._v2e_tab if index == 0 else win._e2v_tab


def _type_into(edit: QLineEdit, text: str) -> None:
    edit.setFocus(Qt.FocusReason.TabFocusReason)
    QTest.qWait(20)
    edit.selectAll()
    QTest.keyClick(edit, Qt.Key.Key_Backspace)
    QTest.keyClicks(edit, text)
    QTest.qWait(20)


def _form_fields(tab: ConvertTab) -> list[QWidget]:
    widgets: list[QWidget] = [
        tab.input_path,
        tab._browse_in,
        tab.src_btn,
        tab._frame_range_edit,
        tab.output_path,
        tab._browse_out,
        tab.dst_btn,
        tab.scale_combo,
    ]
    if tab.codec_combo is not None:
        widgets.append(tab.codec_combo)
    if tab.compression_combo is not None:
        widgets.append(tab.compression_combo)
    if tab.padding_spin is not None:
        widgets.append(tab.padding_spin)
    if tab.start_frame_spin is not None:
        widgets.append(tab.start_frame_spin)
    if tab.fps_widget is not None:
        widgets.append(tab.fps_widget)
    return widgets


def _scroll_area(tab: ConvertTab) -> QScrollArea:
    scroll = tab.findChild(QScrollArea, "convertTabScroll")
    assert scroll is not None
    return scroll


def _assert_fields_readable(tab: ConvertTab) -> QScrollArea:
    heights = [w.height() for w in _form_fields(tab)]
    assert all(h == _MIN_FIELD_H for h in heights), heights
    assert tab._browse_in.height() == tab.input_path.height()
    assert tab._browse_out.height() == tab.output_path.height()
    scroll = _scroll_area(tab)
    assert scroll.focusPolicy() == Qt.FocusPolicy.NoFocus
    return scroll


def test_help_menu_version_matches_app_version(main_window: MainWindow) -> None:
    texts = [a.text() for a in main_window.findChildren(QAction)]
    assert f"Version {APP_VERSION}" in texts


def test_launch_floors_and_splitter_not_collapsible(main_window: MainWindow) -> None:
    win = main_window
    _assert_fields_readable(_show_tab(win, 0))
    _assert_fields_readable(_show_tab(win, 1))
    log = win.findChild(QPlainTextEdit, "logPane")
    assert log is not None
    assert log.minimumHeight() == LOG_PANE_MIN_HEIGHT
    assert win._tabs.minimumHeight() == CONVERT_TABS_MIN_HEIGHT
    ocio = win._ocio_panel._source_combo
    assert ocio.height() == FORM_ROW_MIN_HEIGHT
    assert win._progress.height() == FORM_ROW_MIN_HEIGHT
    assert win._go.height() == FORM_ROW_MIN_HEIGHT
    assert win._cancel_btn.height() == FORM_ROW_MIN_HEIGHT
    assert win._go.height() == win._cancel_btn.height()
    splitter = win.findChild(QSplitter, "logSplitter")
    assert splitter is not None
    assert not splitter.childrenCollapsible()


def test_short_window_scrolls_and_fields_still_type(main_window: MainWindow) -> None:
    win = main_window
    tab = _show_tab(win, 1)
    scroll = _assert_fields_readable(tab)
    body = scroll.widget()
    assert body is not None
    natural = body.sizeHint().height()
    tall_heights = [w.height() for w in _form_fields(tab)]

    splitter = win.findChild(QSplitter, "logSplitter")
    assert splitter is not None
    total = sum(splitter.sizes()) or splitter.height()
    before_top = splitter.sizes()[0]
    splitter.setSizes([CONVERT_TABS_MIN_HEIGHT, max(total - CONVERT_TABS_MIN_HEIGHT, 80)])
    QTest.qWait(20)
    after_top = splitter.sizes()[0]
    assert after_top < before_top
    assert win._tabs.height() >= CONVERT_TABS_MIN_HEIGHT

    _assert_fields_readable(tab)
    short_heights = [w.height() for w in _form_fields(tab)]
    assert short_heights == tall_heights, (tall_heights, short_heights)
    assert natural > scroll.viewport().height()
    assert scroll.verticalScrollBar().maximum() > 0

    scroll.ensureWidgetVisible(tab.output_path)
    _type_into(tab.output_path, "/tmp/short-window.mov")
    assert tab.output_path.text() == "/tmp/short-window.mov"

    v2e = _show_tab(win, 0)
    _assert_fields_readable(v2e)
    _type_into(v2e.output_path, "/tmp/after-switch")
    assert v2e.output_path.text() == "/tmp/after-switch"


def test_tab_key_skips_scroll_area(main_window: MainWindow) -> None:
    win = main_window
    win.activateWindow()
    tab = _show_tab(win, 1)
    scroll = _scroll_area(tab)
    tab.input_path.setFocus(Qt.FocusReason.TabFocusReason)
    QTest.qWait(20)
    assert tab.input_path.hasFocus()
    QTest.keyClick(tab.input_path, Qt.Key.Key_Tab)
    QTest.qWait(20)
    focused = QApplication.focusWidget()
    assert focused is not None
    assert focused is not tab.input_path
    assert focused is not scroll
    assert focused is not scroll.viewport()


def test_isolated_convert_tab_stays_readable_when_short(qapp: QApplication, settings) -> None:
    tab = ConvertTab("exr2video", settings)
    tab.setStyleSheet(load_stylesheet())
    tab.resize(720, 400)
    tab.show()
    QTest.qWaitForWindowExposed(tab)
    tall_heights = [w.height() for w in _form_fields(tab)]
    tab.setFixedHeight(130)
    QTest.qWait(20)
    scroll = _assert_fields_readable(tab)
    body = scroll.widget()
    assert body is not None
    assert body.minimumSizeHint().height() == body.sizeHint().height()
    assert [w.height() for w in _form_fields(tab)] == tall_heights
    assert body.sizeHint().height() > scroll.viewport().height()
    assert scroll.verticalScrollBar().maximum() > 0
    scroll.ensureWidgetVisible(tab.output_path)
    _type_into(tab.output_path, "/tmp/isolated.mov")
    assert tab.output_path.text() == "/tmp/isolated.mov"
    tab.close()
