from __future__ import annotations

import re
from pathlib import Path

import fileseq

from .constants import (
    IMAGE_SEQUENCE_EXTS,
    image_sequence_ext_priority,
    is_image_sequence_ext,
    is_scene_referred_image_ext,
)

# **Writes** use ``name.####.ext`` (dot frame pad only). **Reads** accept both
# common pads: ``name.####.ext`` (fileseq basename ends with ``.``) and
# ``name_####.ext`` (basename ends with ``_``).
_DOT_PAD_PATTERN = re.compile(
    r"^(?P<name>.+)\.(?P<pad>#+)\.(?P<ext>[A-Za-z0-9]+)$",
    re.IGNORECASE,
)
_DOT_FRAME_PATTERN = re.compile(
    r"^(?P<name>.+)\.(?P<frame>\d+)\.(?P<ext>[A-Za-z0-9]+)$",
    re.IGNORECASE,
)
# Nuke / CLI paste: ``name.####.exr``, ``name_####.exr``, ``name.%04d.exr``.
_SEQ_HASH_PAD = re.compile(
    r"^(?P<head>.+?)(?P<sep>[._])(?P<pad>#+)\.(?P<ext>[A-Za-z0-9]+)$",
    re.IGNORECASE,
)
_SEQ_PRINTF_PAD = re.compile(
    r"^(?P<head>.+?)(?P<sep>[._])%0?(?P<width>\d*)d\.(?P<ext>[A-Za-z0-9]+)$",
    re.IGNORECASE,
)


def is_dot_frame_sequence(seq: fileseq.FileSequence) -> bool:
    """True when *seq* uses ``name.####.ext`` (basename ends with ``.``)."""
    try:
        return str(seq.basename()).endswith(".")
    except Exception:
        return False


def is_underscore_frame_sequence(seq: fileseq.FileSequence) -> bool:
    """True when *seq* uses ``name_####.ext`` (basename ends with ``_``)."""
    try:
        return str(seq.basename()).endswith("_")
    except Exception:
        return False


def is_supported_frame_sequence(seq: fileseq.FileSequence) -> bool:
    """True when *seq* is a readable still sequence (dot or underscore pad)."""
    return is_dot_frame_sequence(seq) or is_underscore_frame_sequence(seq)


def parse_dot_sequence_output(
    path: str,
) -> tuple[str, str | None, int | None]:
    """Parse a Video → EXR output path into ``(directory, name, padding)``.

    Accepts:

    - ``/out/shot.####.exr`` → ``("/out", "shot", 4)``
    - ``/out/shot.1001.exr`` → ``("/out", "shot", None)`` (pad from UI/CLI)
    - ``/out`` or ``/out/`` → ``("/out", None, None)`` (name from video stem)

    Underscore pads (``shot_####.exr``) are rejected for **output** paths —
    writes always use ``name.####.ext``. Reads still accept underscore pads.
    """
    raw = (path or "").strip()
    if not raw:
        return "", None, None
    p = Path(raw).expanduser()
    name = p.name

    # Underscore pad — not supported
    if re.search(r"_#+\.[A-Za-z0-9]+$", name, re.I) or re.search(
        r"_\d+\.[A-Za-z0-9]+$", name, re.I
    ):
        raise ValueError(
            f"Unsupported sequence pattern {name!r}: use name.####.ext "
            f"(dot-separated frames), not underscore pads."
        )

    # Explicit pad pattern: name.####.exr
    m = _DOT_PAD_PATTERN.match(name)
    if m:
        return str(p.parent), m.group("name"), len(m.group("pad"))

    # Single frame style name.1001.exr → sequence name only
    m = _DOT_FRAME_PATTERN.match(name)
    if m:
        return str(p.parent), m.group("name"), None

    # Directory (or any non-pattern path treated as output folder)
    return str(p), None, None


def _probe_resolution(filepath: str) -> tuple[int, int]:
    """Read display-window width and height from an image header without decoding pixels."""
    try:
        import OpenImageIO as oiio

        inp = oiio.ImageInput.open(filepath)
        if inp:
            spec = inp.spec()
            w = spec.full_width if spec.full_width > 0 else spec.width
            h = spec.full_height if spec.full_height > 0 else spec.height
            inp.close()
            return w, h
    except Exception:
        pass
    return 0, 0


def _find_image_seqs(directory: str) -> list[fileseq.FileSequence]:
    """Return image FileSequences found in *directory*, sorted by format priority then basename.

    Accepts both common pads:

    - ``name.####.ext`` (dot) — preferred for **writing**
    - ``name_####.ext`` (underscore) — common on disk from cameras / other tools

    OpenEXR sequences sort first so mixed folders still pick EXR by default.
    """
    seqs = fileseq.findSequencesOnDisk(directory)
    out = [
        s
        for s in seqs
        if is_image_sequence_ext(s.extension()) and s.frameSet() and is_supported_frame_sequence(s)
    ]
    return sorted(
        out,
        key=lambda s: (image_sequence_ext_priority(s.extension()), s.basename()),
    )


