from __future__ import annotations

from pathlib import Path

import fileseq


def _probe_resolution(filepath: str) -> tuple[int, int]:
    """Read display-window width and height from an EXR header without decoding pixels."""
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


def _find_exr_seqs(directory: str) -> list[fileseq.FileSequence]:
    """Return all .exr FileSequences found in *directory*, sorted by basename."""
    seqs = fileseq.findSequencesOnDisk(directory)
    exr = [s for s in seqs if s.extension().lower() == ".exr" and s.frameSet()]
    return sorted(exr, key=lambda s: s.basename())


def probe_exr_colorspace(directory: str) -> str:
    """Return the oiio:ColorSpace from the first EXR in *directory*, or ''."""
    for s in _find_exr_seqs(directory):
        first_frame = list(s.frameSet())[0]
        path = s.frame(first_frame)
        try:
            import OpenImageIO as oiio

            inp = oiio.ImageInput.open(path)
            if inp:
                cs = inp.spec().getattribute("oiio:ColorSpace")
                inp.close()
                if cs:
                    return str(cs)
        except Exception:
            pass
        break
    return ""


def probe_exr_metadata(filepath: str) -> dict[str, str]:
    """Return a dict of human-readable EXR metadata from the first frame."""
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
    """Return metadata dicts for every EXR sequence found in *directory*.

    Each dict contains:
        name       - sequence basename (e.g. "beauty")
        frames     - number of frames
        range      - human-readable frame range string
        resolution - "W\u00d7H" string from the first frame
        path       - the directory scanned
    """
    results = []
    for s in _find_exr_seqs(directory):
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
        pattern = f"{s.basename()}{pad}{s.extension()}"

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
            }
        )
    return results


def find_exr_sequence(input_path: str) -> tuple[list[str], str]:
    """Resolve *input_path* to an ordered list of EXR file paths + a basename.

    *input_path* may be:
    - a directory  -> scan for .exr sequences, pick the first
    - a single .exr file -> scan its parent dir, find the sequence it belongs to
    """
    p = Path(input_path)
    if p.is_file():
        scan_dir = str(p.parent)
    elif p.is_dir():
        scan_dir = str(p)
    else:
        raise RuntimeError(f"Path does not exist: {input_path}")

    exr_seqs = _find_exr_seqs(scan_dir)
    if not exr_seqs:
        raise RuntimeError(f"No EXR sequences found in {scan_dir}")

    if p.is_file():
        for s in exr_seqs:
            fs = s.frameSet()
            if not fs:
                continue
            for f in fs:
                if Path(s.frame(f)).name == p.name:
                    frames = sorted(fs)
                    return [s.frame(f) for f in frames], s.basename().rstrip("._")
        return [str(p)], p.stem

    seq = exr_seqs[0]
    fs = seq.frameSet()
    if not fs:
        raise RuntimeError(f"EXR sequence has no frames in {scan_dir}")
    frames = sorted(fs)
    return [seq.frame(f) for f in frames], seq.basename().rstrip("._")


def find_exr_sequence_info(
    input_path: str,
) -> tuple[list[str], str, list[int], int, fileseq.FileSequence]:
    """Like find_exr_sequence but also returns frame numbers, padding, and the FileSequence.

    Returns (paths, basename, sorted_frame_nums, pad_width, file_sequence).
    """
    p = Path(input_path)
    if p.is_file():
        scan_dir = str(p.parent)
    elif p.is_dir():
        scan_dir = str(p)
    else:
        raise RuntimeError(f"Path does not exist: {input_path}")

    exr_seqs = _find_exr_seqs(scan_dir)
    if not exr_seqs:
        raise RuntimeError(f"No EXR sequences found in {scan_dir}")

    seq = None
    if p.is_file():
        for s in exr_seqs:
            fs = s.frameSet()
            if not fs:
                continue
            for f in fs:
                if Path(s.frame(f)).name == p.name:
                    seq = s
                    break
            if seq:
                break
    if seq is None:
        seq = exr_seqs[0]

    fs = seq.frameSet()
    if not fs:
        raise RuntimeError(f"EXR sequence has no frames in {scan_dir}")
    frames = sorted(int(f) for f in fs)
    paths = [seq.frame(f) for f in frames]
    name = seq.basename().rstrip("._")
    pad_width = seq.zfill()
    return paths, name, frames, pad_width, seq
