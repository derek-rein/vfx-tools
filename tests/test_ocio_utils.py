"""Unit tests for :mod:`src.core.ocio_utils` colorspace resolution helpers.

These build a real OCIO builtin config (no external files) and skip cleanly
if the runtime OCIO has no usable builtin.
"""

from __future__ import annotations

import numpy as np
import pytest

from src.core import ocio_utils


@pytest.fixture(scope="module")
def config():
    """A small builtin OCIO config, or skip if the runtime can't provide one."""
    try:
        builtins = ocio_utils.list_builtin_configs()
    except Exception:  # pragma: no cover - defensive
        pytest.skip("OCIO builtin registry unavailable")
    if not builtins:
        pytest.skip("no OCIO builtin configs available")

    import PyOpenColorIO as OCIO

    last_err = None
    for name, _label, _rec in builtins:
        try:
            return OCIO.Config.CreateFromBuiltinConfig(name)
        except Exception as e:  # pragma: no cover - try next
            last_err = e
    pytest.skip(f"could not instantiate any builtin config: {last_err}")


class TestWorkingSpace:
    def test_resolves_scene_linear(self, config):
        ws = ocio_utils.get_working_space(config)
        assert isinstance(ws, str) and ws

    def test_overlay_authoring_space_resolves(self, config):
        space = ocio_utils.get_overlay_authoring_space(config)
        assert isinstance(space, str) and space

    def test_overlay_authoring_prefers_texture_paint_role(self, config):
        # When the config defines the ``texture_paint`` role, the overlay
        # authoring space must resolve to it (the idiomatic sRGB texture space).
        cs = config.getColorSpace("texture_paint")
        if cs is not None:
            assert ocio_utils.get_overlay_authoring_space(config) == cs.getName()

    def test_compositing_space_resolves(self, config):
        space = ocio_utils.get_compositing_space(config)
        assert isinstance(space, str) and space

    def test_compositing_space_prefers_ap0_on_aces(self, config):
        # On ACES configs the compositing space should be the AP0 reference
        # (ACES2065-1); on non-ACES configs it falls back to scene_linear.
        space = ocio_utils.get_compositing_space(config)
        if config.getColorSpace("aces_interchange") is not None:
            assert space == "ACES2065-1"
        else:
            assert space == ocio_utils.get_working_space(config)


class TestResolveAlias:
    def test_empty_returns_empty(self, config):
        assert ocio_utils.resolve_alias(config, "") == ""

    def test_unknown_returns_empty(self, config):
        assert ocio_utils.resolve_alias(config, "definitely-not-a-space") == ""

    def test_known_space_roundtrips(self, config):
        ws = ocio_utils.get_working_space(config)
        # Resolving the canonical name should return a valid (non-empty) name.
        assert ocio_utils.resolve_alias(config, ws) != ""


class TestFindEquivalentSpace:
    def test_empty_and_unknown(self, config):
        assert ocio_utils.find_equivalent_space(config, "") == ""
        assert ocio_utils.find_equivalent_space(config, "TotallyFakeSpaceXYZ") == ""

    def test_identity(self, config):
        ws = ocio_utils.get_working_space(config)
        assert ocio_utils.find_equivalent_space(config, ws) == ws

    def test_role_scene_linear(self, config):
        hit = ocio_utils.find_equivalent_space(config, "scene_linear")
        assert hit
        # Must resolve to a real colorspace name on this config.
        assert config.getColorSpace(hit) is not None

    def test_rec709_family_maps(self, config):
        hit = ocio_utils.find_equivalent_space(config, "Output - Rec.709")
        if not hit:
            hit = ocio_utils.find_equivalent_space(config, "rec709")
        # Most studio/cg configs expose some Rec.709-ish display space.
        if hit:
            assert config.getColorSpace(hit) is not None

    def test_acescg_alias_forms(self, config):
        if config.getColorSpace("ACEScg") is None:
            pytest.skip("no ACEScg on this config")
        for name in ("ACEScg", "acescg", "ACES - ACEScg"):
            hit = ocio_utils.find_equivalent_space(config, name)
            assert hit
            assert "acescg" in hit.lower().replace(" ", "").replace("-", "")


class TestLoadConfigFromSourceInfo:
    def test_empty_falls_back(self):
        cfg = ocio_utils.load_config_from_source_info("", "")
        assert cfg is not None
        assert len(list(cfg.getColorSpaceNames())) > 0

    def test_invalid_path_falls_back(self, tmp_path):
        cfg = ocio_utils.load_config_from_source_info("", str(tmp_path / "nope.ocio"))
        assert cfg is not None


class TestLinearizeOverlay:
    def test_preserves_shape_and_alpha(self, config):
        h, w = 8, 8
        rgba = np.zeros((h, w, 4), dtype=np.uint8)
        rgba[..., :3] = 128
        rgba[..., 3] = 200  # alpha channel
        out = ocio_utils.linearize_overlay(config, rgba)
        assert out.shape == (h, w, 4)
        assert out.dtype == np.float32
        # Alpha passes through unchanged (scaled to 0..1), RGB is transformed.
        assert np.allclose(out[..., 3], 200 / 255.0)
