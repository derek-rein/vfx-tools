"""Unit tests for video playback prefetch (PyAV → FrameCache)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QCoreApplication

from src.services.frame_cache import FrameCache
from src.services.video_prefetch import VideoPrefetchService, _VideoDecoder


def _write_tiny_video(path: Path, *, n_frames: int = 8, fps: int = 24) -> None:
    import av

    path.parent.mkdir(parents=True, exist_ok=True)
    container = av.open(str(path), mode="w")
    stream = container.add_stream("mpeg4", rate=fps)
    stream.width = 64
    stream.height = 48
    stream.pix_fmt = "yuv420p"
    for i in range(n_frames):
        # Solid color that changes per frame so we can tell frames apart.
        rgb = np.zeros((48, 64, 3), dtype=np.uint8)
        rgb[..., 0] = min(255, 20 + i * 25)
        rgb[..., 1] = 40
        rgb[..., 2] = 80
        frame = av.VideoFrame.from_ndarray(rgb, format="rgb24")
        frame = frame.reformat(format="yuv420p")
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()


@pytest.fixture(scope="module")
def qapp():
    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([])
    return app


class TestVideoDecoder:
    def test_sequential_frames(self, tmp_path: Path) -> None:
        vid = tmp_path / "t.mp4"
        _write_tiny_video(vid, n_frames=6)
        dec = _VideoDecoder(str(vid))
        try:
            f1 = dec.get_frame(1)
            f2 = dec.get_frame(2)
            f3 = dec.get_frame(3)
            assert f1 is not None and f2 is not None and f3 is not None
            assert f1.shape[2] == 3
            assert f1.dtype == np.float32
            # Frame 1 should be darker red channel than later frames.
            assert float(f1[..., 0].mean()) < float(f3[..., 0].mean())
        finally:
            dec.close()

    def test_seek_backward(self, tmp_path: Path) -> None:
        vid = tmp_path / "t.mp4"
        _write_tiny_video(vid, n_frames=10)
        dec = _VideoDecoder(str(vid))
        try:
            late = dec.get_frame(8)
            early = dec.get_frame(2)
            assert late is not None and early is not None
            assert float(early[..., 0].mean()) < float(late[..., 0].mean())
        finally:
            dec.close()


class TestVideoPrefetchService:
    def test_loads_frames_into_cache(self, tmp_path: Path, qapp) -> None:
        import time

        vid = tmp_path / "clip.mp4"
        _write_tiny_video(vid, n_frames=5)
        cache = FrameCache(budget_bytes=64 * 1024 * 1024)
        frames = [1, 2, 3, 4, 5]
        svc = VideoPrefetchService(str(vid), cache, frames)
        try:
            svc.set_context(1, playing=False)
            svc.request_immediate(1)
            # Worker + queued delivery need a short real wait.
            for _ in range(100):
                qapp.processEvents()
                if cache.contains(1):
                    break
                time.sleep(0.01)
            assert cache.contains(1), "frame 1 never arrived in cache"
            rgb = cache.get(1)
            assert rgb is not None
            assert rgb.ndim == 3
        finally:
            svc.shutdown()
