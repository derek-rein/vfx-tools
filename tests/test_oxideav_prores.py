"""oxideav-prores PyO3 extension — true 12-bit ProRes-compatible MOV."""

from __future__ import annotations

from pathlib import Path

import av
import exr_prores
import numpy as np
import pytest

from src.core.constants import OXIDEAV_PRORES_KEYS, available_video_codecs, video_codec_by_key
from src.core.convert import run_exr_to_video
from src.core.ocio_utils import get_bundled_aces_studio_path, resolve_ocio_for_cli
from src.core.oxideav_prores import (
    extension_version,
    is_available,
    open_writer,
    profile_for_codec_key,
    unavailable_reason,
    write_rgb48_frame,
)
from src.render.slate import render_slate_frame
from tests.support.integration import (
    assert_video_output,
    conversion_ocio_args,
    run_cli,
    write_synthetic_exr_sequence,
)

pytestmark = pytest.mark.skipif(
    not is_available(),
    reason="exr_prores extension not built (make oxideav-prores)",
)


def _ocio_cfg():
    return resolve_ocio_for_cli(None)


def _spaces_for(mode: str) -> tuple[str, str]:
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


def _y_means(path: Path, *, width: int, height: int) -> list[float]:
    probe = av.open(str(path))
    means: list[float] = []
    for frame in probe.decode(video=0):
        y = np.frombuffer(bytes(frame.planes[0]), dtype="<u2")
        stride = frame.planes[0].line_size // 2
        rows = len(y) // stride
        yimg = y.reshape(rows, stride)[:height, :width]
        cy, cx = height // 2, width // 2
        means.append(float(yimg[cy - 16 : cy + 16, cx - 16 : cx + 16].mean()))
    probe.close()
    return means


class TestOxideavAvailability:
    def test_presets_listed_when_extension_present(self):
        keys = {s.key for s in available_video_codecs()}
        assert "prores_ox_4444" in keys
        assert "prores_ox_xq" in keys

    def test_spec_labels_twelve_bit(self):
        for key in OXIDEAV_PRORES_KEYS:
            s = video_codec_by_key(key)
            assert s is not None
            assert s.bit_depth == 12
            assert s.libav_codec == "oxideav_prores"
            assert s.pix_fmt == "yuv444p12le"
            assert "12" in s.display_name
            assert s.is_available()

    def test_profile_mapping(self):
        assert profile_for_codec_key("prores_ox_4444") == "4444"
        assert profile_for_codec_key("prores_ox_xq") == "xq"
        assert profile_for_codec_key("ap4h") == "4444"
        assert profile_for_codec_key("ap4x") == "xq"
        with pytest.raises(ValueError, match="not an oxideav"):
            profile_for_codec_key("prores_4444")

    def test_extension_version_nonempty(self):
        assert extension_version()
        assert unavailable_reason() == ""


