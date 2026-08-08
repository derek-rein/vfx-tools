"""GPU OCIO preview helpers (no live GL context required)."""

from __future__ import annotations

import numpy as np
import PyOpenColorIO as OCIO
import pytest

from src.core import ocio_utils
from src.gui.ocio_gpu_plane import (
    _rewrite_sampler1d_to_2d,
    gpu_ocio_available,
    nuke_viewer_gamma_power,
)


def _load_bundled_aces_or_skip() -> OCIO.Config:
    """Load bundled ACES Studio; skip if missing or OCIO lib is too old (2.4)."""
    path = ocio_utils.get_bundled_aces_studio_path()
    if path is None or not path.is_file():
        pytest.skip("bundled ACES Studio config not present")
    ok, err = ocio_utils.is_ocio_config_loadable(path)
    if not ok:
        pytest.skip(f"bundled ACES Studio not loadable: {err}")
    return OCIO.Config.CreateFromFile(str(path))


def test_gpu_ocio_modules_importable() -> None:
    assert gpu_ocio_available() is True


def test_nuke_viewer_gamma_power() -> None:
    """Nuke γ is inverted: output = input ** (1/γ)."""
    assert nuke_viewer_gamma_power(1.0) == pytest.approx(1.0)
    assert nuke_viewer_gamma_power(2.0) == pytest.approx(0.5)
    assert nuke_viewer_gamma_power(0.5) == pytest.approx(2.0)
    # Midtone: γ>1 lightens (pow < 1 on [0,1]); γ<1 darkens.
    mid = 0.25
    assert mid ** nuke_viewer_gamma_power(2.0) > mid
    assert mid ** nuke_viewer_gamma_power(0.5) < mid


def test_nuke_gamma_after_display_not_ocio_ec() -> None:
    """At extreme γ, post-display power ≠ pre-display OCIO EC LINEAR gamma."""
    cfg = _load_bundled_aces_or_skip()
    working = ocio_utils.get_compositing_space(cfg)
    display = cfg.getDefaultDisplay()
    view = cfg.getDefaultView(display)

    lin = np.array([[0.18, 0.18, 0.18], [1.0, 1.0, 1.0]], dtype=np.float32)
    nuke_g = 0.01
    exp = nuke_viewer_gamma_power(nuke_g)

    # Display only, then Nuke power.
    dvt = OCIO.DisplayViewTransform()
    dvt.setSrc(working)
    dvt.setDisplay(display)
    dvt.setView(view)
    disp = lin.copy()
    cfg.getProcessor(dvt).getDefaultCPUProcessor().applyRGB(disp)
    nuke = np.power(np.maximum(disp, 0.0), exp)

    # Old wrong path: EC LINEAR gamma=1/γ before DVT.
    group = OCIO.GroupTransform()
    ec = OCIO.ExposureContrastTransform()
    ec.setStyle(OCIO.EXPOSURE_CONTRAST_LINEAR)
    ec.setGamma(exp)
    ec.setPivot(0.18)
    group.appendTransform(ec)
    group.appendTransform(dvt)
    wrong = lin.copy()
    cfg.getProcessor(group).getDefaultCPUProcessor().applyRGB(wrong)

    # Mid-gray must not stay near identity-display while Nuke crushes it.
    assert float(nuke[0, 0]) < 1e-3
    assert float(wrong[0, 0]) > 0.1  # EC+pivot leaves mid gray lit — the bug


def test_rewrite_sampler1d_handles_nested_parens() -> None:
    src = "uniform sampler1D fooSampler;\nfloat lo = texture(fooSampler, (i_lo + 0.5) / 363).r;\n"
    out = _rewrite_sampler1d_to_2d(src)
    assert "sampler1D" not in out
    assert "uniform sampler2D fooSampler" in out
    assert "vec2((i_lo + 0.5) / 363, 0.5)" in out


def test_viewer_gpu_shader_extracts_from_bundled_aces() -> None:
    cfg = _load_bundled_aces_or_skip()
    working = ocio_utils.get_compositing_space(cfg)
    display = cfg.getDefaultDisplay()
    view = cfg.getDefaultView(display)

    # Match production: gain (exposure) only in OCIO; gamma is post-display GLSL.
    group = OCIO.GroupTransform()
    ec = OCIO.ExposureContrastTransform()
    ec.setStyle(OCIO.EXPOSURE_CONTRAST_LINEAR)
    ec.setExposure(0.0)
    ec.setGamma(1.0)
    ec.setPivot(0.18)
    ec.makeExposureDynamic()
    group.appendTransform(ec)

    dvt = OCIO.DisplayViewTransform()
    dvt.setSrc(working)
    dvt.setDisplay(display)
    dvt.setView(view)
    group.appendTransform(dvt)

    proc = cfg.getProcessor(group)
    gpu = proc.getDefaultGPUProcessor()
    desc = OCIO.GpuShaderDesc.CreateShaderDesc(language=OCIO.GPU_LANGUAGE_GLSL_4_0)
    gpu.extractGpuShaderInfo(desc)
    text = desc.getShaderText()
    assert "OCIOMain" in text
    assert desc.hasDynamicProperty(OCIO.DYNAMIC_PROPERTY_EXPOSURE)
    assert not desc.hasDynamicProperty(OCIO.DYNAMIC_PROPERTY_GAMMA)
