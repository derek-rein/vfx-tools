"""oxideav-prores PyO3 extension — true 12-bit ProRes-compatible MOV."""

from __future__ import annotations

from pathlib import Path

import av
import numpy as np
import pytest

from src.core.constants import OXIDEAV_PRORES_KEYS, available_video_codecs, video_codec_by_key
from src.core.oxideav_prores import (
    extension_version,
    is_available,
    open_writer,
    profile_for_codec_key,
    write_rgb48_frame,
)

pytestmark = pytest.mark.skipif(
    not is_available(),
    reason="exr_prores extension not built (make oxideav-prores)",
)


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

    def test_profile_mapping(self):
        assert profile_for_codec_key("prores_ox_4444") == "4444"
        assert profile_for_codec_key("prores_ox_xq") == "xq"

    def test_extension_version_nonempty(self):
        assert extension_version()


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


@pytest.mark.integration
class TestOxideavBitDepth:
    """YUV-domain mid-bin: +32 on a 12-bit lattice must survive encode/decode."""

    W, H = 128, 128
    BASE = 2048
    MID = 32

    def _y_mean(self, path: Path) -> list[float]:
        probe = av.open(str(path))
        means: list[float] = []
        for frame in probe.decode(video=0):
            y = np.frombuffer(bytes(frame.planes[0]), dtype="<u2")
            stride = frame.planes[0].line_size // 2
            rows = len(y) // stride
            yimg = y.reshape(rows, stride)[: self.H, : self.W]
            cy, cx = self.H // 2, self.W // 2
            means.append(float(yimg[cy - 16 : cy + 16, cx - 16 : cx + 16].mean()))
        probe.close()
        return means

    def test_yuv_midbin_kept(self, tmp_path: Path):
        import exr_prores

        out = tmp_path / "midbin.mov"
        with exr_prores.ProResMovWriter(str(out), self.W, self.H, 24, 1, profile="4444") as writer:
            for yv in (self.BASE, self.BASE + self.MID):
                y = np.full((self.H, self.W), yv, dtype=np.uint16)
                c = np.full((self.H, self.W), 2048, dtype=np.uint16)
                writer.write_yuv444_p12(y, c, c)
        means = self._y_mean(out)
        assert len(means) == 2
        delta = means[1] - means[0]
        assert delta > 12, f"12-bit mid-bin collapsed: means={means} delta={delta}"
