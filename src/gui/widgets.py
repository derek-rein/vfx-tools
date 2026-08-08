from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    import fileseq

from PySide6.QtCore import (
    QDir,
    QObject,
    QPoint,
    QRegularExpression,
    QSettings,
    QStandardPaths,
    Qt,
    QThread,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QAction,
    QGuiApplication,
    QIcon,
    QPainter,
    QPixmap,
    QRegularExpressionValidator,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFileSystemModel,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from ..core.constants import (
    BUNDLED_ACES_STUDIO_KEY,
    COMMON_FPS,
    DEFAULT_DST_E2V,
    DEFAULT_DST_V2E,
    DEFAULT_EXR_COMPRESSION,
    DEFAULT_FRAME_PADDING,
    DEFAULT_SCALE,
    DEFAULT_SRC_E2V,
    DEFAULT_SRC_V2E,
    DEFAULT_START_FRAME,
    DEFAULT_VIDEO_CODEC,
    EXR_COMPRESSIONS,
    OCIO_SOURCE_BUNDLED,
    OCIO_SOURCE_ENV,
    OCIO_SOURCE_FILE,
    SCALE_OPTIONS,
    available_video_codecs,
    video_codec_by_key,
)
from ..core.nuke_discover import is_nuke_source_key
from ..core.ocio_utils import (
    find_equivalent_space,
    list_app_configs,
    list_builtin_configs,
    list_nuke_configs,
    resolve_ocio_config,
)
from ..core.sequence import probe_exr_colorspace, probe_exr_metadata, scan_exr_sequences
from ..core.video import probe_video_metadata, scan_video_files
from .style import DESC_STYLE, HINT_STYLE, STATUS_DIM, STATUS_ERR, STATUS_OK

try:
    import PyOpenColorIO as OCIO
except ImportError:
    OCIO = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# Color-space menu button
# ---------------------------------------------------------------------------


class ColorSpaceButton(QToolButton):
    """A button that pops up a nested QMenu grouped by OCIO family.

    The menu is shown manually under the press point (not via QToolButton's
    InstantPopup + setMenu path). That path often opens at the wrong global
    corner once an app-wide QSS stylesheet is applied.

    When the active OCIO config changes, :meth:`populate` may leave the
    control in an **invalid** state if the previous space has no equivalent —
    :meth:`current_space` then returns ``""`` so Convert stays disabled.
    """

    space_changed = Signal(str)

    # Visual cue when the displayed name is not in the current config.
    _INVALID_STYLE = (
        "QToolButton { color: #e07070; border: 1px solid #a04040; background-color: #3a2020; }"
    )

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        # DelayedPopup + no setMenu — we own popup positioning in mouse/key events.
        self.setPopupMode(QToolButton.ToolButtonPopupMode.DelayedPopup)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._current = ""
        self._valid = False
        self._menu = QMenu(self)
        self._set_display("(none)")
        self._apply_valid_style()

    def current_space(self) -> str:
        """Canonical space name if valid; empty string when invalid/unset."""
        return self._current if self._valid and self._current else ""

    def displayed_space(self) -> str:
        """Name shown on the button (may be invalid for the active config)."""
        return self._current

    def is_valid(self) -> bool:
        return self._valid and bool(self._current)

    def set_current_space(self, name: str) -> None:
        """Set the selection; marks invalid if *name* is empty."""
        self._current = name or ""
        self._valid = bool(name)
        self._set_display(name or "(none)")
        self._apply_valid_style()

    def set_invalid(self, wanted: str) -> None:
        """Show *wanted* as missing from the active config (Convert disabled)."""
        self._current = wanted or ""
        self._valid = False
        label = wanted if wanted else "(none)"
        self._set_display(f"⚠ {label}")
        tip = (
            f"“{wanted}” is not in the current OCIO config "
            f"(and no equivalent was found). Pick a color space."
            if wanted
            else "Select a color space."
        )
        self.setToolTip(tip)
        self._apply_valid_style()

    def populate(self, families: dict[str, list[str]], select: str = "") -> None:
        """Rebuild the menu. *select* must already be a name present in *families*
        (or empty). Callers should run :func:`~src.core.ocio_utils.find_equivalent_space`
        first; use :meth:`set_invalid` when no match exists.
        """
        old_menu = self._menu
        self._menu = QMenu(self)
        old_menu.deleteLater()

        submenu_cache: dict[str, QMenu] = {}
        found = False
        all_names: set[str] = set()

        for family in sorted(families.keys()):
            names = families[family]
            if "/" in family:
                parts = family.split("/")
                for depth in range(len(parts)):
                    key = "/".join(parts[: depth + 1])
                    if key not in submenu_cache:
                        parent_key = "/".join(parts[:depth]) if depth else ""
                        parent = submenu_cache[parent_key] if parent_key else self._menu
                        submenu_cache[key] = parent.addMenu(parts[depth])
                target_menu = submenu_cache[family]
            else:
                if len(names) == 1:
                    target_menu = self._menu
                else:
                    target_menu = self._menu.addMenu(family)
                    submenu_cache[family] = target_menu

            for cs_name in names:
                all_names.add(cs_name)
                action = target_menu.addAction(cs_name)
                action.triggered.connect(lambda checked, n=cs_name: self._pick(n))
                if cs_name == select:
                    found = True

        if select and found:
            self._pick(select)
        elif select and select in all_names:
            self._pick(select)
        elif select:
            # Name not in menu — invalid until the user picks.
            self.set_invalid(select)
        else:
            self._current = ""
            self._valid = False
            self._set_display("(none)")
            self.setToolTip("Select a color space.")
            self._apply_valid_style()

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._popup_menu(event.position().toPoint())
            event.accept()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (
            Qt.Key.Key_Space,
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
            Qt.Key.Key_Down,
        ):
            # Keyboard open: under the left edge of the button (combo-like).
            self._popup_menu(QPoint(0, self.height() // 2))
            event.accept()
            return
        super().keyPressEvent(event)

    def _popup_menu(self, local_pos) -> None:
        """Show the family menu under the press, relative to this button."""
        if self._menu is None or not self._menu.actions():
            return
        # Place the menu just below the button, X following the click so a
        # wide control doesn't always open at the far left of the window.
        if not isinstance(local_pos, QPoint):
            local_pos = QPoint(int(local_pos.x()), int(local_pos.y()))
        x = max(0, min(local_pos.x(), max(0, self.width() - 8)))
        global_pos = self.mapToGlobal(QPoint(x, self.height()))
        self._menu.popup(global_pos)

    def _pick(self, name: str) -> None:
        self._current = name
        self._valid = True
        self._set_display(name)
        self._apply_valid_style()
        self.space_changed.emit(name)

    def try_select(self, name: str) -> bool:
        """Select *name* if it exists in the menu. Returns True on match."""
        if not name:
            return False
        if self._find_action(self._menu, name):
            self._pick(name)
            return True
        # Case-insensitive fallback: scan all actions for a match
        found = self._find_action_ci(self._menu, name.lower())
        if found:
            self._pick(found)
            return True
        return False

    def _apply_valid_style(self) -> None:
        if self._valid:
            self.setStyleSheet("")
        else:
            self.setStyleSheet(self._INVALID_STYLE)

    @staticmethod
    def _find_action(menu: QMenu, name: str) -> bool:
        for action in menu.actions():
            sub = action.menu()
            if sub:
                if ColorSpaceButton._find_action(sub, name):
                    return True
            elif action.text() == name:
                return True
        return False

    @staticmethod
    def _find_action_ci(menu: QMenu, name_lower: str) -> str:
        """Case-insensitive search; returns the exact action text or ''."""
        for action in menu.actions():
            sub = action.menu()
            if sub:
                hit = ColorSpaceButton._find_action_ci(sub, name_lower)
                if hit:
                    return hit
            elif action.text().lower() == name_lower:
                return action.text()
        return ""

    def _set_display(self, text: str) -> None:
        metrics = self.fontMetrics()
        elided = metrics.elidedText(text, Qt.TextElideMode.ElideMiddle, max(self.width() - 30, 120))
        self.setText(elided)
        if self._valid:
            self.setToolTip(text)


# ---------------------------------------------------------------------------
# FPS combo with common presets + custom
# ---------------------------------------------------------------------------


class FpsCombo(QWidget):
    """Combo with common fps presets and a custom spinbox."""

    CUSTOM_LABEL = "Custom\u2026"

    def __init__(self, settings: QSettings, key: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._settings = settings
        self._key = key

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._combo = QComboBox()
        for fps_val in COMMON_FPS:
            label = str(int(fps_val)) if fps_val == int(fps_val) else f"{fps_val:.3f}"
            self._combo.addItem(label, float(fps_val))
        self._combo.addItem(self.CUSTOM_LABEL, -1.0)

        self._spin = QDoubleSpinBox()
        self._spin.setRange(1.0, 240.0)
        self._spin.setDecimals(3)
        self._spin.setValue(120.0)
        self._spin.setSuffix(" fps")
        self._spin.setVisible(False)

        layout.addWidget(self._combo, 1)
        layout.addWidget(self._spin)

        saved = float(settings.value(key, 24.0))
        self._restore(saved)

        self._combo.currentIndexChanged.connect(self._on_combo_changed)
        self._spin.valueChanged.connect(self._on_spin_changed)

    def fps(self) -> float:
        val = self._combo.currentData()
        if val == -1.0:
            return float(self._spin.value())
        return float(val)

    def _restore(self, saved: float) -> None:
        for i in range(self._combo.count()):
            data = self._combo.itemData(i)
            if data is not None and data != -1.0 and abs(data - saved) < 0.01:
                self._combo.setCurrentIndex(i)
                return
        self._combo.setCurrentIndex(self._combo.count() - 1)
        self._spin.setValue(saved)
        self._spin.setVisible(True)

    def _on_combo_changed(self, idx: int) -> None:
        val = self._combo.currentData()
        is_custom = val == -1.0
        self._spin.setVisible(is_custom)
        if not is_custom:
            self._settings.setValue(self._key, val)
        else:
            self._settings.setValue(self._key, float(self._spin.value()))

    def _on_spin_changed(self, val: float) -> None:
        if self._combo.currentData() == -1.0:
            self._settings.setValue(self._key, float(val))


# ---------------------------------------------------------------------------
# OCIO config panel
# ---------------------------------------------------------------------------


class OcioConfigPanel(QGroupBox):
    """Panel for selecting OCIO config."""

    config_changed = Signal()

    def __init__(self, settings: QSettings, parent: QWidget | None = None):
        super().__init__(parent)
        self._settings = settings
        self._prev_index = 0
        self._file_path = settings.value("ocio/file_path", "")

        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        source_row = QHBoxLayout()
        source_row.addWidget(QLabel("OCIO Config:"))
        self._source_combo = QComboBox()
        self._source_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        source_row.addWidget(self._source_combo, 1)
        layout.addLayout(source_row)

        self._status = QLabel()
        self._status.setStyleSheet(STATUS_DIM)
        layout.addWidget(self._status)

        self._builtin_configs = list_builtin_configs()
        self._app_configs = list_app_configs()
        self._nuke_configs = list_nuke_configs()

        env_ocio = os.environ.get("OCIO", "")
        env_label = (
            f"$OCIO environment variable ({Path(env_ocio).name})"
            if env_ocio
            else "$OCIO environment variable (not set)"
        )
        self._source_combo.addItem(env_label, OCIO_SOURCE_ENV)
        self._source_combo.insertSeparator(self._source_combo.count())

        # Our bundled "super awesome" config (official ACES studio with tons of cameras) first
        for name, label, recommended in self._app_configs:
            short = label
            if recommended:
                short += "  \u2605"
            self._source_combo.addItem(short, name)
        if self._app_configs:
            self._source_combo.insertSeparator(self._source_combo.count())

        # Local Nuke installs — path references only (never redistributed).
        # Incompatible with the linked OpenColorIO are listed but greyed out.
        from ..core.nuke_discover import resolve_nuke_config_path

        for name, label, recommended, compatible, detail in self._nuke_configs:
            short = label
            if recommended:
                short += "  \u2605"
            self._source_combo.addItem(short, name)
            idx = self._source_combo.count() - 1
            p = resolve_nuke_config_path(name)
            tip_lines = [
                "Uses OCIO from your Nuke install (not redistributed).",
            ]
            if p is not None:
                tip_lines.append(str(p))
            if not compatible:
                tip_lines.append("")
                tip_lines.append(
                    "Unavailable with this app’s OpenColorIO "
                    f"({__import__('PyOpenColorIO').GetVersion()})."
                )
                if detail:
                    tip_lines.append(detail)
                tip_lines.append(
                    "Use the bundled ACES Studio config, or run make ensure-ocio "
                    "if OpenColorIO was downgraded by oiio-python."
                )
                self._set_combo_item_enabled(idx, False)
            self._source_combo.setItemData(
                idx,
                "\n".join(tip_lines),
                Qt.ItemDataRole.ToolTipRole,
            )
        if self._nuke_configs:
            self._source_combo.insertSeparator(self._source_combo.count())

        for name, label, recommended in self._builtin_configs:
            short = label
            if recommended:
                short += "  \u2605"
            self._source_combo.addItem(short, name)
        self._source_combo.insertSeparator(self._source_combo.count())
        self._custom_file_idx = self._source_combo.count()
        self._update_custom_label()

        self._select_saved_source()
        self._prev_index = self._source_combo.currentIndex()

        self._source_combo.currentIndexChanged.connect(self._on_source_changed)

    def _update_custom_label(self) -> None:
        if self._file_path:
            label = f"Custom: {Path(self._file_path).name}"
        else:
            label = "Custom config file\u2026"
        if self._source_combo.count() > self._custom_file_idx:
            self._source_combo.setItemText(self._custom_file_idx, label)
        else:
            self._source_combo.addItem(label, OCIO_SOURCE_FILE)

    def _set_combo_item_enabled(self, index: int, enabled: bool) -> None:
        """Grey out a combo row (still visible) when *enabled* is False."""
        from PySide6.QtGui import QColor, QStandardItemModel

        model = self._source_combo.model()
        if not isinstance(model, QStandardItemModel):
            return
        item = model.item(index)
        if item is None:
            return
        flags = item.flags()
        if enabled:
            item.setFlags(flags | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            item.setForeground(
                self._source_combo.palette().color(self._source_combo.foregroundRole())
            )
        else:
            item.setFlags(flags & ~Qt.ItemFlag.ItemIsEnabled)
            item.setForeground(QColor("#666666"))

    def _combo_item_is_enabled(self, index: int) -> bool:
        from PySide6.QtGui import QStandardItemModel

        model = self._source_combo.model()
        if not isinstance(model, QStandardItemModel):
            return True
        item = model.item(index)
        if item is None:
            return True
        return bool(item.flags() & Qt.ItemFlag.ItemIsEnabled)

    def _select_saved_source(self) -> None:
        saved = self._settings.value("ocio/source", "")
        if not saved:
            env_ocio = os.environ.get("OCIO", "")
            if env_ocio and Path(env_ocio).expanduser().is_file():
                saved = OCIO_SOURCE_ENV
            else:
                # Prefer our bundled rich camera config as the awesome default
                if self._app_configs:
                    saved = self._app_configs[0][0]
                else:
                    recommended = [b for b in self._builtin_configs if b[2]]
                    saved = recommended[0][0] if recommended else self._builtin_configs[-1][0]
        for i in range(self._source_combo.count()):
            if self._source_combo.itemData(i) == saved:
                if self._combo_item_is_enabled(i):
                    self._source_combo.setCurrentIndex(i)
                    return
                # Saved Nuke config is no longer loadable — fall through.
                break
        # Prefer first enabled non-env item if saved was incompatible.
        for i in range(self._source_combo.count()):
            data = self._source_combo.itemData(i)
            if data is None:
                continue
            if self._combo_item_is_enabled(i) and data != OCIO_SOURCE_ENV:
                self._source_combo.setCurrentIndex(i)
                return
        self._source_combo.setCurrentIndex(0)

    def current_source_key(self) -> str:
        return self._source_combo.currentData() or ""

    def set_custom_config_file(self, path: str) -> bool:
        """Force the custom OCIO file source to *path* (used by GUI launch args / Nuke).

        Returns True if the path exists and was selected.
        """
        p = Path(path).expanduser()
        if not p.is_file():
            return False
        self._file_path = str(p)
        self._settings.setValue("ocio/file_path", str(p))
        self._settings.setValue("ocio/source", OCIO_SOURCE_FILE)
        self._update_custom_label()
        for i in range(self._source_combo.count()):
            if self._source_combo.itemData(i) == OCIO_SOURCE_FILE:
                self._source_combo.blockSignals(True)
                self._source_combo.setCurrentIndex(i)
                self._source_combo.blockSignals(False)
                self._prev_index = i
                break
        self.config_changed.emit()
        return True

    def load_config(self):  # -> OCIO.Config | None
        source = self.current_source_key()
        file_path = self._file_path
        try:
            cfg = resolve_ocio_config(source, file_path=file_path)
        except Exception as e:
            self._status.setText(f"\u2718  {e}")
            self._status.setStyleSheet(STATUS_ERR)
            return None

        n = len(list(cfg.getColorSpaceNames()))
        if source == OCIO_SOURCE_ENV:
            desc = f"$OCIO: {os.environ.get('OCIO', '?')}"
        elif source == OCIO_SOURCE_FILE:
            desc = f"File: {Path(file_path).name}"
        elif source == OCIO_SOURCE_BUNDLED or source == BUNDLED_ACES_STUDIO_KEY:
            desc = "ACES Studio (bundled)"
        elif is_nuke_source_key(source):
            from ..core.nuke_discover import nuke_source_label, resolve_nuke_config_path

            desc = nuke_source_label(source)
            p = resolve_nuke_config_path(source)
            if p is not None:
                self._status.setToolTip(str(p))
        else:
            desc = source
        self._status.setText(f"\u2714  {desc}  ({n} color spaces)")
        self._status.setStyleSheet(STATUS_OK)
        return cfg

    def _on_source_changed(self, _idx: int) -> None:
        # Disabled (incompatible) rows should not be selectable; if Qt still
        # delivers the change, bounce back to the previous enabled index.
        if not self._combo_item_is_enabled(_idx):
            self._source_combo.blockSignals(True)
            self._source_combo.setCurrentIndex(self._prev_index)
            self._source_combo.blockSignals(False)
            return

        source = self.current_source_key()
        if source == OCIO_SOURCE_FILE:
            path, _ = QFileDialog.getOpenFileName(
                self,
                "OCIO config file",
                self._file_path or "",
                "OCIO (*.ocio);;All (*.*)",
            )
            if path:
                self._file_path = path
                self._settings.setValue("ocio/file_path", path)
                self._update_custom_label()
                self._prev_index = self._source_combo.currentIndex()
                self._settings.setValue("ocio/source", source)
                self.config_changed.emit()
            else:
                self._source_combo.blockSignals(True)
                self._source_combo.setCurrentIndex(self._prev_index)
                self._source_combo.blockSignals(False)
        else:
            self._prev_index = self._source_combo.currentIndex()
            self._settings.setValue("ocio/source", source)
            self.config_changed.emit()


# ---------------------------------------------------------------------------
# Shared places sidebar for browser dialogs
# ---------------------------------------------------------------------------

_OS_PLACES: list[tuple[str, str, QStandardPaths.StandardLocation]] = [
    ("\U0001f3e0", "Home", QStandardPaths.StandardLocation.HomeLocation),
    ("\U0001f5a5\ufe0f", "Desktop", QStandardPaths.StandardLocation.DesktopLocation),
    ("\U0001f4c4", "Documents", QStandardPaths.StandardLocation.DocumentsLocation),
    ("\u2b07\ufe0f", "Downloads", QStandardPaths.StandardLocation.DownloadLocation),
    ("\U0001f3ac", "Movies", QStandardPaths.StandardLocation.MoviesLocation),
]


def _copy_to_clipboard(text: str) -> None:
    if text:
        QGuiApplication.clipboard().setText(text)


# SizeGrip lives in :mod:`src.gui.size_grip` (imported by window / slate_widgets)
# so the slate package does not create an import cycle with this module.


class _FavoritesDropList(QListWidget):
    """List widget that accepts folders dropped from the directory tree or OS."""

    dirs_dropped = Signal(list)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setAcceptDrops(True)

    @staticmethod
    def _dropped_dirs(event) -> list[str]:
        md = event.mimeData()
        if not md.hasUrls():
            return []
        return [p for url in md.urls() if (p := url.toLocalFile()) and Path(p).is_dir()]

    def dragEnterEvent(self, event) -> None:
        if self._dropped_dirs(event):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        dirs = self._dropped_dirs(event)
        if dirs:
            self.dirs_dropped.emit(dirs)
            event.acceptProposedAction()
        else:
            super().dropEvent(event)


class _PlacesSidebar(QWidget):
    """Sidebar listing OS locations and user-defined favorite directories."""

    navigate_requested = Signal(str)
    _FAVORITES_KEY = "browser/favorites"

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedWidth(140)
        self._current_dir = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._list = _FavoritesDropList()
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._ctx_menu)
        self._list.dirs_dropped.connect(self._on_dirs_dropped)

        for icon, name, location in _OS_PLACES:
            path = QStandardPaths.writableLocation(location)
            if not path or not Path(path).is_dir():
                continue
            item = QListWidgetItem(f"{icon}  {name}")
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(path)
            self._list.addItem(item)

        divider = QListWidgetItem()
        divider.setFlags(Qt.ItemFlag.NoItemFlags)
        divider.setSizeHint(divider.sizeHint().expandedTo(QWidget().sizeHint()))
        self._list.addItem(divider)

        from .style import _PALETTE

        frame = QWidget()
        frame_layout = QVBoxLayout(frame)
        frame_layout.setContentsMargins(4, 6, 4, 4)
        frame_layout.setSpacing(0)
        line = QWidget()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background: {_PALETTE['BORDER']};")
        frame_layout.addWidget(line)
        self._list.setItemWidget(divider, frame)

        header = QListWidgetItem("\u2605  Favorites")
        header.setFlags(Qt.ItemFlag.NoItemFlags)
        font = header.font()
        font.setBold(True)
        header.setFont(font)
        self._list.addItem(header)
        self._fav_start = self._list.count()

        settings = QSettings()
        raw = settings.value(self._FAVORITES_KEY, [])
        if isinstance(raw, str):
            favs: list[str] = [raw] if raw else []
        elif isinstance(raw, (list, tuple)):
            favs = [str(x) for x in raw]
        else:
            favs = []
        for fav_path in favs:
            if Path(fav_path).is_dir():
                self._add_fav_item(fav_path)

        layout.addWidget(self._list, 1)

        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(2, 2, 2, 2)
        btn_row.setSpacing(2)
        add_btn = QToolButton()
        add_btn.setText("+")
        add_btn.setToolTip("Add current folder to favorites")
        add_btn.setAutoRaise(True)
        add_btn.clicked.connect(self._add_current)
        rm_btn = QToolButton()
        rm_btn.setText("\u2212")
        rm_btn.setToolTip("Remove selected favorite")
        rm_btn.setAutoRaise(True)
        rm_btn.clicked.connect(self._remove_selected)
        btn_row.addWidget(add_btn)
        btn_row.addWidget(rm_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self._list.itemClicked.connect(self._on_clicked)

    def set_current_dir(self, path: str) -> None:
        self._current_dir = path

    def _add_fav_item(self, path: str) -> None:
        name = Path(path).name or path
        item = QListWidgetItem(f"\U0001f4c1  {name}")
        item.setData(Qt.ItemDataRole.UserRole, path)
        item.setToolTip(path)
        self._list.addItem(item)

    def _on_clicked(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.ItemDataRole.UserRole)
        if path and Path(path).is_dir():
            self.navigate_requested.emit(path)

    def _add_current(self) -> None:
        self.add_favorite(self._current_dir)

    def add_favorite(self, path: str) -> None:
        if not path or not Path(path).is_dir():
            return
        for i in range(self._fav_start, self._list.count()):
            if self._list.item(i).data(Qt.ItemDataRole.UserRole) == path:
                return
        self._add_fav_item(path)
        self._save_favorites()

    def _on_dirs_dropped(self, paths: list[str]) -> None:
        for path in paths:
            self.add_favorite(path)

    def _remove_selected(self) -> None:
        row = self._list.currentRow()
        if row >= self._fav_start:
            self._list.takeItem(row)
            self._list.setCurrentRow(-1)
            self._save_favorites()

    def _ctx_menu(self, pos) -> None:
        item = self._list.itemAt(pos)
        if not item:
            return
        path = item.data(Qt.ItemDataRole.UserRole)
        if not path:
            return
        row = self._list.row(item)
        menu = QMenu(self)
        menu.addAction("Copy Full Path", lambda: _copy_to_clipboard(path))
        if row >= self._fav_start:
            menu.addAction("Remove from Favorites", lambda: self._remove_row(row))
        menu.exec(self._list.viewport().mapToGlobal(pos))

    def _remove_row(self, row: int) -> None:
        if row >= self._fav_start:
            self._list.takeItem(row)
            self._list.setCurrentRow(-1)
            self._save_favorites()

    def _save_favorites(self) -> None:
        favs = []
        for i in range(self._fav_start, self._list.count()):
            path = self._list.item(i).data(Qt.ItemDataRole.UserRole)
            if path:
                favs.append(path)
        QSettings().setValue(self._FAVORITES_KEY, favs)


def _setup_dir_tree(tree: QTreeView, fs_model: QFileSystemModel, places: _PlacesSidebar) -> None:
    """Enable dragging folders to favorites + a right-click menu on a dir tree."""
    tree.setDragEnabled(True)
    tree.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
    tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    # Expand/collapse is handled on single-click of the row (see
    # :func:`_tree_click_toggle_expand`); double-click should not also toggle.
    tree.setExpandsOnDoubleClick(False)

    def _menu(pos) -> None:
        idx = tree.indexAt(pos)
        if not idx.isValid():
            return
        path = fs_model.filePath(idx)
        if not path:
            return
        menu = QMenu(tree)
        if Path(path).is_dir():
            menu.addAction("Add to Favorites", lambda: places.add_favorite(path))
        menu.addAction("Copy Full Path", lambda: _copy_to_clipboard(path))
        menu.exec(tree.viewport().mapToGlobal(pos))

    tree.customContextMenuRequested.connect(_menu)


def _tree_click_toggle_expand(tree: QTreeView, fs_model: QFileSystemModel, index) -> None:
    """Single-click folder: expand if collapsed, collapse if already expanded.

    Branch-indicator clicks are handled by QTreeView itself (and do not emit
    ``clicked``), so this only runs for row/label clicks — first click opens
    the folder in the tree, second click closes it.
    """
    if not index.isValid() or not fs_model.isDir(index):
        return
    if tree.isExpanded(index):
        tree.collapse(index)
    else:
        tree.expand(index)


# ---------------------------------------------------------------------------
# Directory search: background worker + searchable tree panel
# ---------------------------------------------------------------------------

_SEARCH_SKIP_DIRS = frozenset(
    {
        # VCS
        ".git",
        ".svn",
        ".hg",
        ".bzr",
        # Python
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "node_modules",
        ".venv",
        "venv",
        ".env",
        ".tox",
        ".nox",
        "build",
        "dist",
        ".eggs",
        "site-packages",
        # IDE
        ".idea",
        ".vscode",
        ".vs",
        # Temp / cache
        ".cache",
        ".tmp",
        ".temp",
        "tmp",
        "temp",
        # macOS
        ".Trash",
        ".Spotlight-V100",
        ".fseventsd",
        ".DocumentRevisions-V100",
        ".TemporaryItems",
        ".VolumeIcon.icns",
        # System / library dirs (by name — catches nested occurrences too)
        "System",
        "Library",
        "private",
        "usr",
        "bin",
        "sbin",
        "etc",
        "var",
        "opt",
        # Windows
        "Windows",
        "ProgramData",
        "Program Files",
        "Program Files (x86)",
        "$Recycle.Bin",
        "System Volume Information",
        "AppData",
        "Recovery",
        "PerfLogs",
        # Linux
        "proc",
        "sys",
        "dev",
        "run",
        "snap",
        "lost+found",
        # Package / app internals
        ".app",
        ".framework",
        ".bundle",
        ".plugin",
        ".kext",
        "__MACOSX",
        "Frameworks",
        "PlugIns",
    }
)

_SEARCH_SKIP_ABSPATHS: frozenset[str] = frozenset(
    {
        "/System",
        "/Library",
        "/private",
        "/usr",
        "/bin",
        "/sbin",
        "/etc",
        "/var",
        "/opt",
        "/cores",
        "/dev",
        "/proc",
        "/sys",
        "/run",
        "/snap",
        "C:\\Windows",
        "C:\\Program Files",
        "C:\\Program Files (x86)",
        "C:\\ProgramData",
        "C:\\$Recycle.Bin",
        "C:\\Recovery",
    }
)

_SEARCH_SKIP_FILES = frozenset(
    {
        ".DS_Store",
        "Thumbs.db",
        "desktop.ini",
        "Icon\r",
        ".localized",
        ".CFUserTextEncoding",
        ".com.apple.timemachine.donotpresent",
    }
)
_SEARCH_SKIP_SUFFIXES = frozenset(
    {
        ".pyc",
        ".pyo",
        ".o",
        ".obj",
        ".class",
        ".swp",
        ".swo",
        ".swn",
        ".tmp",
        ".bak",
        ".orig",
    }
)

_VIDEO_EXTS = frozenset(
    {
        ".mp4",
        ".mov",
        ".mkv",
        ".avi",
        ".mxf",
        ".webm",
        ".m4v",
        ".ts",
        ".wmv",
        ".flv",
        ".f4v",
        ".vob",
        ".ogv",
        ".ogg",
        ".3gp",
        ".3g2",
        ".m2ts",
        ".mts",
        ".mpg",
        ".mpeg",
        ".m2v",
        ".divx",
        ".rm",
        ".rmvb",
        ".asf",
        ".dv",
        ".r3d",
        ".braw",
        ".ari",
        ".arx",
        ".mj2",
    }
)

_MAX_SEARCH_DEPTH = 15
_SEARCH_BATCH_SIZE = 60
_MAX_SEARCH_RESULTS = 500
_SEARCH_DEBOUNCE_MS = 200


class _DirSearchWorker(QObject):
    """Runs recursive directory searches on a background thread.

    Each call to ``start_search`` cancels any in-flight search, creates a
    fresh ``threading.Event``, and spawns a daemon thread.  Results stream
    back via ``batch_ready`` in chunks for minimal signal overhead.
    """

    batch_ready = Signal(list)
    search_finished = Signal(int)

    def __init__(
        self,
        parent: QObject | None = None,
        ext_filter: frozenset[str] | None = None,
        dirs_only: bool = False,
    ):
        super().__init__(parent)
        self._cancel: threading.Event = threading.Event()
        self._ext_filter = ext_filter
        self._dirs_only = dirs_only

    def start_search(self, root: str, query: str) -> None:
        self._cancel.set()
        cancel = threading.Event()
        self._cancel = cancel
        threading.Thread(target=self._run, args=(root, query, cancel), daemon=True).start()

    def cancel(self) -> None:
        self._cancel.set()

    def _run(self, root: str, query: str, cancel: threading.Event) -> None:
        query_lower = query.lower()
        root_stripped = root.rstrip(os.sep)
        root_len = len(root_stripped) + 1
        batch: list[tuple[str, str, str, bool]] = []
        total = 0

        stack: list[tuple[str, int]] = [(root_stripped, 0)]
        while stack:
            if cancel.is_set():
                return
            dirpath, depth = stack.pop()
            try:
                scanner = os.scandir(dirpath)
            except OSError:
                continue
            with scanner:
                for entry in scanner:
                    if cancel.is_set():
                        return
                    name = entry.name
                    try:
                        is_dir = entry.is_dir(follow_symlinks=False)
                    except OSError:
                        continue

                    name_lower = name.lower()
                    ext_lower = os.path.splitext(name_lower)[1]

                    if not is_dir and (
                        self._dirs_only
                        or name in _SEARCH_SKIP_FILES
                        or ext_lower in _SEARCH_SKIP_SUFFIXES
                        or (self._ext_filter is not None and ext_lower not in self._ext_filter)
                    ):
                        pass
                    elif query_lower in name_lower or query_lower == ext_lower:
                        rel = entry.path[root_len:]
                        batch.append((name, entry.path, rel, is_dir))
                        total += 1
                        if len(batch) >= _SEARCH_BATCH_SIZE:
                            self.batch_ready.emit(batch)
                            batch = []
                        if total >= _MAX_SEARCH_RESULTS:
                            if batch:
                                self.batch_ready.emit(batch)
                            self.search_finished.emit(total)
                            return

                    if (
                        is_dir
                        and depth < _MAX_SEARCH_DEPTH
                        and name not in _SEARCH_SKIP_DIRS
                        and not name.startswith(".")
                        and entry.path not in _SEARCH_SKIP_ABSPATHS
                    ):
                        stack.append((entry.path, depth + 1))

        if batch:
            self.batch_ready.emit(batch)
        self.search_finished.emit(total)


class _SearchableTree(QWidget):
    """Search bar + directory tree, with inline search results that replace
    the tree while a query is active.

    Typing in the search field triggers a debounced background scan.
    Clicking a result emits ``result_navigated`` with the directory path.
    Clearing the field (or pressing Escape) restores the tree.
    """

    result_navigated = Signal(str)

    def __init__(
        self,
        tree: QTreeView,
        parent: QWidget | None = None,
        ext_filter: frozenset[str] | None = None,
        dirs_only: bool = False,
    ):
        super().__init__(parent)
        self._tree = tree
        self._search_root = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("\U0001f50d  Search folders\u2026")
        self._search_edit.setClearButtonEnabled(True)
        layout.addWidget(self._search_edit)

        self._spinner_frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        self._spinner_idx = 0
        self._spinner_action = QAction(self._search_edit)
        self._search_edit.addAction(self._spinner_action, QLineEdit.ActionPosition.TrailingPosition)
        self._spinner_action.setVisible(False)
        self._spinner_timer = QTimer(self)
        self._spinner_timer.setInterval(80)
        self._spinner_timer.timeout.connect(self._advance_spinner)

        layout.addWidget(tree, 1)

        self._results = QListWidget()
        self._results.setVisible(False)
        layout.addWidget(self._results, 1)

        self._search_status = QLabel()
        self._search_status.setVisible(False)
        self._search_status.setStyleSheet(STATUS_DIM)
        layout.addWidget(self._search_status)

        self._worker = _DirSearchWorker(self, ext_filter=ext_filter, dirs_only=dirs_only)
        # Emits come from plain threading.Thread — must queue onto the GUI thread.
        self._worker.batch_ready.connect(self._on_batch, Qt.ConnectionType.QueuedConnection)
        self._worker.search_finished.connect(
            self._on_search_done, Qt.ConnectionType.QueuedConnection
        )

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(_SEARCH_DEBOUNCE_MS)
        self._debounce.timeout.connect(self._fire_search)

        self._search_edit.textChanged.connect(self._on_text_changed)
        self._results.itemClicked.connect(self._on_result_clicked)
        self._results.itemDoubleClicked.connect(self._on_result_clicked)

    def set_search_root(self, path: str) -> None:
        self._search_root = path

    def _advance_spinner(self) -> None:
        ch = self._spinner_frames[self._spinner_idx % len(self._spinner_frames)]
        self._spinner_idx += 1
        size = self._search_edit.fontMetrics().height()
        pix = QPixmap(size, size)
        pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(pix)
        p.setPen(self._search_edit.palette().text().color())
        p.setFont(self._search_edit.font())
        p.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, ch)
        p.end()
        self._spinner_action.setIcon(QIcon(pix))

    def _start_spinner(self) -> None:
        self._spinner_idx = 0
        self._spinner_action.setVisible(True)
        self._advance_spinner()
        self._spinner_timer.start()

    def _stop_spinner(self) -> None:
        self._spinner_timer.stop()
        self._spinner_action.setVisible(False)

    def _on_text_changed(self, text: str) -> None:
        if not text.strip():
            self._worker.cancel()
            self._debounce.stop()
            self._stop_spinner()
            self._results.setVisible(False)
            self._tree.setVisible(True)
            self._search_status.setVisible(False)
            return
        self._debounce.start()

    def _fire_search(self) -> None:
        query = self._search_edit.text().strip()
        if not query:
            return
        self._results.clear()
        self._results.setVisible(True)
        self._tree.setVisible(False)
        self._search_status.setVisible(True)
        self._search_status.setText("Searching\u2026")
        self._start_spinner()
        root = self._search_root or QDir.homePath()
        self._worker.start_search(root, query)

    def _on_batch(self, items: list) -> None:
        for name, _full_path, rel_path, is_dir in items:
            icon = "\U0001f4c1" if is_dir else "\U0001f4c4"
            parent = os.path.dirname(rel_path)
            display = f"{icon}  {name}    {parent}" if parent else f"{icon}  {name}"
            item = QListWidgetItem(display)
            item.setData(Qt.ItemDataRole.UserRole, _full_path)
            item.setToolTip(_full_path)
            self._results.addItem(item)

    def _on_search_done(self, total: int) -> None:
        self._stop_spinner()
        suffix = "s" if total != 1 else ""
        cap = " (limit reached)" if total >= _MAX_SEARCH_RESULTS else ""
        self._search_status.setText(f"{total} result{suffix}{cap}")

    def _on_result_clicked(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.ItemDataRole.UserRole)
        if not path:
            return
        target = path if os.path.isdir(path) else os.path.dirname(path)
        self.result_navigated.emit(target)
        self._search_edit.clear()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape and self._search_edit.text():
            self._search_edit.clear()
            event.accept()
            return
        super().keyPressEvent(event)


# ---------------------------------------------------------------------------
# EXR sequence browser dialog (with metadata inspector)
# ---------------------------------------------------------------------------


class SequenceBrowserDialog(QDialog):
    """Directory browser + EXR sequence table + toggleable metadata panel."""

    _COLUMNS = [
        "Name",
        "Frames",
        "Range",
        "Resolution",
        "Type",
        "Compression",
        "Color Space",
    ]

    def __init__(
        self,
        start_dir: str = "",
        select_name: str = "",
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Browse EXR Sequences")
        self.resize(1060, 450)
        self._selected_dir: str = ""
        self._selected_name: str = ""
        # First-frame path of the selected sequence (preferred for set_input so
        # multi-sequence folders resolve to the chosen basename, not sorted[0]).
        self._selected_frame_path: str = ""
        self._seq_data: list[dict] = []
        self._auto_select_name = select_name

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        # path bar + inspect toggle
        path_row = QHBoxLayout()
        path_row.addWidget(QLabel("Folder:"))
        self._path_edit = QLineEdit()
        self._path_edit.setPlaceholderText("Navigate in the tree or paste a path here")
        path_row.addWidget(self._path_edit, 1)
        self._inspect_cb = QCheckBox("Inspect")
        self._inspect_cb.setToolTip("Show EXR metadata for selected sequence")
        path_row.addWidget(self._inspect_cb)
        layout.addLayout(path_row)

        # -- left: places sidebar + dir tree (full height) --
        self._places = _PlacesSidebar()
        self._places.navigate_requested.connect(self._navigate_to)

        self._fs_model = QFileSystemModel()
        self._fs_model.setRootPath(QDir.rootPath())
        self._fs_model.setFilter(QDir.Filter.Dirs | QDir.Filter.NoDotAndDotDot)
        self._tree = QTreeView()
        self._tree.setModel(self._fs_model)
        self._tree.setHeaderHidden(True)
        for col in range(1, self._fs_model.columnCount()):
            self._tree.hideColumn(col)
        self._tree.setMinimumWidth(200)
        tree_header = self._tree.header()
        tree_header.setStretchLastSection(True)
        tree_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)

        self._searchable_tree = _SearchableTree(self._tree, dirs_only=True)
        self._searchable_tree.result_navigated.connect(self._navigate_to)
        _setup_dir_tree(self._tree, self._fs_model, self._places)

        left_panel = QWidget()
        left_layout = QHBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(4)
        left_layout.addWidget(self._places)
        left_layout.addWidget(self._searchable_tree, 1)

        # -- center: sequence table --
        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(4)

        self._table = QTableWidget(0, len(self._COLUMNS))
        self._table.setHorizontalHeaderLabels(self._COLUMNS)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.setMinimumWidth(320)
        th = self._table.horizontalHeader()
        th.setStretchLastSection(False)
        th.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, len(self._COLUMNS)):
            th.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        center_layout.addWidget(self._table, 1)

        # -- right: metadata inspector --
        self._meta_panel = QWidget()
        meta_layout = QVBoxLayout(self._meta_panel)
        meta_layout.setContentsMargins(0, 0, 0, 0)
        meta_layout.setSpacing(4)
        meta_layout.addWidget(QLabel("<b>EXR Metadata</b>"))
        self._meta_text = QPlainTextEdit()
        self._meta_text.setReadOnly(True)
        self._meta_text.setMinimumWidth(240)
        self._meta_text.setObjectName("metaPane")
        meta_layout.addWidget(self._meta_text, 1)
        self._meta_panel.setVisible(False)

        # content splitter: table + metadata
        self._content_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._content_splitter.addWidget(center)
        self._content_splitter.addWidget(self._meta_panel)
        self._content_splitter.setStretchFactor(0, 3)
        self._content_splitter.setStretchFactor(1, 2)
        for i in range(self._content_splitter.count()):
            self._content_splitter.setCollapsible(i, False)

        # right side: content splitter + status/buttons row
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)
        right_layout.addWidget(self._content_splitter, 1)

        bottom_row = QHBoxLayout()
        self._status = QLabel()
        self._status.setStyleSheet(STATUS_DIM)
        bottom_row.addWidget(self._status, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Open).clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self._ok_btn = buttons.button(QDialogButtonBox.StandardButton.Open)
        self._ok_btn.setEnabled(False)
        bottom_row.addWidget(buttons)
        right_layout.addLayout(bottom_row)

        # outer splitter: left panel (full height) | right side
        self._outer_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._outer_splitter.addWidget(left_panel)
        self._outer_splitter.addWidget(right_widget)
        self._outer_splitter.setStretchFactor(0, 2)
        self._outer_splitter.setStretchFactor(1, 5)
        for i in range(self._outer_splitter.count()):
            self._outer_splitter.setCollapsible(i, False)
        layout.addWidget(self._outer_splitter, 1)
        self._tree.clicked.connect(self._on_tree_clicked)
        self._table.itemSelectionChanged.connect(self._on_table_selection)
        self._table.cellDoubleClicked.connect(self._on_table_double_clicked)
        self._path_edit.returnPressed.connect(self._on_path_entered)
        # Default Inspect on without relying on toggled side effects for selection.
        self._inspect_cb.setChecked(True)
        self._toggle_inspect(True)
        self._inspect_cb.toggled.connect(self._toggle_inspect)

        if start_dir and Path(start_dir).is_dir():
            self._navigate_to(start_dir)

    def selected_directory(self) -> str:
        """Directory that was scanned (parent of sequences)."""
        return self._selected_dir

    def selected_name(self) -> str:
        return self._selected_name

    def selected_path(self) -> str:
        """Filesystem path to open as input: first frame of the selected sequence.

        Prefer this over :meth:`selected_directory` — a directory alone always
        resolves to the first sequence on disk, which is wrong when the folder
        has several EXR sequences.
        """
        if self._selected_frame_path:
            return self._selected_frame_path
        return self._selected_dir

    def _navigate_to(self, directory: str) -> None:
        idx = self._fs_model.index(directory)
        if idx.isValid():
            self._tree.setCurrentIndex(idx)
            self._tree.scrollTo(idx, QAbstractItemView.ScrollHint.PositionAtCenter)
            self._tree.expand(idx)
        self._path_edit.setText(directory)
        self._places.set_current_dir(directory)
        self._searchable_tree.set_search_root(directory)
        self._scan_directory(directory)

    def _on_tree_clicked(self, index) -> None:
        path = self._fs_model.filePath(index)
        if not path:
            return
        _tree_click_toggle_expand(self._tree, self._fs_model, index)
        self._path_edit.setText(path)
        self._places.set_current_dir(path)
        self._searchable_tree.set_search_root(path)
        self._scan_directory(path)

    def _on_path_entered(self) -> None:
        path = self._path_edit.text().strip()
        if path and Path(path).is_dir():
            self._navigate_to(path)

    def _scan_directory(self, directory: str) -> None:
        self._table.setRowCount(0)
        self._selected_dir = directory
        self._selected_name = ""
        self._selected_frame_path = ""
        self._seq_data = []
        self._ok_btn.setEnabled(False)
        self._meta_text.clear()

        try:
            seqs = scan_exr_sequences(directory)
        except Exception as e:
            self._status.setText(f"Error: {e}")
            return

        if not seqs:
            self._status.setText("No EXR sequences in this folder.")
            return

        self._seq_data = seqs
        self._table.setRowCount(len(seqs))
        for row, s in enumerate(seqs):
            display_name = s.get("pattern", s["name"])
            name_item = QTableWidgetItem(display_name)
            name_item.setData(Qt.ItemDataRole.UserRole, s["name"])

            frames_item = QTableWidgetItem(str(s["frames"]))
            frames_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )

            range_item = QTableWidgetItem(s["range"])
            range_item.setTextAlignment(
                Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter
            )

            res_item = QTableWidgetItem(s.get("resolution", ""))
            res_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)

            type_item = QTableWidgetItem(s.get("pixel_type", ""))
            type_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)

            comp_item = QTableWidgetItem(s.get("compression", ""))
            comp_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)

            cs_item = QTableWidgetItem(s.get("colorspace", ""))
            cs_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)

            self._table.setItem(row, 0, name_item)
            self._table.setItem(row, 1, frames_item)
            self._table.setItem(row, 2, range_item)
            self._table.setItem(row, 3, res_item)
            self._table.setItem(row, 4, type_item)
            self._table.setItem(row, 5, comp_item)
            self._table.setItem(row, 6, cs_item)

        select_row = -1
        if self._auto_select_name:
            for row in range(self._table.rowCount()):
                item = self._table.item(row, 0)
                if item and item.data(Qt.ItemDataRole.UserRole) == self._auto_select_name:
                    select_row = row
                    break
        if select_row < 0 and seqs:
            # Always pick a row so Open is enabled without an extra click / Inspect dance.
            select_row = 0
        if select_row >= 0:
            self._table.selectRow(select_row)
            # selectRow does not always emit on a freshly rebuilt table — apply explicitly.
            self._apply_table_selection(select_row)

        self._status.setText(f"{len(seqs)} sequence(s) found")

    def _first_frame_for_sequence(self, name: str, directory: str, *, cached: str = "") -> str:
        """Resolve the first frame path for sequence *name* in *directory*."""
        if cached and Path(cached).is_file():
            return cached
        import fileseq

        for sq in fileseq.findSequencesOnDisk(directory):
            if (
                sq.basename().rstrip("._") == name
                and sq.extension().lower() == ".exr"
                and sq.frameSet()
            ):
                return str(sq.frame(sorted(sq.frameSet())[0]))
        return ""

    def _apply_table_selection(self, row: int) -> None:
        """Commit table row *row* as the chosen sequence (name + first frame)."""
        if row < 0 or row >= len(self._seq_data):
            self._selected_name = ""
            self._selected_frame_path = ""
            self._ok_btn.setEnabled(False)
            return
        s = self._seq_data[row]
        self._selected_name = str(s.get("name") or "")
        self._selected_dir = str(s.get("path") or self._selected_dir)
        self._selected_frame_path = self._first_frame_for_sequence(
            self._selected_name,
            self._selected_dir,
            cached=str(s.get("first_frame") or ""),
        )
        self._ok_btn.setEnabled(bool(self._selected_name and self._selected_dir))
        if self._meta_panel.isVisible():
            self._show_metadata(row)

    def _on_table_selection(self) -> None:
        rows = self._table.selectionModel().selectedRows()
        if rows:
            self._apply_table_selection(rows[0].row())
        else:
            self._selected_name = ""
            self._selected_frame_path = ""
            self._ok_btn.setEnabled(False)

    def _on_table_double_clicked(self, row: int, _col: int) -> None:
        self._apply_table_selection(row)
        if self._ok_btn.isEnabled():
            self.accept()

    def accept(self) -> None:
        # Re-sync selection in case the model lagged (Open with keyboard, etc.).
        rows = self._table.selectionModel().selectedRows()
        if rows:
            self._apply_table_selection(rows[0].row())
        if not self._selected_name:
            return
        super().accept()

    def _toggle_inspect(self, checked: bool) -> None:
        sizes = self._outer_splitter.sizes()
        self._meta_panel.setVisible(checked)
        self._outer_splitter.setSizes(sizes)
        if checked:
            rows = self._table.selectionModel().selectedRows()
            if rows:
                self._show_metadata(rows[0].row())

    def _show_metadata(self, row: int) -> None:
        if row < 0 or row >= len(self._seq_data):
            self._meta_text.setPlainText("")
            return
        s = self._seq_data[row]
        directory = s["path"]
        name = s["name"]
        first_path = self._first_frame_for_sequence(name, directory)
        if not first_path:
            self._meta_text.setPlainText("Could not locate first frame.")
            return

        meta = probe_exr_metadata(first_path)
        lines = [f"File: {Path(first_path).name}", ""]
        for k, v in meta.items():
            lines.append(f"{k}: {v}")
        self._meta_text.setPlainText("\n".join(lines))


