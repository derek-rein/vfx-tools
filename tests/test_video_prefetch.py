"""Unit tests for video playback prefetch (PyAV → FrameCache)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from PySide6.QtCore import QCoreApplication

from src.services.frame_cache import FrameCache
from src.services.video_prefetch import VideoPrefetchService, _VideoDecoder


def _write_tiny_video(
    path: Path,
    *,
    n_frames: int = 8,
    fps: int = 24,
    gop: int = 12,
) -> None:
    """Write a short MPEG-4 clip with a solid red ramp per frame.

    *gop* is the max keyframe interval so seeks land on earlier I-frames and
    decode-forward is required for mid-GOP targets (mirrors real H.264 GOPs).
    """
    import av

    path.parent.mkdir(parents=True, exist_ok=True)
    container = av.open(str(path), mode="w")
    stream = container.add_stream("mpeg4", rate=fps)
    stream.width = 64
    stream.height = 48
    stream.pix_fmt = "yuv420p"
    try:
        stream.gop_size = gop
    except Exception:
        pass
    for i in range(n_frames):
        # Solid color that changes per frame so we can tell frames apart.
        rgb = np.zeros((48, 64, 3), dtype=np.uint8)
        rgb[..., 0] = min(255, 20 + i * 12)
        rgb[..., 1] = 40
        rgb[..., 2] = 80
        frame = av.VideoFrame.from_ndarray(rgb, format="rgb24")
        frame = frame.reformat(format="yuv420p")
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()


def _red_mean(rgb: np.ndarray) -> float:
    return float(rgb[..., 0].mean())


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
            assert _red_mean(f1) < _red_mean(f3)
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
            assert _red_mean(early) < _red_mean(late)
        finally:
            dec.close()

    def test_seek_matches_sequential_content(self, tmp_path: Path) -> None:
        """Keyframe seek + decode-forward must match a pure sequential read.

        The old decoder labelled the post-seek keyframe as the target index,
        so scrubbing returned the wrong picture (jitter / jumps).
        """
        vid = tmp_path / "gop.mp4"
        n = 30
        _write_tiny_video(vid, n_frames=n, gop=12)

        sequential: dict[int, float] = {}
        dec_seq = _VideoDecoder(str(vid))
        try:
            for i in range(1, n + 1):
                rgb = dec_seq.get_frame(i)
                assert rgb is not None, f"sequential miss at {i}"
                sequential[i] = _red_mean(rgb)
        finally:
            dec_seq.close()

        # Monotonic red ramp (within encode quantisation).
        assert sequential[1] < sequential[n // 2] < sequential[n]

        dec = _VideoDecoder(str(vid))
        try:
            # Mid-GOP target after a cold seek (not sequential from 1).
            for target in (15, 23, 7, 28, 3, 18):
                rgb = dec.get_frame(target)
                assert rgb is not None, f"seek miss at {target}"
                got = _red_mean(rgb)
                # Allow small encode noise; must not land on a distant keyframe.
                assert abs(got - sequential[target]) < 0.05, (
                    f"frame {target}: seek red={got:.4f} sequential={sequential[target]:.4f}"
                )
        finally:
            dec.close()

    def test_forward_play_no_regression(self, tmp_path: Path) -> None:
        """Playing 1→N should keep increasing content (no keyframe snap-back)."""
        vid = tmp_path / "play.mp4"
        _write_tiny_video(vid, n_frames=16, gop=8)
        dec = _VideoDecoder(str(vid))
        try:
            prev = -1.0
            for i in range(1, 17):
                rgb = dec.get_frame(i)
                assert rgb is not None
                m = _red_mean(rgb)
                assert m + 1e-4 >= prev, f"frame {i} jumped backward ({m} < {prev})"
                prev = m
        finally:
            dec.close()


class TestFrameTransformContract:
    """Cache must never store untransformed RGB as float working-space."""

    def test_transform_none_skips_cache(self, tmp_path: Path, qapp) -> None:
        import time

        vid = tmp_path / "t.mp4"
        _write_tiny_video(vid, n_frames=3)
        cache = FrameCache(budget_bytes=32 * 1024 * 1024)

        def boom(_rgb):
            return None

        svc = VideoPrefetchService(str(vid), cache, [1, 2, 3], frame_transform=boom)
        try:
            svc.request_immediate(1)
            for _ in range(50):
                qapp.processEvents()
                time.sleep(0.01)
            assert not cache.contains(1)
        finally:
            svc.shutdown()

    def test_transform_raises_skips_cache(self, tmp_path: Path, qapp) -> None:
        import time

        vid = tmp_path / "t.mp4"
        _write_tiny_video(vid, n_frames=3)
        cache = FrameCache(budget_bytes=32 * 1024 * 1024)

        def boom(_rgb):
            raise RuntimeError("ocio fail")

        svc = VideoPrefetchService(str(vid), cache, [1, 2, 3], frame_transform=boom)
        try:
            svc.request_immediate(1)
            for _ in range(50):
                qapp.processEvents()
                time.sleep(0.01)
            assert not cache.contains(1)
        finally:
            svc.shutdown()

    def test_transform_ok_caches_result(self, tmp_path: Path, qapp) -> None:
        import time

        vid = tmp_path / "t.mp4"
        _write_tiny_video(vid, n_frames=3)
        cache = FrameCache(budget_bytes=32 * 1024 * 1024)

        def scale(rgb):
            return np.ascontiguousarray(rgb * 0.5, dtype=np.float16)

        svc = VideoPrefetchService(str(vid), cache, [1, 2, 3], frame_transform=scale)
        try:
            svc.request_immediate(1)
            for _ in range(100):
                qapp.processEvents()
                if cache.contains(1):
                    break
                time.sleep(0.01)
            assert cache.contains(1)
            raw = _VideoDecoder(str(vid)).get_frame(1)
            assert raw is not None
            got = cache.get(1)
            assert got is not None
            assert float(got.mean()) < float(raw.mean()) * 0.75
        finally:
            svc.shutdown()


class TestResolveVideoSrcColorspace:
    def test_preferred_wins_when_valid(self) -> None:
        from pathlib import Path

        import PyOpenColorIO as OCIO

        from src.core.video import resolve_video_src_colorspace

        cfg = OCIO.Config.CreateFromFile(str(next(Path("resources").rglob("*.ocio"))))
        hit = resolve_video_src_colorspace("", cfg, preferred="Output - Rec.709")
        assert hit
        assert "709" in hit or "Rec" in hit

    def test_empty_preferred_falls_back_to_default(self) -> None:
        from pathlib import Path

        import PyOpenColorIO as OCIO

        from src.core.video import resolve_video_src_colorspace

        cfg = OCIO.Config.CreateFromFile(str(next(Path("resources").rglob("*.ocio"))))
        hit = resolve_video_src_colorspace("/no/such/file.mp4", cfg, preferred="")
        assert hit  # DEFAULT_SRC_V2E maps into studio config


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

    def test_scrub_jump_loads_target(self, tmp_path: Path, qapp) -> None:
        import time

        vid = tmp_path / "scrub.mp4"
        _write_tiny_video(vid, n_frames=20, gop=10)
        cache = FrameCache(budget_bytes=64 * 1024 * 1024)
        frames = list(range(1, 21))
        svc = VideoPrefetchService(str(vid), cache, frames)
        try:
            svc.set_context(1, playing=False)
            svc.request_immediate(1)
            for _ in range(100):
                qapp.processEvents()
                if cache.contains(1):
                    break
                time.sleep(0.01)
            assert cache.contains(1)

            # Jump playhead like a scrub.
            svc.set_context(14, playing=False)
            svc.request_immediate(14)
            for _ in range(150):
                qapp.processEvents()
                if cache.contains(14):
                    break
                time.sleep(0.01)
            assert cache.contains(14), "scrub target never arrived"
            a = cache.get(1)
            b = cache.get(14)
            assert a is not None and b is not None
            assert _red_mean(a) < _red_mean(b)
        finally:
            svc.shutdown()
