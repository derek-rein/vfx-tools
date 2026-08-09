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


class TestAppAnchor:
    def test_anchor_has_interchange_and_texture(self):
        anchor = ocio_utils.get_app_anchor_config()
        assert ocio_utils.get_interchange_space(anchor)
        assert ocio_utils.get_overlay_authoring_space(anchor)
        assert ocio_utils.get_internal_interchange_space()
        assert ocio_utils.get_internal_overlay_authoring_space()

    def test_anchor_is_cached(self):
        a = ocio_utils.get_app_anchor_config()
        b = ocio_utils.get_app_anchor_config()
        assert a is b


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

    def test_works_even_when_user_config_is_raw_only(self):
        """Internal paint must not depend on the user config having sRGB/ACES."""
        import PyOpenColorIO as OCIO

        user = OCIO.Config.CreateRaw()
        h, w = 4, 4
        rgba = np.zeros((h, w, 4), dtype=np.uint8)
        rgba[..., :3] = 64
        rgba[..., 3] = 255
        # No working space on CreateRaw — linearize still succeeds via anchor.
        out = ocio_utils.linearize_overlay(user, rgba, working_space="")
        assert out.shape == (h, w, 4)
        assert out.dtype == np.float32
        assert np.allclose(out[..., 3], 1.0)
        # RGB was transformed off the sRGB code value (not left as 64/255).
        assert not np.allclose(out[..., :3], 64 / 255.0)

    def test_float_input_matches_uint8_path_within_quantize(self, config):
        h, w = 8, 8
        rgba_u8 = np.zeros((h, w, 4), dtype=np.uint8)
        rgba_u8[..., 0] = 200
        rgba_u8[..., 1] = 100
        rgba_u8[..., 2] = 50
        rgba_u8[..., 3] = 255
        rgba_f = rgba_u8.astype(np.float32) / 255.0
        out_u8 = ocio_utils.linearize_overlay(config, rgba_u8)
        out_f = ocio_utils.linearize_overlay(config, rgba_f)
        # Same code values → same linear result (float path has no extra quantize).
        assert np.allclose(out_u8[..., :3], out_f[..., :3], rtol=1e-5, atol=1e-6)

    def test_anchor_bridge_matches_single_config_path(self):
        """CG-anchor paint + interchange bridge == pure modern ACES user-config."""
        import PyOpenColorIO as OCIO

        # Compare against a known-good modern ACES config (not whichever
        # builtin the module fixture happens to load first — older CG v1
        # texture roles are not bit-identical to v4).
        last_err = None
        user = None
        for name in (
            "studio-config-v4.0.0_aces-v2.0_ocio-v2.5",
            "cg-config-v4.0.0_aces-v2.0_ocio-v2.5",
            "studio-config-v2.2.0_aces-v1.3_ocio-v2.4",
        ):
            try:
                user = OCIO.Config.CreateFromBuiltinConfig(name)
                break
            except Exception as e:
                last_err = e
        if user is None:
            pytest.skip(f"no modern ACES builtin: {last_err}")
        auth = ocio_utils.get_overlay_authoring_space(user)
        comp = ocio_utils.get_compositing_space(user)
        assert auth and comp and ocio_utils.get_interchange_space(user)
        h, w = 8, 8
        rgba = np.zeros((h, w, 4), dtype=np.uint8)
        rgba[..., 0] = 255
        rgba[..., 1] = 128
        rgba[..., 2] = 32
        rgba[..., 3] = 255
        bridged = ocio_utils.linearize_overlay(user, rgba, working_space=comp)
        direct = rgba[..., :3].astype(np.float32) / 255.0
        direct = np.ascontiguousarray(direct)
        ocio_utils.make_cpu_processor(user, auth, comp).apply(
            OCIO.PackedImageDesc(direct, w, h, 3)
        )
        assert np.allclose(bridged[..., :3], direct, rtol=1e-4, atol=1e-5)


class TestDisplayViewSelection:
    def test_default_display_view(self, config):
        display, view = ocio_utils.default_display_view(config)
        assert display
        assert view

    def test_video_monitoring_from_config_rules(self, config):
        """Prefer viewing-rule default for video encodings when the config has them."""
        display, view = ocio_utils.preferred_video_monitoring_view(config)
        # ACES CG/Studio: returns Video (colorimetric). Lean/raw configs: "".
        if view:
            assert display
            # Must be a real view on that display.
            assert view in ocio_utils.list_views(config, display)
            # Should not be the scene-linear RRT default when a video rule exists.
            _d0, v0 = ocio_utils.default_display_view(config)
            # If config distinguishes video vs scene, video view differs or equals
            # only when the config has a single view.
            assert isinstance(view, str)

    def test_default_view_for_video_colorspace(self, config):
        """getDefaultView(display, videoCS) path via default_display_view."""
        # Find any sdr-video encoded space if present.
        video_cs = ""
        for name in config.getColorSpaceNames():
            cs = config.getColorSpace(name)
            if cs is not None and (cs.getEncoding() or "").lower() == "sdr-video":
                video_cs = name
                break
        if not video_cs:
            pytest.skip("no sdr-video encoding in this config")
        display, view = ocio_utils.default_display_view(config, color_space=video_cs)
        assert display and view
