from __future__ import annotations

import numpy as np
import OpenImageIO as oiio


def _display_window(spec) -> tuple[int, int, int, int]:
    """Return (x, y, width, height) of the display window from an OIIO ImageSpec.

    Falls back to data window dimensions when full_width/full_height are unset.
    """
    if spec.full_width > 0 and spec.full_height > 0:
        return spec.full_x, spec.full_y, spec.full_width, spec.full_height
    return 0, 0, spec.width, spec.height


def apply_exr_compression_attrs(
    spec: oiio.ImageSpec,
    compression: str,
    exr_opts: dict[str, str] | None = None,
) -> None:
    """Set compression name and optional DWA / ZIP level attributes on *spec*."""
    spec.attribute("compression", compression)
    if not exr_opts:
        return
    if compression in ("dwaa", "dwab"):
        level = exr_opts.get("dwa_compression_level")
        if level is not None:
            try:
                # OIIO accepts the unprefixed name and stores it as
                # openexr:dwaCompressionLevel on the written file.
                spec.attribute("dwaCompressionLevel", float(level))
            except (TypeError, ValueError):
                pass
    elif compression in ("zip", "zips"):
        level = exr_opts.get("zip_level")
        if level is not None:
            try:
                # OIIO / OpenEXR zip compression level (1–9).
                spec.attribute("compressionlevel", int(level))
            except (TypeError, ValueError):
                pass


def read_exr(path: str) -> np.ndarray:
    """Read an EXR file and return float32 array (H, W, 3).

    Always extracts RGB only, even if the source has alpha or more channels.
    Crops to the display window, discarding any overscan from the data window.

    Raises
    ------
    RuntimeError
        If the file cannot be opened or pixels cannot be read.
    """
    buf = oiio.ImageBuf(path)
    if buf.has_error:
        raise RuntimeError(f"Failed to open EXR {path!r}: {buf.geterror()}")
    spec = buf.spec()
    dx, dy, dw, dh = _display_window(spec)
    if dw <= 0 or dh <= 0:
        raise RuntimeError(f"Invalid display window in EXR {path!r}")
    roi = oiio.ROI(dx, dx + dw, dy, dy + dh, 0, 1, 0, min(spec.nchannels, 3))
    pixels = np.ascontiguousarray(buf.get_pixels(oiio.FLOAT, roi), dtype=np.float32)
    if buf.has_error:
        raise RuntimeError(f"Failed to read pixels from EXR {path!r}: {buf.geterror()}")
    if pixels is None or pixels.size == 0:
        raise RuntimeError(f"Empty pixel buffer from EXR {path!r}")
    if pixels.ndim == 3 and pixels.shape[2] >= 3:
        return pixels[:, :, :3]
    if pixels.ndim == 3 and pixels.shape[2] == 1:
        return np.repeat(pixels, 3, axis=2)
    if pixels.ndim == 2:
        return np.repeat(pixels[:, :, np.newaxis], 3, axis=2)
    raise RuntimeError(f"Unsupported pixel layout in EXR {path!r}: shape={pixels.shape}")


def read_exr_uint16(path: str) -> np.ndarray | None:
    """Read an EXR and return uint16 RGB in display window, or ``None`` on failure."""
    try:
        buf = oiio.ImageBuf(path)
        if buf.has_error:
            return None
        spec = buf.spec()
        dx, dy, dw, dh = _display_window(spec)
        roi = oiio.ROI(dx, dx + dw, dy, dy + dh, 0, 1, 0, min(spec.nchannels, 3))
        pixels = buf.get_pixels(oiio.UINT16, roi)
        if pixels is None:
            return None
        if pixels.ndim == 3 and pixels.shape[2] >= 3:
            return np.ascontiguousarray(pixels[:, :, :3])
        if pixels.ndim == 3 and pixels.shape[2] == 1:
            return np.repeat(pixels, 3, axis=2)
        return np.ascontiguousarray(pixels)
    except Exception:
        return None


def read_exr_safe(path: str, w: int, h: int) -> np.ndarray:
    """Read an EXR, returning a black frame of (h, w, 3) on any error or size mismatch."""
    try:
        rgb = read_exr(path)
        if rgb.shape[:2] != (h, w):
            return np.zeros((h, w, 3), dtype=np.float32)
        return rgb
    except Exception:
        return np.zeros((h, w, 3), dtype=np.float32)


def write_exr(
    path: str,
    rgb: np.ndarray,
    compression: str = "dwaa",
    src_space: str = "",
    dst_space: str = "",
    exr_opts: dict[str, str] | None = None,
) -> None:
    """Write a float32 (H, W, 3) array as half-float EXR.

    Raises
    ------
    RuntimeError
        If the write fails (disk full, permissions, unsupported compression, …).
    """
    from .constants import APP_NAME, APP_VERSION

    h, w = rgb.shape[:2]
    spec = oiio.ImageSpec(w, h, 3, oiio.HALF)
    apply_exr_compression_attrs(spec, compression, exr_opts)
    spec.attribute("Software", f"{APP_NAME} {APP_VERSION}")
    if dst_space:
        spec.attribute("oiio:ColorSpace", dst_space)
    if src_space:
        spec.attribute("exrconverter:srcColorSpace", src_space)
    if dst_space:
        spec.attribute("exrconverter:dstColorSpace", dst_space)
    buf = oiio.ImageBuf(spec)
    buf.set_pixels(
        oiio.ROI(0, w, 0, h, 0, 1, 0, 3),
        np.ascontiguousarray(rgb[:, :, :3], dtype=np.float32),
    )
    ok = buf.write(path)
    if not ok or buf.has_error:
        err = buf.geterror() or "unknown OIIO write error"
        raise RuntimeError(f"Failed to write EXR {path!r}: {err}")
