from __future__ import annotations

import multiprocessing
import os
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from fractions import Fraction
from pathlib import Path

import av
import numpy as np
import PyOpenColorIO as OCIO

from .exr_io import read_exr, write_exr
from .ocio_utils import (
    get_compositing_space,
    get_overlay_authoring_space,
    linearize_overlay,
    load_config_from_source_info,
    make_cpu_processor,
)
from .pool import _alpha_over_rgb, process_frame_e2v, process_frame_v2e
from .sequence import find_exr_sequence
from .video import decode_video_frames, probe_video

ProgressCallback = Callable[[int, int], None]
LogCallback = Callable[[str], None]

_DEFAULT_WORKERS = min(os.cpu_count() or 4, 8)

# Always spawn — forking a Qt multi-threaded process on Linux deadlocks.
_MP_CTX = multiprocessing.get_context("spawn")


def _process_pool(max_workers: int) -> ProcessPoolExecutor:
    return ProcessPoolExecutor(max_workers=max_workers, mp_context=_MP_CTX)


def _ensure_ocio(
    ocio_cfg: OCIO.Config | None,
    config_source: str,
    config_path: str,
) -> OCIO.Config:
    if ocio_cfg is not None:
        return ocio_cfg
    return load_config_from_source_info(config_source, config_path)


# Common non-integer broadcast / film rates → exact rationals for libav.
_FPS_RATIONALS: dict[float, Fraction] = {
    23.976: Fraction(24000, 1001),
    23.98: Fraction(24000, 1001),
    29.97: Fraction(30000, 1001),
    59.94: Fraction(60000, 1001),
}


def _frame_num_from_path(filepath: str) -> int | None:
    """Extract the trailing frame number from a sequence filename.

    Handles both dot-separated (``name.0001.exr``) and underscore-separated
    (``name_00001.exr``) conventions.
    """
    import re

    stem = Path(filepath).stem
    m = re.search(r"(\d+)$", stem)
    if m:
        return int(m.group(1))
    return None


def _fps_to_rate(fps: float) -> Fraction | int:
    """Return a libav-compatible frame rate (prefer exact rationals)."""
    if fps <= 0:
        return 24
    # Exact integer rates.
    if abs(fps - round(fps)) < 1e-6:
        return int(round(fps))
    for key, rat in _FPS_RATIONALS.items():
        if abs(fps - key) < 0.01:
            return rat
    # Approximate any other float as a reduced fraction (cap denominator).
    return Fraction(fps).limit_denominator(1001)


def _video_metadata(
    src_space: str = "",
    dst_space: str = "",
    codec_key: str = "",
) -> dict[str, str]:
    """Build metadata dict for video container."""
    from .constants import APP_NAME, APP_VERSION

    meta: dict[str, str] = {
        "encoder": f"{APP_NAME} {APP_VERSION}",
    }
    if src_space:
        meta["source_colorspace"] = src_space
    if dst_space:
        meta["dest_colorspace"] = dst_space
    if codec_key:
        meta["codec_preset"] = codec_key
    return meta


def _scaled_dims(w: int, h: int, scale: float) -> tuple[int, int]:
    """Return even-dimensioned (w, h) after applying scale."""
    if scale >= 1.0:
        return w, h
    sw = max(2, int(w * scale + 0.5))
    sh = max(2, int(h * scale + 0.5))
    sw -= sw % 2
    sh -= sh % 2
    return sw, sh


def _default_codec_opts(codec_key: str) -> dict[str, str]:
    """Built-in codec options when the caller does not supply any."""
    from .constants import (
        DEFAULT_CINEFORM_QUALITY,
        DNXHR_PROFILE,
        PRORES_KS_PROFILE,
        PRORES_VT_PROFILE,
    )

    if codec_key in PRORES_KS_PROFILE:
        return {"profile": PRORES_KS_PROFILE[codec_key], "vendor": "apl0"}
    if codec_key in PRORES_VT_PROFILE:
        # VideoToolbox accepts numeric profile 0–5 (same ladder as prores_ks).
        return {"profile": PRORES_VT_PROFILE[codec_key]}
    if codec_key in DNXHR_PROFILE:
        return {"profile": DNXHR_PROFILE[codec_key]}
    if codec_key == "h264":
        return {"crf": "18", "preset": "medium"}
    if codec_key in ("hevc", "hevc_8"):
        return {"crf": "18", "preset": "medium"}
    if codec_key in ("cineform", "cineform_rgb"):
        return {"quality": DEFAULT_CINEFORM_QUALITY}
    if codec_key == "ffv1":
        return {"slicecrc": "1"}
    return {}


