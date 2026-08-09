"""Video frame prefetch for the sequence player (PyAV decode → FrameCache).

Single-threaded decoder: PyAV containers are not safe for parallel consumers.
When playing forward, frames are decoded sequentially without re-seeking.
Scrubs / jumps seek by timestamp then decode to the target index.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor

import numpy as np
from PySide6.QtCore import QObject, Qt, QTimer, Signal

from .cache_prefs import DEFAULT_LOOKBACK_RATIO
from .frame_cache import FrameCache

log = logging.getLogger(__name__)

FrameTransform = Callable[[np.ndarray], "np.ndarray | None"]

_HARD_LOOKAHEAD_FRAMES = 120
_MIN_LOOKAHEAD_FRAMES = 8
# Re-seek when the requested index is more than this far behind the decoder tip.
_SEEK_BACK_THRESHOLD = 2


class _VideoDecoder:
    """Stateful sequential PyAV decoder with best-effort seek."""

    def __init__(self, path: str) -> None:
        import av

        self.path = path
        self._av = av
        self.container = av.open(path)
        if not self.container.streams.video:
            self.container.close()
            raise RuntimeError(f"No video stream in {path}")
        self.stream = self.container.streams.video[0]
        try:
            self.fps = float(self.stream.average_rate) if self.stream.average_rate else 24.0
        except Exception:
            self.fps = 24.0
        if self.fps <= 0:
            self.fps = 24.0
        # Next 1-based frame index the sequential decoder will produce.
        self._next_idx = 1
        self._last_rgb: np.ndarray | None = None
        self._last_idx: int | None = None
        self._post_seek = False

    def close(self) -> None:
        try:
            self.container.close()
        except Exception:
            pass

    def _frame_to_rgb_f32(self, frame) -> np.ndarray:
        # 16-bit RGB keeps headroom for OCIO; match convert path.
        try:
            arr = frame.to_ndarray(format="rgb48le")
            return np.ascontiguousarray(arr.astype(np.float32) * (1.0 / 65535.0))
        except Exception:
            arr = frame.to_ndarray(format="rgb24")
            return np.ascontiguousarray(arr.astype(np.float32) * (1.0 / 255.0))

    def _seek_to_index(self, idx_1based: int) -> None:
        """Best-effort seek so the next decoded frame is near *idx_1based*."""
        target = max(1, int(idx_1based))
        # Seek slightly early so we can decode forward to the exact frame.
        seek_idx = max(0, target - 1)
        try:
            # Prefer stream time_base pts when available.
            tb = self.stream.time_base
            rate = self.stream.average_rate
            if tb is not None and rate is not None and float(rate) > 0:
                sec = seek_idx / float(rate)
                pts = int(sec / float(tb))
                self.container.seek(pts, stream=self.stream, any_frame=False, backward=True)
            else:
                # Fallback: global timestamp in AV_TIME_BASE units.
                import av

                self.container.seek(
                    int(seek_idx / self.fps * av.time_base),
                    any_frame=False,
                    backward=True,
                )
        except Exception:
            log.debug("Video seek failed for frame %s; reopening", target, exc_info=True)
            try:
                self.container.close()
            except Exception:
                pass
            self.container = self._av.open(self.path)
            self.stream = self.container.streams.video[0]
        self._next_idx = 1
        self._last_rgb = None
        self._last_idx = None
        # After seek, we don't know the exact index until we count from start
        # or use packet pts. For MVP: if we seeked to near start of file for
        # frame 1, sequential count works; for mid-seek, approximate by
        # decoding one frame and assigning target.
        if target <= 1:
            self._next_idx = 1
        else:
            # Mark that the next decoded frame should be treated as *target*
            # after draining the first post-seek frame (often a keyframe early).
            self._next_idx = target
            self._post_seek = True
            return
        self._post_seek = False

    def get_frame(self, idx_1based: int) -> np.ndarray | None:
        """Return float32 RGB for 1-based frame index, or None on failure."""
        idx = max(1, int(idx_1based))
        if self._last_idx == idx and self._last_rgb is not None:
            return self._last_rgb

        # Sequential hit: keep decoding forward.
        need_seek = False
        if self._last_idx is None:
            need_seek = idx > 1
        elif idx < self._last_idx - 0:  # going backward
            need_seek = True
        elif idx > self._last_idx + 30:
            # Big forward jump — seek rather than decode 30+ frames.
            need_seek = True
        elif idx < self._next_idx - _SEEK_BACK_THRESHOLD:
            need_seek = True

        if need_seek:
            self._seek_to_index(idx)

        # Decode until we reach the requested index.
        guard = 0
        max_guard = max(64, idx - (self._last_idx or 0) + 8)
        while guard < max_guard:
            guard += 1
            try:
                for frame in self.container.decode(self.stream):
                    rgb = self._frame_to_rgb_f32(frame)
                    if getattr(self, "_post_seek", False):
                        # First frame after mid-file seek ≈ requested index.
                        self._post_seek = False
                        self._last_idx = idx
                        self._next_idx = idx + 1
                        self._last_rgb = rgb
                        if self._last_idx == idx:
                            return rgb
                        continue
                    cur = self._next_idx
                    self._next_idx = cur + 1
                    self._last_idx = cur
                    self._last_rgb = rgb
                    if cur == idx:
                        return rgb
                    if cur > idx:
                        # Overshot (shouldn't for sequential); return what we have.
                        return rgb
                # EOF
                break
            except Exception:
                log.debug("Video decode error at frame %s", idx, exc_info=True)
                break
        return self._last_rgb if self._last_idx == idx else None


def _decode_one(
    path: str,
    frame: int,
    transform: FrameTransform | None,
    decoder_holder: dict,
    lock: threading.Lock,
) -> np.ndarray | None:
    """Worker entry: decode one frame via a shared locked decoder."""
    with lock:
        dec: _VideoDecoder | None = decoder_holder.get("dec")
        if dec is None or dec.path != path:
            if dec is not None:
                dec.close()
            try:
                dec = _VideoDecoder(path)
            except Exception:
                log.exception("Failed to open video for prefetch: %s", path)
                decoder_holder["dec"] = None
                return None
            decoder_holder["dec"] = dec
        try:
            rgb = dec.get_frame(frame)
        except Exception:
            log.debug("get_frame failed frame=%s", frame, exc_info=True)
            return None
    if rgb is None:
        return None
    if transform is None:
        return np.ascontiguousarray(rgb, dtype=np.float16)
    try:
        return transform(rgb)
    except Exception:
        return np.ascontiguousarray(rgb, dtype=np.float16)


class VideoPrefetchService(QObject):
    """Priority video decoder feeding :class:`FrameCache` (1 worker)."""

    frame_loaded = Signal(int, object)
    _delivery_ready = Signal(int, object, int)

    def __init__(
        self,
        path: str,
        cache: FrameCache,
        shot_frames: list[int],
        *,
        lookback_ratio: float = DEFAULT_LOOKBACK_RATIO,
        frame_transform: FrameTransform | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._path = path
        self._cache = cache
        self._shot_frames = sorted(shot_frames)
        self._frame_index = {f: i for i, f in enumerate(self._shot_frames)}
        self._frame_set = set(self._shot_frames)
        self._lookback_ratio = max(0.0, min(1.0, lookback_ratio))
        self._frame_transform = frame_transform

        self._current = self._shot_frames[0] if self._shot_frames else 1
        self._playing = False
        self._paused = False
        self._generation = 0

        self._queue: deque[int] = deque()
        self._queued: set[int] = set()
        self._inflight: dict[int, Future] = {}

        self._decoder_lock = threading.Lock()
        self._decoder_holder: dict = {"dec": None}

        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="vid-prefetch")

        self._kick_timer = QTimer(self)
        self._kick_timer.setSingleShot(True)
        self._kick_timer.setInterval(0)
        self._kick_timer.timeout.connect(self._fill_slots)
        self._delivery_ready.connect(self._deliver, Qt.ConnectionType.QueuedConnection)

    def shutdown(self) -> None:
        self._generation += 1
        self._queue.clear()
        self._queued.clear()
        for fut in self._inflight.values():
            fut.cancel()
        self._inflight.clear()
        self._pool.shutdown(wait=False, cancel_futures=True)
        with self._decoder_lock:
            dec = self._decoder_holder.get("dec")
            if dec is not None:
                dec.close()
            self._decoder_holder["dec"] = None

    def set_paused(self, paused: bool) -> None:
        self._paused = paused
        if not paused:
            self._schedule_kick()

    def set_context(self, current_frame: int, *, playing: bool = False) -> None:
        self._current = current_frame
        self._playing = playing
        self._rebuild_queue()
        self._schedule_kick()

    def request_immediate(self, frame: int) -> bool:
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

    def set_frame_transform(self, transform: FrameTransform | None) -> None:
        self._frame_transform = transform

    def _cache_capacity(self) -> int:
        sample_bytes = self._cache.estimate_frame_bytes()
        budget_bytes = self._cache.budget_bytes
        if sample_bytes > 0 and budget_bytes > 0:
            return max(_MIN_LOOKAHEAD_FRAMES, budget_bytes // sample_bytes)
        return _MIN_LOOKAHEAD_FRAMES

    def _lookback_ahead_counts(self) -> tuple[int, int]:
        capacity = min(self._cache_capacity(), _HARD_LOOKAHEAD_FRAMES + 32)
        if self._playing:
            return 0, min(capacity, _HARD_LOOKAHEAD_FRAMES)
        lookback = min(int(capacity * self._lookback_ratio), max(0, capacity - 1))
        lookahead = max(1, capacity - lookback)
        return lookback, min(lookahead, _HARD_LOOKAHEAD_FRAMES)

    def _frame_at_offset(self, start_frame: int, offset: int) -> int | None:
        idx = self._frame_index.get(start_frame)
        if idx is None:
            return None
        n = len(self._shot_frames)
        return self._shot_frames[(idx + offset) % n]

    def _rebuild_queue(self) -> None:
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
        self._queue = deque(want)
        self._queued = want_set

    def _schedule_kick(self) -> None:
        if not self._kick_timer.isActive():
            self._kick_timer.start()

    def _fill_slots(self) -> None:
        if self._paused:
            return
        gen = self._generation
        capacity = self._cache_capacity()
        cached_count = len(self._cache.cached_frames())
        while len(self._inflight) < 1 and self._queue:
            if gen != self._generation:
                return
            if cached_count + len(self._inflight) >= capacity:
                return
            frame = self._queue.popleft()
            self._queued.discard(frame)
            if self._cache.contains(frame) or frame in self._inflight:
                continue
            fut = self._pool.submit(
                _decode_one,
                self._path,
                frame,
                self._frame_transform,
                self._decoder_holder,
                self._decoder_lock,
            )
            self._inflight[frame] = fut
            fut.add_done_callback(lambda f, fr=frame, g=gen: self._on_done(fr, f, g))

    def _on_done(self, frame: int, fut: Future, generation: int) -> None:
        if generation != self._generation:
            return
        try:
            rgb = fut.result()
        except Exception:
            rgb = None
        try:
            self._delivery_ready.emit(frame, rgb, generation)
        except RuntimeError:
            return

    def _deliver(self, frame: int, rgb: np.ndarray | None, generation: int) -> None:
        self._inflight.pop(frame, None)
        if generation != self._generation:
            self._schedule_kick()
            return
        had_sample_before = self._cache.estimate_frame_bytes() > 0
        if rgb is not None:
            self._cache.put(frame, rgb)
        self.frame_loaded.emit(frame, rgb)
        if not had_sample_before and rgb is not None:
            self._rebuild_queue()
        self._schedule_kick()


__all__ = ["VideoPrefetchService"]
