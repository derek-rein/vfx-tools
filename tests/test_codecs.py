"""Video codec preset metadata and encode smoke tests."""

from __future__ import annotations

import sys
from pathlib import Path

import av
import numpy as np
import pytest

from src.core.constants import (
    DEFAULT_CINEFORM_QUALITY,
    DNXHR_PROFILE,
    PRORES_KS_PROFILE,
    PRORES_VT_PROFILE,
    VIDEO_CODECS,
    VideoCodecSpec,
    available_video_codecs,
    video_codec_by_key,
)
from src.core.convert import _default_codec_opts


class TestVideoCodecSpecs:
    def test_keys_unique(self):
        keys = [s.key for s in VIDEO_CODECS]
        assert len(keys) == len(set(keys))

    def test_every_spec_has_bit_depth_in_label(self):
        for spec in VIDEO_CODECS:
            assert isinstance(spec, VideoCodecSpec)
            assert spec.bit_depth in (8, 10, 12, 16)
            assert str(spec.bit_depth) in spec.display_name
            assert spec.pix_fmt
            assert spec.chroma
            assert "bit" in spec.format_label
            assert spec.pix_fmt in spec.format_label

    def test_prores_software_ladder(self):
        keys = {s.key for s in VIDEO_CODECS}
        for k in (
            "prores_proxy",
            "prores_lt",
            "prores_422",
            "prores",
            "prores_4444",
            "prores_xq",
        ):
            assert k in keys
            assert k in PRORES_KS_PROFILE
            assert _default_codec_opts(k).get("profile") == PRORES_KS_PROFILE[k]

    def test_prores_vt_mac_only(self):
        vt = [s for s in VIDEO_CODECS if s.key.startswith("prores_vt_")]
        assert len(vt) == 6
        for s in vt:
            assert s.platforms == ("darwin",)
            assert s.libav_codec == "prores_videotoolbox"
            assert s.key in PRORES_VT_PROFILE
        if sys.platform == "darwin":
            assert all(s.is_available() for s in vt)
            assert any(s.key.startswith("prores_vt_") for s in available_video_codecs())
        else:
            assert not any(s.is_available() for s in vt)
            assert not any(s.key.startswith("prores_vt_") for s in available_video_codecs())

    def test_dnxhr_full_ladder(self):
        for k, bit, chroma in (
            ("dnxhr_lb", 8, "4:2:2"),
            ("dnxhr_sq", 8, "4:2:2"),
            ("dnxhr_hq", 8, "4:2:2"),
            ("dnxhr_hqx", 10, "4:2:2"),
            ("dnxhr_444", 10, "4:4:4"),
        ):
            s = video_codec_by_key(k)
            assert s is not None
            assert s.bit_depth == bit
            assert s.chroma == chroma
            assert _default_codec_opts(k)["profile"] == DNXHR_PROFILE[k]

    def test_hevc_and_cineform(self):
        hevc = video_codec_by_key("hevc")
        hevc8 = video_codec_by_key("hevc_8")
        assert hevc is not None and hevc.bit_depth == 10 and hevc.pix_fmt == "yuv420p10le"
        assert hevc8 is not None and hevc8.bit_depth == 8 and hevc8.pix_fmt == "yuv420p"
        assert _default_codec_opts("hevc")["crf"] == "18"
        assert _default_codec_opts("cineform")["quality"] == DEFAULT_CINEFORM_QUALITY


@pytest.mark.integration
class TestCodecEncodes:
    def test_prores_proxy_encode(self, tmp_path: Path):
        out = tmp_path / "proxy.mov"
        _encode(out, "prores_ks", "yuv422p10le", {"profile": "0", "vendor": "apl0"}, 64, 36)
        assert out.stat().st_size > 0

    def test_hevc_10_encode(self, tmp_path: Path):
        out = tmp_path / "hevc.mp4"
        _encode(out, "libx265", "yuv420p10le", {"crf": "28", "preset": "ultrafast"}, 64, 36)
        probe = av.open(str(out))
        assert probe.streams.video[0].codec_context.name in ("hevc", "libx265", "h265")
        probe.close()

    def test_dnxhr_lb_needs_hd(self, tmp_path: Path):
        # DNxHR rejects tiny frames — use 1920x1080 smoke.
        out = tmp_path / "dnx.mov"
        _encode(
            out,
            "dnxhd",
            "yuv422p",
            {"profile": "dnxhr_lb"},
            1920,
            1080,
        )
        assert out.stat().st_size > 0

    @pytest.mark.skipif(sys.platform != "darwin", reason="VideoToolbox is macOS-only")
    def test_prores_videotoolbox_hq(self, tmp_path: Path):
        out = tmp_path / "vt.mov"
        _encode(out, "prores_videotoolbox", "p210le", {"profile": "3"}, 64, 36)
        probe = av.open(str(out))
        assert "prores" in (probe.streams.video[0].codec_context.name or "")
        probe.close()


def _encode(
    path: Path,
    codec: str,
    pix_fmt: str,
    options: dict[str, str],
    w: int,
    h: int,
) -> None:
    container = av.open(str(path), mode="w")
    stream = container.add_stream(codec, rate=24)
    stream.width = w
    stream.height = h
    stream.pix_fmt = pix_fmt
    stream.options = options
    rgb = np.full((h, w, 3), 22000, dtype=np.uint16)
    vf = av.VideoFrame.from_ndarray(rgb, format="rgb48le").reformat(format=pix_fmt)
    for packet in stream.encode(vf):
        container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()
