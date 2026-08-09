"""Preferences dialog and helpers for post-convert actions (player, reveal)."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ..services.app_settings import Keys
from ..services.cache_prefs import (
    load_cache_budget_pct,
    save_cache_budget_pct,
    total_ram_bytes,
)

# QSettings keys (canonical registry: app_settings.Keys)
PLAYER_MODE_KEY = Keys.PLAYER_MODE
PLAYER_PATH_KEY = Keys.PLAYER_PATH
THUMBNAIL_FRAME_KEY = Keys.SLATE_THUMBNAIL_FRAME

# Built-in SequencePlayer is the default for Open result (EXR → Video).
PLAYER_MODE_BUILTIN = "builtin"
PLAYER_MODE_SYSTEM = "system"
PLAYER_MODE_CUSTOM = "custom"
_PLAYER_MODES = frozenset({PLAYER_MODE_BUILTIN, PLAYER_MODE_SYSTEM, PLAYER_MODE_CUSTOM})

# Integer prefs for which source frame becomes the slate thumbnail.
THUMBNAIL_FRAME_FIRST = 0
THUMBNAIL_FRAME_MID = 1
THUMBNAIL_FRAME_LAST = 2
THUMBNAIL_FRAME_CHOICES: tuple[tuple[int, str], ...] = (
    (THUMBNAIL_FRAME_FIRST, "First frame"),
    (THUMBNAIL_FRAME_MID, "Middle frame"),
    (THUMBNAIL_FRAME_LAST, "Last frame"),
)


def player_mode(settings: QSettings) -> str:
    """Return preferred Open-result video player mode (default: built-in)."""
    mode = str(settings.value(PLAYER_MODE_KEY, PLAYER_MODE_BUILTIN) or PLAYER_MODE_BUILTIN)
    if mode not in _PLAYER_MODES:
        return PLAYER_MODE_BUILTIN
    return mode


def player_path(settings: QSettings) -> str:
    return str(settings.value(PLAYER_PATH_KEY, "") or "").strip()


def set_player_prefs(settings: QSettings, mode: str, path: str) -> None:
    if mode not in _PLAYER_MODES:
        mode = PLAYER_MODE_BUILTIN
    settings.setValue(PLAYER_MODE_KEY, mode)
    settings.setValue(PLAYER_PATH_KEY, path.strip())


def thumbnail_frame_choice(settings: QSettings) -> int:
    """Return which frame to use for the slate thumbnail (0=first, 1=mid, 2=last)."""
    # Prefer typed read; fall back for stringy platforms / older values.
    try:
        raw = settings.value(THUMBNAIL_FRAME_KEY, THUMBNAIL_FRAME_MID, type=int)
    except (TypeError, ValueError):
        raw = settings.value(THUMBNAIL_FRAME_KEY, THUMBNAIL_FRAME_MID)
    try:
        val = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return THUMBNAIL_FRAME_MID
    if val not in (
        THUMBNAIL_FRAME_FIRST,
        THUMBNAIL_FRAME_MID,
        THUMBNAIL_FRAME_LAST,
    ):
        return THUMBNAIL_FRAME_MID
    return val


def set_thumbnail_frame_choice(settings: QSettings, which: int) -> None:
    which_i = int(which)
    if which_i not in (
        THUMBNAIL_FRAME_FIRST,
        THUMBNAIL_FRAME_MID,
        THUMBNAIL_FRAME_LAST,
    ):
        which_i = THUMBNAIL_FRAME_MID
    settings.setValue(THUMBNAIL_FRAME_KEY, which_i)


def pick_thumbnail_index(count: int, which: int) -> int:
    """Map a thumbnail preference to an index in ``[0, count)``."""
    n = int(count)
    if n <= 0:
        return 0
    w = int(which)
    if w == THUMBNAIL_FRAME_FIRST:
        return 0
    if w == THUMBNAIL_FRAME_LAST:
        return n - 1
    return n // 2


def _is_macos_app_bundle(path: Path) -> bool:
    return sys.platform == "darwin" and path.suffix.lower() == ".app" and path.is_dir()


def open_video_with_player(path: str | Path, settings: QSettings) -> str:
    """Open *path* with the user's preferred **external** player.

    Handles ``system`` and ``custom`` modes only. For ``builtin``, the main
    window should open :class:`~src.gui.player.player_window.SequencePlayerWindow`
    (it needs a long-lived Qt parent/holder). Calling this with builtin falls
    back to the system default handler.

    Returns a short status string suitable for the log / status bar.
    Falls back to the system default handler when custom is unset or fails.
    """
    media = Path(path)
    if not media.is_file():
        raise FileNotFoundError(f"Video not found: {media}")

    mode = player_mode(settings)
    custom = player_path(settings)

    if mode == PLAYER_MODE_CUSTOM and custom:
        try:
            return _launch_custom_player(custom, media)
        except OSError as e:
            # Fall through to system default so convert still "succeeds".
            _open_with_system_default(media)
            return f"custom player failed ({e}); opened with system default"

    if mode == PLAYER_MODE_BUILTIN:
        # Prefer MainWindow path; system default is a safe external fallback.
        _open_with_system_default(media)
        return "opened with system default (built-in unavailable here)"

    _open_with_system_default(media)
    return "opened with system default"


def _open_with_system_default(media: Path) -> None:
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(media)))


def _launch_custom_player(player: str, media: Path) -> str:
    p = Path(player).expanduser()
    if not p.exists():
        # Allow bare command names on PATH (mpv, vlc, ffplay, …).
        which = shutil.which(player)
        if which:
            p = Path(which)
        else:
            raise FileNotFoundError(f"player not found: {player}")

    media_s = str(media)
    label = p.name

    if _is_macos_app_bundle(p):
        # open -a "App.app" file.mov
        subprocess.Popen(
            ["open", "-a", str(p), media_s],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return f"opened in {p.stem}"

    if not p.is_file():
        raise FileNotFoundError(f"not an executable or app: {p}")

    # DETACHED_PROCESS so we don't inherit a console; do not set CREATE_NO_WINDOW
    # or GUI players (VLC, etc.) can fail to show a window.
    creationflags = 0
    if sys.platform == "win32":
        creationflags = getattr(subprocess, "DETACHED_PROCESS", 0)

    subprocess.Popen(
        [str(p), media_s],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        creationflags=creationflags,
    )
    return f"opened in {label}"


def file_manager_label() -> str:
    """User-facing name of the OS file manager (for menus / tooltips)."""
    if sys.platform == "darwin":
        return "Open in Finder"
    if sys.platform == "win32":
        return "Show in Explorer"
    return "Open Containing Folder"


def path_is_revealable(path: str | Path) -> bool:
    """Return True when *path* or its parent directory exists on disk."""
    raw = str(path or "").strip()
    if not raw:
        return False
    p = Path(raw).expanduser()
    try:
        if p.exists():
            return True
        return p.parent.is_dir()
    except OSError:
        return False


def reveal_in_file_manager(path: str | Path) -> str:
    """Reveal *path* in the OS file manager (select file when possible).

    Returns a short status string for the log.
    """
    target = Path(path).expanduser().resolve()
    if not target.exists():
        # Fall back to parent if the leaf was just written / renamed.
        parent = target.parent
        if parent.is_dir():
            target = parent
        else:
            raise FileNotFoundError(f"path not found: {path}")

    if sys.platform == "darwin":
        if target.is_file():
            subprocess.Popen(
                ["open", "-R", str(target)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return f"revealed in Finder: {target.name}"
        subprocess.Popen(
            ["open", str(target)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return f"opened folder: {target}"

    if sys.platform == "win32":
        if target.is_file():
            subprocess.Popen(
                ["explorer", f"/select,{target}"],
                start_new_session=True,
            )
            return f"revealed in Explorer: {target.name}"
        subprocess.Popen(
            ["explorer", str(target)],
            start_new_session=True,
        )
        return f"opened folder: {target}"

    # Linux / other: open the directory (no portable "select file" API).
    folder = target if target.is_dir() else target.parent
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
    return f"opened folder: {folder}"


def _known_player_candidates() -> list[tuple[str, str]]:
    """Return ``(label, path)`` pairs for common players that exist on this machine."""
    found: list[tuple[str, str]] = []
    seen: set[str] = set()

    def _add(label: str, path: str) -> None:
        key = path.rstrip("/").lower()
        if key in seen:
            return
        if Path(path).exists() or shutil.which(path):
            seen.add(key)
            found.append((label, path if Path(path).exists() else (shutil.which(path) or path)))

    if sys.platform == "darwin":
        for label, app in (
            ("IINA", "/Applications/IINA.app"),
            ("VLC", "/Applications/VLC.app"),
            ("mpv", "/Applications/mpv.app"),
            ("QuickTime Player", "/System/Applications/QuickTime Player.app"),
        ):
            _add(label, app)
        # Homebrew CLI players
        for name in ("mpv", "ffplay", "vlc"):
            which = shutil.which(name)
            if which:
                _add(name, which)
            else:
                for prefix in ("/opt/homebrew/bin", "/usr/local/bin"):
                    candidate = f"{prefix}/{name}"
                    if Path(candidate).is_file():
                        _add(name, candidate)
                        break
    elif sys.platform == "win32":
        for label, path in (
            ("VLC", r"C:\Program Files\VideoLAN\VLC\vlc.exe"),
            ("VLC (x86)", r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe"),
            ("mpv", r"C:\Program Files\mpv\mpv.exe"),
        ):
            _add(label, path)
        for name in ("mpv", "ffplay", "vlc"):
            which = shutil.which(name)
            if which:
                _add(name, which)
    else:
        for name in ("mpv", "vlc", "ffplay", "totem", "celluloid"):
            which = shutil.which(name)
            if which:
                _add(name, which)

    return found


class PreferencesDialog(QDialog):
    """App preferences — currently the video player used by “Open result”."""

    def __init__(self, settings: QSettings, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Preferences")
        self.setMinimumWidth(520)
        self._settings = settings

        root = QVBoxLayout(self)
        root.setSpacing(12)

        player_box = QGroupBox("Video player")
        player_layout = QVBoxLayout(player_box)

        hint = QLabel(
            "Used when <b>Open result</b> is checked after an EXR → video conversion. "
            "Default is the built-in player (same OCIO viewer as sequence Preview). "
            "Or use the system default / a custom app (IINA, VLC, mpv, ffplay, …)."
        )
        hint.setWordWrap(True)
        hint.setTextFormat(Qt.TextFormat.RichText)
        hint.setStyleSheet("color: #aaa;")
        player_layout.addWidget(hint)

        self._mode_group = QButtonGroup(self)
        self._builtin_radio = QRadioButton("Built-in player")
        self._builtin_radio.setToolTip(
            "Open the finished video in EXR Converter’s built-in SequencePlayer "
            "(GPU OCIO display, cache strip, same transport as browser Preview)."
        )
        self._system_radio = QRadioButton("System default")
        self._system_radio.setToolTip(
            "Open with the OS default application for this file type "
            "(QuickTime, Windows Photos, etc.)."
        )
        self._custom_radio = QRadioButton("Custom application")
        self._custom_radio.setToolTip(
            "Launch a specific player app or executable with the output file."
        )
        self._mode_group.addButton(self._builtin_radio)
        self._mode_group.addButton(self._system_radio)
        self._mode_group.addButton(self._custom_radio)
        player_layout.addWidget(self._builtin_radio)
        player_layout.addWidget(self._system_radio)
        player_layout.addWidget(self._custom_radio)

        custom_row = QHBoxLayout()
        self._path_edit = QLineEdit()
        self._path_edit.setPlaceholderText(
            "/Applications/IINA.app  or  /usr/local/bin/mpv  or  mpv"
        )
        self._browse_btn = QPushButton("Browse…")
        self._browse_btn.clicked.connect(self._browse_player)
        custom_row.addWidget(self._path_edit, 1)
        custom_row.addWidget(self._browse_btn)
        player_layout.addLayout(custom_row)

        detected = _known_player_candidates()
        if detected:
            quick = QHBoxLayout()
            quick_lbl = QLabel("Detected:")
            quick_lbl.setStyleSheet("color: #888;")
            quick.addWidget(quick_lbl)
            for label, path in detected:
                btn = QPushButton(label)
                btn.setToolTip(path)
                btn.clicked.connect(lambda _checked=False, p=path: self._use_detected(p))
                quick.addWidget(btn)
            quick.addStretch()
            player_layout.addLayout(quick)

        root.addWidget(player_box)

        # --- Slate thumbnail frame ---
        slate_box = QGroupBox("Slate")
        slate_form = QFormLayout(slate_box)
        self._thumb_combo = QComboBox()
        for value, label in THUMBNAIL_FRAME_CHOICES:
            self._thumb_combo.addItem(label, int(value))
        self._thumb_combo.setToolTip(
            "Which frame of the EXR sequence is used for the slate thumbnail "
            "(EXR → video only). Picked by index from the known frame list — "
            "first, middle, or last. Converted to the slate authoring space "
            "(sRGB-like) so colour management matches the rest of the slate."
        )
        slate_form.addRow("Thumbnail frame:", self._thumb_combo)
        root.addWidget(slate_box)

        # Load current values
        mode = player_mode(settings)
        if mode == PLAYER_MODE_CUSTOM:
            self._custom_radio.setChecked(True)
        elif mode == PLAYER_MODE_SYSTEM:
            self._system_radio.setChecked(True)
        else:
            self._builtin_radio.setChecked(True)
        self._path_edit.setText(player_path(settings))
        self._sync_custom_enabled()
        self._builtin_radio.toggled.connect(self._sync_custom_enabled)
        self._system_radio.toggled.connect(self._sync_custom_enabled)
        self._custom_radio.toggled.connect(self._sync_custom_enabled)

        which = thumbnail_frame_choice(settings)
        for i in range(self._thumb_combo.count()):
            if int(self._thumb_combo.itemData(i)) == which:
                self._thumb_combo.setCurrentIndex(i)
                break

        # --- Playback RAM cache budget ---
        cache_box = QGroupBox("Playback cache")
        cache_layout = QVBoxLayout(cache_box)
        cache_hint = QLabel(
            "How much system RAM the sequence player may use for decoded frames "
            "(slate editor and file-browser Preview). Higher values mean longer "
            "smooth play; lower values free memory for other apps."
        )
        cache_hint.setWordWrap(True)
        cache_hint.setStyleSheet("color: #aaa;")
        cache_layout.addWidget(cache_hint)

        cache_row = QHBoxLayout()
        cache_row.addWidget(QLabel("Budget:"))
        self._cache_slider = QSlider(Qt.Orientation.Horizontal)
        self._cache_slider.setRange(1, 90)
        self._cache_slider.setValue(load_cache_budget_pct(settings))
        self._cache_slider.setToolTip("Playback RAM cache as % of physical memory")
        self._cache_value = QLabel()
        self._cache_value.setMinimumWidth(120)
        self._cache_slider.valueChanged.connect(self._update_cache_label)
        self._update_cache_label(self._cache_slider.value())
        cache_row.addWidget(self._cache_slider, 1)
        cache_row.addWidget(self._cache_value)
        cache_layout.addLayout(cache_row)
        root.addWidget(cache_box)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _update_cache_label(self, pct: int) -> None:
        budget_gb = total_ram_bytes() * pct / 100 / (1024**3)
        total_gb = total_ram_bytes() / (1024**3)
        self._cache_value.setText(f"{pct}%  (~{budget_gb:.1f} / {total_gb:.1f} GB)")

    def _sync_custom_enabled(self) -> None:
        on = self._custom_radio.isChecked()
        self._path_edit.setEnabled(on)
        self._browse_btn.setEnabled(on)

    def _use_detected(self, path: str) -> None:
        self._custom_radio.setChecked(True)
        self._path_edit.setText(path)
        self._sync_custom_enabled()

    def _browse_player(self) -> None:
        if sys.platform == "darwin":
            # .app bundles are directories; start in /Applications.
            start = "/Applications"
            current = self._path_edit.text().strip()
            if current and Path(current).exists():
                start = str(Path(current).parent)
            path = QFileDialog.getExistingDirectory(
                self,
                "Select Application (.app) or folder",
                start,
            )
            if path:
                self._path_edit.setText(path)
            return

        if sys.platform == "win32":
            path, _ = QFileDialog.getOpenFileName(
                self,
                "Select video player",
                self._path_edit.text().strip() or r"C:\Program Files",
                "Executables (*.exe);;All files (*.*)",
            )
        else:
            path, _ = QFileDialog.getOpenFileName(
                self,
                "Select video player",
                self._path_edit.text().strip() or "/usr/bin",
                "All files (*)",
            )
        if path:
            self._path_edit.setText(path)

    def _on_accept(self) -> None:
        if self._custom_radio.isChecked():
            mode = PLAYER_MODE_CUSTOM
        elif self._system_radio.isChecked():
            mode = PLAYER_MODE_SYSTEM
        else:
            mode = PLAYER_MODE_BUILTIN
        path = self._path_edit.text().strip()
        if mode == PLAYER_MODE_CUSTOM and not path:
            QMessageBox.warning(
                self,
                "Preferences",
                "Choose a custom player path, or switch to Built-in / System default.",
            )
            return
        if mode == PLAYER_MODE_CUSTOM and path:
            p = Path(path).expanduser()
            if not p.exists() and not shutil.which(path):
                # Soft warning — allow PATH names that may appear later, but
                # warn when the path looks absolute/relative and is missing.
                if "/" in path or "\\" in path or path.endswith(".app"):
                    QMessageBox.warning(
                        self,
                        "Preferences",
                        f"Player path does not exist:\n{path}",
                    )
                    return
        set_player_prefs(self._settings, mode, path)
        thumb_data = self._thumb_combo.currentData()
        set_thumbnail_frame_choice(
            self._settings,
            int(thumb_data) if thumb_data is not None else THUMBNAIL_FRAME_MID,
        )
        save_cache_budget_pct(self._settings, int(self._cache_slider.value()))
        self.accept()
