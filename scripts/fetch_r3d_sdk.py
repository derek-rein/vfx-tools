#!/usr/bin/env python3
"""Download the private R3D SDK tarball for local/CI builds.

Expects a GitHub Release on a **private** repo (default: derek-rein/r3d-sdk-private)
with asset ``R3DSDKv9_2_1-full.tar.gz`` (or override via env).

Auth (first match wins):
  * ``R3D_SDK_READ_TOKEN`` / ``GH_TOKEN`` / ``GITHUB_TOKEN``
  * ``gh auth token`` (local developer machines)

Usage:
  python3 scripts/fetch_r3d_sdk.py
  python3 scripts/fetch_r3d_sdk.py --out /tmp/r3d-sdk

Prints the unpacked SDK root path on stdout (last line) and sets nothing else;
export yourself::

  export R3D_SDK_ROOT="$(python3 scripts/fetch_r3d_sdk.py)"
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_REPO = "derek-rein/r3d-sdk-private"
DEFAULT_TAG = "sdk-9.2.1"
DEFAULT_ASSET = "R3DSDKv9_2_1-full.tar.gz"
# Prefer repo-local cache (gitignored) then XDG-ish home cache.
ROOT = Path(__file__).resolve().parents[1]


def _token() -> str:
    for key in ("R3D_SDK_READ_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        val = os.environ.get(key, "").strip()
        if val:
            return val
    try:
        out = subprocess.check_output(
            ["gh", "auth", "token"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        return out.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def _api_download(repo: str, tag: str, asset: str, dest: Path, token: str) -> None:
    """Download a release asset via the GitHub API (private-repo friendly)."""
    # Resolve asset id via release metadata.
    api = f"https://api.github.com/repos/{repo}/releases/tags/{tag}"
    req = urllib.request.Request(
        api,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "exr-converter-fetch-r3d-sdk",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            import json

            data = json.load(resp)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:500]
        raise SystemExit(f"Failed to fetch release {repo}@{tag}: HTTP {e.code}\n{body}") from e

    asset_id = None
    for a in data.get("assets") or []:
        if a.get("name") == asset:
            asset_id = a.get("id")
            break
    if not asset_id:
        names = [a.get("name") for a in (data.get("assets") or [])]
        raise SystemExit(f"Asset {asset!r} not found on {repo}@{tag}. Available: {names}")

    url = f"https://api.github.com/repos/{repo}/releases/assets/{asset_id}"
    req2 = urllib.request.Request(
        url,
        headers={
            "Accept": "application/octet-stream",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "exr-converter-fetch-r3d-sdk",
        },
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urllib.request.urlopen(req2, timeout=600) as resp, dest.open("wb") as f:
            shutil.copyfileobj(resp, f)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:500]
        raise SystemExit(f"Failed to download asset {asset}: HTTP {e.code}\n{body}") from e


def _find_sdk_root(extract_dir: Path) -> Path:
    direct = extract_dir / "Include" / "R3DSDK.h"
    if direct.is_file():
        return extract_dir
    for child in sorted(extract_dir.iterdir()):
        if child.is_dir() and (child / "Include" / "R3DSDK.h").is_file():
            return child
    raise SystemExit(f"No R3DSDK.h under {extract_dir} after extract")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--repo",
        default=os.environ.get("R3D_SDK_GITHUB_REPO", DEFAULT_REPO),
        help=f"owner/name of private SDK repo (default {DEFAULT_REPO})",
    )
    ap.add_argument(
        "--tag",
        default=os.environ.get("R3D_SDK_RELEASE_TAG", DEFAULT_TAG),
        help=f"release tag (default {DEFAULT_TAG})",
    )
    ap.add_argument(
        "--asset",
        default=os.environ.get("R3D_SDK_ASSET", DEFAULT_ASSET),
        help=f"asset filename (default {DEFAULT_ASSET})",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=None,
        help="unpack directory (default: .r3d-sdk/ under repo or $R3D_SDK_CACHE)",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="re-download even if SDK already present",
    )
    args = ap.parse_args()

    cache = args.out
    if cache is None:
        env_cache = os.environ.get("R3D_SDK_CACHE", "").strip()
        cache = Path(env_cache) if env_cache else (ROOT / ".r3d-sdk")
    cache = cache.expanduser().resolve()

    # Already unpacked?
    existing = None
    if (cache / "Include" / "R3DSDK.h").is_file():
        existing = cache
    else:
        for child in cache.glob("R3DSDK*") if cache.is_dir() else []:
            if (child / "Include" / "R3DSDK.h").is_file():
                existing = child
                break
    if existing is not None and not args.force:
        print(f"Using existing SDK at {existing}", file=sys.stderr)
        print(existing)
        return

    token = _token()
    if not token:
        raise SystemExit(
            "No GitHub token. Set R3D_SDK_READ_TOKEN (or GH_TOKEN), or run `gh auth login`."
        )

    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="r3d-sdk-") as tmp:
        tarball = Path(tmp) / args.asset
        print(f"Downloading {args.repo}@{args.tag} / {args.asset} ...", file=sys.stderr)
        _api_download(args.repo, args.tag, args.asset, tarball, token)
        print(f"Extracting -> {cache}", file=sys.stderr)
        # Clean previous extract of same layout.
        for child in list(cache.iterdir()) if cache.is_dir() else []:
            if child.name.startswith("R3DSDK") or child.name == "Include":
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
        with tarfile.open(tarball, "r:gz") as tf:
            tf.extractall(cache, filter="data")

    sdk_root = _find_sdk_root(cache)
    print(f"R3D SDK ready: {sdk_root}", file=sys.stderr)
    print(sdk_root)


if __name__ == "__main__":
    main()
