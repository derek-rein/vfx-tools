"""Aggressive forward-priority EXR prefetch for the slate preview.

Inspired by RV's "look-ahead" cache mode and mrv2 — when playing, frames
ahead of the playhead get scheduled first and the queue keeps growing
until the cache budget is exhausted.  When scrubbing, a small lookback
window is kept hot too so step-back is instant.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor

import numpy as np
from PySide6.QtCore import QObject, Qt, QTimer, Signal

from .cache_prefs import DEFAULT_LOOKBACK_RATIO
from .frame_cache import FrameCache

# Worker-thread transform applied to freshly-read EXR pixels before they hit
# the cache.  Returning ``None`` discards the frame.  Must be thread-safe;
# OCIO ``CPUProcessor.apply`` is.
FrameTransform = Callable[[np.ndarray], "np.ndarray | None"]

# Hard ceiling on look-ahead distance even when budget allows more.
# (Beyond a few seconds of lookahead, additional prefetch buys nothing
# but blocks I/O bandwidth that should serve scrubs.)
_HARD_LOOKAHEAD_FRAMES = 240
# Minimum lookahead even on tiny caches — guarantees smooth start of play.
_MIN_LOOKAHEAD_FRAMES = 8

DEFAULT_MAX_WORKERS = 4


def _read_exr_frame(path: str, transform: FrameTransform | None) -> np.ndarray | None:
    """Worker-thread pipeline: read **HDR float** RGB and run an optional transform.

    Uses :func:`read_exr` (float32, unclamped) — **not** uint16.  Requesting
    UINT16 from OIIO clamps values above 1.0, which permanently destroys
    highlight headroom so exposing *down* cannot recover detail (unlike Nuke).

    The transform typically applies OCIO ``src → working`` and returns float16
    working-space pixels (still HDR-capable) for the RAM cache.
    """
    import numpy as np

    from ..core.exr_io import read_exr

    try:
        rgb = read_exr(path)
    except Exception:
        return None
    if rgb is None:
        return None
    if transform is None:
        # Keep unclamped headroom even without OCIO (float16 can hold >1.0).
        return np.ascontiguousarray(rgb, dtype=np.float16)
    try:
        return transform(rgb)
    except Exception:
        return np.ascontiguousarray(rgb, dtype=np.float16)


class ExrPrefetchService(QObject):
    """Background EXR loader with a priority queue and parallel workers.

    Signals
    -------
    frame_loaded(int, object)
        ``(frame_number, float16_rgb | None)`` on the main thread
        (HDR working-space or source-linear pixels — not uint16).
    """

    frame_loaded = Signal(int, object)
    # Internal: hop a worker-thread completion onto the GUI thread.
    _delivery_ready = Signal(int, object, int)

    def __init__(
        self,
        exr_seq,
        cache: FrameCache,
        shot_frames: list[int],
        *,
        lookback_ratio: float = DEFAULT_LOOKBACK_RATIO,
        max_workers: int = DEFAULT_MAX_WORKERS,
        frame_transform: FrameTransform | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._seq = exr_seq
        self._cache = cache
        self._shot_frames = sorted(shot_frames)
        # O(1) frame -> index lookup for offset math (replaces list.index).
        self._frame_index = {f: i for i, f in enumerate(self._shot_frames)}
        self._frame_set = set(self._shot_frames)
        self._lookback_ratio = max(0.0, min(1.0, lookback_ratio))
        self._max_workers = max(1, max_workers)
        self._frame_transform: FrameTransform | None = frame_transform

        self._current = 0
        self._playing = False
        self._paused = False
        self._generation = 0

        self._queue: deque[int] = deque()
        self._queued: set[int] = set()
        self._inflight: dict[int, Future] = {}

        self._pool = ThreadPoolExecutor(
            max_workers=self._max_workers,
            thread_name_prefix="exr-prefetch",
        )

        self._kick_timer = QTimer(self)
        self._kick_timer.setSingleShot(True)
        self._kick_timer.setInterval(0)
        self._kick_timer.timeout.connect(self._fill_slots)

        # _on_done fires on a worker thread; bounce delivery onto the
        # GUI thread before touching QTimer / emitting public signals.
        self._delivery_ready.connect(self._deliver, Qt.ConnectionType.QueuedConnection)

    def shutdown(self) -> None:
        self._generation += 1
        self._queue.clear()
        self._queued.clear()
        for fut in self._inflight.values():
            fut.cancel()
        self._inflight.clear()
        self._pool.shutdown(wait=False, cancel_futures=True)

    def set_paused(self, paused: bool) -> None:
        self._paused = paused
        if not paused:
            self._schedule_kick()

    def set_context(
        self,
        current_frame: int,
        *,
        playing: bool = False,
    ) -> None:
        """Re-prioritise the prefetch window around *current_frame*."""
        self._current = current_frame
        self._playing = playing
        self._rebuild_queue()
        self._schedule_kick()

    def _cache_capacity(self) -> int:
        """How many frames we estimate the cache can hold under the current budget."""
        sample_bytes = self._cache.estimate_frame_bytes()
        budget_bytes = self._cache.budget_bytes
        if sample_bytes > 0 and budget_bytes > 0:
            return max(_MIN_LOOKAHEAD_FRAMES, budget_bytes // sample_bytes)
        # No sample yet — schedule a small probe window so we don't queue
        # 30+ frames into a 5-frame cache and trash the LRU.
        return _MIN_LOOKAHEAD_FRAMES

    def _lookback_ahead_counts(self) -> tuple[int, int]:
        """Return (lookback_frames, lookahead_frames) sized to the cache budget.

        Strategy:
          - **Playing** → forward-only.  Schedule as many frames ahead as
            the cache budget can hold (capped at a few seconds).
          - **Scrubbing** → forward-priority but keep a small lookback so
            step-back is instant.
          - **Empty cache** → conservative window (probe size) until we
            learn how big a frame is.
        """
        capacity = min(self._cache_capacity(), _HARD_LOOKAHEAD_FRAMES + 32)
        if self._playing:
            return 0, min(capacity, _HARD_LOOKAHEAD_FRAMES)

        lookback = min(int(capacity * self._lookback_ratio), max(0, capacity - 1))
        lookahead = max(1, capacity - lookback)
        return lookback, min(lookahead, _HARD_LOOKAHEAD_FRAMES)

    def request_immediate(self, frame: int) -> bool:
        """Bump *frame* to the front of the load queue (scrub / playback miss).

        Returns ``True`` if a load was queued / is already in flight, ``False``
        when *frame* is unknown or already cached.
        """
        if frame not in self._frame_set:
            return False
        if self._cache.contains(frame):
            return True
        if frame in self._inflight:
            return True
        if frame in self._queue:
            self._queue.remove(frame)
            self._queued.discard(frame)
        self._queue.appendleft(frame)
        self._queued.add(frame)
        self._schedule_kick()
        return True

    def _shot_index(self, frame: int) -> int | None:
        return self._frame_index.get(frame)

    def _frame_at_offset(self, start_frame: int, offset: int) -> int | None:
        idx = self._shot_index(start_frame)
        if idx is None:
            return None
        n = len(self._shot_frames)
        return self._shot_frames[(idx + offset) % n]

    def _rebuild_queue(self) -> None:
        """Build forward-priority queue, sized to the cache budget.

        Snapshots the cached-frame set once at the start so we do a single
        lock acquisition instead of one per candidate (queues can be
        hundreds of frames long under a generous RAM budget).
        """
        if self._paused:
            self._queue.clear()
            self._queued.clear()
            return

        anchor = self._current
        if anchor not in self._frame_set:
            anchor = self._shot_frames[0] if self._shot_frames else anchor

        lookback_n, lookahead_n = self._lookback_ahead_counts()
        cached_snapshot = self._cache.cached_frames()
        inflight_snapshot = set(self._inflight.keys())

        want: list[int] = []
        want_set: set[int] = set()

        def add(frame: int | None) -> None:
            if frame is None or frame in want_set:
                return
            if frame in cached_snapshot or frame in inflight_snapshot:
                return
            want.append(frame)
            want_set.add(frame)

        add(anchor)
        for offset in range(1, lookahead_n + 1):
            add(self._frame_at_offset(anchor, offset))
        for offset in range(1, lookback_n + 1):
            add(self._frame_at_offset(anchor, -offset))

        # Drop queue entries that aren't wanted any more.  We don't bother
        # preserving the previous order — the new ``want`` list already
        # encodes the desired priority (anchor → lookahead → lookback).
        self._queue = deque(want)
        self._queued = want_set

    def _schedule_kick(self) -> None:
        if not self._kick_timer.isActive():
            self._kick_timer.start()

    def _fill_slots(self) -> None:
        """Submit queued frames, throttled to the cache's true capacity.

        We never schedule more reads than will fit in the cache budget
        (cached + in-flight ≤ capacity).  Without this, an empty cache's
        conservative initial estimate would let us submit 30+ frames into
        a budget that fits 8, the LRU would evict the most-recent ahead
        frames first, and the user would see backwards frames cached when
        scrubbing.
        """
        if self._paused:
            return
        gen = self._generation
        capacity = self._cache_capacity()
        cached_count = len(self._cache.cached_frames())
        while len(self._inflight) < self._max_workers and self._queue:
            if gen != self._generation:
                return
            if cached_count + len(self._inflight) >= capacity:
                return
            frame = self._queue.popleft()
            self._queued.discard(frame)
            if self._cache.contains(frame) or frame in self._inflight:
                continue
            try:
                path = self._seq.frame(frame)
            except Exception:
                continue
            fut = self._pool.submit(_read_exr_frame, path, self._frame_transform)
            self._inflight[frame] = fut
            fut.add_done_callback(lambda f, fr=frame, g=gen: self._on_done(fr, f, g))

    def set_frame_transform(self, transform: FrameTransform | None) -> None:
        """Replace the worker-thread transform.

        Existing in-flight loads keep the old transform; subsequent reads
        use the new one.  Callers should typically clear the cache after
        changing transforms.
        """
        self._frame_transform = transform

    def _on_done(self, frame: int, fut: Future, generation: int) -> None:
        # Runs on a worker thread — must not touch QTimer / emit cross-thread
        # work synchronously.  Hop to the GUI thread via _delivery_ready (a
        # QueuedConnection-bound signal).
        if generation != self._generation:
            return
        try:
            rgb = fut.result()
        except Exception:
            rgb = None
        try:
            self._delivery_ready.emit(frame, rgb, generation)
        except RuntimeError:
            # Prefetch QObject already deleted (dialog closed mid-load).
            return

    def _deliver(self, frame: int, rgb: np.ndarray | None, generation: int) -> None:
        # GUI-thread slot.
        self._inflight.pop(frame, None)
        if generation != self._generation:
            self._schedule_kick()
            return
        had_sample_before = self._cache.estimate_frame_bytes() > 0
        if rgb is not None:
            self._cache.put(frame, rgb)
        self.frame_loaded.emit(frame, rgb)
        # First successful load reveals the per-frame byte size.  Re-prioritise
        # the queue against the now-accurate capacity so we keep filling
        # forward until the budget is full.
        if not had_sample_before and rgb is not None:
            self._rebuild_queue()
        self._schedule_kick()
