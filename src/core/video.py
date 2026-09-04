from __future__ import annotations

# libavformat probe limits used when av.open()'s defaults (≈5 MB / 5 s) miss
# stream parameters — typical for vendor-tagged MXFs (e.g. Sony Venice
# X-OCN/XAVC) whose codec metadata sits past the default probe window.
# PyAV bundles libavformat, so this retrieves exactly the data ffprobe
# would, without a subprocess.
_DEEP_PROBE_OPTS = {"probesize": "100M", "analyzeduration": "100M"}


def _stream_dims(stream) -> tuple[int, int]:
    """Best-effort (width, height) without raising; returns (0, 0) on failure."""
    import av

    for getter in (
        lambda: (stream.width, stream.height),
        lambda: (stream.codec_context.coded_width, stream.codec_context.coded_height),
        lambda: (stream.codec_context.width, stream.codec_context.height),
    ):
        try:
            w, h = getter()
            if w and h:
                return int(w), int(h)
        except (AttributeError, av.error.FFmpegError):
            continue
    return 0, 0


def _decode_one_frame_dims(container) -> tuple[int, int]:
    """Decode a single video frame to learn its dimensions."""
    import av

    try:
        for frame in container.decode(video=0):
            return int(frame.width), int(frame.height)
    except (av.error.FFmpegError, StopIteration, RuntimeError):
        pass
    return 0, 0


def stream_fps(stream) -> float:
    """Single clock for probe + preview decode (average → base → codec rate)."""
    import av

    for attr in ("average_rate", "base_rate", "guessed_rate"):
        try:
            rate = getattr(stream, attr, None)
            if rate is not None and float(rate) > 0:
                return float(rate)
        except (AttributeError, TypeError, ValueError, av.error.FFmpegError):
            continue
    try:
        rate = stream.codec_context.rate
        if rate is not None and float(rate) > 0:
            return float(rate)
    except (AttributeError, TypeError, ValueError, av.error.FFmpegError):
        pass
    return 0.0


def _stream_basics(stream) -> tuple[int, int, float, int, str, str]:
    """Pull (w, h, fps, n_frames, codec_name, pix_fmt) from a PyAV video stream."""
    import av

    w, h = _stream_dims(stream)
    fps = stream_fps(stream)
    try:
        n_frames = stream.frames or 0
    except (AttributeError, av.error.FFmpegError):
        n_frames = 0
    try:
        codec_name = stream.codec_context.name or ""
    except (AttributeError, av.error.FFmpegError):
        codec_name = ""
    try:
        pix_fmt = stream.codec_context.pix_fmt or ""
    except (AttributeError, av.error.FFmpegError):
        pix_fmt = ""
    return w, h, fps, n_frames, codec_name, pix_fmt


def probe_video(path: str) -> tuple[int, int, float, int]:
    """Return (width, height, fps, frame_count) using PyAV or the R3D SDK.

    RED R3D / N-RAW (``.r3d`` / ``.nev``) use the optional R3D bridge when
    available. Other formats use PyAV, falling back through codec_context, a
    deep libavformat probe, and a single-frame decode when the default probe
    doesn't surface stream attributes (e.g. Sony Venice MXFs).
    """
    from pathlib import Path

    from .r3d import is_available as r3d_available
    from .r3d import is_r3d_path, probe_r3d

    # Reject OS metadata sidecars early (CLI / forced paths), not only in the browser.
    if is_ignored_media_filename(path):
        raise RuntimeError(f"Not a media file (OS metadata sidecar): {Path(path).name}")

    if is_r3d_path(path):
        if not r3d_available():
            from .r3d import unavailable_reason

            raise RuntimeError(unavailable_reason())
        return probe_r3d(path)

    import av

    duration = time_base = None

    def _gather(opts: dict | None) -> tuple[int, int, float, int]:
        nonlocal duration, time_base
        container = av.open(path, options=opts) if opts else av.open(path)
        try:
            stream = container.streams.video[0]
            w, h, fps, n_frames, _codec, _pix = _stream_basics(stream)
            try:
                duration = duration or stream.duration
                time_base = time_base or stream.time_base
            except (AttributeError, av.error.FFmpegError):
                pass
            if not (w and h):
                w2, h2 = _decode_one_frame_dims(container)
                if w2 and h2:
                    w, h = w2, h2
            return w, h, fps, n_frames
        finally:
            container.close()

    w, h, fps, n_frames = _gather(None)
    if not (w and h) or not fps or not n_frames:
        w2, h2, fps2, n2 = _gather(_DEEP_PROBE_OPTS)
        w = w or w2
        h = h or h2
        fps = fps or fps2
        n_frames = n_frames or n2

    if not fps:
        fps = 24.0
    if not n_frames and duration and time_base:
        n_frames = max(1, int(float(duration * time_base) * fps + 0.5))
    if not n_frames:
        n_frames = 1
    if not (w and h):
        raise RuntimeError(
            f"Could not determine video dimensions for {path!r}. "
            "If this is a Sony Venice/F55 X-OCN MXF, FFmpeg cannot decode "
            "it directly — transcode to ProRes/DNxHR first."
        )
    return w, h, fps, n_frames


