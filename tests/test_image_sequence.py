"""Unit tests for multi-format image sequence discovery and still I/O."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import OpenImageIO as oiio
import pytest

from src.core.constants import IMAGE_SEQUENCE_EXTS, is_image_sequence_ext
from src.core.exr_io import read_image, write_exr
from src.core.sequence import (
    find_exr_sequence,
    find_exr_sequence_info,
    looks_like_sequence_pattern,
    parse_dot_sequence_output,
    scan_exr_sequences,
    sequence_looks_scene_referred,
    sequence_pattern_stem,
)


def _write_rgb_still(path: Path, rgb: np.ndarray) -> None:
    """Write an 8-bit RGB still via OIIO (png/jpg/webp)."""
    h, w = rgb.shape[:2]
    arr = np.clip(rgb, 0.0, 1.0).astype(np.float32)
    spec = oiio.ImageSpec(w, h, 3, oiio.UINT8)
    buf = oiio.ImageBuf(spec)
    buf.set_pixels(oiio.ROI(0, w, 0, h, 0, 1, 0, 3), arr)
    ok = buf.write(str(path))
    assert ok, buf.geterror() or f"failed to write {path}"


def _write_still_seq(
    directory: Path,
    *,
    stem: str = "plate",
    ext: str = ".png",
    frames: tuple[int, ...] = (1001, 1002, 1003),
    width: int = 16,
    height: int = 8,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for i, fnum in enumerate(frames):
        val = 0.2 + 0.2 * i
        rgb = np.full((height, width, 3), val, dtype=np.float32)
        _write_rgb_still(directory / f"{stem}.{fnum:04d}{ext}", rgb)
    return directory


class TestImageSequenceExts:
    def test_constants_include_png_jpg(self) -> None:
        assert ".exr" in IMAGE_SEQUENCE_EXTS
        assert ".dpx" in IMAGE_SEQUENCE_EXTS
        assert ".png" in IMAGE_SEQUENCE_EXTS
        assert ".jpg" in IMAGE_SEQUENCE_EXTS
        assert ".jpeg" in IMAGE_SEQUENCE_EXTS
        assert ".webp" in IMAGE_SEQUENCE_EXTS
        assert ".bmp" not in IMAGE_SEQUENCE_EXTS
        assert ".gif" not in IMAGE_SEQUENCE_EXTS
        assert ".tga" not in IMAGE_SEQUENCE_EXTS
        assert is_image_sequence_ext("PNG")
        assert is_image_sequence_ext(".dpx")
        assert is_image_sequence_ext(".jpg")
        assert not is_image_sequence_ext(".mp4")
        assert not is_image_sequence_ext(".gif")


class TestStillIO:
    def test_read_png(self, tmp_path: Path) -> None:
        path = tmp_path / "f.png"
        src = np.zeros((6, 10, 3), dtype=np.float32)
        src[..., 0] = 1.0
        src[..., 1] = 0.5
        _write_rgb_still(path, src)
        got = read_image(str(path))
        assert got.shape == (6, 10, 3)
        assert got.dtype == np.float32
        assert float(got[..., 0].mean()) > 0.9

    def test_read_jpeg(self, tmp_path: Path) -> None:
        path = tmp_path / "f.jpg"
        src = np.full((8, 12, 3), 0.6, dtype=np.float32)
        _write_rgb_still(path, src)
        got = read_image(str(path))
        assert got.shape == (8, 12, 3)
        # JPEG is lossy — just check non-black
        assert float(got.mean()) > 0.3


class TestSequenceDiscovery:
    @pytest.mark.parametrize("ext", [".png", ".jpg", ".webp", ".dpx"])
    def test_find_still_sequence(self, tmp_path: Path, ext: str) -> None:
        d = _write_still_seq(tmp_path / "seq", ext=ext, frames=(1, 2, 3))
        paths, basename = find_exr_sequence(str(d))
        assert len(paths) == 3
        assert basename == "plate"
        assert all(Path(p).suffix.lower() == ext for p in paths)

    def test_find_from_single_frame(self, tmp_path: Path) -> None:
        d = _write_still_seq(tmp_path / "seq", ext=".png", frames=(10, 11, 12))
        paths, _ = find_exr_sequence(str(d / "plate.0011.png"))
        assert len(paths) == 3

    def test_exr_preferred_over_png_in_mixed_folder(self, tmp_path: Path) -> None:
        d = tmp_path / "mixed"
        d.mkdir()
        for fnum in (1, 2):
            write_exr(
                str(d / f"beauty.{fnum:04d}.exr"),
                np.full((8, 12, 3), 0.4, dtype=np.float32),
                compression="zip",
            )
            _write_rgb_still(
                d / f"proxy.{fnum:04d}.png",
                np.full((8, 12, 3), 0.7, dtype=np.float32),
            )
        paths, basename = find_exr_sequence(str(d))
        assert basename == "beauty"
        assert all(Path(p).suffix.lower() == ".exr" for p in paths)

    def test_scan_includes_extension(self, tmp_path: Path) -> None:
        _write_still_seq(tmp_path / "s", ext=".png", frames=(1, 2))
        rows = scan_exr_sequences(str(tmp_path / "s"))
        assert len(rows) == 1
        assert rows[0]["extension"] == ".png"
        assert rows[0]["frames"] == 2

    def test_sequence_looks_scene_referred(self, tmp_path: Path) -> None:
        exr_d = tmp_path / "exr"
        exr_d.mkdir()
        write_exr(
            str(exr_d / "a.0001.exr"),
            np.full((4, 4, 3), 0.2, dtype=np.float32),
            compression="zip",
        )
        png_d = _write_still_seq(tmp_path / "png", ext=".png", frames=(1, 2))
        dpx_d = _write_still_seq(tmp_path / "dpx", ext=".dpx", frames=(1, 2))
        assert sequence_looks_scene_referred(str(exr_d)) is True
        assert sequence_looks_scene_referred(str(dpx_d)) is True
        assert sequence_looks_scene_referred(str(png_d)) is False
        assert sequence_looks_scene_referred(str(png_d / "plate.0001.png")) is False

    def test_find_info_returns_frames(self, tmp_path: Path) -> None:
        d = _write_still_seq(tmp_path / "s", ext=".png", frames=(1001, 1002))
        paths, name, frames, pad, seq = find_exr_sequence_info(str(d))
        assert len(paths) == 2
        assert name == "plate"
        assert frames == [1001, 1002]
        assert pad >= 4
        assert seq.extension().lower() == ".png"

    def test_underscore_sequences_discovered(self, tmp_path: Path) -> None:
        """Reads accept both name.####.ext and name_####.ext pads."""
        d = tmp_path / "mix"
        d.mkdir()
        for fnum in (1, 2, 3):
            write_exr(
                str(d / f"good.{fnum:04d}.exr"),
                np.full((4, 4, 3), 0.3, dtype=np.float32),
                compression="zip",
            )
            write_exr(
                str(d / f"also_{fnum:05d}.exr"),
                np.full((4, 4, 3), 0.9, dtype=np.float32),
                compression="zip",
            )
        rows = scan_exr_sequences(str(d))
        names = {r["name"] for r in rows}
        assert names == {"good", "also"}
        # Directory default: first by basename when neither matches folder name.
        paths, basename = find_exr_sequence(str(d))
        assert basename in names
        assert len(paths) == 3
        # Selecting an underscore frame resolves that sequence fully.
        paths_u, name_u, frames_u, _pad, _seq = find_exr_sequence_info(str(d / "also_00001.exr"))
        assert name_u == "also"
        assert frames_u == [1, 2, 3]
        assert all("also_" in p for p in paths_u)

    def test_folder_name_prefers_matching_sequence(self, tmp_path: Path) -> None:
        """Folder ``shot`` prefers ``shot_####`` over ``shot-alt.####``."""
        d = tmp_path / "shot"
        d.mkdir()
        for fnum in (1001, 1002):
            write_exr(
                str(d / f"shot-alt.{fnum:04d}.exr"),
                np.full((4, 4, 3), 0.2, dtype=np.float32),
                compression="zip",
            )
            write_exr(
                str(d / f"shot_{fnum:05d}.exr"),
                np.full((4, 4, 3), 0.8, dtype=np.float32),
                compression="zip",
            )
        paths, name, frames, _pad, seq = find_exr_sequence_info(str(d))
        assert name == "shot"
        assert frames == [1001, 1002]
        assert all(Path(p).name.startswith("shot_") for p in paths)
        assert str(seq.basename()).endswith("_")

    def test_parse_dot_sequence_output(self) -> None:
        d, name, pad = parse_dot_sequence_output("/tmp/out/04_5d-2.####.exr")
        # Path.parent is OS-native separators on Windows.
        assert Path(d) == Path("/tmp/out")
        assert name == "04_5d-2"
        assert pad == 4
        d2, name2, pad2 = parse_dot_sequence_output("/tmp/out")
        assert Path(d2) == Path("/tmp/out")
        assert name2 is None
        assert pad2 is None
        with pytest.raises(ValueError, match="dot-separated"):
            parse_dot_sequence_output("/tmp/out/shot_####.exr")


