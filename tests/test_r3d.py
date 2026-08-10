"""Unit tests for optional R3D / N-RAW support (no proprietary SDK required)."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.core.r3d import (
    DECODE_FULL_PREMIUM,
    DECODE_HALF_PREMIUM,
    DECODE_QUARTER_GOOD,
    R3D_SUFFIXES,
    decode_mode_for_scale,
    is_r3d_path,
    r3d_src_colorspace_candidates,
)


def test_is_r3d_path_extensions() -> None:
    assert is_r3d_path("clip.R3D")
    assert is_r3d_path("clip.r3d")
    assert is_r3d_path(Path("/tmp/DSC_0001.NEV"))
    assert is_r3d_path("shot.nev")
    assert not is_r3d_path("plate.mov")
    assert not is_r3d_path("seq.####.exr")


def test_r3d_suffixes_set() -> None:
    assert ".r3d" in R3D_SUFFIXES
    assert ".nev" in R3D_SUFFIXES


def test_decode_mode_for_scale() -> None:
    assert decode_mode_for_scale(1.0) == DECODE_FULL_PREMIUM
    assert decode_mode_for_scale(0.5) == DECODE_HALF_PREMIUM
    assert decode_mode_for_scale(0.25) == DECODE_QUARTER_GOOD


def test_src_colorspace_candidates_include_log3g10() -> None:
    cands = r3d_src_colorspace_candidates("x.R3D")
    assert any("Log3G10" in c or "log3g10" in c.lower() for c in cands)
    assert cands[0]


def test_red_notice_is_nonempty() -> None:
    from src.core.r3d import RED_REDISTRIBUTABLE_NOTICE

    assert "RED" in RED_REDISTRIBUTABLE_NOTICE
    assert "reverse engineer" in RED_REDISTRIBUTABLE_NOTICE.lower()


def _force_r3d_unavailable() -> tuple:
    """Return previous r3d module state after forcing unavailable."""
    from src.core import r3d as r3d_mod

    prev = (
        r3d_mod._init_attempted,
        r3d_mod._init_ok,
        r3d_mod._init_error,
        r3d_mod._lib,
    )
    r3d_mod._init_attempted = True
    r3d_mod._init_ok = False
    r3d_mod._init_error = "test: bridge missing"
    r3d_mod._lib = None
    return prev


def _restore_r3d_state(prev: tuple) -> None:
    from src.core import r3d as r3d_mod

    (
        r3d_mod._init_attempted,
        r3d_mod._init_ok,
        r3d_mod._init_error,
        r3d_mod._lib,
    ) = prev


def test_probe_r3d_without_bridge_raises() -> None:
    """When the bridge is missing, probe must not silently return bogus dims."""
    prev = _force_r3d_unavailable()
    try:
        from src.core.video import probe_video

        with pytest.raises(RuntimeError):
            probe_video("/tmp/nonexistent_clip_for_test.R3D")
    finally:
        _restore_r3d_state(prev)


def test_video_suffixes_include_r3d() -> None:
    from src.core.video import _VIDEO_SUFFIXES

    assert ".r3d" in _VIDEO_SUFFIXES
    assert ".nev" in _VIDEO_SUFFIXES


def test_run_video_to_exr_r3d_missing_sdk(tmp_path: Path) -> None:
    from src.core.convert import run_video_to_exr
    from src.core.r3d import R3DUnavailableError

    prev = _force_r3d_unavailable()
    try:
        fake = tmp_path / "clip.R3D"
        fake.write_bytes(b"not a real r3d")

        with pytest.raises(R3DUnavailableError):
            run_video_to_exr(
                str(fake),
                tmp_path / "out",
                ocio_cfg=None,
                src_space="ACEScg",
                dst_space="ACEScg",
                config_source="",
                config_path="",
            )
    finally:
        _restore_r3d_state(prev)
