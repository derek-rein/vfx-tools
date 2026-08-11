"""Reusable EXR sequence player: transport, RAM cache, OCIO GPU/CPU display.

Used by the slate/overlay editor (with :class:`OverlayHooks`) and by the
image-sequence browser quick preview (playback only).
"""

from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from PySide6.QtCore import QSettings, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...core.constants import APP_NAME, APP_ORG
from ...services.cache_prefs import cache_budget_bytes
from ...services.exr_prefetch import ExrPrefetchService
from ...services.frame_cache import FrameCache
from ...services.video_prefetch import VideoPrefetchService
from ..ocio_gpu_plane import (
    OcioGpuImagePlane,
    gpu_ocio_available,
    nuke_viewer_gamma_power,
)
from ..timeline_slider import TimelineSlider
from .preview_view import ImagePreviewView
from .shuttle_bar import ShuttleBar

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

_PREFETCH_WORKERS = 4


@runtime_checkable
class OverlayHooks(Protocol):
    """Optional hooks for synthetic frames (slate) and live overlays."""

    def is_synthetic_frame(self, frame: int) -> bool: ...

    def render_synthetic_frame(self, frame: int) -> tuple[object, str] | None:
        """Return ``(float32 RGB HxWx3, src_colorspace)`` for non-disk frames."""
        ...

    def gpu_overlay_rgba(self, w: int, h: int, frame: int) -> tuple[object | None, object]:
        """Return ``(linear RGBA float32 or None, cache key)`` for GPU overlay texture."""
        ...

    def cpu_composite_overlays(self, working_f32: object, frame: int) -> object:
        """Return working RGB with burn-in/watermark composited (shot frames)."""
        ...