class TestNukeStylePatterns:
    def test_pattern_stem_hash_and_printf(self) -> None:
        assert sequence_pattern_stem("chs_010_010_v0001.####.exr") == "chs_010_010_v0001"
        assert sequence_pattern_stem("plate_####.exr") == "plate"
        assert sequence_pattern_stem("plate.%04d.exr") == "plate"
        assert looks_like_sequence_pattern("/show/shot/chs_010_010_v0001.####.exr")
        assert not looks_like_sequence_pattern("/show/shot/readme.txt")

    def test_resolve_hash_pattern_to_sequence(self, tmp_path: Path) -> None:
        d = tmp_path / "footage"
        d.mkdir()
        for f in (1001, 1002, 1003):
            write_exr(
                str(d / f"chs_010_010_v0001.{f:04d}.exr"),
                np.zeros((4, 4, 3), dtype=np.float32),
                compression="none",
            )
        # Unrelated sequence in same folder
        write_exr(
            str(d / "other.0001.exr"),
            np.zeros((4, 4, 3), dtype=np.float32),
            compression="none",
        )
        pattern = str(d / "chs_010_010_v0001.####.exr")
        paths, name, frames, pad, _seq = find_exr_sequence_info(pattern)
        assert name == "chs_010_010_v0001"
        assert frames == [1001, 1002, 1003]
        assert pad == 4
        assert len(paths) == 3
        assert paths[0].endswith("chs_010_010_v0001.1001.exr")

        paths2, name2 = find_exr_sequence(pattern)
        assert name2 == "chs_010_010_v0001"
        assert len(paths2) == 3
