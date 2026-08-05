"""Unit tests for :mod:`src.render.watermark` overlay rendering."""

from __future__ import annotations

import numpy as np

from src.render.watermark import render_watermark_overlay


class TestRenderWatermark:
    def test_shape_and_dtype(self, qapp):
        out = render_watermark_overlay(64, 64, {"enabled": True, "text": "WIP"})
        assert out.shape == (64, 64, 4)
        assert out.dtype == np.uint8

    def test_disabled_is_transparent(self, qapp):
        out = render_watermark_overlay(64, 64, {"enabled": False, "text": "WIP"})
        assert out[..., 3].max() == 0

    def test_blank_text_is_transparent(self, qapp):
        out = render_watermark_overlay(64, 64, {"enabled": True, "text": "   "})
        assert out[..., 3].max() == 0

    def test_enabled_with_text_is_visible(self, qapp):
        out = render_watermark_overlay(
            256, 256, {"enabled": True, "text": "FOR REVIEW ONLY", "opacity": 80}
        )
        assert out[..., 3].max() > 0

    def test_tiled_covers_more_than_single(self, qapp):
        base = {"enabled": True, "text": "WIP", "opacity": 80, "size_pct": 6.0}
        single = render_watermark_overlay(256, 256, {**base, "tiled": False})
        tiled = render_watermark_overlay(256, 256, {**base, "tiled": True})
        single_cover = int((single[..., 3] > 0).sum())
        tiled_cover = int((tiled[..., 3] > 0).sum())
        # Tiling stamps the text across the frame, so it must mark more pixels
        # than a single centred line.  Use a soft factor — font metrics differ
        # across platforms (Windows CI paints thicker glyphs).
        assert tiled_cover > single_cover
        assert tiled_cover > single_cover * 1.5

    def test_tiled_reaches_all_quadrants(self, qapp):
        out = render_watermark_overlay(
            256, 256, {"enabled": True, "text": "WIP", "opacity": 80, "tiled": True}
        )
        alpha = out[..., 3]
        h, w = alpha.shape
        # Each corner quadrant should receive ink somewhere.
        assert alpha[: h // 2, : w // 2].max() > 0
        assert alpha[: h // 2, w // 2 :].max() > 0
        assert alpha[h // 2 :, : w // 2].max() > 0
        assert alpha[h // 2 :, w // 2 :].max() > 0
