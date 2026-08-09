"""Thread-safe RAM cache for decoded EXR frames (typically float16 HDR RGB).

Adapted from Triton's :class:`FrameCache` pattern: LRU eviction under a byte
budget, Qt signal for timeline cache-bar updates.  Viewer prefetch stores
unclamped float16 working-space (or source HDR) so highlight headroom
survives expose-down — not uint16 (which clamps values above 1.0 at read).
"""

from __future__ import annotations

import threading
from collections import OrderedDict

import numpy as np
from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal

# Coalesce cache_changed bursts (e.g. 4 prefetch workers all completing in
# the same ms) into one update at most every N ms — keeps the timeline
# repaint + usage-bar updates from saturating the GUI thread.
_CACHE_CHANGED_COALESCE_MS = 33


class FrameCache(QObject):
    """LRU cache keyed by frame number → ``(H, W, 3)`` ndarray (RGB).

    The dtype is whatever the producer wrote — usually float16 HDR working
    space after OCIO ``src → working`` in the prefetch worker (or float16
    source HDR if no OCIO).  Legacy uint16 is still accepted but cannot
    recover values that were clipped above 1.0 at load.

    ``cache_changed`` is *coalesced* — multiple ``put`` / ``clear`` calls in
    quick succession fan in to a single emission ~33ms later, so listeners
    (timeline repaint, status-bar usage bar) don't drown the event loop.
    """

    cache_changed = Signal()
    # Internal: forwarded from worker threads to the GUI thread.
    _emit_request = Signal()

    def __init__(
        self,
        budget_bytes: int | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        if budget_bytes is None:
            from .app_settings import get_app_settings
            from .cache_prefs import cache_budget_bytes

            budget_bytes = cache_budget_bytes(get_app_settings().qsettings)
        self._budget = budget_bytes
        self._lock = threading.Lock()
        self._store: OrderedDict[int, np.ndarray] = OrderedDict()
        self._current_bytes = 0
        self._batch_mode = False

        self._pending_emit = False
        self._coalesce_timer = QTimer(self)
        self._coalesce_timer.setSingleShot(True)
        self._coalesce_timer.setInterval(_CACHE_CHANGED_COALESCE_MS)
        self._coalesce_timer.timeout.connect(self._flush_pending_emit)
        # Allow worker threads to safely request an emit by hopping onto the
        # GUI thread via QueuedConnection.
        self._emit_request.connect(self._schedule_emit, Qt.ConnectionType.QueuedConnection)

    @property
    def budget_bytes(self) -> int:
        return self._budget

    @budget_bytes.setter
    def budget_bytes(self, value: int) -> None:
        with self._lock:
            self._budget = max(0, value)
            self._evict()
        self._notify_changed()

    @property
    def current_bytes(self) -> int:
        with self._lock:
            return self._current_bytes

    def set_batch_mode(self, enabled: bool) -> None:
        was_batch = self._batch_mode
        self._batch_mode = enabled
        if was_batch and not enabled:
            self._notify_changed()

    def estimate_frame_bytes(self) -> int:
        """Average bytes-per-frame in the cache; ``0`` while empty."""
        with self._lock:
            n = len(self._store)
            if n == 0:
                return 0
            return self._current_bytes // n

    def put(self, frame: int, pixels: np.ndarray) -> None:
        """Insert *pixels* (any dtype, RGB) as a contiguous ndarray."""
        if pixels.ndim != 3 or pixels.shape[2] < 3:
            return
        if pixels.shape[2] > 3:
            pixels = pixels[..., :3]
        pixels = np.ascontiguousarray(pixels)
        nbytes = pixels.nbytes
        with self._lock:
            if frame in self._store:
                old = self._store.pop(frame)
                self._current_bytes -= old.nbytes
            self._store[frame] = pixels
            self._current_bytes += nbytes
            self._evict()
        if not self._batch_mode:
            self._notify_changed()

    def get(self, frame: int) -> np.ndarray | None:
        with self._lock:
            if frame not in self._store:
                return None
            self._store.move_to_end(frame)
            return self._store[frame]

    def contains(self, frame: int) -> bool:
        with self._lock:
            return frame in self._store

    def cached_frames(self) -> set[int]:
        with self._lock:
            return set(self._store.keys())

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
            self._current_bytes = 0
        self._notify_changed()

    def _evict(self) -> None:
        while self._current_bytes > self._budget and self._store:
            _frame, arr = self._store.popitem(last=False)
            self._current_bytes -= arr.nbytes

    # -- Coalesced change notifications --

    def _notify_changed(self) -> None:
        """Schedule a coalesced ``cache_changed`` emit (thread-safe)."""
        if QThread.currentThread() is self.thread():
            # Same-thread fast path: schedule directly.
            self._schedule_emit()
        else:
            # Worker-thread path: hop onto the GUI thread (QTimer is not
            # safe to arm from a non-owning thread).
            self._emit_request.emit()

    def _schedule_emit(self) -> None:
        """GUI-thread-only helper: arm the coalesce timer if not already."""
        self._pending_emit = True
        if not self._coalesce_timer.isActive():
            self._coalesce_timer.start()

    def _flush_pending_emit(self) -> None:
        if self._pending_emit:
            self._pending_emit = False
            self.cache_changed.emit()