# ---------------------------------------------------------------------------
# Video file browser dialog (with metadata inspector)
# ---------------------------------------------------------------------------


class VideoBrowserDialog(QDialog):
    """Directory browser + video file table + toggleable metadata panel."""

    _COLUMNS = ["Name", "Resolution", "Codec", "FPS", "Frames", "Duration"]

    def __init__(self, start_dir: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Browse Video Files")
        self.resize(1060, 450)
        self._selected_path: str = ""
        self._file_data: list[dict[str, str]] = []
        self._auto_select_path: str = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        path_row = QHBoxLayout()
        path_row.addWidget(QLabel("Folder:"))
        self._path_edit = QLineEdit()
        self._path_edit.setPlaceholderText("Navigate in the tree or paste a path here")
        path_row.addWidget(self._path_edit, 1)
        self._inspect_cb = QCheckBox("Inspect")
        self._inspect_cb.setToolTip("Show video metadata for selected file")
        path_row.addWidget(self._inspect_cb)
        layout.addLayout(path_row)

        # -- left: places sidebar + dir tree (full height) --
        self._places = _PlacesSidebar()
        self._places.navigate_requested.connect(self._navigate_to)

        self._fs_model = QFileSystemModel()
        self._fs_model.setRootPath(QDir.rootPath())
        self._fs_model.setFilter(QDir.Filter.Dirs | QDir.Filter.NoDotAndDotDot)
        self._tree = QTreeView()
        self._tree.setModel(self._fs_model)
        self._tree.setHeaderHidden(True)
        for col in range(1, self._fs_model.columnCount()):
            self._tree.hideColumn(col)
        self._tree.setMinimumWidth(200)
        tree_header = self._tree.header()
        tree_header.setStretchLastSection(True)
        tree_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)

        self._searchable_tree = _SearchableTree(self._tree, ext_filter=_VIDEO_EXTS)
        self._searchable_tree.result_navigated.connect(self._navigate_to)
        _setup_dir_tree(self._tree, self._fs_model, self._places)

        left_panel = QWidget()
        left_layout = QHBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(4)
        left_layout.addWidget(self._places)
        left_layout.addWidget(self._searchable_tree, 1)

        # -- center: video file table --
        center = QWidget()
        center_layout = QVBoxLayout(center)
        center_layout.setContentsMargins(0, 0, 0, 0)
        center_layout.setSpacing(4)

        self._table = QTableWidget(0, len(self._COLUMNS))
        self._table.setHorizontalHeaderLabels(self._COLUMNS)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.verticalHeader().setVisible(False)
        self._table.setMinimumWidth(320)
        th = self._table.horizontalHeader()
        th.setStretchLastSection(False)
        th.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, len(self._COLUMNS)):
            th.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        center_layout.addWidget(self._table, 1)

        # -- right: metadata inspector --
        self._meta_panel = QWidget()
        meta_layout = QVBoxLayout(self._meta_panel)
        meta_layout.setContentsMargins(0, 0, 0, 0)
        meta_layout.setSpacing(4)
        meta_layout.addWidget(QLabel("<b>Video Metadata</b>"))
        self._meta_text = QPlainTextEdit()
        self._meta_text.setReadOnly(True)
        self._meta_text.setMinimumWidth(240)
        self._meta_text.setObjectName("metaPane")
        meta_layout.addWidget(self._meta_text, 1)
        self._meta_panel.setVisible(False)

        # content splitter: table + metadata
        self._content_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._content_splitter.addWidget(center)
        self._content_splitter.addWidget(self._meta_panel)
        self._content_splitter.setStretchFactor(0, 3)
        self._content_splitter.setStretchFactor(1, 2)
        for i in range(self._content_splitter.count()):
            self._content_splitter.setCollapsible(i, False)

        # right side: content splitter + status/buttons row
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)
        right_layout.addWidget(self._content_splitter, 1)

        bottom_row = QHBoxLayout()
        self._status = QLabel()
        self._status.setStyleSheet(STATUS_DIM)
        bottom_row.addWidget(self._status, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Open).clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self._ok_btn = buttons.button(QDialogButtonBox.StandardButton.Open)
        self._ok_btn.setEnabled(False)
        bottom_row.addWidget(buttons)
        right_layout.addLayout(bottom_row)

        # outer splitter: left panel (full height) | right side
        self._outer_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._outer_splitter.addWidget(left_panel)
        self._outer_splitter.addWidget(right_widget)
        self._outer_splitter.setStretchFactor(0, 2)
        self._outer_splitter.setStretchFactor(1, 5)
        for i in range(self._outer_splitter.count()):
            self._outer_splitter.setCollapsible(i, False)
        layout.addWidget(self._outer_splitter, 1)

        self._tree.clicked.connect(self._on_tree_clicked)
        self._table.itemSelectionChanged.connect(self._on_table_selection)
        self._table.cellDoubleClicked.connect(lambda _r, _c: self.accept())
        self._path_edit.returnPressed.connect(self._on_path_entered)
        self._inspect_cb.toggled.connect(self._toggle_inspect)

        if start_dir:
            d = Path(start_dir)
            if d.is_file():
                self._auto_select_path = str(d)
                d = d.parent
            if d.is_dir():
                self._navigate_to(str(d))

    def selected_path(self) -> str:
        return self._selected_path

    def _navigate_to(self, directory: str) -> None:
        idx = self._fs_model.index(directory)
        if idx.isValid():
            self._tree.setCurrentIndex(idx)
            self._tree.scrollTo(idx, QAbstractItemView.ScrollHint.PositionAtCenter)
            self._tree.expand(idx)
        self._path_edit.setText(directory)
        self._places.set_current_dir(directory)
        self._searchable_tree.set_search_root(directory)
        self._scan_directory(directory)

    def _on_tree_clicked(self, index) -> None:
        path = self._fs_model.filePath(index)
        if not path:
            return
        _tree_click_toggle_expand(self._tree, self._fs_model, index)
        self._path_edit.setText(path)
        self._places.set_current_dir(path)
        self._searchable_tree.set_search_root(path)
        self._scan_directory(path)

    def _on_path_entered(self) -> None:
        path = self._path_edit.text().strip()
        if path and Path(path).is_dir():
            self._navigate_to(path)

    def _scan_directory(self, directory: str) -> None:
        self._table.setRowCount(0)
        self._selected_path = ""
        self._file_data = []
        self._ok_btn.setEnabled(False)
        self._meta_text.clear()

        try:
            files = scan_video_files(directory)
        except Exception as e:
            self._status.setText(f"Error: {e}")
            return

        if not files:
            self._status.setText("No video files in this folder.")
            return

        self._file_data = files
        self._table.setRowCount(len(files))
        center_align = Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter
        for row, f in enumerate(files):
            name_item = QTableWidgetItem(f["name"])
            name_item.setData(Qt.ItemDataRole.UserRole, f["path"])

            res_item = QTableWidgetItem(f.get("resolution", ""))
            res_item.setTextAlignment(center_align)

            codec_item = QTableWidgetItem(f.get("codec", ""))
            codec_item.setTextAlignment(center_align)

            fps_item = QTableWidgetItem(f.get("fps", ""))
            fps_item.setTextAlignment(center_align)

            frames_item = QTableWidgetItem(f.get("frames", ""))
            frames_item.setTextAlignment(center_align)

            dur_item = QTableWidgetItem(f.get("duration", ""))
            dur_item.setTextAlignment(center_align)

            self._table.setItem(row, 0, name_item)
            self._table.setItem(row, 1, res_item)
            self._table.setItem(row, 2, codec_item)
            self._table.setItem(row, 3, fps_item)
            self._table.setItem(row, 4, frames_item)
            self._table.setItem(row, 5, dur_item)

        selected = False
        if self._auto_select_path:
            for row in range(self._table.rowCount()):
                item = self._table.item(row, 0)
                if item and item.data(Qt.ItemDataRole.UserRole) == self._auto_select_path:
                    self._table.selectRow(row)
                    selected = True
                    break
        if not selected and len(files) == 1:
            self._table.selectRow(0)

        self._status.setText(f"{len(files)} video file(s) found")

    def _on_table_selection(self) -> None:
        rows = self._table.selectionModel().selectedRows()
        if rows:
            row = rows[0].row()
            item = self._table.item(row, 0)
            self._selected_path = item.data(Qt.ItemDataRole.UserRole) if item else ""
            self._ok_btn.setEnabled(bool(self._selected_path))
            if self._meta_panel.isVisible():
                self._show_metadata(row)
        else:
            self._selected_path = ""
            self._ok_btn.setEnabled(False)

    def _toggle_inspect(self, checked: bool) -> None:
        sizes = self._outer_splitter.sizes()
        self._meta_panel.setVisible(checked)
        self._outer_splitter.setSizes(sizes)
        if checked:
            rows = self._table.selectionModel().selectedRows()
            if rows:
                self._show_metadata(rows[0].row())

    def _show_metadata(self, row: int) -> None:
        if row < 0 or row >= len(self._file_data):
            self._meta_text.setPlainText("")
            return
        fpath = self._file_data[row]["path"]
        meta = probe_video_metadata(fpath)
        lines = [f"File: {Path(fpath).name}", ""]
        for k, v in meta.items():
            lines.append(f"{k}: {v}")
        self._meta_text.setPlainText("\n".join(lines))


