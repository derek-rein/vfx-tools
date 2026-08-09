"""Video frame prefetch for the sequence player (PyAV decode → FrameCache).

Single-threaded decoder: PyAV containers are not safe for parallel consumers.

Playback / scrub strategy (FFmpeg / PyAV best practice)
-------------------------------------------------------
- ``container.seek(pts, stream=…, backward=True, any_frame=False)`` lands on the
  previous **keyframe**, not the exact target. Callers must **decode forward**
  until presentation time / frame index matches.
- Keep a long-lived ``decode()`` iterator so multi-frame packets / B-frame
  reorder buffers are not discarded mid-GOP.
- Forward play stays sequential (no re-seek) when the next requested index is
  near the decoder tip.
- Scrub lookback is kept small: every reverse jump costs a keyframe seek +
  decode-forward.
"""

from __future__ import annotations

import logging
import math
import threading
from collections import deque
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor

import numpy as np
from PySide6.QtCore import QObject, Qt, QTimer, Signal

from .cache_prefs import DEFAULT_LOOKBACK_RATIO
from .frame_cache import FrameCache

log = logging.getLogger(__name__)

FrameTransform = Callable[[np.ndarray], "np.ndarray | None"]

_HARD_LOOKAHEAD_FRAMES = 120
_MIN_LOOKAHEAD_FRAMES = 8
# Decode this many frames forward instead of seeking (cheap sequential path).
_FORWARD_DECODE_LIMIT = 48
# Scrubbing: avoid reverse seeks that thrash the GOP decoder.
_SCRUB_LOOKBACK_FRAMES = 2
# Safety cap when walking from a keyframe toward a target (long GOPs).
_MAX_DECODE_STEPS = 600