def _make_deint_graph(frame, time_base, deint: str):
    """Build a one-in/one-out ``bwdif`` deinterlace filter graph.

    ``deint`` is bwdif's ``deint`` value: ``"interlaced"`` only touches frames
    the decoder flagged as interlaced; ``"all"`` forces every frame.
    ``mode=send_frame`` emits exactly one output frame per input frame, so the
    EXR sequence keeps its 1:1 frame count, ordering, and numbering.
    """
    import av

    graph = av.filter.Graph()
    src = graph.add_buffer(
        width=frame.width,
        height=frame.height,
        format=frame.format.name,
        time_base=time_base,
    )
    bwdif = graph.add("bwdif", f"mode=send_frame:deint={deint}")
    sink = graph.add("buffersink")
    src.link_to(bwdif)
    bwdif.link_to(sink)
    graph.configure()
    return graph


def av_frame_sws_compatible(frame):
    """Copy *frame* planes into a new VideoFrame with default colour metadata.

    oxideav ProRes (and some other bitstreams) decode as YUV but tag
    ``colorspace=RGB`` / reserved-0 primaries. libswscale then returns
    ``ENOTSUP`` on ``rgb24`` / ``rgb48le``. A plane copy drops that metadata
    so swscale can convert. Cheap relative to the convert itself.
    """
    import av

    clean = av.VideoFrame(int(frame.width), int(frame.height), frame.format.name)
    for i, plane in enumerate(frame.planes):
        clean.planes[i].update(plane)
    return clean


def av_frame_reformat(frame, **kwargs):
    """``VideoFrame.reformat`` with a swscale-compatible fallback."""
    try:
        return frame.reformat(**kwargs)
    except Exception:
        return av_frame_sws_compatible(frame).reformat(**kwargs)


def av_frame_to_rgb_ndarray(frame, pix_fmt: str = "rgb48le"):
    """RGB numpy array from a PyAV frame (``rgb48le`` / ``rgb24``, …)."""
    try:
        return frame.to_ndarray(format=pix_fmt)
    except Exception:
        return av_frame_sws_compatible(frame).to_ndarray(format=pix_fmt)


def decode_video_frames(container, stream, deinterlace: str = "auto", log=None):
    """Yield decoded video frames, deinterlacing interlaced sources.

    ``deinterlace``:
      * ``"off"``  – decode straight through (legacy passthrough).
      * ``"auto"`` – run frames through ``bwdif`` with ``deint=interlaced`` so
        only frames flagged interlaced (e.g. 1080i Sony MXF) are deinterlaced;
        progressive footage passes through untouched.
      * ``"on"``   – force ``bwdif`` (``deint=all``) on every frame.

    Because ``bwdif`` runs in ``send_frame`` mode the input→output frame count
    is 1:1, so callers that rely on frame indexing/numbering are unaffected —
    and a combed frame can never reach the EXR writer.
    """
    import av

    if deinterlace == "off":
        yield from container.decode(stream)
        return

    deint = "all" if deinterlace == "on" else "interlaced"
    time_base = getattr(stream, "time_base", None)
    graph = None
    logged = False

    def _drain():
        while True:
            try:
                yield graph.pull()
            except (av.error.BlockingIOError, av.error.EOFError):
                return

    try:
        for frame in container.decode(stream):
            if log and not logged and getattr(frame, "interlaced_frame", False):
                log(
                    "Interlaced source detected \u2014 deinterlacing with bwdif "
                    "(send_frame) so the EXR plate stays progressive"
                )
                logged = True
            if graph is None:
                graph = _make_deint_graph(frame, time_base, deint)
            graph.push(frame)
            yield from _drain()
        if graph is not None:
            graph.push(None)  # flush bwdif's look-ahead buffer at EOF
            yield from _drain()
    except av.error.FFmpegError:
        # If the filter graph can't be built/run for this stream, fall back to
        # undecorated frames rather than failing the whole conversion.
        if graph is None:
            yield from container.decode(stream)
        else:
            raise