# ---------------------------------------------------------------------------
# EXR compression settings dialog
# ---------------------------------------------------------------------------

_EXR_HAS_SETTINGS = {"dwaa", "dwab", "zip", "zips"}
# Profile is selected via the codec dropdown itself for ProRes / DNxHR ladders.
_CODEC_HAS_SETTINGS = {
    "h264",
    "hevc",
    "hevc_8",
    "hevc_12",
    "cineform",
    "cineform_rgb",
}

_EXR_COMPRESSION_HELP: dict[str, str] = {
    "none": "No compression. Fastest write, largest files.",
    "rle": "Run-length encoding. Fast, good for flat areas.",
    "zip": "Zip per scanline block (16 rows). Good general-purpose.",
    "zips": "Zip per scanline. Slightly smaller than ZIP.",
    "piz": "Wavelet-based. Best lossless ratio for noisy/CG images.",
    "pxr24": "Lossy 24-bit float. Good ratio, slight precision loss.",
    "b44": "Lossy fixed-rate. Constant block size, fast random access.",
    "b44a": "Like B44 but flat areas compress further.",
    "dwaa": "Lossy DCT-based, per-scanline. Best lossy ratio at low levels.",
    "dwab": "Lossy DCT-based, per-tile (256 scanlines). Slightly better ratio than DWAA.",
}


