"""Unit tests for sequence-browser thumbnail helper."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import OpenImageIO as oiio

from src.core.exr_io import write_exr
from src.gui.browser_thumbs import load_browser_thumbnail_rgb


def _write_png(path: Path, rgb: np.ndarray) -> None:
    h, w = rgb.shape[:2]
    arr = np.clip(rgb, 0.0, 1.0).astype(np.float32)
    spec = oiio.ImageSpec(w, h, 3, oiio.UINT8)
    buf = oiio.ImageBuf(spec)
    buf.set_pixels(oiio.ROI(0, w, 0, h, 0, 1, 0, 3), arr)
    assert buf.write(str(path)), buf.geterror()


class TestBrowserThumbs:
    def test_png_thumbnail(self, tmp_path: Path) -> None:
        path = tmp_path / "a.0001.png"
        src = np.zeros((64, 128, 3), dtype=np.float32)
        src[..., 0] = 1.0
        _write_png(path, src)
        thumb = load_browser_thumbnail_rgb(str(path), max_edge=32)
        assert thumb is not None
        assert thumb.dtype == np.uint8
        assert thumb.shape[2] == 3
        assert max(thumb.shape[0], thumb.shape[1]) <= 32
        assert float(thumb[..., 0].mean()) > 200

    def test_exr_thumbnail_not_black(self, tmp_path: Path) -> None:
        path = tmp_path / "a.0001.exr"
        # Linear mid-grey — without OETF this can look very dark on display.
        write_exr(
            str(path),
            np.full((48, 96, 3), 0.18, dtype=np.float32),
            compression="zip",
        )
        thumb = load_browser_thumbnail_rgb(str(path), max_edge=40)
        assert thumb is not None
        assert float(thumb.mean()) > 20  # visible after display curve

    def test_missing_file(self, tmp_path: Path) -> None:
        assert load_browser_thumbnail_rgb(str(tmp_path / "nope.exr")) is None
