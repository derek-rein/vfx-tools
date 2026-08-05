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
        path = presets_mod.save_preset("review_prores", {"codec": "prores", "fps": 24})
        assert path.parent == preset_root.resolve()
        assert path.is_file()
        data = presets_mod.load_preset("review_prores")
        assert data["codec"] == "prores"
        assert "review_prores" in presets_mod.list_presets()
        presets_mod.delete_preset("review_prores")
        assert "review_prores" not in presets_mod.list_presets()

    def test_save_rejects_traversal(self, preset_root):
        with pytest.raises(ValueError):
            presets_mod.save_preset("../../evil", {"x": 1})
