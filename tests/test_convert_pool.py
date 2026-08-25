"""Unit tests for convert pool helpers and cancel typing."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

from src.core.constants import OCIO_SOURCE_FILE
from src.core.convert import (
    _READY_BYTES_BUDGET,
    _array_nbytes,
    default_codec_opts,
)
from src.core.errors import ConversionCancelled
from src.core.ocio_utils import get_bundled_aces_studio_path
from src.core.pool import process_frame_v2e
from tests.support.integration import E2V_DST, E2V_SRC, V2E_DST, V2E_SRC


def test_array_nbytes_none_and_array() -> None:
    assert _array_nbytes(None) == 0
    arr = np.zeros((4, 4, 3), dtype=np.float32)
    assert _array_nbytes(arr) == arr.nbytes


def test_ready_bytes_budget_is_positive() -> None:
    assert _READY_BYTES_BUDGET >= 64 * 1024 * 1024


def test_default_codec_opts_public_alias() -> None:
    assert default_codec_opts("h264")["crf"] == "18"


def test_conversion_cancelled_message() -> None:
    exc = ConversionCancelled()
    assert str(exc) == "Cancelled"
    assert isinstance(exc, Exception)


def test_process_frame_v2e_thread_pool(tmp_path) -> None:
    """Video→EXR worker runs correctly under ThreadPoolExecutor (no pickling)."""
    path = get_bundled_aces_studio_path()
    if path is None:
        pytest.skip("bundled ACES Studio config not present")

    rgb = np.full((8, 8, 3), 0.25, dtype=np.float32)
    out = tmp_path / "frame.1001.exr"

    with ThreadPoolExecutor(max_workers=2) as pool:
        fut = pool.submit(
            process_frame_v2e,
            1,
            rgb,
            str(out),
            "zip",
            OCIO_SOURCE_FILE,
            str(path),
            V2E_SRC,
            V2E_DST,
            None,
            None,
        )
        assert fut.result() == 1

    assert out.is_file()
    assert out.stat().st_size > 0
    # Keep color-space constants referenced so import errors surface early.
    assert E2V_SRC and E2V_DST
