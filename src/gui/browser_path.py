"""Shared path cleaning / navigation helpers for file browsers and path fields.

Handles Nuke-style pastes (``name.####.exr``), ``file://`` URLs, quoted paths,
and folder resolution for Copy Folder Path / reveal-in-finder actions.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

from ..core.constants import is_image_sequence_ext
from ..core.sequence import looks_like_sequence_pattern, sequence_pattern_stem


def clean_path_string(raw: str) -> str:
    """Normalize a pasted / typed path: strip, unquote ``file://``, drop quotes."""
    text = (raw or "").strip()
    if not text:
        return ""
    # Strip matching single/double quotes (Finder / terminal pastes).
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1].strip()
    # file:// URLs from drag/drop or browser paste.
    # Do not use urlparse alone: Nuke ``####`` patterns put ``#`` in the path,
    # which urlparse treats as a URL fragment and would strip.
    if text.lower().startswith("file:"):
        rest = text[5:]  # after "file:"
        if rest.startswith("///"):
            # file:///absolute → /absolute
            path = unquote(rest[2:])  # keep leading /
        elif rest.startswith("//"):
            # file://host/path or file://localhost/path
            without = rest[2:]
            if without.lower().startswith("localhost/"):
                path = unquote("/" + without[len("localhost/") :])
            elif "/" in without:
                host, _, tail = without.partition("/")
                if host.lower() in {"", "localhost"}:
                    path = unquote("/" + tail)
                else:
                    path = unquote(f"//{host}/{tail}")
            else:
                path = unquote(without)
        else:
            # file:/absolute (rare)
            path = unquote(rest)
        # Drop accidental fragment only when it looks like a real URL fragment
        # (not Nuke #### pads). Keep all ``#`` characters in the path.
        text = path
    return text.strip()


def folder_path_for_copy(text: str) -> str:
    """Directory to copy for a file, folder, or ``name.####.ext`` path string.

    Shared by path-field and file-browser context menus (same semantics as the
    Input/Output line-edit **Copy Folder Path** action).
    """
    raw = clean_path_string(text)
    if not raw:
        return ""
    p = Path(raw).expanduser()
    # Sequence pattern → containing directory
    if "#" in p.name or "%" in p.name:
        return str(p.parent)
    try:
        if p.is_dir():
            return str(p)
        if p.is_file():
            return str(p.parent)
    except OSError:
        pass
    # Non-existent file-like path → parent; bare path → as-is
    if p.suffix or "." in p.name:
        return str(p.parent) if str(p.parent) not in ("", ".") else str(p)
    return str(p)


def resolve_sequence_browser_path(raw: str) -> tuple[str, str] | None:
    """Resolve a paste for the EXR sequence browser.

    Returns ``(directory, select_name)`` or ``None`` if nothing usable.
    *select_name* is the sequence stem used to auto-select a row (may be empty).
    """
    text = clean_path_string(raw)
    if not text:
        return None
    p = Path(text).expanduser()
    directory = ""
    select_name = ""
    if p.is_dir():
        directory = str(p)
    elif p.is_file() and is_image_sequence_ext(p.suffix):
        directory = str(p.parent)
        stem = sequence_pattern_stem(p.name)
        if stem is None:
            m = re.match(
                r"^(?P<head>.+)\.(?P<frame>\d+)\.(?P<ext>[A-Za-z0-9]+)$",
                p.name,
                re.I,
            )
            select_name = m.group("head") if m else p.stem
        else:
            select_name = stem
    elif looks_like_sequence_pattern(text):
        directory = str(p.parent)
        select_name = sequence_pattern_stem(p.name) or ""
    else:
        if p.parent.is_dir():
            directory = str(p.parent)
        else:
            return None

    if not directory or not Path(directory).is_dir():
        return None
    return directory, select_name


def resolve_video_browser_path(
    raw: str,
    video_exts: frozenset[str],
) -> tuple[str, str] | None:
    """Resolve a paste for the video browser.

    Returns ``(directory, select_path)`` or ``None``. *select_path* is the full
    path of a video file to select (empty when navigating a folder only).
    """
    from ..core.video import is_ignored_media_filename

    text = clean_path_string(raw)
    if not text:
        return None
    p = Path(text).expanduser()
    directory = ""
    select_path = ""
    if p.is_dir():
        directory = str(p)
    elif p.is_file() and p.suffix.lower() in video_exts and not is_ignored_media_filename(p.name):
        directory = str(p.parent)
        select_path = str(p)
    elif p.parent.is_dir():
        directory = str(p.parent)
    else:
        return None
    if not directory:
        return None
    return directory, select_path