class _VideoDecoder:
    """Stateful sequential PyAV decoder with keyframe-seek + decode-forward."""

    def __init__(self, path: str, *, fps: float = 0.0) -> None:
        import av

        from ..core.video import stream_fps

        self.path = path
        self._av = av
        self.container = av.open(path)
        if not self.container.streams.video:
            self.container.close()
            raise RuntimeError(f"No video stream in {path}")
        self.stream = self.container.streams.video[0]
        # Slice / frame threading helps H.264/HEVC scrub latency.
        try:
            self.stream.thread_type = "AUTO"
        except Exception:
            pass

        # Prefer the same rate the player used for the 1…N timeline (probe).
        probed = float(fps) if fps and fps > 0 else 0.0
        self.fps = probed if probed > 0 else (stream_fps(self.stream) or 24.0)
        self._time_base = self.stream.time_base
        self._start_pts = int(self.stream.start_time or 0)

        # Persistent decoder iterator (invalidated on seek / reopen).
        self._frame_iter: Iterator | None = None
        # Next 1-based index that sequential ``_next_decoded`` will assign when
        # counting (set after first post-seek frame via PTS).
        self._next_idx: int | None = None
        self._last_rgb: np.ndarray | None = None
        self._last_idx: int | None = None
        self._eof = False

    def close(self) -> None:
        self._frame_iter = None
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

    def _pts_to_index(self, pts: int | None) -> int:
        """Map a presentation timestamp to a 1-based timeline index."""
        if pts is None or self._time_base is None:
            return 1
        try:
            rel = float((int(pts) - self._start_pts) * self._time_base)
        except Exception:
            return 1
        if rel < 0:
            rel = 0.0
        # floor(t * fps + ε) + 1 — stable at frame boundaries.
        return max(1, int(math.floor(rel * self.fps + 1e-6)) + 1)

    def _index_to_pts(self, idx_1based: int) -> int:
        """Stream PTS for the start of *idx_1based* (1-based)."""
        idx = max(1, int(idx_1based))
        if self._time_base is None:
            # AV_TIME_BASE units when no stream time_base (rare).
            import av

            return int((idx - 1) / self.fps * av.time_base)
        sec = (idx - 1) / self.fps
        return self._start_pts + int(round(sec / float(self._time_base)))

    def _frame_index(self, frame) -> int:
        pts = getattr(frame, "pts", None)
        if pts is not None:
            return self._pts_to_index(int(pts))
        # Some decoders only expose ``time`` (seconds).
        t = getattr(frame, "time", None)
        if t is not None:
            try:
                rel = float(t)
                if self._time_base is not None and self._start_pts:
                    rel = max(0.0, rel - float(self._start_pts * self._time_base))
                return max(1, int(math.floor(rel * self.fps + 1e-6)) + 1)
            except Exception:
                pass
        return self._next_idx or 1

    def _reopen(self) -> None:
        try:
            self.container.close()
        except Exception:
            pass
        self.container = self._av.open(self.path)
        self.stream = self.container.streams.video[0]
        try:
            self.stream.thread_type = "AUTO"
        except Exception:
            pass
        self._time_base = self.stream.time_base
        self._start_pts = int(self.stream.start_time or 0)
        # Keep the timeline clock from construction (probe); only re-read if
        # we never had a rate.
        if self.fps <= 0:
            from ..core.video import stream_fps

            self.fps = stream_fps(self.stream) or 24.0
        self._frame_iter = None
        self._next_idx = None
        self._last_rgb = None
        self._last_idx = None
        self._eof = False

    def _seek_to_index(self, idx_1based: int) -> None:
        """Keyframe-seek just before *idx_1based*; next decode establishes index."""
        target = max(1, int(idx_1based))
        try:
            if target <= 1:
                # Start of stream — prefer stream timeline origin.
                if self._time_base is not None:
                    self.container.seek(
                        self._start_pts,
                        stream=self.stream,
                        any_frame=False,
                        backward=True,
                    )
                else:
                    self.container.seek(0, any_frame=False, backward=True)
            else:
                pts = self._index_to_pts(target)
                self.container.seek(
                    pts,
                    stream=self.stream,
                    any_frame=False,
                    backward=True,
                )
        except Exception:
            log.debug("Video seek failed for frame %s; reopening", target, exc_info=True)
            self._reopen()
            try:
                if target > 1 and self._time_base is not None:
                    self.container.seek(
                        self._index_to_pts(target),
                        stream=self.stream,
                        any_frame=False,
                        backward=True,
                    )
            except Exception:
                log.debug("Retry seek failed for frame %s", target, exc_info=True)

        try:
            flush = getattr(self.stream.codec_context, "flush_buffers", None)
            if callable(flush):
                flush()
        except Exception:
            pass

        # Invalidate iterator so the next demux starts at the new position.
        self._frame_iter = None
        self._next_idx = None
        self._last_rgb = None
        self._last_idx = None
        self._eof = False

    def _ensure_iter(self) -> Iterator:
        if self._frame_iter is None:
            self._frame_iter = self.container.decode(self.stream)
        return self._frame_iter

    def _next_decoded(self) -> tuple[int, np.ndarray] | None:
        """Pull one frame; return ``(1-based index, rgb float32)`` or None at EOF."""
        if self._eof:
            return None
        it = self._ensure_iter()
        try:
            frame = next(it)
        except StopIteration:
            self._eof = True
            self._frame_iter = None
            return None
        except Exception:
            log.debug("Video decode error", exc_info=True)
            self._eof = True
            self._frame_iter = None
            return None

        if self._next_idx is None:
            cur = self._frame_index(frame)
            self._next_idx = cur + 1
        else:
            cur = self._next_idx
            self._next_idx = cur + 1

        rgb = self._frame_to_rgb_f32(frame)
        self._last_idx = cur
        self._last_rgb = rgb
        return cur, rgb

    def get_frame(self, idx_1based: int) -> np.ndarray | None:
        """Return float32 RGB for 1-based frame index, or None on failure."""
        idx = max(1, int(idx_1based))
        if self._last_idx == idx and self._last_rgb is not None:
            return self._last_rgb

        need_seek = False
        if self._eof:
            need_seek = True
        elif self._last_idx is None:
            # Cold start: sequential from 1 is cheaper than seek for early frames.
            need_seek = idx > 1
        elif idx < self._last_idx:
            need_seek = True
        elif idx > self._last_idx + _FORWARD_DECODE_LIMIT:
            need_seek = True

        if need_seek:
            self._seek_to_index(idx)
        elif idx == self._last_idx and self._last_rgb is not None:
            return self._last_rgb

        # After keyframe seek, walk forward until we hit (or pass) the target.
        # Never label the first post-seek keyframe as *idx* unless PTS agrees.
        steps = 0
        while steps < _MAX_DECODE_STEPS:
            steps += 1
            got = self._next_decoded()
            if got is None:
                break
            cur, rgb = got
            if cur == idx:
                return rgb
            if cur > idx:
                # Overshot (coarse PTS mapping / VFR). Return nearest decoded.
                return rgb
            # cur < idx: keep decoding toward target
            continue

        if self._last_idx == idx:
            return self._last_rgb
        return None


