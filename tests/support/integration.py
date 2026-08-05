"""Helpers for end-to-end conversion integration tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import av
import numpy as np
import OpenImageIO as oiio

from src.core.exr_io import write_exr
from src.core.sequence import find_exr_sequence
from src.core.video import probe_video

FIXTURES_ROOT = Path(__file__).resolve().parents[1] / "fixtures"
MANIFEST_PATH = FIXTURES_ROOT / "manifest.json"
MEDIA_ROOT = FIXTURES_ROOT / "media"

# Color spaces that exist in the bundled ACES Studio v4 config.
V2E_SRC = "sRGB Encoded Rec.709 (sRGB)"
V2E_DST = "ACEScg"
E2V_SRC = "ACEScg"
E2V_DST = "Rec.1886 Rec.709 - Display"


@dataclass(frozen=True)
class VideoFixture:
    id: str
    path: Path
    description: str
    expected_frames: int | None = None
    min_width: int = 1
    min_height: int = 1
    src: str | None = None
    dst: str | None = None


@dataclass(frozen=True)
class ExrSequenceFixture:
    id: str
    path: Path
    description: str
    frame_count: int | None = None
    src: str | None = None
    dst: str | None = None


def conversion_ocio_args(mode: str) -> list[str]:
    """Return ``--ocio/--src/--dst`` args appropriate for the runtime OCIO build."""
    import PyOpenColorIO as OCIO

    from src.core.ocio_utils import (
        get_bundled_aces_studio_path,
        get_working_space,
        resolve_ocio_for_cli,
    )

    bundled = get_bundled_aces_studio_path()
    if bundled is not None:
        try:
            OCIO.Config.CreateFromFile(str(bundled))
            return ["--ocio", str(bundled), *standard_color_args(mode)]
        except Exception:
            pass

    cfg = resolve_ocio_for_cli(None)
    working = get_working_space(cfg)
    if mode == "video2exr":
        for src in (
            "sRGB Encoded Rec.709 (sRGB)",
            "Gamma 2.2 Encoded Rec.709",
            "Linear Rec.709 (sRGB)",
        ):
            if cfg.getColorSpace(src) is not None:
                return ["--src", src, "--dst", working]
    else:
        for dst in ("Rec.1886 Rec.709 - Display", "sRGB - Display"):
            if cfg.getColorSpace(dst) is not None:
                return ["--src", working, "--dst", dst]

    raise RuntimeError(f"no usable OCIO color spaces for {mode}")


def resolve_exr_input(path: Path) -> str:
    """Return a CLI input path that :func:`find_exr_sequence` accepts."""
    if path.is_dir():
        return str(path)
    if path.is_file():
        return str(path)
    if "####" in str(path):
        parent = path.parent
        if parent.is_dir():
            return str(parent)
    raise FileNotFoundError(path)


def standard_color_args(mode: str) -> list[str]:
    if mode == "video2exr":
        return ["--src", V2E_SRC, "--dst", V2E_DST]
    if mode == "exr2video":
        return ["--src", E2V_SRC, "--dst", E2V_DST]
    raise ValueError(mode)


def converter_command(*args: str) -> list[str]:
    """Build argv for the converter CLI (source or bundled binary)."""
    bin_path = os.environ.get("EXR_CONVERTER_BIN")
    if bin_path:
        return [bin_path, *args]
    return [sys.executable, str(Path(__file__).resolve().parents[2] / "main.py"), *args]


def run_converter(*args: str, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    cmd = converter_command(*args)
    return subprocess.run(
        cmd,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def run_cli(
    mode: str,
    *args: str,
    workers: str = "1",
    timeout: int = 300,
) -> subprocess.CompletedProcess[str]:
    """Run a conversion subcommand with bundled OCIO + standard color spaces."""
    return run_converter(
        "--workers",
        workers,
        mode,
        *conversion_ocio_args(mode),
        *args,
        timeout=timeout,
    )


def run_cli_with_spaces(
    mode: str,
    *args: str,
    src: str | None = None,
    dst: str | None = None,
    workers: str = "1",
    timeout: int = 300,
) -> subprocess.CompletedProcess[str]:
    """Like :func:`run_cli` but allows per-fixture ``--src`` / ``--dst`` overrides."""
    ocio_args = conversion_ocio_args(mode)
    if src or dst:
        filtered: list[str] = []
        skip = False
        for token in ocio_args:
            if skip:
                skip = False
                continue
            if token in ("--src", "--dst"):
                skip = True
                continue
            filtered.append(token)
        ocio_args = filtered
        if src:
            ocio_args.extend(["--src", src])
        if dst:
            ocio_args.extend(["--dst", dst])
    return run_converter(
        "--workers",
        workers,
        mode,
        *ocio_args,
        *args,
        timeout=timeout,
    )


def load_manifest() -> tuple[list[VideoFixture], list[ExrSequenceFixture]]:
    if not MANIFEST_PATH.is_file():
        return [], []

    data = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    videos: list[VideoFixture] = []
    for entry in data.get("videos", []):
        rel = entry.get("path", "")
        if not rel:
            continue
        path = FIXTURES_ROOT / rel
        if not path.is_file():
            continue
        videos.append(
            VideoFixture(
                id=str(entry.get("id", path.stem)),
                path=path,
                description=str(entry.get("description", "")),
                expected_frames=entry.get("expected_frames"),
                min_width=int(entry.get("min_width", 1)),
                min_height=int(entry.get("min_height", 1)),
                src=entry.get("src"),
                dst=entry.get("dst"),
            )
        )

    exr_sequences: list[ExrSequenceFixture] = []
    for entry in data.get("exr_sequences", []):
        pattern = entry.get("path", "")
        if not pattern:
            continue
        path = FIXTURES_ROOT / pattern
        if "####" in pattern:
            glob_part = pattern.replace("####", "*")
            matches = sorted((FIXTURES_ROOT / glob_part).parent.glob(Path(glob_part).name))
            if not matches:
                continue
            path = FIXTURES_ROOT / pattern
        elif not path.is_file():
            continue
        exr_sequences.append(
            ExrSequenceFixture(
                id=str(entry.get("id", path.stem)),
                path=path,
                description=str(entry.get("description", "")),
                frame_count=entry.get("frame_count"),
                src=entry.get("src"),
                dst=entry.get("dst"),
            )
        )

    return videos, exr_sequences


def write_synthetic_video(
    path: Path,
    *,
    width: int = 64,
    height: int = 36,
    frames: int = 3,
    fps: int = 24,
) -> None:
    """Write a tiny H.264 clip suitable for CI conversion tests."""
    path.parent.mkdir(parents=True, exist_ok=True)
    container = av.open(str(path), mode="w")
    stream = container.add_stream("libx264", rate=fps)
    stream.width = width
    stream.height = height
    stream.pix_fmt = "yuv420p"
    stream.options = {"preset": "ultrafast", "crf": "23"}

    for i in range(frames):
        img = np.zeros((height, width, 3), dtype=np.uint8)
        img[:, :, 0] = (30 + i * 40) % 256
        img[:, :, 1] = 90
        img[:, :, 2] = (150 + i * 10) % 256
        frame = av.VideoFrame.from_ndarray(img, format="rgb24")
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode():
        container.mux(packet)
    container.close()


def write_synthetic_exr_sequence(
    directory: Path,
    *,
    stem: str = "plate",
    frames: tuple[int, ...] = (1001, 1002, 1003),
    width: int = 64,
    height: int = 36,
) -> Path:
    """Write a short linear EXR sequence and return the Nuke-style input spec."""
    directory.mkdir(parents=True, exist_ok=True)
    for frame_num in frames:
        rgb = np.zeros((height, width, 3), dtype=np.float32)
        rgb[:, :, 0] = 0.1 + frame_num * 0.001
        rgb[:, :, 1] = 0.25
        rgb[:, :, 2] = 0.4
        out = directory / f"{stem}.{frame_num:04d}.exr"
        write_exr(str(out), rgb, compression="zip", dst_space="ACEScg")
    return directory / f"{stem}.####.exr"


def assert_exr_sequence(
    directory: Path,
    *,
    stem: str,
    expected_frames: int,
    min_width: int,
    min_height: int,
) -> None:
    paths, basename = find_exr_sequence(str(directory))
    assert basename == stem
    assert len(paths) == expected_frames
    for path in paths:
        inp = oiio.ImageInput.open(path)
        assert inp is not None, path
        spec = inp.spec()
        inp.close()
        assert spec.width >= min_width, path
        assert spec.height >= min_height, path


def assert_video_output(
    path: Path,
    *,
    min_frames: int = 1,
    min_width: int = 1,
    min_height: int = 1,
) -> None:
    assert path.is_file(), path
    assert path.stat().st_size > 0, path
    w, h, _fps, total = probe_video(str(path))
    assert w >= min_width, path
    assert h >= min_height, path
    assert total >= min_frames, path