class SequencePlayer(QWidget):
    """Cache-first sequence playback with OCIO viewer controls.

    Disk frames are decoded into a :class:`~src.services.frame_cache.FrameCache`
    and prefetched ahead of the playhead. Display prefers **GPU OCIO**;
    CPU ``applyRGB`` is the fallback. Optional :class:`OverlayHooks` supply
    synthetic frames (slate) and burn-in/watermark layers.
    """

    frame_changed = Signal(int)
    playing_changed = Signal(bool)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        settings: QSettings | None = None,
        show_cache_ui: bool = True,
        prefer_gpu: bool = True,
    ) -> None:
        super().__init__(parent)
        self._settings = settings if settings is not None else QSettings(APP_ORG, APP_NAME)
        self._show_cache_ui = show_cache_ui
        self._prefer_gpu = prefer_gpu

        self._ocio_cfg: object | None = None
        self._src_colorspace: str = ""
        self._ocio_proc_cache: dict[tuple, object] = {}

        self._comp_f32 = None
        self._comp_src_space = ""
        self._working_f32 = None
        self._working_space: str = ""
        self._display_f32 = None
        self._composed_working_f32 = None
        self._preview_pixmap_item = None
        self._gpu_plane: OcioGpuImagePlane | None = None
        self._use_gpu = False
        self._gpu_init_failed = False

        self._viewer_display_proc = None
        self._ec_exposure_prop = None
        self._ec_gamma_prop = None
        self._last_viewer_display: tuple[str, str, str] | None = None

        self._exr_seq = None
        self._video_path: str | None = None
        self._shot_frames: list[int] = []
        self._shot_frames_set: set[int] = set()
        self._first_shot: int | None = None
        self._last_shot: int | None = None
        self._current_frame: int = 1
        self._width: int = 1920
        self._height: int = 1080
        self._fps: float = 24.0

        self._shot_cache = FrameCache(cache_budget_bytes(self._settings), self)
        self._prefetch: ExrPrefetchService | VideoPrefetchService | None = None
        self._playback_wait_frame: int | None = None
        self._cache_paused = False
        self._hooks: OverlayHooks | None = None

        self._cache_bar: QProgressBar | None = None
        self._cache_pause_btn: QToolButton | None = None
        self._cache_clear_btn: QToolButton | None = None
        self._cache_host: QWidget | None = None

        self._gain = 1.0
        self._gamma = 1.0
        self._display_view_pairs: list[tuple[str, str]] = []
        # Prefer colorimetric / un-tone-mapped view for video-originated media.
        self._prefer_video_monitoring = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self._preview = ImagePreviewView()
        self._preview_stack = QStackedWidget()
        self._preview_stack.addWidget(self._preview)
        # Create the GPU plane *eagerly* (before the top-level window is shown).
        # Qt 6.4+ docs: adding the first QOpenGLWidget to an *already shown*
        # top-level recreates the native window (RasterSurface → OpenGLSurface)
        # and is a known crash path — especially on macOS dialogs. The slate
        # editor is fine because its player is built in the dialog constructor;
        # the browser must do the same (create player before exec/show).
        if prefer_gpu and gpu_ocio_available():
            try:
                plane = OcioGpuImagePlane(parent=self._preview_stack)
                plane.gpu_failed.connect(self._on_gpu_failed)
                self._preview_stack.addWidget(plane)
                self._gpu_plane = plane
                self._use_gpu = True
                self._preview_stack.setCurrentWidget(plane)
                log.info("SequencePlayer: GPU OCIO display enabled")
            except Exception:
                log.exception("GPU OCIO preview init failed; using CPU path")
                self._gpu_plane = None
                self._use_gpu = False
                self._gpu_init_failed = True
        else:
            log.info("SequencePlayer: CPU display path (prefer_gpu=%s)", prefer_gpu)

        self._build_viewer_controls(root)
        root.addWidget(self._preview_stack, 1)

        self._preview.set_frame_size(self._width, self._height)

        self._timeline = TimelineSlider()
        ideal_h = self._timeline._ideal_height()
        self._timeline.setFixedHeight(ideal_h)
        self._timeline.set_range(1, 1)
        self._timeline.set_value(1)
        self._timeline.value_changed.connect(self._on_timeline_changed)

        self._shuttle = ShuttleBar(self._timeline, fps=self._fps)
        self._shuttle.setFixedHeight(ideal_h)
        self._shuttle.set_advance_callback(self._playback_tick)
        self._shuttle.playing_changed.connect(self._on_shuttle_play_toggled)

        transport_row = QHBoxLayout()
        transport_row.setContentsMargins(0, 0, 0, 0)
        transport_row.setSpacing(0)
        transport_row.addWidget(self._shuttle)
        transport_row.addWidget(self._timeline, 1)
        root.addLayout(transport_row)

        if self._show_cache_ui:
            self._build_cache_ui(root)

        self._shot_cache.cache_changed.connect(self._on_shot_cache_changed)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        QTimer.singleShot(0, self.fit_in_view)

    def _release_gpu_plane(self) -> None:
        """Free GL resources; leave the QObject parented for Qt to destroy.

        Qt docs for QOpenGLWidget: do **not** rely on ``deleteLater()`` for GL
        cleanup (context may not be current). Also avoid ``setParent(None)`` +
        dropping the last Python ref (PySide double-destroy → SIGSEGV in
        ``QObject::~QObject``).
        """
        plane = self._gpu_plane
        self._use_gpu = False
        if plane is None:
            return
        try:
            self._preview_stack.setCurrentWidget(self._preview)
        except RuntimeError:
            pass
        try:
            plane.hide()
            plane.release_gl()
        except Exception:
            log.debug("GPU plane release_gl failed", exc_info=True)
        # Keep plane as a child of the stack; mark dead so we never re-use it.
        self._gpu_plane = None
        self._gpu_init_failed = True

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def _begin_media_load(
        self,
        *,
        ocio_cfg: object | None,
        src_colorspace: str,
        fps: float = 0.0,
        resolution: tuple[int, int] | None = None,
    ) -> None:
        """Shared teardown + OCIO/budget reset before load_sequence / load_video."""
        self.shutdown_prefetch_only()
        self._shot_cache.clear()
        self._shot_cache.budget_bytes = cache_budget_bytes(self._settings)
        self._ocio_cfg = ocio_cfg
        self._src_colorspace = src_colorspace or ""
        self._ocio_proc_cache.clear()
        self._working_space = ""
        self._last_viewer_display = None
        self._viewer_display_proc = None
        self._ec_exposure_prop = None
        self._ec_gamma_prop = None
        self._exr_seq = None
        self._video_path = None
        self._shot_frames = []
        self._shot_frames_set = set()
        self._first_shot = None
        self._last_shot = None
        self._playback_wait_frame = None
        if fps > 0:
            self.set_fps(fps)
        if resolution is not None and resolution[0] > 0 and resolution[1] > 0:
            self.set_resolution(resolution[0], resolution[1])

    def _finish_media_load(self) -> bool:
        """Wire timeline + display after frames / colorspace are known.

        Returns ``False`` if frame range was never established (fail closed).
        """
        if self._first_shot is None or self._last_shot is None:
            log.error("Media load finished without a frame range")
            self._repopulate_display_views()
            self._sync_gpu_view_settings()
            return False
        self._timeline.set_range(self._first_shot, self._last_shot)
        self._timeline.set_marker_frames({})
        self._current_frame = self._first_shot
        self._timeline.set_value(self._current_frame)
        self._repopulate_display_views()
        self._sync_gpu_view_settings()
        self._sync_prefetch()
        self.refresh()
        return True

    def load_sequence(
        self,
        input_path: str,
        *,
        fps: float = 24.0,
        ocio_cfg: object | None = None,
        src_colorspace: str = "",
        resolution: tuple[int, int] | None = None,
        prefer_video_monitoring: bool = False,
    ) -> bool:
        """Resolve *input_path* as an EXR sequence and start prefetch.

        Returns ``True`` when at least one frame was found.

        *prefer_video_monitoring*: if the config exposes a video-oriented view
        via viewing rules / encodings (``getDefaultView(display, videoCS)``),
        select it; otherwise keep the config default. Leave false for
        camera/scene-linear EXR.
        """
        self._prefer_video_monitoring = bool(prefer_video_monitoring)
        self._begin_media_load(
            ocio_cfg=ocio_cfg,
            src_colorspace=src_colorspace,
            fps=fps,
            resolution=resolution,
        )

        if not input_path:
            self._repopulate_display_views()
            self._sync_gpu_view_settings()
            return False

        try:
            from ...core.sequence import find_exr_sequence_info, resolve_sequence_src_colorspace

            paths, _name, frames, _pad, seq = find_exr_sequence_info(input_path)
            if not frames:
                self._repopulate_display_views()
                self._sync_gpu_view_settings()
                return False
            self._shot_frames = sorted(frames)
            self._shot_frames_set = set(self._shot_frames)
            self._first_shot = self._shot_frames[0]
            self._last_shot = self._shot_frames[-1]
            self._exr_seq = seq

            # Caller (post-convert dst / tab) preferred; else file attrs.
            probe_path = paths[0] if paths else input_path
            self._src_colorspace = resolve_sequence_src_colorspace(
                probe_path, self._ocio_cfg, preferred=self._src_colorspace
            )
            log.info(
                "SequencePlayer load: src=%r working=%r frames=%s–%s",
                self._src_colorspace,
                self._resolve_working_space(),
                self._first_shot,
                self._last_shot,
            )
        except Exception:
            log.exception("Could not resolve EXR sequence for player: %s", input_path)
            self._repopulate_display_views()
            self._sync_gpu_view_settings()
            return False

        # Same OCIO path as the slate editor: worker applies src→working into
        # the RAM cache; the GPU/CPU viewer only does working→display.
        self._prefetch = ExrPrefetchService(
            self._exr_seq,
            self._shot_cache,
            self._shot_frames,
            max_workers=_PREFETCH_WORKERS,
            frame_transform=self._build_worker_frame_transform(),
            parent=self,
        )
        self._prefetch.frame_loaded.connect(self._on_prefetch_frame_loaded)
        return self._finish_media_load()

    def load_video(
        self,
        path: str,
        *,
        fps: float = 0.0,
        ocio_cfg: object | None = None,
        src_colorspace: str = "",
        resolution: tuple[int, int] | None = None,
    ) -> bool:
        """Open a video file for cache-first playback (same viewer as sequences).

        Frame indices are 1-based over an estimated frame count from the probe.
        Colour: decode → worker ``src→working`` → display/view (slate contract).
        Tries the config's video-monitoring view (viewing rules) when present.
        """
        self._prefer_video_monitoring = True
        self._begin_media_load(
            ocio_cfg=ocio_cfg,
            src_colorspace=src_colorspace,
            resolution=resolution,
        )

        if not path:
            self._repopulate_display_views()
            self._sync_gpu_view_settings()
            return False

        try:
            from ...core.r3d import DECODE_PREVIEW, is_r3d_path, scale_for_decode_mode
            from ...core.video import probe_video, resolve_video_src_colorspace

            w, h, probed_fps, n_frames = probe_video(path)
        except Exception:
            log.exception("Could not probe video for player: %s", path)
            self._repopulate_display_views()
            self._sync_gpu_view_settings()
            return False

        n_frames = max(1, int(n_frames or 1))
        use_fps = float(fps) if fps and fps > 0 else float(probed_fps or 24.0)
        if use_fps <= 0:
            use_fps = 24.0
        self.set_fps(use_fps)
        # Probe size when caller did not pass a positive resolution.
        # R3D player path uses DECODE_PREVIEW (half) — set resolution from the
        # decode ladder so the viewer matches actual prefetch buffers.
        if w > 0 and h > 0 and (resolution is None or resolution[0] <= 0 or resolution[1] <= 0):
            if is_r3d_path(path):
                ladder = scale_for_decode_mode(DECODE_PREVIEW)
                self.set_resolution(
                    max(1, int(round(w * ladder))),
                    max(1, int(round(h * ladder))),
                )
            else:
                self.set_resolution(int(w), int(h))

        self._video_path = path
        self._shot_frames = list(range(1, n_frames + 1))
        self._shot_frames_set = set(self._shot_frames)
        self._first_shot = 1
        self._last_shot = n_frames

        self._src_colorspace = resolve_video_src_colorspace(
            path, self._ocio_cfg, preferred=self._src_colorspace
        )
        log.info(
            "SequencePlayer load_video: %s frames=%s fps=%.3f src=%r working=%r",
            path,
            n_frames,
            use_fps,
            self._src_colorspace,
            self._resolve_working_space(),
        )

        self._prefetch = VideoPrefetchService(
            path,
            self._shot_cache,
            self._shot_frames,
            fps=use_fps,
            frame_transform=self._build_worker_frame_transform(),
            parent=self,
        )
        self._prefetch.frame_loaded.connect(self._on_prefetch_frame_loaded)
        return self._finish_media_load()

    def clear(self) -> None:
        """Stop playback, drop cache, and reset to an empty timeline."""
        self.set_playing(False)
        self.shutdown_prefetch_only()
        self._shot_cache.clear()
        self._exr_seq = None
        self._video_path = None
        self._shot_frames = []
        self._shot_frames_set = set()
        self._first_shot = None
        self._last_shot = None
        self._comp_f32 = None
        self._working_f32 = None
        self._display_f32 = None
        self._composed_working_f32 = None
        self._timeline.set_range(1, 1)
        self._timeline.set_marker_frames({})
        self._timeline.set_cached_frames(set())
        self._current_frame = 1
        self._timeline.set_value(1)
        if self._gpu_plane is not None:
            try:
                self._gpu_plane.set_overlay_rgba(None)
            except Exception:
                pass

    def shutdown(self) -> None:
        """Stop prefetch workers and release playback resources.

        Safe to call before the dialog closes. Does not ``deleteLater`` this
        widget — the parent dialog owns the QObject tree.
        """
        self.set_playing(False)
        self.shutdown_prefetch_only()
        try:
            self._shot_cache.cache_changed.disconnect(self._on_shot_cache_changed)
        except (RuntimeError, TypeError):
            pass
        try:
            self._shot_cache.clear()
        except RuntimeError:
            pass
        self._hooks = None
        self._working_f32 = None
        self._comp_f32 = None
        self._display_f32 = None
        self._composed_working_f32 = None
        self._release_gpu_plane()

    def shutdown_prefetch_only(self) -> None:
        if self._prefetch is not None:
            try:
                self._prefetch.frame_loaded.disconnect(self._on_prefetch_frame_loaded)
            except (RuntimeError, TypeError):
                pass
            self._prefetch.shutdown()
            self._prefetch = None

    def set_frame(self, frame: int) -> None:
        self._goto_frame(frame)

    def frame(self) -> int:
        return self._current_frame

    def set_playing(self, playing: bool) -> None:
        self._shuttle.set_playing(playing)

    def is_playing(self) -> bool:
        return self._shuttle.is_playing()

    def set_fps(self, fps: float) -> None:
        self._fps = max(1.0, float(fps))
        self._shuttle.set_fps(self._fps)

    def set_range(self, first: int, last: int) -> None:
        self._timeline.set_range(first, last)
        if self._current_frame < first or self._current_frame > last:
            self._current_frame = first
            self._timeline.set_value(first)

    def set_markers(self, markers: dict[int, str]) -> None:
        self._timeline.set_marker_frames(dict(markers))

    def set_overlay_hooks(self, hooks: OverlayHooks | None) -> None:
        self._hooks = hooks

    def refresh(self) -> None:
        """Re-render the current frame (form/overlay changes, etc.)."""
        self._refresh_current_frame()

    def fit_in_view(self) -> None:
        # Guard against QTimer.singleShot(0) firing after the dialog was closed.
        try:
            if self._use_gpu and self._gpu_plane is not None:
                try:
                    self._gpu_plane.fit_in_view()
                except RuntimeError:
                    return
            self._preview.fit_in_view()
        except RuntimeError:
            return

    def set_resolution(self, w: int, h: int) -> None:
        self._width = max(1, int(w))
        self._height = max(1, int(h))
        self._preview.set_frame_size(self._width, self._height)

    def resolution(self) -> tuple[int, int]:
        return self._width, self._height

    def first_shot_frame(self) -> int | None:
        return self._first_shot

    def last_shot_frame(self) -> int | None:
        return self._last_shot

    def shot_frames(self) -> list[int]:
        return list(self._shot_frames)

    # ------------------------------------------------------------------
    # Viewer controls
    # ------------------------------------------------------------------

    def _build_viewer_controls(self, parent_layout: QVBoxLayout) -> None:
        from ..nuke_slider import NukeSlider

        strip = QHBoxLayout()
        strip.setContentsMargins(4, 1, 4, 1)
        strip.setSpacing(6)

        self._gain_value_label = QLabel("1.0")
        self._gain_value_label.setFixedWidth(28)
        self._gain_value_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self._style_viewer_value_label(self._gain_value_label, 1.0)
        strip.addWidget(self._gain_value_label)

        self._gain_slider = NukeSlider(
            default=1.0,
            val_min=0.01,
            val_max=64.0,
            map_mode="log",
        )
        self._gain_slider.setMinimumWidth(140)
        self._gain_slider.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        strip.addWidget(self._gain_slider, 1)

        strip.addSpacing(8)

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

        self._gamma_slider = NukeSlider(
            default=1.0,
            val_min=0.0,
            val_max=4.0,
            map_mode="pivot",
        )
        self._gamma_slider.setMinimumWidth(140)
        self._gamma_slider.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        strip.addWidget(self._gamma_slider, 1)

        strip.addSpacing(8)

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
        self._display_view_combo.setToolTip("OCIO display / view")
        strip.addWidget(self._display_view_combo, 0)

        parent_layout.addLayout(strip)

        self._display_view_pairs = []
        self._repopulate_display_views()

        self._gain_slider.valueChanged.connect(self._on_gain_changed)
        self._gamma_slider.valueChanged.connect(self._on_gamma_changed)
        self._display_view_combo.currentIndexChanged.connect(
            lambda _: self._invalidate_display_cache()
        )
        self._sync_gpu_view_settings()

    def _repopulate_display_views(self) -> None:
        self._display_view_combo.blockSignals(True)
        self._display_view_combo.clear()
        self._display_view_pairs.clear()

        if self._ocio_cfg is None:
            self._display_view_pairs.append(("sRGB", "Raw"))
            self._display_view_combo.addItem("sRGB")
            self._display_view_combo.blockSignals(False)
            return

        from ...core.ocio_utils import (
            default_display_view,
            list_displays,
            list_views,
            preferred_video_monitoring_view,
        )

        default_display, default_view = default_display_view(self._ocio_cfg)
        default_idx = 0

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

        # Soft prefer: ask the config (viewing rules / video encodings) for a
        # monitoring view. No hard-coded view names — if the config has none,
        # keep getDefaultView(display) from above.
        if self._prefer_video_monitoring and self._display_view_pairs:
            pref_d, pref_v = preferred_video_monitoring_view(self._ocio_cfg, default_display)
            if pref_v:
                for i, (d, v) in enumerate(self._display_view_pairs):
                    if v == pref_v and (not pref_d or d == pref_d):
                        default_idx = i
                        break

        if self._display_view_combo.count() > 0:
            self._display_view_combo.setCurrentIndex(default_idx)
        self._display_view_combo.blockSignals(False)

    def _exposure_stops(self) -> float:
        return math.log2(max(float(getattr(self, "_gain", 1.0)), 1e-10))

    def _on_gpu_failed(self, reason: str) -> None:
        log.error("Falling back to CPU OCIO preview: %s", reason)
        self._use_gpu = False
        self._preview_stack.setCurrentWidget(self._preview)
        self._apply_display_transform()

    def _sync_gpu_view_settings(self) -> None:
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
            self._gpu_plane.set_gamma(gamma)
            return
        if self._display_f32 is not None:
            self._paint_display_with_viewer_gamma(self._display_f32)
            return
        self._refresh_gain_gamma()

    # ------------------------------------------------------------------
    # Cache UI
    # ------------------------------------------------------------------

    def _build_cache_ui(self, parent_layout: QVBoxLayout) -> None:
        """Compact pause/clear + usage only. Budget % lives in Preferences."""
        muted = "color: #8a8a8a;"
        self._cache_host = QWidget()
        row = QHBoxLayout(self._cache_host)
        row.setContentsMargins(8, 2, 8, 2)
        row.setSpacing(6)

        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setFrameShadow(QFrame.Shadow.Plain)
        sep.setStyleSheet("color: #3a3a3a;")
        row.addWidget(sep)

        cache_lbl = QLabel("Cache")
        cache_lbl.setStyleSheet(muted)
        row.addWidget(cache_lbl)

        self._cache_bar = QProgressBar()
        self._cache_bar.setMaximum(1000)
        self._cache_bar.setFixedWidth(150)
        self._cache_bar.setFixedHeight(16)
        self._cache_bar.setTextVisible(True)
        self._cache_bar.setToolTip(
            "Playback cache memory in use (budget set under File → Preferences)"
        )
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

        row.addStretch(1)
        parent_layout.addWidget(self._cache_host)

        self._cache_pause_btn.toggled.connect(self._on_cache_pause_toggled)
        self._cache_clear_btn.clicked.connect(self._on_cache_clear)
        # Apply budget from Preferences (no in-player slider).
        self._shot_cache.budget_bytes = cache_budget_bytes(self._settings)
        self._update_cache_usage_bar()

    def _update_cache_usage_bar(self) -> None:
        if self._cache_bar is None:
            return
        used = self._shot_cache.current_bytes
        budget = self._shot_cache.budget_bytes
        if budget > 0:
            self._cache_bar.setValue(min(1000, int(used * 1000 / budget)))
        else:
            self._cache_bar.setValue(0)
        used_mb = used / (1024 * 1024)
        budget_mb = budget / (1024 * 1024)
        self._cache_bar.setFormat(f"{used_mb:.0f}/{budget_mb:.0f} MB")

    def _on_cache_pause_toggled(self, paused: bool) -> None:
        self._cache_paused = paused
        if self._cache_pause_btn is not None:
            self._cache_pause_btn.setText("\u25b6" if paused else "\u23f8")
        if self._prefetch is not None:
            self._prefetch.set_paused(paused)
        if not paused:
            self._sync_prefetch()

    def _on_cache_clear(self) -> None:
        self._shot_cache.clear()

    def _on_shot_cache_changed(self) -> None:
        self._timeline.set_cached_frames(self._shot_cache.cached_frames())
        self._update_cache_usage_bar()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        """Left/Right step one frame; Space play/pause when focused."""
        key = event.key()
        if key == Qt.Key.Key_Left:
            self._step_frame(-1)
            event.accept()
            return
        if key == Qt.Key.Key_Right:
            self._step_frame(1)
            event.accept()
            return
        if key == Qt.Key.Key_Space and not event.isAutoRepeat():
            self.set_playing(not self.is_playing())
            event.accept()
            return
        super().keyPressEvent(event)

    def _step_frame(self, delta: int) -> None:
        """Step playhead by *delta* frames (wraps at ends)."""
        if self.is_playing():
            self.set_playing(False)
        cur = self._timeline.value()
        first = self._timeline.first_frame
        last = self._timeline.last_frame
        if last < first:
            return
        span = last - first + 1
        nxt = first + ((cur - first + int(delta)) % span)
        self._goto_frame(nxt)

    # ------------------------------------------------------------------
    # Transport / frame routing
    # ------------------------------------------------------------------

    def _on_timeline_changed(self, frame: int) -> None:
        self._goto_frame(frame)

    def _on_shuttle_play_toggled(self, playing: bool) -> None:
        if not playing:
            self._playback_wait_frame = None
            self._shuttle.stop_timer()
        self._shot_cache.set_batch_mode(playing)
        self._sync_prefetch()
        self.playing_changed.emit(playing)
        if playing:
            self._playback_tick()

    def _next_playback_frame(self, frame: int) -> int:
        nxt = frame + 1
        if nxt > self._timeline.last_frame:
            nxt = self._timeline.first_frame
        return nxt

    def _needs_disk_cache(self, frame: int) -> bool:
        """True when *frame* is a media frame that must hit the RAM cache."""
        if self._hooks is not None and self._hooks.is_synthetic_frame(frame):
            return False
        return frame in self._shot_frames_set

    def _goto_frame(self, frame: int) -> None:
        frame = max(self._timeline.first_frame, min(frame, self._timeline.last_frame))
        if frame == self._current_frame:
            self._refresh_current_frame()
            return
        self._current_frame = frame
        self._playback_wait_frame = None
        self._timeline.set_value(frame)
        self._sync_prefetch()
        self.frame_changed.emit(frame)
        self._refresh_current_frame()

    def _sync_prefetch(self) -> None:
        if self._prefetch is not None and not self._cache_paused:
            # Prefetch context is disk-frame based; clamp to nearest shot if on slate.
            ctx = self._current_frame
            if self._hooks is not None and self._hooks.is_synthetic_frame(ctx):
                if self._first_shot is not None:
                    ctx = self._first_shot
            self._prefetch.set_context(ctx, playing=self.is_playing())

    def _playback_tick(self) -> None:
        if not self.is_playing():
            return

        cur = self._timeline.value()
        nxt = self._next_playback_frame(cur)

        if self._needs_disk_cache(nxt) and not self._shot_cache.contains(nxt):
            self._playback_wait_frame = nxt
            self._shuttle.stop_timer()
            if self._prefetch is not None:
                self._prefetch.request_immediate(nxt)
            return

        self._goto_frame(nxt)

    def _on_prefetch_frame_loaded(self, frame: int, rgb) -> None:
        if rgb is None:
            if frame == self._playback_wait_frame and self.is_playing():
                self._playback_wait_frame = None
                skip = self._next_playback_frame(frame)
                self._goto_frame(skip)
                if not self._shuttle.is_timer_active():
                    self._shuttle.start_timer()
            return

        if frame == self._current_frame:
            self._composite_shot_with_pixels(frame, rgb)

        if frame == self._playback_wait_frame and self.is_playing():
            self._goto_frame(frame)
            if not self._shuttle.is_timer_active():
                self._shuttle.start_timer()

        self._sync_prefetch()

    def _refresh_current_frame(self) -> None:
        frame = self._current_frame
        if self._hooks is not None and self._hooks.is_synthetic_frame(frame):
            self._composite_synthetic(frame)
        else:
            self._composite_shot(frame)

    def _composite_synthetic(self, frame: int) -> None:
        if self._hooks is None:
            return
        try:
            result = self._hooks.render_synthetic_frame(frame)
        except Exception:
            log.exception("Synthetic frame render failed (frame %s)", frame)
            return
        if result is None:
            return
        rgb, src_cs = result
        import numpy as np

        self._comp_f32 = np.ascontiguousarray(rgb, dtype=np.float32)
        self._comp_src_space = src_cs or ""
        self._working_f32 = None
        self._composed_working_f32 = None
        self._display_f32 = None
        self._apply_display_transform()

    def _composite_shot(self, frame: int) -> None:
        rgb = self._shot_cache.get(frame)
        if rgb is None:
            if self._prefetch is not None:
                self._prefetch.request_immediate(frame)
            return
        self._composite_shot_with_pixels(frame, rgb)

    def _composite_shot_with_pixels(self, frame: int, rgb) -> None:
        import numpy as np

        # Cache contract (slate + video browsers share this player):
        #   uint16  → source-referred, transform on GUI thread
        #   float*  → already working-space (worker src→working), display only
        if rgb.dtype == np.uint16:
            self._comp_f32 = rgb.astype(np.float32) / 65535.0
            self._comp_src_space = self._src_colorspace or ""
            self._working_f32 = None
            self._composed_working_f32 = None
        else:
            self._comp_f32 = None
            self._comp_src_space = ""
            self._working_f32 = rgb
            self._composed_working_f32 = None
        self._display_f32 = None

        # Adopt frame resolution from first cached image if host left defaults.
        try:
            h, w = int(rgb.shape[0]), int(rgb.shape[1])
            if h > 0 and w > 0 and (self._width, self._height) == (1920, 1080):
                # Only auto-adopt when still at constructor defaults and image differs.
                if (w, h) != (1920, 1080):
                    self.set_resolution(w, h)
        except Exception:
            pass

        self._apply_display_transform()

    # ------------------------------------------------------------------
    # OCIO working / display path
    # ------------------------------------------------------------------

    def _resolve_working_space(self) -> str:
        if self._ocio_cfg is None:
            return ""
        if self._working_space:
            return self._working_space
        try:
            from ...core.ocio_utils import get_compositing_space

            self._working_space = get_compositing_space(self._ocio_cfg)
        except Exception:
            log.exception("Could not resolve compositing space")
            self._working_space = ""
        return self._working_space

    def _build_worker_frame_transform(self):
        if self._ocio_cfg is None:
            return None
        src_space = self._src_colorspace or ""
        cpu = self._get_src_to_working_proc(src_space)
        if cpu is None:
            return None

        import PyOpenColorIO as OCIO

        def _transform(rgb_f32):
            import numpy as np

            buf = np.ascontiguousarray(rgb_f32, dtype=np.float32)
            h, w = buf.shape[:2]
            try:
                cpu.apply(OCIO.PackedImageDesc(buf, w, h, 3))
            except Exception:
                # Never cache untransformed pixels as float working-space —
                # that poisons display (video looks washed/crushed).
                log.exception("Worker src→working OCIO apply failed")
                return None
            return buf.astype(np.float16)

        return _transform

    def _get_src_to_working_proc(self, src_space: str):
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
            from ...core.ocio_utils import make_cpu_processor

            proc = make_cpu_processor(self._ocio_cfg, src_space, working_space)
        except Exception:
            log.exception(
                "Failed to build src→working processor (%s → %s)", src_space, working_space
            )
            proc = None
        self._ocio_proc_cache[key] = proc
        return proc

    def _get_working_to_display_proc(self, display: str, view: str):
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
            from ...core.ocio_utils import make_display_processor

            proc = make_display_processor(
                self._ocio_cfg, working_space, display, view, exposure=0.0, gamma=1.0
            )
        except Exception:
            proc = None
        self._ocio_proc_cache[key] = proc
        return proc

    def _ensure_viewer_display_proc(self, display: str, view: str) -> object | None:
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

        if self._viewer_display_proc is not None and getattr(
            self, "_last_viewer_display", None
        ) == (working_space, display, view):
            return self._viewer_display_proc

        try:
            from ...core.ocio_utils import make_viewer_display_processor

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

    def _is_shot_frame(self, frame: int) -> bool:
        if self._hooks is None:
            return True
        return not self._hooks.is_synthetic_frame(frame)

    def _apply_display_transform(self) -> None:
        import numpy as np

        working = self._build_working_f32()
        if working is None:
            return

        is_shot = self._is_shot_frame(self._current_frame)

        if self._use_gpu and self._gpu_plane is not None and self._gpu_plane.is_alive():
            h, w = int(working.shape[0]), int(working.shape[1])
            if is_shot and self._hooks is not None:
                ov, ov_key = self._hooks.gpu_overlay_rgba(w, h, self._current_frame)
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
        if is_shot and self._hooks is not None:
            composed = self._hooks.cpu_composite_overlays(working, self._current_frame)
        else:
            composed = working
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

        self._paint_display_with_viewer_gamma(self._display_f32)

    def _invalidate_display_cache(self) -> None:
        self._viewer_display_proc = None
        self._ec_exposure_prop = None
        self._ec_gamma_prop = None
        self._last_viewer_display = None
        self._display_f32 = None
        self._sync_gpu_view_settings()
        self._apply_display_transform()

    def _refresh_gain_gamma(self) -> None:
        if self._use_gpu and self._gpu_plane is not None:
            self._gpu_plane.set_exposure_stops(self._exposure_stops())
            self._gpu_plane.set_gamma(float(getattr(self, "_gamma", 1.0)))
            return

        if self._display_f32 is None:
            return

        out = self._display_f32
        if self._ec_exposure_prop is None:
            gain = float(getattr(self, "_gain", 1.0))
            if gain != 1.0:
                out = out * gain
        self._paint_display_with_viewer_gamma(out)

    def _paint_display_with_viewer_gamma(self, rgb_f32) -> None:
        import numpy as np

        gamma = float(getattr(self, "_gamma", 1.0))
        out = rgb_f32
        if abs(gamma - 1.0) > 1e-6:
            exp = nuke_viewer_gamma_power(gamma)
            pos = np.maximum(out, 0.0)
            powered = np.power(pos, exp)
            out = np.where(out >= 0.0, powered, out)
        self._paint_display_buffer(out)

    def _paint_display_buffer(self, rgb_f32) -> None:
        import numpy as np
        from PySide6.QtGui import QImage, QPixmap

        comp_u8 = np.ascontiguousarray((np.clip(rgb_f32, 0.0, 1.0) * 255 + 0.5).astype(np.uint8))

        fh, fw = comp_u8.shape[:2]
        bpl = (fw * 3 + 3) & ~3
        if bpl == fw * 3:
            buf = comp_u8
        else:
            buf = np.zeros((fh, bpl), dtype=np.uint8)
            flat = comp_u8.reshape(fh, -1)
            buf[:, : fw * 3] = flat
        qimg = QImage(buf.data, fw, fh, bpl, QImage.Format.Format_RGB888).copy()
        del buf
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


__all__ = ["OverlayHooks", "SequencePlayer"]
