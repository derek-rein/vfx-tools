"""Preset path safety and round-trip (uses isolated QSettings dir via monkeypatch)."""

from __future__ import annotations

import pytest

from src.services import presets as presets_mod


@pytest.fixture
def preset_root(tmp_path, monkeypatch, qapp):
    """Redirect AppData presets into tmp_path so nothing leaks to the user."""
    monkeypatch.setattr(
        presets_mod,
        "_preset_dir",
        lambda: tmp_path / "presets",
    )
    (tmp_path / "presets").mkdir(parents=True, exist_ok=True)
    return tmp_path / "presets"


class TestPresetNameValidation:
    def test_accepts_normal_names(self):
        assert presets_mod.validate_preset_name("My Preset") == "My Preset"
        assert presets_mod.validate_preset_name("v1.2_final") == "v1.2_final"

    def test_rejects_empty(self):
        with pytest.raises(ValueError):
            presets_mod.validate_preset_name("  ")

    def test_rejects_path_traversal(self):
        with pytest.raises(ValueError):
            presets_mod.validate_preset_name("../etc/passwd")
        with pytest.raises(ValueError):
            presets_mod.validate_preset_name("foo/bar")
        with pytest.raises(ValueError):
            presets_mod.validate_preset_name("foo\\bar")

    def test_rejects_dot_dot_in_name(self):
        with pytest.raises(ValueError):
            presets_mod.validate_preset_name("a..b")


class TestPresetRoundTrip:
    def test_save_load_delete(self, preset_root):
        path = presets_mod.save_preset("review_prores", {"e2v_codec": "prores", "e2v_fps": 24})
        assert path.parent == preset_root.resolve()
        assert path.is_file()
        data = presets_mod.load_preset("review_prores")
        assert data["e2v_codec"] == "prores"
        assert data["schema_version"] == presets_mod.SCHEMA_VERSION
        assert data["kind"] == "convert_preset"
        assert "review_prores" in presets_mod.list_presets()
        presets_mod.delete_preset("review_prores")
        assert "review_prores" not in presets_mod.list_presets()

    def test_normalize_strips_paths(self):
        data = presets_mod.normalize_preset(
            {"v2e_src_space": "sRGB", "input": "/secret/path", "output": "/out"}
        )
        assert "input" not in data
        assert "output" not in data
        assert data["v2e_src_space"] == "sRGB"
        assert data["schema_version"] == presets_mod.SCHEMA_VERSION

    def test_legacy_load_gets_version(self, preset_root):
        # Write pre-version flat file by hand
        path = preset_root / "legacy.json"
        path.write_text('{"e2v_codec": "h264"}', encoding="utf-8")
        data = presets_mod.load_preset("legacy")
        assert data["e2v_codec"] == "h264"
        assert data["schema_version"] == presets_mod.SCHEMA_VERSION

    def test_save_rejects_traversal(self, preset_root):
        with pytest.raises(ValueError):
            presets_mod.save_preset("../../evil", {"x": 1})
