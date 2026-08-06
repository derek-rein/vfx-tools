"""Command-line interface — optimized for minimal typing vs ffmpeg.

Philosophy
----------
* One clear job per subcommand (``video2exr`` / ``exr2video``).
* Sensible defaults for everything except the input (and usually the output).
* Auto-detect colorspaces when ``--src`` / ``--dst`` are omitted.
* Resolve legacy / cross-config names via :func:`find_equivalent_space`.
* Advanced knobs exist but stay out of the way of the happy path::

      main.py video2exr -i plate.mov
      main.py exr2video -i ./plate
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .core.constants import (
    DEFAULT_DST_E2V,
    DEFAULT_DST_V2E,
    DEFAULT_EXR_COMPRESSION,
    DEFAULT_FRAME_PADDING,
    DEFAULT_SRC_E2V,
    DEFAULT_SRC_V2E,
    DEFAULT_START_FRAME,
    DEFAULT_VIDEO_CODEC,
    EXR_COMPRESSIONS,
    available_video_codecs,
    video_codec_by_key,
)
from .core.ocio_utils import find_equivalent_space, resolve_ocio_for_cli

_CODEC_KEYS = [spec.key for spec in available_video_codecs()]

_EPILOG = """\
examples:
  %(prog)s video2exr -i plate.mov
      → ./plate/plate.####.exr  (Rec.709-ish → ACEScg, DWAA EXR)

  %(prog)s exr2video -i ./plate
      → ./plate.mov  (scene-linear → Rec.709 display, ProRes)

  %(prog)s video2exr -i plate.mov -o /tmp/out --frame-range 1-100
  %(prog)s exr2video -i ./plate -o review.mp4 --codec h264

