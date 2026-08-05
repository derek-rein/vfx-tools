"""Tests for local Nuke OCIO discovery (no Foundry files redistributed)."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.core import nuke_discover
from src.core.ocio_utils import list_nuke_configs, resolve_ocio_config


def _fake_nuke_tree(root: Path, version: str = "17.0v3") -> Path:
    """Build a minimal Nuke-like OCIOConfigs tree under *root*."""
    install = root / f"Nuke{version}"
    configs = (
        install
        / f"Nuke{version}.app"
        / "Contents"
        / "Resources"
        / "OCIOConfigs"
        / "configs"
    )
    configs.mkdir(parents=True)

    studio = configs / "fn-nuke_studio-config-v3.0.0_aces-v2.0_ocio-v2.4.ocio"
    studio.write_text("ocio_profile_version: 1\n\nroles:\n  default: raw\n\ncolorspaces:\n")
    cg = configs / "fn-nuke_cg-config-v3.0.0_aces-v2.0_ocio-v2.4.ocio"
    cg.write_text("ocio_profile_version: 1\n\nroles:\n  default: raw\n\ncolorspaces:\n")
    nd = configs / "nuke-default"
    nd.mkdir()
    (nd / "config.ocio").write_text(
        "ocio_profile_version: 1\n\nroles:\n  default: raw\n\ncolorspaces:\n"
    )
    return install


def test_find_nuke_ocio_configs_from_env_root(tmp_path, monkeypatch):
    install = _fake_nuke_tree(tmp_path)
    monkeypatch.setenv("EXR_CONVERTER_NUKE_ROOTS", str(install))
    # Avoid scanning real /Applications in CI
    monkeypatch.setattr(nuke_discover.platform, "system", lambda: "Linux")
    monkeypatch.setattr(nuke_discover, "_iter_install_roots", lambda: [install])

    found = nuke_discover.find_nuke_ocio_configs()
    assert len(found) == 3
    keys = {c.key for c in found}
    assert any("studio" in k for k in keys)
    assert any("nuke-default" in k for k in keys)
    # Studio ACES 2.0 should sort first
    assert found[0].kind == "studio_aces2"
    assert found[0].path.is_file()


def test_list_nuke_configs_empty_without_install(monkeypatch):
    from src.core import ocio_utils

    monkeypatch.setattr(ocio_utils, "find_nuke_ocio_configs", lambda: [])
    assert list_nuke_configs() == []


def test_resolve_nuke_source_key(tmp_path, monkeypatch):
    install = _fake_nuke_tree(tmp_path, "16.0v5")
    monkeypatch.setattr(nuke_discover, "_iter_install_roots", lambda: [install])

    found = nuke_discover.find_nuke_ocio_configs()
    assert found
    key = found[0].key
    assert nuke_discover.is_nuke_source_key(key)
    path = nuke_discover.resolve_nuke_config_path(key)
    assert path is not None and path.is_file()

    # Missing key
    assert nuke_discover.resolve_nuke_config_path("nuke:9.0v1:missing") is None


def test_resolve_ocio_config_nuke_missing_raises(monkeypatch):
    from src.core import ocio_utils

    monkeypatch.setattr(ocio_utils, "resolve_nuke_config_path", lambda _k: None)
    with pytest.raises(RuntimeError, match="no longer found"):
        resolve_ocio_config("nuke:17.0v3:fn-nuke_studio-config-v3.0.0_aces-v2.0_ocio-v2.4")


def test_version_sort_key_orders_newest_first():
    versions = ["15.1v2", "17.0v3", "16.0v5", "17.0v1"]
    ordered = sorted(versions, key=lambda v: tuple(-x for x in nuke_discover._version_sort_key(v)))
    assert ordered[0] == "17.0v3"
    assert ordered[-1] == "15.1v2"
