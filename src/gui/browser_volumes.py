"""Mounted volumes for file browsers (cross-platform).

``QFileSystemModel`` with the usual ``Dirs | NoDotAndDotDot`` filter does **not**
show macOS ``/Volumes`` (it is treated as a hidden directory), so external
drives never appear under ``/``. On Windows, ``QDir.rootPath()`` is typically
only the system drive (``C:/``), so ``D:`` / USB sticks are also invisible.

This module:

* lists **user-facing volumes** via :class:`~PySide6.QtCore.QStorageInfo`
* provides :class:`MultiRootDirModel` — each volume is a **top-level** tree row,
  with children from a shared :class:`~PySide6.QtWidgets.QFileSystemModel`
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import (
    QAbstractItemModel,
    QDir,
    QMimeData,
    QModelIndex,
    QObject,
    QStorageInfo,
    Qt,
    QUrl,
    Signal,
)
from PySide6.QtWidgets import QFileSystemModel

# Pseudo / system filesystems that are not useful as browser roots.
_SKIP_FS_TYPES = frozenset(
    {
        "autofs",
        "devfs",
        "devtmpfs",
        "proc",
        "sysfs",
        "tmpfs",
        "cgroup",
        "cgroup2",
        "squashfs",
        "overlay",
        "rpc_pipefs",
        "fusectl",
        "debugfs",
        "tracefs",
        "configfs",
        "pstore",
        "securityfs",
        "mqueue",
        "hugetlbfs",
        "binfmt_misc",
        "bpf",
        "nsfs",
        "ramfs",
        "rootfs",
    }
)

# Path prefixes that are never useful browser roots (platform-specific).
_SKIP_PATH_PREFIXES_UNIX = (
    "/System/Volumes",  # macOS APFS helpers (Data, Preboot, VM, …)
    "/private/var/vm",
    "/dev",
    "/proc",
    "/sys",
    "/run/user",  # often per-session bus mounts; real media is under /run/media
)


@dataclass(frozen=True, slots=True)
class VolumeInfo:
    """One user-facing mount the browser should treat as a top-level root."""

    path: str
    """Absolute mount path (``/``, ``/Volumes/KINGSTON``, ``C:/``, …)."""

    name: str
    """Label for the tree / places sidebar (volume name or drive letter)."""

    is_system_root: bool = False
    """True for the OS boot volume (``/`` or ``C:/``)."""


def _norm_mount_path(path: str) -> str:
    """Normalize a mount path for comparison (stable separators, no trailing slash)."""
    raw = (path or "").strip()
    if not raw:
        return ""
    # QStorageInfo / QDir use forward slashes on Windows; Path handles either.
    p = Path(raw)
    try:
        # Prefer absolute form without resolving symlinks (keeps /Volumes/Name).
        s = os.path.abspath(str(p))
    except (OSError, RuntimeError, ValueError):
        s = str(p)
    if sys.platform == "win32":
        s = s.replace("/", "\\")
        # Drive roots: keep trailing backslash form as ``C:\`` style → strip to ``C:``
        if len(s) == 3 and s[1] == ":" and s[2] in "\\/":
            return s[:2] + "\\"
        if len(s) == 2 and s[1] == ":":
            return s + "\\"
        return s.rstrip("\\") or s
    # Unix: keep ``/`` as-is; strip trailing slashes from others.
    if s != "/":
        s = s.rstrip("/")
    return s or "/"


def _fs_type_str(info: QStorageInfo) -> str:
    raw = info.fileSystemType()
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw).decode("utf-8", "replace").lower()
    return str(raw or "").lower()


def _should_skip_mount(info: QStorageInfo) -> bool:
    if not info.isValid() or not info.isReady():
        return True
    path = _norm_mount_path(info.rootPath())
    if not path:
        return True

    fs = _fs_type_str(info)
    if fs in _SKIP_FS_TYPES:
        return True

    if sys.platform != "win32":
        for prefix in _SKIP_PATH_PREFIXES_UNIX:
            if path == prefix or path.startswith(prefix + "/"):
                return True
        # Linux session garbage under /run except conventional media mounts.
        if path.startswith("/run/") and not path.startswith("/run/media"):
            return True

    # Zero-size pseudo mounts (skip unless it is the system root).
    if not info.isRoot() and info.bytesTotal() <= 0:
        return True

    return False


def _display_name_for(info: QStorageInfo, path: str) -> str:
    name = (info.displayName() or info.name() or "").strip()
    if name:
        return name
    if sys.platform == "win32":
        # ``C:\`` → ``C:``
        drive = path.rstrip("\\/")
        return drive or path
    base = Path(path).name
    return base or path


def list_browser_volumes() -> list[VolumeInfo]:
    """Return mounted volumes suitable as browser roots (deduped, stable order).

    Order: system root first, then other volumes by display name (case-insensitive).
    """
    found: list[VolumeInfo] = []
    seen_paths: set[str] = set()
    # Device identity for dedupe (``/`` vs ``/Volumes/Macintosh HD``).
    seen_devices: set[str] = set()

    for info in QStorageInfo.mountedVolumes():
        if _should_skip_mount(info):
            continue
        path = _norm_mount_path(info.rootPath())
        if path in seen_paths:
            continue

        device_raw = info.device()
        if isinstance(device_raw, (bytes, bytearray)):
            device = bytes(device_raw).decode("utf-8", "replace")
        else:
            device = str(device_raw or "")

        # Prefer the true system root over firmlink duplicates (macOS).
        if device and device in seen_devices and not info.isRoot():
            continue

        name = _display_name_for(info, path)
        found.append(
            VolumeInfo(
                path=path,
                name=name,
                is_system_root=bool(info.isRoot()),
            )
        )
        seen_paths.add(path)
        if device:
            seen_devices.add(device)

    # Windows fallback: ensure every drive letter from QDir.drives() is present
    # even if QStorageInfo briefly omits a ready stick.
    if sys.platform == "win32":
        for drive in QDir.drives():
            path = _norm_mount_path(drive.absolutePath())
            if not path or path in seen_paths:
                continue
            # Only add if the path exists / is a directory.
            try:
                if not Path(path).exists():
                    continue
            except OSError:
                continue
            found.append(
                VolumeInfo(
                    path=path,
                    name=path.rstrip("\\/") or path,
                    is_system_root=False,
                )
            )
            seen_paths.add(path)

    if not found:
        # Absolute fallback so the tree is never empty.
        root = _norm_mount_path(QDir.rootPath() or ("C:\\" if sys.platform == "win32" else "/"))
        found.append(VolumeInfo(path=root, name=root, is_system_root=True))

    roots = [v for v in found if v.is_system_root]
    others = sorted(
        [v for v in found if not v.is_system_root],
        key=lambda v: (v.name.lower(), v.path.lower()),
    )
    # At most one system root (first).
    ordered = (roots[:1] if roots else []) + others
    if not roots and others:
        return others
    return ordered


class MultiRootDirModel(QAbstractItemModel):
    """Directory tree with each :func:`list_browser_volumes` entry as a top-level row.

    Public API mirrors the subset of :class:`QFileSystemModel` used by the
    browsers and :mod:`browser_state` helpers: ``index(path)``, ``filePath``,
    ``isDir``, ``rowCount``, ``fileName``.
    """

    volumes_changed = Signal()

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._fs = QFileSystemModel(self)
        self._fs.setFilter(QDir.Filter.Dirs | QDir.Filter.NoDotAndDotDot)
        # Watch the whole machine so every volume path can resolve.
        # Windows: empty root → "My Computer" (all drives). Else filesystem root.
        if sys.platform == "win32":
            self._fs.setRootPath("")
        else:
            self._fs.setRootPath(QDir.rootPath() or "/")

        self._volumes: list[VolumeInfo] = []
        self._path_to_id: dict[str, int] = {}
        self._id_to_path: list[str] = []

        self._fs.directoryLoaded.connect(self._on_directory_loaded)
        self.refresh_volumes()

    # -- volume list ----------------------------------------------------------

    def volumes(self) -> list[VolumeInfo]:
        return list(self._volumes)

    def refresh_volumes(self) -> bool:
        """Rescan mounts. Returns True when the top-level list changed."""
        new = list_browser_volumes()
        if new == self._volumes:
            # Still touch each root so QFileSystemModel keeps them warm.
            for v in new:
                self._fs.index(v.path)
            return False
        self.beginResetModel()
        self._volumes = new
        self._path_to_id.clear()
        self._id_to_path.clear()
        for v in self._volumes:
            self._id_for(v.path)
            self._fs.index(v.path)
        self.endResetModel()
        self.volumes_changed.emit()
        return True

    # -- path id map ----------------------------------------------------------

    def _id_for(self, path: str) -> int:
        key = _norm_mount_path(path)
        if key not in self._path_to_id:
            self._path_to_id[key] = len(self._id_to_path)
            self._id_to_path.append(key)
        return self._path_to_id[key]

    def _path_of(self, index: QModelIndex) -> str:
        if not index.isValid():
            return ""
        iid = index.internalId()
        if 0 <= iid < len(self._id_to_path):
            return self._id_to_path[iid]
        return ""

    def _volume_containing(self, path: str) -> VolumeInfo | None:
        norm = _norm_mount_path(path)
        if not norm:
            return None
        best: VolumeInfo | None = None
        best_len = -1
        for v in self._volumes:
            vp = _norm_mount_path(v.path)
            if not vp:
                continue
            if norm == vp:
                return v
            # Prefix match with boundary (avoid C:\ matching C:\foo vs C:\foobar — path sep).
            if sys.platform == "win32":
                prefix = vp if vp.endswith("\\") else vp + "\\"
                if norm.lower().startswith(prefix.lower()) or norm.lower() == vp.lower():
                    if len(vp) > best_len:
                        best = v
                        best_len = len(vp)
            else:
                if norm == vp or norm.startswith(vp.rstrip("/") + "/"):
                    if len(vp) > best_len:
                        best = v
                        best_len = len(vp)
        return best

    # -- QFileSystemModel-compatible helpers ----------------------------------

    def filePath(self, index: QModelIndex) -> str:  # noqa: N802 — Qt API
        return self._path_of(index)

    def fileName(self, index: QModelIndex) -> str:  # noqa: N802 — Qt API
        if not index.isValid():
            return ""
        path = self._path_of(index)
        for v in self._volumes:
            if _norm_mount_path(v.path) == _norm_mount_path(path):
                return v.name
        return Path(path).name or path

    def isDir(self, index: QModelIndex) -> bool:  # noqa: N802 — Qt API
        if not index.isValid():
            return False
        # All nodes in this model are directories (filter is dirs-only).
        return True

    def index(self, *args):  # type: ignore[override]
        """``index(row, column, parent=…)`` or ``index(path: str)``."""
        if len(args) == 1 and isinstance(args[0], str):
            return self._index_for_path(args[0])
        row = int(args[0]) if args else 0
        column = int(args[1]) if len(args) > 1 else 0
        parent = args[2] if len(args) > 2 else QModelIndex()
        return self._index_row_col(row, column, parent)

    def _index_row_col(self, row: int, column: int, parent: QModelIndex) -> QModelIndex:
        if row < 0 or column != 0:
            return QModelIndex()
        if not parent.isValid():
            if row >= len(self._volumes):
                return QModelIndex()
            return self.createIndex(row, column, self._id_for(self._volumes[row].path))

        parent_path = self._path_of(parent)
        if not parent_path:
            return QModelIndex()
        fs_parent = self._fs.index(parent_path)
        if not fs_parent.isValid():
            return QModelIndex()
        fs_child = self._fs.index(row, 0, fs_parent)
        if not fs_child.isValid():
            return QModelIndex()
        child_path = self._fs.filePath(fs_child)
        if not child_path:
            return QModelIndex()
        return self.createIndex(row, column, self._id_for(child_path))

    def _index_for_path(self, path: str) -> QModelIndex:
        if not path:
            return QModelIndex()
        norm = _norm_mount_path(path)
        vol = self._volume_containing(norm)
        if vol is None:
            # Path may exist but volume list is stale — try raw fs index under root.
            fs_idx = self._fs.index(path)
            if not fs_idx.isValid():
                try:
                    fs_idx = self._fs.index(str(Path(path)))
                except (OSError, RuntimeError, ValueError):
                    return QModelIndex()
            if not fs_idx.isValid():
                return QModelIndex()
            # If it is a volume root we know, use top-level row.
            for i, v in enumerate(self._volumes):
                if _norm_mount_path(v.path) == norm:
                    return self.createIndex(i, 0, self._id_for(v.path))
            # Build via parent chain under whatever volume matches after refresh.
            return QModelIndex()

        vol_path = _norm_mount_path(vol.path)
        if norm == vol_path or (sys.platform == "win32" and norm.lower() == vol_path.lower()):
            try:
                row = next(
                    i for i, v in enumerate(self._volumes) if _norm_mount_path(v.path) == vol_path
                )
            except StopIteration:
                return QModelIndex()
            return self.createIndex(row, 0, self._id_for(vol_path))

        # Child of a volume: need correct *row among siblings* for createIndex.
        fs_idx = self._fs.index(norm)
        if not fs_idx.isValid():
            fs_idx = self._fs.index(path)
        if not fs_idx.isValid():
            return QModelIndex()
        return self.createIndex(fs_idx.row(), 0, self._id_for(self._fs.filePath(fs_idx)))

    # -- QAbstractItemModel ---------------------------------------------------

    def columnCount(self, parent: QModelIndex | None = None) -> int:  # noqa: ARG002
        return 1

    def rowCount(self, parent: QModelIndex | None = None) -> int:
        if parent is None:
            parent = QModelIndex()
        if not parent.isValid():
            return len(self._volumes)
        path = self._path_of(parent)
        if not path:
            return 0
        fs_idx = self._fs.index(path)
        if not fs_idx.isValid():
            return 0
        return self._fs.rowCount(fs_idx)

    def parent(self, index: QModelIndex) -> QModelIndex:  # type: ignore[override]
        if not index.isValid():
            return QModelIndex()
        path = self._path_of(index)
        if not path:
            return QModelIndex()
        # Volume roots have no parent.
        for v in self._volumes:
            if _norm_mount_path(v.path) == _norm_mount_path(path):
                return QModelIndex()
            if (
                sys.platform == "win32"
                and _norm_mount_path(v.path).lower() == _norm_mount_path(path).lower()
            ):
                return QModelIndex()

        parent_path = str(Path(path).parent)
        # Windows drive root's parent is the volume itself (already handled).
        if sys.platform == "win32":
            if len(path) <= 3 and path[1:2] == ":":
                return QModelIndex()
        else:
            if path == "/":
                return QModelIndex()

        vol = self._volume_containing(path)
        if vol is not None and _norm_mount_path(parent_path) == _norm_mount_path(vol.path):
            # Parent is the volume top-level row.
            try:
                row = next(
                    i
                    for i, v in enumerate(self._volumes)
                    if _norm_mount_path(v.path) == _norm_mount_path(vol.path)
                )
            except StopIteration:
                return QModelIndex()
            return self.createIndex(row, 0, self._id_for(vol.path))

        # Deeper parent: row among its siblings via QFileSystemModel.
        fs_parent = self._fs.index(parent_path)
        if not fs_parent.isValid():
            return self._index_for_path(parent_path)
        return self.createIndex(fs_parent.row(), 0, self._id_for(parent_path))

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        path = self._path_of(index)
        if not path:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return self.fileName(index)
        if role == Qt.ItemDataRole.ToolTipRole:
            return path
        if role in (
            Qt.ItemDataRole.DecorationRole,
            Qt.ItemDataRole.FontRole,
            Qt.ItemDataRole.ForegroundRole,
            Qt.ItemDataRole.BackgroundRole,
            Qt.ItemDataRole.SizeHintRole,
        ):
            fs_idx = self._fs.index(path)
            if fs_idx.isValid():
                return self._fs.data(fs_idx, role)
        return None

    def flags(self, index: QModelIndex):
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return (
            Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDragEnabled
        )

    def mimeTypes(self) -> list[str]:
        return ["text/uri-list"]

    def mimeData(self, indexes: list[QModelIndex]) -> QMimeData:  # type: ignore[override]
        """Expose folder paths as ``file://`` URLs for drag → Favorites."""
        md = QMimeData()
        urls: list[QUrl] = []
        seen: set[str] = set()
        for idx in indexes:
            if not idx.isValid() or idx.column() != 0:
                continue
            path = self._path_of(idx)
            if not path or path in seen:
                continue
            seen.add(path)
            urls.append(QUrl.fromLocalFile(path))
        if urls:
            md.setUrls(urls)
        return md

    def hasChildren(self, parent: QModelIndex | None = None) -> bool:
        if parent is None:
            parent = QModelIndex()
        if not parent.isValid():
            return bool(self._volumes)
        path = self._path_of(parent)
        if not path:
            return False
        fs_idx = self._fs.index(path)
        if not fs_idx.isValid():
            return True  # assume expandable until loaded
        return self._fs.hasChildren(fs_idx) or self._fs.canFetchMore(fs_idx)

    def canFetchMore(self, parent: QModelIndex) -> bool:
        if not parent.isValid():
            return False
        path = self._path_of(parent)
        fs_idx = self._fs.index(path) if path else QModelIndex()
        return bool(fs_idx.isValid() and self._fs.canFetchMore(fs_idx))

    def fetchMore(self, parent: QModelIndex) -> None:
        if not parent.isValid():
            return
        path = self._path_of(parent)
        fs_idx = self._fs.index(path) if path else QModelIndex()
        if fs_idx.isValid() and self._fs.canFetchMore(fs_idx):
            self._fs.fetchMore(fs_idx)

    def _on_directory_loaded(self, path: str) -> None:
        """Propagate QFileSystemModel loads into our indexes."""
        if not path:
            return
        # Prefer a targeted dataChanged when we can resolve the index; fall
        # back to layoutChanged so the tree refetches row counts.
        idx = self._index_for_path(path)
        if not idx.isValid():
            # Volume may still be loading under a path we track as a root.
            for i, v in enumerate(self._volumes):
                if _norm_mount_path(v.path) == _norm_mount_path(path):
                    idx = self.createIndex(i, 0, self._id_for(v.path))
                    break
        self.layoutChanged.emit()
        if idx.isValid():
            self.dataChanged.emit(idx, idx)