def detect_interlaced(path: str) -> bool | None:
    """Return whether the first decodable video frame is flagged interlaced.

    ``True``/``False`` when determinable, ``None`` when it can't be probed.
    """
    import av

    try:
        container = av.open(path)
        try:
            for frame in container.decode(video=0):
                return bool(getattr(frame, "interlaced_frame", False))
        finally:
            container.close()
    except Exception:
        return None
    return None


def guess_video_colorspace_candidates(path: str) -> list[str]:
    """Return a ranked list of OCIO color space name candidates for the video.

    Uses codec, pixel format, and color metadata to infer the transfer
    characteristics. The caller should try each candidate through alias
    resolution until one matches the active OCIO config.
    """
    from .r3d import is_r3d_path, r3d_src_colorspace_candidates

    if is_r3d_path(path):
        return r3d_src_colorspace_candidates(path)

    import av

    try:
        container = av.open(path)
        stream = container.streams.video[0]
        codec = stream.codec_context.name
        pix_fmt = stream.codec_context.pix_fmt or ""
        color_trc = str(getattr(stream.codec_context, "color_trc", "") or "")
        color_primaries = str(getattr(stream.codec_context, "color_primaries", "") or "")
        color_space = str(getattr(stream.codec_context, "color_space", "") or "")
        meta = getattr(stream, "metadata", {}) or {}
        make = (meta.get("make") or meta.get("manufacturer") or "").lower()
        encoder = (meta.get("encoder") or "").lower()
        container.close()
    except Exception:
        return []

    is_10bit = "10" in pix_fmt or "12" in pix_fmt or "16" in pix_fmt
    trc = color_trc.lower()
    prim = color_primaries.lower()
    csp = color_space.lower()

    # Apple Log (iPhone 15/16 Pro cinematic mode, ProRes Log) detection
    # The bundled ACES Studio config (and recent library studio configs) provide
    # "Apple Log" (with aliases) as an Input/Apple colorspace.
    apple_hint = (
        "apple" in (codec or "").lower()
        or "apple" in trc
        or "iphone" in make
        or "iphone" in encoder
        or "apple" in make
        or "apple" in encoder
    )
    if "log" in trc or "log" in (codec or "").lower():
        if apple_hint:
            return ["Apple Log", "Cineon", "scene_linear"]
        return ["Cineon", "Apple Log", "scene_linear"]
    if "linear" in trc:
        return ["scene_linear"]
    if "smpte2084" in trc or "2084" in trc or "pq" in trc:
        return ["Output - Rec.2100-PQ", "Rec.2100-PQ - Display"]
    if "arib-std-b67" in trc or "hlg" in trc:
        return ["Output - Rec.2100-HLG", "Rec.2100-HLG - Display"]

    if codec == "ffv1" and is_10bit:
        return ["scene_linear"]

    # BT.2020 primaries with SDR transfer → still often tagged Rec.709 TRC.
    if "bt2020" in prim or "bt2020" in csp:
        return [
            "Output - Rec.2020",
            "Rec.1886 Rec.2020 - Display",
            "Output - Rec.709",
            "sRGB - Display",
        ]

    # sRGB transfer / primaries (web / screen captures).
    if "iec61966" in trc or "srgb" in trc or "srgb" in prim:
        return [
            "sRGB - Display",
            "Output - sRGB",
            "Output - Rec.709",
            "Rec.1886 Rec.709 - Display",
        ]

    return [
        "Output - Rec.709",
        "Rec.1886 Rec.709 - Display",
        "Gamma 2.4 Rec.709 - Texture",
        "sRGB - Display",
    ]