def _configure_stream(
    stream,
    codec_key: str,
    codec_opts: dict[str, str] | None = None,
) -> None:
    """Set codec-specific options on a PyAV output stream.

    *codec_opts* (from GUI/CLI) overrides the defaults for the given *codec_key*.
    """
    opts = dict(_default_codec_opts(codec_key))
    if codec_opts:
        opts.update({str(k): str(v) for k, v in codec_opts.items()})
    if opts:
        stream.options = opts


def _cancel_pool(pool: ProcessPoolExecutor, pending: dict) -> None:
    """Best-effort cancel of in-flight pool work."""
    for fut in list(pending):
        fut.cancel()
    pending.clear()
    try:
        pool.shutdown(wait=False, cancel_futures=True)
    except TypeError:
        # Older Python without cancel_futures (not expected on 3.13).
        pool.shutdown(wait=False)


def _bake_slate_to_display(
    slate_rgba_srgb: np.ndarray,
    ocio_cfg: OCIO.Config,
    overlay_authoring_space: str,
    working_space: str,
    dst_space: str,
    slate_overlay_working: np.ndarray | None,
) -> np.ndarray:
    """Take a sRGB-encoded slate (float32 RGBA) through the working-space pipeline.

    Steps mirror the per-frame worker:

    1. sRGB → working (linearise the QPainter-rendered slate)
    2. composite the slate-watermark overlay (already in working space)
    3. working → display

    Returns float32 RGB in display space, ready for ``rgb48le`` encoding.
    """
    rgb = np.ascontiguousarray(slate_rgba_srgb[:, :, :3], dtype=np.float32)
    h, w = rgb.shape[:2]
    cpu_to_working = make_cpu_processor(ocio_cfg, overlay_authoring_space, working_space)
    cpu_to_working.apply(OCIO.PackedImageDesc(rgb, w, h, 3))

    if slate_overlay_working is not None and slate_overlay_working.shape[:2] == (h, w):
        rgb = _alpha_over_rgb(rgb, slate_overlay_working)
        rgb = np.ascontiguousarray(rgb, dtype=np.float32)

    cpu_to_display = make_cpu_processor(ocio_cfg, working_space, dst_space)
    cpu_to_display.apply(OCIO.PackedImageDesc(rgb, w, h, 3))
    return rgb


def _encode_slate_video_frame(
    slate_rgb_display: np.ndarray,
    stream,
    container,
    ow: int,
    oh: int,
    do_resize: bool,
) -> None:
    """Encode a slate (float32 RGB **already in display space**) as a video frame."""
    rgb_u16 = np.clip(slate_rgb_display * 65535.0, 0.0, 65535.0).astype(np.uint16)
    vf = av.VideoFrame.from_ndarray(rgb_u16, format="rgb48le")
    if do_resize:
        vf = vf.reformat(width=ow, height=oh)
    for packet in stream.encode(vf):
        container.mux(packet)


# ---- video -> exr ----------------------------------------------------------


