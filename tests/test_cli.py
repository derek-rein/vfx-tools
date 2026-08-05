"""CLI surface: defaults, auto-detect, and minimal-argv happy path.

These tests lock in the product goal: better/easier than ffmpeg for EXR↔video
with OCIO — few required flags, sensible defaults, just works.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.cli import (
    build_parser,
    default_e2v_output_path,
    default_v2e_output_dir,
    resolve_e2v_spaces,
    resolve_v2e_spaces,
)
from src.core.ocio_utils import resolve_ocio_for_cli
from tests.support.integration import (
    assert_exr_sequence,
    assert_video_output,
    run_converter,
    write_synthetic_exr_sequence,
    write_synthetic_video,
)


class TestParserDefaults:
    def test_subcommands_exist(self):
        p = build_parser()
        # smoke: both modes parse with only -i ( -o optional )
        a = p.parse_args(["video2exr", "-i", "a.mov"])
        assert a.command == "video2exr"
        assert a.input == "a.mov"
        assert a.output_dir is None
        assert a.src is None and a.dst is None

        b = p.parse_args(["exr2video", "-i", "./seq"])
        assert b.command == "exr2video"
        assert b.output is None
        assert b.src is None and b.dst is None

    def test_explicit_overrides(self):
        p = build_parser()
        a = p.parse_args(
            [
                "video2exr",
                "-i",
                "a.mov",
                "-o",
                "/tmp/out",
                "--src",
                "sRGB",
                "--dst",
                "ACEScg",
                "--padding",
                "5",
            ]
        )
        assert a.output_dir == "/tmp/out"
        assert a.src == "sRGB"
        assert a.dst == "ACEScg"
        assert a.padding == 5


class TestPathDefaults:
    def test_v2e_output_dir(self, tmp_path: Path):
        vid = tmp_path / "plate.mov"
        vid.write_bytes(b"x")
        assert default_v2e_output_dir(str(vid)) == tmp_path / "plate"

    def test_e2v_output_path(self, tmp_path: Path):
        seq = tmp_path / "shot01"
        seq.mkdir()
        out = default_e2v_output_path(str(seq), "prores")
        assert out == tmp_path / "shot01.mov"
        out_h264 = default_e2v_output_path(str(seq), "h264")
        assert out_h264.suffix == ".mp4"


class TestSpaceResolution:
    def test_v2e_auto_resolves_on_config(self, tmp_path: Path):
        cfg = resolve_ocio_for_cli(None)
        logs: list[str] = []
        # Minimal namespace
        args = build_parser().parse_args(
            ["video2exr", "-i", str(tmp_path / "x.mov")]
        )
        # No real video — still must resolve display-ish src + scene-linear dst
        src, dst = resolve_v2e_spaces(cfg, args, logs.append)
        assert cfg.getColorSpace(src) is not None
        assert cfg.getColorSpace(dst) is not None

    def test_e2v_auto_resolves_on_config(self, tmp_path: Path):
        cfg = resolve_ocio_for_cli(None)
        logs: list[str] = []
        args = build_parser().parse_args(["exr2video", "-i", str(tmp_path)])
        src, dst = resolve_e2v_spaces(cfg, args, logs.append)
        assert cfg.getColorSpace(src) is not None
        assert cfg.getColorSpace(dst) is not None

    def test_legacy_name_remaps(self):
        cfg = resolve_ocio_for_cli(None)
        logs: list[str] = []
        args = build_parser().parse_args(
            [
                "video2exr",
                "-i",
                "x.mov",
                "--src",
                "Output - Rec.709",
                "--dst",
                "ACEScg",
            ]
        )
        src, dst = resolve_v2e_spaces(cfg, args, logs.append)
        assert cfg.getColorSpace(src) is not None
        # ACEScg may remap to itself or stay
        assert cfg.getColorSpace(dst) is not None
        assert "Output - Rec.709" != src or cfg.getColorSpace("Output - Rec.709") is not None


@pytest.mark.integration
class TestMinimalCliHappyPath:
    """The whole product pitch: -i (and maybe -o) is enough."""

    def test_video2exr_only_input(self, tmp_path: Path):
        vid = tmp_path / "plate.mov"
        write_synthetic_video(vid, width=64, height=36, frames=3)
        # No -o, no --src/--dst, no --ocio
        result = run_converter(
            "--workers",
            "1",
            "video2exr",
            "-i",
            str(vid),
        )
        assert result.returncode == 0, result.stderr
        out = tmp_path / "plate"
        assert_exr_sequence(out, stem="plate", expected_frames=3, min_width=64, min_height=36)

    def test_video2exr_minimal_with_output(self, tmp_path: Path):
        vid = tmp_path / "clip.mov"
        write_synthetic_video(vid, width=64, height=36, frames=2)
        out = tmp_path / "exrs"
        result = run_converter(
            "--workers",
            "1",
            "video2exr",
            "-i",
            str(vid),
            "-o",
            str(out),
        )
        assert result.returncode == 0, result.stderr
        assert_exr_sequence(out, stem="clip", expected_frames=2, min_width=64, min_height=36)

    def test_exr2video_only_input(self, tmp_path: Path):
        seq = tmp_path / "shot"
        write_synthetic_exr_sequence(seq, frames=(1001, 1002, 1003), width=64, height=36)
        result = run_converter(
            "--workers",
            "1",
            "exr2video",
            "-i",
            str(seq),
            "--codec",
            "h264",  # widely available; still no --src/--dst/--ocio
        )
        assert result.returncode == 0, result.stderr
        # default name shot.mp4 next to dir (h264 → .mp4)
        out = tmp_path / "shot.mp4"
        assert_video_output(out, min_frames=3, min_width=64, min_height=36)

    def test_legacy_src_name_still_works(self, tmp_path: Path):
        vid = tmp_path / "clip.mov"
        write_synthetic_video(vid, width=32, height=18, frames=1)
        out = tmp_path / "o"
        result = run_converter(
            "--workers",
            "1",
            "video2exr",
            "-i",
            str(vid),
            "-o",
            str(out),
            "--src",
            "Output - Rec.709",  # not a literal name on ACES Studio
        )
        assert result.returncode == 0, result.stderr
