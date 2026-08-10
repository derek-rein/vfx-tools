"""Unit tests for mounted-volume discovery (file browser roots)."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

from src.gui.browser_volumes import (
    VolumeInfo,
    _norm_mount_path,
    _should_skip_mount,
    list_browser_volumes,
)


def test_norm_mount_path_unix() -> None:
    if sys.platform == "win32":
        pytest.skip("unix path rules")
    assert _norm_mount_path("/") == "/"
    assert _norm_mount_path("/Volumes/Disk/") == "/Volumes/Disk"
    assert _norm_mount_path("  /media/user/stick  ") == "/media/user/stick"


def test_norm_mount_path_windows() -> None:
    if sys.platform != "win32":
        # Logic still applies when we force win-style strings through the helper
        # only on Windows; skip elsewhere.
        pytest.skip("windows path rules")
    assert _norm_mount_path("C:\\") in ("C:\\", "C:/")
    assert _norm_mount_path("D:") == "D:\\" or _norm_mount_path("D:").startswith("D:")


def _mock_volume(
    *,
    path: str,
    name: str = "",
    display: str = "",
    is_root: bool = False,
    ready: bool = True,
    valid: bool = True,
    fs: bytes = b"apfs",
    device: bytes = b"/dev/disk1",
    bytes_total: int = 10**12,
) -> MagicMock:
    v = MagicMock()
    v.rootPath.return_value = path
    v.name.return_value = name
    v.displayName.return_value = display or name or path
    v.isRoot.return_value = is_root
    v.isReady.return_value = ready
    v.isValid.return_value = valid
    v.fileSystemType.return_value = fs
    v.device.return_value = device
    v.bytesTotal.return_value = bytes_total
    v.isReadOnly.return_value = False
    return v


def test_should_skip_pseudo_fs() -> None:
    proc = _mock_volume(path="/proc", fs=b"proc", is_root=False, bytes_total=0)
    assert _should_skip_mount(proc) is True


def test_should_skip_macos_system_volumes() -> None:
    if sys.platform == "win32":
        pytest.skip("unix skip prefixes")
    data = _mock_volume(
        path="/System/Volumes/Data",
        name="Data",
        fs=b"apfs",
        is_root=False,
        device=b"disk3s5",
    )
    assert _should_skip_mount(data) is True


def test_should_keep_external_volume() -> None:
    stick = _mock_volume(
        path="/Volumes/KINGSTON",
        name="KINGSTON",
        display="KINGSTON",
        fs=b"exfat",
        is_root=False,
        device=b"/dev/disk8s1",
    )
    assert _should_skip_mount(stick) is False


def test_list_browser_volumes_live() -> None:
    """Smoke: real machine has at least the system root."""
    vols = list_browser_volumes()
    assert vols
    assert isinstance(vols[0], VolumeInfo)
    assert vols[0].path
    # Exactly one system root when present.
    roots = [v for v in vols if v.is_system_root]
    assert len(roots) <= 1


def test_list_browser_volumes_dedupes_macos_firmlink(monkeypatch: pytest.MonkeyPatch) -> None:
    """``/`` and ``/Volumes/Macintosh HD`` share a device — keep system root only."""
    if sys.platform == "win32":
        pytest.skip("macOS firmlink case")

    root = _mock_volume(
        path="/",
        name="Macintosh HD",
        display="Macintosh HD",
        is_root=True,
        device=b"/dev/disk3s1s1",
        fs=b"apfs",
    )
    firm = _mock_volume(
        path="/Volumes/Macintosh HD",
        name="Macintosh HD",
        display="Macintosh HD",
        is_root=False,
        device=b"/dev/disk3s1s1",  # same device
        fs=b"apfs",
    )
    stick = _mock_volume(
        path="/Volumes/USB",
        name="USB",
        display="USB",
        is_root=False,
        device=b"/dev/disk9s1",
        fs=b"exfat",
    )
    monkeypatch.setattr(
        "src.gui.browser_volumes.QStorageInfo.mountedVolumes",
        staticmethod(lambda: [root, firm, stick]),
    )
    vols = list_browser_volumes()
    paths = [v.path for v in vols]
    assert "/" in paths
    assert "/Volumes/Macintosh HD" not in paths
    assert "/Volumes/USB" in paths
    assert vols[0].is_system_root is True
