"""Unit tests for optional R3D / N-RAW support (no proprietary SDK required)."""

from __future__ import annotations

import sys
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


def test_bridge_candidates_include_exe_r3d_dir(monkeypatch, tmp_path: Path) -> None:
    """Packaged apps place libr3d_bridge next to the binary under r3d/."""
    from src.core import r3d as r3d_mod

    fake_exe = tmp_path / "MacOS" / "exr_converter"
    fake_exe.parent.mkdir(parents=True)
    fake_exe.write_bytes(b"")
    # Use the platform-native bridge filename (Linux CI looks for .so, not .dylib).
    bridge_name = r3d_mod._bridge_names()[0]
    bridge = tmp_path / "MacOS" / "r3d" / bridge_name
    bridge.parent.mkdir(parents=True)
    bridge.write_bytes(b"")

    monkeypatch.setattr(sys, "executable", str(fake_exe))
    monkeypatch.setattr(sys, "argv", [str(fake_exe)])
    # Simulate Nuitka not setting sys.frozen.
    if hasattr(sys, "frozen"):
        monkeypatch.delattr(sys, "frozen", raising=False)

    cands = r3d_mod._bridge_candidates()
    assert any(p.name == bridge_name and "r3d" in p.parts for p in cands)
    assert bridge.resolve() in {p.resolve() for p in cands if p.exists()}


def _force_r3d_unavailable() -> tuple:
    """Return previous r3d native state after forcing unavailable."""
    from src.core.r3d import native as native_mod

    prev = (
        native_mod._init_attempted,
        native_mod._init_ok,
        native_mod._init_error,
        native_mod._lib,
    )
    native_mod._init_attempted = True
    native_mod._init_ok = False
    native_mod._init_error = "test: bridge missing"
    native_mod._lib = None
    return prev


def _restore_r3d_state(prev: tuple) -> None:
    from src.core.r3d import native as native_mod

    (
        native_mod._init_attempted,
        native_mod._init_ok,
        native_mod._init_error,
        native_mod._lib,
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