def resolve_video_src_colorspace(
    path: str,
    ocio_cfg: object | None,
    preferred: str = "",
) -> str:
    """Return a config-valid source space for video preview / convert.

    Prefers *preferred* (tab selection) when it maps into *ocio_cfg*, else
    file metadata guesses, else ``DEFAULT_SRC_V2E``. Without a config, returns
    *preferred* unchanged (or empty).
    """
    from .constants import DEFAULT_SRC_V2E
    from .ocio_utils import find_equivalent_space

    preferred = (preferred or "").strip()
    if ocio_cfg is None:
        return preferred

    if preferred:
        hit = find_equivalent_space(ocio_cfg, preferred)
        if hit:
            return hit

    for name in (*guess_video_colorspace_candidates(path or ""), DEFAULT_SRC_V2E):
        if not name:
            continue
        hit = find_equivalent_space(ocio_cfg, name)
        if hit:
            return hit
    return ""


_VIDEO_SUFFIXES = {
    ".mp4",
    ".mov",
    ".mkv",
    ".avi",
    ".mxf",
    ".webm",
    ".m4v",
    ".ts",
    ".r3d",
    ".nev",
}

# OS / desktop metadata that often sits next to media and can share an extension
# (notably macOS AppleDouble ``._clip.R3D`` on non-HFS volumes and network shares).
_IGNORED_MEDIA_BASENAMES = frozenset({".DS_Store", "Thumbs.db", "desktop.ini"})


def is_ignored_media_filename(name: str) -> bool:
    """True for OS metadata sidecars that must not be treated as video media.

    macOS AppleDouble resource-fork files (``._clip.R3D``) store Finder metadata
    next to the real clip. They end with the same extension as the media and
    would otherwise appear in the video browser as empty R3D/MOV rows.

    *name* may be a basename or a full path; only the final path component is
    inspected.
    """
    from pathlib import Path

    base = Path(name).name
    if base.startswith("._"):
        return True
    return base in _IGNORED_MEDIA_BASENAMES


