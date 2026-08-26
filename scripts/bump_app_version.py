#!/usr/bin/env python3
"""Bump [project].version in pyproject.toml (the only written app version).

Runtime code reads that file via ``APP_VERSION`` in ``src/core/constants.py``.
Tags use plain semver: v1.2.3.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"

VERSION_LINE = re.compile(r"^version\s*=\s*\"([^\"]+)\"\s*$", re.MULTILINE)


def parse_semver(s: str) -> tuple[int, int, int]:
    parts = s.strip().split(".")
    if len(parts) != 3:
        raise ValueError(f"expected semver x.y.z, got {s!r}")
    return int(parts[0]), int(parts[1]), int(parts[2])


def bump_semver(current: str, part: str) -> str:
    major, minor, patch = parse_semver(current)
    if part == "major":
        major += 1
        minor = 0
        patch = 0
    elif part == "minor":
        minor += 1
        patch = 0
    else:
        patch += 1
    return f"{major}.{minor}.{patch}"


def read_pyproject_version() -> str:
    text = PYPROJECT.read_text(encoding="utf-8")
    m = VERSION_LINE.search(text)
    if not m:
        raise SystemExit(f"no version = line found in {PYPROJECT}")
    return m.group(1)


def write_pyproject_version(new_version: str, dry_run: bool) -> None:
    text = PYPROJECT.read_text(encoding="utf-8")
    new_text, n = VERSION_LINE.subn(f'version = "{new_version}"', text, count=1)
    if n != 1:
        raise SystemExit(f"failed to replace version in {PYPROJECT}")
    if not dry_run:
        PYPROJECT.write_text(new_text, encoding="utf-8")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    p_bump = sub.add_parser("bump", help="increment semver in pyproject.toml")
    p_bump.add_argument(
        "part",
        choices=("patch", "minor", "major"),
        help="which segment to increment",
    )
    p_bump.add_argument(
        "--dry-run",
        action="store_true",
        help="print new version but do not write files",
    )

    sub.add_parser(
        "show",
        help="print VERSION/TAG from current pyproject (no file changes)",
    )

    args = p.parse_args()

    if args.cmd == "show":
        current = read_pyproject_version()
        tag = f"v{current}"
        print(f'VERSION="{current}"')
        print(f'TAG="{tag}"')
        return

    if not PYPROJECT.is_file():
        raise SystemExit(f"missing {PYPROJECT}")

    current = read_pyproject_version()
    new_version = bump_semver(current, args.part)
    tag = f"v{new_version}"

    print(f"{current} \u2192 {new_version} ({args.part})")
    print(f"tag: {tag}")

    write_pyproject_version(new_version, args.dry_run)

    if args.dry_run:
        print("(dry-run: no files modified)")


if __name__ == "__main__":
    try:
        main()
    except BrokenPipeError:
        sys.exit(0)
