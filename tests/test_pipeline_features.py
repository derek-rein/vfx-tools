"""Feature-level conversion tests.

All media is **generated under ``tmp_path``** during the test and cleaned up
automatically by pytest — nothing is committed to the repo.
"""

from __future__ import annotations

from fractions import Fraction
from pathlib import Path

import av
import numpy as np
import OpenImageIO as oiio
import pytest

from src.core.convert import _fps_to_rate, run_exr_to_video, run_video_to_exr
from src.core.exr_io import read_exr, write_exr
from src.core.ocio_utils import resolve_ocio_for_cli
from src.core.sequence import find_exr_sequence, scan_exr_sequences
from src.core.video import probe_video
from src.render.burnin import render_burnin_overlay
from src.render.slate import render_slate_frame
from src.render.watermark import render_watermark_overlay
from tests.support.integration import (
    assert_exr_sequence,
    assert_video_output,
    conversion_ocio_args,
    run_cli,
    write_synthetic_exr_sequence,
    write_synthetic_video,
)


def _ocio_cfg():
    return resolve_ocio_for_cli(None)


def _spaces_for(mode: str) -> tuple[str, str]:
    """Pull --src/--dst from conversion_ocio_args for direct API calls."""
    args = conversion_ocio_args(mode)
    src = dst = ""
    i = 0
    while i < len(args):
        if args[i] == "--src" and i + 1 < len(args):
            src = args[i + 1]
            i += 2
            continue
        if args[i] == "--dst" and i + 1 < len(args):
            dst = args[i + 1]
            i += 2
            continue
        i += 1
    return src, dst


# ---------------------------------------------------------------------------
# EXR generation helpers (tmp only)
# ---------------------------------------------------------------------------


