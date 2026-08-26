from __future__ import annotations

import os
import threading
from pathlib import Path

from PySide6.QtCore import (
    QDir,
    QObject,
    QRunnable,
    QSize,
    QStandardPaths,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QGuiApplication,
    QIcon,
    QImage,
    QPainter,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QSizePolicy,
    QToolButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from .browser_path import folder_path_for_copy
from .browser_state import (
    VIEW_GRID,
    VIEW_LIST,
    VIEW_PREVIEW,
    load_favorite_paths,
    save_favorite_paths,
)
from .browser_volumes import MultiRootDirModel, list_browser_volumes
from .style import _PALETTE, STATUS_DIM

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


def _folder_path_for_copy(text: str) -> str:
    """Directory to copy for a file, folder, or ``name.####.ext`` path string."""
    return folder_path_for_copy(text)


def _add_copy_path_actions(menu: QMenu, *, file_path: str = "", folder_path: str = "") -> None:
    """Append **Copy File Path** / **Copy Folder Path** (disabled when empty)."""
    # QAction.triggered(bool) — never bind the bool as the path string.
    file_path = (file_path or "").strip()
    folder_path = (folder_path or "").strip()
    if not folder_path and file_path:
        folder_path = _folder_path_for_copy(file_path)

    copy_file = QAction("Copy File Path", menu)
    copy_file.setEnabled(bool(file_path))
    copy_file.triggered.connect(lambda _=False, p=file_path: _copy_to_clipboard(p))
    menu.addAction(copy_file)

    copy_folder = QAction("Copy Folder Path", menu)
    copy_folder.setEnabled(bool(folder_path))
    copy_folder.triggered.connect(lambda _=False, p=folder_path: _copy_to_clipboard(p))
    menu.addAction(copy_folder)


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


def _places_divider_item(list_widget: QListWidget) -> None:
    """Insert a thin horizontal rule row into a places list."""
    divider = QListWidgetItem()
    divider.setFlags(Qt.ItemFlag.NoItemFlags)
    divider.setSizeHint(divider.sizeHint().expandedTo(QWidget().sizeHint()))
    list_widget.addItem(divider)

    frame = QWidget()
    frame_layout = QVBoxLayout(frame)
    frame_layout.setContentsMargins(4, 6, 4, 4)
    frame_layout.setSpacing(0)
    line = QWidget()
    line.setFixedHeight(1)
    line.setStyleSheet(f"background: {_PALETTE['BORDER']};")
    frame_layout.addWidget(line)
    list_widget.setItemWidget(divider, frame)


class _PlacesSidebar(QWidget):
    """Sidebar listing volumes, OS locations, and user-defined favorites."""

    navigate_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setFixedWidth(150)
        self._current_dir = ""
        # Row ranges for rebuildable sections (volumes + static places stay fixed
        # after first build except the volumes block which is refreshed).
        self._vol_header_row = -1
        self._vol_start = 0
        self._vol_end = 0  # exclusive
        self._fav_start = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._list = _FavoritesDropList()
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.customContextMenuRequested.connect(self._ctx_menu)
        self._list.dirs_dropped.connect(self._on_dirs_dropped)

        # -- Volumes (drives / external media) — rebuilt by refresh_volumes --
        vol_header = QListWidgetItem("Volumes")
        vol_header.setFlags(Qt.ItemFlag.NoItemFlags)
        font = vol_header.font()
        font.setBold(True)
        vol_header.setFont(font)
        self._list.addItem(vol_header)
        self._vol_header_row = 0
        self._vol_start = 1
        self._vol_end = 1
        self._rebuild_volume_items()

        _places_divider_item(self._list)

        for icon, name, location in _OS_PLACES:
            path = QStandardPaths.writableLocation(location)
            if not path or not Path(path).is_dir():
                continue
            item = QListWidgetItem(f"{icon}  {name}")
            item.setData(Qt.ItemDataRole.UserRole, path)
            item.setToolTip(path)
            self._list.addItem(item)

        _places_divider_item(self._list)

        header = QListWidgetItem("\u2605  Favorites")
        header.setFlags(Qt.ItemFlag.NoItemFlags)
        font = header.font()
        font.setBold(True)
        header.setFont(font)
        self._list.addItem(header)
        self._fav_start = self._list.count()

        for fav_path in load_favorite_paths():
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

    def refresh_volumes(self) -> None:
        """Rescan mounted volumes in the places list."""
        self._rebuild_volume_items()

    def _rebuild_volume_items(self) -> None:
        """Replace volume rows under the Volumes header without touching favorites."""
        # Remove previous volume rows (from _vol_start to _vol_end exclusive).
        while self._vol_end > self._vol_start:
            self._list.takeItem(self._vol_start)
            self._vol_end -= 1
            self._fav_start = max(self._vol_start, self._fav_start - 1)

        volumes = list_browser_volumes()
        insert_at = self._vol_start
        for vol in volumes:
            # Prefer a disk glyph; system root gets a computer glyph.
            icon = "\U0001f5a5" if vol.is_system_root else "\U0001f4be"
            item = QListWidgetItem(f"{icon}  {vol.name}")
            item.setData(Qt.ItemDataRole.UserRole, vol.path)
            item.setToolTip(vol.path)
            self._list.insertItem(insert_at, item)
            insert_at += 1
            self._fav_start += 1
        self._vol_end = insert_at

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
        save_favorite_paths(favs)


def _setup_dir_tree(tree: QTreeView, fs_model: MultiRootDirModel, places: _PlacesSidebar) -> None:
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


def _tree_click_toggle_expand(tree: QTreeView, fs_model: MultiRootDirModel, index) -> None:
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


def _wire_volume_refresh(
    places: _PlacesSidebar,
    model: MultiRootDirModel,
    parent: QObject,
) -> QTimer:
    """Poll mounts so USB plug-in/eject updates places + tree roots."""

    def _tick() -> None:
        places.refresh_volumes()
        model.refresh_volumes()

    timer = QTimer(parent)
    timer.setInterval(3000)
    timer.timeout.connect(_tick)
    timer.start()
    return timer


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
        ".nev",
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
                        or name.startswith("._")
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
# Image sequence browser dialog (list / grid + metadata inspector)
# ---------------------------------------------------------------------------

# View mode string constants (aliases for browser_state; keep local names for
# call-site readability inside the dialog classes).
_SEQ_BROWSER_VIEW_LIST = VIEW_LIST
_SEQ_BROWSER_VIEW_GRID = VIEW_GRID
_SEQ_BROWSER_VIEW_PREVIEW = VIEW_PREVIEW
_VID_BROWSER_VIEW_LIST = VIEW_LIST
_VID_BROWSER_VIEW_GRID = VIEW_GRID
_VID_BROWSER_VIEW_PREVIEW = VIEW_PREVIEW


def _configure_path_line_edit(edit: QLineEdit) -> None:
    """Path field must not grow the dialog when the text is very long.

    ``QLineEdit.sizeHint()`` tracks content width; with a normal Expanding
    policy that becomes the layout minimum. Horizontal *Ignored* lets the
    layout assign width (stretch=1) without following path length.
    """
    edit.setMinimumWidth(80)
    edit.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)


