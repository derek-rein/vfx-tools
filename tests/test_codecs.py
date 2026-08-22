"""Video codec preset metadata, encode smoke tests, and bit-depth truth.

Bit-depth claims are validated with solid-patch mid-bin roundtrips:
a 10-bit lattice value ``B`` (multiple of 64 in rgb48) vs ``B+32``.
If the encode only keeps 10 bits, both decode to the same mean (delta ≈ 0).
If 12-bit precision is kept, delta stays near 32.
"""

from __future__ import annotations

import sys
from pathlib import Path

import av
import numpy as np
import pytest

from src.core.constants import (
    DEFAULT_CINEFORM_QUALITY,
    DNXHR_PROFILE,
    FFV1_CODEC_KEYS,
    HEVC_CODEC_KEYS,
    OXIDEAV_PRORES_KEYS,
    PRORES_KS_PROFILE,
    PRORES_VT_PROFILE,
    VIDEO_CODECS,
    VideoCodecSpec,
    available_video_codecs,
    video_codec_by_key,
)
from src.core.convert import _default_codec_opts
from src.core.oxideav_prores import is_available as oxideav_prores_available


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

    def test_pix_fmt_matches_claimed_bit_depth(self):
        """pix_fmt token must not claim a higher depth than bit_depth."""
        for spec in VIDEO_CODECS:
            fmt = spec.pix_fmt
            if "16" in fmt or fmt in ("ayuv64le", "rgb48le", "rgba64le"):
                # Wide intermediate containers (VT ayuv64le) may still be 12-bit class.
                assert spec.bit_depth >= 10
            elif "12" in fmt:
                assert spec.bit_depth == 12, f"{spec.key}: {fmt} vs bit_depth={spec.bit_depth}"
            elif "10" in fmt or fmt in ("p210le", "p010le"):
                assert spec.bit_depth == 10, f"{spec.key}: {fmt} vs bit_depth={spec.bit_depth}"
            elif fmt in ("yuv420p", "yuv422p", "yuv444p"):
                assert spec.bit_depth == 8, f"{spec.key}: {fmt} vs bit_depth={spec.bit_depth}"

    def test_prores_ks_all_ten_bit(self):
        """Software prores_ks cannot encode 12-bit (only *10le pix_fmts)."""
        for key in PRORES_KS_PROFILE:
            s = video_codec_by_key(key)
            assert s is not None
            assert s.libav_codec == "prores_ks"
            assert s.bit_depth == 10, f"{key} must be labeled 10-bit"
            assert "10" in s.pix_fmt

    def test_prores_vt_4444_xq_twelve_bit(self):
        for key in ("prores_vt_4444", "prores_vt_xq"):
            s = video_codec_by_key(key)
            assert s is not None
            assert s.bit_depth == 12
            assert s.pix_fmt == "ayuv64le"

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

    def test_oxideav_prores_twelve_bit_gated(self):
        """oxideav presets claim true 12-bit and hide when extension missing."""
        assert OXIDEAV_PRORES_KEYS == frozenset({"prores_ox_4444", "prores_ox_xq"})
        for key in OXIDEAV_PRORES_KEYS:
            s = video_codec_by_key(key)
            assert s is not None
            assert s.libav_codec == "oxideav_prores"
            assert s.bit_depth == 12
            assert s.pix_fmt == "yuv444p12le"
            assert s.chroma == "4:4:4"
        avail_keys = {c.key for c in available_video_codecs()}
        if oxideav_prores_available():
            assert OXIDEAV_PRORES_KEYS <= avail_keys
        else:
            assert not (OXIDEAV_PRORES_KEYS & avail_keys)

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

    def test_hevc_ladder_and_cineform(self):
        hevc = video_codec_by_key("hevc")
        hevc8 = video_codec_by_key("hevc_8")
        hevc12 = video_codec_by_key("hevc_12")
        assert hevc is not None and hevc.bit_depth == 10 and hevc.pix_fmt == "yuv420p10le"
        assert hevc8 is not None and hevc8.bit_depth == 8 and hevc8.pix_fmt == "yuv420p"
        assert hevc12 is not None and hevc12.bit_depth == 12 and hevc12.pix_fmt == "yuv420p12le"
        assert HEVC_CODEC_KEYS == frozenset({"hevc", "hevc_8", "hevc_12"})
        assert _default_codec_opts("hevc")["crf"] == "18"
        assert _default_codec_opts("hevc_12")["crf"] == "18"
        assert _default_codec_opts("cineform")["quality"] == DEFAULT_CINEFORM_QUALITY

    def test_ffv1_ladder(self):
        ff10 = video_codec_by_key("ffv1")
        ff12 = video_codec_by_key("ffv1_12")
        assert ff10 is not None and ff10.bit_depth == 10 and ff10.pix_fmt == "yuv444p10le"
        assert ff12 is not None and ff12.bit_depth == 12 and ff12.pix_fmt == "yuv444p12le"
        assert FFV1_CODEC_KEYS == frozenset({"ffv1", "ffv1_12"})
        assert _default_codec_opts("ffv1_12")["slicecrc"] == "1"


