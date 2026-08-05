"""Burn-in / watermark template variables (Maya / Nuke-style ``<token>``).

Text fields in the burn-in and watermark editors accept angle-bracket tokens
such as ``<shot>``, ``<version>`` or ``<frame>``.  They are resolved at render
time against the slate metadata and the current frame number, so a single
template like ``<shot> <version> - <frame>`` expands to ``sq010_0040 v0003 - 1017``.

This module is intentionally Qt-free so it can be unit-tested in isolation and
imported by both the editor widgets and the render pipeline:

- :data:`TOKEN_GROUPS` drives the right-click "Insert Variable" menu.
- :func:`substitute` expands tokens in a string given a resolved value map.
- :func:`build_values` maps slate render data + a frame number to that map.
- :func:`has_per_frame_token` lets the pipeline skip per-frame rendering when
  every field is constant across the sequence.
"""

from __future__ import annotations

import datetime
import re
from collections.abc import Mapping

# Ordered (group label, [(canonical_name, display_label, description)]).  The
# canonical name is the lower-case key looked up in the value map; aliases are
# defined separately so several spellings can map to one value.
TOKEN_GROUPS: list[tuple[str, list[tuple[str, str, str]]]] = [
    (
        "Frame",
        [
            ("frame", "<frame>", "Current frame number (changes every frame)"),
            ("startframe", "<startframe>", "First frame of the range"),
            ("endframe", "<endframe>", "Last frame of the range"),
            ("framerange", "<framerange>", "Frame range, e.g. 1001-1100"),
        ],
    ),
    (
        "Shot",
        [
            ("show", "<show>", "Show / production code"),
            ("sequence", "<sequence>", "Sequence name"),
            ("shot", "<shot>", "Shot name"),
            ("version", "<version>", "Version string, e.g. v0003"),
            ("take", "<take>", "Take number"),
        ],
    ),
    (
        "Production",
        [
            ("artist", "<artist>", "Artist name"),
            ("vendor", "<vendor>", "Studio / vendor name"),
            ("scope", "<scope>", "Scope of work"),
            ("shottypes", "<shottypes>", "Shot types, e.g. 2d comp, roto"),
            ("submitfor", "<submitfor>", "Submission status (WIP / FINAL / CBB)"),
            ("date", "<date>", "Today's date (YYYY-MM-DD)"),
            ("fps", "<fps>", "Frames per second"),
            ("resolution", "<resolution>", "Output resolution, e.g. 1920x1080"),
            ("input", "<input>", "Input file / sequence name"),
        ],
    ),
]

# Alternate spellings → canonical name.
_ALIASES: dict[str, str] = {
    "f": "frame",
    "seq": "sequence",
    "ver": "version",
    "res": "resolution",
    "range": "framerange",
    "file": "input",
    "filename": "input",
    "status": "submitfor",
    "shot_types": "shottypes",
    "start": "startframe",
    "end": "endframe",
}

# Tokens whose value differs from frame to frame.  When none of these appear in
# any field the pipeline can render the overlay once and reuse it.
PER_FRAME_TOKENS: frozenset[str] = frozenset({"frame"})

_TOKEN_RE = re.compile(r"<([A-Za-z][A-Za-z0-9_]*)>")


def _canonical(name: str) -> str:
    key = name.lower()
    return _ALIASES.get(key, key)


def has_per_frame_token(text: str | None) -> bool:
    """True if *text* contains any token that varies per frame (e.g. ``<frame>``)."""
    if not text:
        return False
    return any(_canonical(m.group(1)) in PER_FRAME_TOKENS for m in _TOKEN_RE.finditer(text))


def any_per_frame_token(texts: object) -> bool:
    """True if any string in *texts* (str, dict values, or iterable) is per-frame."""
    if texts is None:
        return False
    if isinstance(texts, str):
        return has_per_frame_token(texts)
    if isinstance(texts, Mapping):
        return any(has_per_frame_token(str(v)) for v in texts.values())
    if isinstance(texts, (list, tuple, set, frozenset)):
        return any(has_per_frame_token(str(v)) for v in texts)
    return False


def substitute(text: str | None, values: Mapping[str, str]) -> str:
    """Expand ``<token>`` references in *text* using *values*.

    Lookups are case-insensitive and alias-aware.  Unknown tokens are left
    verbatim (so an unrelated ``<foo>`` survives untouched rather than
    vanishing), and a known token with an empty value collapses to ``""``.
    """
    if not text:
        return text or ""

    def _repl(m: re.Match[str]) -> str:
        canonical = _canonical(m.group(1))
        if canonical in values and values[canonical] is not None:
            return str(values[canonical])
        return m.group(0)

    return _TOKEN_RE.sub(_repl, text)


def _pad(num: int | None, pad: int) -> str:
    if num is None:
        return ""
    return f"{int(num):0{max(1, int(pad))}d}"


def build_values(
    slate_render: Mapping[str, str] | None = None,
    *,
    input_name: str = "",
    frame: int | None = None,
    frame_pad: int = 4,
    start_frame: int | None = None,
    end_frame: int | None = None,
    resolution: str | None = None,
    frame_range: str | None = None,
) -> dict[str, str]:
    """Build a token → value map from slate render data and frame context.

    *slate_render* is the camelCase dict produced by
    :meth:`SlateModel.slate_data_for_render`.  Frame-related kwargs override the
    slate metadata when supplied (e.g. *resolution* / *frame_range* reflect the
    real output rather than the slate's own settings).
    """
    s = dict(slate_render or {})
    values: dict[str, str] = {
        "show": s.get("show", "") or "",
        "sequence": s.get("sequence", "") or "",
        "shot": s.get("shot", "") or "",
        "version": s.get("version", "") or "",
        "take": s.get("take", "") or "",
        "artist": s.get("artist", "") or "",
        "vendor": s.get("vendor", "") or "",
        "scope": s.get("scope", "") or "",
        "shottypes": s.get("shotTypes", "") or "",
        "submitfor": s.get("submitFor", "") or "",
        "date": s.get("date", "") or datetime.date.today().isoformat(),
        "fps": str(s.get("fps", "") or ""),
        "resolution": resolution if resolution is not None else (s.get("resolution", "") or ""),
        "framerange": frame_range if frame_range is not None else (s.get("frameRange", "") or ""),
        "input": input_name or "",
    }
    if frame is not None:
        values["frame"] = _pad(frame, frame_pad)
    if start_frame is not None:
        values["startframe"] = _pad(start_frame, frame_pad)
    if end_frame is not None:
        values["endframe"] = _pad(end_frame, frame_pad)
    return values


__all__ = [
    "PER_FRAME_TOKENS",
    "TOKEN_GROUPS",
    "any_per_frame_token",
    "build_values",
    "has_per_frame_token",
    "substitute",
]
