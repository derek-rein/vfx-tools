"""Unit tests for sequence/video color resolve and stream_fps."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace


class TestStreamFps:
    def test_average_rate_wins(self):
        from src.core.video import stream_fps

        stream = SimpleNamespace(
            average_rate=24.0,
            base_rate=30.0,
            guessed_rate=None,
            codec_context=SimpleNamespace(rate=60.0),
        )
        assert stream_fps(stream) == 24.0

    def test_falls_through_to_codec_rate(self):
        from src.core.video import stream_fps

        stream = SimpleNamespace(
            average_rate=0,
            base_rate=None,
            guessed_rate=None,
            codec_context=SimpleNamespace(rate=29.97),
        )
        assert abs(stream_fps(stream) - 29.97) < 1e-6

    def test_zero_when_nothing(self):
        from src.core.video import stream_fps

        stream = SimpleNamespace(
            average_rate=None,
            base_rate=None,
            guessed_rate=None,
            codec_context=SimpleNamespace(rate=None),
        )
        assert stream_fps(stream) == 0.0


class TestResolveSequenceSrcColorspace:
    def test_preferred_valid_wins(self):
        import PyOpenColorIO as OCIO

        from src.core.sequence import resolve_sequence_src_colorspace

        cfg = OCIO.Config.CreateFromFile(str(next(Path("resources").rglob("*.ocio"))))
        hit = resolve_sequence_src_colorspace("", cfg, preferred="ACEScg")
        assert hit
        assert "ACES" in hit or "ACEScg" in hit or hit == "ACEScg"

    def test_unmapped_probe_not_returned(self, monkeypatch, tmp_path):
        import PyOpenColorIO as OCIO

        from src.core import sequence as seq_mod
        from src.core.ocio_utils import find_equivalent_space

        cfg = OCIO.Config.CreateFromFile(str(next(Path("resources").rglob("*.ocio"))))
        bad = "lin_rec709_not_in_config_xyz"
        monkeypatch.setattr(seq_mod, "probe_pixel_colorspace", lambda _p: bad)
        hit = seq_mod.resolve_sequence_src_colorspace(
            str(tmp_path / "x.exr"), cfg, preferred=""
        )
        # Must be config-valid (fallback ACEScg/scene_linear), never the raw tag.
        assert hit
        assert bad not in hit
        assert find_equivalent_space(cfg, hit)


class TestAppSettingsDefaults:
    def test_post_convert_defaults(self, tmp_path, qapp):
        from src.services.app_settings import make_ini_settings

        s = make_ini_settings(str(tmp_path / "d.ini"))
        assert s.get_bool("missing_true", True) is True
        assert s.get_bool("missing_false", False) is False
        assert s.copy_path_after() is True  # default when key absent
        assert s.open_after() is False
        assert s.show_folder_after() is False