def _pick_default_sequence(
    seqs: list[fileseq.FileSequence],
    directory: str,
) -> fileseq.FileSequence:
    """Choose a default sequence when the input is a folder (not a frame file).

    Prefers a sequence whose basename matches the folder name (e.g. folder
    ``04_5d`` → ``04_5d_#####.exr`` over ``04_5d-2.####.exr``), then falls
    back to the sorted list order (EXR first, then basename).
    """
    if not seqs:
        raise RuntimeError(f"No image sequences found in {directory}")
    if len(seqs) == 1:
        return seqs[0]
    folder = Path(directory).name
    if not folder:
        return seqs[0]
    exact = [s for s in seqs if s.basename().rstrip("._") == folder]
    if exact:
        # Prefer underscore/dot variants of the folder name; keep EXR-first order.
        return exact[0]
    return seqs[0]


# Back-compat alias used by older call sites / tests.
_find_exr_seqs = _find_image_seqs


def sequence_pattern_stem(filename: str) -> str | None:
    """Return the sequence stem for a Nuke-style pattern filename, or ``None``.

    Examples::

        ``chs_010.####.exr`` → ``chs_010``
        ``chs_010_####.exr`` → ``chs_010``
        ``plate.%04d.exr`` → ``plate``
        ``plate.1001.exr`` → ``plate`` (numeric frame token)
    """
    name = Path(filename).name
    m = _SEQ_HASH_PAD.match(name)
    if m:
        return m.group("head")
    m = _SEQ_PRINTF_PAD.match(name)
    if m:
        return m.group("head")
    m = _DOT_FRAME_PATTERN.match(name)
    if m and not Path(filename).is_file():
        # Only treat as a pattern when the path is not a real single frame.
        return m.group("name")
    return None


def looks_like_sequence_pattern(path: str) -> bool:
    """True when *path* looks like ``name.####.ext`` (may not exist on disk)."""
    raw = (path or "").strip()
    if not raw:
        return False
    return sequence_pattern_stem(Path(raw).name) is not None


def _match_seq_by_stem(
    seqs: list[fileseq.FileSequence],
    stem: str,
) -> fileseq.FileSequence | None:
    """Pick the sequence whose basename (sans trailing ``.``/``_``) equals *stem*."""
    stem = (stem or "").strip()
    if not stem:
        return None
    exact = [s for s in seqs if s.basename().rstrip("._") == stem]
    if exact:
        return exact[0]
    return None


def probe_pixel_colorspace(filepath: str) -> str:
    """Return the OCIO colorspace of the *pixels* in *filepath*, if known.

    Preference order:

    1. ``exrconverter:dstColorSpace`` — space we wrote after OCIO (always correct
       for files from this app).
    2. ``oiio:ColorSpace`` — third-party / OIIO tag (often mangled to
       ``lin_rec709`` for any scene-linear EXR; treat as weak hint only when
       our attribute is missing).
    """
    try:
        import OpenImageIO as oiio

        inp = oiio.ImageInput.open(filepath)
        if not inp:
            return ""
        try:
            spec = inp.spec()
            ours = spec.getattribute("exrconverter:dstColorSpace")
            if ours:
                return str(ours)
            oiio_cs = spec.getattribute("oiio:ColorSpace")
            if oiio_cs:
                return str(oiio_cs)
        finally:
            inp.close()
    except Exception:
        pass
    return ""


def probe_exr_colorspace(directory: str) -> str:
    """Return the pixel colorspace of the preferred sequence in *directory*, or ''."""
    for s in _find_image_seqs(directory):
        first_frame = list(s.frameSet())[0]
        path = s.frame(first_frame)
        cs = probe_pixel_colorspace(path)
        if cs:
            return cs
        break
    return ""


def resolve_sequence_src_colorspace(
    filepath: str,
    ocio_cfg: object | None,
    preferred: str = "",
) -> str:
    """Return a **config-valid** source space for sequence preview.

    Prefers *preferred* when it maps into *ocio_cfg*, else file attributes via
    :func:`probe_pixel_colorspace` + :func:`~src.core.ocio_utils.find_equivalent_space`.
    Never returns an unmapped probe tag (e.g. mangled ``lin_rec709``) when a
    config is present — that would skip ``src→working`` and poison the player
    cache. Without a config, returns preferred or the raw probe for display only.
    """
    from .constants import DEFAULT_SRC_E2V
    from .ocio_utils import find_equivalent_space

    preferred = (preferred or "").strip()
    if ocio_cfg is None:
        return preferred or (probe_pixel_colorspace(filepath) if filepath else "")

    if preferred:
        hit = find_equivalent_space(ocio_cfg, preferred)
        if hit:
            return hit
    if filepath:
        probed = probe_pixel_colorspace(filepath)
        if probed:
            hit = find_equivalent_space(ocio_cfg, probed)
            if hit:
                return hit
    for fallback in (DEFAULT_SRC_E2V, "ACEScg", "ACES2065-1", "scene_linear"):
        hit = find_equivalent_space(ocio_cfg, fallback)
        if hit:
            return hit
    return ""


