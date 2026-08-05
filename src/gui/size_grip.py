"""Standalone window-corner size grip (shared by main window and slate dialog).

Lives in its own module so :mod:`slate_widgets` does not need to import the
heavy :mod:`widgets` package (avoids an import cycle).
"""

from __future__ import annotations

from PySide6.QtGui import QColor, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QSizeGrip, QWidget


class SizeGrip(QSizeGrip):
    """A window-corner resize grip that paints its own diagonal-line texture.

    Qt's native ``QSizeGrip`` stops drawing the familiar corner texture as soon
    as an app-wide QSS stylesheet is applied (the style engine paints nothing),
    which is why ours rendered blank. We keep ``QSizeGrip``'s built-in resize
    behaviour and just draw the classic three nested diagonal lines.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(16, 16)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        del event
        p = QPainter(self)
        pen = QPen(QColor(0x70, 0x70, 0x70))
        pen.setWidth(1)
        p.setPen(pen)
        w, h = self.width(), self.height()
        margin = 3
        for offset in (0, 4, 8):
            p.drawLine(w - margin - offset, h - margin, w - margin, h - margin - offset)
        p.end()