def scan_video_files(directory: str) -> list[dict[str, str]]:
    """Return summary dicts for every video file in *directory*.

    Each dict: name, resolution, codec, fps, duration, path (full).

    Skips OS metadata sidecars (macOS AppleDouble ``._*``, ``.DS_Store``, etc.).
    """
    from pathlib import Path

    import av

    results: list[dict[str, str]] = []
    try:
        entries = sorted(Path(directory).iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return results

    for entry in entries:
        if not entry.is_file():
            continue
        if is_ignored_media_filename(entry.name):
            continue
        if entry.suffix.lower() not in _VIDEO_SUFFIXES:
            continue
        row: dict[str, str] = {"name": entry.name, "path": str(entry)}
        # R3D / N-RAW — PyAV cannot probe these; use the optional SDK bridge.
        from .r3d import is_available as r3d_available
        from .r3d import is_r3d_path, probe_r3d

        if is_r3d_path(entry):
            try:
                if r3d_available():
                    vw, vh, fps, nframes = probe_r3d(entry)
                    row["resolution"] = f"{vw}x{vh}" if vw and vh else ""
                    row["codec"] = "R3D" if entry.suffix.lower() == ".r3d" else "N-RAW"
                    row["fps"] = f"{fps:.3f}".rstrip("0").rstrip(".") if fps else ""
                    if nframes and fps:
                        dur = nframes / fps
                        row["duration"] = f"{dur:.2f}s"
                    else:
                        row["duration"] = ""
                else:
                    row["resolution"] = ""
                    row["codec"] = "R3D (SDK missing)"
                    row["fps"] = ""
                    row["duration"] = ""
            except Exception:
                row.setdefault("resolution", "")
                row.setdefault("codec", "R3D")
                row.setdefault("fps", "")
                row.setdefault("duration", "")
            results.append(row)
            continue

        try:
            container = av.open(str(entry))
            vs = container.streams.video[0] if container.streams.video else None
            fps = 0.0
            vw = vh = 0
            nframes = 0
            codec_name = ""
            if vs:
                vw, vh, fps, nframes, codec_name, _pix = _stream_basics(vs)

            if vs and (not (vw and vh) or not fps or not codec_name):
                # Default probe was too shallow — retry with a deeper one.
                container.close()
                container = av.open(str(entry), options=_DEEP_PROBE_OPTS)
                vs = container.streams.video[0] if container.streams.video else None
                if vs:
                    w2, h2, fps2, n2, c2, _ = _stream_basics(vs)
                    vw = vw or w2
                    vh = vh or h2
                    fps = fps or fps2
                    nframes = nframes or n2
                    codec_name = codec_name or c2

            if vs:
                row["resolution"] = f"{vw}\u00d7{vh}" if vw and vh else ""
                row["codec"] = codec_name
                if fps:
                    row["fps"] = str(int(fps)) if fps == int(fps) else f"{fps:.3f}"
                else:
                    row["fps"] = ""
                if nframes > 0:
                    row["frames"] = str(nframes)
            if container.duration:
                secs = container.duration / av.time_base
                mins, s = divmod(int(secs), 60)
                hrs, mins = divmod(mins, 60)
                if hrs:
                    row["duration"] = f"{hrs}:{mins:02d}:{s:02d}"
                else:
                    row["duration"] = f"{mins}:{s:02d}"
                if "frames" not in row and fps > 0:
                    row["frames"] = str(int(round(secs * fps)))
            else:
                row["duration"] = ""
            container.close()
        except Exception:
            row.setdefault("resolution", "")
            row.setdefault("codec", "")
            row.setdefault("fps", "")
            row.setdefault("duration", "")
            row.setdefault("frames", "")
        results.append(row)
    return results


def probe_video_metadata(path: str) -> dict[str, str]:
    """Return a dict of human-readable video metadata via PyAV."""
    import av

    result: dict[str, str] = {}
    try:
        container = av.open(path)
        result["Format"] = container.format.long_name or container.format.name

        if container.duration:
            secs = container.duration / av.time_base
            mins, s = divmod(int(secs), 60)
            hrs, mins = divmod(mins, 60)
            if hrs:
                result["Duration"] = f"{hrs}:{mins:02d}:{s:02d}"
            else:
                result["Duration"] = f"{mins}:{s:02d}"
            result["Duration (s)"] = f"{secs:.2f}"

        if container.bit_rate:
            mbps = container.bit_rate / 1_000_000
            result["Bitrate"] = f"{mbps:.2f} Mbps"

        for meta_key, meta_val in container.metadata.items():
            result[f"meta:{meta_key}"] = str(meta_val)

        deep_streams: list | None = None
        for i, stream in enumerate(container.streams.video):
            pfx = "Video" if i == 0 else f"Video[{i}]"
            sw, sh, fps, sframes, codec_name, pix_fmt = _stream_basics(stream)
            if codec_name:
                result[f"{pfx} codec"] = codec_name
            try:
                long = stream.codec_context.codec.long_name
                if long:
                    result[f"{pfx} codec (long)"] = long
            except (AttributeError, av.error.FFmpegError):
                pass
            try:
                profile = stream.codec_context.profile
            except (AttributeError, av.error.FFmpegError):
                profile = None

            if i == 0 and (not (sw and sh) or not fps or not codec_name):
                # Default probe didn't surface enough — re-open with a deep
                # libavformat probe and pull fresh basics.  PyAV bundles
                # libavformat, so we don't need ffprobe.
                if deep_streams is None:
                    try:
                        deep = av.open(path, options=_DEEP_PROBE_OPTS)
                        deep_streams = list(deep.streams.video)
                        deep.close()
                    except Exception:
                        deep_streams = []
                if i < len(deep_streams):
                    dw, dh, dfps, dn, dcodec, dpix = _stream_basics(deep_streams[i])
                    sw = sw or dw
                    sh = sh or dh
                    fps = fps or dfps
                    sframes = sframes or dn
                    if dcodec and not result.get(f"{pfx} codec"):
                        result[f"{pfx} codec"] = dcodec
                    if dpix and not pix_fmt:
                        pix_fmt = dpix

            if sw and sh:
                result[f"{pfx} resolution"] = f"{sw}\u00d7{sh}"
            if pix_fmt:
                result[f"{pfx} pix_fmt"] = pix_fmt
            if fps:
                if fps == int(fps):
                    result[f"{pfx} fps"] = str(int(fps))
                else:
                    result[f"{pfx} fps"] = f"{fps:.3f}"
            if sframes:
                result[f"{pfx} frames"] = str(sframes)
            if profile:
                result[f"{pfx} profile"] = profile

        if container.streams.video:
            interlaced = detect_interlaced(path)
            if interlaced is not None:
                result["Video scan"] = "Interlaced" if interlaced else "Progressive"

        for i, stream in enumerate(container.streams.audio):
            pfx = "Audio" if i == 0 else f"Audio[{i}]"
            result[f"{pfx} codec"] = stream.codec_context.name
            result[f"{pfx} sample_rate"] = f"{stream.codec_context.sample_rate} Hz"
            result[f"{pfx} channels"] = str(stream.codec_context.channels)

        container.close()
    except Exception as e:
        result["error"] = str(e)
    return result
