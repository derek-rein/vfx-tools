"""Unit tests for EXR I/O helpers — generate frames under tmp_path, clean up after."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import OpenImageIO as oiio
import pytest

from src.core.exr_io import read_exr, read_exr_safe, write_exr


def _solid(h: int, w: int, rgb=(0.2, 0.4, 0.6)) -> np.ndarray:
    arr = np.zeros((h, w, 3), dtype=np.float32)
    arr[..., 0] = rgb[0]
    arr[..., 1] = rgb[1]
    arr[..., 2] = rgb[2]
    return arr


class TestWriteReadRoundTrip:
    def test_round_trip_zip(self, tmp_path: Path):
        path = tmp_path / "plate.1001.exr"
        src = _solid(16, 32, (0.1, 0.5, 0.9))
        write_exr(str(path), src, compression="zip", dst_space="ACEScg")
        assert path.is_file()
        got = read_exr(str(path))
        assert got.shape == (16, 32, 3)
        np.testing.assert_allclose(got, src, atol=2e-3)

    def test_dwa_level_attribute_written(self, tmp_path: Path):
        path = tmp_path / "dwa.exr"
        write_exr(
            str(path),
            _solid(8, 8),
            compression="dwaa",
            exr_opts={"dwa_compression_level": "12.5"},
        )
        inp = oiio.ImageInput.open(str(path))
        assert inp is not None
        level = inp.spec().getattribute("openexr:dwaCompressionLevel")
        inp.close()
        assert level is not None
        assert abs(float(level) - 12.5) < 0.01

    def test_write_metadata(self, tmp_path: Path):
        path = tmp_path / "meta.exr"
        write_exr(
            str(path),
            _solid(4, 4),
            compression="zip",
            src_space="sRGB",
            dst_space="ACEScg",
        )
        inp = oiio.ImageInput.open(str(path))
        spec = inp.spec()
        # Custom tags always survive; oiio:ColorSpace may be remapped by OIIO.
        assert str(spec.getattribute("exrconverter:srcColorSpace")) == "sRGB"
        assert str(spec.getattribute("exrconverter:dstColorSpace")) == "ACEScg"
        inp.close()


class TestReadExrErrors:
    def test_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(RuntimeError, match="Failed to open|Failed to read"):
            read_exr(str(tmp_path / "nope.exr"))

    def test_corrupt_file_raises(self, tmp_path: Path):
        path = tmp_path / "corrupt.exr"
        path.write_bytes(b"not an exr file at all")
        with pytest.raises(RuntimeError):
            read_exr(str(path))


class TestReadExrSafe:
    def test_ok_match(self, tmp_path: Path):
        path = tmp_path / "ok.exr"
        write_exr(str(path), _solid(10, 20), compression="zip")
        got = read_exr_safe(str(path), 20, 10)
        assert got.shape == (10, 20, 3)

    def test_mismatch_returns_black_of_requested_size(self, tmp_path: Path):
        path = tmp_path / "small.exr"
        write_exr(str(path), _solid(10, 20), compression="zip")
        got = read_exr_safe(str(path), 64, 48)
        assert got.shape == (48, 64, 3)
        assert float(got.max()) == 0.0

    def test_missing_returns_black(self, tmp_path: Path):
        got = read_exr_safe(str(tmp_path / "missing.exr"), 16, 8)
        assert got.shape == (8, 16, 3)
        assert float(got.sum()) == 0.0