def probe_exr_metadata(filepath: str) -> dict[str, str]:
    """Return a dict of human-readable image metadata from the first frame."""
    result: dict[str, str] = {}
    try:
        import OpenImageIO as oiio

        inp = oiio.ImageInput.open(filepath)
        if not inp:
            return {"error": "Could not open file"}
        spec = inp.spec()
        fw = spec.full_width if spec.full_width > 0 else spec.width
        fh = spec.full_height if spec.full_height > 0 else spec.height
        result["Resolution"] = f"{fw} \u00d7 {fh}"
        if spec.width != fw or spec.height != fh:
            result["Data Window"] = f"{spec.width} \u00d7 {spec.height} (offset {spec.x}, {spec.y})"
        result["Channels"] = str(spec.nchannels)
        ch_names = [spec.channel_name(i) for i in range(spec.nchannels)]
        result["Channel names"] = ", ".join(ch_names)
        result["Pixel type"] = str(spec.format)
        comp = spec.getattribute("compression")
        if comp:
            result["Compression"] = str(comp)
        for attr in spec.extra_attribs:
            name = attr.name
            if name in ("compression",):
                continue
            val = str(attr.value)
            if len(val) > 200:
                val = val[:200] + "\u2026"
            result[name] = val
        inp.close()
    except Exception as e:
        result["error"] = str(e)
    return result


def scan_exr_sequences(directory: str) -> list[dict]:
    """Return metadata dicts for every supported image sequence found in *directory*.

    Each dict contains:
        name       - sequence basename (e.g. "beauty")
        frames     - number of frames
        range      - human-readable frame range string
        resolution - "W×H" string from the first frame
        path       - the directory scanned
        extension  - file extension including the leading dot (e.g. ".exr")
    """
    results = []
    for s in _find_image_seqs(directory):
        fs = s.frameSet()
        frame_list = sorted(fs)
        range_str = s.frameRange() if frame_list else "?"

        first_path = s.frame(frame_list[0]) if frame_list else ""
        w, h = _probe_resolution(first_path) if first_path else (0, 0)
        res_str = f"{w}\u00d7{h}" if w and h else ""

        pixel_type = ""
        compression = ""
        colorspace = ""
        if first_path:
            try:
                import OpenImageIO as oiio

                inp = oiio.ImageInput.open(first_path)
                if inp:
                    spec = inp.spec()
                    pixel_type = str(spec.format)
                    comp = spec.getattribute("compression")
                    if comp:
                        compression = str(comp)
                    cs = spec.getattribute("oiio:ColorSpace")
                    if cs:
                        colorspace = str(cs)
                    inp.close()
            except Exception:
                pass

        pad = "#" * s.zfill()
        ext = s.extension()
        pattern = f"{s.basename()}{pad}{ext}"

        results.append(
            {
                "name": s.basename().rstrip("._"),
                "pattern": pattern,
                "frames": len(frame_list),
                "range": range_str,
                "resolution": res_str,
                "pixel_type": pixel_type,
                "compression": compression,
                "colorspace": colorspace,
                "path": directory,
                "first_frame": first_path,
                "extension": ext.lower() if ext else "",
            }
        )
    return results


def _scan_dir_and_pattern(
    input_path: str,
) -> tuple[Path, str, str | None]:
    """Return ``(path_obj, scan_dir, pattern_stem_or_None)`` for open resolution.

    Accepts real files/dirs and non-existent Nuke patterns like
    ``/show/shot.####.exr`` (parent must exist).
    """
    raw = (input_path or "").strip()
    if not raw:
        raise RuntimeError("Empty sequence path")
    p = Path(raw).expanduser()
    if p.is_file() or p.is_dir():
        scan_dir = str(p.parent) if p.is_file() else str(p)
        return p, scan_dir, None
    # Non-existent path: may be a #### / %04d pattern whose parent exists.
    parent = p.parent
    if parent.is_dir():
        stem = sequence_pattern_stem(p.name)
        if stem is not None:
            return p, str(parent), stem
        # Parent exists but name is not a pattern (typo / missing file).
        raise RuntimeError(f"Path does not exist: {input_path}")
    raise RuntimeError(f"Path does not exist: {input_path}")