@pytest.mark.integration
class TestCodecEncodes:
    def test_prores_proxy_encode(self, tmp_path: Path):
        out = tmp_path / "proxy.mov"
        _encode(out, "prores_ks", "yuv422p10le", {"profile": "0", "vendor": "apl0"}, 64, 36)
        assert out.stat().st_size > 0

    def test_hevc_10_encode(self, tmp_path: Path):
        # MKV is more reliable than MP4 for tiny test frames with libx265.
        out = tmp_path / "hevc.mkv"
        _encode(out, "libx265", "yuv420p10le", {"crf": "28", "preset": "ultrafast"}, 64, 36)
        probe = av.open(str(out))
        assert probe.streams.video[0].codec_context.name in ("hevc", "libx265", "h265")
        assert probe.streams.video[0].codec_context.pix_fmt == "yuv420p10le"
        probe.close()

    def test_hevc_12_encode(self, tmp_path: Path):
        out = tmp_path / "hevc12.mkv"
        _encode(out, "libx265", "yuv420p12le", {"crf": "28", "preset": "ultrafast"}, 64, 36)
        probe = av.open(str(out))
        stream = probe.streams.video[0]
        assert stream.codec_context.name in ("hevc", "libx265", "h265")
        assert stream.codec_context.pix_fmt == "yuv420p12le"
        probe.close()

    def test_ffv1_12_encode(self, tmp_path: Path):
        out = tmp_path / "ffv1_12.mkv"
        _encode(out, "ffv1", "yuv444p12le", {"slicecrc": "1"}, 64, 36)
        probe = av.open(str(out))
        assert probe.streams.video[0].codec_context.pix_fmt == "yuv444p12le"
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
        # VideoToolbox ProRes rejects tiny frames on some runner OS versions;
        # use HD and soft-skip if the hardware encoder is unavailable.
        try:
            _encode(
                out,
                "prores_videotoolbox",
                "p210le",
                {"profile": "3"},
                1920,
                1080,
            )
        except av.error.FFmpegError as e:
            pytest.skip(f"prores_videotoolbox unavailable on this host: {e}")
        probe = av.open(str(out))
        assert "prores" in (probe.streams.video[0].codec_context.name or "")
        probe.close()