class _ElidingLabel(QLabel):
    """Status/footer label that elides long text instead of forcing layout width.

    Plain ``QLabel`` uses the full text for ``sizeHint``, which expands dialogs
    when the status line contains a long path. This widget ignores content
    width and shows an elided string (middle ellipsis) for the current width.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full_text = ""
        self.setMinimumWidth(40)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setWordWrap(False)

    def setText(self, text: str | None) -> None:  # type: ignore[override]
        self._full_text = str(text or "")
        # Full text on hover when truncated.
        self.setToolTip(self._full_text if len(self._full_text) > 24 else "")
        self._apply_elide()

    def text(self) -> str:  # type: ignore[override]
        return self._full_text

    def resizeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().resizeEvent(event)
        self._apply_elide()

    def _apply_elide(self) -> None:
        fm = self.fontMetrics()
        w = max(1, self.width() - 2)
        elided = fm.elidedText(self._full_text, Qt.TextElideMode.ElideMiddle, w)
        # Bypass our setText to avoid recursion / clearing tooltip.
        QLabel.setText(self, elided)


_SEQ_THUMB_EDGE = 160
_SEQ_THUMB_ICON = QSize(160, 100)


class _ThumbSignals(QObject):
    """Signals for :class:`_ThumbJob` (QRunnable cannot define Qt signals)."""

    ready = Signal(int, int, object)  # gen, row, QImage | None

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)


class _ThumbJob(QRunnable):
    """Background first-frame thumbnail decode for one sequence row."""

    def __init__(self, gen: int, row: int, path: str, signals: _ThumbSignals) -> None:
        super().__init__()
        self._gen = gen
        self._row = row
        self._path = path
        self._signals = signals
        self.setAutoDelete(True)

    def run(self) -> None:
        # Lazy import: this runs on a QThreadPool worker thread, not the GUI
        # thread — avoid pulling numpy / OIIO onto the import path for callers
        # that never open a browser dialog.
        import numpy as np

        from .browser_thumbs import load_browser_thumbnail_rgb, load_video_thumbnail_rgb

        qimg: QImage | None = None
        try:
            ext = Path(self._path).suffix.lower()
            if ext in _VIDEO_EXTS:
                rgb = load_video_thumbnail_rgb(self._path, max_edge=_SEQ_THUMB_EDGE)
            else:
                rgb = load_browser_thumbnail_rgb(self._path, max_edge=_SEQ_THUMB_EDGE)
            if rgb is not None and rgb.ndim == 3 and rgb.shape[2] >= 3:
                h, w = int(rgb.shape[0]), int(rgb.shape[1])
                # Copy so QImage owns a stable buffer after the numpy array frees.
                buf = np.ascontiguousarray(rgb[:, :, :3], dtype=np.uint8).copy()
                qimg = QImage(buf.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()
        except Exception:
            qimg = None
        self._signals.ready.emit(self._gen, self._row, qimg)
