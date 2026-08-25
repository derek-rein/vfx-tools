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
import signal
import sys
import threading
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
from .core.errors import ConversionCancelled
from .core.ocio_utils import find_equivalent_space, resolve_ocio_for_cli

_CODEC_KEYS = [spec.key for spec in available_video_codecs()]

# Set by SIGINT during CLI convert so cancel_check can stop pools cleanly.
_cli_cancel = threading.Event()


def _install_cli_sigint() -> None:
    """Map Ctrl-C to a cooperative cancel flag (and keep default interrupt)."""

    def _handler(signum: int, frame: object) -> None:
        _cli_cancel.set()

    try:
        signal.signal(signal.SIGINT, _handler)
    except (ValueError, OSError):
        # Not on the main thread, or signals unsupported — best-effort only.
        pass


_EPILOG = """\
usage modes
  (no subcommand)     Launch the GUI
  video2exr           Video → OCIO → EXR sequence (CLI convert)
  exr2video           EXR sequence → OCIO → video (CLI convert)

GUI launch (no subcommand)
  %(prog)s
  %(prog)s --open /path/to/plate.####.exr
  %(prog)s --open /path/to/clip.mov --mode video2exr
  %(prog)s --open /path/to/exrs --gui-ocio /path/to/config.ocio

CLI convert examples
  %(prog)s video2exr -i plate.mov
      → ./plate/plate.####.exr  (Rec.709-ish → ACEScg, DWAA EXR)

  %(prog)s exr2video -i ./plate
      → ./plate.mov  (scene-linear → Rec.709 display, default codec)

  %(prog)s video2exr -i plate.mov -o /tmp/out --frame-range 1-100
  %(prog)s exr2video -i ./plate -o review.mp4 --codec h264 --fps 24

Color / OCIO
  Defaults: probe + OCIO-aware equivalents for --src/--dst when omitted.
  Config:   --ocio PATH  (CLI) or --gui-ocio PATH (GUI), else $OCIO, else
            bundled ACES Studio Config v4.

Full docs: docs/cli.md
Nuke menu: integrations/nuke/  (see docs/nuke.md)
"""

_V2E_EPILOG = """\
examples:
  %(prog)s -i plate.mov
  %(prog)s -i plate.mov -o /tmp/exr_out --exr-compression zip
  %(prog)s -i plate.mov --src "sRGB Encoded Rec.709 (sRGB)" --dst ACEScg
  %(prog)s -i plate.mov --frame-range 1-100 --deinterlace auto
  %(prog)s -i plate.mov --ocio /path/to/config.ocio --workers 4

Omit -o to write <input_parent>/<stem>/<stem>.####.exr
See docs/cli.md for the full option list.
"""