class ExrCompressionSettingsDialog(QDialog):
    """Show settings relevant to the selected EXR compression method."""

    def __init__(
        self,
        compression: str,
        settings: QSettings,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._compression = compression
        self._settings = settings
        self.setWindowTitle(f"EXR Compression Settings — {compression.upper()}")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        desc = _EXR_COMPRESSION_HELP.get(compression, "")
        if desc:
            lbl = QLabel(desc)
            lbl.setWordWrap(True)
            lbl.setStyleSheet(DESC_STYLE)
            layout.addWidget(lbl)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self._dwa_spin: QSpinBox | None = None
        self._zip_spin: QSpinBox | None = None

        if compression in ("dwaa", "dwab"):
            saved = int(float(settings.value("exr_opts/dwa_level", 45)))
            row = QHBoxLayout()
            row.setSpacing(6)
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(0, 250)
            slider.setValue(saved)
            self._dwa_spin = QSpinBox()
            self._dwa_spin.setRange(0, 250)
            self._dwa_spin.setValue(saved)
            self._dwa_spin.setMinimumWidth(72)
            self._dwa_spin.setFixedWidth(72)
            self._dwa_spin.setAlignment(Qt.AlignmentFlag.AlignRight)
            self._dwa_spin.setToolTip(
                "0 = lossless, 45 = visually lossless (default), higher = more compression"
            )
            slider.valueChanged.connect(self._dwa_spin.setValue)
            self._dwa_spin.valueChanged.connect(slider.setValue)
            row.addWidget(slider, 1)
            row.addWidget(self._dwa_spin)
            hint = QLabel("0 = lossless · 45 = visually lossless (default) · 100+ = aggressive")
            hint.setStyleSheet(HINT_STYLE)
            form.addRow("Compression level", row)
            form.addRow("", hint)
        elif compression in ("zip", "zips"):
            saved = int(settings.value("exr_opts/zip_level", 4))
            row = QHBoxLayout()
            row.setSpacing(6)
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(1, 9)
            slider.setValue(saved)
            self._zip_spin = QSpinBox()
            self._zip_spin.setRange(1, 9)
            self._zip_spin.setValue(saved)
            self._zip_spin.setMinimumWidth(72)
            self._zip_spin.setFixedWidth(72)
            self._zip_spin.setAlignment(Qt.AlignmentFlag.AlignRight)
            self._zip_spin.setToolTip("1 = fastest, 9 = best compression")
            slider.valueChanged.connect(self._zip_spin.setValue)
            self._zip_spin.valueChanged.connect(slider.setValue)
            row.addWidget(slider, 1)
            row.addWidget(self._zip_spin)
            form.addRow("Zip level", row)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_settings(self) -> dict[str, str]:
        """Return dict of option key -> value strings."""
        result: dict[str, str] = {}
        if self._dwa_spin is not None:
            result["dwa_compression_level"] = str(self._dwa_spin.value())
        if self._zip_spin is not None:
            result["zip_level"] = str(self._zip_spin.value())
        return result

    def accept(self) -> None:
        if self._dwa_spin is not None:
            self._settings.setValue("exr_opts/dwa_level", float(self._dwa_spin.value()))
        if self._zip_spin is not None:
            self._settings.setValue("exr_opts/zip_level", self._zip_spin.value())
        super().accept()


# ---------------------------------------------------------------------------
# Video codec settings dialog
# ---------------------------------------------------------------------------

_CODEC_HELP: dict[str, str] = {
    "prores_proxy": "ProRes 422 Proxy — lightest ProRes; 10-bit 4:2:2 offline/proxy (prores_ks).",
    "prores_lt": "ProRes 422 LT — lighter intermediate; 10-bit 4:2:2 (prores_ks).",
    "prores_422": "ProRes 422 (Standard) — balanced intermediate; 10-bit 4:2:2 (prores_ks).",
    "prores": "ProRes 422 HQ — default finishing intermediate; 10-bit 4:2:2 (prores_ks).",
    "prores_4444": (
        "ProRes 4444 — 4:4:4:4 with alpha (prores_ks). FFmpeg software encodes at "
        "10-bit only (yuva444p10le); Apple's format can be 12-bit, but prores_ks "
        "does not. Prefer VideoToolbox 4444 on macOS for ~12-bit precision."
    ),
    "prores_xq": (
        "ProRes 4444 XQ — highest ProRes tier, 4:4:4:4 with alpha (prores_ks). "
        "Software encode is still 10-bit only; use prores_vt_xq on macOS for "
        "true 12-bit-class precision."
    ),
    "prores_vt_proxy": (
        "Hardware ProRes Proxy via Apple VideoToolbox. macOS only; faster, "
        "quality/controls differ slightly from software prores_ks. 10-bit 4:2:2."
    ),
    "prores_vt_lt": "Hardware ProRes LT via VideoToolbox (macOS only); 10-bit 4:2:2.",
    "prores_vt_422": "Hardware ProRes 422 via VideoToolbox (macOS only); 10-bit 4:2:2.",
    "prores_vt_hq": "Hardware ProRes HQ via VideoToolbox (macOS only); 10-bit 4:2:2.",
    "prores_vt_4444": (
        "Hardware ProRes 4444 via VideoToolbox (macOS only). ~12-bit class with "
        "ayuv64le intermediate — better precision than software prores_ks 4444."
    ),
    "prores_vt_xq": (
        "Hardware ProRes 4444 XQ via VideoToolbox (macOS only). ~12-bit class "
        "precision; preferred over software XQ when bit depth matters."
    ),
    "cineform": (
        "GoPro CineForm (cfhd) — 10-bit 4:2:2 wavelet intermediate. "
        "Quality ladder film3+…low in settings."
    ),
    "cineform_rgb": ("GoPro CineForm RGB — true 12-bit planar RGB (gbrp12le) for RGB pipelines."),
    "dnxhr_lb": "DNxHR LB — lightest Avid/Resolve profile; 8-bit 4:2:2.",
    "dnxhr_sq": "DNxHR SQ — standard quality; 8-bit 4:2:2.",
    "dnxhr_hq": "DNxHR HQ — high quality intermediate; 8-bit 4:2:2 (not 10-bit).",
    "dnxhr_hqx": "DNxHR HQX — 10-bit 4:2:2; use when 10-bit headroom is required.",
    "dnxhr_444": "DNxHR 444 — 10-bit 4:4:4 for highest chroma fidelity in DNx family.",
    "h264": ("H.264 / AVC — 8-bit 4:2:0 delivery/review. Not a grading intermediate."),
    "hevc": (
        "H.265 / HEVC — 10-bit 4:2:0 delivery (libx265 Main 10). Smaller than ProRes; "
        "not a scene-linear intermediate."
    ),
    "hevc_12": (
        "H.265 / HEVC — true 12-bit 4:2:0 (libx265 Main 12, yuv420p12le). "
        "Playback support is rarer than Main 10."
    ),
    "hevc_8": "H.265 / HEVC — 8-bit 4:2:0 delivery for maximum player compatibility.",
    "ffv1": "FFV1 — mathematically lossless 10-bit 4:4:4 archival intermediate.",
    "ffv1_12": (
        "FFV1 — mathematically lossless 12-bit 4:4:4 (yuv444p12le). "
        "Best open archival option when you need true 12-bit YUV."
    ),
}


class VideoCodecSettingsDialog(QDialog):
    """Show settings relevant to the selected video codec."""

    def __init__(
        self,
        codec_key: str,
        settings: QSettings,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._codec_key = codec_key
        self._settings = settings

        from ..core.constants import (
            CINEFORM_QUALITY_OPTIONS,
            DEFAULT_CINEFORM_QUALITY,
            X26X_PRESETS,
            video_codec_by_key,
        )

        spec = video_codec_by_key(codec_key)
        display = spec.display_name if spec else codec_key
        bit_note = spec.format_label if spec else ""
        self.setWindowTitle(f"Codec Settings — {display}")
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        desc = _CODEC_HELP.get(codec_key, "")
        if desc:
            lbl = QLabel(desc)
            lbl.setWordWrap(True)
            lbl.setStyleSheet(DESC_STYLE)
            layout.addWidget(lbl)
        if bit_note:
            bit_lbl = QLabel(f"<b>Encode format:</b> {bit_note}")
            bit_lbl.setWordWrap(True)
            bit_lbl.setStyleSheet(HINT_STYLE)
            layout.addWidget(bit_lbl)
        if spec and spec.platforms:
            plat = QLabel(
                f"<i>Platform: {', '.join(spec.platforms)} only (hidden on other OSes).</i>"
            )
            plat.setWordWrap(True)
            plat.setStyleSheet(HINT_STYLE)
            layout.addWidget(plat)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self._crf_spin: QSpinBox | None = None
        self._preset: QComboBox | None = None
        self._cineform_quality: QComboBox | None = None
        self._settings_prefix = "h264"
        if codec_key in ("hevc", "hevc_8", "hevc_12"):
            self._settings_prefix = "hevc"

        if codec_key in ("h264", "hevc", "hevc_8", "hevc_12"):
            crf_key = f"codec_opts/{self._settings_prefix}_crf"
            preset_key = f"codec_opts/{self._settings_prefix}_preset"
            saved_crf = int(settings.value(crf_key, 18))
            crf_row = QHBoxLayout()
            crf_row.setSpacing(6)
            crf_slider = QSlider(Qt.Orientation.Horizontal)
            crf_slider.setRange(0, 51)
            crf_slider.setValue(saved_crf)
            self._crf_spin = QSpinBox()
            self._crf_spin.setRange(0, 51)
            self._crf_spin.setValue(saved_crf)
            self._crf_spin.setMinimumWidth(72)
            self._crf_spin.setFixedWidth(72)
            self._crf_spin.setAlignment(Qt.AlignmentFlag.AlignRight)
            self._crf_spin.setToolTip(
                "0 = lossless, 18 = visually lossless, 23 = default, 51 = worst quality"
            )
            crf_slider.valueChanged.connect(self._crf_spin.setValue)
            self._crf_spin.valueChanged.connect(crf_slider.setValue)
            crf_row.addWidget(crf_slider, 1)
            crf_row.addWidget(self._crf_spin)
            form.addRow("CRF (quality)", crf_row)

            self._preset = QComboBox()
            for p in X26X_PRESETS:
                self._preset.addItem(p, p)
            saved_preset = settings.value(preset_key, "medium")
            idx = X26X_PRESETS.index(saved_preset) if saved_preset in X26X_PRESETS else 5
            self._preset.setCurrentIndex(idx)
            self._preset.setToolTip("Slower = better compression at same quality")
            form.addRow("Preset", self._preset)

        elif codec_key in ("cineform", "cineform_rgb"):
            self._cineform_quality = QComboBox()
            for val, label in CINEFORM_QUALITY_OPTIONS:
                self._cineform_quality.addItem(label, val)
            saved_q = str(settings.value("codec_opts/cineform_quality", DEFAULT_CINEFORM_QUALITY))
            for i in range(self._cineform_quality.count()):
                if self._cineform_quality.itemData(i) == saved_q:
                    self._cineform_quality.setCurrentIndex(i)
                    break
            self._cineform_quality.setToolTip(
                "FFmpeg cfhd quality: film3+/film3 ≈ least compression; low ≈ smallest"
            )
            form.addRow("Quality", self._cineform_quality)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_settings(self) -> dict[str, str]:
        """Return dict of option key -> value strings for PyAV stream.options."""
        result: dict[str, str] = {}
        if self._crf_spin is not None:
            result["crf"] = str(self._crf_spin.value())
        if self._preset is not None:
            result["preset"] = self._preset.currentData() or "medium"
        if self._cineform_quality is not None:
            result["quality"] = self._cineform_quality.currentData() or "film3"
        return result

    def accept(self) -> None:
        if self._codec_key in ("h264", "hevc", "hevc_8", "hevc_12"):
            if self._crf_spin:
                self._settings.setValue(
                    f"codec_opts/{self._settings_prefix}_crf",
                    self._crf_spin.value(),
                )
            if self._preset:
                self._settings.setValue(
                    f"codec_opts/{self._settings_prefix}_preset",
                    self._preset.currentData(),
                )
        elif self._codec_key in ("cineform", "cineform_rgb") and self._cineform_quality:
            self._settings.setValue(
                "codec_opts/cineform_quality",
                self._cineform_quality.currentData(),
            )
        super().accept()


# ---------------------------------------------------------------------------
# Conversion tab
# ---------------------------------------------------------------------------


class VideoInput(NamedTuple):
    """Validated video probe result — the *model* behind the video input field."""

    path: str
    width: int
    height: int
    fps: float
    frame_count: int


class _InputProbeWorker(QObject):
    """Background probe for video / EXR inputs (avoids freezing the GUI thread)."""

    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, mode: str, path: str) -> None:
        super().__init__()
        self._mode = mode
        self._path = path

    @Slot()
    def run(self) -> None:
        path = self._path
        try:
            if self._mode == "video2exr":
                from ..core.video import probe_video

                w, h, fps, total = probe_video(path)
                self.finished.emit(
                    {
                        "kind": "video",
                        "path": path,
                        "w": w,
                        "h": h,
                        "fps": fps,
                        "total": total,
                    }
                )
            else:
                from ..core.sequence import find_exr_sequence_info

                _paths, _name, frame_nums, _pad, seq = find_exr_sequence_info(path)
                self.finished.emit(
                    {
                        "kind": "exr",
                        "path": path,
                        "frame_nums": frame_nums,
                        "seq": seq,
                    }
                )
        except Exception as e:
            self.failed.emit(str(e))


class ConvertTab(QWidget):
    log_message = Signal(str)
    readiness_changed = Signal(bool)

    def __init__(self, mode: str, settings: QSettings, parent: QWidget | None = None):
        super().__init__(parent)
        self._mode = mode
        self._settings = settings
        self._ocio_cfg: object | None = None

        # Input model objects — the source of truth for what is loaded.
        # The QLineEdit is a *view* on these; is_ready() gates on them.
        self._input_seq: fileseq.FileSequence | None = None
        self._video_info: VideoInput | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # -- Input + src colorspace inline --
        in_group = QGroupBox("Input")
        in_main = QVBoxLayout(in_group)
        in_main.setSpacing(4)

        in_row = QHBoxLayout()
        self.input_path = QLineEdit()
        self.input_path.setPlaceholderText(
            "Video file (mp4, mov, mkv, \u2026)"
            if mode == "video2exr"
            else "Folder with EXRs, or any .exr from the sequence"
        )
        saved_in = str(settings.value(f"{mode}/input", "") or "").strip()
        if saved_in:
            self.input_path.setText(saved_in)
        self._browse_in = QPushButton("Browse\u2026")
        in_row.addWidget(self.input_path, 1)
        in_row.addWidget(self._browse_in)
        in_main.addLayout(in_row)

        cs_in_row = QHBoxLayout()
        cs_in_row.addWidget(QLabel("Color space:"))
        self.src_btn = ColorSpaceButton()
        cs_in_row.addWidget(self.src_btn, 1)
        in_main.addLayout(cs_in_row)

        range_row = QHBoxLayout()
        range_row.addWidget(QLabel("Frames:"))
        self._frame_range_edit = QLineEdit()
        self._frame_range_edit.setPlaceholderText("e.g. 1001-1100, 1-50x2")
        self._frame_range_edit.setToolTip(
            "Nuke-style frame range.\nExamples: 1-100, 1-10x2, 1-4 8-10"
        )
        self._frame_range_edit.setValidator(
            QRegularExpressionValidator(
                QRegularExpression(r"(\d+(-\d+)?(x\d+)?([, ] *)?)*"),
                self._frame_range_edit,
            )
        )
        range_row.addWidget(self._frame_range_edit, 1)

        self._reset_range_btn = QToolButton()
        self._reset_range_btn.setText("\u21ba")
        self._reset_range_btn.setToolTip("Reset to source range")
        self._reset_range_btn.setEnabled(False)
        self._reset_range_btn.clicked.connect(self._reset_to_source_range)
        range_row.addWidget(self._reset_range_btn)

        in_main.addLayout(range_row)
        self._full_input_range = ""

        layout.addWidget(in_group)

        # -- Output + dst colorspace inline --
        out_group = QGroupBox("Output")
        out_main = QVBoxLayout(out_group)
        out_main.setSpacing(4)

        out_row = QHBoxLayout()
        self.output_path = QLineEdit()
        self.output_path.setPlaceholderText(
            f"Output directory for EXR sequence (name.{'#' * DEFAULT_FRAME_PADDING}.exr)"
            if mode == "video2exr"
            else "Output video file (mp4, mov, \u2026)"
        )
        saved_out = settings.value(f"{mode}/output", "")
        if saved_out:
            self.output_path.setText(saved_out)
        self._browse_out = QPushButton("Browse\u2026")
        out_row.addWidget(self.output_path, 1)
        out_row.addWidget(self._browse_out)
        out_main.addLayout(out_row)

        cs_out_row = QHBoxLayout()
        cs_out_row.addWidget(QLabel("Color space:"))
        self.dst_btn = ColorSpaceButton()
        cs_out_row.addWidget(self.dst_btn, 1)
        out_main.addLayout(cs_out_row)

        layout.addWidget(out_group)

        # -- Options row: scale + mode-specific in one group --
        opts_group = QGroupBox("Options")
        opts_layout = QFormLayout(opts_group)
        opts_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.scale_combo = QComboBox()
        for scale_val, scale_label in SCALE_OPTIONS:
            self.scale_combo.addItem(scale_label, scale_val)
        saved_scale = float(settings.value(f"{mode}/scale", DEFAULT_SCALE))
        for i in range(self.scale_combo.count()):
            if abs(self.scale_combo.itemData(i) - saved_scale) < 0.01:
                self.scale_combo.setCurrentIndex(i)
                break
        self.scale_combo.currentIndexChanged.connect(
            lambda _: self._settings.setValue(f"{self._mode}/scale", self.scale_combo.currentData())
        )

        scale_row = QHBoxLayout()
        scale_row.setSpacing(12)
        scale_row.addWidget(self.scale_combo, 1)

        if mode == "exr2video":
            from ..services.slate_model import SlateModel

            self._slate_model = SlateModel(settings, mode, parent=self)

            self._slate_check = QCheckBox("Slate")
            self._slate_check.setToolTip("Prepend a 1-frame slate image before the video")
            self._slate_check.setChecked(self._slate_model.slate_enabled)
            scale_row.addWidget(self._slate_check)

            self._burnin_check = QCheckBox("Burn-in")
            self._burnin_check.setToolTip("Overlay semi-transparent text on every frame")
            self._burnin_check.setChecked(self._slate_model.burnin_enabled)
            scale_row.addWidget(self._burnin_check)

            self._watermark_check = QCheckBox("Watermark")
            self._watermark_check.setToolTip("Add a diagonal watermark across every frame")
            self._watermark_check.setChecked(self._slate_model.watermark_enabled)
            scale_row.addWidget(self._watermark_check)

            scale_row.addStretch()

            self._edit_slate_btn = QToolButton()
            self._edit_slate_btn.setText("\u270e")
            self._edit_slate_btn.setToolTip("Edit Slate & Overlay\u2026")
            self._edit_slate_btn.setAutoRaise(True)
            self._edit_slate_btn.setFixedWidth(28)
            self._edit_slate_btn.clicked.connect(self._open_slate_dialog)
            scale_row.addWidget(self._edit_slate_btn)
        else:
            self._slate_model = None
            self._slate_check = None
            self._burnin_check = None
            self._watermark_check = None

        opts_layout.addRow("Scale", scale_row)

        if mode == "video2exr":
            self.compression_combo = QComboBox()
            for c in EXR_COMPRESSIONS:
                self.compression_combo.addItem(c.upper(), c)
            saved_comp = settings.value(f"{mode}/exr_compression", DEFAULT_EXR_COMPRESSION)
            idx = EXR_COMPRESSIONS.index(saved_comp) if saved_comp in EXR_COMPRESSIONS else 0
            self.compression_combo.setCurrentIndex(idx)
            self.compression_combo.currentIndexChanged.connect(
                lambda _: self._settings.setValue(
                    f"{self._mode}/exr_compression",
                    self.compression_combo.currentData(),
                )
            )
            comp_row = QHBoxLayout()
            comp_row.setSpacing(4)
            comp_row.addWidget(self.compression_combo, 1)
            self._comp_settings_btn = QToolButton()
            self._comp_settings_btn.setText("\u2699")
            self._comp_settings_btn.setAutoRaise(True)
            self._comp_settings_btn.setFixedWidth(28)
            self._comp_settings_btn.setToolTip("Compression settings\u2026")
            self._comp_settings_btn.clicked.connect(self._open_compression_settings)
            self._update_comp_btn_state()
            self.compression_combo.currentIndexChanged.connect(
                lambda _: self._update_comp_btn_state()
            )
            comp_row.addWidget(self._comp_settings_btn)
            opts_layout.addRow("EXR Compression", comp_row)

            self.padding_spin = QSpinBox()
            self.padding_spin.setRange(1, 8)
            self.padding_spin.setValue(
                int(settings.value(f"{mode}/padding", DEFAULT_FRAME_PADDING))
            )
            self.padding_spin.setToolTip("Number of # digits in the frame number (e.g. #### = 4)")
            self.padding_spin.valueChanged.connect(
                lambda v: self._settings.setValue(f"{self._mode}/padding", v)
            )
            self.padding_spin.valueChanged.connect(lambda _: self._update_output_placeholder())

            self.start_frame_spin = QSpinBox()
            self.start_frame_spin.setRange(0, 999999)
            self.start_frame_spin.setValue(
                int(settings.value(f"{mode}/start_frame", DEFAULT_START_FRAME))
            )
            self.start_frame_spin.setToolTip("First frame number in the output sequence")
            self.start_frame_spin.valueChanged.connect(
                lambda v: self._settings.setValue(f"{self._mode}/start_frame", v)
            )

            frame_row = QHBoxLayout()
            frame_row.setSpacing(8)
            frame_row.addWidget(QLabel("Padding"))
            frame_row.addWidget(self.padding_spin)
            frame_row.addSpacing(12)
            frame_row.addWidget(QLabel("Start frame"))
            frame_row.addWidget(self.start_frame_spin)
            frame_row.addStretch()
            opts_layout.addRow("Frame numbering", frame_row)

            self.fps_widget = None
            self.codec_combo = None
        elif mode == "exr2video":
            self.compression_combo = None
            self.padding_spin = None
            self.start_frame_spin = None
            self.fps_widget = FpsCombo(settings, f"{mode}/fps")
            opts_layout.addRow("Frame rate", self.fps_widget)

            self.codec_combo = QComboBox()
            for spec in available_video_codecs():
                tip = spec.format_label
                if spec.platforms:
                    tip += f" · {', '.join(spec.platforms)} only"
                self.codec_combo.addItem(spec.display_name, spec.key)
                idx = self.codec_combo.count() - 1
                self.codec_combo.setItemData(idx, tip, Qt.ItemDataRole.ToolTipRole)
            saved_codec = settings.value(f"{mode}/video_codec", DEFAULT_VIDEO_CODEC)
            for i in range(self.codec_combo.count()):
                if self.codec_combo.itemData(i) == saved_codec:
                    self.codec_combo.setCurrentIndex(i)
                    break
            self.codec_combo.currentIndexChanged.connect(
                lambda _: self._settings.setValue(
                    f"{self._mode}/video_codec",
                    self.codec_combo.currentData(),
                )
            )
            codec_row = QHBoxLayout()
            codec_row.setSpacing(4)
            codec_row.addWidget(self.codec_combo, 1)
            self._codec_settings_btn = QToolButton()
            self._codec_settings_btn.setText("\u2699")
            self._codec_settings_btn.setAutoRaise(True)
            self._codec_settings_btn.setFixedWidth(28)
            self._codec_settings_btn.setToolTip("Codec settings\u2026")
            self._codec_settings_btn.clicked.connect(self._open_codec_settings)
            self._update_codec_btn_state()
            self.codec_combo.currentIndexChanged.connect(lambda _: self._update_codec_btn_state())
            self.codec_combo.currentIndexChanged.connect(lambda _: self._update_output_ext())
            self.codec_combo.currentIndexChanged.connect(lambda _: self._update_dst_for_codec())
            codec_row.addWidget(self._codec_settings_btn)
            opts_layout.addRow("Codec", codec_row)
        else:
            self.compression_combo = None
            self.padding_spin = None
            self.start_frame_spin = None
            self.fps_widget = None
            self.codec_combo = None

        layout.addWidget(opts_group)

        # Two-way binding between the master checkboxes and the model.
        # The lambdas push view → model; ``_on_model_changed`` reflects
        # model → view so external writers (the slate dialog, settings
        # restore, scripts) stay in sync.
        if self._slate_check is not None:
            self._slate_check.toggled.connect(self._slate_model.set_slate_enabled)
        if self._burnin_check is not None:
            self._burnin_check.toggled.connect(self._slate_model.set_burnin_enabled)
        if self._watermark_check is not None:
            self._watermark_check.toggled.connect(self._slate_model.set_watermark_enabled)
        if self._slate_model is not None:
            self._slate_model.changed.connect(self._on_slate_model_changed)

        layout.addStretch()

        # -- Tab order --
        tab_chain = [
            self.input_path,
            self._browse_in,
            self.src_btn,
            self._frame_range_edit,
            self._reset_range_btn,
            self.output_path,
            self._browse_out,
            self.dst_btn,
            self.scale_combo,
        ]
        if mode == "video2exr":
            tab_chain += [
                self.compression_combo,
                self._comp_settings_btn,
                self.padding_spin,
                self.start_frame_spin,
            ]
        elif mode == "exr2video":
            if self.fps_widget:
                tab_chain.append(self.fps_widget)
            tab_chain += [self.codec_combo, self._codec_settings_btn]
        for i in range(len(tab_chain) - 1):
            if tab_chain[i] and tab_chain[i + 1]:
                self.setTabOrder(tab_chain[i], tab_chain[i + 1])

        # -- Connections --
        self._browse_in.clicked.connect(self._pick_input)
        self._browse_out.clicked.connect(self._pick_output)
        # Input settings are persisted by set_input(); no textChanged hook needed.
        self.output_path.textChanged.connect(
            lambda t: self._settings.setValue(f"{self._mode}/output", t)
        )
        self.src_btn.space_changed.connect(
            lambda n: self._settings.setValue(f"{self._mode}/src_space", n)
        )
        self.dst_btn.space_changed.connect(
            lambda n: self._settings.setValue(f"{self._mode}/dst_space", n)
        )

        self.input_path.textChanged.connect(self._on_input_text_changed)
        self.output_path.textChanged.connect(lambda _: self._emit_readiness())
        self.src_btn.space_changed.connect(lambda _: self._emit_readiness())
        self.dst_btn.space_changed.connect(lambda _: self._emit_readiness())

        # Validate any saved input path once the event loop starts — unless a
        # GUI launch path (--open / Nuke) already applied input on this tab.
        # The QLineEdit is only a view; readiness gates on _input_seq / _video_info
        # after a successful probe, so we must re-accept on every cold start.
        self._skip_saved_input_restore = False
        saved = self.input_path.text().strip()
        if saved:
            QTimer.singleShot(0, self, self._restore_saved_input_if_needed)

    def _restore_saved_input_if_needed(self) -> None:
        if getattr(self, "_skip_saved_input_restore", False):
            return
        saved = str(self.input_path.text() or "").strip()
        if not saved:
            return
        # Already accepted (e.g. browse completed before the deferred slot).
        if self._path_matches_current_input(saved) and self.is_ready():
            return
        self.log_message.emit("Restoring saved input…")
        self.set_input_async(saved)

    def suppress_saved_input_restore(self) -> None:
        """Cancel deferred QSettings input restore (used by ``--open`` / Nuke)."""
        self._skip_saved_input_restore = True

    def _path_matches_current_input(self, text: str) -> bool:
        """True if *text* is the same source as the validated model (path or #### view)."""
        text = (text or "").strip()
        if not text:
            return False
        if self._video_info is not None:
            return text == self._video_info.path
        if self._input_seq is not None:
            dirn = self._input_seq.dirname().rstrip("/\\")
            if text.rstrip("/\\") == dirn:
                return True
            pad = "#" * max(1, self._input_seq.zfill())
            display = (
                f"{self._input_seq.dirname()}{self._input_seq.basename()}"
                f"{pad}{self._input_seq.extension()}"
            )
            return text == display
        return False

    def _persist_input_path(self, path: str) -> None:
        """Write input path to QSettings and flush (survives kill / crash better)."""
        self._settings.setValue(f"{self._mode}/input", path)
        self._settings.sync()

    def _on_input_text_changed(self, text: str) -> None:
        """React to manual edits (typing / paste) in the input field.

        Does **not** clear a validated model when the field still shows the
        same source (real path *or* the Nuke-style ``####`` display string
        written by :meth:`set_input`).  Clearing first used to drop a loaded
        sequence the moment anything re-emitted ``textChanged`` with the
        display pattern, which is not a filesystem path.
        """
        text = text.strip()
        if not text:
            self._video_info = None
            self._input_seq = None
            self._full_input_range = ""
            self._frame_range_edit.clear()
            self._reset_range_btn.setEnabled(False)
            self._persist_input_path("")
            self._emit_readiness()
            return

        if self._path_matches_current_input(text):
            self._emit_readiness()
            return

        # Text no longer matches the model — drop model until re-validated.
        self._video_info = None
        self._input_seq = None

        p = Path(text)
        if self._mode == "video2exr":
            if p.is_file() and p.suffix.lower() in _VIDEO_EXTS:
                self.set_input(text)
                return
        else:
            if p.is_dir() or (p.is_file() and p.suffix.lower() == ".exr"):
                self.set_input(text)
                return

        self._full_input_range = ""
        self._frame_range_edit.clear()
        self._reset_range_btn.setEnabled(False)
        self._emit_readiness()

    def _emit_readiness(self) -> None:
        self.readiness_changed.emit(self.is_ready())

    def is_ready(self) -> bool:
        """True when all required fields are populated with validated inputs."""
        if self._mode == "video2exr" and self._video_info is None:
            return False
        if self._mode == "exr2video" and self._input_seq is None:
            return False
        if not self.output_path.text().strip():
            return False
        if not self.src_btn.is_valid():
            return False
        if not self.dst_btn.is_valid():
            return False
        return True

    def populate_spaces(
        self,
        families: dict[str, list[str]],
        ocio_cfg: object | None = None,
    ) -> None:
        """Rebuild color-space menus for a (possibly new) OCIO config.

        Keeps the current selection when possible, remaps via
        :func:`find_equivalent_space`, or marks the button invalid so Convert
        stays disabled until the user picks a space that exists in *ocio_cfg*.
        """
        self._ocio_cfg = ocio_cfg
        if self._mode == "video2exr":
            default_src, default_dst = DEFAULT_SRC_V2E, DEFAULT_DST_V2E
        else:
            default_src, default_dst = DEFAULT_SRC_E2V, DEFAULT_DST_E2V

        # Prefer the live selection (including an invalid “wanted” name) so a
        # config switch remaps what the user was using; fall back to settings.
        wanted_src = (
            self.src_btn.displayed_space()
            or self._settings.value(f"{self._mode}/src_space", default_src)
            or default_src
        )
        wanted_dst = (
            self.dst_btn.displayed_space()
            or self._settings.value(f"{self._mode}/dst_space", default_dst)
            or default_dst
        )
        # Strip invalid-state warning prefix if re-populating after a failed map.
        if isinstance(wanted_src, str) and wanted_src.startswith("⚠ "):
            wanted_src = wanted_src[2:].strip()
        if isinstance(wanted_dst, str) and wanted_dst.startswith("⚠ "):
            wanted_dst = wanted_dst[2:].strip()

        resolved_src = ""
        resolved_dst = ""
        if ocio_cfg is not None:
            resolved_src = find_equivalent_space(ocio_cfg, str(wanted_src))
            resolved_dst = find_equivalent_space(ocio_cfg, str(wanted_dst))
        else:
            # No config — only exact names in the (empty) families will work.
            resolved_src = str(wanted_src) if wanted_src else ""
            resolved_dst = str(wanted_dst) if wanted_dst else ""

        if resolved_src:
            self.src_btn.populate(families, resolved_src)
            if resolved_src != wanted_src:
                self.log_message.emit(f"Source color space remapped: {wanted_src} → {resolved_src}")
        else:
            self.src_btn.populate(families, "")
            self.src_btn.set_invalid(str(wanted_src) if wanted_src else "")
            if wanted_src:
                self.log_message.emit(
                    f"Source color space “{wanted_src}” not in new OCIO config — pick one"
                )

        if resolved_dst:
            self.dst_btn.populate(families, resolved_dst)
            if resolved_dst != wanted_dst:
                self.log_message.emit(
                    f"Destination color space remapped: {wanted_dst} → {resolved_dst}"
                )
        else:
            self.dst_btn.populate(families, "")
            self.dst_btn.set_invalid(str(wanted_dst) if wanted_dst else "")
            if wanted_dst:
                self.log_message.emit(
                    f"Destination color space “{wanted_dst}” not in new OCIO config — pick one"
                )

        # Persist only valid selections so we don't lock in a dead name.
        if self.src_btn.is_valid():
            self._settings.setValue(f"{self._mode}/src_space", self.src_btn.current_space())
        if self.dst_btn.is_valid():
            self._settings.setValue(f"{self._mode}/dst_space", self.dst_btn.current_space())
        self._emit_readiness()

    def get_fps(self) -> float:
        if self.fps_widget:
            return self.fps_widget.fps()
        return 24.0

    def get_compression(self) -> str:
        if self.compression_combo:
            return self.compression_combo.currentData() or DEFAULT_EXR_COMPRESSION
        return DEFAULT_EXR_COMPRESSION

    def get_scale(self) -> float:
        return float(self.scale_combo.currentData() or DEFAULT_SCALE)

    def get_padding(self) -> int:
        if self.padding_spin:
            return self.padding_spin.value()
        return DEFAULT_FRAME_PADDING

    def get_start_frame(self) -> int:
        if self.start_frame_spin:
            return self.start_frame_spin.value()
        return DEFAULT_START_FRAME

    def get_video_codec_info(self) -> tuple[str, str, str]:
        """Return (key, libav_codec, pix_fmt) for the selected video codec."""
        if not self.codec_combo:
            return ("h264", "libx264", "yuv420p")
        key = self.codec_combo.currentData() or DEFAULT_VIDEO_CODEC
        spec = video_codec_by_key(str(key))
        if spec is not None and spec.is_available():
            return (spec.key, spec.libav_codec, spec.pix_fmt)
        # Fall back to first available codec on this platform.
        avail = available_video_codecs()
        if avail:
            s = avail[0]
            return (s.key, s.libav_codec, s.pix_fmt)
        return ("h264", "libx264", "yuv420p")

    def get_exr_opts(self) -> dict[str, str]:
        """Return saved EXR compression options."""
        comp = self.get_compression()
        result: dict[str, str] = {}
        if comp in ("dwaa", "dwab"):
            level = float(self._settings.value("exr_opts/dwa_level", 45.0))
            result["dwa_compression_level"] = str(level)
        elif comp in ("zip", "zips"):
            level = int(self._settings.value("exr_opts/zip_level", 4))
            result["zip_level"] = str(level)
        return result

    def get_codec_opts(self) -> dict[str, str]:
        """Return saved video codec options for PyAV stream.options."""
        from ..core.convert import _default_codec_opts

        key = self.get_video_codec_info()[0]
        opts = dict(_default_codec_opts(key))
        if key == "h264":
            crf = str(int(self._settings.value("codec_opts/h264_crf", 18)))
            preset = self._settings.value("codec_opts/h264_preset", "medium")
            opts.update({"crf": crf, "preset": str(preset)})
        elif key in ("hevc", "hevc_8", "hevc_12"):
            crf = str(int(self._settings.value("codec_opts/hevc_crf", 18)))
            preset = self._settings.value("codec_opts/hevc_preset", "medium")
            opts.update({"crf": crf, "preset": str(preset)})
        elif key in ("cineform", "cineform_rgb"):
            from ..core.constants import DEFAULT_CINEFORM_QUALITY

            q = self._settings.value("codec_opts/cineform_quality", DEFAULT_CINEFORM_QUALITY)
            opts["quality"] = str(q)
        return opts

    def slate_model(self):
        """Return the per-tab :class:`SlateModel`, or ``None`` for non-slate modes."""
        return self._slate_model

    def slate_enabled(self) -> bool:
        return self._slate_model is not None and self._slate_model.slate_enabled

    def get_slate_data(self) -> dict | None:
        """Return the rendered-shape slate data, or None if slate is disabled."""
        if not self.slate_enabled():
            return None
        return self._slate_model.slate_data_for_render()

    def get_slate_thumbnail_b64(self) -> str:
        """Return the base64-encoded thumbnail for the slate, or ''."""
        if not self.slate_enabled() or self._slate_model is None:
            return ""
        return self._slate_model.thumbnail_b64

    def get_slate_resolution(self) -> tuple[int, int] | None:
        """Return the slate resolution if slate is enabled."""
        if not self.slate_enabled() or self._slate_model is None:
            return None
        return self._slate_model.slate_resolution

    def burnin_enabled(self) -> bool:
        return self._slate_model is not None and self._slate_model.burnin_enabled

    def get_burnin_fields(self) -> dict[str, str] | None:
        """Return the user-typed burn-in fields, or None if burn-in is disabled."""
        if not self.burnin_enabled() or self._slate_model is None:
            return None
        return self._slate_model.burnin_fields

    def get_effective_burnin_fields(self, input_path: str = "") -> dict[str, str] | None:
        """Return burn-in fields for rendering (manual cells, else slate-derived)."""
        if not self.burnin_enabled() or self._slate_model is None:
            return None
        return self._slate_model.effective_burnin_fields(input_path)

    def watermark_enabled(self) -> bool:
        return self._slate_model is not None and self._slate_model.watermark_enabled

    def get_watermark_params(self) -> dict | None:
        """Return watermark styling, or ``None`` if the tab master switch is off.

        The tab **Watermark** checkbox is the master switch for export.  The
        editor's watermark group (``enabled`` inside the dict) is a secondary
        toggle: both must be on for :meth:`SlateModel.watermark_active` and
        for overlays to bake into the output.
        """
        if not self.watermark_enabled() or self._slate_model is None:
            return None
        return self._slate_model.watermark_params

    def _on_slate_model_changed(self, section: str) -> None:
        """Reflect model changes in the master checkboxes (model → view)."""
        if section == "slate_enabled" and self._slate_check is not None:
            self._slate_check.blockSignals(True)
            self._slate_check.setChecked(self._slate_model.slate_enabled)
            self._slate_check.blockSignals(False)
        elif section == "burnin_enabled" and self._burnin_check is not None:
            self._burnin_check.blockSignals(True)
            self._burnin_check.setChecked(self._slate_model.burnin_enabled)
            self._burnin_check.blockSignals(False)
        elif section == "watermark_enabled" and self._watermark_check is not None:
            self._watermark_check.blockSignals(True)
            self._watermark_check.setChecked(self._slate_model.watermark_enabled)
            self._watermark_check.blockSignals(False)

    def _open_slate_dialog(self) -> None:
        from .slate_widgets import SlateDialog

        if self._slate_model is None:
            return

        # Timeline + shot scrub require a validated EXR sequence. If the field
        # still shows a path but the model was never accepted (or was cleared),
        # re-probe before opening so the dialog is not missing the transport.
        inp = self.get_input_path()
        if not inp and self._mode == "exr2video":
            raw = self.input_path.text().strip()
            if raw and self.set_input(raw):
                inp = self.get_input_path()
            elif raw:
                inp = raw

        locked_w, locked_h = self._detect_input_resolution()
        inferred_fps = self._infer_fps_from_input()
        dst_cs = self.dst_btn.current_space()
        src_cs = self.src_btn.current_space()

        dlg = SlateDialog(
            self._slate_model,
            locked_width=locked_w,
            locked_height=locked_h,
            input_path=inp,
            mode=self._mode,
            inferred_fps=inferred_fps,
            frame_range=self._full_input_range,
            dst_colorspace=dst_cs,
            ocio_cfg=self._ocio_cfg,
            src_colorspace=src_cs,
            parent=self,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.log_message.emit("Slate & overlay data updated")

    def _infer_fps_from_input(self) -> float:
        """Return frame rate from the validated input. 0.0 if unavailable."""
        if self._video_info is not None:
            return self._video_info.fps
        return 0.0

    def _detect_input_resolution(self) -> tuple[int, int]:
        """Return resolution from the validated input, or (0, 0)."""
        if self._video_info is not None:
            return self._video_info.width, self._video_info.height
        if self._input_seq is not None:
            try:
                import OpenImageIO as oiio

                first_frame = sorted(self._input_seq.frameSet())[0]
                first_path = self._input_seq.frame(first_frame)
                inp_img = oiio.ImageInput.open(first_path)
                if inp_img:
                    spec = inp_img.spec()
                    w = spec.full_width if spec.full_width > 0 else spec.width
                    h = spec.full_height if spec.full_height > 0 else spec.height
                    inp_img.close()
                    return w, h
            except Exception:
                pass
        return 0, 0

    def _update_comp_btn_state(self) -> None:
        comp = self.get_compression()
        self._comp_settings_btn.setVisible(comp in _EXR_HAS_SETTINGS)

    def _update_codec_btn_state(self) -> None:
        key = self.get_video_codec_info()[0]
        self._codec_settings_btn.setVisible(key in _CODEC_HAS_SETTINGS)

    def _open_compression_settings(self) -> None:
        comp = self.get_compression()
        dlg = ExrCompressionSettingsDialog(comp, self._settings, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            opts = dlg.get_settings()
            if opts:
                parts = [f"{k}={v}" for k, v in opts.items()]
                self.log_message.emit(f"EXR compression ({comp.upper()}): {', '.join(parts)}")
            else:
                self.log_message.emit(f"EXR compression ({comp.upper()}): default settings")

    def _open_codec_settings(self) -> None:
        key = self.get_video_codec_info()[0]
        dlg = VideoCodecSettingsDialog(key, self._settings, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            opts = dlg.get_settings()
            if opts:
                parts = [f"{k}={v}" for k, v in opts.items()]
                self.log_message.emit(f"Codec ({key}): {', '.join(parts)}")
            else:
                self.log_message.emit(f"Codec ({key}): default settings")

    def _pick_input(self) -> None:
        if self._mode == "video2exr":
            start = self.input_path.text().strip() or str(Path.home())
            dlg = VideoBrowserDialog(start, parent=self)
            if dlg.exec() == QDialog.DialogCode.Accepted and dlg.selected_path():
                self.set_input(dlg.selected_path())
        else:
            start = self.get_input_path() or str(Path.home())
            sel_name = ""
            if self._input_seq is not None:
                sel_name = self._input_seq.basename().rstrip("._")
            dlg = SequenceBrowserDialog(start, select_name=sel_name, parent=self)
            if dlg.exec() == QDialog.DialogCode.Accepted:
                # Prefer first-frame path so multi-sequence folders open the
                # row the user selected (directory alone always picks sorted[0]).
                path = dlg.selected_path() or dlg.selected_directory()
                if path:
                    self.set_input(path)

    def _pick_output(self) -> None:
        if self._mode == "video2exr":
            path = QFileDialog.getExistingDirectory(
                self,
                "Output directory",
                self.output_path.text(),
            )
        else:
            codec_key = ""
            if self.codec_combo:
                codec_key = self.codec_combo.currentData() or ""
            if codec_key in ("prores", "prores_4444", "prores_xq") or str(codec_key).startswith(
                "prores_"
            ):
                filt = "Video (*.mov)"
            elif codec_key in ("ffv1", "ffv1_12"):
                filt = "Video (*.mkv *.avi)"
            elif codec_key in ("h264", "hevc", "hevc_8", "hevc_12"):
                filt = "Video (*.mp4 *.mov *.mkv)"
            else:
                filt = "Video (*.mp4 *.mov *.mkv)"
            path, _ = QFileDialog.getSaveFileName(
                self,
                "Save video as",
                self.output_path.text(),
                filt,
            )
        if path:
            self.output_path.setText(path)

    def handle_dropped_path(self, path: str) -> bool:
        """Accept a dropped path if valid for this tab's mode. Returns True if accepted."""
        p = Path(path)
        if self._mode == "video2exr":
            if p.is_file() and p.suffix.lower() in _VIDEO_EXTS:
                return self.set_input(str(p))
        else:
            if p.is_dir() or (p.is_file() and p.suffix.lower() == ".exr"):
                return self.set_input(str(p))
        return False

    def _codec_ext(self) -> str:
        """Return the preferred file extension for the current codec."""
        codec_key = ""
        if self.codec_combo:
            codec_key = self.codec_combo.currentData() or ""
        if codec_key in ("ffv1", "ffv1_12"):
            return ".mkv"
        if codec_key in ("h264", "hevc", "hevc_8", "hevc_12"):
            return ".mp4"
        if str(codec_key).startswith("dnxhr"):
            return ".mxf"
        # ProRes (software + VideoToolbox), CineForm, etc.
        return ".mov"

    def _auto_fill_video_output(self, exr_dir: str) -> None:
        """Set output video path to <parent>/<dirname>.<ext> if not already set."""
        if self._mode != "exr2video":
            return
        if self.output_path.text().strip():
            return
        p = Path(exr_dir)
        out = p.parent / f"{p.name}{self._codec_ext()}"
        self.output_path.setText(str(out))

    def _auto_fill_exr_output(self, video_path: str) -> None:
        """Set output EXR path to <video_parent>/<stem>/<stem>.####.exr."""
        if self._mode != "video2exr":
            return
        p = Path(video_path)
        out_dir = p.parent / p.stem
        pad = "#" * self.get_padding()
        display = str(out_dir / f"{p.stem}.{pad}.exr")
        self.output_path.setText(display)

    def _auto_detect_video_colorspace(self, video_path: str) -> None:
        """Guess the source colorspace from video codec/format and select it."""
        if self._mode != "video2exr":
            return
        from ..core.video import guess_video_colorspace_candidates

        candidates = guess_video_colorspace_candidates(video_path)
        if not candidates:
            return
        ocio_cfg = getattr(self, "_ocio_cfg", None)
        preferred = candidates[0]
        if ocio_cfg is not None:
            from ..core.ocio_utils import resolve_alias

            for name in candidates:
                resolved = resolve_alias(ocio_cfg, name)
                if resolved:
                    preferred = resolved
                    break
        if self.src_btn.try_select(preferred):
            self.log_message.emit(f"Auto-detected source color space: {preferred}")
            self.src_btn.setStyleSheet("background-color: #3a3020;")
            QTimer.singleShot(500, self, lambda: self.src_btn.setStyleSheet(""))

    def _update_output_placeholder(self) -> None:
        """Update the output placeholder and current pattern to reflect padding."""
        if self._mode != "video2exr" or not self.padding_spin:
            return
        pat = "#" * self.padding_spin.value()
        self.output_path.setPlaceholderText(f"Output EXR sequence (name.{pat}.exr)")
        import re

        current = self.output_path.text()
        if current and re.search(r"#+\.exr$", current):
            updated = re.sub(r"#+\.exr$", f"{pat}.exr", current)
            if updated != current:
                self.output_path.setText(updated)
                self.output_path.setStyleSheet("background-color: #3a3020;")
                QTimer.singleShot(500, self, lambda: self.output_path.setStyleSheet(""))

    def _update_output_ext(self) -> None:
        """Update the output path extension to match the current codec."""
        if self._mode != "exr2video":
            return
        current = self.output_path.text().strip()
        if not current:
            return
        p = Path(current)
        new_ext = self._codec_ext()
        if p.suffix.lower() != new_ext:
            self.output_path.setText(str(p.with_suffix(new_ext)))
            self.output_path.setStyleSheet("background-color: #3a3020;")
            QTimer.singleShot(500, self, lambda: self.output_path.setStyleSheet(""))

    def _update_dst_for_codec(self) -> None:
        """Suggest a sensible destination colorspace for the selected codec."""
        if self._mode != "exr2video":
            return
        codec_key = self.codec_combo.currentData() if self.codec_combo else ""
        if codec_key in ("ffv1", "ffv1_12"):
            candidates = ["scene_linear"]
        else:
            candidates = [
                "Output - Rec.709",
                "Rec.1886 Rec.709 - Display",
            ]
        ocio_cfg = getattr(self, "_ocio_cfg", None)
        preferred = candidates[0]
        if ocio_cfg is not None:
            from ..core.ocio_utils import resolve_alias

            for name in candidates:
                resolved = resolve_alias(ocio_cfg, name)
                if resolved:
                    preferred = resolved
                    break
        if self.dst_btn.try_select(preferred):
            self.dst_btn.setStyleSheet("background-color: #3a3020;")
            QTimer.singleShot(500, self, lambda: self.dst_btn.setStyleSheet(""))

    def _auto_detect_colorspace(self, exr_dir: str) -> None:
        """Probe colorspace from EXRs and select it in src_btn if found."""
        if self._mode != "exr2video":
            return
        cs = probe_exr_colorspace(exr_dir)
        if not cs:
            return
        canonical = cs
        ocio_cfg = getattr(self, "_ocio_cfg", None)
        if ocio_cfg is not None:
            from ..core.ocio_utils import resolve_alias

            resolved = resolve_alias(ocio_cfg, cs)
            if resolved:
                canonical = resolved
        if self.src_btn.try_select(canonical):
            self.log_message.emit(f"Auto-detected source color space: {canonical}")
            self.src_btn.setStyleSheet("background-color: #3a3020;")
            QTimer.singleShot(500, self, lambda: self.src_btn.setStyleSheet(""))
        else:
            self.log_message.emit(f'EXR color space "{cs}" not found in current OCIO config')

    # -- Input model --

    def set_input_async(self, path: str) -> None:
        """Probe *path* off the GUI thread, then apply results on the main thread.

        Heavy PyAV / OIIO probes on large MXFs must not freeze the UI.
        """
        path = path.strip()
        if not path:
            self.set_input(path)
            return

        # Bump generation so late results from a cancelled probe are ignored.
        self._probe_gen = getattr(self, "_probe_gen", 0) + 1
        gen = self._probe_gen

        # Replace any in-flight probe so a newer path wins.
        old_thr = getattr(self, "_probe_thread", None)
        old_worker = getattr(self, "_probe_worker", None)
        if old_worker is not None:
            try:
                old_worker.finished.disconnect()
                old_worker.failed.disconnect()
            except (RuntimeError, TypeError):
                pass
        if old_thr is not None and old_thr.isRunning():
            old_thr.quit()
            old_thr.wait(500)

        thr = QThread(self)
        worker = _InputProbeWorker(self._mode, path)
        worker.moveToThread(thr)
        thr.started.connect(worker.run)

        def _ok(result: object) -> None:
            if gen != getattr(self, "_probe_gen", 0):
                return
            self._apply_probe_result(result)

        def _fail(err: str) -> None:
            if gen != getattr(self, "_probe_gen", 0):
                return
            self.log_message.emit(f"Could not open input: {err}")
            # Sync fallback: directory/file probes are usually fast; try once
            # on the GUI thread so a restored path is not left "shown but
            # unaccepted" after a transient async failure.
            try:
                if self.set_input(path):
                    self.log_message.emit(f"Opened (retry): {path}")
            except Exception:
                pass

        def _cleanup() -> None:
            if getattr(self, "_probe_thread", None) is thr:
                self._probe_thread = None
                self._probe_worker = None

        worker.finished.connect(_ok, Qt.ConnectionType.QueuedConnection)
        worker.failed.connect(_fail, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(thr.quit)
        worker.failed.connect(thr.quit)
        thr.finished.connect(worker.deleteLater)
        thr.finished.connect(thr.deleteLater)
        thr.finished.connect(_cleanup)
        self._probe_thread = thr
        self._probe_worker = worker
        thr.start()

    def _apply_probe_result(self, result: object) -> None:
        """Apply a successful async probe result on the GUI thread."""
        if not isinstance(result, dict):
            return
        kind = result.get("kind")
        path = str(result.get("path", ""))

        # Probe mode must match this tab (guards against cross-tab races).
        if kind == "video" and self._mode != "video2exr":
            return
        if kind == "exr" and self._mode != "exr2video":
            return

        if kind == "video":
            self._video_info = VideoInput(
                path,
                int(result["w"]),
                int(result["h"]),
                float(result["fps"]),
                int(result["total"]),
            )
            self._input_seq = None
            frames = list(range(1, int(result["total"]) + 1))
            display = path
            actual = path
        elif kind == "exr":
            seq = result.get("seq")
            if seq is None:
                self.log_message.emit(f"Could not resolve EXR sequence: {path}")
                return
            self._input_seq = seq
            self._video_info = None
            frames = list(result.get("frame_nums") or [])
            pad = "#" * seq.zfill()
            display = f"{seq.dirname()}{seq.basename()}{pad}{seq.extension()}"
            actual = seq.dirname().rstrip("/") or path
        else:
            return

        from ..core.framerange import format_frame_range

        self.input_path.blockSignals(True)
        self.input_path.setText(display)
        self.input_path.blockSignals(False)

        if frames:
            range_str = format_frame_range(frames)
            self._full_input_range = range_str
            self._frame_range_edit.setText(range_str)
            self._reset_range_btn.setEnabled(True)
        else:
            self._full_input_range = ""
            self._frame_range_edit.clear()
            self._reset_range_btn.setEnabled(False)

        if self._mode == "video2exr":
            self._auto_fill_exr_output(path)
            self._auto_detect_video_colorspace(path)
        elif self._input_seq is not None:
            exr_dir = self._input_seq.dirname().rstrip("/")
            self._auto_fill_video_output(exr_dir)
            self._auto_detect_colorspace(exr_dir)

        self._persist_input_path(actual)
        self.log_message.emit(f"Input ready: {display}")
        self._emit_readiness()

    def set_input(self, path: str) -> bool:
        """Validate *path* and adopt it as the current input.

        For ``video2exr`` the file is probed with PyAV and a
        :class:`VideoInput` is stored.  For ``exr2video`` the path is
        resolved to a :class:`~fileseq.FileSequence` on disk.

        The ``QLineEdit`` is updated to reflect the validated source and
        the frame-range, output path, and colorspace fields are
        auto-populated.  Returns ``True`` on success.
        """
        from ..core.framerange import format_frame_range

        path = path.strip()
        if not path:
            self._video_info = None
            self._input_seq = None
            self._full_input_range = ""
            self._frame_range_edit.clear()
            self._reset_range_btn.setEnabled(False)
            self._settings.setValue(f"{self._mode}/input", "")
            self._emit_readiness()
            return False

        try:
            if self._mode == "video2exr":
                from ..core.video import probe_video

                w, h, fps, total = probe_video(path)
                self._video_info = VideoInput(path, w, h, fps, total)
                self._input_seq = None
                frames = list(range(1, total + 1))
                display = path
            else:
                from ..core.sequence import find_exr_sequence_info

                _paths, _name, frame_nums, _pad, seq = find_exr_sequence_info(path)
                self._input_seq = seq
                self._video_info = None
                frames = frame_nums
                pad = "#" * seq.zfill()
                display = f"{seq.dirname()}{seq.basename()}{pad}{seq.extension()}"
        except Exception:
            self._video_info = None
            self._input_seq = None
            self._full_input_range = ""
            self._frame_range_edit.clear()
            self._reset_range_btn.setEnabled(False)
            self._emit_readiness()
            return False

        # Update the view
        self.input_path.blockSignals(True)
        self.input_path.setText(display)
        self.input_path.blockSignals(False)

        # Frame range
        if frames:
            range_str = format_frame_range(frames)
            self._full_input_range = range_str
            self._frame_range_edit.setText(range_str)
            self._reset_range_btn.setEnabled(True)
        else:
            self._full_input_range = ""
            self._frame_range_edit.clear()
            self._reset_range_btn.setEnabled(False)

        # Auto-fill dependent fields
        if self._mode == "video2exr":
            self._auto_fill_exr_output(path)
            self._auto_detect_video_colorspace(path)
        else:
            exr_dir = self._input_seq.dirname().rstrip("/")
            self._auto_fill_video_output(exr_dir)
            self._auto_detect_colorspace(exr_dir)

        # Persist the real filesystem path (not the display pattern) and flush
        # so a hard kill does not leave an empty restore on next launch.
        actual = path if self._video_info is not None else self._input_seq.dirname().rstrip("/")
        self._persist_input_path(actual)

        self._emit_readiness()
        return True

    def _reset_to_source_range(self) -> None:
        """Reset the frame range field to the full source range."""
        if self._full_input_range:
            self._frame_range_edit.setText(self._full_input_range)

    def get_input_path(self) -> str:
        """Return the validated filesystem path from the model, or ``""``."""
        if self._video_info is not None:
            return self._video_info.path
        if self._input_seq is not None:
            return self._input_seq.dirname().rstrip("/")
        return ""

    def get_output_path(self) -> str:
        """Return the real filesystem path for the output.

        For video2exr, the display shows a sequence pattern (e.g. name.####.exr)
        but the converter needs just the directory.
        """
        raw = self.output_path.text().strip()
        if self._mode == "video2exr" and raw:
            p = Path(raw)
            if "#" in p.name:
                return str(p.parent)
        return raw

    def get_frame_range(self) -> str:
        """Return the user-specified frame range string, or '' for all frames."""
        return self._frame_range_edit.text().strip()