class TestOxideavEncode:
    def test_write_rgb48_smoke(self, tmp_path: Path):
        out = tmp_path / "rgb.mov"
        w, h = 64, 48
        writer = open_writer(out, w, h, 24, 1, "prores_ox_4444")
        try:
            write_rgb48_frame(writer, np.full((h, w, 3), 20000, dtype=np.uint16))
            write_rgb48_frame(writer, np.full((h, w, 3), 30000, dtype=np.uint16))
        finally:
            writer.close()
        assert out.stat().st_size > 0
        probe = av.open(str(out))
        stream = probe.streams.video[0]
        assert "prores" in (stream.codec_context.name or "")
        assert stream.codec_context.codec_tag == "ap4h"
        assert stream.codec_context.pix_fmt == "yuv444p12le"
        frames = list(probe.decode(video=0))
        probe.close()
        assert len(frames) == 2

    def test_xq_fourcc(self, tmp_path: Path):
        out = tmp_path / "xq.mov"
        w, h = 64, 48
        with open_writer(out, w, h, 24, 1, "prores_ox_xq") as writer:
            write_rgb48_frame(writer, np.full((h, w, 3), 16000, dtype=np.uint16))
        probe = av.open(str(out))
        assert probe.streams.video[0].codec_context.codec_tag == "ap4x"
        probe.close()

    def test_rejects_odd_dimensions(self, tmp_path: Path):
        with pytest.raises(Exception, match="even"):
            open_writer(tmp_path / "odd.mov", 65, 48, 24, 1, "prores_ox_4444")

    def test_rejects_invalid_profile(self, tmp_path: Path):
        with pytest.raises(Exception, match="profile"):
            exr_prores.ProResMovWriter(str(tmp_path / "bad.mov"), 64, 48, 24, 1, profile="proxy")

    def test_rejects_wrong_rgb_shape(self, tmp_path: Path):
        out = tmp_path / "shape.mov"
        with open_writer(out, 64, 48, 24, 1, "prores_ox_4444") as writer:
            with pytest.raises(Exception, match="shape|size"):
                write_rgb48_frame(writer, np.zeros((48, 32, 3), dtype=np.uint16))
            with pytest.raises(ValueError, match="HxWx3"):
                write_rgb48_frame(writer, np.zeros((48, 64), dtype=np.uint16))

    def test_rejects_write_after_close(self, tmp_path: Path):
        out = tmp_path / "closed.mov"
        writer = open_writer(out, 64, 48, 24, 1, "prores_ox_4444")
        write_rgb48_frame(writer, np.full((48, 64, 3), 10000, dtype=np.uint16))
        writer.close()
        with pytest.raises(Exception, match="closed"):
            write_rgb48_frame(writer, np.full((48, 64, 3), 20000, dtype=np.uint16))

    def test_empty_close_ok(self, tmp_path: Path):
        """Cancel-before-encode must not raise on close (zero frames)."""
        out = tmp_path / "empty.mov"
        writer = open_writer(out, 64, 48, 24, 1, "prores_ox_4444")
        writer.close()  # no frames
        assert out.is_file()

    def test_fps_rational_23_976(self, tmp_path: Path):
        out = tmp_path / "film.mov"
        w, h = 64, 48
        with open_writer(out, w, h, 24000, 1001, "prores_ox_4444") as writer:
            write_rgb48_frame(writer, np.full((h, w, 3), 18000, dtype=np.uint16))
            write_rgb48_frame(writer, np.full((h, w, 3), 22000, dtype=np.uint16))
        probe = av.open(str(out))
        stream = probe.streams.video[0]
        rate = stream.average_rate or stream.rate
        probe.close()
        assert rate is not None
        assert abs(float(rate) - 23.976) < 0.05


@pytest.mark.integration
class TestOxideavBitDepth:
    """YUV-domain mid-bin: +32 on a 12-bit lattice must survive encode/decode."""

    W, H = 128, 128
    BASE = 2048
    MID = 32

    def test_yuv_midbin_kept_4444(self, tmp_path: Path):
        out = tmp_path / "midbin_4444.mov"
        with exr_prores.ProResMovWriter(str(out), self.W, self.H, 24, 1, profile="4444") as writer:
            for yv in (self.BASE, self.BASE + self.MID):
                y = np.full((self.H, self.W), yv, dtype=np.uint16)
                c = np.full((self.H, self.W), 2048, dtype=np.uint16)
                writer.write_yuv444_p12(y, c, c)
        means = _y_means(out, width=self.W, height=self.H)
        assert len(means) == 2
        delta = means[1] - means[0]
        assert delta > 12, f"12-bit mid-bin collapsed: means={means} delta={delta}"

    def test_yuv_midbin_kept_xq(self, tmp_path: Path):
        out = tmp_path / "midbin_xq.mov"
        with exr_prores.ProResMovWriter(str(out), self.W, self.H, 24, 1, profile="xq") as writer:
            for yv in (self.BASE, self.BASE + self.MID):
                y = np.full((self.H, self.W), yv, dtype=np.uint16)
                c = np.full((self.H, self.W), 2048, dtype=np.uint16)
                writer.write_yuv444_p12(y, c, c)
        means = _y_means(out, width=self.W, height=self.H)
        delta = means[1] - means[0]
        assert delta > 12, f"XQ mid-bin collapsed: means={means} delta={delta}"