_E2V_EPILOG = """\
examples:
  %(prog)s -i ./plate
  %(prog)s -i "./plate.####.exr" -o review.mov --fps 24
  %(prog)s -i ./png_seq -o review.mp4 --codec h264 --fps 24
  %(prog)s -i ./plate --codec prores --src ACEScg --dst "Output - Rec.709"
  %(prog)s -i ./plate --codec h264 --crf 18 --preset medium
  %(prog)s -i ./plate --frame-range 1001-1100 --ocio /path/to/config.ocio

Input: OpenEXR sequences (primary), DPX, or PNG / JPEG / WebP.
Omit -o to write <parent>/<dirname>.mov (extension follows codec).
See docs/cli.md for codecs and bit-depth notes.
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
        description=(
            "EXR Converter — GUI and CLI for video ↔ OpenEXR with OpenColorIO.\n"
            "Run with no subcommand to open the GUI; use video2exr / exr2video to convert."
        ),
        epilog=_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--headless", action="store_true", help=argparse.SUPPRESS)
    p.add_argument(
        "--smoke-test",
        action="store_true",
        help="Launch the GUI, verify it initializes, then exit (CI).",
    )
    p.add_argument(
        "--workers",
        dest="workers_global",
        type=int,
        default=0,
        help="CLI convert: parallel workers (0=auto, 1=serial). Default: auto. "
        "May also be passed after the subcommand.",
    )
    # GUI-only launch helpers (ignored when a convert subcommand is used).
    p.add_argument(
        "--open",
        metavar="PATH",
        default=None,
        help="GUI: open this video / EXR sequence / folder on launch.",
    )
    p.add_argument(
        "--gui-ocio",
        metavar="PATH",
        default=None,
        help="GUI: load this OCIO config file on launch (overrides saved preference).",
    )
    p.add_argument(
        "--mode",
        choices=["auto", "video2exr", "exr2video"],
        default="auto",
        help="GUI: which tab to show (default: auto from --open).",
    )
    sub = p.add_subparsers(dest="command")

    v2e = sub.add_parser(
        "video2exr",
        help="Video → OCIO → EXR sequence.",
        description="Decode video, apply OCIO, write an EXR sequence.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_V2E_EPILOG,
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
    v2e.add_argument(
        "--workers",
        dest="workers_local",
        type=int,
        default=None,
        help="Parallel workers (0=auto, 1=serial). Overrides global --workers.",
    )

    e2v = sub.add_parser(
        "exr2video",
        help="Image sequence (EXR/DPX/PNG/JPG/WebP) → OCIO → video.",
        description=(
            "Read an image sequence (OpenEXR primary; also DPX, PNG, JPEG, WebP), "
            "apply OCIO, encode a video file."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_E2V_EPILOG,
    )
    e2v.add_argument(
        "-i",
        "--input",
        required=True,
        help=(
            "Image sequence directory or any existing frame file "
            "(.exr, .dpx, .png, .jpg/.jpeg, .webp)"
        ),
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
        help=(
            "Source color space (default: metadata / scene_linear for EXR & DPX; "
            "sRGB-ish for PNG/JPEG/WebP display stills)"
        ),
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
    e2v.add_argument(
        "--workers",
        dest="workers_local",
        type=int,
        default=None,
        help="Parallel workers (0=auto, 1=serial). Overrides global --workers.",
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
        # Same probe ranking as GUI preview / tab auto-detect.
        try:
            from .core.video import resolve_video_src_colorspace

            resolved = resolve_video_src_colorspace(args.input, cfg, preferred="")
        except Exception:
            resolved = ""
        if resolved:
            src = resolved
            log(f"Auto-detected source color space: {src}")
        else:
            src = _resolve_space(
                cfg,
                None,
                fallbacks=[
                    DEFAULT_SRC_V2E,
                    "Rec.1886 Rec.709 - Display",
                    "sRGB Encoded Rec.709 (sRGB)",
                    "sRGB - Display",
                    "sRGB",
                    "rec709",
                ],
                role="color_picking",
                log=log,
            )
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
    """Source/destination for image→video, with metadata / role defaults.

    EXR defaults toward scene-linear; display-encoded stills (PNG/JPEG/…) default
    toward sRGB so OCIO does not treat 8-bit sRGB as linear light.
    """
    from .core.sequence import probe_exr_colorspace, sequence_looks_scene_referred

    scene_linear = True
    try:
        scene_linear = sequence_looks_scene_referred(args.input)
    except Exception:
        scene_linear = True

    src_arg = args.src
    if src_arg is None:
        probed = ""
        try:
            from .core.sequence import probe_pixel_colorspace

            p = Path(args.input)
            if p.is_file():
                # Frame path: probe *this* sequence, not sorted[0] in the folder.
                probed = probe_pixel_colorspace(str(p)) or ""
            else:
                probe_dir = str(p if p.is_dir() else p.parent)
                probed = probe_exr_colorspace(probe_dir) or ""
        except Exception:
            probed = ""
        if scene_linear:
            fallbacks = ([probed] if probed else []) + [
                DEFAULT_SRC_E2V,
                "ACEScg",
                "ACES2065-1",
                "linear",
            ]
            role = "scene_linear"
        else:
            fallbacks = ([probed] if probed else []) + [
                "sRGB Encoded Rec.709 (sRGB)",
                "sRGB - Texture",
                "Utility - sRGB - Texture",
                "sRGB",
                "Output - Rec.709",
            ]
            role = "color_picking"
        src = _resolve_space(cfg, None, fallbacks=fallbacks, role=role, log=log)
        if probed:
            log(f"Image metadata color space: {probed} → {src}")
        elif not scene_linear:
            log(f"Display still sequence — source color space: {src}")
    else:
        src = _resolve_space(
            cfg,
            src_arg,
            fallbacks=[DEFAULT_SRC_E2V if scene_linear else "sRGB Encoded Rec.709 (sRGB)"],
            role="scene_linear" if scene_linear else "color_picking",
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

    # Subcommand --workers overrides parent global.
    workers_local = getattr(args, "workers_local", None)
    workers = (
        int(workers_local)
        if workers_local is not None
        else int(getattr(args, "workers_global", 0) or 0)
    )

    _cli_cancel.clear()
    _install_cli_sigint()

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
                cancel_check=_cli_cancel.is_set,
                log=_log,
                compression=args.exr_compression,
                workers=workers,
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
                cancel_check=_cli_cancel.is_set,
                log=_log,
                workers=workers,
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
    except ConversionCancelled:
        print("\nCancelled.", file=sys.stderr)
        return 130
    except KeyboardInterrupt:
        _cli_cancel.set()
        print("\nCancelled.", file=sys.stderr)
        return 130
    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        return 1
    return 0
