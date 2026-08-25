from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QPoint, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QStandardItemModel
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core.constants import (
    BUNDLED_ACES_STUDIO_KEY,
    COMMON_FPS,
    OCIO_SOURCE_BUNDLED,
    OCIO_SOURCE_ENV,
    OCIO_SOURCE_FILE,
)
from ..core.nuke_discover import (
    is_nuke_source_key,
    nuke_source_label,
    resolve_nuke_config_path,
)
from ..core.ocio_utils import (
    list_app_configs,
    list_builtin_configs,
    list_nuke_configs,
    resolve_ocio_config,
)
from .style import STATUS_DIM, STATUS_ERR, STATUS_OK

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

    Auto-detect (media probe) uses :meth:`try_select` with ``auto=True`` for a
    brief amber flash; manual picks, presets, and ``populate`` never leave that
    flash stuck on.
    """

    space_changed = Signal(str)

    # Visual cue when the displayed name is not in the current config.
    _INVALID_STYLE = (
        "QToolButton { color: #e07070; border: 1px solid #a04040; background-color: #3a2020; }"
    )
    # Brief cue when the space was chosen by media auto-detect (not sticky).
    _AUTO_STYLE = (
        "QToolButton { color: #e8dcc0; border: 1px solid #8a6a30; "
        "background-color: #3a3020; border-radius: 3px; padding: 4px 8px; "
        "text-align: left; font-size: 13px; }"
    )
    _AUTO_FLASH_MS = 1200

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        # DelayedPopup + no setMenu — we own popup positioning in mouse/key events.
        self.setPopupMode(QToolButton.ToolButtonPopupMode.DelayedPopup)
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._current = ""
        self._valid = False
        self._auto_flash = False
        self._menu = QMenu(self)
        self._auto_timer = QTimer(self)
        self._auto_timer.setSingleShot(True)
        self._auto_timer.timeout.connect(self._end_auto_flash)
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

    def is_auto_flash(self) -> bool:
        """True while the auto-detect highlight is showing."""
        return self._auto_flash

    def set_current_space(self, name: str) -> None:
        """Set the selection; marks invalid if *name* is empty."""
        self._stop_auto_flash()
        self._current = name or ""
        self._valid = bool(name)
        self._set_display(name or "(none)")
        self._apply_valid_style()

    def set_invalid(self, wanted: str) -> None:
        """Show *wanted* as missing from the active config (Convert disabled)."""
        self._stop_auto_flash()
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
        self._stop_auto_flash()
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
                # Menu pick is always manual — clears any auto-detect flash.
                action.triggered.connect(lambda checked, n=cs_name: self._pick(n, auto=False))
                if cs_name == select:
                    found = True

        if select and found:
            self._pick(select, auto=False)
        elif select and select in all_names:
            self._pick(select, auto=False)
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

    def _pick(self, name: str, *, auto: bool = False) -> None:
        self._current = name
        self._valid = True
        self._set_display(name)
        if auto:
            self._begin_auto_flash()
        else:
            self._stop_auto_flash()
            self._apply_valid_style()
            self.setToolTip(name)
        self.space_changed.emit(name)

    def try_select(self, name: str, *, auto: bool = False) -> bool:
        """Select *name* if it exists in the menu. Returns True on match.

        Pass ``auto=True`` when the value comes from media auto-detect so the
        button briefly highlights, then returns to the normal style.
        """
        if not name:
            return False
        if self._find_action(self._menu, name):
            self._pick(name, auto=auto)
            return True
        # Case-insensitive fallback: scan all actions for a match
        found = self._find_action_ci(self._menu, name.lower())
        if found:
            self._pick(found, auto=auto)
            return True
        return False

    def _begin_auto_flash(self) -> None:
        """Show the auto-detect highlight; timer clears it (never sticky)."""
        self._auto_flash = True
        self.setStyleSheet(self._AUTO_STYLE)
        self.setToolTip(f"{self._current} — auto-detected from media")
        self._auto_timer.start(self._AUTO_FLASH_MS)

    def _end_auto_flash(self) -> None:
        """Timer callback: drop auto-detect highlight, keep selection."""
        self._auto_flash = False
        self._apply_valid_style()
        if self._valid and self._current:
            self.setToolTip(self._current)

    def _stop_auto_flash(self) -> None:
        """Cancel any in-flight auto flash without waiting for the timer."""
        if self._auto_timer.isActive():
            self._auto_timer.stop()
        self._auto_flash = False

    def _apply_valid_style(self) -> None:
        if self._auto_flash:
            self.setStyleSheet(self._AUTO_STYLE)
        elif self._valid:
            # Empty stylesheet → inherit app QSS (normal input chrome).
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