def find_exr_sequence(input_path: str) -> tuple[list[str], str]:
    """Resolve *input_path* to an ordered list of image file paths + a basename.

    *input_path* may be:
    - a directory  → scan for supported image sequences, pick preferred (EXR first)
    - a single frame file → scan its parent dir, find the sequence it belongs to
    - a Nuke-style pattern (``name.####.ext`` / ``name_%04d.ext``) → match by stem

    Supported extensions: see :data:`IMAGE_SEQUENCE_EXTS` (``.exr``, ``.dpx``,
    ``.png``, ``.jpg`` / ``.jpeg``, ``.webp``).
    """
    p, scan_dir, pattern_stem = _scan_dir_and_pattern(input_path)

    seqs = _find_image_seqs(scan_dir)
    if not seqs:
        exts = ", ".join(sorted(IMAGE_SEQUENCE_EXTS))
        raise RuntimeError(f"No image sequences found in {scan_dir} (supported: {exts})")

    if p.is_file():
        for s in seqs:
            fs = s.frameSet()
            if not fs:
                continue
            for f in fs:
                if Path(s.frame(f)).name == p.name:
                    frames = sorted(fs)
                    return [s.frame(f) for f in frames], s.basename().rstrip("._")
        # Lone single frame that is not part of a multi-frame sequence.
        if is_image_sequence_ext(p.suffix):
            return [str(p)], p.stem
        raise RuntimeError(f"Not a supported image sequence frame: {p.name}")

    if pattern_stem is not None:
        matched = _match_seq_by_stem(seqs, pattern_stem)
        if matched is None:
            raise RuntimeError(
                f"No sequence matching {p.name!r} in {scan_dir} (looked for stem {pattern_stem!r})"
            )
        seq = matched
    else:
        seq = _pick_default_sequence(seqs, scan_dir)
    fs = seq.frameSet()
    if not fs:
        raise RuntimeError(f"Image sequence has no frames in {scan_dir}")
    frames = sorted(fs)
    return [seq.frame(f) for f in frames], seq.basename().rstrip("._")


def find_exr_sequence_info(
    input_path: str,
) -> tuple[list[str], str, list[int], int, fileseq.FileSequence]:
    """Like find_exr_sequence but also returns frame numbers, padding, and the FileSequence.

    Returns (paths, basename, sorted_frame_nums, pad_width, file_sequence).

    Accepts directories, real frame files, and Nuke-style ``####`` / ``%04d``
    patterns whose parent directory exists.
    """
    p, scan_dir, pattern_stem = _scan_dir_and_pattern(input_path)

    seqs = _find_image_seqs(scan_dir)
    if not seqs:
        exts = ", ".join(sorted(IMAGE_SEQUENCE_EXTS))
        raise RuntimeError(f"No image sequences found in {scan_dir} (supported: {exts})")

    seq = None
    if p.is_file():
        for s in seqs:
            fs = s.frameSet()
            if not fs:
                continue
            for f in fs:
                if Path(s.frame(f)).name == p.name:
                    seq = s
                    break
            if seq:
                break
        if seq is None and is_image_sequence_ext(p.suffix):
            # Build a one-frame sequence for an isolated still.
            seq = fileseq.FileSequence(str(p))
    elif pattern_stem is not None:
        seq = _match_seq_by_stem(seqs, pattern_stem)
        if seq is None:
            raise RuntimeError(
                f"No sequence matching {p.name!r} in {scan_dir} (looked for stem {pattern_stem!r})"
            )
    if seq is None:
        seq = _pick_default_sequence(seqs, scan_dir)

    fs = seq.frameSet()
    if not fs:
        # Single-file FileSequence may have an empty frame set depending on path.
        if p.is_file() and is_image_sequence_ext(p.suffix):
            return [str(p)], p.stem, [0], 0, seq
        raise RuntimeError(f"Image sequence has no frames in {scan_dir}")
    frames = sorted(int(f) for f in fs)
    paths = [seq.frame(f) for f in frames]
    name = seq.basename().rstrip("._")
    pad_width = seq.zfill()
    return paths, name, frames, pad_width, seq


def sequence_looks_scene_referred(input_path: str) -> bool:
    """True when the resolved sequence is EXR (or another scene-linear format).

    Used for OCIO source defaults: display-encoded PNG/JPG sequences should
    default toward sRGB, not ``scene_linear``.
    """
    p = Path(input_path)
    if p.is_file() and is_image_sequence_ext(p.suffix):
        return is_scene_referred_image_ext(p.suffix)
    try:
        paths, _ = find_exr_sequence(input_path)
    except Exception:
        return True
    if not paths:
        return True
    return is_scene_referred_image_ext(Path(paths[0]).suffix)
