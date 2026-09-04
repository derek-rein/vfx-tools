"""PyAV RGB convert fallback for YUV frames tagged with RGB colorspace."""

from __future__ import annotations

import numpy as np
import pytest

av = pytest.importorskip("av")

from src.core.video import av_frame_to_rgb_ndarray  # noqa: E402


def _yuv422p10_rgb_matrix_frame(*, width: int = 32, height: int = 32):
    """Build a YUV422P10 frame whose colour tags make libswscale reject RGB."""
    rgb = np.full((height, width, 3), 128, dtype=np.uint8)
    src = av.VideoFrame.from_ndarray(rgb, format="rgb24")
    yuv = src.reformat(format="yuv422p10le")
    yuv.colorspace = 0  # AVCOL_SPC_RGB
    yuv.color_primaries = 0
    yuv.color_trc = 0
    yuv.color_range = 1
    return yuv


def test_direct_rgb24_raises_on_yuv_with_rgb_colorspace() -> None:
    frame = _yuv422p10_rgb_matrix_frame()
    with pytest.raises(OSError):
        frame.to_ndarray(format="rgb24")


def test_av_frame_to_rgb_ndarray_recovers_rgb24() -> None:
    frame = _yuv422p10_rgb_matrix_frame()
    rgb = av_frame_to_rgb_ndarray(frame, pix_fmt="rgb24")
    assert rgb.shape == (32, 32, 3)
    assert rgb.dtype == np.uint8
    assert rgb.mean() > 1


def test_av_frame_to_rgb_ndarray_recovers_rgb48le() -> None:
    frame = _yuv422p10_rgb_matrix_frame()
    rgb = av_frame_to_rgb_ndarray(frame, pix_fmt="rgb48le")
    assert rgb.shape == (32, 32, 3)
    assert rgb.dtype == np.uint16
    assert rgb.mean() > 1
