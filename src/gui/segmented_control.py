"""Exclusive multi-segment toggle control (macOS / Fluent style).

A compact ``QWidget`` that presents mutually exclusive text segments in a
single pill track.  Styled via ``objectName`` rules in ``resources/style.qss``
(``SegmentedControl`` / child ``QPushButton``).

Qt conventions used:

- Subclass ``QWidget``; enable ``WA_StyledBackground`` so QSS can paint the track
- Expose index / text API mirrored after ``QComboBox`` / ``QTabBar``
- ``currentIndexChanged(int)`` and ``currentTextChanged(str)`` signals
- Optional per-segment user data via ``addItem(..., data=…)``
- Size policy ``Fixed`` horizontally so the control hugs its labels
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFocusEvent, QKeyEvent
from PySide6.QtWidgets import (
    QButtonGroup,
    QHBoxLayout,
    QPushButton,
    QSizePolicy,
    QWidget,
)

# Focus reasons that warrant a visible ring (keyboard / a11y navigation).
_KEYBOARD_FOCUS_REASONS = frozenset(
    {
        Qt.FocusReason.TabFocusReason,
        Qt.FocusReason.BacktabFocusReason,
        Qt.FocusReason.ShortcutFocusReason,
    }
)


class SegmentedControl(QWidget):
    """Horizontal exclusive segment switcher.

    Parameters
    ----------
    items
        Optional initial segment labels (or ``(text, data)`` pairs).
    parent
        Parent widget.
    """

    currentIndexChanged = Signal(int)
    currentTextChanged = Signal(str)

    def __init__(
        self,
        items: Sequence[str | tuple[str, object]] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("SegmentedControl")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setToolTip("")  # segments carry their own tips

        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(2, 2, 2, 2)
        self._layout.setSpacing(0)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._group.idClicked.connect(self._on_id_clicked)

        self._buttons: list[QPushButton] = []
        self._data: list[object] = []
        self._block = False

        if items:
            for item in items:
                if isinstance(item, tuple):
                    text, data = item[0], item[1] if len(item) > 1 else None
                    self.addItem(str(text), data=data)
                else:
                    self.addItem(str(item))

        if self._buttons and self.currentIndex() < 0:
            self.setCurrentIndex(0)

    # -- public API (ComboBox / TabBar-like) --------------------------------

    def addItem(
        self,
        text: str,
        *,
        data: object = None,
        tooltip: str = "",
    ) -> int:
        """Append a segment. Returns the new index."""
        btn = QPushButton(text, self)
        btn.setCheckable(True)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # focus stays on the control
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
        if tooltip:
            btn.setToolTip(tooltip)

        index = len(self._buttons)
        self._buttons.append(btn)
        self._data.append(data)
        self._group.addButton(btn, index)
        self._layout.addWidget(btn)

        if index == 0 and not self._block:
            # First segment is selected by default.
            btn.setChecked(True)
        return index

    def addItems(self, texts: Iterable[str]) -> None:
        for t in texts:
            self.addItem(t)

    def clear(self) -> None:
        """Remove all segments."""
        for btn in self._buttons:
            self._group.removeButton(btn)
            self._layout.removeWidget(btn)
            btn.deleteLater()
        self._buttons.clear()
        self._data.clear()

    def count(self) -> int:
        return len(self._buttons)

    def currentIndex(self) -> int:
        checked = self._group.checkedId()
        return int(checked) if checked >= 0 else -1

    def currentText(self) -> str:
        i = self.currentIndex()
        if i < 0 or i >= len(self._buttons):
            return ""
        return self._buttons[i].text()

    def currentData(self) -> object:
        i = self.currentIndex()
        if i < 0 or i >= len(self._data):
            return None
        return self._data[i]

    def itemText(self, index: int) -> str:
        if 0 <= index < len(self._buttons):
            return self._buttons[index].text()
        return ""

    def itemData(self, index: int) -> object:
        if 0 <= index < len(self._data):
            return self._data[index]
        return None

    def setCurrentIndex(self, index: int) -> None:
        """Select *index* without emitting if already selected."""
        if index < 0 or index >= len(self._buttons):
            return
        if self.currentIndex() == index and self._buttons[index].isChecked():
            return
        self._block = True
        try:
            self._buttons[index].setChecked(True)
        finally:
            self._block = False
        self._emit_current()

    def setCurrentText(self, text: str) -> None:
        for i, btn in enumerate(self._buttons):
            if btn.text() == text:
                self.setCurrentIndex(i)
                return

    def setCurrentData(self, data: object) -> None:
        for i, d in enumerate(self._data):
            if d == data:
                self.setCurrentIndex(i)
                return

    def setSegmentToolTip(self, index: int, tooltip: str) -> None:
        if 0 <= index < len(self._buttons):
            self._buttons[index].setToolTip(tooltip)

    # -- size ---------------------------------------------------------------

    def sizeHint(self) -> QSize:
        # Sum segment hints + track padding.
        w = 4
        h = 0
        for btn in self._buttons:
            sh = btn.sizeHint()
            w += sh.width()
            h = max(h, sh.height())
        return QSize(max(w, 40), max(h + 4, 24))

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    # -- keyboard / focus ---------------------------------------------------

    def focusInEvent(self, event: QFocusEvent) -> None:  # noqa: N802
        # Accent border only for tab / keyboard focus — not mouse click.
        self._set_keyboard_focus(event.reason() in _KEYBOARD_FOCUS_REASONS)
        super().focusInEvent(event)

    def focusOutEvent(self, event: QFocusEvent) -> None:  # noqa: N802
        self._set_keyboard_focus(False)
        super().focusOutEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        n = len(self._buttons)
        if n == 0:
            super().keyPressEvent(event)
            return
        cur = self.currentIndex()
        if cur < 0:
            cur = 0
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Up):
            self.setCurrentIndex((cur - 1) % n)
            event.accept()
            return
        if key in (Qt.Key.Key_Right, Qt.Key.Key_Down):
            self.setCurrentIndex((cur + 1) % n)
            event.accept()
            return
        if key in (Qt.Key.Key_Home,):
            self.setCurrentIndex(0)
            event.accept()
            return
        if key in (Qt.Key.Key_End,):
            self.setCurrentIndex(n - 1)
            event.accept()
            return
        super().keyPressEvent(event)

    def _set_keyboard_focus(self, on: bool) -> None:
        """Drive QSS via dynamic property ``keyboardFocus`` (true/false)."""
        val = "true" if on else "false"
        if self.property("keyboardFocus") == val:
            return
        self.setProperty("keyboardFocus", val)
        # Force QSS to re-evaluate property selectors.
        style = self.style()
        if style is not None:
            style.unpolish(self)
            style.polish(self)
        self.update()

    # -- internals ----------------------------------------------------------

    def _on_id_clicked(self, _id: int) -> None:
        if self._block:
            return
        self._emit_current()

    def _emit_current(self) -> None:
        i = self.currentIndex()
        self.currentIndexChanged.emit(i)
        self.currentTextChanged.emit(self.currentText())


__all__ = ["SegmentedControl"]
