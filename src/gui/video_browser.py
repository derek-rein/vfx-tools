from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, QSize, Qt, QThreadPool, QTimer, Slot
from PySide6.QtGui import (
    QAction,
    QColor,
    QIcon,
    QImage,
    QKeyEvent,
    QKeySequence,
    QPainter,
    QPixmap,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPlainTextEdit,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from ..core.video import probe_video_metadata, scan_video_files
from .browser_chrome import (
    _SEQ_THUMB_ICON,
    _VID_BROWSER_VIEW_GRID,
    _VID_BROWSER_VIEW_LIST,
    _VID_BROWSER_VIEW_PREVIEW,
    _VIDEO_EXTS,
    _add_copy_path_actions,
    _configure_path_line_edit,
    _ElidingLabel,
    _PlacesSidebar,
    _SearchableTree,
    _setup_dir_tree,
    _ThumbJob,
    _ThumbSignals,
    _tree_click_toggle_expand,
    _wire_volume_refresh,
)
from .browser_path import clean_path_string, resolve_video_browser_path
from .browser_state import (
    VID_BROWSER_KEYS,
    VIEW_GRID,
    VIEW_LIST,
    VIEW_PREVIEW,
    BrowserPreviewContext,
    browser_qsettings,
    coerce_view_mode,
    collect_expanded_dirs,
    dirs_equal,
    expand_path_chain,
    load_shared_geometry,
    normalize_dir,
    parse_int_list,
    parse_str_list,
    restore_tree_expanded,
    save_shared_geometry,
    set_tree_vscroll,
    settings_bool,
    tree_vscroll_value,
)
from .browser_volumes import MultiRootDirModel
from .player.sequence_player import SequencePlayer
from .segmented_control import SegmentedControl
from .style import STATUS_DIM

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Video file browser dialog (with metadata inspector)
# ---------------------------------------------------------------------------


class VideoBrowserDialog(QDialog):
    """Directory browser + video file table + in-dialog playback preview."""

    _COLUMNS = ["Name", "Resolution", "Codec", "FPS", "Frames", "Duration"]

    def __init__(
        self,
        start_dir: str = "",
        parent: QWidget | None = None,
        *,
        preview: BrowserPreviewContext | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Browse Video Files")
        self.resize(1060, 520)
        self._keys = VID_BROWSER_KEYS
        self._preview_ctx = preview or BrowserPreviewContext()
        self._selected_path: str = ""
        self._file_data: list[dict[str, str]] = []
        self._auto_select_path: str = ""
        self._player = None
        self._previewing = False
        self._last_browse_mode = _VID_BROWSER_VIEW_LIST
        self._same_path_session = False
        self._pending_preview = False
        self._thumb_gen = 0
        self._thumb_cache: dict[str, QPixmap] = {}
        self._placeholder_icon = self._make_vid_placeholder_icon()
        self._thumb_signals = _ThumbSignals(self)
        self._thumb_signals.ready.connect(self._on_vid_thumb_ready)
        self._thumb_pool = QThreadPool.globalInstance()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        # Don't let children force the dialog wider than the user sized it.
        layout.setSizeConstraint(QVBoxLayout.SizeConstraint.SetDefaultConstraint)

        path_row = QHBoxLayout()
        path_row.addWidget(QLabel("Folder:"))
        self._path_edit = QLineEdit()
        self._path_edit.setPlaceholderText("Navigate in the tree or paste a path here")
        _configure_path_line_edit(self._path_edit)
        path_row.addWidget(self._path_edit, 1)
        self._view_seg = SegmentedControl(
            [
                ("List", _VID_BROWSER_VIEW_LIST),
                ("Grid", _VID_BROWSER_VIEW_GRID),
                ("Preview", _VID_BROWSER_VIEW_PREVIEW),
            ],
            parent=self,
        )
        self._view_seg.setSegmentToolTip(0, "List view (table)")
        self._view_seg.setSegmentToolTip(1, "Grid view with first-frame thumbnails")
        self._view_seg.setSegmentToolTip(2, "Playback of the selected / first video")
        path_row.addWidget(self._view_seg)
        self._inspect_cb = QCheckBox("Inspect")
        self._inspect_cb.setToolTip("Show video metadata for selected / previewed file")
        path_row.addWidget(self._inspect_cb)
        layout.addLayout(path_row)

        # -- left: places sidebar + dir tree (full height) --
        self._places = _PlacesSidebar()
        self._places.navigate_requested.connect(self._navigate_to)

        self._fs_model = MultiRootDirModel(self)
        self._tree = QTreeView()
        self._tree.setModel(self._fs_model)
        self._tree.setHeaderHidden(True)
        self._tree.setMinimumWidth(200)
        tree_header = self._tree.header()
        tree_header.setStretchLastSection(True)
        tree_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self._volume_timer = _wire_volume_refresh(self._places, self._fs_model, self)

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
        self._table.setMinimumWidth(200)
        self._table.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self._table.setWordWrap(False)
        self._table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        th = self._table.horizontalHeader()
        th.setStretchLastSection(False)
        th.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        # Interactive (not ResizeToContents) so long paths never force dialog width.
        for col in range(1, len(self._COLUMNS)):
            th.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
            th.resizeSection(col, 88)
        th.resizeSection(1, 100)
        th.resizeSection(2, 80)
        th.resizeSection(3, 56)
        th.resizeSection(4, 64)
        th.resizeSection(5, 72)
        # table alone is not the center — stack holds list/grid/preview
        self._list_page = QWidget()
        list_layout = QVBoxLayout(self._list_page)
        list_layout.setContentsMargins(0, 0, 0, 0)
        list_layout.addWidget(self._table, 1)

        self._grid = QListWidget()
        self._grid.setViewMode(QListWidget.ViewMode.IconMode)
        self._grid.setIconSize(_SEQ_THUMB_ICON)
        self._grid.setResizeMode(QListWidget.ResizeMode.Adjust)
        self._grid.setMovement(QListWidget.Movement.Static)
        self._grid.setUniformItemSizes(True)
        self._grid.setSpacing(10)
        self._grid.setWordWrap(True)
        self._grid.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._grid.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self._grid.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._grid.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._grid.setMinimumWidth(200)

        # Preview page (SequencePlayer via load_video)
        self._preview_page = QWidget()
        preview_layout = QVBoxLayout(self._preview_page)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(0)
        self._preview_host = preview_layout

        self._view_stack = QStackedWidget()
        self._view_stack.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._view_stack.addWidget(self._list_page)  # 0 list
        self._view_stack.addWidget(self._grid)  # 1 grid
        self._view_stack.addWidget(self._preview_page)  # 2 preview
        center_layout.addWidget(self._view_stack, 1)
        center.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        center.setMinimumWidth(200)

        # Pre-create player before dialog show (OpenGL surface rule).
        try:
            self._ensure_player()
        except Exception:
            log.exception("Could not pre-create video player")

        # -- right: metadata inspector --
        self._meta_panel = QWidget()
        meta_layout = QVBoxLayout(self._meta_panel)
        meta_layout.setContentsMargins(0, 0, 0, 0)
        meta_layout.setSpacing(4)
        meta_layout.addWidget(QLabel("<b>Video Metadata</b>"))
        self._meta_text = QPlainTextEdit()
        self._meta_text.setReadOnly(True)
        self._meta_text.setMinimumWidth(160)
        self._meta_text.setObjectName("metaPane")
        meta_layout.addWidget(self._meta_text, 1)
        self._meta_panel.setVisible(False)
        self._meta_panel.setMinimumWidth(160)

        # content splitter: list/grid/preview + metadata
        self._content_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._content_splitter.addWidget(center)
        self._content_splitter.addWidget(self._meta_panel)
        self._content_splitter.setStretchFactor(0, 1)
        self._content_splitter.setStretchFactor(1, 0)
        self._content_splitter.setCollapsible(0, False)
        self._content_splitter.setCollapsible(1, False)

        # right side: content splitter + status/buttons row
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)
        right_layout.addWidget(self._content_splitter, 1)

        bottom_row = QHBoxLayout()
        self._status = _ElidingLabel()
        self._status.setStyleSheet(STATUS_DIM)
        bottom_row.addWidget(self._status, 1)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Open).clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self._ok_btn = buttons.button(QDialogButtonBox.StandardButton.Open)
        self._ok_btn.setEnabled(False)
        buttons.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        bottom_row.addWidget(buttons)
        right_layout.addLayout(bottom_row)

        # outer splitter: left panel (full height) | right side
        left_panel.setMinimumWidth(160)
        left_panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        self._outer_splitter = QSplitter(Qt.Orientation.Horizontal)
        self._outer_splitter.addWidget(left_panel)
        self._outer_splitter.addWidget(right_widget)
        self._outer_splitter.setStretchFactor(0, 0)
        self._outer_splitter.setStretchFactor(1, 1)
        self._outer_splitter.setCollapsible(0, False)
        self._outer_splitter.setCollapsible(1, False)
        self._outer_splitter.setSizes([240, 820])
        layout.addWidget(self._outer_splitter, 1)

        # Debounced layout save (mirrors sequence browser).
        self._layout_save_timer = QTimer(self)
        self._layout_save_timer.setSingleShot(True)
        self._layout_save_timer.setInterval(400)
        self._layout_save_timer.timeout.connect(self._save_browser_layout)
        th = self._table.horizontalHeader()
        th.sectionResized.connect(lambda *_: self._schedule_layout_save())
        self._outer_splitter.splitterMoved.connect(lambda *_: self._schedule_layout_save())
        self._content_splitter.splitterMoved.connect(lambda *_: self._schedule_layout_save())

        self._tree.clicked.connect(self._on_tree_clicked)
        self._table.itemSelectionChanged.connect(self._on_table_selection)
        self._table.cellDoubleClicked.connect(lambda _r, _c: self.accept())
        self._table.customContextMenuRequested.connect(self._on_table_context_menu)
        self._grid.itemSelectionChanged.connect(self._on_grid_selection)
        self._grid.itemDoubleClicked.connect(self._on_grid_double_clicked)
        self._grid.customContextMenuRequested.connect(self._on_grid_context_menu)
        self._path_edit.returnPressed.connect(self._on_path_entered)
        self._path_edit.installEventFilter(self)
        self._table.installEventFilter(self)
        self._table.viewport().installEventFilter(self)
        self._grid.installEventFilter(self)
        self._grid.viewport().installEventFilter(self)
        self.installEventFilter(self)

        settings = browser_qsettings()
        keys = self._keys
        # Resolve start folder + optional file to select.
        start_folder = ""
        if start_dir:
            d = Path(start_dir)
            if d.is_file():
                self._auto_select_path = str(d)
                d = d.parent
            if d.is_dir():
                start_folder = str(d)

        saved_dir = str(settings.value(keys.last_dir, "") or "")
        self._same_path_session = bool(start_folder) and dirs_equal(start_folder, saved_dir)
        saved_view = coerce_view_mode(settings.value(keys.view, VIEW_LIST))
        self._pending_preview = saved_view == VIEW_PREVIEW
        last_browse = coerce_view_mode(
            settings.value(keys.last_browse, saved_view), allow_preview=False
        )
        if saved_view == VIEW_GRID:
            last_browse = VIEW_GRID
        elif saved_view == VIEW_LIST:
            last_browse = VIEW_LIST
        self._last_browse_mode = last_browse if last_browse in (VIEW_LIST, VIEW_GRID) else VIEW_LIST
        if self._last_browse_mode == VIEW_GRID:
            self._view_seg.setCurrentData(VIEW_GRID)
            self._view_stack.setCurrentIndex(1)
        else:
            self._view_seg.setCurrentData(VIEW_LIST)
            self._view_stack.setCurrentIndex(0)
        self._view_seg.currentIndexChanged.connect(self._on_view_changed)

        inspect_on = settings_bool(settings, keys.inspect, True)
        self._inspect_cb.blockSignals(True)
        self._inspect_cb.setChecked(inspect_on)
        self._inspect_cb.blockSignals(False)
        self._toggle_inspect(inspect_on)
        self._inspect_cb.toggled.connect(self._toggle_inspect)

        if self._same_path_session and not self._auto_select_path:
            self._auto_select_path = str(settings.value(keys.selected, "") or "")

        self._restore_browser_layout()

        if start_folder:
            self._navigate_to(start_folder, restore_tree=self._same_path_session)
        elif self._same_path_session and saved_dir and Path(saved_dir).is_dir():
            self._navigate_to(saved_dir, restore_tree=True)

        if self._pending_preview and self._file_data:
            self._view_seg.setCurrentData(VIEW_PREVIEW)
        self._pending_preview = False

    def selected_path(self) -> str:
        return self._selected_path

    def _schedule_layout_save(self) -> None:
        timer = getattr(self, "_layout_save_timer", None)
        if timer is not None:
            timer.start()

    def _restore_browser_layout(self) -> None:
        """Restore shared geometry + per-mode splitters / video table header."""
        settings = browser_qsettings()
        keys = self._keys
        geo = load_shared_geometry(settings)
        if geo is not None:
            try:
                self.restoreGeometry(geo)
            except Exception:
                pass

        outer = parse_int_list(settings.value(keys.outer_split))
        if outer is not None and len(outer) == 2 and all(s > 0 for s in outer):
            self._outer_splitter.setSizes(outer)

        content = parse_int_list(settings.value(keys.content_split))
        if content is not None and self._meta_panel.isVisible():
            if len(content) == 2 and content[0] > 0 and content[1] >= 0:
                self._content_splitter.setSizes(content)

        th = self._table.horizontalHeader()
        header_state = settings.value(keys.header_state)
        if header_state is not None:
            try:
                th.restoreState(header_state)
                th.setStretchLastSection(False)
                th.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
                for col in range(1, self._table.columnCount()):
                    th.setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
            except Exception:
                header_state = None
        if header_state is None:
            widths = parse_int_list(settings.value(keys.col_widths))
            if widths is not None:
                for i, w in enumerate(widths):
                    if i == 0:
                        continue
                    if i < self._table.columnCount() and w >= 48:
                        th.resizeSection(i, w)

    def _save_browser_layout(self) -> None:
        """Persist shared geometry + video-browser layout and session state."""
        settings = browser_qsettings()
        keys = self._keys
        save_shared_geometry(self.saveGeometry(), settings)
        settings.setValue(keys.outer_split, self._outer_splitter.sizes())
        if self._meta_panel.isVisible():
            settings.setValue(keys.content_split, self._content_splitter.sizes())
        th = self._table.horizontalHeader()
        settings.setValue(keys.header_state, th.saveState())
        widths = [th.sectionSize(i) for i in range(self._table.columnCount())]
        settings.setValue(keys.col_widths, widths)
        settings.setValue(keys.inspect, self._inspect_cb.isChecked())
        mode = self._view_seg.currentData() or VIEW_LIST
        settings.setValue(keys.view, str(mode))
        settings.setValue(
            keys.last_browse,
            str(self._last_browse_mode or VIEW_LIST),
        )
        directory = self._path_edit.text().strip()
        if directory:
            settings.setValue(keys.last_dir, normalize_dir(directory) or directory)
        if self._selected_path:
            settings.setValue(keys.selected, self._selected_path)
        settings.setValue(keys.tree_expanded, collect_expanded_dirs(self._tree, self._fs_model))
        settings.setValue(keys.tree_vscroll, tree_vscroll_value(self._tree))
        settings.sync()

    def _make_vid_placeholder_icon(self) -> QIcon:
        pm = QPixmap(_SEQ_THUMB_ICON)
        pm.fill(QColor(0x2A, 0x2A, 0x2A))
        return QIcon(pm)

    def _ensure_player(self):
        if self._player is not None:
            return self._player
        # Cache budget is Preferences-only (same as sequence browser / 0.7.0).
        self._player = SequencePlayer(
            settings=browser_qsettings(),
            show_cache_ui=False,
            prefer_gpu=True,
            parent=self._preview_page,
        )
        self._preview_host.addWidget(self._player, 1)
        return self._player

    def _on_view_changed(self, _index: int) -> None:
        mode = self._view_seg.currentData()
        settings = browser_qsettings()
        if mode == _VID_BROWSER_VIEW_PREVIEW:
            settings.setValue(self._keys.view, _VID_BROWSER_VIEW_PREVIEW)
            settings.setValue(self._keys.last_browse, str(self._last_browse_mode or VIEW_LIST))
            self._view_stack.setCurrentIndex(2)
            self._load_preview_video()
            return
        if self._previewing:
            self._stop_preview_playback()
        if mode == _VID_BROWSER_VIEW_GRID:
            self._last_browse_mode = _VID_BROWSER_VIEW_GRID
            self._view_stack.setCurrentIndex(1)
            settings.setValue(self._keys.view, _VID_BROWSER_VIEW_GRID)
            settings.setValue(self._keys.last_browse, _VID_BROWSER_VIEW_GRID)
            self._queue_video_thumbnails()
        else:
            self._last_browse_mode = _VID_BROWSER_VIEW_LIST
            self._view_stack.setCurrentIndex(0)
            settings.setValue(self._keys.view, _VID_BROWSER_VIEW_LIST)
            settings.setValue(self._keys.last_browse, _VID_BROWSER_VIEW_LIST)

    def _load_preview_video(self) -> None:
        self._previewing = True
        self._view_stack.setCurrentIndex(2)
        path = self._selected_path
        if not path and self._file_data:
            path = str(self._file_data[0].get("path") or "")
            if path:
                self._selected_path = path
                self._ok_btn.setEnabled(True)
                self._table.selectRow(0)
                if self._grid.count() > 0:
                    self._grid.setCurrentRow(0)
        if not path:
            self._status.setText("No video to preview in this folder")
            self._stop_preview_playback(clear_only=True)
            return

        ctx = self._preview_ctx

        try:
            player = self._ensure_player()
        except Exception as e:
            log.exception("Failed to create video player")
            self._status.setText(f"Preview unavailable: {e}")
            return

        try:
            ok = player.load_video(
                path,
                ocio_cfg=ctx.ocio_cfg,
                src_colorspace=ctx.src_colorspace or "",
            )
        except Exception as e:
            log.exception("Video preview load failed for %s", path)
            self._status.setText(f"Preview failed: {e}")
            return

        label = Path(path).name
        if ok:
            self._status.setText(f"Preview · {label}")
        else:
            self._status.setText(f"Could not open {label}")
        self.setWindowTitle(f"Browse Video Files — {label}")
        if self._meta_panel.isVisible():
            for row, f in enumerate(self._file_data):
                if f.get("path") == path:
                    self._show_metadata(row)
                    break
        player.setFocus(Qt.FocusReason.OtherFocusReason)
        QTimer.singleShot(0, player.fit_in_view)

    def _stop_preview_playback(self, *, clear_only: bool = False) -> None:
        self._previewing = False
        if self._player is not None:
            try:
                self._player.set_playing(False)
            except RuntimeError:
                pass
            try:
                self._player.shutdown_prefetch_only()
            except RuntimeError:
                pass
        if not clear_only:
            self.setWindowTitle("Browse Video Files")

    def _shutdown_player(self) -> None:
        self._previewing = False
        player = self._player
        self._player = None
        if player is not None:
            try:
                player.set_playing(False)
            except RuntimeError:
                pass
            try:
                player.shutdown()
            except RuntimeError:
                pass
            try:
                player.hide()
            except RuntimeError:
                pass

    def accept(self) -> None:
        mode = self._view_seg.currentData()
        if mode == _VID_BROWSER_VIEW_GRID:
            item = self._grid.currentItem()
            if item is not None:
                row = int(item.data(Qt.ItemDataRole.UserRole) or 0)
                if 0 <= row < len(self._file_data):
                    self._selected_path = str(self._file_data[row].get("path") or "")
        elif mode != _VID_BROWSER_VIEW_PREVIEW:
            rows = self._table.selectionModel().selectedRows()
            if rows:
                item = self._table.item(rows[0].row(), 0)
                if item is not None:
                    self._selected_path = item.data(Qt.ItemDataRole.UserRole) or ""
        if not self._selected_path:
            return
        self._shutdown_player()
        self._save_browser_layout()
        super().accept()

    def reject(self) -> None:
        # Always close the dialog. Escape already leaves Preview via eventFilter;
        # window chrome (X) and Cancel must not be trapped in Preview.
        self._shutdown_player()
        self._save_browser_layout()
        super().reject()

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        self._thumb_gen += 1
        self._shutdown_player()
        self._save_browser_layout()
        super().closeEvent(event)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if obj is self._path_edit and event.type() == QEvent.Type.KeyPress:
            if isinstance(event, QKeyEvent) and event.matches(QKeySequence.StandardKey.Paste):
                QTimer.singleShot(0, self._on_path_entered)
                return False
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()  # type: ignore[attr-defined]
            if obj is self._path_edit:
                return super().eventFilter(obj, event)
            if key == Qt.Key.Key_Space and not event.isAutoRepeat():  # type: ignore[attr-defined]
                if self._view_seg.currentData() == _VID_BROWSER_VIEW_PREVIEW:
                    # Let the player handle Space for play/pause when focused.
                    if self._player is not None and self._player.hasFocus():
                        return False
                    self._view_seg.setCurrentData(self._last_browse_mode)
                else:
                    self._view_seg.setCurrentData(_VID_BROWSER_VIEW_PREVIEW)
                return True
            if key == Qt.Key.Key_Escape:
                if self._view_seg.currentData() == _VID_BROWSER_VIEW_PREVIEW:
                    self._view_seg.setCurrentData(self._last_browse_mode)
                    return True
        return super().eventFilter(obj, event)

    def _navigate_to(self, directory: str, *, restore_tree: bool = False) -> None:
        expand_path_chain(self._tree, self._fs_model, directory)
        idx = self._fs_model.index(directory)
        if idx.isValid():
            self._tree.setCurrentIndex(idx)
            self._tree.scrollTo(idx, QAbstractItemView.ScrollHint.PositionAtCenter)
        if restore_tree:
            self._restore_tree_session(directory)
        self._path_edit.setText(directory)
        self._places.set_current_dir(directory)
        self._searchable_tree.set_search_root(directory)
        self._scan_directory(directory)

    def _restore_tree_session(self, directory: str) -> None:
        settings = browser_qsettings()
        keys = self._keys
        paths = parse_str_list(settings.value(keys.tree_expanded))
        restore_tree_expanded(self._tree, self._fs_model, paths, focus_path=directory)
        vscroll = settings.value(keys.tree_vscroll)
        try:
            scroll_val = int(vscroll) if vscroll is not None else -1
        except (TypeError, ValueError):
            scroll_val = -1
        if scroll_val >= 0:
            QTimer.singleShot(0, lambda v=scroll_val: set_tree_vscroll(self._tree, v))

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
        """Navigate to a pasted/typed folder or video file path."""
        raw = clean_path_string(self._path_edit.text())
        if not raw:
            return
        if raw != self._path_edit.text().strip():
            self._path_edit.setText(raw)
        self._navigate_to_path_string(raw)

    def _navigate_to_path_string(self, raw: str, *, prefer_preview: bool = True) -> None:
        resolved = resolve_video_browser_path(raw, _VIDEO_EXTS)
        if resolved is None:
            return
        directory, select_path = resolved
        self._auto_select_path = select_path
        if prefer_preview and select_path:
            self._pending_preview = True
            self._view_seg.blockSignals(True)
            self._view_seg.setCurrentData(_VID_BROWSER_VIEW_PREVIEW)
            self._view_seg.blockSignals(False)
        self._navigate_to(directory)

    def _scan_directory(self, directory: str) -> None:
        was_preview = self._view_seg.currentData() == _VID_BROWSER_VIEW_PREVIEW
        if was_preview:
            self._stop_preview_playback()
        self._thumb_gen += 1
        self._table.setRowCount(0)
        self._grid.clear()
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
            name_item.setToolTip(f["path"])

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

            gitem = QListWidgetItem(self._placeholder_icon, f["name"])
            gitem.setData(Qt.ItemDataRole.UserRole, row)
            gitem.setToolTip(f["path"])
            gitem.setSizeHint(QSize(_SEQ_THUMB_ICON.width() + 24, _SEQ_THUMB_ICON.height() + 48))
            self._grid.addItem(gitem)

        selected = False
        select_row = -1
        if self._auto_select_path:
            for row in range(self._table.rowCount()):
                item = self._table.item(row, 0)
                if item and item.data(Qt.ItemDataRole.UserRole) == self._auto_select_path:
                    select_row = row
                    selected = True
                    break
        if not selected and files:
            select_row = 0
        if select_row >= 0:
            self._table.selectRow(select_row)
            self._grid.setCurrentRow(select_row)
            self._table.scrollToItem(
                self._table.item(select_row, 0),
                QAbstractItemView.ScrollHint.PositionAtCenter,
            )
            if 0 <= select_row < self._grid.count():
                self._grid.scrollToItem(
                    self._grid.item(select_row),
                    QAbstractItemView.ScrollHint.PositionAtCenter,
                )

        self._status.setText(f"{len(files)} video file(s) found")
        mode = self._view_seg.currentData()
        if mode == _VID_BROWSER_VIEW_GRID:
            self._queue_video_thumbnails()
        elif was_preview or mode == _VID_BROWSER_VIEW_PREVIEW:
            self._load_preview_video()

    def _queue_video_thumbnails(self) -> None:
        if not self._file_data:
            return
        gen = self._thumb_gen
        for row, f in enumerate(self._file_data):
            path = str(f.get("path") or "")
            if not path:
                continue
            cached = self._thumb_cache.get(path)
            if cached is not None:
                self._set_vid_grid_icon(row, cached)
                continue
            self._thumb_pool.start(_ThumbJob(gen, row, path, self._thumb_signals))

    @Slot(int, int, object)
    def _on_vid_thumb_ready(self, gen: int, row: int, qimg: object) -> None:
        if gen != self._thumb_gen:
            return
        if row < 0 or row >= self._grid.count():
            return
        if qimg is None or not isinstance(qimg, QImage) or qimg.isNull():
            return
        pm = QPixmap.fromImage(qimg)
        path = ""
        if 0 <= row < len(self._file_data):
            path = str(self._file_data[row].get("path") or "")
        if path:
            self._thumb_cache[path] = pm
        self._set_vid_grid_icon(row, pm)

    def _set_vid_grid_icon(self, row: int, pm: QPixmap) -> None:
        if row < 0 or row >= self._grid.count():
            return
        item = self._grid.item(row)
        if item is None:
            return
        canvas = QPixmap(_SEQ_THUMB_ICON)
        canvas.fill(QColor(0x1E, 0x1E, 0x1E))
        scaled = pm.scaled(
            _SEQ_THUMB_ICON,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        x = (_SEQ_THUMB_ICON.width() - scaled.width()) // 2
        y = (_SEQ_THUMB_ICON.height() - scaled.height()) // 2
        painter = QPainter(canvas)
        painter.drawPixmap(x, y, scaled)
        painter.end()
        item.setIcon(QIcon(canvas))

    def _on_grid_selection(self) -> None:
        item = self._grid.currentItem()
        if item is None:
            return
        row = int(item.data(Qt.ItemDataRole.UserRole) or 0)
        if 0 <= row < len(self._file_data):
            self._selected_path = str(self._file_data[row].get("path") or "")
            self._ok_btn.setEnabled(bool(self._selected_path))
            self._table.selectRow(row)
            if self._meta_panel.isVisible():
                self._show_metadata(row)
            if self._view_seg.currentData() == _VID_BROWSER_VIEW_PREVIEW:
                self._load_preview_video()

    def _on_grid_double_clicked(self, item: QListWidgetItem) -> None:
        if item is None:
            return
        row = int(item.data(Qt.ItemDataRole.UserRole) or 0)
        if 0 <= row < len(self._file_data):
            self._selected_path = str(self._file_data[row].get("path") or "")
            if self._selected_path:
                self.accept()

    def _select_video_row(self, row: int) -> None:
        """Commit video index *row* into selection (table + grid stay in sync)."""
        if row < 0 or row >= len(self._file_data):
            self._selected_path = ""
            self._ok_btn.setEnabled(False)
            return
        self._selected_path = str(self._file_data[row].get("path") or "")
        self._ok_btn.setEnabled(bool(self._selected_path))
        self._table.selectRow(row)
        if 0 <= row < self._grid.count():
            self._grid.setCurrentRow(row)
        if self._meta_panel.isVisible():
            self._show_metadata(row)

    def _on_table_context_menu(self, pos: QPoint) -> None:
        index = self._table.indexAt(pos)
        if index.isValid():
            self._select_video_row(index.row())
        self._show_video_context_menu(self._table.viewport().mapToGlobal(pos))

    def _on_grid_context_menu(self, pos: QPoint) -> None:
        item = self._grid.itemAt(pos)
        if item is not None:
            row = int(item.data(Qt.ItemDataRole.UserRole) or self._grid.row(item))
            self._select_video_row(row)
        self._show_video_context_menu(self._grid.viewport().mapToGlobal(pos))

    def _show_video_context_menu(self, global_pos: QPoint) -> None:
        menu = QMenu(self)
        preview_act = QAction("Preview", self)
        preview_act.setShortcut(Qt.Key.Key_Space)
        preview_act.triggered.connect(
            lambda: self._view_seg.setCurrentData(_VID_BROWSER_VIEW_PREVIEW)
        )
        menu.addAction(preview_act)
        open_act = QAction("Open", self)
        open_act.setEnabled(self._ok_btn.isEnabled())
        open_act.triggered.connect(self.accept)
        menu.addAction(open_act)
        menu.addSeparator()
        _add_copy_path_actions(menu, file_path=self._selected_path)
        menu.exec(global_pos)

    def _on_table_selection(self) -> None:
        rows = self._table.selectionModel().selectedRows()
        if rows:
            row = rows[0].row()
            item = self._table.item(row, 0)
            self._selected_path = item.data(Qt.ItemDataRole.UserRole) if item else ""
            self._ok_btn.setEnabled(bool(self._selected_path))
            if 0 <= row < self._grid.count():
                self._grid.setCurrentRow(row)
            if self._meta_panel.isVisible():
                self._show_metadata(row)
            if self._view_seg.currentData() == _VID_BROWSER_VIEW_PREVIEW:
                self._load_preview_video()
        else:
            self._selected_path = ""
            self._ok_btn.setEnabled(False)

    def _toggle_inspect(self, checked: bool) -> None:
        sizes = self._content_splitter.sizes()
        self._meta_panel.setVisible(checked)
        if checked:
            total = sum(sizes) if sum(sizes) > 0 else max(self._content_splitter.width(), 640)
            meta_w = 260
            saved = parse_int_list(browser_qsettings().value(self._keys.content_split))
            if saved is not None and len(saved) == 2 and saved[1] >= 120:
                meta_w = saved[1]
            self._content_splitter.setSizes([max(200, total - meta_w), meta_w])
            rows = self._table.selectionModel().selectedRows()
            if rows:
                self._show_metadata(rows[0].row())
            elif self._selected_path:
                for row, f in enumerate(self._file_data):
                    if f.get("path") == self._selected_path:
                        self._show_metadata(row)
                        break
        else:
            total = sum(sizes) if sum(sizes) > 0 else max(self._content_splitter.width(), 640)
            self._content_splitter.setSizes([total, 0])
        self._schedule_layout_save()

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
