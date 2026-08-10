#!/usr/bin/env python3
"""Extract a versioned section from CHANGELOG.md."""

from __future__ import annotations

import sys
from pathlib import Path


def extract_section(changelog: Path, version: str) -> str:
    """Return the ``## [version]`` section body (through next ``## [`` or EOF)."""
    text = changelog.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    heading = f"## [{version}]"
    start: int | None = None
    for i, line in enumerate(lines):
        if line.startswith(heading):
            start = i
            break
    if start is None:
        print(f"error: no section for version {version!r} in {changelog}", file=sys.stderr)
        raise SystemExit(1)

    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].startswith("## ["):
            end = j
            break

    section = "".join(lines[start:end])
    return section.rstrip("\n") + "\n"


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(
            f"usage: {Path(sys.argv[0]).name} VERSION [OUTPUT_PATH]",
            file=sys.stderr,
        )
        return 2

    version = args[0]
    out_path = Path(args[1]) if len(args) > 1 else None
    repo_root = Path(__file__).resolve().parent.parent
    changelog = repo_root / "CHANGELOG.md"
    if not changelog.is_file():
        print(f"error: CHANGELOG.md not found at {changelog}", file=sys.stderr)
        return 1

    section = extract_section(changelog, version)
    if out_path is None:
        sys.stdout.write(section)
    else:
        out_path.write_text(section, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
