"""QSettings keys and helpers for input file-browser dialogs.

Video → EXR (``VideoBrowserDialog``) and EXR → Video (``SequenceBrowserDialog``)
persist **separate** layout/session state under ``ui/video_browser_*`` and
``ui/sequence_browser_*``. Window **geometry** (size + position) is **shared**
via ``ui/browser_geometry`` so both dialogs open at the same place on screen.

When the start directory matches the last directory for that browser mode,
session extras (tree expansion, selection, scroll) are restored so Browse
reopens as the user left it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from PySide6.QtCore import QByteArray, QModelIndex, QSettings
from PySide6.QtWidgets import QTreeView


@runtime_checkable
class DirTreeModel(Protocol):
    """Subset of QFileSystemModel / MultiRootDirModel used by browser helpers."""

    def index(self, *args): ...  # path str or (row, col, parent)
    def filePath(self, index: QModelIndex) -> str: ...
    def isDir(self, index: QModelIndex) -> bool: ...
    def rowCount(self, parent: QModelIndex = ...) -> int: ...


# Shared by sequence + video browsers.
BROWSER_GEOMETRY_KEY = "ui/browser_geometry"
# Pre-0.8 sequence-only geometry; still read as fallback.
_LEGACY_SEQ_BROWSER_GEOMETRY_KEY = "ui/sequence_browser_geometry"

VIEW_LIST = "list"
VIEW_GRID = "grid"
VIEW_PREVIEW = "preview"

# Favorites (shared); always use browser_qsettings() so org/app match the app.
BROWSER_FAVORITES_KEY = "ui/browser_favorites"
_LEGACY_FAVORITES_KEY = "browser/favorites"


def load_favorite_paths() -> list[str]:
    """Read favorite directories (migrates legacy bare ``browser/favorites`` once)."""
    settings = browser_qsettings()
    raw = settings.value(BROWSER_FAVORITES_KEY, None)
    if raw is None:
        raw = settings.value(_LEGACY_FAVORITES_KEY, [])
    if isinstance(raw, str):
        return [raw] if raw else []
    if isinstance(raw, (list, tuple)):
        return [str(x) for x in raw]
    return []


def save_favorite_paths(paths: list[str]) -> None:
    settings = browser_qsettings()
    settings.setValue(BROWSER_FAVORITES_KEY, list(paths))
    settings.sync()


@dataclass(frozen=True, slots=True)
class BrowserPreviewContext:
    """OCIO + rate injected into file browsers (no parent-attribute poking)."""

    ocio_cfg: object | None = None
    src_colorspace: str = ""
    fps: float = 24.0


@dataclass(frozen=True, slots=True)
class BrowserSettingsKeys:
    """Per-mode QSettings key set (geometry is not included — use shared key)."""

    kind: str  # "sequence" | "video"

    @property
    def _p(self) -> str:
        return f"ui/{self.kind}_browser"

    @property
    def view(self) -> str:
        return f"{self._p}_view"

    @property
    def last_browse(self) -> str:
        """Last non-Preview mode (list or grid) for Esc / Back from Preview."""
        return f"{self._p}_last_browse"

    @property
    def outer_split(self) -> str:
        return f"{self._p}_outer_splitter"

    @property
    def content_split(self) -> str:
        return f"{self._p}_content_splitter"

    @property
    def col_widths(self) -> str:
        return f"{self._p}_column_widths"

    @property
    def header_state(self) -> str:
        return f"{self._p}_header_state"

    @property
    def inspect(self) -> str:
        return f"{self._p}_inspect"

    @property
    def last_dir(self) -> str:
        return f"{self._p}_last_dir"

    @property
    def selected(self) -> str:
        """Sequence basename or video file path last selected in this browser."""
        return f"{self._p}_selected"

    @property
    def tree_expanded(self) -> str:
        return f"{self._p}_tree_expanded"

    @property
    def tree_vscroll(self) -> str:
        return f"{self._p}_tree_vscroll"


SEQ_BROWSER_KEYS = BrowserSettingsKeys("sequence")
VID_BROWSER_KEYS = BrowserSettingsKeys("video")


def browser_qsettings() -> QSettings:
    """Shared process QSettings (same backend as :func:`get_app_settings`)."""
    from ..services.app_settings import get_app_settings

    return get_app_settings().qsettings


def normalize_dir(path: str | Path | None) -> str:
    """Absolute, resolved directory path for equality checks ('' if unusable)."""
    if path is None:
        return ""
    raw = str(path).strip()
    if not raw:
        return ""
    try:
        p = Path(raw).expanduser()
        if p.is_file():
            p = p.parent
        if not p.is_dir():
            # Still normalize for comparison when the folder existed last session.
            return os.path.normcase(os.path.normpath(str(p)))
        return os.path.normcase(str(p.resolve()))
    except (OSError, RuntimeError, ValueError):
        try:
            return os.path.normcase(os.path.normpath(raw))
        except Exception:
            return raw


def dirs_equal(a: str | Path | None, b: str | Path | None) -> bool:
    na, nb = normalize_dir(a), normalize_dir(b)
    return bool(na) and na == nb


def parse_int_list(value: object) -> list[int] | None:
    if value is None:
        return None
    try:
        if isinstance(value, (list, tuple)):
            out = [int(x) for x in value]
        else:
            return None
        return out
    except (TypeError, ValueError):
        return None


def parse_str_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (list, tuple)):
        return [str(x) for x in value if str(x)]
    return []


def settings_bool(settings: QSettings, key: str, default: bool) -> bool:
    raw = settings.value(key, default)
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        return bool(raw)
    if isinstance(raw, str):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    return default


def load_shared_geometry(settings: QSettings | None = None) -> QByteArray | None:
    s = settings if settings is not None else browser_qsettings()
    geo = s.value(BROWSER_GEOMETRY_KEY)
    if geo is not None:
        return geo  # type: ignore[return-value]
    # Migrate pre-shared sequence geometry once.
    legacy = s.value(_LEGACY_SEQ_BROWSER_GEOMETRY_KEY)
    if legacy is not None:
        s.setValue(BROWSER_GEOMETRY_KEY, legacy)
        return legacy  # type: ignore[return-value]
    return None


def save_shared_geometry(geometry: QByteArray, settings: QSettings | None = None) -> None:
    s = settings if settings is not None else browser_qsettings()
    s.setValue(BROWSER_GEOMETRY_KEY, geometry)


def collect_expanded_dirs(tree: QTreeView, model: DirTreeModel) -> list[str]:
    """Return absolute paths of expanded directories currently loaded in the model."""
    out: list[str] = []

    def walk(parent: QModelIndex) -> None:
        rows = model.rowCount(parent)
        for r in range(rows):
            idx = model.index(r, 0, parent)
            if not idx.isValid() or not model.isDir(idx):
                continue
            if tree.isExpanded(idx):
                path = model.filePath(idx)
                if path:
                    out.append(path)
                walk(idx)

    walk(QModelIndex())
    return out


def expand_path_chain(tree: QTreeView, model: DirTreeModel, path: str) -> bool:
    """Expand every ancestor of *path* (and the path itself if it is a dir)."""
    if not path:
        return False
    try:
        p = Path(path)
        if p.is_file():
            p = p.parent
        target = str(p)
    except (OSError, RuntimeError, ValueError):
        target = path

    idx = model.index(target)
    if not idx.isValid():
        # Try resolved form.
        try:
            idx = model.index(str(Path(target).resolve()))
        except (OSError, RuntimeError, ValueError):
            return False
    if not idx.isValid():
        return False

    chain: list[QModelIndex] = []
    cur = idx
    while cur.isValid():
        chain.append(cur)
        cur = cur.parent()
    for node in reversed(chain):
        if model.isDir(node):
            tree.expand(node)
    return True


def restore_tree_expanded(
    tree: QTreeView,
    model: DirTreeModel,
    paths: list[str],
    focus_path: str = "",
) -> None:
    """Expand *paths* (parents first) and optionally focus *focus_path*."""
    ordered = sorted({p for p in paths if p}, key=lambda p: (p.count(os.sep), p))
    for p in ordered:
        if Path(p).is_dir() or model.index(p).isValid():
            expand_path_chain(tree, model, p)
    if focus_path:
        expand_path_chain(tree, model, focus_path)
        idx = model.index(focus_path)
        if not idx.isValid():
            try:
                idx = model.index(str(Path(focus_path).resolve()))
            except (OSError, RuntimeError, ValueError):
                idx = QModelIndex()
        if idx.isValid():
            tree.setCurrentIndex(idx)


def tree_vscroll_value(tree: QTreeView) -> int:
    bar = tree.verticalScrollBar()
    return int(bar.value()) if bar is not None else 0


def set_tree_vscroll(tree: QTreeView, value: int) -> None:
    bar = tree.verticalScrollBar()
    if bar is not None and value >= 0:
        bar.setValue(int(value))


def coerce_view_mode(raw: object, *, allow_preview: bool = True) -> str:
    s = str(raw or VIEW_LIST).strip().lower()
    if s == VIEW_GRID:
        return VIEW_GRID
    if s == VIEW_PREVIEW and allow_preview:
        return VIEW_PREVIEW
    return VIEW_LIST