def _decode_one(
    path: str,
    frame: int,
    transform: FrameTransform | None,
    decoder_holder: dict,
    lock: threading.Lock,
    fps: float = 0.0,
) -> np.ndarray | None:
    """Worker entry: decode one frame via a shared locked decoder."""
    with lock:
        dec: _VideoDecoder | None = decoder_holder.get("dec")
        if dec is None or dec.path != path:
            if dec is not None:
                dec.close()
            try:
                dec = _VideoDecoder(path, fps=fps)
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
        # No OCIO: cache source-referred float (viewer treats float as working
        # only when a transform was configured — keep passthrough for no-OCIO).
        return np.ascontiguousarray(rgb, dtype=np.float16)
    try:
        out = transform(rgb)
    except Exception:
        log.debug("frame_transform raised frame=%s", frame, exc_info=True)
        return None
    # None = transform failed deliberately (do not poison cache as working).
    return out


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
        fps: float = 0.0,
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
        # Same rate as SequencePlayer timeline / probe_video (seek clock).
        self._fps = float(fps) if fps and fps > 0 else 0.0
        # lookback_ratio kept for API parity with EXR; scrub uses a fixed small
        # reverse window (seek cost) instead of a large lookback ratio.
        self._lookback_ratio = max(0.0, min(1.0, lookback_ratio))
        self._frame_transform = frame_transform

        self._current = self._shot_frames[0] if self._shot_frames else 1
        self._playing = False
        self._paused = False
        self._generation = 0
        # Latest scrub / play target — drop stale queue work when this moves.
        self._wanted: int | None = None

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
        self._wanted = current_frame
        self._rebuild_queue()
        self._schedule_kick()

    def request_immediate(self, frame: int) -> bool:
        if frame not in self._frame_set:
            return False
        self._wanted = frame
        if self._cache.contains(frame):
            return True
        if frame in self._inflight:
            return True
        # Scrub coalesce: drop other pending work so the worker serves the
        # playhead next (in-flight decode still finishes — cannot cancel mid-GOP).
        if not self._playing:
            self._queue.clear()
            self._queued.clear()
        elif frame in self._queue:
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
        # Scrub: tiny lookback (reverse seeks are expensive), prioritise ahead
        # so the decoder can stay sequential after landing on the playhead.
        lookback = min(_SCRUB_LOOKBACK_FRAMES, max(0, capacity - 1))
        lookahead = max(1, capacity - lookback)
        return lookback, min(lookahead, _HARD_LOOKAHEAD_FRAMES)

    def _frame_at_offset(self, start_frame: int, offset: int) -> int | None:
        idx = self._frame_index.get(start_frame)
        if idx is None:
            return None
        n = len(self._shot_frames)
        if n == 0:
            return None
        j = idx + offset
        if self._playing:
            # Looping transport: warm the wrap target while playing.
            return self._shot_frames[j % n]
        # Scrub: no wrap — reverse/forward seeks across the loop point thrash.
        if j < 0 or j >= n:
            return None
        return self._shot_frames[j]

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
            # While scrubbing, skip stale queue entries far from the playhead
            # so a fast drag does not backfill abandoned positions first.
            if (
                not self._playing
                and self._wanted is not None
                and abs(frame - self._wanted) > _FORWARD_DECODE_LIMIT
            ):
                continue
            fut = self._pool.submit(
                _decode_one,
                self._path,
                frame,
                self._frame_transform,
                self._decoder_holder,
                self._decoder_lock,
                self._fps,
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
