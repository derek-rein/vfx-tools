"""Standalone sequence playback window (post-convert Video → EXR Open result)."""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QDialog, QVBoxLayout, QWidget

from ...core.constants import APP_NAME, APP_ORG
from .sequence_player import SequencePlayer

log = logging.getLogger(__name__)

_GEOMETRY_KEY = "ui/sequence_player_window_geometry"


class SequencePlayerWindow(QDialog):
    """Non-modal window hosting :class:`SequencePlayer` with the cache strip.

    Used for **Open result** after Video → EXR (and anywhere else we want a
    full player outside the slate editor / browse dialog).
    """

    def __init__(
        self,
        input_path: str,
        *,
        settings: QSettings | None = None,
        ocio_cfg: object | None = None,
        src_colorspace: str = "",
        fps: float = 24.0,
        parent: QWidget | None = None,
        title: str | None = None,
    ) -> None:
        # parent=None so this is a true top-level window (not clipped / stacked
        # under the main window on multi-monitor setups).
        super().__init__(None)
        self._owner = parent  # keep a Python ref if caller needs lifetime link
        self._settings = settings if settings is not None else QSettings(APP_ORG, APP_NAME)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.WindowTitleHint
            | Qt.WindowType.WindowSystemMenuHint
            | Qt.WindowType.WindowMinMaxButtonsHint
            | Qt.WindowType.WindowCloseButtonHint
        )
        self.resize(1120, 720)

        geo = self._settings.value(_GEOMETRY_KEY)
        if geo is not None:
            try:
                self.restoreGeometry(geo)
            except Exception:
                pass

        label = title or Path(input_path).name or "Sequence Player"
        self.setWindowTitle(f"Sequence Player — {label}")

        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(0)

        # show_cache_ui=True — full cache budget strip (unlike browser Preview).
        # GPU plane is created in SequencePlayer.__init__ before this window is
        # shown (same Qt 6.4 OpenGL-surface rule as the slate / browse dialogs).
        self._player = SequencePlayer(
            settings=self._settings,
            show_cache_ui=True,
            prefer_gpu=True,
            parent=self,
        )
        root.addWidget(self._player, 1)

        ok = self._load(
            input_path,
            ocio_cfg=ocio_cfg,
            src_colorspace=src_colorspace,
            fps=fps,
            title=title,
        )
        if not ok:
            log.warning("Sequence player: no frames found for %s", input_path)

    def player(self) -> SequencePlayer:
        return self._player

    def _load(
        self,
        input_path: str,
        *,
        ocio_cfg: object | None,
        src_colorspace: str,
        fps: float,
        title: str | None,
    ) -> bool:
        label = title or Path(input_path).name or "Sequence Player"
        self.setWindowTitle(f"Sequence Player — {label}")
        ok = self._player.load_sequence(
            input_path,
            fps=fps if fps > 0 else 24.0,
            ocio_cfg=ocio_cfg,
            src_colorspace=src_colorspace or "",
        )
        if ok:
            try:
                from ...core.sequence import find_exr_sequence_info

                _paths, name, frames, _pad, _seq = find_exr_sequence_info(input_path)
                if name:
                    n = len(frames)
                    self.setWindowTitle(f"Sequence Player — {name} ({n} frames)")
            except Exception:
                pass
        else:
            self.setWindowTitle(f"Sequence Player — no frames ({label})")
        return ok

    def reload(
        self,
        input_path: str,
        *,
        ocio_cfg: object | None = None,
        src_colorspace: str = "",
        fps: float = 24.0,
        title: str | None = None,
    ) -> bool:
        """Load a new sequence into this window (e.g. another convert finished)."""
        return self._load(
            input_path,
            ocio_cfg=ocio_cfg,
            src_colorspace=src_colorspace,
            fps=fps,
            title=title,
        )

    def showEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().showEvent(event)
        # restoreGeometry can land us off all screens (multi-monitor changes).
        self._ensure_visible_on_screen()

    def _ensure_visible_on_screen(self) -> None:
        screens = QGuiApplication.screens()
        if not screens:
            return
        frame = self.frameGeometry()
        on_any = any(s.availableGeometry().intersects(frame) for s in screens)
        if on_any:
            return
        primary = QGuiApplication.primaryScreen()
        if primary is None:
            return
        avail = primary.availableGeometry()
        w = min(self.width(), max(400, avail.width() - 40))
        h = min(self.height(), max(300, avail.height() - 40))
        self.resize(w, h)
        # Center on the primary available area.
        self.move(
            avail.x() + (avail.width() - w) // 2,
            avail.y() + (avail.height() - h) // 2,
        )
        log.info("Sequence player window was off-screen; centered on primary display")
        # Drop the bad saved rect so the next open does not re-apply it.
        try:
            self._settings.setValue(_GEOMETRY_KEY, self.saveGeometry())
        except Exception:
            pass

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        try:
            self._settings.setValue(_GEOMETRY_KEY, self.saveGeometry())
        except Exception:
            pass
        try:
            self._player.set_playing(False)
            self._player.shutdown()
        except RuntimeError:
            pass
        super().closeEvent(event)


__all__ = ["SequencePlayerWindow"]
