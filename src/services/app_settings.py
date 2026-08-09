"""Process-wide application settings (single QSettings backend).

Architecture (Qt / desktop app best practice)
---------------------------------------------
* **One** ``QSettings(APP_ORG, APP_NAME)`` per process, not ad-hoc factories.
* **Named keys** live here (or in small domain modules that re-export constants).
* **Typed getters** avoid ``bool("false") is True`` and string/int platform drift.
* **Domain layers** (callers still free to use ``.qsettings`` during migration):

  * **App preferences** — player, cache, post-convert, geometry (long-lived)
  * **Session** — last paths, last tab, last convert options (restored on launch)
  * **Document / presets** — named JSON via :mod:`presets` (not QSettings)
  * **Slate document** — :class:`~src.services.slate_model.SlateModel` (+ undo stack)

Presets and slate content should not grow more magic strings in widgets.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QByteArray, QObject, QSettings, Signal

from ..core.constants import APP_NAME, APP_ORG

# ---------------------------------------------------------------------------
# Key registry (single source of truth for string keys)
# ---------------------------------------------------------------------------


class Keys:
    """All QSettings keys used by the app. Prefer these over bare strings."""

    # UI / shell
    UI_GEOMETRY = "ui/geometry"
    UI_TAB = "ui/tab"
    UI_COPY_PATH_AFTER = "ui/copy_path_after"
    UI_OPEN_AFTER = "ui/open_after"
    UI_SHOW_FOLDER_AFTER = "ui/show_folder_after"
    UI_PLAYER_WINDOW_GEOMETRY = "ui/sequence_player_window_geometry"

    # Preferences (player / cache / slate thumb)
    PLAYER_MODE = "player/mode"
    PLAYER_PATH = "player/path"
    CACHE_BUDGET_PCT = "cache/budget_pct"
    SLATE_THUMBNAIL_FRAME = "slate/thumbnail_frame"

    # OCIO
    OCIO_SOURCE = "ocio/source"
    OCIO_FILE_PATH = "ocio/file_path"


# ---------------------------------------------------------------------------
# AppSettings
# ---------------------------------------------------------------------------


class AppSettings(QObject):
    """Typed façade over one :class:`QSettings` instance.

    Inject into windows/tabs; use :func:`get_app_settings` for the process default.
    Tests should call :func:`set_app_settings` with an IniFormat temp backend.
    """

    # Emitted when a preference that live consumers care about changes.
    preference_changed = Signal(str)  # key

    def __init__(
        self,
        backend: QSettings | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._s = backend if backend is not None else QSettings(APP_ORG, APP_NAME)

    # -- raw backend (escape hatch for geometry QByteArray, gradual migration) --

    @property
    def qsettings(self) -> QSettings:
        return self._s

    def sync(self) -> None:
        self._s.sync()

    # -- typed accessors ------------------------------------------------------

    def get_bool(self, key: str, default: bool = False) -> bool:
        try:
            raw = self._s.value(key, default, type=bool)
            return bool(raw)
        except (TypeError, ValueError):
            raw = self._s.value(key, default)
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, (int, float)):
            return bool(raw)
        if isinstance(raw, str):
            low = raw.strip().lower()
            if low in ("1", "true", "yes", "on"):
                return True
            if low in ("0", "false", "no", "off", ""):
                return False
        return bool(default)

    def set_bool(self, key: str, value: bool, *, notify: bool = False) -> None:
        self._s.setValue(key, bool(value))
        if notify:
            self.preference_changed.emit(key)

    def get_int(self, key: str, default: int = 0) -> int:
        raw = self._s.value(key, default)
        if isinstance(raw, bool):
            return default
        if isinstance(raw, int):
            return raw
        if isinstance(raw, float):
            return int(raw)
        if isinstance(raw, str):
            try:
                return int(raw.strip())
            except ValueError:
                return default
        return default

    def set_int(self, key: str, value: int, *, notify: bool = False) -> None:
        self._s.setValue(key, int(value))
        if notify:
            self.preference_changed.emit(key)

    def get_float(self, key: str, default: float = 0.0) -> float:
        raw = self._s.value(key, default)
        if isinstance(raw, bool):
            return default
        if isinstance(raw, (int, float)):
            return float(raw)
        if isinstance(raw, str):
            try:
                return float(raw.strip())
            except ValueError:
                return default
        return default

    def set_float(self, key: str, value: float, *, notify: bool = False) -> None:
        self._s.setValue(key, float(value))
        if notify:
            self.preference_changed.emit(key)

    def get_str(self, key: str, default: str = "") -> str:
        raw = self._s.value(key, default)
        if raw is None:
            return default
        return str(raw)

    def set_str(self, key: str, value: str, *, notify: bool = False) -> None:
        self._s.setValue(key, str(value))
        if notify:
            self.preference_changed.emit(key)

    def get_bytearray(self, key: str) -> QByteArray | None:
        raw = self._s.value(key)
        if raw is None:
            return None
        if isinstance(raw, QByteArray):
            return raw if not raw.isEmpty() else None
        if isinstance(raw, (bytes, bytearray)):
            ba = QByteArray(bytes(raw))
            return ba if not ba.isEmpty() else None
        return None

    def set_bytearray(self, key: str, value: QByteArray | bytes | bytearray) -> None:
        if isinstance(value, QByteArray):
            self._s.setValue(key, value)
        else:
            self._s.setValue(key, QByteArray(bytes(value)))

    def get_value(self, key: str, default: Any = None) -> Any:
        """Untyped read (prefer typed helpers)."""
        return self._s.value(key, default)

    def set_value(self, key: str, value: Any, *, notify: bool = False) -> None:
        """Untyped write (prefer typed helpers)."""
        self._s.setValue(key, value)
        if notify:
            self.preference_changed.emit(key)

    def remove(self, key: str) -> None:
        self._s.remove(key)

    # -- convenience: post-convert prefs ------------------------------------

    def copy_path_after(self) -> bool:
        return self.get_bool(Keys.UI_COPY_PATH_AFTER, True)

    def open_after(self) -> bool:
        return self.get_bool(Keys.UI_OPEN_AFTER, False)

    def show_folder_after(self) -> bool:
        return self.get_bool(Keys.UI_SHOW_FOLDER_AFTER, False)


# Process-wide default (tests replace via set_app_settings).
_app_settings: AppSettings | None = None


def get_app_settings() -> AppSettings:
    """Return the process-wide :class:`AppSettings` (created on first use)."""
    global _app_settings
    if _app_settings is None:
        _app_settings = AppSettings()
    return _app_settings


def set_app_settings(settings: AppSettings | None) -> None:
    """Install or clear the process-wide settings (use temp Ini in tests)."""
    global _app_settings
    _app_settings = settings


def make_ini_settings(path: str) -> AppSettings:
    """Create :class:`AppSettings` backed by an Ini file (tests / portable)."""
    backend = QSettings(path, QSettings.Format.IniFormat)
    return AppSettings(backend)


__all__ = [
    "AppSettings",
    "Keys",
    "get_app_settings",
    "set_app_settings",
    "make_ini_settings",
]
