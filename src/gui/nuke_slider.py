"""Nuke-style viewer gain/gamma slider (shared by slate + sequence player)."""

from __future__ import annotations

import math

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFontDatabase,
    QFontMetricsF,
    QMouseEvent,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import QWidget


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


__all__ = ["NukeSlider"]