def _write_exr_seq(
    directory: Path,
    *,
    stem: str = "plate",
    frames: tuple[int, ...] = (1001, 1002, 1003),
    width: int = 64,
    height: int = 36,
    compression: str = "zip",
    exr_opts: dict | None = None,
    value_fn=None,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for i, frame_num in enumerate(frames):
        rgb = np.zeros((height, width, 3), dtype=np.float32)
        if value_fn:
            rgb[:] = value_fn(i, frame_num)
        else:
            rgb[:, :, 0] = 0.1 + i * 0.05
            rgb[:, :, 1] = 0.25
            rgb[:, :, 2] = 0.4
        write_exr(
            str(directory / f"{stem}.{frame_num:04d}.exr"),
            rgb,
            compression=compression,
            dst_space="ACEScg",
            exr_opts=exr_opts,
        )
    return directory


# ---------------------------------------------------------------------------
# Video → EXR
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestVideoToExrFeatures:
    def test_cli_basic(self, tmp_path: Path):
        vid = tmp_path / "clip.mov"
        write_synthetic_video(vid, width=64, height=36, frames=4)
        out = tmp_path / "exr_out"
        result = run_cli(
            "video2exr",
            "-i",
            str(vid),
            "-o",
            str(out),
            "--padding",
            "4",
            "--start-frame",
            "1001",
            "--exr-compression",
            "zip",
        )
        assert result.returncode == 0, result.stderr
        assert_exr_sequence(out, stem="clip", expected_frames=4, min_width=64, min_height=36)

    def test_scale_half(self, tmp_path: Path):
        vid = tmp_path / "clip.mov"
        write_synthetic_video(vid, width=128, height=72, frames=2)
        out = tmp_path / "half"
        result = run_cli(
            "video2exr",
            "-i",
            str(vid),
            "-o",
            str(out),
            "--scale",
            "0.5",
            "--exr-compression",
            "zip",
        )
        assert result.returncode == 0, result.stderr
        paths, _ = find_exr_sequence(str(out))
        rgb = read_exr(paths[0])
        assert rgb.shape[1] == 64
        assert rgb.shape[0] == 36

    def test_frame_range_subset(self, tmp_path: Path):
        vid = tmp_path / "clip.mov"
        write_synthetic_video(vid, width=64, height=36, frames=6)
        out = tmp_path / "range"
        # video2exr frame_set is 1-based decode index, not start_frame numbering
        result = run_cli(
            "video2exr",
            "-i",
            str(vid),
            "-o",
            str(out),
            "--frame-range",
            "2-4",
            "--start-frame",
            "1001",
            "--exr-compression",
            "zip",
        )
        assert result.returncode == 0, result.stderr
        paths, _ = find_exr_sequence(str(out))
        assert len(paths) == 3

    def test_dwa_level_cli(self, tmp_path: Path):
        vid = tmp_path / "clip.mov"
        write_synthetic_video(vid, width=64, height=36, frames=1)
        out = tmp_path / "dwa"
        result = run_cli(
            "video2exr",
            "-i",
            str(vid),
            "-o",
            str(out),
            "--exr-compression",
            "dwaa",
            "--dwa-level",
            "20",
        )
        assert result.returncode == 0, result.stderr
        paths, _ = find_exr_sequence(str(out))
        inp = oiio.ImageInput.open(paths[0])
        level = inp.spec().getattribute("openexr:dwaCompressionLevel")
        inp.close()
        assert level is not None
        assert abs(float(level) - 20.0) < 0.5

    def test_api_cancel_mid_serial(self, tmp_path: Path):
        vid = tmp_path / "clip.mov"
        write_synthetic_video(vid, width=64, height=36, frames=8)
        out = tmp_path / "cancel"
        cfg = _ocio_cfg()
        src, dst = _spaces_for("video2exr")
        n = {"i": 0}

        def cancel_after_two() -> bool:
            n["i"] += 1
            return n["i"] > 2

        with pytest.raises(RuntimeError, match="Cancelled"):
            run_video_to_exr(
                str(vid),
                out,
                cfg,
                src,
                dst,
                workers=1,
                compression="zip",
                cancel_check=cancel_after_two,
            )


# ---------------------------------------------------------------------------
# EXR → video
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestExrToVideoFeatures:
    def test_cli_basic(self, tmp_path: Path):
        exr_dir = tmp_path / "exr"
        write_synthetic_exr_sequence(exr_dir, frames=(1001, 1002, 1003))
        out = tmp_path / "out.mov"
        result = run_cli(
            "exr2video",
            "-i",
            str(exr_dir),
            "-o",
            str(out),
            "--fps",
            "24",
            "--codec",
            "h264",
        )
        assert result.returncode == 0, result.stderr
        assert_video_output(out, min_frames=3, min_width=64, min_height=36)

    def test_fps_23_976_not_truncated(self, tmp_path: Path):
        """Regression: int(23.976) == 23 broke film rates."""
        exr_dir = tmp_path / "exr"
        write_synthetic_exr_sequence(exr_dir, frames=(1, 2, 3), width=64, height=36)
        out = tmp_path / "film.mov"
        cfg = _ocio_cfg()
        src, dst = _spaces_for("exr2video")
        run_exr_to_video(
            str(exr_dir),
            out,
            cfg,
            src,
            dst,
            fps=23.976,
            workers=1,
            video_codec="libx264",
            pix_fmt_out="yuv420p",
            codec_key="h264",
        )
        container = av.open(str(out))
        stream = container.streams.video[0]
        rate = stream.average_rate or stream.rate
        container.close()
        assert rate is not None
        fps_val = float(rate)
        assert abs(fps_val - 23.976) < 0.05, f"got fps={fps_val}"

    def test_codec_opts_crf(self, tmp_path: Path):
        exr_dir = tmp_path / "exr"
        write_synthetic_exr_sequence(exr_dir, frames=(1, 2), width=64, height=36)
        out = tmp_path / "crf.mov"
        cfg = _ocio_cfg()
        src, dst = _spaces_for("exr2video")
        run_exr_to_video(
            str(exr_dir),
            out,
            cfg,
            src,
            dst,
            fps=24,
            workers=1,
            video_codec="libx264",
            pix_fmt_out="yuv420p",
            codec_key="h264",
            codec_opts={"crf": "28", "preset": "ultrafast"},
        )
        assert out.is_file() and out.stat().st_size > 0

    def test_frame_range_on_sequence_numbers(self, tmp_path: Path):
        exr_dir = tmp_path / "exr"
        write_synthetic_exr_sequence(exr_dir, frames=(1001, 1002, 1003, 1004, 1005))
        out = tmp_path / "range.mov"
        result = run_cli(
            "exr2video",
            "-i",
            str(exr_dir),
            "-o",
            str(out),
            "--frame-range",
            "1002-1004",
            "--codec",
            "h264",
        )
        assert result.returncode == 0, result.stderr
        assert_video_output(out, min_frames=3)

    def test_scale_half(self, tmp_path: Path):
        exr_dir = tmp_path / "exr"
        write_synthetic_exr_sequence(exr_dir, frames=(1, 2), width=128, height=72)
        out = tmp_path / "half.mov"
        result = run_cli(
            "exr2video",
            "-i",
            str(exr_dir),
            "-o",
            str(out),
            "--scale",
            "0.5",
            "--codec",
            "h264",
        )
        assert result.returncode == 0, result.stderr
        w, h, _, _ = probe_video(str(out))
        assert w == 64
        assert h == 36

    def test_burnin_overlay(self, tmp_path: Path, qapp):
        exr_dir = tmp_path / "exr"
        write_synthetic_exr_sequence(exr_dir, frames=(1, 2, 3), width=128, height=72)
        overlay = render_burnin_overlay(
            128,
            72,
            {
                "top_left": "VENDOR",
                "top_center": "SHOW",
                "top_right": "2026-01-01",
                "bottom_left": "v001",
                "bottom_center": "",
                "bottom_right": "1-3",
            },
        )
        assert overlay.shape == (72, 128, 4)
        assert overlay[..., 3].max() > 0

        out = tmp_path / "burnin.mov"
        cfg = _ocio_cfg()
        src, dst = _spaces_for("exr2video")
        run_exr_to_video(
            str(exr_dir),
            out,
            cfg,
            src,
            dst,
            fps=24,
            workers=1,
            video_codec="libx264",
            pix_fmt_out="yuv420p",
            codec_key="h264",
            burnin_overlay=overlay,
        )
        assert_video_output(out, min_frames=3, min_width=128, min_height=72)

    def test_watermark_overlay(self, tmp_path: Path, qapp):
        exr_dir = tmp_path / "exr"
        write_synthetic_exr_sequence(exr_dir, frames=(1, 2), width=128, height=72)
        wm = render_watermark_overlay(
            128,
            72,
            {"enabled": True, "text": "FOR REVIEW ONLY", "opacity": 40, "size_pct": 12.0},
        )
        assert wm[..., 3].max() > 0
        out = tmp_path / "wm.mov"
        cfg = _ocio_cfg()
        src, dst = _spaces_for("exr2video")
        run_exr_to_video(
            str(exr_dir),
            out,
            cfg,
            src,
            dst,
            fps=24,
            workers=1,
            video_codec="libx264",
            pix_fmt_out="yuv420p",
            codec_key="h264",
            burnin_overlay=wm,
        )
        assert out.is_file()

    def test_slate_prepends_frame(self, tmp_path: Path, qapp):
        exr_dir = tmp_path / "exr"
        write_synthetic_exr_sequence(exr_dir, frames=(1, 2), width=128, height=72)
        slate = render_slate_frame(
            {
                "show": "TEST",
                "sequence": "sq010",
                "shot": "0010",
                "version": "v0001",
                "artist": "pytest",
                "vendor": "VFX",
                "frameRange": "1-2",
                "fps": "24",
                "date": "2026-01-01",
            },
            128,
            72,
        )
        assert slate.shape[0] == 72 and slate.shape[1] == 128
        out = tmp_path / "slate.mov"
        cfg = _ocio_cfg()
        src, dst = _spaces_for("exr2video")
        run_exr_to_video(
            str(exr_dir),
            out,
            cfg,
            src,
            dst,
            fps=24,
            workers=1,
            video_codec="libx264",
            pix_fmt_out="yuv420p",
            codec_key="h264",
            slate_frame=slate,
        )
        # 2 shot frames + 1 slate
        assert_video_output(out, min_frames=3, min_width=128, min_height=72)

    def test_per_frame_overlay_provider(self, tmp_path: Path, qapp):
        exr_dir = tmp_path / "exr"
        write_synthetic_exr_sequence(exr_dir, frames=(1001, 1002), width=64, height=36)
        calls: list[int | None] = []

        def provider(frame_num: int | None):
            calls.append(frame_num)
            return render_burnin_overlay(
                64,
                36,
                {
                    "top_left": f"F{frame_num}",
                    "top_center": "",
                    "top_right": "",
                    "bottom_left": "",
                    "bottom_center": "",
                    "bottom_right": "",
                },
            )

        out = tmp_path / "pf.mov"
        cfg = _ocio_cfg()
        src, dst = _spaces_for("exr2video")
        run_exr_to_video(
            str(exr_dir),
            out,
            cfg,
            src,
            dst,
            fps=24,
            workers=1,
            video_codec="libx264",
            pix_fmt_out="yuv420p",
            codec_key="h264",
            overlay_provider=provider,
        )
        assert len(calls) == 2
        assert 1001 in calls and 1002 in calls

    def test_missing_frame_fails(self, tmp_path: Path):
        exr_dir = tmp_path / "exr"
        write_synthetic_exr_sequence(exr_dir, frames=(1, 2), width=32, height=32)
        # Corrupt second frame
        paths, _ = find_exr_sequence(str(exr_dir))
        Path(paths[1]).write_bytes(b"garbage")
        out = tmp_path / "bad.mov"
        cfg = _ocio_cfg()
        src, dst = _spaces_for("exr2video")
        with pytest.raises(RuntimeError):
            run_exr_to_video(
                str(exr_dir),
                out,
                cfg,
                src,
                dst,
                fps=24,
                workers=1,
                video_codec="libx264",
                pix_fmt_out="yuv420p",
                codec_key="h264",
            )


# ---------------------------------------------------------------------------
# Sequence helpers + round-trip
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestSequenceAndRoundTrip:
    def test_scan_exr_sequences(self, tmp_path: Path):
        _write_exr_seq(tmp_path / "shot", stem="beauty", frames=(10, 11, 12), width=32, height=16)
        rows = scan_exr_sequences(str(tmp_path / "shot"))
        assert len(rows) >= 1
        assert rows[0]["frames"] == 3

    def test_underscore_frame_names(self, tmp_path: Path):
        d = tmp_path / "us"
        d.mkdir()
        for i in range(3):
            write_exr(
                str(d / f"04_5d_{i:05d}.exr"),
                np.full((16, 32, 3), 0.3, dtype=np.float32),
                compression="zip",
            )
        paths, basename = find_exr_sequence(str(d))
        assert len(paths) == 3
        assert basename

    def test_round_trip_video_exr_video(self, tmp_path: Path):
        vid = tmp_path / "in.mov"
        write_synthetic_video(vid, width=64, height=36, frames=3)
        exr_dir = tmp_path / "exr"
        r1 = run_cli(
            "video2exr",
            "-i",
            str(vid),
            "-o",
            str(exr_dir),
            "--exr-compression",
            "zip",
        )
        assert r1.returncode == 0, r1.stderr
        out = tmp_path / "out.mov"
        r2 = run_cli(
            "exr2video",
            "-i",
            str(exr_dir),
            "-o",
            str(out),
            "--codec",
            "h264",
            "--fps",
            "24",
        )
        assert r2.returncode == 0, r2.stderr
        assert_video_output(out, min_frames=3, min_width=64, min_height=36)


# ---------------------------------------------------------------------------
# Pure unit (no integration marker)
# ---------------------------------------------------------------------------


class TestFpsRationalUnit:
    def test_fraction_matches_common_rates(self):
        assert _fps_to_rate(23.976) == Fraction(24000, 1001)
        assert _fps_to_rate(29.97) == Fraction(30000, 1001)
        assert _fps_to_rate(59.94) == Fraction(60000, 1001)