@pytest.mark.integration
class TestOxideavConvertPath:
    """End-to-end EXR → oxideav ProRes through ``run_exr_to_video`` and CLI."""

    def test_run_exr_to_video_4444(self, tmp_path: Path):
        exr_dir = tmp_path / "exr"
        write_synthetic_exr_sequence(exr_dir, frames=(1, 2, 3), width=64, height=48)
        out = tmp_path / "out.mov"
        cfg = _ocio_cfg()
        src, dst = _spaces_for("exr2video")
        logs: list[str] = []
        progress_calls: list[tuple[int, int]] = []
        run_exr_to_video(
            str(exr_dir),
            out,
            cfg,
            src,
            dst,
            fps=24,
            workers=1,
            video_codec="oxideav_prores",
            pix_fmt_out="yuv444p12le",
            codec_key="prores_ox_4444",
            log=logs.append,
            progress=lambda n, t: progress_calls.append((n, t)),
        )
        assert_video_output(out, min_frames=3, min_width=64, min_height=48)
        probe = av.open(str(out))
        stream = probe.streams.video[0]
        assert stream.codec_context.codec_tag == "ap4h"
        assert stream.codec_context.pix_fmt == "yuv444p12le"
        probe.close()
        assert any("oxideav" in line for line in logs)
        assert progress_calls[-1] == (3, 3)

    def test_run_exr_to_video_xq_workers(self, tmp_path: Path):
        """Pool path still encodes ordered frames via oxideav."""
        exr_dir = tmp_path / "exr"
        write_synthetic_exr_sequence(exr_dir, frames=(1, 2, 3, 4), width=64, height=48)
        out = tmp_path / "xq.mov"
        cfg = _ocio_cfg()
        src, dst = _spaces_for("exr2video")
        bundled = get_bundled_aces_studio_path()
        assert bundled is not None
        run_exr_to_video(
            str(exr_dir),
            out,
            cfg,
            src,
            dst,
            fps=24,
            workers=2,
            video_codec="oxideav_prores",
            pix_fmt_out="yuv444p12le",
            codec_key="prores_ox_xq",
            config_source="",
            config_path=str(bundled),
        )
        assert_video_output(out, min_frames=4)
        probe = av.open(str(out))
        assert probe.streams.video[0].codec_context.codec_tag == "ap4x"
        probe.close()

    def test_scale_half(self, tmp_path: Path):
        exr_dir = tmp_path / "exr"
        write_synthetic_exr_sequence(exr_dir, frames=(1, 2), width=128, height=96)
        out = tmp_path / "scaled.mov"
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
            scale=0.5,
            video_codec="oxideav_prores",
            pix_fmt_out="yuv444p12le",
            codec_key="prores_ox_4444",
        )
        assert_video_output(out, min_frames=2, min_width=64, min_height=48)

    def test_slate_prepended(self, tmp_path: Path, qapp):
        exr_dir = tmp_path / "exr"
        write_synthetic_exr_sequence(exr_dir, frames=(1, 2), width=64, height=48)
        out = tmp_path / "slate.mov"
        cfg = _ocio_cfg()
        src, dst = _spaces_for("exr2video")
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
            64,
            48,
        )
        run_exr_to_video(
            str(exr_dir),
            out,
            cfg,
            src,
            dst,
            fps=24,
            workers=1,
            video_codec="oxideav_prores",
            pix_fmt_out="yuv444p12le",
            codec_key="prores_ox_4444",
            slate_frame=slate,
        )
        # Slate + 2 EXR frames.
        assert_video_output(out, min_frames=3)

    def test_forces_mov_suffix(self, tmp_path: Path):
        exr_dir = tmp_path / "exr"
        write_synthetic_exr_sequence(exr_dir, frames=(1, 2), width=64, height=48)
        out = tmp_path / "wrong.mp4"
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
            video_codec="oxideav_prores",
            pix_fmt_out="yuv444p12le",
            codec_key="prores_ox_4444",
        )
        assert (tmp_path / "wrong.mov").is_file()

    def test_cancel(self, tmp_path: Path):
        exr_dir = tmp_path / "exr"
        write_synthetic_exr_sequence(exr_dir, frames=(1, 2, 3, 4, 5), width=64, height=48)
        out = tmp_path / "cancel.mov"
        cfg = _ocio_cfg()
        src, dst = _spaces_for("exr2video")
        calls = {"n": 0}

        def cancel_check() -> bool:
            calls["n"] += 1
            return calls["n"] > 2

        with pytest.raises(RuntimeError, match="Cancelled"):
            run_exr_to_video(
                str(exr_dir),
                out,
                cfg,
                src,
                dst,
                fps=24,
                workers=1,
                video_codec="oxideav_prores",
                pix_fmt_out="yuv444p12le",
                codec_key="prores_ox_4444",
                cancel_check=cancel_check,
            )

    def test_cancel_before_first_frame(self, tmp_path: Path):
        """Empty writer close must succeed when cancel fires immediately."""
        exr_dir = tmp_path / "exr"
        write_synthetic_exr_sequence(exr_dir, frames=(1, 2, 3), width=64, height=48)
        out = tmp_path / "cancel0.mov"
        cfg = _ocio_cfg()
        src, dst = _spaces_for("exr2video")

        with pytest.raises(RuntimeError, match="Cancelled"):
            run_exr_to_video(
                str(exr_dir),
                out,
                cfg,
                src,
                dst,
                fps=24,
                workers=1,
                video_codec="oxideav_prores",
                pix_fmt_out="yuv444p12le",
                codec_key="prores_ox_4444",
                cancel_check=lambda: True,
            )

    def test_cli_exr2video_oxideav(self, tmp_path: Path):
        exr_dir = tmp_path / "exr"
        write_synthetic_exr_sequence(exr_dir, frames=(1001, 1002, 1003), width=64, height=48)
        out = tmp_path / "cli.mov"
        result = run_cli(
            "exr2video",
            "-i",
            str(exr_dir),
            "-o",
            str(out),
            "--fps",
            "24",
            "--codec",
            "prores_ox_4444",
            "--workers",
            "1",
        )
        assert result.returncode == 0, result.stderr
        assert_video_output(out, min_frames=3)
        probe = av.open(str(out))
        assert probe.streams.video[0].codec_context.codec_tag == "ap4h"
        probe.close()

    def test_cli_exr2video_xq(self, tmp_path: Path):
        exr_dir = tmp_path / "exr"
        write_synthetic_exr_sequence(exr_dir, frames=(1, 2), width=64, height=48)
        out = tmp_path / "cli_xq.mov"
        result = run_cli(
            "exr2video",
            "-i",
            str(exr_dir),
            "-o",
            str(out),
            "--fps",
            "24",
            "--codec",
            "prores_ox_xq",
            "--workers",
            "1",
        )
        assert result.returncode == 0, result.stderr
        assert_video_output(out, min_frames=2)
        probe = av.open(str(out))
        assert probe.streams.video[0].codec_context.codec_tag == "ap4x"
        probe.close()

    def test_cli_lists_oxideav_codec(self):
        result = run_cli("exr2video", "--help")
        assert result.returncode == 0
        assert "prores_ox_4444" in result.stdout
        assert "prores_ox_xq" in result.stdout