def run_video_to_exr(
    video_path: str,
    output_dir: Path,
    ocio_cfg: OCIO.Config | None,
    src_space: str,
    dst_space: str,
    progress: ProgressCallback | None = None,
    cancel_check: Callable[[], bool] | None = None,
    log: LogCallback | None = None,
    compression: str = "dwaa",
    workers: int = 0,
    config_source: str = "",
    config_path: str = "",
    scale: float = 1.0,
    padding: int = 4,
    start_frame: int = 1001,
    frame_set: set[int] | None = None,
    slate_frame: np.ndarray | None = None,
    exr_opts: dict[str, str] | None = None,
    deinterlace: str = "auto",
) -> None:
    ocio_cfg = _ensure_ocio(ocio_cfg, config_source, config_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    w, h, _fps, total = probe_video(video_path)
    ow, oh = _scaled_dims(w, h, scale)
    render_total = len(frame_set) if frame_set else total
    if log:
        res_info = f"{w}x{h}" if scale >= 1.0 else f"{w}x{h} \u2192 {ow}x{oh}"
        range_info = f", range trimmed to {render_total}" if frame_set else ""
        log(f"Input: {video_path}  ({res_info}, ~{total} frames{range_info})")

    if slate_frame is not None:
        stem = Path(video_path).stem
        fmt = f"0{padding}d"
        slate_num = start_frame - 1
        slate_path = str(output_dir / f"{stem}.{slate_num:{fmt}}.exr")
        rgb3 = np.ascontiguousarray(slate_frame[:, :, :3], dtype=np.float32)
        write_exr(slate_path, rgb3, compression=compression, exr_opts=exr_opts)
        if log:
            log(f"Slate frame written \u2192 {slate_path}")

    n_workers = workers if workers > 0 else _DEFAULT_WORKERS

    if n_workers <= 1 or (not config_source and not config_path):
        _v2e_serial(
            video_path,
            output_dir,
            ocio_cfg,
            src_space,
            dst_space,
            progress,
            cancel_check,
            log,
            compression,
            ow,
            oh,
            total,
            scale,
            padding,
            start_frame,
            frame_set,
            exr_opts=exr_opts,
            deinterlace=deinterlace,
        )
        return

    if log:
        log(f"OCIO: {src_space} \u2192 {dst_space}  ({n_workers} workers)")

    stem = Path(video_path).stem
    container = av.open(video_path)
    stream = container.streams.video[0]
    max_inflight = n_workers * 2
    do_resize = scale < 1.0
    fmt = f"0{padding}d"

    idx = 0
    submitted = 0
    finished = 0
    try:
        with _process_pool(n_workers) as pool:
            pending: dict = {}
            frame_iter = decode_video_frames(container, stream, deinterlace=deinterlace, log=log)
            all_submitted = False

            def _submit_batch() -> None:
                nonlocal idx, submitted, all_submitted
                if all_submitted:
                    return
                while len(pending) < max_inflight:
                    try:
                        frame = next(frame_iter)
                    except StopIteration:
                        all_submitted = True
                        return
                    if cancel_check and cancel_check():
                        _cancel_pool(pool, pending)
                        raise RuntimeError("Cancelled")
                    idx += 1
                    if frame_set and idx not in frame_set:
                        if frame_set and idx > max(frame_set):
                            all_submitted = True
                            return
                        continue
                    if do_resize:
                        frame = frame.reformat(width=ow, height=oh)
                    rgb_u16 = frame.to_ndarray(format="rgb48le")
                    rgb_f32 = rgb_u16.astype(np.float32) * (1.0 / 65535.0)
                    frame_num = start_frame + idx - 1
                    out_path = str(output_dir / f"{stem}.{frame_num:{fmt}}.exr")
                    fut = pool.submit(
                        process_frame_v2e,
                        idx,
                        rgb_f32,
                        out_path,
                        compression,
                        config_source,
                        config_path,
                        src_space,
                        dst_space,
                        exr_opts,
                    )
                    pending[fut] = idx
                    submitted += 1
                    if frame_set and submitted >= len(frame_set):
                        all_submitted = True
                        return

            _submit_batch()
            while pending:
                if cancel_check and cancel_check():
                    _cancel_pool(pool, pending)
                    raise RuntimeError("Cancelled")
                done_set, _ = wait(pending, return_when=FIRST_COMPLETED)
                for done in done_set:
                    done.result()
                    del pending[done]
                    finished += 1
                    if progress:
                        progress(finished, render_total)
                _submit_batch()
    finally:
        container.close()

    if finished == 0:
        raise RuntimeError("No frames decoded from the video file.")
    if log:
        nuke_pat = "#" * padding
        log(f"Wrote {finished} EXR frames \u2192 {output_dir / f'{stem}.{nuke_pat}.exr'}")


def _v2e_serial(
    video_path: str,
    output_dir: Path,
    ocio_cfg: OCIO.Config,
    src_space: str,
    dst_space: str,
    progress: ProgressCallback | None,
    cancel_check: Callable[[], bool] | None,
    log: LogCallback | None,
    compression: str,
    w: int,
    h: int,
    total: int,
    scale: float = 1.0,
    padding: int = 4,
    start_frame: int = 1001,
    frame_set: set[int] | None = None,
    exr_opts: dict[str, str] | None = None,
    deinterlace: str = "auto",
) -> None:
    cpu = make_cpu_processor(ocio_cfg, src_space, dst_space)
    render_total = len(frame_set) if frame_set else total
    if log:
        log(f"OCIO: {src_space} \u2192 {dst_space}  (single-threaded)")

    stem = Path(video_path).stem
    container = av.open(video_path)
    stream = container.streams.video[0]
    frame_buf = np.empty((h, w, 3), dtype=np.float32)
    do_resize = scale < 1.0
    fmt = f"0{padding}d"

    max_idx = max(frame_set) if frame_set else 0
    idx = 0
    written = 0
    try:
        for frame in decode_video_frames(container, stream, deinterlace=deinterlace, log=log):
            if cancel_check and cancel_check():
                raise RuntimeError("Cancelled")
            idx += 1
            if frame_set and idx not in frame_set:
                if idx > max_idx:
                    break
                continue
            if do_resize:
                frame = frame.reformat(width=w, height=h)
            rgb_u16 = frame.to_ndarray(format="rgb48le")
            np.multiply(rgb_u16, 1.0 / 65535.0, out=frame_buf, casting="unsafe")
            desc = OCIO.PackedImageDesc(frame_buf, w, h, 3)
            cpu.apply(desc)
            frame_num = start_frame + idx - 1
            out_path = output_dir / f"{stem}.{frame_num:{fmt}}.exr"
            write_exr(
                str(out_path),
                frame_buf,
                compression=compression,
                src_space=src_space,
                dst_space=dst_space,
                exr_opts=exr_opts,
            )
            written += 1
            if progress:
                progress(written, render_total)
    finally:
        container.close()

    if written == 0:
        raise RuntimeError("No frames decoded from the video file.")
    if log:
        nuke_pat = "#" * padding
        log(f"Wrote {written} EXR frames \u2192 {output_dir / f'{stem}.{nuke_pat}.exr'}")


# ---- exr -> video ----------------------------------------------------------


def run_exr_to_video(
    input_spec: str,
    output_video: Path,
    ocio_cfg: OCIO.Config | None,
    src_space: str,
    dst_space: str,
    fps: float,
    progress: ProgressCallback | None = None,
    cancel_check: Callable[[], bool] | None = None,
    log: LogCallback | None = None,
    video_codec: str = "libx264",
    pix_fmt_out: str = "yuv420p",
    workers: int = 0,
    config_source: str = "",
    config_path: str = "",
    scale: float = 1.0,
    codec_key: str = "h264",
    frame_set: set[int] | None = None,
    slate_frame: np.ndarray | None = None,
    burnin_overlay: np.ndarray | None = None,
    slate_overlay: np.ndarray | None = None,
    overlay_provider: Callable[[int | None], np.ndarray | None] | None = None,
    codec_opts: dict[str, str] | None = None,
) -> None:
    """Encode an EXR sequence (with optional slate / overlays) to a video.

    The encode pipeline runs in a scene-linear *working space*:

    1. EXR src → working (per-worker)
    2. composite *burnin_overlay* (linearised into working space) on every frame
    3. working → display
    4. quantise to uint16 → video stream

    The slate uses the same pipeline but runs in the main process so the
    parallel worker pool stays full of shot frames.

    Parameters
    ----------
    slate_frame
        Raw float32 RGBA slate **in the overlay-authoring space**
        (sRGB).  ``run_exr_to_video`` will OCIO-transform it; do **not**
        pre-transform it.
    burnin_overlay
        Combined burn-in + watermark RGBA overlay (uint8) in sRGB.
        Composited onto every shot frame in working space.
    slate_overlay
        Watermark RGBA overlay (uint8) in sRGB.  Composited onto the
        slate frame only.
    overlay_provider
        Optional callback ``fn(frame_num) -> uint8 RGBA | None`` returning a
        freshly rendered burn-in + watermark overlay (sRGB authoring space) for
        a given frame number.  Used only when a field contains a per-frame
        token such as ``<frame>``; it forces the single-threaded path and
        re-renders + re-linearises the overlay for every frame.  When ``None``
        the static *burnin_overlay* is reused for all frames (the fast path).
    codec_opts
        Optional libav codec options (e.g. ``{"crf": "18", "preset": "medium"}``).
    """
    ocio_cfg = _ensure_ocio(ocio_cfg, config_source, config_path)
    paths, basename = find_exr_sequence(input_spec)

    if frame_set:
        paths = [p for p in paths if _frame_num_from_path(p) in frame_set]

    total = len(paths)
    if total == 0:
        raise RuntimeError("No EXR frames to encode.")

    first = read_exr(paths[0])
    h, w = first.shape[:2]
    ow, oh = _scaled_dims(w, h, scale)
    if log:
        res_info = f"{w}x{h}" if scale >= 1.0 else f"{w}x{h} \u2192 {ow}x{oh}"
        log(f"Sequence: {basename} ({total} frames, {res_info})")

    # Resolve compositing colorspace and pre-linearise overlays --------------
    # Overlays are baked in a wide-gamut scene-linear space (ACES2065-1 / AP0
    # when available) so the alpha-over never clips the user's footage.
    working_space = get_compositing_space(ocio_cfg)
    overlay_auth = get_overlay_authoring_space(ocio_cfg)
    if log:
        log(f"Compositing space: {working_space}  (overlay auth: {overlay_auth})")

    burnin_working: np.ndarray | None = None
    if burnin_overlay is not None:
        burnin_working = linearize_overlay(
            ocio_cfg, burnin_overlay, src_space=overlay_auth, working_space=working_space
        )
    slate_overlay_working: np.ndarray | None = None
    if slate_overlay is not None:
        slate_overlay_working = linearize_overlay(
            ocio_cfg, slate_overlay, src_space=overlay_auth, working_space=working_space
        )

    n_workers = workers if workers > 0 else _DEFAULT_WORKERS

    # A per-frame overlay provider re-renders the overlay each frame, so the
    # worker pool can't share one pre-baked buffer — run single-threaded.
    if overlay_provider is not None or n_workers <= 1 or (not config_source and not config_path):
        _e2v_serial(
            paths,
            output_video,
            ocio_cfg,
            src_space,
            working_space,
            dst_space,
            fps,
            progress,
            cancel_check,
            log,
            video_codec,
            pix_fmt_out,
            ow,
            oh,
            total,
            scale,
            codec_key,
            slate_frame=slate_frame,
            slate_overlay_working=slate_overlay_working,
            burnin_working=burnin_working,
            overlay_auth_space=overlay_auth,
            overlay_provider=overlay_provider,
            codec_opts=codec_opts,
        )
        return

    if log:
        log(f"OCIO: {src_space} \u2192 {working_space} \u2192 {dst_space}  ({n_workers} workers)")

    output_video = Path(output_video)
    output_video.parent.mkdir(parents=True, exist_ok=True)

    rate = _fps_to_rate(fps)
    container = av.open(str(output_video), mode="w")
    container.metadata.update(_video_metadata(src_space, dst_space, codec_key))
    stream = container.add_stream(video_codec, rate=rate)
    stream.width = ow
    stream.height = oh
    stream.pix_fmt = pix_fmt_out
    _configure_stream(stream, codec_key, codec_opts)

    max_inflight = n_workers * 2
    do_resize = scale < 1.0

    try:
        if slate_frame is not None:
            slate_display = _bake_slate_to_display(
                slate_frame,
                ocio_cfg,
                overlay_auth,
                working_space,
                dst_space,
                slate_overlay_working,
            )
            _encode_slate_video_frame(slate_display, stream, container, ow, oh, do_resize)
            if log:
                log("Slate frame encoded as first video frame")

        with _process_pool(n_workers) as pool:
            pending: dict = {}
            ready: dict[int, np.ndarray] = {}
            next_encode = 1
            submit_idx = 0

            def _submit_batch() -> None:
                nonlocal submit_idx
                while len(pending) < max_inflight and submit_idx < total:
                    if cancel_check and cancel_check():
                        _cancel_pool(pool, pending)
                        raise RuntimeError("Cancelled")
                    path = paths[submit_idx]
                    frame_idx = submit_idx + 1
                    submit_idx += 1
                    fut = pool.submit(
                        process_frame_e2v,
                        frame_idx,
                        path,
                        config_source,
                        config_path,
                        src_space,
                        working_space,
                        dst_space,
                        burnin_working,
                    )
                    pending[fut] = frame_idx

            def _drain_ready() -> None:
                nonlocal next_encode
                while next_encode in ready:
                    rgb_u16 = ready.pop(next_encode)
                    vf = av.VideoFrame.from_ndarray(rgb_u16, format="rgb48le")
                    if do_resize:
                        vf = vf.reformat(width=ow, height=oh)
                    for packet in stream.encode(vf):
                        container.mux(packet)
                    if progress:
                        progress(next_encode, total)
                    next_encode += 1

            _submit_batch()
            while pending:
                if cancel_check and cancel_check():
                    _cancel_pool(pool, pending)
                    raise RuntimeError("Cancelled")
                done_set, _ = wait(pending, return_when=FIRST_COMPLETED)
                for done in done_set:
                    pending.pop(done)
                    fidx, rgb_u16 = done.result()
                    ready[fidx] = rgb_u16
                _drain_ready()
                _submit_batch()

            _drain_ready()

        for packet in stream.encode():
            container.mux(packet)
    finally:
        container.close()

    if log:
        log(f"Wrote {output_video} ({total} frames, {fps} fps)")


def _e2v_serial(
    paths: list[str],
    output_video: Path,
    ocio_cfg: OCIO.Config,
    src_space: str,
    working_space: str,
    dst_space: str,
    fps: float,
    progress: ProgressCallback | None,
    cancel_check: Callable[[], bool] | None,
    log: LogCallback | None,
    video_codec: str,
    pix_fmt_out: str,
    w: int,
    h: int,
    total: int,
    scale: float = 1.0,
    codec_key: str = "h264",
    slate_frame: np.ndarray | None = None,
    slate_overlay_working: np.ndarray | None = None,
    burnin_working: np.ndarray | None = None,
    overlay_auth_space: str = "",
    overlay_provider: Callable[[int | None], np.ndarray | None] | None = None,
    codec_opts: dict[str, str] | None = None,
) -> None:
    cpu_to_working = make_cpu_processor(ocio_cfg, src_space, working_space)
    cpu_to_display = make_cpu_processor(ocio_cfg, working_space, dst_space)
    auth_space = overlay_auth_space or get_overlay_authoring_space(ocio_cfg)
    if log:
        log(f"OCIO: {src_space} \u2192 {working_space} \u2192 {dst_space}  (single-threaded)")

    output_video = Path(output_video)
    output_video.parent.mkdir(parents=True, exist_ok=True)

    rate = _fps_to_rate(fps)
    container = av.open(str(output_video), mode="w")
    container.metadata.update(_video_metadata(src_space, dst_space, codec_key))
    stream = container.add_stream(video_codec, rate=rate)
    stream.width = w
    stream.height = h
    stream.pix_fmt = pix_fmt_out
    _configure_stream(stream, codec_key, codec_opts)

    do_resize = scale < 1.0

    try:
        if slate_frame is not None:
            slate_display = _bake_slate_to_display(
                slate_frame,
                ocio_cfg,
                overlay_auth_space or get_overlay_authoring_space(ocio_cfg),
                working_space,
                dst_space,
                slate_overlay_working,
            )
            _encode_slate_video_frame(slate_display, stream, container, w, h, do_resize)
            if log:
                log("Slate frame encoded as first video frame")

        for idx, path in enumerate(paths, 1):
            if cancel_check and cancel_check():
                raise RuntimeError("Cancelled")
            rgb = read_exr(path)
            frame_buf = np.ascontiguousarray(rgb[:, :, :3], dtype=np.float32)
            fh, fw = frame_buf.shape[:2]
            # *w*/*h* are the (possibly scaled) output dims; process at native
            # EXR resolution and only reformat the VideoFrame if needed.
            cpu_to_working.apply(OCIO.PackedImageDesc(frame_buf, fw, fh, 3))

            # Per-frame tokens (e.g. <frame>) require re-rendering + re-linearising
            # the overlay each frame; otherwise reuse the shared pre-baked buffer.
            overlay = burnin_working
            if overlay_provider is not None:
                overlay_u8 = overlay_provider(_frame_num_from_path(path))
                overlay = (
                    linearize_overlay(
                        ocio_cfg, overlay_u8, src_space=auth_space, working_space=working_space
                    )
                    if overlay_u8 is not None
                    else None
                )

            if overlay is not None and overlay.shape[:2] == (fh, fw):
                frame_buf = _alpha_over_rgb(frame_buf, overlay)
                frame_buf = np.ascontiguousarray(frame_buf, dtype=np.float32)

            cpu_to_display.apply(OCIO.PackedImageDesc(frame_buf, fw, fh, 3))
            rgb_u16 = np.clip(frame_buf * 65535.0, 0.0, 65535.0).astype(np.uint16)

            vf = av.VideoFrame.from_ndarray(rgb_u16, format="rgb48le")
            if do_resize or (fw, fh) != (w, h):
                vf = vf.reformat(width=w, height=h)
            for packet in stream.encode(vf):
                container.mux(packet)
            if progress:
                progress(idx, total)
        for packet in stream.encode():
            container.mux(packet)
    finally:
        container.close()

    if log:
        log(f"Wrote {output_video} ({total} frames, {fps} fps)")
