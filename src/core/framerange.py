"""Nuke-style frame range parsing and formatting via fileseq.

Reference: https://learn.foundry.com/nuke/content/getting_started/managing_scripts/defining_frame_ranges.html
"""

from __future__ import annotations

import fileseq


def parse_frame_range(spec: str) -> list[int]:
    """Parse a Nuke-style frame range string into a sorted list of ints."""
    if not spec or not spec.strip():
        return []
    fs = fileseq.FrameSet(spec.strip())
    return sorted(int(f) for f in fs)


def format_frame_range(frames: list[int]) -> str:
    """Format a list of frame numbers into a compact range string."""
    if not frames:
        return ""
    fs = fileseq.FrameSet(sorted(set(frames)))
    return str(fs)