Color spaces default to auto (probe + OCIO-aware equivalents). Pass --src / --dst
only when you need an override. OCIO defaults to the bundled ACES Studio config
(or $OCIO if set).
"""


def _resolve_config_source(ocio_arg: str | None) -> tuple[str, str]:
    """Return (config_source, config_path) for the pool workers."""
    if ocio_arg:
        return ("", str(Path(ocio_arg).expanduser()))
    env = os.environ.get("OCIO", "")
    if env and Path(env).expanduser().is_file():
        return ("", str(Path(env).expanduser()))
    # Prefer the bundled super ACES studio config (cameras etc.)
    from .core.ocio_utils import config_source_info, list_app_configs

    app = list_app_configs()
    if app:
        key = app[0][0]
        src, path = config_source_info(key)
        if path:
            return (src, path)
        if src:
            return (src, "")
    from .core.ocio_utils import list_builtin_configs

    builtins = list_builtin_configs()
    recommended = [b for b in builtins if b[2]]
    name = recommended[0][0] if recommended else builtins[-1][0]
    return (name, "")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=("Convert between video and EXR with OCIO — simpler happy-path than ffmpeg."),
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--headless", action="store_true", help=argparse.SUPPRESS)
    p.add_argument(
        "--smoke-test",
        action="store_true",
        help="Launch the GUI, verify it initializes, then exit.",
    )
    p.add_argument(
        "--workers",
        type=int,
        default=0,
        help="Parallel workers (0=auto, 1=serial). Default: auto.",
    )
    sub = p.add_subparsers(dest="command")

    v2e = sub.add_parser(
        "video2exr",
        help="Video → OCIO → EXR sequence.",
        description="Decode video, apply OCIO, write an EXR sequence.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Omit -o to write <input_parent>/<stem>/<stem>.####.exr",
    )
    v2e.add_argument("-i", "--input", required=True, help="Input video file")
    v2e.add_argument(
        "-o",
        "--output-dir",
        default=None,
        help="Output directory (default: <input_dir>/<stem>/)",
    )
    v2e.add_argument("--ocio", default=None, help="OCIO config file (default: bundled / $OCIO)")
    v2e.add_argument(
        "--src",
        default=None,
        help="Source color space (default: auto-detect from video / Rec.709-ish)",
    )
    v2e.add_argument(
        "--dst",
        default=None,
        help=f"Destination color space (default: {DEFAULT_DST_V2E} / scene_linear)",
    )
    v2e.add_argument(
        "--exr-compression",
        default=DEFAULT_EXR_COMPRESSION,
        choices=EXR_COMPRESSIONS,
        help=f"EXR compression (default: {DEFAULT_EXR_COMPRESSION})",
    )
    v2e.add_argument(
        "--dwa-level",
        type=float,
        default=None,
        help="DWA level for dwaa/dwab (0=lossless; omit = library default)",
    )
    v2e.add_argument(
        "--zip-level",
        type=int,
        default=None,
        help="ZIP level 1-9 for zip/zips (omit = library default)",
    )
    v2e.add_argument(
        "--scale",
        type=float,
        default=1.0,
        help="Output scale (e.g. 0.5). Default: 1.0",
    )
    v2e.add_argument(
        "--padding",
        type=int,
        default=DEFAULT_FRAME_PADDING,
        help=f"Frame zero-pad width (default: {DEFAULT_FRAME_PADDING})",
    )
    v2e.add_argument(
        "--start-frame",
        type=int,
        default=DEFAULT_START_FRAME,
        help=f"First frame number (default: {DEFAULT_START_FRAME})",
    )
    v2e.add_argument(
        "--frame-range",
        default="",
        help="Nuke-style range (e.g. 1-100, 1-50x2). Default: all frames",
    )
    v2e.add_argument(
        "--deinterlace",
        default="auto",
        choices=["auto", "on", "off"],
        help="Deinterlace (default: auto)",
    )

    e2v = sub.add_parser(
        "exr2video",
        help="EXR sequence → OCIO → video.",
        description="Read an EXR sequence, apply OCIO, encode a video file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Omit -o to write <parent>/<dirname>.mov next to the sequence.",
    )
    e2v.add_argument(
        "-i",
        "--input",
        required=True,
        help="EXR sequence directory, frame, or pattern",
    )
    e2v.add_argument(
        "-o",
        "--output",
        default=None,
        help="Output video path (default: <seq_parent>/<name>.mov)",
    )
    e2v.add_argument(
        "--fps",
        type=float,
        default=24.0,
        help="Frame rate (default: 24)",
    )
    e2v.add_argument("--ocio", default=None, help="OCIO config file (default: bundled / $OCIO)")
    e2v.add_argument(
        "--src",
        default=None,
        help="Source color space (default: EXR metadata / scene_linear)",
    )
    e2v.add_argument(
        "--dst",
        default=None,
        help=f"Destination color space (default: display Rec.709 / {DEFAULT_DST_E2V})",
    )
    e2v.add_argument(
        "--scale",
        type=float,
        default=1.0,
        help="Output scale (e.g. 0.5). Default: 1.0",
    )
    e2v.add_argument(
        "--codec",
        default=DEFAULT_VIDEO_CODEC,
        choices=_CODEC_KEYS,
        help=f"Video codec (default: {DEFAULT_VIDEO_CODEC})",
    )
    e2v.add_argument(
        "--crf",
        type=int,
        default=None,
        help="H.264/HEVC CRF (omit = codec default)",
    )
    e2v.add_argument(
        "--preset",
        default=None,
        help="H.264/HEVC x264/x265 preset (omit = codec default)",
    )
    e2v.add_argument(
        "--frame-range",
        default="",
        help="Nuke-style range (e.g. 1001-1100). Default: all frames",
    )

    return p


def _exr_opts_from_args(args: argparse.Namespace) -> dict[str, str] | None:
    opts: dict[str, str] = {}
    comp = getattr(args, "exr_compression", "")
    if getattr(args, "dwa_level", None) is not None and comp in ("dwaa", "dwab"):
        opts["dwa_compression_level"] = str(args.dwa_level)
    if getattr(args, "zip_level", None) is not None and comp in ("zip", "zips"):
        opts["zip_level"] = str(args.zip_level)
    return opts or None


def _codec_opts_from_args(args: argparse.Namespace) -> dict[str, str] | None:
    opts: dict[str, str] = {}
    if getattr(args, "crf", None) is not None:
        opts["crf"] = str(args.crf)
    if getattr(args, "preset", None):
        opts["preset"] = str(args.preset)
    return opts or None


def default_v2e_output_dir(input_path: str) -> Path:
    """``plate.mov`` → ``<parent>/plate`` (matches GUI auto-fill)."""
    p = Path(input_path).expanduser().resolve()
    return p.parent / p.stem


def default_e2v_output_path(input_path: str, codec_key: str = DEFAULT_VIDEO_CODEC) -> Path:
    """EXR dir/pattern → sibling ``<name>.mov`` (or codec-appropriate ext)."""
    from .core.constants import video_codec_by_key as _by_key

    p = Path(input_path).expanduser()
    if p.is_file():
        # single frame or pattern file — use parent dir name
        base = p.parent
    else:
        base = p
    # If path still has #### in name, strip to parent
    if "#" in base.name:
        base = base.parent
    name = base.name or "output"
    ext = ".mov"
    spec = _by_key(codec_key)
    if spec is not None:
        # Keep container sensible for the codec family.
        if codec_key in ("h264", "hevc", "hevc_8", "hevc_12"):
            ext = ".mp4"
        elif str(codec_key).startswith("dnxhr"):
            ext = ".mxf"
        elif codec_key in ("ffv1", "ffv1_12"):
            ext = ".mkv"
    return base.parent / f"{name}{ext}"


def _resolve_space(
    cfg,
    name: str | None,
    *,
    fallbacks: list[str],
    role: str,
    log,
) -> str:
    """Pick a colorspace that exists on *cfg*.

    Tries explicit *name* (with equivalence remap), then each fallback, then
    the OCIO *role* if present.
    """
    candidates: list[str] = []
    if name:
        candidates.append(name)
    candidates.extend(fallbacks)

    for cand in candidates:
        hit = find_equivalent_space(cfg, cand)
        if hit:
            if name and cand == name and hit != name:
                log(f"Color space remapped: {name} → {hit}")
            elif not name and hit != cand:
                log(f"Using {role}: {hit}")
            return hit
        try:
            cs = cfg.getColorSpace(cand)
            if cs is not None:
                return cs.getName()
        except Exception:
            pass

    # Last resort: OCIO role
    try:
        cs = cfg.getColorSpace(role) if role else None
        if cs is not None:
            hit = cs.getName()
            log(f"Using OCIO role {role!r} → {hit}")
            return hit
    except Exception:
        pass

    tried = ", ".join(repr(c) for c in candidates[:6])
    raise RuntimeError(
        f"Could not resolve a {role or 'color'} space on the active OCIO config "
        f"(tried {tried}). Pass --src/--dst explicitly with a name from the config."
    )


def resolve_v2e_spaces(cfg, args: argparse.Namespace, log) -> tuple[str, str]:
    """Source/destination for video→EXR, with auto-detect when omitted."""
    src_arg = args.src
    if src_arg is None:
        # Probe the file the way the GUI does.
        try:
            from .core.video import guess_video_colorspace_candidates

            guesses = guess_video_colorspace_candidates(args.input)
        except Exception:
            guesses = []
        fallbacks = list(guesses) + [
            DEFAULT_SRC_V2E,
            "Rec.1886 Rec.709 - Display",
            "sRGB Encoded Rec.709 (sRGB)",
            "sRGB - Display",
            "sRGB",
            "rec709",
        ]
        src = _resolve_space(cfg, None, fallbacks=fallbacks, role="color_picking", log=log)
        if guesses:
            log(f"Auto-detected source color space: {src}")
    else:
        src = _resolve_space(
            cfg,
            src_arg,
            fallbacks=[DEFAULT_SRC_V2E],
            role="color_picking",
            log=log,
        )

    dst = _resolve_space(
        cfg,
        args.dst,
        fallbacks=[DEFAULT_DST_V2E, "ACEScg", "ACES2065-1", "linear"],
        role="scene_linear",
        log=log,
    )
    return src, dst


def resolve_e2v_spaces(cfg, args: argparse.Namespace, log) -> tuple[str, str]:
    """Source/destination for EXR→video, with metadata / role defaults."""
    src_arg = args.src
    if src_arg is None:
        probed = ""
        try:
            from .core.sequence import probe_exr_colorspace

            # Prefer directory for probe.
            p = Path(args.input)
            probe_dir = str(p if p.is_dir() else p.parent)
            probed = probe_exr_colorspace(probe_dir) or ""
        except Exception:
            probed = ""
        fallbacks = ([probed] if probed else []) + [
            DEFAULT_SRC_E2V,
            "ACEScg",
            "ACES2065-1",
            "linear",
        ]
        src = _resolve_space(cfg, None, fallbacks=fallbacks, role="scene_linear", log=log)
        if probed:
            log(f"EXR metadata color space: {probed} → {src}")
    else:
        src = _resolve_space(
            cfg,
            src_arg,
            fallbacks=[DEFAULT_SRC_E2V],
            role="scene_linear",
            log=log,
        )

    dst = _resolve_space(
        cfg,
        args.dst,
        fallbacks=[
            DEFAULT_DST_E2V,
            "Rec.1886 Rec.709 - Display",
            "sRGB Encoded Rec.709 (sRGB)",
            "sRGB - Display",
            "sRGB",
            "rec709",
        ],
        role="color_picking",
        log=log,
    )
    return src, dst


def run_cli(args: argparse.Namespace) -> int:
    def _progress(cur: int, total: int) -> None:
        pct = int(100 * cur / total) if total else 0
        print(f"\r[{pct:3d}%] {cur}/{total}", end="", file=sys.stderr, flush=True)

    def _log(msg: str) -> None:
        print(msg, file=sys.stderr)

    try:
        cfg = resolve_ocio_for_cli(args.ocio)
        cs, cp = _resolve_config_source(args.ocio)

        frame_set: set[int] | None = None
        if getattr(args, "frame_range", ""):
            from .core.framerange import parse_frame_range

            frames = parse_frame_range(args.frame_range)
            if frames:
                frame_set = set(frames)

        if args.command == "video2exr":
            from .core.convert import run_video_to_exr

            out_dir = (
                Path(args.output_dir).expanduser()
                if args.output_dir
                else default_v2e_output_dir(args.input)
            )
            if not args.output_dir:
                _log(f"Output directory: {out_dir}")

            src, dst = resolve_v2e_spaces(cfg, args, _log)
            _log(f"OCIO: {src} → {dst}")

            run_video_to_exr(
                args.input,
                out_dir,
                cfg,
                src,
                dst,
                progress=_progress,
                log=_log,
                compression=args.exr_compression,
                workers=args.workers,
                config_source=cs,
                config_path=cp,
                scale=args.scale,
                padding=args.padding,
                start_frame=args.start_frame,
                frame_set=frame_set,
                exr_opts=_exr_opts_from_args(args),
                deinterlace=getattr(args, "deinterlace", "auto"),
            )
            _log(f"Done → {out_dir}")
        else:
            codec_key = args.codec
            spec = video_codec_by_key(codec_key)
            if spec is None or not spec.is_available():
                print(
                    f"\nError: codec {codec_key!r} is not available on this platform.",
                    file=sys.stderr,
                )
                return 1
            codec_name, pix_fmt = spec.libav_codec, spec.pix_fmt
            from .core.convert import run_exr_to_video

            out_path = (
                Path(args.output).expanduser()
                if args.output
                else default_e2v_output_path(args.input, codec_key)
            )
            if not args.output:
                _log(f"Output video: {out_path}")

            src, dst = resolve_e2v_spaces(cfg, args, _log)
            _log(f"OCIO: {src} → {dst}")

            run_exr_to_video(
                args.input,
                out_path,
                cfg,
                src,
                dst,
                args.fps,
                progress=_progress,
                log=_log,
                workers=args.workers,
                config_source=cs,
                config_path=cp,
                scale=args.scale,
                video_codec=codec_name,
                pix_fmt_out=pix_fmt,
                codec_key=codec_key,
                frame_set=frame_set,
                codec_opts=_codec_opts_from_args(args),
            )
            _log(f"Done → {out_path}")
        print(file=sys.stderr)
    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        return 1
    return 0