@pytest.mark.integration
class TestBitDepthRoundtrip:
    """Prove encode precision with solid-patch mid-bin tests."""

    W, H = 128, 72
    # 10-bit lattice point in full-range 16-bit (multiples of 64).
    BASE = 16384  # exactly 10-bit aligned
    MID = 32  # halfway to next 10-bit code (needs >10 bits)

    def _mean_after_roundtrip(
        self,
        tmp_path: Path,
        value: int,
        codec: str,
        pix_fmt: str,
        options: dict[str, str],
        *,
        w: int | None = None,
        h: int | None = None,
    ) -> float:
        w = w or self.W
        h = h or self.H
        path = tmp_path / f"{codec}_{pix_fmt}_{value}.mkv"
        # Prefer container that accepts the codec.
        if codec in ("prores_ks", "prores_videotoolbox", "cfhd", "dnxhd"):
            path = path.with_suffix(".mov")
        elif codec == "libx265":
            path = path.with_suffix(".mkv")
        rgb = np.full((h, w, 3), value, dtype=np.uint16)
        _encode(path, codec, pix_fmt, options, w, h, rgb=rgb, n_frames=4)
        probe = av.open(str(path))
        frames = list(probe.decode(video=0))
        probe.close()
        assert frames
        out = (
            frames[min(1, len(frames) - 1)].reformat(format="rgb48le").to_ndarray(format="rgb48le")
        )
        crop = out[h // 4 : 3 * h // 4, w // 4 : 3 * w // 4].astype(np.float64)
        return float(crop.mean())

    def test_prores_ks_4444_is_ten_bit_not_twelve(self, tmp_path: Path):
        """prores_ks 4444 collapses B vs B+32 — 10-bit encode despite 12-bit probe labels."""
        opts = {"profile": "4", "vendor": "apl0"}
        m0 = self._mean_after_roundtrip(tmp_path, self.BASE, "prores_ks", "yuva444p10le", opts)
        m1 = self._mean_after_roundtrip(
            tmp_path, self.BASE + self.MID, "prores_ks", "yuva444p10le", opts
        )
        assert abs(m1 - m0) < 1.0, f"expected 10-bit collapse, got delta={m1 - m0}"

    def test_prores_ks_xq_is_ten_bit(self, tmp_path: Path):
        opts = {"profile": "5", "vendor": "apl0"}
        m0 = self._mean_after_roundtrip(tmp_path, self.BASE, "prores_ks", "yuva444p10le", opts)
        m1 = self._mean_after_roundtrip(
            tmp_path, self.BASE + self.MID, "prores_ks", "yuva444p10le", opts
        )
        assert abs(m1 - m0) < 1.0, f"expected 10-bit collapse, got delta={m1 - m0}"

    def test_prores_ks_rejects_twelve_bit_pix_fmt(self, tmp_path: Path):
        """Encoder does not accept yuva444p12le — open fails or rewrites."""
        path = tmp_path / "force12.mov"
        with pytest.raises((av.error.FFmpegError, ValueError, Exception)):
            _encode(
                path,
                "prores_ks",
                "yuva444p12le",
                {"profile": "4", "vendor": "apl0"},
                64,
                36,
            )

    def test_hevc_12_preserves_mid_bin(self, tmp_path: Path):
        opts = {"crf": "0", "preset": "ultrafast", "x265-params": "lossless=1"}
        m0 = self._mean_after_roundtrip(tmp_path, self.BASE, "libx265", "yuv420p12le", opts)
        m1 = self._mean_after_roundtrip(
            tmp_path, self.BASE + self.MID, "libx265", "yuv420p12le", opts
        )
        # 12-bit step in 16-bit is 16; mid-bin +32 must remain clearly non-zero.
        assert (m1 - m0) > 16, f"expected 12-bit mid-bin keep, delta={m1 - m0}"

    def test_hevc_10_collapses_mid_bin(self, tmp_path: Path):
        opts = {"crf": "0", "preset": "ultrafast", "x265-params": "lossless=1"}
        m0 = self._mean_after_roundtrip(tmp_path, self.BASE, "libx265", "yuv420p10le", opts)
        m1 = self._mean_after_roundtrip(
            tmp_path, self.BASE + self.MID, "libx265", "yuv420p10le", opts
        )
        assert abs(m1 - m0) < 1.0, f"expected 10-bit collapse, delta={m1 - m0}"

    def test_ffv1_12_preserves_mid_bin(self, tmp_path: Path):
        m0 = self._mean_after_roundtrip(
            tmp_path, self.BASE, "ffv1", "yuv444p12le", {"slicecrc": "1"}
        )
        m1 = self._mean_after_roundtrip(
            tmp_path, self.BASE + self.MID, "ffv1", "yuv444p12le", {"slicecrc": "1"}
        )
        assert (m1 - m0) > 16, f"expected 12-bit mid-bin keep, delta={m1 - m0}"

    def test_cineform_rgb_preserves_mid_bin(self, tmp_path: Path):
        m0 = self._mean_after_roundtrip(
            tmp_path, self.BASE, "cfhd", "gbrp12le", {"quality": "film3+"}
        )
        m1 = self._mean_after_roundtrip(
            tmp_path, self.BASE + self.MID, "cfhd", "gbrp12le", {"quality": "film3+"}
        )
        assert abs((m1 - m0) - 32.0) < 2.0, f"expected ~32 mid-bin, delta={m1 - m0}"

    @pytest.mark.skipif(sys.platform != "darwin", reason="VideoToolbox is macOS-only")
    def test_prores_vt_4444_keeps_beyond_ten_bit(self, tmp_path: Path):
        """Apple VT 4444 preserves mid-bin (unlike prores_ks)."""
        opts = {"profile": "4"}
        try:
            m0 = self._mean_after_roundtrip(
                tmp_path,
                self.BASE,
                "prores_videotoolbox",
                "ayuv64le",
                opts,
                w=192,
                h=108,
            )
            m1 = self._mean_after_roundtrip(
                tmp_path,
                self.BASE + self.MID,
                "prores_videotoolbox",
                "ayuv64le",
                opts,
                w=192,
                h=108,
            )
        except av.error.FFmpegError as e:
            pytest.skip(f"prores_videotoolbox unavailable: {e}")
        # Lossy HW: not exact 32, but clearly not collapsed to 0.
        assert (m1 - m0) > 12, f"expected VT 12-bit-class keep, delta={m1 - m0}"


def _encode(
    path: Path,
    codec: str,
    pix_fmt: str,
    options: dict[str, str],
    w: int,
    h: int,
    *,
    rgb: np.ndarray | None = None,
    n_frames: int = 1,
) -> None:
    container = av.open(str(path), mode="w")
    stream = container.add_stream(codec, rate=24)
    stream.width = w
    stream.height = h
    stream.pix_fmt = pix_fmt
    stream.options = options
    if rgb is None:
        rgb = np.full((h, w, 3), 22000, dtype=np.uint16)
    for _ in range(n_frames):
        vf = av.VideoFrame.from_ndarray(rgb, format="rgb48le").reformat(format=pix_fmt)
        for packet in stream.encode(vf):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()
