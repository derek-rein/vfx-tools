"""Slate editor widgets: form panel + preview dialog.

The ``SlateDialog`` is opened from the conversion tabs when the user checks
"Prepend slate" and clicks "Edit Slate…".  It contains a form on the left
and a live QPainter-driven preview on the right.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from typing import TYPE_CHECKING

from PySide6.QtCore import (
    QEvent,
    QPointF,
    QRectF,
    QRegularExpression,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFontDatabase,
    QFontMetricsF,
    QMouseEvent,
    QPainter,
    QPen,
    QRegularExpressionValidator,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGraphicsScene,
    QGraphicsView,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QStatusBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..render.slate import SLATE_COLORSPACE, render_slate_frame
from ..services.cache_prefs import (
    cache_budget_bytes,
    load_cache_budget_pct,
    save_cache_budget_pct,
    total_ram_bytes,
)
from ..services.exr_prefetch import ExrPrefetchService
from ..services.frame_cache import FrameCache
from .ocio_gpu_plane import (
    OcioGpuImagePlane,
    gpu_ocio_available,
    nuke_viewer_gamma_power,
)
from .size_grip import SizeGrip
from .timeline_slider import TimelineSlider
from .token_line_edit import TokenLineEdit

if TYPE_CHECKING:
    import numpy as np

log = logging.getLogger(__name__)

# Playback RAM cache — float16 HDR working RGB; 75% prefetch ahead, 25% lookback.
_PREFETCH_WORKERS = 4

ZOOM_MIN = 0.05
ZOOM_MAX = 5.0


def _alpha_over_rgb(bg_rgb_f32, overlay_rgba_u8):
    """Vectorised straight-alpha 'over' composite of an RGBA8 overlay onto an
    RGB float32 background.  Returns a new float32 RGB array — leaves the
    input untouched.

    Used by the slate dialog to bake burn-in + watermark on top of the
    display-space frame, matching what :mod:`convert` does for the final
    rendered output.
    """
    import numpy as np

    if overlay_rgba_u8.shape[2] < 4:
        return bg_rgb_f32
    a = overlay_rgba_u8[..., 3:4].astype(np.float32) / 255.0
    fg = overlay_rgba_u8[..., :3].astype(np.float32) / 255.0
    return fg * a + bg_rgb_f32 * (1.0 - a)


def _alpha_over_linear(bg_rgb_f32, overlay_rgba_lin_f32):
    """Same as :func:`_alpha_over_rgb` but with an already-linearised RGBA float32
    overlay — skips the per-frame ``/255`` and OCIO call.
    """
    if overlay_rgba_lin_f32 is None or overlay_rgba_lin_f32.shape[2] < 4:
        return bg_rgb_f32
    a = overlay_rgba_lin_f32[..., 3:4]
    fg = overlay_rgba_lin_f32[..., :3]
    return fg * a + bg_rgb_f32 * (1.0 - a)


# ---------------------------------------------------------------------------
# Nuke-style custom-painted slider
# ---------------------------------------------------------------------------


class NukeSlider(QWidget):
    """QPainter viewer slider with procedural value curves (gain / gamma).

    Mapping is derived only from ``val_min`` / ``val_max`` / ``map_mode``:

    - ``linear`` — uniform in value
    - ``log`` — uniform in log(value) (gain)
    - ``pivot`` — 1.0 pinned at mid-track (gamma): each side is a gentle
      asinh curve so resolution is denser near the pivot (Nuke-style feel)

    Tick candidates are nice numbers from the active range + curve; density
    and which labels draw scale with track width (no hard-coded tick tables).
    """

    valueChanged = Signal(float)

    _BG = QColor(0x1E, 0x1E, 0x1E)
    _GROOVE = QColor(0x3C, 0x3C, 0x3C)
    _TICK = QColor(0x58, 0x58, 0x58)
    _LABEL_COLOR = QColor(0x88, 0x88, 0x88)
    _INDICATOR = QColor(0xC8, 0x78, 0x28)  # Nuke orange
    _DEFAULT_MARK = QColor(0x50, 0x50, 0x50)
    _PIVOT = 1.0
    # asinh steepness: higher → more track budget near the pivot.
    _PIVOT_K = 1.6
    # Target horizontal gap between tick marks / labels (px).
    _MIN_TICK_GAP_PX = 14.0
    _MIN_LABEL_GAP_PX = 22.0

    def __init__(
        self,
        default: float,
        val_min: float = 0.0,
        val_max: float = 1.0,
        *,
        map_mode: str = "linear",
        ticks: list[float] | None = None,
        log_scale: bool = False,
        nuke_gamma_map: bool = False,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        # Back-compat kwargs from older call sites.
        if nuke_gamma_map:
            map_mode = "pivot"
        elif log_scale:
            map_mode = "log"
        mode = map_mode if map_mode in ("linear", "log", "pivot") else "linear"

        self._default = default
        self._map_mode = mode
        self._val_min = float(val_min)
        self._val_max = float(val_max)
        self._ticks_override = list(ticks) if ticks is not None else None
        self._value = max(self._val_min, min(self._val_max, float(default)))
        self._dragging = False

        if self._map_mode == "log":
            self._log_min = math.log(max(self._val_min, 1e-10))
            self._log_max = math.log(max(self._val_max, 1e-10))
        else:
            self._log_min = 0.0
            self._log_max = 1.0

        # Precompute asinh denominators for pivot mode (range-driven).
        k = self._PIVOT_K
        pivot = self._PIVOT
        self._pivot_den_lo = math.asinh(k * max(pivot - self._val_min, 0.0))
        self._pivot_den_hi = math.asinh(k * max(self._val_max - pivot, 0.0))

        self.setMinimumHeight(22)
        self.setMaximumHeight(22)
        self.setMinimumWidth(80)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self._font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        self._font.setPointSize(8)

    # -- value <-> normalised track position t ∈ [0, 1] --

    def _value_to_t(self, val: float) -> float:
        val = max(self._val_min, min(self._val_max, val))
        if self._map_mode == "pivot":
            return self._pivot_value_to_t(val)
        if self._map_mode == "log":
            return (math.log(max(val, 1e-10)) - self._log_min) / max(
                self._log_max - self._log_min, 1e-10
            )
        return (val - self._val_min) / max(self._val_max - self._val_min, 1e-10)

    def _t_to_value(self, t: float) -> float:
        t = max(0.0, min(1.0, t))
        if self._map_mode == "pivot":
            return self._pivot_t_to_value(t)
        if self._map_mode == "log":
            return math.exp(self._log_min + t * (self._log_max - self._log_min))
        return self._val_min + t * (self._val_max - self._val_min)

    def _pivot_value_to_t(self, val: float) -> float:
        """Map value → t with pivot fixed at 0.5; denser resolution near pivot."""
        k = self._PIVOT_K
        pivot = self._PIVOT
        if val >= pivot:
            den = self._pivot_den_hi
            if den <= 1e-12:
                return 0.5
            return 0.5 + 0.5 * math.asinh(k * (val - pivot)) / den
        den = self._pivot_den_lo
        if den <= 1e-12:
            return 0.5
        return 0.5 - 0.5 * math.asinh(k * (pivot - val)) / den

    def _pivot_t_to_value(self, t: float) -> float:
        k = self._PIVOT_K
        pivot = self._PIVOT
        if t >= 0.5:
            u = (t - 0.5) / 0.5
            den = self._pivot_den_hi
            if den <= 1e-12 or k <= 1e-12:
                return pivot
            return pivot + math.sinh(u * den) / k
        u = (0.5 - t) / 0.5
        den = self._pivot_den_lo
        if den <= 1e-12 or k <= 1e-12:
            return pivot
        return pivot - math.sinh(u * den) / k

    def _margin_left(self) -> int:
        return 2

    def _margin_right(self) -> int:
        return 2

    def _track_x(self) -> tuple[int, int]:
        ml = self._margin_left()
        return ml, self.width() - self._margin_right() - ml

    def _t_to_x(self, t: float) -> float:
        ml, track_w = self._track_x()
        return ml + t * track_w

    def _x_to_t(self, x: float) -> float:
        ml, track_w = self._track_x()
        if track_w <= 0:
            return 0.0
        return (x - ml) / track_w

    # -- procedural nice ticks (range + width driven) --

    def _target_tick_count(self) -> int:
        """How many tick candidates fit the current track width."""
        _, track_w = self._track_x()
        if track_w <= 0:
            return 5
        return max(3, min(24, int(track_w / self._MIN_TICK_GAP_PX)))

    @staticmethod
    def _nice_step(span: float, target_intervals: int) -> float:
        """1–2–5 × 10^k step covering *span* in ~*target_intervals* steps."""
        if span <= 0 or target_intervals <= 0:
            return 1.0
        raw = span / max(target_intervals, 1)
        if raw <= 0:
            return 1.0
        exp = math.floor(math.log10(raw))
        base = 10.0**exp
        frac = raw / base
        if frac <= 1.5:
            nice = 1.0
        elif frac <= 3.5:
            nice = 2.0
        elif frac <= 7.5:
            nice = 5.0
        else:
            nice = 10.0
        return nice * base

    @classmethod
    def _nice_linear_ticks(cls, vmin: float, vmax: float, target_n: int) -> list[float]:
        """Even nice-number ticks on a linear span."""
        if vmax <= vmin:
            return [vmin]
        step = cls._nice_step(vmax - vmin, max(target_n - 1, 1))
        if step <= 0:
            return [vmin, vmax]
        # Align start to a multiple of step at or below vmin.
        start = math.floor(vmin / step + 1e-12) * step
        ticks: list[float] = []
        # Guard against float runaway.
        v = start
        for _ in range(512):
            if v > vmax + step * 0.5:
                break
            if v >= vmin - step * 1e-9:
                ticks.append(round(v, 12))
            v += step
        if not ticks or ticks[0] > vmin + 1e-9:
            ticks.insert(0, vmin)
        if ticks[-1] < vmax - 1e-9:
            ticks.append(vmax)
        # Dedup near-equals
        out: list[float] = []
        for t in ticks:
            if not out or abs(t - out[-1]) > step * 1e-6:
                out.append(t)
        return out

    @classmethod
    def _nice_log_ticks(cls, vmin: float, vmax: float, target_n: int) -> list[float]:
        """1–2–5 decade ticks, thinned when the range is dense for *target_n*."""
        if vmax <= vmin:
            return [max(vmin, 1e-10)]
        lo = max(vmin, 1e-10)
        hi = max(vmax, lo * 1.0001)
        exp0 = math.floor(math.log10(lo))
        exp1 = math.ceil(math.log10(hi))
        # Multipliers: full 1-2-5, or thinner 1-5 / decades only when crowded.
        decades = max(exp1 - exp0, 1)
        if target_n >= decades * 3:
            mults = (1.0, 2.0, 5.0)
        elif target_n >= decades * 1.5:
            mults = (1.0, 5.0)
        else:
            mults = (1.0,)
        ticks: list[float] = []
        for e in range(exp0, exp1 + 1):
            for m in mults:
                v = m * (10.0**e)
                if lo <= v <= hi:
                    ticks.append(v)
        if not ticks or ticks[0] > lo * 1.01:
            ticks.insert(0, lo)
        if ticks[-1] < hi * 0.99:
            ticks.append(hi)
        return ticks

    def _generate_ticks(self) -> list[float]:
        """Build tick values from range + map mode + current width budget."""
        vmin, vmax = self._val_min, self._val_max
        if vmax <= vmin:
            return [vmin]
        n = self._target_tick_count()

        if self._map_mode == "log":
            return self._nice_log_ticks(vmin, vmax, n)

        if self._map_mode == "pivot":
            pivot = self._PIVOT
            # Split budget proportional to track halves (always 50/50 in t).
            n_lo = max(2, n // 2)
            n_hi = max(2, n - n_lo)
            ticks: list[float] = []
            if vmin < pivot:
                ticks.extend(self._nice_linear_ticks(vmin, pivot, n_lo))
            else:
                ticks.append(vmin)
            if vmax > pivot:
                hi = self._nice_linear_ticks(pivot, vmax, n_hi)
                # Avoid duplicating the pivot from both halves.
                ticks.extend(v for v in hi if abs(v - pivot) > 1e-12)
            else:
                if abs(ticks[-1] - vmax) > 1e-12:
                    ticks.append(vmax)
            return sorted({round(v, 12) for v in ticks})

        return self._nice_linear_ticks(vmin, vmax, n)

    def _ticks_for_paint(self) -> list[float]:
        if self._ticks_override is not None:
            return self._ticks_override
        return self._generate_ticks()

    @staticmethod
    def _format_tick(tick_val: float) -> str:
        if abs(tick_val - round(tick_val)) < 1e-9 and abs(tick_val) >= 1.0 - 1e-9:
            return str(int(round(tick_val)))
        if abs(tick_val) >= 10:
            return f"{tick_val:.0f}"
        if abs(tick_val) >= 1:
            # Prefer "1" / "2" over "1.0" when close to integers.
            if abs(tick_val - round(tick_val)) < 1e-6:
                return str(int(round(tick_val)))
            return f"{tick_val:.1f}"
        if abs(tick_val) >= 0.1:
            return f"{tick_val:.1f}"
        if abs(tick_val) >= 0.01:
            return f"{tick_val:.2f}"
        return f"{tick_val:g}"

    # -- public interface --

    def value(self) -> float:
        return self._value

    def setValue(self, val: float) -> None:
        val = max(self._val_min, min(self._val_max, val))
        if val != self._value:
            self._value = val
            self.update()

    # -- painting --

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()

        # background
        p.fillRect(0, 0, w, h, self._BG)

        groove_y = h // 2
        ml, track_w = self._track_x()

        # groove line
        p.setPen(QPen(self._GROOVE, 1))
        p.drawLine(ml, groove_y, ml + track_w, groove_y)

        # default-value mark (thin dim vertical line)
        def_t = self._value_to_t(self._default)
        def_x = self._t_to_x(def_t)
        p.setPen(QPen(self._DEFAULT_MARK, 1))
        p.drawLine(int(def_x), 2, int(def_x), h - 2)

        # Tick marks + labels: candidates from curve/range; labels thinned by width.
        p.setFont(self._font)
        fm = QFontMetricsF(self._font)
        last_label_right = -1e9
        last_tick_x = -1e9
        for tick_val in self._ticks_for_paint():
            t = self._value_to_t(tick_val)
            tx = self._t_to_x(t)
            # Cull tick marks that sit on top of each other when narrowed.
            if tx - last_tick_x < self._MIN_TICK_GAP_PX * 0.55:
                continue
            last_tick_x = tx
            p.setPen(QPen(self._TICK, 1))
            p.drawLine(int(tx), groove_y - 3, int(tx), groove_y + 3)

            label = self._format_tick(tick_val)
            lw = fm.horizontalAdvance(label)
            lx = tx - lw / 2
            lx = max(0.0, min(float(w) - lw, lx))
            if lx < last_label_right + self._MIN_LABEL_GAP_PX:
                continue
            p.setPen(self._LABEL_COLOR)
            p.drawText(int(lx), h - 2, label)
            last_label_right = lx + lw

        # indicator line (current value)
        cur_t = self._value_to_t(self._value)
        cur_x = self._t_to_x(cur_t)
        p.setPen(QPen(self._INDICATOR, 2))
        p.drawLine(int(cur_x), 1, int(cur_x), h - 1)

        p.end()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        # Tick density / labels reflow from width.
        super().resizeEvent(event)
        self.update()

    # -- mouse interaction --

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            # Right-click resets to default (common in viewers; complements double-click).
            if self._value != self._default:
                self._value = self._default
                self.update()
                self.valueChanged.emit(self._value)
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._set_from_x(event.position().x())
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._dragging:
            self._set_from_x(event.position().x())
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._dragging:
            self._dragging = False
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        """Reset to default on double-click."""
        if event.button() == Qt.MouseButton.LeftButton:
            self._value = self._default
            self.update()
            self.valueChanged.emit(self._value)
            event.accept()
        else:
            super().mouseDoubleClickEvent(event)

    def _set_from_x(self, x: float) -> None:
        t = self._x_to_t(x)
        val = self._t_to_value(t)
        self._value = val
        self.update()
        self.valueChanged.emit(self._value)


def extract_thumbnail_b64(
    input_path: str,
    mode: str = "exr2video",
    *,
    which: int | None = None,
    ocio_cfg: object | None = None,
    src_space: str = "",
) -> str:
    """Extract a JPEG thumbnail as raw base64 for the slate (sRGB authoring).

    Slate / burn-in only apply to **EXR → video**. The thumbnail is one EXR
    frame from the known sequence (first / middle / last by frame index — no
    video seek). Scene-linear pixels are OCIO-transformed
    ``src → slate authoring (sRGB-like)`` when a config is available so the
    still matches slate colour management at convert time.

    *which* is ``0`` first / ``1`` middle / ``2`` last
    (:func:`preferences.thumbnail_frame_choice`). Defaults to middle.

    Returns a plain base64 string (no data-URI prefix), or ``''`` on failure.
    ``mode`` is accepted for call-site compatibility; non-EXR modes return ``''``.
    """
    import base64

    import numpy as np
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QImage

    from .preferences import THUMBNAIL_FRAME_MID

    if mode and mode != "exr2video":
        return ""

    if which is None:
        which = THUMBNAIL_FRAME_MID

    try:
        arr = _thumbnail_rgb_from_exr(
            input_path,
            which,
            ocio_cfg=ocio_cfg,
            src_space=src_space,
        )
        if arr is None or getattr(arr, "size", 0) == 0:
            return ""

        arr = np.ascontiguousarray(arr, dtype=np.uint8)
        if arr.ndim != 3 or arr.shape[2] < 3:
            return ""
        h, w = arr.shape[:2]
        if h < 1 or w < 1:
            return ""
        # .copy() so QImage owns the buffer (arr can be freed after this).
        qimg = QImage(arr.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()
        if qimg.isNull():
            return ""

        max_w = 640
        if w > max_w:
            qimg = qimg.scaledToWidth(max_w, Qt.TransformationMode.SmoothTransformation)

        qbuf = QBuffer()
        qbuf.open(QIODevice.OpenModeFlag.WriteOnly)
        if not qimg.save(qbuf, "JPEG", 85):
            log.warning("thumbnail JPEG encode failed for %s", input_path)
            return ""
        data = bytes(qbuf.data())
        if not data:
            return ""
        return base64.b64encode(data).decode("ascii")
    except (FileNotFoundError, NotADirectoryError, OSError) as e:
        log.debug("thumbnail extract: path error for %s: %s", input_path, e)
        return ""
    except Exception:
        log.warning("thumbnail extract failed for %s", input_path, exc_info=True)
        return ""


def _thumbnail_rgb_from_exr(
    input_path: str,
    which: int,
    *,
    ocio_cfg: object | None = None,
    src_space: str = "",
) -> object | None:
    """Return uint8 RGB for one EXR frame in slate authoring (sRGB-like) space.

    Frame list is known from the sequence (no seeking): pick first/mid/last index.
    """
    import numpy as np
    import OpenImageIO as oiio

    from ..core.sequence import find_exr_sequence_info
    from .preferences import pick_thumbnail_index

    try:
        _paths, _name, frames, _pad, seq = find_exr_sequence_info(input_path)
    except (RuntimeError, OSError, ValueError) as e:
        log.debug("thumbnail EXR resolve failed for %s: %s", input_path, e)
        return None
    if not frames:
        return None
    ordered = sorted(frames)
    idx = pick_thumbnail_index(len(ordered), which)
    frame_no = ordered[idx]
    frame_path = seq.frame(frame_no)

    img_buf = oiio.ImageBuf(frame_path)
    if img_buf.has_error:
        return None
    spec = img_buf.spec()
    if spec.full_width > 0 and spec.full_height > 0:
        dx, dy = spec.full_x, spec.full_y
        dw, dh = spec.full_width, spec.full_height
    else:
        dx, dy = 0, 0
        dw, dh = spec.width, spec.height
    roi = oiio.ROI(dx, dx + dw, dy, dy + dh, 0, 1, 0, min(spec.nchannels, 3))
    pixels = np.ascontiguousarray(img_buf.get_pixels(oiio.FLOAT, roi), dtype=np.float32)
    if pixels is None or pixels.size == 0:
        return None
    rgb = (
        pixels[..., :3]
        if pixels.ndim == 3 and pixels.shape[2] >= 3
        else np.repeat(pixels.reshape(pixels.shape[0], pixels.shape[1], 1), 3, axis=2)
    )
    rgb = np.ascontiguousarray(rgb, dtype=np.float32)

    # Prefer OCIO src → slate authoring (display-encoded sRGB), matching how
    # the slate itself is colour-managed at convert time.
    display_rgb = _exr_rgb_to_slate_authoring(rgb, ocio_cfg, src_space, frame_path)
    return np.clip(display_rgb * 255.0, 0, 255).astype(np.uint8)


def _exr_rgb_to_slate_authoring(
    rgb: np.ndarray,
    ocio_cfg: object | None,
    src_space: str,
    frame_path: str,
) -> np.ndarray:
    """Convert scene-linear EXR RGB to display-encoded slate authoring space."""
    import numpy as np

    arr = np.ascontiguousarray(rgb, dtype=np.float32)

    if ocio_cfg is not None:
        try:
            import PyOpenColorIO as OCIO

            from ..core.ocio_utils import (
                find_equivalent_space,
                get_overlay_authoring_space,
                get_working_space,
                make_cpu_processor,
            )

            # Resolve source: explicit UI space, EXR metadata, then working.
            src = (src_space or "").strip()
            if not src:
                try:
                    meta = oiio_colorspace_attr(frame_path)
                    if meta:
                        src = find_equivalent_space(ocio_cfg, meta) or meta
                except Exception:
                    pass
            if not src:
                try:
                    src = get_working_space(ocio_cfg)
                except Exception:
                    src = ""
            if src:
                # Map aliases so "ACEScg" etc. resolve on ACES Studio configs.
                resolved = find_equivalent_space(ocio_cfg, src) or src
                auth = get_overlay_authoring_space(ocio_cfg)
                if auth and resolved:
                    cpu = make_cpu_processor(ocio_cfg, resolved, auth)
                    h, w = arr.shape[:2]
                    buf = np.ascontiguousarray(arr.copy(), dtype=np.float32)
                    cpu.apply(OCIO.PackedImageDesc(buf, w, h, 3))
                    return np.clip(buf, 0.0, 1.0)
        except Exception:
            log.warning(
                "OCIO thumbnail transform failed; using transfer fallback",
                exc_info=True,
            )

    # Fallback: assume linear light, apply Rec.709/sRGB OETF (display-ish).
    lin = np.clip(arr, 0.0, None)
    srgb = np.where(
        lin <= 0.0031308,
        lin * 12.92,
        1.055 * np.power(lin, 1.0 / 2.4) - 0.055,
    )
    return np.clip(srgb, 0.0, 1.0)


def oiio_colorspace_attr(path: str) -> str:
    """Return ``oiio:ColorSpace`` from an EXR if present."""
    try:
        import OpenImageIO as oiio

        inp = oiio.ImageInput.open(path)
        if inp is None:
            return ""
        try:
            cs = inp.spec().getattribute("oiio:ColorSpace")
            return str(cs) if cs else ""
        finally:
            inp.close()
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Slate form panel
# ---------------------------------------------------------------------------


class SlateFormPanel(QWidget):
    """View / editor for the slate metadata, burn-in fields and watermark.

    The form is a thin view over a :class:`~src.services.slate_model.SlateModel` —
    every edit pushes the new value into the model, and the model is the
    canonical source of truth (also persists to ``QSettings``).
    """

    data_changed = Signal(dict)

    def __init__(
        self,
        model,  # SlateModel
        input_path: str = "",
        parent: QWidget | None = None,
        embed_overlays: bool = True,
    ):
        super().__init__(parent)
        self._model = model
        self._input_path = input_path
        self._suppress_emit = False
        self._pushing_to_model = False
        # When False the burn-in + watermark groups are built but not added to
        # this panel's layout; the host (SlateDialog) places them in a separate
        # column flanking the image view. They remain children of this widget
        # via reparenting, so all signal wiring / accessors stay valid.
        self._embed_overlays = embed_overlays
        self.setMinimumWidth(280)
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)

        fields = model.slate_fields

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # --- Slate (checkable header toggles whether a slate frame renders) ---
        slate_group = QGroupBox("Slate")
        slate_group.setCheckable(True)
        slate_group.setChecked(model.slate_enabled)
        slate_group.setStatusTip("Render a slate frame in front of the shot")
        slate_layout = QVBoxLayout(slate_group)
        slate_layout.setSpacing(10)
        self._slate_group = slate_group

        # --- Top row: Show / Seq / Shot / Version (horizontal) ---
        top_group = QGroupBox("Shot Identity")
        top_layout = QHBoxLayout(top_group)
        top_layout.setSpacing(8)

        def _labeled_field(label_text: str, widget: QLineEdit) -> QVBoxLayout:
            col = QVBoxLayout()
            col.setSpacing(2)
            lbl = QLabel(label_text)
            lbl.setStyleSheet("font-size: 10px; color: #888;")
            col.addWidget(lbl)
            col.addWidget(widget)
            return col

        self.show_edit = self._line_validated(fields.get("show", ""), "$SHOW")
        self.sequence_edit = self._line_validated(fields.get("sequence", ""), "$SEQ")
        self.shot_edit = self._line_validated(fields.get("shot", ""), "$SHOT")

        self.version_spin = QSpinBox()
        self.version_spin.setRange(0, 9999)
        self.version_spin.setPrefix("v")
        self.version_spin.setWrapping(True)
        self.version_spin.setValue(model.slate_version)
        self.version_spin.valueChanged.connect(self._emit_changed)

        top_layout.addLayout(_labeled_field("Show", self.show_edit), 2)
        top_layout.addLayout(_labeled_field("Seq", self.sequence_edit), 2)
        top_layout.addLayout(_labeled_field("Shot", self.shot_edit), 2)

        ver_col = QVBoxLayout()
        ver_col.setSpacing(2)
        ver_lbl = QLabel("Version")
        ver_lbl.setStyleSheet("font-size: 10px; color: #888;")
        ver_col.addWidget(ver_lbl)
        ver_col.addWidget(self.version_spin)
        top_layout.addLayout(ver_col, 1)
        slate_layout.addWidget(top_group)

        # --- Primary fields (always visible) ---
        primary = QGroupBox("Slate Info")
        pf = QFormLayout(primary)
        pf.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.notes_edit = QPlainTextEdit()
        self.notes_edit.setMaximumHeight(80)
        self.notes_edit.setPlaceholderText("Submission notes…")
        if fields.get("notes"):
            self.notes_edit.setPlainText(fields["notes"])

        self.submit_for_combo = QComboBox()
        for label in ("WIP", "FINAL", "CBB"):
            self.submit_for_combo.addItem(label)
        sf_idx = self.submit_for_combo.findText(fields.get("submit_for", "WIP"))
        if sf_idx >= 0:
            self.submit_for_combo.setCurrentIndex(sf_idx)

        self.artist_edit = self._line(fields.get("artist", ""), "Artist Name")

        pf.addRow("Submitting For", self.submit_for_combo)
        pf.addRow("Submit Notes", self.notes_edit)
        self.shot_types_edit = self._line(fields.get("shot_types", ""), "2d comp, 3d, matte paint…")
        self.scope_edit = self._line(fields.get("scope", ""), "VFX scope of work")
        pf.addRow("Shot Types", self.shot_types_edit)
        pf.addRow("Scope of Work", self.scope_edit)
        slate_layout.addWidget(primary)

        # --- Right-column fields (Vendor, Artist, Take, Logo) ---
        right_group = QGroupBox("Artist / Studio")
        rf = QFormLayout(right_group)
        rf.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.vendor_edit = self._line(fields.get("vendor", ""), "Studio / Vendor name")
        self.take_edit = self._line(fields.get("take", ""), "01")
        self.logo_edit = self._line(fields.get("logo", ""), "Logo text (blank to hide)")

        rf.addRow("Vendor", self.vendor_edit)
        rf.addRow("Artist", self.artist_edit)
        rf.addRow("Take", self.take_edit)
        rf.addRow("Logo / Studio", self.logo_edit)
        slate_layout.addWidget(right_group)
        root.addWidget(slate_group)

        # --- Burn-in (six corner cells, manual entry) ---
        burnin_group = QGroupBox("Burn-in (per-frame overlay)")
        burnin_group.setCheckable(True)
        burnin_group.setChecked(model.burnin_enabled)
        burnin_group.setStatusTip("Bake the corner burn-in text onto every frame")
        bf = QFormLayout(burnin_group)
        bf.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        burnin = model.burnin_fields
        self.burnin_top_left = self._line(burnin.get("top_left", ""), "Top left", tokenized=True)
        self.burnin_top_center = self._line(
            burnin.get("top_center", ""), "Top center", tokenized=True
        )
        self.burnin_top_right = self._line(burnin.get("top_right", ""), "Top right", tokenized=True)
        self.burnin_bottom_left = self._line(
            burnin.get("bottom_left", ""), "Bottom left", tokenized=True
        )
        self.burnin_bottom_center = self._line(
            burnin.get("bottom_center", ""), "Bottom center", tokenized=True
        )
        self.burnin_bottom_right = self._line(
            burnin.get("bottom_right", ""), "Bottom right", tokenized=True
        )

        bf.addRow("Top Left", self.burnin_top_left)
        bf.addRow("Top Center", self.burnin_top_center)
        bf.addRow("Top Right", self.burnin_top_right)
        bf.addRow("Bottom Left", self.burnin_bottom_left)
        bf.addRow("Bottom Center", self.burnin_bottom_center)
        bf.addRow("Bottom Right", self.burnin_bottom_right)

        # 'Fill from slate' button — convenience for users who don't want to
        # type six fields by hand; pulls vendor/show/version/etc. via the
        # legacy :func:`burnin_fields_from_slate` helper.
        self._fill_burnin_btn = QPushButton("Fill from slate fields")
        self._fill_burnin_btn.setToolTip(
            "Replace burn-in cells with values derived from slate metadata"
        )
        self._fill_burnin_btn.clicked.connect(self._on_fill_burnin)
        bf.addRow("", self._fill_burnin_btn)

        self._burnin_group = burnin_group
        if self._embed_overlays:
            root.addWidget(burnin_group)

        # --- Watermark (drawn over every preview & output frame) ---
        wm_group = QGroupBox("Watermark")
        wm_group.setCheckable(True)
        wm_params = model.watermark_params
        wm_group.setChecked(model.watermark_enabled)
        wm_group.setStatusTip("Draw a diagonal watermark across every frame")
        wmf = QFormLayout(wm_group)
        wmf.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.watermark_text_edit = TokenLineEdit()
        self.watermark_text_edit.setPlaceholderText("FOR REVIEW ONLY")
        self.watermark_text_edit.setText(str(wm_params.get("text", "")))

        self.watermark_opacity_spin = QSpinBox()
        self.watermark_opacity_spin.setRange(0, 100)
        self.watermark_opacity_spin.setSuffix(" %")
        self.watermark_opacity_spin.setValue(int(wm_params.get("opacity", 40)))

        self.watermark_size_spin = QSpinBox()
        self.watermark_size_spin.setRange(1, 30)
        self.watermark_size_spin.setSuffix(" %")
        self.watermark_size_spin.setValue(int(wm_params.get("size_pct", 9)))
        self.watermark_size_spin.setToolTip("Text size as a percentage of frame height")

        self.watermark_angle_spin = QSpinBox()
        self.watermark_angle_spin.setRange(-90, 90)
        self.watermark_angle_spin.setSuffix("\u00b0")
        self.watermark_angle_spin.setValue(int(wm_params.get("angle", 30)))

        self.watermark_tiled_cb = QCheckBox("Tile across frame")
        self.watermark_tiled_cb.setChecked(bool(wm_params.get("tiled", True)))

        wmf.addRow("Text", self.watermark_text_edit)
        wmf.addRow("Opacity", self.watermark_opacity_spin)
        wmf.addRow("Size", self.watermark_size_spin)
        wmf.addRow("Angle", self.watermark_angle_spin)
        wmf.addRow("", self.watermark_tiled_cb)
        if self._embed_overlays:
            root.addWidget(wm_group)
        self._watermark_group = wm_group

        self.watermark_text_edit.setStatusTip("Watermark text drawn diagonally across every frame")
        self.watermark_opacity_spin.setStatusTip("Watermark opacity (0 = invisible)")
        self.watermark_size_spin.setStatusTip("Watermark text height as % of frame height")
        self.watermark_angle_spin.setStatusTip("Rotation angle of the watermark text")
        self.watermark_tiled_cb.setStatusTip("Repeat the watermark to cover the entire frame")

        root.addStretch()

        for widget in (
            self.show_edit,
            self.shot_edit,
            self.artist_edit,
            self.sequence_edit,
            self.take_edit,
            self.vendor_edit,
            self.shot_types_edit,
            self.scope_edit,
            self.logo_edit,
        ):
            widget.textChanged.connect(self._emit_changed)

        self.submit_for_combo.currentIndexChanged.connect(self._emit_changed)
        self.notes_edit.textChanged.connect(self._emit_changed)

        # Burn-in fields fan into a single push-to-model handler
        for w in (
            self.burnin_top_left,
            self.burnin_top_center,
            self.burnin_top_right,
            self.burnin_bottom_left,
            self.burnin_bottom_center,
            self.burnin_bottom_right,
        ):
            w.textChanged.connect(self._on_burnin_changed)

        self._slate_group.toggled.connect(self._on_slate_enabled_toggled)
        self._burnin_group.toggled.connect(self._on_burnin_enabled_toggled)

        self._watermark_group.toggled.connect(self._on_watermark_enabled_toggled)
        self.watermark_text_edit.textChanged.connect(self._on_watermark_changed)
        self.watermark_opacity_spin.valueChanged.connect(self._on_watermark_changed)
        self.watermark_size_spin.valueChanged.connect(self._on_watermark_changed)
        self.watermark_angle_spin.valueChanged.connect(self._on_watermark_changed)
        self.watermark_tiled_cb.toggled.connect(self._on_watermark_changed)
        # Nested checkboxes (e.g. Tile) often still look enabled when a
        # checkable QGroupBox is off — force children to match the master.
        self._set_watermark_fields_enabled(self._watermark_group.isChecked())

        # Listen for external model changes so multiple views stay in sync.
        self._model.changed.connect(self._on_model_changed)

        # Status tips — shown in the dialog's QStatusBar on hover
        self.show_edit.setStatusTip("Production or show code (falls back to $SHOW env var)")
        self.sequence_edit.setStatusTip("Sequence name (falls back to $SEQ env var)")
        self.shot_edit.setStatusTip("Shot name (falls back to $SHOT env var)")
        self.version_spin.setStatusTip("Version number — appears as v001, v002, etc.")
        self.submit_for_combo.setStatusTip("Submission stage: WIP, FINAL, or CBB")
        self.notes_edit.setStatusTip("Free-form notes displayed on the slate")
        self.artist_edit.setStatusTip("Artist name — who did the work")
        self.vendor_edit.setStatusTip("Studio or vendor name")
        self.take_edit.setStatusTip("Take number for this version")
        self.shot_types_edit.setStatusTip("e.g. 2D comp, 3D, matte paint, roto…")
        self.scope_edit.setStatusTip("Description of VFX scope of work for this shot")
        self.logo_edit.setStatusTip("Text displayed as logo/studio branding (blank to hide)")

    def overlay_groups(self) -> list[QGroupBox]:
        """Return the burn-in + watermark group boxes.

        Used by :class:`SlateDialog` when ``embed_overlays=False`` to host the
        overlay controls in a dedicated column to the right of the image view.
        """
        return [self._burnin_group, self._watermark_group]

    # --- Helpers ---

    def _line(self, initial: str, placeholder: str, *, tokenized: bool = False) -> QLineEdit:
        edit = TokenLineEdit() if tokenized else QLineEdit()
        edit.setPlaceholderText(placeholder)
        if initial:
            edit.setText(initial)
        return edit

    def _line_validated(self, initial: str, placeholder: str) -> QLineEdit:
        """A QLineEdit restricted to alphanumeric/underscore (for show/seq/shot)."""
        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        edit.setValidator(QRegularExpressionValidator(QRegularExpression(r"[A-Za-z0-9_]*")))
        if initial:
            edit.setText(initial)
        return edit

    def set_thumbnail_b64(self, b64: str) -> None:
        """Forward the thumbnail to the model (single source of truth)."""
        self._model.set_thumbnail_b64(b64)

    def _push_slate_to_model(self) -> None:
        """Bulk-update the model with the current widget state.

        Suppresses the model→widget echo so ``setPlainText`` / ``setText`` do
        not run on every keystroke (``QPlainTextEdit.setPlainText`` resets the
        cursor to the start — that made Submit Notes insert at the beginning).
        """
        fields = {
            "show": self.show_edit.text(),
            "sequence": self.sequence_edit.text(),
            "shot": self.shot_edit.text(),
            "artist": self.artist_edit.text(),
            "vendor": self.vendor_edit.text(),
            "take": self.take_edit.text(),
            "submit_for": self.submit_for_combo.currentText(),
            "shot_types": self.shot_types_edit.text(),
            "scope": self.scope_edit.text(),
            "logo": self.logo_edit.text(),
            "notes": self.notes_edit.toPlainText(),
        }
        self._pushing_to_model = True
        try:
            self._model.set_slate_fields(fields, version=self.version_spin.value())
        finally:
            self._pushing_to_model = False

    def watermark_params(self) -> dict:
        """Return the current watermark settings as a plain dict."""
        return {
            "enabled": self._watermark_group.isChecked(),
            "text": self.watermark_text_edit.text(),
            "opacity": int(self.watermark_opacity_spin.value()),
            "size_pct": float(self.watermark_size_spin.value()),
            "angle": float(self.watermark_angle_spin.value()),
            "tiled": self.watermark_tiled_cb.isChecked(),
        }

    def burnin_fields(self) -> dict[str, str]:
        return {
            "top_left": self.burnin_top_left.text(),
            "top_center": self.burnin_top_center.text(),
            "top_right": self.burnin_top_right.text(),
            "bottom_left": self.burnin_bottom_left.text(),
            "bottom_center": self.burnin_bottom_center.text(),
            "bottom_right": self.burnin_bottom_right.text(),
        }

    def _emit_changed(self, *_args) -> None:
        if self._suppress_emit:
            return
        self._push_slate_to_model()
        self.data_changed.emit(self.slate_data())

    def _on_burnin_changed(self, *_args) -> None:
        if self._suppress_emit:
            return
        self._pushing_to_model = True
        try:
            self._model.set_burnin_fields(self.burnin_fields())
        finally:
            self._pushing_to_model = False
        self.data_changed.emit(self.slate_data())

    def _on_slate_enabled_toggled(self, checked: bool) -> None:
        if self._suppress_emit:
            return
        self._pushing_to_model = True
        try:
            self._model.set_slate_enabled(bool(checked))
        finally:
            self._pushing_to_model = False
        self.data_changed.emit(self.slate_data())

    def _on_burnin_enabled_toggled(self, checked: bool) -> None:
        if self._suppress_emit:
            return
        self._pushing_to_model = True
        try:
            self._model.set_burnin_enabled(bool(checked))
        finally:
            self._pushing_to_model = False
        self.data_changed.emit(self.slate_data())

    def _watermark_field_widgets(self) -> list[QWidget]:
        return [
            self.watermark_text_edit,
            self.watermark_opacity_spin,
            self.watermark_size_spin,
            self.watermark_angle_spin,
            self.watermark_tiled_cb,
        ]

    def _set_watermark_fields_enabled(self, enabled: bool) -> None:
        """Enable/disable watermark controls (incl. Tile) with the group master."""
        for w in self._watermark_field_widgets():
            w.setEnabled(bool(enabled))

    def _on_watermark_enabled_toggled(self, checked: bool) -> None:
        self._set_watermark_fields_enabled(bool(checked))
        if self._suppress_emit:
            return
        self._pushing_to_model = True
        try:
            self._model.set_watermark_enabled(bool(checked))
        finally:
            self._pushing_to_model = False
        self.data_changed.emit(self.slate_data())

    def _on_watermark_changed(self, *_args) -> None:
        if self._suppress_emit:
            return
        self._pushing_to_model = True
        try:
            self._model.set_watermark_params(self.watermark_params())
        finally:
            self._pushing_to_model = False
        self.data_changed.emit(self.slate_data())

    def _on_fill_burnin(self) -> None:
        """Populate burn-in fields from current slate metadata via the helper."""
        # Push current slate state to the model first so the helper sees it.
        self._push_slate_to_model()
        self._model.reset_burnin_from_slate(self._input_path)
        # Refresh widgets from the freshly-populated model fields.
        self._sync_burnin_widgets()

    def _on_model_changed(self, section: str) -> None:
        """Re-pull state from the model when an *external* writer modifies it.

        Skips when this form just pushed (``_pushing_to_model``) so we do not
        clobber the caret — especially in Submit Notes (``QPlainTextEdit``).
        """
        if getattr(self, "_pushing_to_model", False):
            return
        if section == "slate_data":
            self._sync_slate_widgets()
        elif section == "burnin_fields":
            self._sync_burnin_widgets()
        elif section == "watermark_params":
            self._sync_watermark_widgets()
        elif section == "slate_enabled":
            self._suppress_emit = True
            try:
                self._slate_group.setChecked(self._model.slate_enabled)
            finally:
                self._suppress_emit = False
        elif section == "burnin_enabled":
            self._suppress_emit = True
            try:
                self._burnin_group.setChecked(self._model.burnin_enabled)
            finally:
                self._suppress_emit = False
        elif section == "watermark_enabled":
            self._suppress_emit = True
            try:
                on = self._model.watermark_enabled
                self._watermark_group.setChecked(on)
                self._set_watermark_fields_enabled(on)
            finally:
                self._suppress_emit = False

    def _sync_slate_widgets(self) -> None:
        self._suppress_emit = True
        try:
            f = self._model.slate_fields
            for edit, key in (
                (self.show_edit, "show"),
                (self.sequence_edit, "sequence"),
                (self.shot_edit, "shot"),
                (self.artist_edit, "artist"),
                (self.vendor_edit, "vendor"),
                (self.take_edit, "take"),
                (self.shot_types_edit, "shot_types"),
                (self.scope_edit, "scope"),
                (self.logo_edit, "logo"),
            ):
                new = f.get(key, "")
                if edit.text() != new:
                    edit.setText(new)
            notes = f.get("notes", "")
            if self.notes_edit.toPlainText() != notes:
                # Preserve caret if content is unchanged-aside (defensive).
                cur = self.notes_edit.textCursor()
                pos = cur.position()
                self.notes_edit.setPlainText(notes)
                cur.setPosition(min(pos, len(notes)))
                self.notes_edit.setTextCursor(cur)
            sf_idx = self.submit_for_combo.findText(f.get("submit_for", "WIP"))
            if sf_idx >= 0 and self.submit_for_combo.currentIndex() != sf_idx:
                self.submit_for_combo.setCurrentIndex(sf_idx)
            ver = self._model.slate_version
            if self.version_spin.value() != ver:
                self.version_spin.setValue(ver)
        finally:
            self._suppress_emit = False

    def _sync_burnin_widgets(self) -> None:
        self._suppress_emit = True
        try:
            b = self._model.burnin_fields
            self.burnin_top_left.setText(b.get("top_left", ""))
            self.burnin_top_center.setText(b.get("top_center", ""))
            self.burnin_top_right.setText(b.get("top_right", ""))
            self.burnin_bottom_left.setText(b.get("bottom_left", ""))
            self.burnin_bottom_center.setText(b.get("bottom_center", ""))
            self.burnin_bottom_right.setText(b.get("bottom_right", ""))
        finally:
            self._suppress_emit = False
        self.data_changed.emit(self.slate_data())

    def _sync_watermark_widgets(self) -> None:
        self._suppress_emit = True
        try:
            p = self._model.watermark_params
            on = self._model.watermark_enabled
            self._watermark_group.setChecked(on)
            self.watermark_text_edit.setText(str(p.get("text", "")))
            self.watermark_opacity_spin.setValue(int(p.get("opacity", 40)))
            self.watermark_size_spin.setValue(int(p.get("size_pct", 9)))
            self.watermark_angle_spin.setValue(int(p.get("angle", 30)))
            self.watermark_tiled_cb.setChecked(bool(p.get("tiled", True)))
            # After syncing values, re-apply enable state (checkable group +
            # nested Tile checkbox need an explicit disable to look inactive).
            self._set_watermark_fields_enabled(on)
        finally:
            self._suppress_emit = False
        self.data_changed.emit(self.slate_data())

    def slate_data(self) -> dict:
        """Return a dict suitable for passing to the JS ``updateSlate()`` function.

        Sources the data from the model so this stays in sync with whatever
        the model sees as canonical.
        """
        data = self._model.slate_data_for_render()
        data["bitDepth"] = "16-bit half"
        return data

    def thumbnail_b64(self) -> str:
        """Return the raw base64 thumbnail string from the model."""
        return self._model.thumbnail_b64


# ---------------------------------------------------------------------------
# Slate preview (single pan/zoom graphics view for all tabs)
# ---------------------------------------------------------------------------


class SlatePreviewView(QGraphicsView):
    """Single QGraphicsView with Nuke-style pan/zoom for the slate editor.

    Both the slate HTML proxy and the shot-preview (thumbnail + burn-in)
    proxy live in the **same** scene.  A tab bar toggles which group is
    visible — one view, one transform, one background.

    Controls:
      - MMB drag: pan
      - Scroll wheel: zoom to cursor
      - RMB drag: zoom (horizontal, anchored at press position)
      - F key: fit in view
    """

    WHEEL_SCALE_FACTOR = 1.0 / 4.0

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)

        # No SmoothPixmapTransform — nearest/raw pixels when zooming (matches GPU path).
        self.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setBackgroundBrush(QBrush(QColor("#323232")))

        self._scene.setSceneRect(-1e6, -1e6, 2e6, 2e6)

        self._slate_rect = QRectF(0, 0, 1920, 1080)

        self._panning = False
        self._zooming = False
        self._last_pos: QPointF | None = None
        self._zoom_anchor_view: QPointF | None = None

    def set_slate_size(self, w: int, h: int) -> None:
        preview_h = 1080
        preview_w = int(preview_h * w / max(h, 1))
        self._slate_rect = QRectF(0, 0, preview_w, preview_h)

    def fit_in_view(self) -> None:
        self.fitInView(self._slate_rect, Qt.AspectRatioMode.KeepAspectRatio)

    def _scale_at(self, factor: float, view_anchor: QPointF) -> None:
        cur = self.transform().m11()
        target = max(ZOOM_MIN, min(ZOOM_MAX, cur * factor))
        s = target / cur
        if abs(s - 1.0) < 1e-7:
            return
        scene_pt = self.mapToScene(view_anchor.toPoint())
        cx, cy = scene_pt.x(), scene_pt.y()
        t = self.transform()
        t.translate(cx, cy)
        t.scale(s, s)
        t.translate(-cx, -cy)
        self.setTransform(t)

    def _translate_view(self, dx: float, dy: float) -> None:
        t = self.transform()
        s = t.m11()
        t.translate(dx / s, dy / s)
        self.setTransform(t)

    # -- events --

    def mousePressEvent(self, event):
        btn = event.button()
        if btn in (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton):
            self._panning = True
            self._last_pos = event.position()
            self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        if btn == Qt.MouseButton.RightButton:
            self._zooming = True
            self._last_pos = event.position()
            self._zoom_anchor_view = event.position()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        pos = event.position()
        if self._panning and self._last_pos is not None:
            delta = pos - self._last_pos
            self._last_pos = pos
            self._translate_view(delta.x(), delta.y())
            event.accept()
            return
        if self._zooming and self._last_pos is not None:
            dx = pos.x() - self._last_pos.x()
            self._last_pos = pos
            s = 1.02 ** (dx * 0.5)
            anchor = self._zoom_anchor_view
            if anchor is not None:
                self._scale_at(s, anchor)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        btn = event.button()
        if btn in (Qt.MouseButton.LeftButton, Qt.MouseButton.MiddleButton) and self._panning:
            self._panning = False
            self._last_pos = None
            self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
            event.accept()
            return
        if btn == Qt.MouseButton.RightButton and self._zooming:
            self._zooming = False
            self._last_pos = None
            self._zoom_anchor_view = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event: QWheelEvent):
        delta = event.angleDelta().y()
        if delta == 0:
            return
        s = 1.02 ** (delta * self.WHEEL_SCALE_FACTOR)
        self._scale_at(s, event.position())
        event.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_F and not event.modifiers():
            self.fit_in_view()
            event.accept()
            return
        super().keyPressEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)


# ---------------------------------------------------------------------------
# Transport / shuttle bar
# ---------------------------------------------------------------------------


_SHUTTLE_BTN_STYLE = (
    "QPushButton { background: #2a2a2a; color: #e0e0e0;"
    " border: 1px solid #3c3c3c; border-radius: 3px;"
    " font-size: 12px; padding: 0; }"
    "QPushButton:hover { background: #3c3c3c; }"
    "QPushButton:pressed { background: #c87828; }"
    "QPushButton:checked { background: #c87828; color: #fff; }"
)


class _ShuttleBar(QWidget):
    """Tiny transport strip: first / step-back / play-pause / step-forward / last.

    Drives a :class:`TimelineSlider` directly — give it the timeline and it
    handles all wiring including a :class:`QTimer` for playback at *fps*.
    """

    def __init__(
        self,
        timeline: TimelineSlider,
        fps: float = 24.0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._timeline = timeline
        self._fps = max(1.0, fps)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(2)

        self._btn_first = self._make_btn("\u23ee", "Go to first frame")
        self._btn_back = self._make_btn("\u23ea", "Step back one frame")
        self._btn_play = self._make_btn("\u25b6", "Play / pause")
        self._btn_play.setCheckable(True)
        self._btn_fwd = self._make_btn("\u23e9", "Step forward one frame")
        self._btn_last = self._make_btn("\u23ed", "Go to last frame")

        for btn in (
            self._btn_first,
            self._btn_back,
            self._btn_play,
            self._btn_fwd,
            self._btn_last,
        ):
            layout.addWidget(btn)

        self._btn_first.clicked.connect(self._on_first)
        self._btn_back.clicked.connect(self._on_back)
        self._btn_play.toggled.connect(self._on_play_toggled)
        self._btn_fwd.clicked.connect(self._on_fwd)
        self._btn_last.clicked.connect(self._on_last)

        self._timer = QTimer(self)
        self._advance_cb: Callable[[], None] | None = None
        self._timer.timeout.connect(self._on_timer_tick)
        self._refresh_timer_interval()

        # If the user grabs the playhead, stop playback so the timer doesn't
        # fight their drag.
        self._timeline.value_changed.connect(self._on_user_scrubbed)

    def set_advance_callback(self, callback: Callable[[], None] | None) -> None:
        """Replace the default per-tick advance with a custom callback.

        Used by the slate dialog for cache-first playback (stall until the next
        frame is in RAM).  Pass ``None`` to restore the default step-forward.
        """
        self._advance_cb = callback

    def is_timer_active(self) -> bool:
        return self._timer.isActive()

    def stop_timer(self) -> None:
        self._timer.stop()

    def start_timer(self) -> None:
        self._timer.start()

    @staticmethod
    def _make_btn(text: str, tooltip: str) -> QPushButton:
        b = QPushButton(text)
        b.setStyleSheet(_SHUTTLE_BTN_STYLE)
        b.setFixedSize(26, 22)
        b.setToolTip(tooltip)
        b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return b

    def set_fps(self, fps: float) -> None:
        self._fps = max(1.0, fps)
        self._refresh_timer_interval()

    def _refresh_timer_interval(self) -> None:
        self._timer.setInterval(max(10, int(round(1000.0 / self._fps))))

    def _on_first(self) -> None:
        self._timeline.set_value(self._timeline._first)
        self._timeline.value_changed.emit(self._timeline.value())

    def _on_last(self) -> None:
        self._timeline.set_value(self._timeline._last)
        self._timeline.value_changed.emit(self._timeline.value())

    def _on_back(self) -> None:
        self._timeline.set_value(self._timeline.value() - 1)
        self._timeline.value_changed.emit(self._timeline.value())

    def _on_fwd(self) -> None:
        self._timeline.set_value(self._timeline.value() + 1)
        self._timeline.value_changed.emit(self._timeline.value())

    def _on_play_toggled(self, checked: bool) -> None:
        self._btn_play.setText("\u23f8" if checked else "\u25b6")
        if checked:
            self._timer.start()
        else:
            self._timer.stop()

    def _on_user_scrubbed(self, _frame: int) -> None:
        # Only stop playback when the user drags the head, not when the
        # play timer itself fired set_value → value_changed.
        if self._btn_play.isChecked() and self._timeline._dragging_playhead:
            self._btn_play.setChecked(False)

    def _on_timer_tick(self) -> None:
        if self._advance_cb is not None:
            self._advance_cb()
        else:
            self._advance()

    def _advance(self) -> None:
        cur = self._timeline.value()
        nxt = cur + 1
        if nxt > self._timeline._last:
            nxt = self._timeline._first
        self._timeline.set_value(nxt)
        self._timeline.value_changed.emit(nxt)


# ---------------------------------------------------------------------------
# Slate dialog
# ---------------------------------------------------------------------------


class SlateDialog(QDialog):
    """Modal dialog for editing slate + burn-in overlay data with live preview.

    Left side: :class:`SlateFormPanel` in a scroll area.
    Right side: preview + Nuke-style :class:`TimelineSlider`.  Frame
    ``first - 1`` shows the slate; frames ``first .. last`` show the actual
    EXR shot frames with burn-in / watermark composited on top.

    Shot frames are decoded into a :class:`~src.services.frame_cache.FrameCache`
    (float16 working-space when OCIO is available) and prefetched ahead of the
    playhead.  Display is preferably **GPU OCIO** (:class:`OcioGpuImagePlane`):
    a cache hit is texture upload + GLSL display transform — not a full-res
    CPU ``applyRGB``.  CPU display is the fallback when OpenGL is unavailable.
    """

    def __init__(
        self,
        model,  # SlateModel
        locked_width: int = 0,
        locked_height: int = 0,
        input_path: str = "",
        mode: str = "",
        inferred_fps: float = 0.0,
        frame_range: str = "",
        dst_colorspace: str = "",
        ocio_cfg: object | None = None,
        src_colorspace: str = "",
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Slate & Overlay Editor")
        self.resize(1400, 850)

        self._model = model
        self._input_path = input_path
        self._mode = mode
        self._ocio_cfg = ocio_cfg
        self._src_colorspace = src_colorspace
        self._dst_colorspace = dst_colorspace

        # Cache of OCIO CPUProcessors keyed by transform direction/spaces.
        # Populated lazily by the various ``_get_*_proc`` helpers, several of
        # which run during ``__init__`` (e.g. via ``_build_worker_frame_transform``).
        self._ocio_proc_cache: dict[tuple, object] = {}

        # Output metadata (resolution, fps, frame range, colorspace) comes from
        # the conversion tab — seed the model once when the dialog opens.
        init_fields: dict[str, str] = {}
        if frame_range:
            init_fields["frame_range"] = frame_range
        init_fps = inferred_fps if inferred_fps > 0 else None
        init_res = (locked_width, locked_height) if locked_width > 0 and locked_height > 0 else None
        if init_fields or init_fps is not None or init_res is not None:
            self._model.set_slate_fields(
                init_fields,
                fps=init_fps,
                resolution=init_res,
            )

        # Preview pipeline (working-space comp + live viewer controls):
        #
        #   FrameCache (float16 working when prefetch transform is on)
        #     │  main thread: float16→float32 + alpha-over overlays
        #     ▼
        #   _composed_working_f32
        #     │  GPU: upload texture → OCIO GLSL display/view (+ dynamic EC)
        #     │  CPU fallback: applyRGB → QImage → QGraphicsView
        #     ▼
        #   preview
        #
        # Gain/gamma are OCIO dynamic ExposureContrast properties (uniforms on
        # GPU; dynamic CPU props on fallback). Display/view rebuilds the processor
        # only when the combo changes.
        self._comp_f32 = None
        self._comp_src_space = ""
        self._working_f32 = None
        self._working_space: str = ""
        self._display_f32 = None
        self._composed_working_f32 = None
        self._preview_pixmap_item = None
        self._gpu_plane: OcioGpuImagePlane | None = None
        self._use_gpu = False

        # Cache of linearised burn-in / watermark RGBA overlays, keyed by a
        # signature of (size + content). Rebuilt only when the overlay content
        # or frame size changes; read on every composite pass.
        self._overlay_lin_cache: dict[str, object] = {}

        # Dynamic working→display viewer processor + its live EC properties
        # (rebuilt on display/view/working-space change). Read on the first
        # composite pass, so must exist before any render helper runs.
        self._viewer_display_proc = None
        self._ec_exposure_prop = None
        self._ec_gamma_prop = None
        self._last_viewer_display: tuple[str, str, str] | None = None

        # Shot frame cache + parallel prefetch (EXR → uint16 RGB in RAM)
        self._exr_seq = None
        self._shot_frames: list[int] = []
        self._shot_frames_set: set[int] = set()
        self._first_shot: int | None = None
        self._last_shot: int | None = None
        self._slate_frame: int = 0
        self._current_frame: int = 0
        self._shot_cache = FrameCache(
            cache_budget_bytes(self._model.settings),
            self,
        )
        self._prefetch: ExrPrefetchService | None = None
        self._playback_wait_frame: int | None = None
        self._cache_paused = False
        # Optional cache-status widgets (only built when EXR shot frames exist).
        self._cache_pct_slider: QSlider | None = None
        self._cache_pct_label: QLabel | None = None
        self._cache_bar: QProgressBar | None = None
        self._cache_pause_btn: QToolButton | None = None
        self._cache_clear_btn: QToolButton | None = None

        # Resolve EXR frame range (only available for exr2video mode)
        if input_path and mode == "exr2video":
            try:
                from ..core.sequence import find_exr_sequence_info

                _paths, _name, frames, _pad, seq = find_exr_sequence_info(input_path)
                if frames:
                    self._shot_frames = sorted(frames)
                    self._shot_frames_set = set(self._shot_frames)
                    self._first_shot = self._shot_frames[0]
                    self._last_shot = self._shot_frames[-1]
                    self._exr_seq = seq
                    self._slate_frame = self._first_shot - 1
                    self._current_frame = self._slate_frame
            except Exception:
                log.exception("Could not resolve EXR sequence for slate preview: %s", input_path)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # --- Left: slate metadata form (burn-in / watermark hosted separately) ---
        self._form = SlateFormPanel(model, input_path=input_path, embed_overlays=False)

        if input_path:
            from .preferences import thumbnail_frame_choice

            which = thumbnail_frame_choice(self._model.settings)
            thumb = extract_thumbnail_b64(
                input_path,
                mode,
                which=which,
                ocio_cfg=self._ocio_cfg,
                src_space=self._src_colorspace or "",
            )
            if thumb:
                self._form.set_thumbnail_b64(thumb)
            else:
                log.warning(
                    "No slate thumbnail extracted from %s (mode=%s, which=%s)",
                    input_path,
                    mode,
                    which,
                )

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._form)
        scroll.setMinimumWidth(300)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        splitter.addWidget(scroll)

        # --- Center: viewer controls + preview + timeline ---
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(0)

        # Preview surface first so viewer-control wiring can push GPU view state.
        self._preview = SlatePreviewView()
        self._preview_stack = QStackedWidget()
        self._preview_stack.addWidget(self._preview)
        if gpu_ocio_available():
            try:
                self._gpu_plane = OcioGpuImagePlane()
                self._gpu_plane.gpu_failed.connect(self._on_gpu_failed)
                self._preview_stack.addWidget(self._gpu_plane)
                self._use_gpu = True
                self._preview_stack.setCurrentWidget(self._gpu_plane)
                log.info("Slate preview: GPU OCIO display enabled")
            except Exception:
                log.exception("GPU OCIO preview init failed; using CPU path")
                self._gpu_plane = None
                self._use_gpu = False

        self._build_viewer_controls(right_layout)
        right_layout.addWidget(self._preview_stack, 1)

        w, h = self.resolution()
        self._preview.set_slate_size(w, h)

        # Timeline scrubber + shuttle controls (only meaningful when there
        # are shot frames to scrub through).
        self._timeline: TimelineSlider | None = None
        self._shuttle: _ShuttleBar | None = None
        if self._exr_seq is not None and self._shot_frames:
            self._timeline = TimelineSlider()
            ideal_h = self._timeline._ideal_height()
            self._timeline.setFixedHeight(ideal_h)
            last = self._last_shot if self._last_shot is not None else self._slate_frame
            self._timeline.set_range(self._slate_frame, last)
            self._timeline.set_marker_frames({self._slate_frame: "SLATE"})
            self._timeline.set_value(self._slate_frame)
            self._timeline.value_changed.connect(self._on_timeline_changed)

            self._shuttle = _ShuttleBar(self._timeline, fps=self.fps())
            self._shuttle.setFixedHeight(ideal_h)
            # Cache-first playback: stall until the next frame is in RAM.
            self._shuttle.set_advance_callback(self._playback_tick)

            self._prefetch = ExrPrefetchService(
                self._exr_seq,
                self._shot_cache,
                self._shot_frames,
                max_workers=_PREFETCH_WORKERS,
                frame_transform=self._build_worker_frame_transform(),
                parent=self,
            )
            self._prefetch.frame_loaded.connect(self._on_prefetch_frame_loaded)
            self._shot_cache.cache_changed.connect(self._on_shot_cache_changed)
            self._shuttle._btn_play.toggled.connect(self._on_shuttle_play_toggled)

            transport_row = QHBoxLayout()
            transport_row.setContentsMargins(0, 0, 0, 0)
            transport_row.setSpacing(0)
            transport_row.addWidget(self._shuttle)
            transport_row.addWidget(self._timeline, 1)
            right_layout.addLayout(transport_row)

        splitter.addWidget(right_panel)

        # --- Right: burn-in + watermark overlay controls ---
        overlay_panel = QWidget()
        overlay_layout = QVBoxLayout(overlay_panel)
        overlay_layout.setContentsMargins(12, 12, 12, 12)
        overlay_layout.setSpacing(10)
        for group in self._form.overlay_groups():
            overlay_layout.addWidget(group)
        overlay_layout.addStretch()

        overlay_scroll = QScrollArea()
        overlay_scroll.setWidgetResizable(True)
        overlay_scroll.setWidget(overlay_panel)
        overlay_scroll.setMinimumWidth(280)
        overlay_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        splitter.addWidget(overlay_scroll)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setCollapsible(0, False)
        splitter.setCollapsible(1, False)
        splitter.setCollapsible(2, False)
        splitter.setSizes([360, 740, 320])

        layout.addWidget(splitter, 1)

        # --- Bottom: status bar ---
        # Left: hover/status tips (the message area). Right (permanent): a compact
        # cache cluster, then OK / Cancel. The size grip stays in the corner.
        self._status_bar = QStatusBar()
        # The native size grip renders blank under our QSS stylesheet, so we
        # disable it and add a self-painted grip in the corner instead.
        self._status_bar.setSizeGripEnabled(False)
        self._status_bar.setContentsMargins(8, 0, 0, 0)
        self._status_bar.setStyleSheet("QStatusBar::item { border: 0; }")
        if self._exr_seq is not None and self._shot_frames:
            self._build_cache_status_bar()
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self._status_bar.addPermanentWidget(buttons)
        self._status_bar.addPermanentWidget(SizeGrip(self._status_bar))
        layout.addWidget(self._status_bar)

        # --- Live preview wiring ---
        # Form changes (slate metadata + burn-in fields) → invalidate cached
        # composites and re-render whatever frame the user is currently on.
        self._refresh_timer = QTimer()
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(120)
        self._refresh_timer.timeout.connect(self._refresh_current_frame)

        self._form.data_changed.connect(lambda _: self._refresh_timer.start())
        # React to the slate being enabled/disabled so the timeline can add or
        # remove the slate frame from the scrubbable range live.
        self._model.changed.connect(self._on_model_section_changed)

        QTimer.singleShot(0, self._apply_slate_visibility)
        QTimer.singleShot(0, self._refresh_current_frame)
        QTimer.singleShot(0, self._sync_prefetch)
        QTimer.singleShot(0, self._preview.fit_in_view)

    # -- Viewer controls (Nuke-style) --

    def _build_viewer_controls(self, parent_layout: QVBoxLayout) -> None:
        """Build Nuke-style gain/gamma sliders + display colorspace combo.

        Sliders take the flexible width; the display/view combo is capped so long
        ACES labels cannot crush the tracks.
        """
        strip = QHBoxLayout()
        strip.setContentsMargins(4, 1, 4, 1)
        strip.setSpacing(6)

        # --- Gain value label + slider ---
        # Nuke: numeric readout goes red when ≠ default (1.0).
        self._gain_value_label = QLabel("1.0")
        self._gain_value_label.setFixedWidth(28)
        self._gain_value_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self._style_viewer_value_label(self._gain_value_label, 1.0)
        strip.addWidget(self._gain_value_label)

        # Log curve + procedural 1–2–5 decade ticks (range-driven).
        self._gain_slider = NukeSlider(
            default=1.0,
            val_min=0.01,
            val_max=64.0,
            map_mode="log",
        )
        self._gain_slider.setMinimumWidth(140)
        self._gain_slider.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        strip.addWidget(self._gain_slider, 1)

        strip.addSpacing(8)

        # --- Gamma label + slider ---
        gamma_lbl = QLabel("\u03b3")
        gamma_lbl.setFixedWidth(10)
        gamma_lbl.setStyleSheet("font-size: 10px; color: #888;")
        strip.addWidget(gamma_lbl)

        self._gamma_value_label = QLabel("1.0")
        self._gamma_value_label.setFixedWidth(22)
        self._gamma_value_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self._style_viewer_value_label(self._gamma_value_label, 1.0)
        strip.addWidget(self._gamma_value_label)

        # Pivot-at-1 curve: half track for [0,1], half for [1,max]; ticks auto.
        self._gamma_slider = NukeSlider(
            default=1.0,
            val_min=0.0,
            val_max=4.0,
            map_mode="pivot",
        )
        self._gamma_slider.setMinimumWidth(140)
        self._gamma_slider.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        strip.addWidget(self._gamma_slider, 1)

        strip.addSpacing(8)

        # --- Display/view combo: fixed budget so long labels don't steal track width ---
        self._display_view_combo = QComboBox()
        self._display_view_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self._display_view_combo.setMinimumContentsLength(12)
        self._display_view_combo.setMinimumWidth(100)
        self._display_view_combo.setMaximumWidth(168)
        self._display_view_combo.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        # Full name still visible in the popup; closed field may elide.
        self._display_view_combo.setToolTip("OCIO display / view")
        strip.addWidget(self._display_view_combo, 0)

        parent_layout.addLayout(strip)

        # Populate display/view from OCIO config
        self._display_view_pairs: list[tuple[str, str]] = []
        if self._ocio_cfg is not None:
            self._populate_display_view_combo()
        else:
            self._display_view_pairs.append(("sRGB", "Raw"))
            self._display_view_combo.addItem("sRGB")

        # Gain/gamma: dynamic EC on GPU (uniforms) or CPU processor; display/view
        # rebuilds the OCIO display leg only.
        self._gain_slider.valueChanged.connect(self._on_gain_changed)
        self._gamma_slider.valueChanged.connect(self._on_gamma_changed)
        self._display_view_combo.currentIndexChanged.connect(
            lambda _: self._invalidate_display_cache()
        )

        self._gain = 1.0
        self._gamma = 1.0
        self._sync_gpu_view_settings()

    def _populate_display_view_combo(self) -> None:
        from ..core.ocio_utils import list_displays, list_views

        self._display_view_combo.blockSignals(True)
        self._display_view_combo.clear()
        self._display_view_pairs.clear()

        default_display = ""
        default_view = ""
        default_idx = 0
        try:
            default_display = self._ocio_cfg.getDefaultDisplay()
            default_view = self._ocio_cfg.getDefaultView(default_display)
        except Exception:
            pass

        try:
            displays = list_displays(self._ocio_cfg)
            idx = 0
            for display in displays:
                views = list_views(self._ocio_cfg, display)
                for view in views:
                    self._display_view_pairs.append((display, view))
                    if len(displays) == 1:
                        label = view
                    else:
                        label = f"{display} / {view}"
                    self._display_view_combo.addItem(label)
                    if display == default_display and view == default_view:
                        default_idx = idx
                    idx += 1
        except Exception:
            pass

        if self._display_view_combo.count() > 0:
            self._display_view_combo.setCurrentIndex(default_idx)
        self._display_view_combo.blockSignals(False)

    def _exposure_stops(self) -> float:
        return math.log2(max(float(getattr(self, "_gain", 1.0)), 1e-10))

    def _on_gpu_failed(self, reason: str) -> None:
        """Permanent GPU failure — switch preview to the CPU QGraphicsView path."""
        log.error("Falling back to CPU OCIO preview: %s", reason)
        self._use_gpu = False
        if self._preview_stack is not None:
            self._preview_stack.setCurrentWidget(self._preview)
        # Re-show current frame on the CPU path.
        self._apply_display_transform()

    def _sync_gpu_view_settings(self) -> None:
        """Push display/view + EC state to the GPU plane (no-op if GPU off).

        ``set_ocio_view`` is cheap when the display/view is unchanged (no shader
        rebuild). Call this on display/view changes and once before first paint —
        not as a per-frame rebuild.
        """
        if self._gpu_plane is None or not self._use_gpu:
            return
        if not self._gpu_plane.is_alive():
            self._use_gpu = False
            return
        working = self._resolve_working_space() or self._comp_src_space
        display, view = "", ""
        idx = self._display_view_combo.currentIndex()
        if 0 <= idx < len(self._display_view_pairs):
            display, view = self._display_view_pairs[idx]
        self._gpu_plane.set_ocio_view(self._ocio_cfg, working, display, view)
        self._gpu_plane.set_exposure_stops(self._exposure_stops())
        self._gpu_plane.set_gamma(float(getattr(self, "_gamma", 1.0)))

    @staticmethod
    def _style_viewer_value_label(label: QLabel, value: float, *, default: float = 1.0) -> None:
        """Nuke-style: red readout when value is not exactly the default (1.0)."""
        is_default = abs(float(value) - default) < 1e-6
        color = "#d4d4d4" if is_default else "#e05030"
        label.setStyleSheet(f"font-size: 10px; color: {color};")

    def _on_gain_changed(self, gain: float) -> None:
        self._gain = gain
        if abs(gain - 1.0) < 1e-6:
            txt = "1.0"
        elif gain >= 10:
            txt = f"{gain:.0f}"
        elif gain >= 1:
            txt = f"{gain:.1f}"
        elif gain >= 0.1:
            txt = f"{gain:.2f}"
        else:
            txt = f"{gain:.3f}"
        self._gain_value_label.setText(txt)
        self._style_viewer_value_label(self._gain_value_label, gain)

        stops = self._exposure_stops()
        if self._use_gpu and self._gpu_plane is not None and self._gpu_plane.is_alive():
            self._gpu_plane.set_exposure_stops(stops)
            return
        if self._ec_exposure_prop is not None:
            self._ec_exposure_prop.setDouble(stops)
            self._reapply_display_with_ec()
            return
        self._refresh_gain_gamma()

    def _on_gamma_changed(self, gamma: float) -> None:
        self._gamma = gamma
        if abs(gamma - 1.0) < 1e-6:
            txt = "1.0"
        elif gamma >= 1:
            txt = f"{gamma:.1f}"
        else:
            txt = f"{gamma:.2f}"
        self._gamma_value_label.setText(txt)
        self._style_viewer_value_label(self._gamma_value_label, gamma)

        if self._use_gpu and self._gpu_plane is not None and self._gpu_plane.is_alive():
            # Post-display pow(x, 1/γ) uniform — no OCIO rebuild.
            self._gpu_plane.set_gamma(gamma)
            return
        # CPU: gamma is a cheap post-display power on the cached buffer.
        if self._display_f32 is not None:
            self._paint_display_with_viewer_gamma(self._display_f32)
            return
        self._refresh_gain_gamma()

    # -- Tab switching --

    def event(self, ev: QEvent) -> bool:
        if ev.type() == QEvent.Type.StatusTip:
            from PySide6.QtGui import QStatusTipEvent

            if isinstance(ev, QStatusTipEvent):
                self._status_bar.showMessage(ev.tip())
                return True
        return super().event(ev)

    # -- Frame routing --

    def _on_timeline_changed(self, frame: int) -> None:
        """Timeline playhead moved (scrub or shuttle step)."""
        self._goto_frame(frame)

    def _is_playing(self) -> bool:
        return self._shuttle is not None and self._shuttle._btn_play.isChecked()

    def _on_shuttle_play_toggled(self, playing: bool) -> None:
        if not playing:
            self._playback_wait_frame = None
            if self._shuttle is not None:
                self._shuttle.stop_timer()
        self._shot_cache.set_batch_mode(playing)
        self._sync_prefetch()
        if playing:
            self._playback_tick()

    def _next_playback_frame(self, frame: int) -> int:
        nxt = frame + 1
        if self._timeline is not None and nxt > self._timeline._last:
            nxt = self._timeline._first
        return nxt

    def _needs_exr_cache(self, frame: int) -> bool:
        return frame != self._slate_frame and frame in self._shot_frames_set

    def _on_model_section_changed(self, section: str) -> None:
        """Dialog-level reaction to model changes (the form handles its own)."""
        if section == "slate_enabled":
            self._apply_slate_visibility()

    def _apply_slate_visibility(self) -> None:
        """Add or remove the slate frame from the scrubbable timeline.

        When the slate is disabled it shouldn't be reachable in the preview, so
        we shrink the timeline range to the shot frames and drop the SLATE
        marker. If the playhead is parked on the slate we move it onto the first
        shot frame.
        """
        if self._timeline is None:
            return
        slate_on = self._model is None or self._model.slate_enabled
        first = self._first_shot
        last = self._last_shot
        if first is None or last is None:
            return
        if slate_on:
            self._timeline.set_range(self._slate_frame, last)
            self._timeline.set_marker_frames({self._slate_frame: "SLATE"})
        else:
            self._timeline.set_range(first, last)
            self._timeline.set_marker_frames({})
            if self._current_frame == self._slate_frame:
                self._goto_frame(first)

    def _goto_frame(self, frame: int) -> None:
        """Move playhead to *frame* and refresh the preview."""
        if self._timeline is not None:
            frame = max(self._timeline._first, min(frame, self._timeline._last))
        if frame == self._current_frame:
            self._refresh_current_frame()
            return
        self._current_frame = frame
        self._playback_wait_frame = None
        if self._timeline is not None:
            self._timeline.set_value(frame)
        self._sync_prefetch()
        self._refresh_current_frame()

    def _sync_prefetch(self) -> None:
        if self._prefetch is not None and not self._cache_paused:
            self._prefetch.set_context(
                self._current_frame,
                playing=self._is_playing(),
            )

    def _build_cache_status_bar(self) -> None:
        """Compact RAM-cache cluster pinned to the right of the status bar.

        Layout: [ | ] Cache [slider] 42%  [====usage====] ⏸ ✕
        Added without stretch so the message area keeps room for hover tips.
        """
        muted = "color: #8a8a8a;"
        cache_host = QWidget()
        row = QHBoxLayout(cache_host)
        row.setContentsMargins(0, 0, 6, 0)
        row.setSpacing(6)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setFrameShadow(QFrame.Shadow.Plain)
        sep.setStyleSheet("color: #3a3a3a;")
        row.addWidget(sep)

        cache_lbl = QLabel("Cache")
        cache_lbl.setStyleSheet(muted)
        row.addWidget(cache_lbl)

        self._cache_pct_slider = QSlider(Qt.Orientation.Horizontal)
        self._cache_pct_slider.setRange(1, 90)
        self._cache_pct_slider.setValue(load_cache_budget_pct(self._model.settings))
        self._cache_pct_slider.setFixedWidth(80)
        self._cache_pct_slider.setToolTip("Playback RAM cache as % of system memory")
        row.addWidget(self._cache_pct_slider)

        # Single label: percentage on the bar, full GB breakdown in its tooltip.
        self._cache_pct_label = QLabel()
        self._cache_pct_label.setMinimumWidth(30)
        self._cache_pct_label.setStyleSheet(muted)
        row.addWidget(self._cache_pct_label)

        self._cache_bar = QProgressBar()
        self._cache_bar.setMaximum(1000)
        self._cache_bar.setFixedWidth(150)
        self._cache_bar.setFixedHeight(16)
        self._cache_bar.setTextVisible(True)
        self._cache_bar.setToolTip("Playback cache memory in use")
        row.addWidget(self._cache_bar)

        self._cache_pause_btn = QToolButton()
        self._cache_pause_btn.setText("\u23f8")
        self._cache_pause_btn.setCheckable(True)
        self._cache_pause_btn.setAutoRaise(True)
        self._cache_pause_btn.setFixedSize(24, 22)
        self._cache_pause_btn.setToolTip("Pause background prefetch")
        row.addWidget(self._cache_pause_btn)

        self._cache_clear_btn = QToolButton()
        self._cache_clear_btn.setText("\u2715")
        self._cache_clear_btn.setAutoRaise(True)
        self._cache_clear_btn.setFixedSize(24, 22)
        self._cache_clear_btn.setToolTip("Clear playback cache")
        row.addWidget(self._cache_clear_btn)

        self._status_bar.addPermanentWidget(cache_host)

        self._cache_pct_slider.valueChanged.connect(self._on_cache_pct_changed)
        self._cache_pause_btn.toggled.connect(self._on_cache_pause_toggled)
        self._cache_clear_btn.clicked.connect(self._on_cache_clear)
        self._update_cache_labels(self._cache_pct_slider.value())
        self._update_cache_usage_bar()

    def _update_cache_labels(self, pct: int) -> None:
        budget_gb = total_ram_bytes() * pct / 100 / (1024**3)
        total_gb = total_ram_bytes() / (1024**3)
        self._cache_pct_label.setText(f"{pct}%")
        tip = f"Playback RAM cache: {budget_gb:.1f} of {total_gb:.1f} GB ({pct}%)"
        self._cache_pct_label.setToolTip(tip)
        self._cache_pct_slider.setToolTip(tip)

    def _update_cache_usage_bar(self) -> None:
        used = self._shot_cache.current_bytes
        budget = self._shot_cache.budget_bytes
        if budget > 0:
            self._cache_bar.setValue(min(1000, int(used * 1000 / budget)))
        else:
            self._cache_bar.setValue(0)
        used_mb = used / (1024 * 1024)
        budget_mb = budget / (1024 * 1024)
        self._cache_bar.setFormat(f"{used_mb:.0f}/{budget_mb:.0f} MB")

    def _on_cache_pct_changed(self, pct: int) -> None:
        save_cache_budget_pct(self._model.settings, pct)
        self._shot_cache.budget_bytes = cache_budget_bytes(self._model.settings)
        self._update_cache_labels(pct)
        self._update_cache_usage_bar()
        self._sync_prefetch()

    def _on_cache_pause_toggled(self, paused: bool) -> None:
        self._cache_paused = paused
        self._cache_pause_btn.setText("\u25b6" if paused else "\u23f8")
        if self._prefetch is not None:
            self._prefetch.set_paused(paused)
        if not paused:
            self._sync_prefetch()

    def _on_cache_clear(self) -> None:
        self._shot_cache.clear()
        self._status_bar.showMessage("Playback cache cleared", 2000)

    def _on_shot_cache_changed(self) -> None:
        self._update_timeline_cache_bar()
        self._update_cache_usage_bar()

    def _update_timeline_cache_bar(self) -> None:
        if self._timeline is not None:
            self._timeline.set_cached_frames(self._shot_cache.cached_frames())

    def _playback_tick(self) -> None:
        """Shuttle timer: advance playhead; pause until the next shot is cached."""
        if self._timeline is None or self._shuttle is None or not self._is_playing():
            return

        cur = self._timeline.value()
        nxt = self._next_playback_frame(cur)

        if self._needs_exr_cache(nxt) and not self._shot_cache.contains(nxt):
            self._playback_wait_frame = nxt
            self._shuttle.stop_timer()
            if self._prefetch is not None:
                self._prefetch.request_immediate(nxt)
            return

        self._goto_frame(nxt)

    def _on_prefetch_frame_loaded(self, frame: int, rgb) -> None:
        if rgb is None:
            if frame == self._playback_wait_frame and self._is_playing():
                # Failed read — skip past the bad frame so playback does not stall.
                self._playback_wait_frame = None
                skip = self._next_playback_frame(frame)
                self._goto_frame(skip)
                if self._shuttle is not None and not self._shuttle.is_timer_active():
                    self._shuttle.start_timer()
            return

        if frame == self._current_frame:
            self._composite_shot_with_pixels(frame, rgb)

        if frame == self._playback_wait_frame and self._is_playing():
            self._goto_frame(frame)
            if self._shuttle is not None and not self._shuttle.is_timer_active():
                self._shuttle.start_timer()

        self._sync_prefetch()

    def _refresh_current_frame(self) -> None:
        """Render whichever frame the timeline points at (slate or a shot frame)."""
        if self._current_frame == self._slate_frame:
            self._composite_slate()
        else:
            self._composite_shot(self._current_frame)

    # -- Slate path --

    def _composite_slate(self) -> None:
        """Render the slate at preview resolution and feed it into the OCIO pass."""
        import numpy as np

        w_full, h_full = self.resolution()
        preview_h = 1080
        preview_w = max(1, int(preview_h * w_full / max(h_full, 1)))
        try:
            rgba = render_slate_frame(
                self._form.slate_data(),
                preview_w,
                preview_h,
                thumbnail_b64=self._form.thumbnail_b64(),
            )
        except Exception:
            log.exception("Slate preview render failed")
            return

        self._comp_f32 = np.ascontiguousarray(rgba[..., :3].copy(), dtype=np.float32)
        # The slate is QPainter-rendered in sRGB.  Tag it with the config's
        # *resolved* sRGB authoring space (e.g. "sRGB Encoded Rec.709 (sRGB)")
        # rather than the literal "sRGB", which doesn't exist in ACES configs.
        # Otherwise src→working silently no-ops and working→display double-
        # encodes the slate — it would then only look right with a Raw view.
        self._comp_src_space = self._resolve_overlay_auth_space()
        self._working_f32 = None
        self._composed_working_f32 = None
        self._display_f32 = None
        self._apply_display_transform()

    # -- Shot path --

    def _composite_shot(self, frame: int) -> None:
        """Composite burn-in onto shot ``frame`` and feed into the OCIO pass.

        If the frame is cached, runs synchronously.  Otherwise, queues a
        background load and waits for ``_on_frame_loaded`` to call us again.
        """
        rgb = self._shot_cache.get(frame)
        if rgb is None:
            if self._prefetch is not None:
                self._prefetch.request_immediate(frame)
            return
        self._composite_shot_with_pixels(frame, rgb)

    def _composite_shot_with_pixels(self, frame: int, rgb) -> None:
        """Run the OCIO display pass on a cached shot frame and paint it.

        Prefetch stores **unclamped** float16 HDR (working-space when OCIO
        ran in the worker).  Legacy uint16 cache entries (if any) are still
        accepted but cannot recover values that were clipped at read time.

        Burn-in and watermark are composited in working space inside
        :meth:`_apply_display_transform` (GPU overlay texture / CPU path).
        """
        import numpy as np

        if rgb.dtype == np.uint16:
            # Legacy path only — uint16 reads clamp >1.0 at load.
            self._comp_f32 = rgb.astype(np.float32) / 65535.0
            self._comp_src_space = self._src_colorspace or ""
            self._working_f32 = None
            self._composed_working_f32 = None
        else:
            # float16/32 HDR working (or source HDR if no OCIO). Keep float16
            # for GPU upload — no per-frame widen.
            self._comp_f32 = None
            self._comp_src_space = ""
            self._working_f32 = rgb
            self._composed_working_f32 = None
        self._display_f32 = None
        self._apply_display_transform()

    def done(self, result: int) -> None:
        """Stop background prefetch workers before the dialog closes."""
        if self._prefetch is not None:
            self._prefetch.shutdown()
            self._prefetch = None
        super().done(result)

    # -- Working-space comp pipeline --

    def _resolve_working_space(self) -> str:
        """Return the OCIO compositing colorspace, or '' if unavailable.

        Matches the export pipeline: overlays are composited in a wide-gamut
        scene-linear space (ACES2065-1 / AP0 when available) so the live
        preview is colour-accurate to the rendered output.
        """
        if self._ocio_cfg is None:
            return ""
        if self._working_space:
            return self._working_space
        try:
            from ..core.ocio_utils import get_compositing_space

            self._working_space = get_compositing_space(self._ocio_cfg)
        except Exception:
            log.exception("Could not resolve compositing space")
            self._working_space = ""
        return self._working_space

    def _build_worker_frame_transform(self):
        """Return a worker-thread callable: ``float32 HDR RGB → float16 working RGB``.

        Input is unclamped scene values from :func:`read_exr` (not uint16 —
        UINT16 reads clamp at 1.0 and kill highlight recovery when exposing
        down).  OCIO ``src → working`` runs here so cache hits only pay
        ``working → display`` + gain/gamma on the GUI/GPU path.

        Returns ``None`` if OCIO isn't configured — the cache then stores
        float16 source HDR (still unclamped).
        """
        if self._ocio_cfg is None:
            return None
        src_space = self._src_colorspace or ""
        cpu = self._get_src_to_working_proc(src_space)
        if cpu is None:
            return None

        import PyOpenColorIO as OCIO

        def _transform(rgb_f32):
            # Runs on a prefetch worker thread.  OCIO CPUProcessor.apply()
            # is documented as thread-safe.
            import numpy as np

            buf = np.ascontiguousarray(rgb_f32, dtype=np.float32)
            h, w = buf.shape[:2]
            try:
                cpu.apply(OCIO.PackedImageDesc(buf, w, h, 3))
            except Exception:
                log.exception("Worker src→working OCIO apply failed")
                return np.ascontiguousarray(rgb_f32, dtype=np.float16)
            # float16 preserves working-space headroom (>1.0) for expose-down.
            return buf.astype(np.float16)

        return _transform

    def _resolve_overlay_auth_space(self) -> str:
        """Resolve the sRGB authoring space for QPainter overlays (slate / burn-in
        / watermark), matching :func:`convert.run_exr_to_video`.

        Falls back to the literal :data:`SLATE_COLORSPACE` only when no OCIO
        config is active (the no-colour-management path).
        """
        if self._ocio_cfg is None:
            return SLATE_COLORSPACE
        try:
            from ..core.ocio_utils import get_overlay_authoring_space

            return get_overlay_authoring_space(self._ocio_cfg) or SLATE_COLORSPACE
        except Exception:
            log.exception("Could not resolve overlay authoring space")
            return SLATE_COLORSPACE

    def _get_src_to_working_proc(self, src_space: str):
        """Return a cached OCIO ``src → working`` CPUProcessor (or ``None``)."""
        if not src_space or self._ocio_cfg is None:
            return None
        working_space = self._resolve_working_space()
        if not working_space:
            return None
        key = ("src->work", src_space, working_space)
        proc = self._ocio_proc_cache.get(key)
        if proc is not None:
            return proc
        try:
            from ..core.ocio_utils import make_cpu_processor

            proc = make_cpu_processor(self._ocio_cfg, src_space, working_space)
        except Exception:
            log.exception(
                "Failed to build src→working processor (%s → %s)", src_space, working_space
            )
            proc = None
        self._ocio_proc_cache[key] = proc
        return proc

    def _get_working_to_display_proc(self, display: str, view: str):
        """Return a cached OCIO ``working → display/view`` CPUProcessor (or ``None``).

        This is the *static* path (no live viewer EC).  The slate preview primarily
        uses the dynamic viewer processor (see :meth:`_ensure_viewer_display_proc`).
        """
        if not display or self._ocio_cfg is None:
            return None
        working_space = self._resolve_working_space() or self._comp_src_space
        if not working_space:
            return None
        key = ("work->disp", working_space, display, view)
        proc = self._ocio_proc_cache.get(key)
        if proc is not None:
            return proc
        try:
            from ..core.ocio_utils import make_display_processor

            proc = make_display_processor(
                self._ocio_cfg, working_space, display, view, exposure=0.0, gamma=1.0
            )
        except Exception:
            proc = None
        self._ocio_proc_cache[key] = proc
        return proc

    def _ensure_viewer_display_proc(self, display: str, view: str) -> object | None:
        """Ensure a working→display processor with dynamic gain (exposure).

        Exposure prop is on ``self._ec_exposure_prop``.  Viewer gamma is *not*
        part of this processor — it is applied post-display (Nuke order).
        """
        if not display or self._ocio_cfg is None:
            self._viewer_display_proc = None
            self._ec_exposure_prop = None
            self._ec_gamma_prop = None
            return None

        working_space = self._resolve_working_space() or self._comp_src_space
        if not working_space:
            self._viewer_display_proc = None
            self._ec_exposure_prop = None
            self._ec_gamma_prop = None
            return None

        # Rebuild only when display/view (or working space) actually changed.
        if self._viewer_display_proc is not None and getattr(
            self, "_last_viewer_display", None
        ) == (working_space, display, view):
            return self._viewer_display_proc

        try:
            from ..core.ocio_utils import make_viewer_display_processor

            proc, exp_prop, _gamma_unused = make_viewer_display_processor(
                self._ocio_cfg, working_space, display, view
            )
        except Exception:
            proc, exp_prop = None, None

        self._viewer_display_proc = proc
        self._ec_exposure_prop = exp_prop
        self._ec_gamma_prop = None
        self._last_viewer_display = (working_space, display, view)

        if self._ec_exposure_prop is not None:
            self._ec_exposure_prop.setDouble(self._exposure_stops())

        return self._viewer_display_proc

    def _build_working_f32(self):
        """src → working (scene-linear).  Cached; rebuilds only on frame change.

        Returns the existing ``_working_f32`` immediately if a worker
        transform already produced it — that's the fast playback path.
        """
        import numpy as np

        if self._working_f32 is not None:
            return self._working_f32
        if self._comp_f32 is None:
            return None

        cpu = self._get_src_to_working_proc(self._comp_src_space)
        if cpu is None:
            self._working_f32 = self._comp_f32
            return self._working_f32

        try:
            import PyOpenColorIO as OCIO

            h, w = self._comp_f32.shape[:2]
            buf = np.ascontiguousarray(self._comp_f32.copy(), dtype=np.float32)
            cpu.apply(OCIO.PackedImageDesc(buf, w, h, 3))
            self._working_f32 = buf
        except Exception:
            log.exception("GUI-thread src→working OCIO apply failed")
            self._working_f32 = self._comp_f32
        return self._working_f32

    def _apply_display_transform(self) -> None:
        """Working-space composite → display (GPU OCIO preferred).

        GPU path (pyociodisplay-style):
          plate texture (float16 OK) + cached overlay texture + GLSL OCIO/EC.
        CPU path: alpha-over overlays then ``applyRGB`` + QImage.
        """
        import numpy as np

        working = self._build_working_f32()
        if working is None:
            return

        is_shot = self._current_frame != self._slate_frame

        if self._use_gpu and self._gpu_plane is not None and self._gpu_plane.is_alive():
            # pyociodisplay pattern: plate texture + overlay texture + GPU OCIO.
            # No full-plate CPU alpha-over; no float16→float32 widen; gain/gamma
            # are uniforms only (handled in set_exposure/set_gamma).
            h, w = int(working.shape[0]), int(working.shape[1])
            if is_shot:
                ov, ov_key = self._gpu_overlay_layer(w, h)
                self._gpu_plane.set_overlay_rgba(ov, key=ov_key)
            else:
                self._gpu_plane.set_overlay_rgba(None)
            self._gpu_plane.set_working_image(working)
            self._composed_working_f32 = None
            self._display_f32 = None
            return

        # ---- CPU fallback ----
        if working.dtype != np.float32:
            working = np.ascontiguousarray(working, dtype=np.float32)
        composed = self._composite_overlays_working_space(working, is_shot)
        self._composed_working_f32 = composed
        if self._ocio_cfg is None:
            self._display_f32 = composed
            self._refresh_gain_gamma()
            return

        idx = self._display_view_combo.currentIndex()
        if not (0 <= idx < len(self._display_view_pairs)):
            self._display_f32 = composed
            self._refresh_gain_gamma()
            return

        display, view = self._display_view_pairs[idx]
        cpu = self._ensure_viewer_display_proc(display, view)
        if cpu is None:
            cpu = self._get_working_to_display_proc(display, view)
            if cpu is None:
                self._display_f32 = composed
                self._refresh_gain_gamma()
                return
            try:
                h, w = composed.shape[:2]
                pixels = np.ascontiguousarray(composed.reshape(-1, 3).copy())
                cpu.applyRGB(pixels)
                self._display_f32 = pixels.reshape(h, w, 3)
            except Exception:
                log.exception("Static working→display OCIO apply failed")
                self._display_f32 = composed
            self._refresh_gain_gamma()
            return

        try:
            h, w = composed.shape[:2]
            pixels = np.ascontiguousarray(composed.reshape(-1, 3).copy())
            cpu.applyRGB(pixels)
            self._display_f32 = pixels.reshape(h, w, 3)
        except Exception:
            log.exception("Viewer EC working→display OCIO apply failed")
            self._display_f32 = composed

        # Nuke order: display first, then pow(display, 1/γ).
        self._paint_display_with_viewer_gamma(self._display_f32)

    def _invalidate_display_cache(self) -> None:
        """Display/view combo changed — rebuild display leg and re-show current frame."""
        self._viewer_display_proc = None
        self._ec_exposure_prop = None
        self._ec_gamma_prop = None
        self._last_viewer_display = None
        self._display_f32 = None
        self._sync_gpu_view_settings()
        self._apply_display_transform()

    def _refresh_gain_gamma(self) -> None:
        """CPU viewer gain/gamma when dynamic EC is unavailable (or gamma-only)."""
        if self._use_gpu and self._gpu_plane is not None:
            self._gpu_plane.set_exposure_stops(self._exposure_stops())
            self._gpu_plane.set_gamma(float(getattr(self, "_gamma", 1.0)))
            return

        if self._display_f32 is None:
            return

        out = self._display_f32
        # Gain via EC when available; otherwise multiply post-display (fallback).
        if self._ec_exposure_prop is None:
            gain = float(getattr(self, "_gain", 1.0))
            if gain != 1.0:
                out = out * gain
        self._paint_display_with_viewer_gamma(out)

    def _paint_display_with_viewer_gamma(self, rgb_f32) -> None:
        """Apply Nuke post-display gamma then paint: ``pow(max(0, rgb), 1/γ)``."""
        import numpy as np

        gamma = float(getattr(self, "_gamma", 1.0))
        out = rgb_f32
        if abs(gamma - 1.0) > 1e-6:
            exp = nuke_viewer_gamma_power(gamma)
            # Negatives left alone (Nuke); non-negative channels powered.
            pos = np.maximum(out, 0.0)
            powered = np.power(pos, exp)
            out = np.where(out >= 0.0, powered, out)
        self._paint_display_buffer(out)

    def _paint_display_buffer(self, rgb_f32) -> None:
        """Take a float32 RGB buffer (0-1 range) and paint it to the preview view."""
        import numpy as np
        from PySide6.QtGui import QImage, QPixmap

        comp_u8 = np.ascontiguousarray((np.clip(rgb_f32, 0.0, 1.0) * 255 + 0.5).astype(np.uint8))

        fh, fw = comp_u8.shape[:2]
        # 4-byte-aligned bytesPerLine for RGB888 (required by some backends).
        bpl = (fw * 3 + 3) & ~3
        if bpl == fw * 3:
            buf = comp_u8
        else:
            buf = np.zeros((fh, bpl), dtype=np.uint8)
            flat = comp_u8.reshape(fh, -1)
            buf[:, : fw * 3] = flat
        # .copy() so QImage owns its memory (external buffer would dangle).
        qimg = QImage(buf.data, fw, fh, bpl, QImage.Format.Format_RGB888).copy()
        del buf
        # HiDPI: nearest-neighbor upscale (raw pixel grid, not bilinear).
        dpr = float(self.devicePixelRatioF() or 1.0)
        if dpr > 1.01:
            qimg = qimg.scaled(
                int(fw * dpr + 0.5),
                int(fh * dpr + 0.5),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )
            qimg.setDevicePixelRatio(dpr)
        pix = QPixmap.fromImage(qimg)
        if dpr > 1.01:
            pix.setDevicePixelRatio(dpr)

        scene = self._preview._scene
        if self._preview_pixmap_item is not None:
            scene.removeItem(self._preview_pixmap_item)
        self._preview_pixmap_item = scene.addPixmap(pix)
        self._preview_pixmap_item.setZValue(0)

        w, h = self.resolution()
        preview_h = 1080
        preview_w = int(preview_h * w / max(h, 1))
        # Logical pixmap size (device pixels / dpr).
        lw = max(1, int(round(pix.width() / max(dpr, 1.0))))
        lh = max(1, int(round(pix.height() / max(dpr, 1.0))))
        if lw > 0 and lh > 0:
            sx = preview_w / lw
            sy = preview_h / lh
            s = min(sx, sy)
            self._preview_pixmap_item.setScale(s)
            self._preview_pixmap_item.setPos(
                (preview_w - lw * s) / 2,
                (preview_h - lh * s) / 2,
            )

    def _reapply_display_with_ec(self) -> None:
        """Fast path for live gain on the CPU EC processor (+ post-display gamma).

        GPU path never reaches here (uniforms update in ``_on_gain/gamma_changed``).
        """
        import numpy as np

        if self._use_gpu and self._gpu_plane is not None:
            self._gpu_plane.set_exposure_stops(self._exposure_stops())
            self._gpu_plane.set_gamma(float(getattr(self, "_gamma", 1.0)))
            return

        if self._composed_working_f32 is None or self._viewer_display_proc is None:
            self._apply_display_transform()
            return

        try:
            h, w = self._composed_working_f32.shape[:2]
            pixels = np.ascontiguousarray(self._composed_working_f32.reshape(-1, 3).copy())
            self._viewer_display_proc.applyRGB(pixels)
            self._display_f32 = pixels.reshape(h, w, 3)
        except Exception:
            self._display_f32 = self._composed_working_f32

        self._paint_display_with_viewer_gamma(self._display_f32)

    # -- Working-space overlay composite (burn-in + watermark) --

    def _alpha_over_rgba(self, bg_rgba, fg_rgba):
        """Straight alpha-over for two float32 RGBA buffers."""
        import numpy as np

        if fg_rgba is None:
            return bg_rgba
        a = fg_rgba[..., 3:4]
        out = np.empty_like(bg_rgba, dtype=np.float32)
        out[..., :3] = fg_rgba[..., :3] * a + bg_rgba[..., :3] * (1.0 - a)
        out[..., 3:4] = a + bg_rgba[..., 3:4] * (1.0 - a)
        return out

    def _gpu_overlay_layer(self, w: int, h: int):
        """Return (combined lin RGBA overlay or None, cache key).

        Burn-in + watermark are rasterised + linearised once per content change
        (signature includes substituted tokens, so ``<frame>`` still updates).
        The combined layer is uploaded as a *separate* GPU texture — not
        alpha-over'd onto the plate on the CPU every frame.
        """
        import numpy as np

        burnin_lin = self._cached_burnin_lin_rgba(w, h)
        wm_lin = self._cached_watermark_lin_rgba(w, h)
        key = (
            self._overlay_lin_cache.get("burnin_sig"),
            self._overlay_lin_cache.get("wm_sig"),
        )
        if self._overlay_lin_cache.get("gpu_combined_sig") == key:
            return self._overlay_lin_cache.get("gpu_combined_lin"), key

        if burnin_lin is None and wm_lin is None:
            comb = None
        else:
            comb = np.zeros((h, w, 4), dtype=np.float32)
            if burnin_lin is not None:
                comb = self._alpha_over_rgba(comb, burnin_lin)
            if wm_lin is not None:
                comb = self._alpha_over_rgba(comb, wm_lin)
        self._overlay_lin_cache["gpu_combined_sig"] = key
        self._overlay_lin_cache["gpu_combined_lin"] = comb
        return comb, key

    def _composite_overlays_working_space(self, working_f32, is_shot: bool):
        """Alpha-over burn-in + watermark (shot frames only) on the working-space frame.

        Overlays are authored in display-encoded sRGB (QPainter-rendered)
        and need to be linearised into the working colorspace before
        compositing — otherwise white text would read as ``1.0`` linear,
        which is way too hot.  Mirrors :mod:`convert.run_exr_to_video`.

        The linearised RGBA buffers are *cached* and only rebuilt when the
        burn-in fields, watermark settings, frame size, or working space
        change — re-linearising every frame was eating the event loop.
        """
        h, w = working_f32.shape[:2]
        out = working_f32

        # Burn-in and watermark apply to shot frames only — the slate is its own
        # designed frame and shouldn't be stamped over.
        if is_shot:
            burnin_lin = self._cached_burnin_lin_rgba(w, h)
            if burnin_lin is not None:
                out = _alpha_over_linear(out, burnin_lin)

            wm_lin = self._cached_watermark_lin_rgba(w, h)
            if wm_lin is not None:
                out = _alpha_over_linear(out, wm_lin)

        return out

    def _linearise_overlay_cached(self, rgba_u8):
        """sRGB RGBA8 → working-space float32 RGBA, with safe fallback."""
        import numpy as np

        if rgba_u8 is None:
            return None
        working_space = self._resolve_working_space()
        if self._ocio_cfg is None or not working_space:
            return rgba_u8.astype(np.float32) / 255.0
        try:
            from ..core.ocio_utils import linearize_overlay

            return linearize_overlay(self._ocio_cfg, rgba_u8, working_space=working_space)
        except Exception:
            return rgba_u8.astype(np.float32) / 255.0

    def _cached_burnin_lin_rgba(self, w: int, h: int):
        """Return cached linearised burn-in overlay (RGBA float32) or ``None``."""
        from ..render import tokens as tok
        from ..render.burnin import render_burnin_overlay

        burnin_on = self._model is None or self._model.burnin_enabled
        values = self._token_values()
        fields = {k: tok.substitute(v, values) for k, v in self._effective_burnin_fields().items()}
        if not burnin_on or not any((v or "").strip() for v in fields.values()):
            sig = ("burnin", w, h, None)
        else:
            sig = ("burnin", w, h, tuple(sorted(fields.items())))
        if self._overlay_lin_cache.get("burnin_sig") == sig:
            return self._overlay_lin_cache.get("burnin_lin")

        if sig[3] is None:
            lin = None
        else:
            try:
                rgba = render_burnin_overlay(w, h, fields)
            except RuntimeError:
                rgba = None
            lin = self._linearise_overlay_cached(rgba)
        self._overlay_lin_cache["burnin_sig"] = sig
        self._overlay_lin_cache["burnin_lin"] = lin
        return lin

    def _cached_watermark_lin_rgba(self, w: int, h: int):
        """Return cached linearised watermark overlay (RGBA float32) or ``None``."""
        from ..render import tokens as tok
        from ..render.watermark import render_watermark_overlay

        params = dict(self._form.watermark_params())
        params["text"] = tok.substitute(params.get("text", ""), self._token_values())
        text = (params.get("text") or "").strip()
        wm_on = self._model is None or self._model.watermark_enabled
        if not (wm_on and text):
            sig = ("wm", w, h, None)
        else:
            sig = ("wm", w, h, tuple(sorted(params.items())))
        if self._overlay_lin_cache.get("wm_sig") == sig:
            return self._overlay_lin_cache.get("wm_lin")

        if sig[3] is None:
            lin = None
        else:
            try:
                rgba = render_watermark_overlay(w, h, params)
            except Exception:
                rgba = None
            lin = self._linearise_overlay_cached(rgba)
        self._overlay_lin_cache["wm_sig"] = sig
        self._overlay_lin_cache["wm_lin"] = lin
        return lin

    def _invalidate_overlay_cache(self) -> None:
        """Drop linearised overlay buffers (form / watermark / display changed)."""
        self._overlay_lin_cache.clear()

    def _effective_burnin_fields(self) -> dict[str, str]:
        """Return the burn-in cells to render — manual entry first, slate-derived fallback."""
        from ..render.burnin import burnin_fields_from_slate

        manual = self._model.burnin_fields if self._model is not None else {}
        if any((v or "").strip() for v in manual.values()):
            return manual
        return burnin_fields_from_slate(self._form.slate_data(), self._input_path)

    def _token_values(self) -> dict[str, str]:
        """Resolve overlay tokens for the frame currently shown in the preview.

        ``<frame>`` reflects the scrubbed frame so the burn-in counter updates
        live as the timeline moves; the rest come from the slate metadata.
        """
        from pathlib import Path

        from ..render import tokens as tok

        slate_render = self._form.slate_data() if self._form is not None else {}
        w, h = self.resolution()
        start_f = self._first_shot
        end_f = self._last_shot
        pad = max(4, len(str(end_f))) if end_f is not None else 4
        input_name = Path(self._input_path).name if self._input_path else ""
        return tok.build_values(
            slate_render,
            input_name=input_name,
            frame=self._current_frame,
            frame_pad=pad,
            start_frame=start_f,
            end_frame=end_f,
            resolution=f"{w}x{h}",
        )

    # -- Shared --

    def resolution(self) -> tuple[int, int]:
        return self._model.slate_resolution

    def fps(self) -> float:
        return self._model.slate_fps

    def watermark_params(self) -> dict:
        """Return the current watermark settings (passes through to the form)."""
        return self._form.watermark_params()

    def slate_data(self) -> dict:
        """Return the slate form data."""
        data = self._form.slate_data()
        data["colorspace"] = self._dst_colorspace or "\u2014"
        return data

    def thumbnail_b64(self) -> str:
        """Return the raw base64 thumbnail string."""
        return self._form.thumbnail_b64()
