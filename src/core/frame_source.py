"""Unified frame sources for video / R3D ingest and player preview.

Convert and the sequence player both need float32 RGB frames from either
PyAV (common codecs) or the optional R3D SDK bridge. This module owns that
dispatch so callers do not branch on extension at every site.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

import numpy as np

log = logging.getLogger(__name__)

ProgressCancel = Callable[[], bool]

# Player scrub constants (mirrored historical video_prefetch behaviour).
_FORWARD_DECODE_LIMIT = 48
_MAX_DECODE_STEPS = 600


@dataclass(frozen=True)
class MediaInfo:
    """Probe-level description of a media file (full sensor / stream size)."""

    path: str
    width: int
    height: int
    fps: float
    frame_count: int
    kind: str  # "video" | "r3d"
    colorspace_hint: str = ""


@runtime_checkable
class PreviewDecoder(Protocol):
    """Player / prefetch decoder: random-access 1-based frames, float32 RGB."""

    path: str

    def close(self) -> None: ...

    def get_frame(self, idx_1based: int) -> np.ndarray | None: ...

    @property
    def decode_size(self) -> tuple[int, int]:
        """Width × height of frames returned by :meth:`get_frame`."""
        ...


@runtime_checkable
class IngestSource(Protocol):
    """Convert-path source: sequential or random-access RGB + optional EXR attrs."""

    @property
    def info(self) -> MediaInfo: ...

    @property
    def output_size(self) -> tuple[int, int]:
        """Width × height after scale / decode ladder."""
        ...

    def close(self) -> None: ...

    def __enter__(self) -> IngestSource: ...

    def __exit__(self, *exc: object) -> None: ...

    def log_header(self, log_fn: Callable[[str], None] | None) -> None:
        """Optional source-specific log lines (R3D SDK version, camera, …)."""
        ...

    def iter_frames(
        self,
        frame_set: set[int] | None = None,
        *,
        cancel_check: ProgressCancel | None = None,
    ) -> Iterator[tuple[int, np.ndarray, dict[str, str]]]:
        """Yield ``(1-based index, float32 RGB HWC, extra_exr_attrs)``."""
        ...


def open_preview_decoder(path: str, *, fps: float = 0.0) -> PreviewDecoder:
    """Factory for the sequence-player / prefetch decoder."""
    from .r3d import is_r3d_path

    if is_r3d_path(path):
        return R3DPreviewDecoder(path, fps=fps)
    return VideoPreviewDecoder(path, fps=fps)


def open_ingest_source(
    path: str | Path,
    *,
    scale: float = 1.0,
    deinterlace: str = "auto",
    log_fn: Callable[[str], None] | None = None,
) -> IngestSource:
    """Factory for video→EXR decode (R3D SDK or PyAV)."""
    from .r3d import R3DUnavailableError, is_available, is_r3d_path, unavailable_reason

    path_s = str(path)
    if is_r3d_path(path_s):
        if not is_available():
            raise R3DUnavailableError(unavailable_reason())
        return R3DIngestSource(path_s, scale=scale)
    return VideoIngestSource(path_s, scale=scale, deinterlace=deinterlace, log_fn=log_fn)


def scaled_dims(w: int, h: int, scale: float) -> tuple[int, int]:
    """Return even-dimensioned (w, h) after applying *scale* (matches convert)."""
    if scale >= 1.0:
        return w, h
    sw = max(2, int(w * scale + 0.5))
    sh = max(2, int(h * scale + 0.5))
    sw -= sw % 2
    sh -= sh % 2
    return sw, sh


def resize_rgb_f32(rgb: np.ndarray, out_w: int, out_h: int) -> np.ndarray:
    """Nearest-neighbor resize for float32 RGB."""
    h, w = rgb.shape[:2]
    if w == out_w and h == out_h:
        return rgb
    ys = np.linspace(0, h - 1, out_h)
    xs = np.linspace(0, w - 1, out_w)
    yi = np.clip(np.rint(ys).astype(np.intp), 0, h - 1)
    xi = np.clip(np.rint(xs).astype(np.intp), 0, w - 1)
    return np.ascontiguousarray(rgb[np.ix_(yi, xi)], dtype=np.float32)


# ---------------------------------------------------------------------------
# Preview decoders (player / prefetch)
# ---------------------------------------------------------------------------


class VideoPreviewDecoder:
    """Stateful sequential PyAV decoder with keyframe-seek + decode-forward."""

    def __init__(self, path: str, *, fps: float = 0.0) -> None:
        import av

        from .video import stream_fps

        self.path = path
        self._av = av
        self.container = av.open(path)
        if not self.container.streams.video:
            self.container.close()
            raise RuntimeError(f"No video stream in {path}")
        self.stream = self.container.streams.video[0]
        try:
            self.stream.thread_type = "AUTO"
        except Exception:
            pass

        probed = float(fps) if fps and fps > 0 else 0.0
        self.fps = probed if probed > 0 else (stream_fps(self.stream) or 24.0)
        self._time_base = self.stream.time_base
        self._start_pts = int(self.stream.start_time or 0)

        self._frame_iter: Iterator | None = None
        self._next_idx: int | None = None
        self._last_rgb: np.ndarray | None = None
        self._last_idx: int | None = None
        self._eof = False
        self._decode_w = int(self.stream.width or 0)
        self._decode_h = int(self.stream.height or 0)

    @property
    def decode_size(self) -> tuple[int, int]:
        if self._last_rgb is not None:
            h, w = self._last_rgb.shape[:2]
            return int(w), int(h)
        return max(1, self._decode_w), max(1, self._decode_h)

    def close(self) -> None:
        self._frame_iter = None
        try:
            self.container.close()
        except Exception:
            pass

    def _frame_to_rgb_f32(self, frame) -> np.ndarray:
        try:
            arr = frame.to_ndarray(format="rgb48le")
            rgb = np.ascontiguousarray(arr.astype(np.float32) * (1.0 / 65535.0))
        except Exception:
            arr = frame.to_ndarray(format="rgb24")
            rgb = np.ascontiguousarray(arr.astype(np.float32) * (1.0 / 255.0))
        self._decode_w = int(rgb.shape[1])
        self._decode_h = int(rgb.shape[0])
        return rgb

    def _pts_to_index(self, pts: int | None) -> int:
        if pts is None or self._time_base is None:
            return 1
        try:
            rel = float((int(pts) - self._start_pts) * self._time_base)
        except Exception:
            return 1
        if rel < 0:
            rel = 0.0
        return max(1, int(math.floor(rel * self.fps + 1e-6)) + 1)

    def _index_to_pts(self, idx_1based: int) -> int:
        idx = max(1, int(idx_1based))
        if self._time_base is None:
            import av

            return int((idx - 1) / self.fps * av.time_base)
        sec = (idx - 1) / self.fps
        return self._start_pts + int(round(sec / float(self._time_base)))

    def _frame_index(self, frame) -> int:
        pts = getattr(frame, "pts", None)
        if pts is not None:
            return self._pts_to_index(int(pts))
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
        if self.fps <= 0:
            from .video import stream_fps

            self.fps = stream_fps(self.stream) or 24.0
        self._frame_iter = None
        self._next_idx = None
        self._last_rgb = None
        self._last_idx = None
        self._eof = False

    def _seek_to_index(self, idx_1based: int) -> None:
        target = max(1, int(idx_1based))
        try:
            if target <= 1:
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
        idx = max(1, int(idx_1based))
        if self._last_idx == idx and self._last_rgb is not None:
            return self._last_rgb

        need_seek = False
        if self._eof:
            need_seek = True
        elif self._last_idx is None:
            need_seek = idx > 1
        elif idx < self._last_idx:
            need_seek = True
        elif idx > self._last_idx + _FORWARD_DECODE_LIMIT:
            need_seek = True

        if need_seek:
            self._seek_to_index(idx)
        elif idx == self._last_idx and self._last_rgb is not None:
            return self._last_rgb

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
                return rgb

        if self._last_idx == idx:
            return self._last_rgb
        return None


class R3DPreviewDecoder:
    """R3D / N-RAW decoder for player scrub (half-res good by default)."""

    def __init__(self, path: str, *, fps: float = 0.0, mode: int | None = None) -> None:
        from .r3d import DECODE_PREVIEW, R3DClip, scale_for_decode_mode

        self.path = path
        self._clip = R3DClip(path)
        self.fps = float(fps) if fps and fps > 0 else float(self._clip.info.fps or 24.0)
        self._mode = int(mode) if mode is not None else DECODE_PREVIEW
        self._last_idx: int | None = None
        self._last_rgb: np.ndarray | None = None
        ladder = scale_for_decode_mode(self._mode)
        full_w, full_h = self._clip.info.width, self._clip.info.height
        self._decode_w = max(1, int(round(full_w * ladder)))
        self._decode_h = max(1, int(round(full_h * ladder)))

    @property
    def decode_size(self) -> tuple[int, int]:
        if self._last_rgb is not None:
            h, w = self._last_rgb.shape[:2]
            return int(w), int(h)
        return self._decode_w, self._decode_h

    def close(self) -> None:
        try:
            self._clip.close()
        except Exception:
            pass

    def get_frame(self, idx_1based: int) -> np.ndarray | None:
        idx = max(1, int(idx_1based))
        if self._last_idx == idx and self._last_rgb is not None:
            return self._last_rgb
        try:
            rgb = self._clip.decode_frame(idx - 1, mode=self._mode)
        except Exception:
            log.debug("R3D get_frame failed frame=%s", idx, exc_info=True)
            return None
        self._last_idx = idx
        self._last_rgb = rgb
        self._decode_w = int(rgb.shape[1])
        self._decode_h = int(rgb.shape[0])
        return rgb


# ---------------------------------------------------------------------------
# Ingest sources (convert)
# ---------------------------------------------------------------------------


class VideoIngestSource:
    """PyAV sequential decode for video→EXR."""

    def __init__(
        self,
        path: str,
        *,
        scale: float = 1.0,
        deinterlace: str = "auto",
        log_fn: Callable[[str], None] | None = None,
    ) -> None:
        from .video import probe_video

        self._path = path
        self._scale = float(scale)
        self._deinterlace = deinterlace
        self._log_fn = log_fn
        w, h, fps, total = probe_video(path)
        self._info = MediaInfo(
            path=path,
            width=int(w),
            height=int(h),
            fps=float(fps) if fps else 24.0,
            frame_count=max(1, int(total)),
            kind="video",
        )
        self._out_w, self._out_h = scaled_dims(self._info.width, self._info.height, self._scale)
        self._container = None

    @property
    def info(self) -> MediaInfo:
        return self._info

    @property
    def output_size(self) -> tuple[int, int]:
        return self._out_w, self._out_h

    def close(self) -> None:
        if self._container is not None:
            try:
                self._container.close()
            except Exception:
                pass
            self._container = None

    def __enter__(self) -> VideoIngestSource:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def log_header(self, log_fn: Callable[[str], None] | None) -> None:
        return

    def iter_frames(
        self,
        frame_set: set[int] | None = None,
        *,
        cancel_check: ProgressCancel | None = None,
    ) -> Iterator[tuple[int, np.ndarray, dict[str, str]]]:
        import av

        from .video import decode_video_frames

        do_resize = self._scale < 1.0
        ow, oh = self._out_w, self._out_h
        max_idx = max(frame_set) if frame_set else 0

        container = av.open(self._path)
        self._container = container
        try:
            stream = container.streams.video[0]
            idx = 0
            submitted = 0
            target_count = len(frame_set) if frame_set else None
            for frame in decode_video_frames(
                container, stream, deinterlace=self._deinterlace, log=self._log_fn
            ):
                if cancel_check and cancel_check():
                    raise RuntimeError("Cancelled")
                idx += 1
                if frame_set is not None:
                    if idx not in frame_set:
                        if max_idx and idx > max_idx:
                            break
                        continue
                if do_resize:
                    frame = frame.reformat(width=ow, height=oh)
                rgb_u16 = frame.to_ndarray(format="rgb48le")
                rgb_f32 = np.ascontiguousarray(rgb_u16.astype(np.float32) * (1.0 / 65535.0))
                yield idx, rgb_f32, {}
                submitted += 1
                if target_count is not None and submitted >= target_count:
                    break
                if max_idx and idx >= max_idx:
                    break
        finally:
            self.close()


class R3DIngestSource:
    """R3D SDK decode for video→EXR (IPP2 primary Log3G10/RWG)."""

    def __init__(self, path: str, *, scale: float = 1.0) -> None:
        from .r3d import R3DClip, decode_mode_for_scale, scale_for_decode_mode

        self._path = path
        self._scale = float(scale)
        self._mode = decode_mode_for_scale(self._scale)
        ladder = scale_for_decode_mode(self._mode)
        self._extra_scale = self._scale / ladder if ladder > 0 else self._scale
        self._need_extra_resize = abs(self._extra_scale - 1.0) > 0.02
        self._clip = R3DClip(path)
        info = self._clip.info
        self._info = MediaInfo(
            path=path,
            width=int(info.width),
            height=int(info.height),
            fps=float(info.fps) if info.fps else 24.0,
            frame_count=max(1, int(info.frame_count)),
            kind="r3d",
            colorspace_hint=info.colorspace_hint or "",
        )
        self._out_w, self._out_h = scaled_dims(self._info.width, self._info.height, self._scale)
        self._base_attrs: dict[str, str] = {
            f"exrconverter:r3d:{k}": v for k, v in self._clip.clip_metadata_dict().items() if v
        }
        self._sdk_version = info.sdk_version

    @property
    def info(self) -> MediaInfo:
        return self._info

    @property
    def output_size(self) -> tuple[int, int]:
        return self._out_w, self._out_h

    @property
    def decode_mode(self) -> int:
        return self._mode

    def close(self) -> None:
        try:
            self._clip.close()
        except Exception:
            pass

    def __enter__(self) -> R3DIngestSource:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def log_header(self, log_fn: Callable[[str], None] | None) -> None:
        if not log_fn:
            return
        from .r3d import sdk_version

        log_fn(f"R3D SDK: {sdk_version() or self._sdk_version}")
        log_fn(f"R3D decode: mode={self._mode} pipeline=IPP2 primary Log3G10/RWG")
        cam = self._base_attrs.get("exrconverter:r3d:camera_model")
        if cam:
            log_fn(f"R3D camera: {cam}")

    def _frame_attrs(self, idx_0: int) -> dict[str, str]:
        attrs = dict(self._base_attrs)
        tc = self._clip.absolute_timecode(idx_0)
        if tc:
            attrs["exrconverter:r3d:absolute_timecode"] = tc
        etc = self._clip.edge_timecode(idx_0)
        if etc:
            attrs["exrconverter:r3d:edge_timecode"] = etc
        attrs["exrconverter:r3d:source_frame"] = str(idx_0)
        return attrs

    def iter_frames(
        self,
        frame_set: set[int] | None = None,
        *,
        cancel_check: ProgressCancel | None = None,
    ) -> Iterator[tuple[int, np.ndarray, dict[str, str]]]:
        total = self._info.frame_count
        if frame_set:
            indices = sorted(i for i in frame_set if 1 <= i <= total)
        else:
            indices = list(range(1, total + 1))
        if not indices:
            raise RuntimeError("No frames selected for R3D decode.")

        ow, oh = self._out_w, self._out_h
        for idx_1based in indices:
            if cancel_check and cancel_check():
                raise RuntimeError("Cancelled")
            idx_0 = idx_1based - 1
            rgb = self._clip.decode_frame(idx_0, mode=self._mode)
            if self._need_extra_resize and (rgb.shape[1], rgb.shape[0]) != (ow, oh):
                rgb = resize_rgb_f32(rgb, ow, oh)
            yield (
                idx_1based,
                np.ascontiguousarray(rgb, dtype=np.float32),
                self._frame_attrs(idx_0),
            )
