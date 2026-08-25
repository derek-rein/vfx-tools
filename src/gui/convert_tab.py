from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    import fileseq

from PySide6.QtCore import (
    QObject,
    QPoint,
    QRegularExpression,
    QSettings,
    Qt,
    QThread,
    QTimer,
    QUrl,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QAction,
    QDesktopServices,
    QGuiApplication,
    QKeySequence,
    QRegularExpressionValidator,
    QStandardItemModel,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSlider,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core.constants import (
    CINEFORM_QUALITY_OPTIONS,
    DEFAULT_CINEFORM_QUALITY,
    DEFAULT_DST_E2V,
    DEFAULT_DST_V2E,
    DEFAULT_EXR_COMPRESSION,
    DEFAULT_FRAME_PADDING,
    DEFAULT_SCALE,
    DEFAULT_SRC_E2V,
    DEFAULT_SRC_V2E,
    DEFAULT_START_FRAME,
    DEFAULT_VIDEO_CODEC,
    EXR_COMPRESSIONS,
    SCALE_OPTIONS,
    X26X_PRESETS,
    available_video_codecs,
    available_video_codecs_grouped,
    is_image_sequence_ext,
    video_codec_by_key,
)
from ..core.convert import default_codec_opts
from ..core.framerange import format_frame_range
from ..core.ocio_utils import find_equivalent_space, resolve_alias
from ..core.sequence import (
    find_exr_sequence_info,
    looks_like_sequence_pattern,
    parse_dot_sequence_output,
    probe_exr_colorspace,
    probe_pixel_colorspace,
    sequence_looks_scene_referred,
)
from ..core.video import is_ignored_media_filename, probe_video, resolve_video_src_colorspace
from ..services.slate_model import SlateModel
from .browser_chrome import _VIDEO_EXTS, _add_copy_path_actions, _folder_path_for_copy
from .browser_path import clean_path_string
from .browser_state import BrowserPreviewContext
from .color_widgets import ColorSpaceButton, FpsCombo
from .preferences import file_manager_label, path_is_revealable
from .sequence_browser import SequenceBrowserDialog
from .style import DESC_STYLE, HINT_STYLE
from .video_browser import VideoBrowserDialog

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# EXR compression settings dialog
# ---------------------------------------------------------------------------

_EXR_HAS_SETTINGS = {"dwaa", "dwab", "zip", "zips"}
# Profile is selected via the codec dropdown itself for ProRes / DNxHR ladders.
_CODEC_HAS_SETTINGS = {
    "h264",
    "hevc",
    "hevc_8",
    "hevc_12",
    "cineform",
    "cineform_rgb",
}

_EXR_COMPRESSION_HELP: dict[str, str] = {
    "none": "No compression. Fastest write, largest files.",
    "rle": "Run-length encoding. Fast, good for flat areas.",
    "zip": "Zip per scanline block (16 rows). Good general-purpose.",
    "zips": "Zip per scanline. Slightly smaller than ZIP.",
    "piz": "Wavelet-based. Best lossless ratio for noisy/CG images.",
    "pxr24": "Lossy 24-bit float. Good ratio, slight precision loss.",
    "b44": "Lossy fixed-rate. Constant block size, fast random access.",
    "b44a": "Like B44 but flat areas compress further.",
    "dwaa": "Lossy DCT-based, per-scanline. Best lossy ratio at low levels.",
    "dwab": "Lossy DCT-based, per-tile (256 scanlines). Slightly better ratio than DWAA.",
}


class ExrCompressionSettingsDialog(QDialog):
    """Show settings relevant to the selected EXR compression method."""

    def __init__(
        self,
        compression: str,
        settings: QSettings,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._compression = compression
        self._settings = settings
        self.setWindowTitle(f"EXR Compression Settings — {compression.upper()}")
        self.setMinimumWidth(420)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        desc = _EXR_COMPRESSION_HELP.get(compression, "")
        if desc:
            lbl = QLabel(desc)
            lbl.setWordWrap(True)
            lbl.setStyleSheet(DESC_STYLE)
            layout.addWidget(lbl)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self._dwa_spin: QSpinBox | None = None
        self._zip_spin: QSpinBox | None = None

        if compression in ("dwaa", "dwab"):
            saved = int(float(settings.value("exr_opts/dwa_level", 45)))
            row = QHBoxLayout()
            row.setSpacing(6)
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(0, 250)
            slider.setValue(saved)
            self._dwa_spin = QSpinBox()
            self._dwa_spin.setRange(0, 250)
            self._dwa_spin.setValue(saved)
            self._dwa_spin.setMinimumWidth(72)
            self._dwa_spin.setFixedWidth(72)
            self._dwa_spin.setAlignment(Qt.AlignmentFlag.AlignRight)
            self._dwa_spin.setToolTip(
                "0 = lossless, 45 = visually lossless (default), higher = more compression"
            )
            slider.valueChanged.connect(self._dwa_spin.setValue)
            self._dwa_spin.valueChanged.connect(slider.setValue)
            row.addWidget(slider, 1)
            row.addWidget(self._dwa_spin)
            hint = QLabel("0 = lossless · 45 = visually lossless (default) · 100+ = aggressive")
            hint.setStyleSheet(HINT_STYLE)
            form.addRow("Compression level", row)
            form.addRow("", hint)
        elif compression in ("zip", "zips"):
            saved = int(settings.value("exr_opts/zip_level", 4))
            row = QHBoxLayout()
            row.setSpacing(6)
            slider = QSlider(Qt.Orientation.Horizontal)
            slider.setRange(1, 9)
            slider.setValue(saved)
            self._zip_spin = QSpinBox()
            self._zip_spin.setRange(1, 9)
            self._zip_spin.setValue(saved)
            self._zip_spin.setMinimumWidth(72)
            self._zip_spin.setFixedWidth(72)
            self._zip_spin.setAlignment(Qt.AlignmentFlag.AlignRight)
            self._zip_spin.setToolTip("1 = fastest, 9 = best compression")
            slider.valueChanged.connect(self._zip_spin.setValue)
            self._zip_spin.valueChanged.connect(slider.setValue)
            row.addWidget(slider, 1)
            row.addWidget(self._zip_spin)
            form.addRow("Zip level", row)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_settings(self) -> dict[str, str]:
        """Return dict of option key -> value strings."""
        result: dict[str, str] = {}
        if self._dwa_spin is not None:
            result["dwa_compression_level"] = str(self._dwa_spin.value())
        if self._zip_spin is not None:
            result["zip_level"] = str(self._zip_spin.value())
        return result

    def accept(self) -> None:
        if self._dwa_spin is not None:
            self._settings.setValue("exr_opts/dwa_level", float(self._dwa_spin.value()))
        if self._zip_spin is not None:
            self._settings.setValue("exr_opts/zip_level", self._zip_spin.value())
        super().accept()


# ---------------------------------------------------------------------------
# Video codec settings dialog
# ---------------------------------------------------------------------------

_CODEC_HELP: dict[str, str] = {
    "prores_proxy": "ProRes 422 Proxy — lightest ProRes; 10-bit 4:2:2 offline/proxy (prores_ks).",
    "prores_lt": "ProRes 422 LT — lighter intermediate; 10-bit 4:2:2 (prores_ks).",
    "prores_422": "ProRes 422 (Standard) — balanced intermediate; 10-bit 4:2:2 (prores_ks).",
    "prores": "ProRes 422 HQ — default finishing intermediate; 10-bit 4:2:2 (prores_ks).",
    "prores_4444": (
        "ProRes 4444 — 4:4:4:4 with alpha (prores_ks). FFmpeg software encodes at "
        "10-bit only (yuva444p10le); Apple's format can be 12-bit, but prores_ks "
        "does not. Prefer VideoToolbox 4444 on macOS for ~12-bit precision."
    ),
    "prores_xq": (
        "ProRes 4444 XQ — highest ProRes tier, 4:4:4:4 with alpha (prores_ks). "
        "Software encode is still 10-bit only; use prores_vt_xq on macOS for "
        "true 12-bit-class precision."
    ),
    "prores_vt_proxy": (
        "Hardware ProRes Proxy via Apple VideoToolbox. macOS only; faster, "
        "quality/controls differ slightly from software prores_ks. 10-bit 4:2:2."
    ),
    "prores_vt_lt": "Hardware ProRes LT via VideoToolbox (macOS only); 10-bit 4:2:2.",
    "prores_vt_422": "Hardware ProRes 422 via VideoToolbox (macOS only); 10-bit 4:2:2.",
    "prores_vt_hq": "Hardware ProRes HQ via VideoToolbox (macOS only); 10-bit 4:2:2.",
    "prores_vt_4444": (
        "Hardware ProRes 4444 via VideoToolbox (macOS only). ~12-bit class with "
        "ayuv64le intermediate — better precision than software prores_ks 4444."
    ),
    "prores_vt_xq": (
        "Hardware ProRes 4444 XQ via VideoToolbox (macOS only). ~12-bit class "
        "precision; preferred over software XQ when bit depth matters."
    ),
    "prores_ox_4444": (
        "Experimental cross-platform true 12-bit ProRes 4444-compatible encode "
        "(SMPTE RDD 36 via oxideav-prores PyO3 bindings). Not Apple-certified. "
        "Requires the built-in exr_prores extension (make oxideav-prores)."
    ),
    "prores_ox_xq": (
        "Experimental cross-platform true 12-bit ProRes 4444 XQ-compatible encode "
        "(SMPTE RDD 36 via oxideav-prores). Not Apple-certified. Requires exr_prores."
    ),
    "cineform": (
        "GoPro CineForm (cfhd) — 10-bit 4:2:2 wavelet intermediate. "
        "Quality ladder film3+…low in settings."
    ),
    "cineform_rgb": ("GoPro CineForm RGB — true 12-bit planar RGB (gbrp12le) for RGB pipelines."),
    "dnxhr_lb": "DNxHR LB — lightest Avid/Resolve profile; 8-bit 4:2:2.",
    "dnxhr_sq": "DNxHR SQ — standard quality; 8-bit 4:2:2.",
    "dnxhr_hq": "DNxHR HQ — high quality intermediate; 8-bit 4:2:2 (not 10-bit).",
    "dnxhr_hqx": "DNxHR HQX — 10-bit 4:2:2; use when 10-bit headroom is required.",
    "dnxhr_444": "DNxHR 444 — 10-bit 4:4:4 for highest chroma fidelity in DNx family.",
    "h264": ("H.264 / AVC — 8-bit 4:2:0 delivery/review. Not a grading intermediate."),
    "hevc": (
        "H.265 / HEVC — 10-bit 4:2:0 delivery (libx265 Main 10). Smaller than ProRes; "
        "not a scene-linear intermediate."
    ),
    "hevc_12": (
        "H.265 / HEVC — true 12-bit 4:2:0 (libx265 Main 12, yuv420p12le). "
        "Playback support is rarer than Main 10."
    ),
    "hevc_8": "H.265 / HEVC — 8-bit 4:2:0 delivery for maximum player compatibility.",
    "ffv1": "FFV1 — mathematically lossless 10-bit 4:4:4 archival intermediate.",
    "ffv1_12": (
        "FFV1 — mathematically lossless 12-bit 4:4:4 (yuv444p12le). "
        "Best open archival option when you need true 12-bit YUV."
    ),
}


class VideoCodecSettingsDialog(QDialog):
    """Show settings relevant to the selected video codec."""

    def __init__(
        self,
        codec_key: str,
        settings: QSettings,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._codec_key = codec_key
        self._settings = settings

        spec = video_codec_by_key(codec_key)
        display = spec.display_name if spec else codec_key
        bit_note = spec.format_label if spec else ""
        self.setWindowTitle(f"Codec Settings — {display}")
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        desc = _CODEC_HELP.get(codec_key, "")
        if desc:
            lbl = QLabel(desc)
            lbl.setWordWrap(True)
            lbl.setStyleSheet(DESC_STYLE)
            layout.addWidget(lbl)
        if bit_note:
            bit_lbl = QLabel(f"<b>Encode format:</b> {bit_note}")
            bit_lbl.setWordWrap(True)
            bit_lbl.setStyleSheet(HINT_STYLE)
            layout.addWidget(bit_lbl)
        if spec and spec.platforms:
            plat = QLabel(
                f"<i>Platform: {', '.join(spec.platforms)} only (hidden on other OSes).</i>"
            )
            plat.setWordWrap(True)
            plat.setStyleSheet(HINT_STYLE)
            layout.addWidget(plat)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self._crf_spin: QSpinBox | None = None
        self._preset: QComboBox | None = None
        self._cineform_quality: QComboBox | None = None
        self._settings_prefix = "h264"
        if codec_key in ("hevc", "hevc_8", "hevc_12"):
            self._settings_prefix = "hevc"

        if codec_key in ("h264", "hevc", "hevc_8", "hevc_12"):
            crf_key = f"codec_opts/{self._settings_prefix}_crf"
            preset_key = f"codec_opts/{self._settings_prefix}_preset"
            saved_crf = int(settings.value(crf_key, 18))
            crf_row = QHBoxLayout()
            crf_row.setSpacing(6)
            crf_slider = QSlider(Qt.Orientation.Horizontal)
            crf_slider.setRange(0, 51)
            crf_slider.setValue(saved_crf)
            self._crf_spin = QSpinBox()
            self._crf_spin.setRange(0, 51)
            self._crf_spin.setValue(saved_crf)
            self._crf_spin.setMinimumWidth(72)
            self._crf_spin.setFixedWidth(72)
            self._crf_spin.setAlignment(Qt.AlignmentFlag.AlignRight)
            self._crf_spin.setToolTip(
                "0 = lossless, 18 = visually lossless, 23 = default, 51 = worst quality"
            )
            crf_slider.valueChanged.connect(self._crf_spin.setValue)
            self._crf_spin.valueChanged.connect(crf_slider.setValue)
            crf_row.addWidget(crf_slider, 1)
            crf_row.addWidget(self._crf_spin)
            form.addRow("CRF (quality)", crf_row)

            self._preset = QComboBox()
            for p in X26X_PRESETS:
                self._preset.addItem(p, p)
            saved_preset = settings.value(preset_key, "medium")
            idx = X26X_PRESETS.index(saved_preset) if saved_preset in X26X_PRESETS else 5
            self._preset.setCurrentIndex(idx)
            self._preset.setToolTip("Slower = better compression at same quality")
            form.addRow("Preset", self._preset)

        elif codec_key in ("cineform", "cineform_rgb"):
            self._cineform_quality = QComboBox()
            for val, label in CINEFORM_QUALITY_OPTIONS:
                self._cineform_quality.addItem(label, val)
            saved_q = str(settings.value("codec_opts/cineform_quality", DEFAULT_CINEFORM_QUALITY))
            for i in range(self._cineform_quality.count()):
                if self._cineform_quality.itemData(i) == saved_q:
                    self._cineform_quality.setCurrentIndex(i)
                    break
            self._cineform_quality.setToolTip(
                "FFmpeg cfhd quality: film3+/film3 ≈ least compression; low ≈ smallest"
            )
            form.addRow("Quality", self._cineform_quality)

        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_settings(self) -> dict[str, str]:
        """Return dict of option key -> value strings for PyAV stream.options."""
        result: dict[str, str] = {}
        if self._crf_spin is not None:
            result["crf"] = str(self._crf_spin.value())
        if self._preset is not None:
            result["preset"] = self._preset.currentData() or "medium"
        if self._cineform_quality is not None:
            result["quality"] = self._cineform_quality.currentData() or "film3"
        return result

    def accept(self) -> None:
        if self._codec_key in ("h264", "hevc", "hevc_8", "hevc_12"):
            if self._crf_spin:
                self._settings.setValue(
                    f"codec_opts/{self._settings_prefix}_crf",
                    self._crf_spin.value(),
                )
            if self._preset:
                self._settings.setValue(
                    f"codec_opts/{self._settings_prefix}_preset",
                    self._preset.currentData(),
                )
        elif self._codec_key in ("cineform", "cineform_rgb") and self._cineform_quality:
            self._settings.setValue(
                "codec_opts/cineform_quality",
                self._cineform_quality.currentData(),
            )
        super().accept()


# ---------------------------------------------------------------------------
# Conversion tab
# ---------------------------------------------------------------------------


class VideoInput(NamedTuple):
    """Validated video probe result — the *model* behind the video input field."""

    path: str
    width: int
    height: int
    fps: float
    frame_count: int


class _InputProbeWorker(QObject):
    """Background probe for video / EXR inputs (avoids freezing the GUI thread)."""

    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, mode: str, path: str) -> None:
        super().__init__()
        self._mode = mode
        self._path = path

    @Slot()
    def run(self) -> None:
        path = self._path
        try:
            if self._mode == "video2exr":
                w, h, fps, total = probe_video(path)
                self.finished.emit(
                    {
                        "kind": "video",
                        "path": path,
                        "w": w,
                        "h": h,
                        "fps": fps,
                        "total": total,
                    }
                )
            else:
                _paths, _name, frame_nums, _pad, seq = find_exr_sequence_info(path)
                self.finished.emit(
                    {
                        "kind": "exr",
                        "path": path,
                        "frame_nums": frame_nums,
                        "seq": seq,
                    }
                )
        except Exception as e:
            self.failed.emit(str(e))


def _set_codec_combo_header_row(combo: QComboBox, index: int) -> None:
    """Non-selectable bold group label row in the codec picker."""
    model = combo.model()
    if not isinstance(model, QStandardItemModel):
        return
    item = model.item(index)
    if item is None:
        return
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled & ~Qt.ItemFlag.ItemIsSelectable)
    font = item.font()
    font.setBold(True)
    item.setFont(font)


def _populate_video_codec_combo(combo: QComboBox) -> None:
    """Fill *combo* with OS-filtered codecs grouped by family."""
    combo.clear()
    first_group = True
    for group_label, specs in available_video_codecs_grouped():
        if not first_group:
            combo.insertSeparator(combo.count())
        first_group = False

        combo.addItem(group_label, None)
        _set_codec_combo_header_row(combo, combo.count() - 1)

        for spec in specs:
            tip = spec.format_label
            if spec.platforms:
                tip += f" · {', '.join(spec.platforms)} only"
            combo.addItem(spec.display_name, spec.key)
            idx = combo.count() - 1
            combo.setItemData(idx, tip, Qt.ItemDataRole.ToolTipRole)


def _select_video_codec_combo_key(
    combo: QComboBox,
    key: str,
    *,
    fallback: str = DEFAULT_VIDEO_CODEC,
) -> None:
    for codec_key in (key, fallback):
        for i in range(combo.count()):
            if combo.itemData(i) == codec_key:
                combo.setCurrentIndex(i)
                return


class ConvertTab(QWidget):
    log_message = Signal(str)
    readiness_changed = Signal(bool)

    def __init__(self, mode: str, settings: QSettings, parent: QWidget | None = None):
        super().__init__(parent)
        self._mode = mode
        self._settings = settings
        self._ocio_cfg: object | None = None

        # Input model objects — the source of truth for what is loaded.
        # The QLineEdit is a *view* on these; is_ready() gates on them.
        self._input_seq: fileseq.FileSequence | None = None
        self._video_info: VideoInput | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # -- Input + src colorspace inline --
        in_group = QGroupBox("Input")
        in_main = QVBoxLayout(in_group)
        in_main.setSpacing(4)

        in_row = QHBoxLayout()
        self.input_path = QLineEdit()
        self.input_path.setPlaceholderText(
            "Video file (mp4, mov, mkv, \u2026)"
            if mode == "video2exr"
            else "Folder with frames, or any frame (EXR / PNG / JPG / \u2026)"
        )
        saved_in = str(settings.value(f"{mode}/input", "") or "").strip()
        if saved_in:
            self.input_path.setText(saved_in)
        self._browse_in = QPushButton("Browse\u2026")
        self._browse_in.setToolTip(self._browse_button_tooltip())
        in_row.addWidget(self.input_path, 1)
        in_row.addWidget(self._browse_in)
        in_main.addLayout(in_row)

        cs_in_row = QHBoxLayout()
        cs_in_row.addWidget(QLabel("Color space:"))
        self.src_btn = ColorSpaceButton()
        cs_in_row.addWidget(self.src_btn, 1)
        in_main.addLayout(cs_in_row)

        range_row = QHBoxLayout()
        range_row.addWidget(QLabel("Frames:"))
        self._frame_range_edit = QLineEdit()
        self._frame_range_edit.setPlaceholderText("e.g. 1001-1100, 1-50x2")
        self._frame_range_edit.setToolTip(
            "Nuke-style frame range.\nExamples: 1-100, 1-10x2, 1-4 8-10"
        )
        self._frame_range_edit.setValidator(
            QRegularExpressionValidator(
                QRegularExpression(r"(\d+(-\d+)?(x\d+)?([, ] *)?)*"),
                self._frame_range_edit,
            )
        )
        range_row.addWidget(self._frame_range_edit, 1)

        self._reset_range_btn = QToolButton()
        self._reset_range_btn.setText("\u21ba")
        self._reset_range_btn.setToolTip("Reset to source range")
        self._reset_range_btn.setEnabled(False)
        self._reset_range_btn.clicked.connect(self._reset_to_source_range)
        range_row.addWidget(self._reset_range_btn)

        in_main.addLayout(range_row)
        self._full_input_range = ""

        layout.addWidget(in_group)

        # -- Output + dst colorspace inline --
        out_group = QGroupBox("Output")
        out_main = QVBoxLayout(out_group)
        out_main.setSpacing(4)

        out_row = QHBoxLayout()
        self.output_path = QLineEdit()
        self.output_path.setPlaceholderText(
            f"Output directory for EXR sequence (name.{'#' * DEFAULT_FRAME_PADDING}.exr)"
            if mode == "video2exr"
            else "Output video file (mp4, mov, \u2026)"
        )
        saved_out = settings.value(f"{mode}/output", "")
        if saved_out:
            self.output_path.setText(saved_out)
        self._browse_out = QPushButton("Browse\u2026")
        self._browse_out.setToolTip(self._browse_button_tooltip())
        out_row.addWidget(self.output_path, 1)
        out_row.addWidget(self._browse_out)
        out_main.addLayout(out_row)

        cs_out_row = QHBoxLayout()
        cs_out_row.addWidget(QLabel("Color space:"))
        self.dst_btn = ColorSpaceButton()
        cs_out_row.addWidget(self.dst_btn, 1)
        out_main.addLayout(cs_out_row)

        layout.addWidget(out_group)

        # -- Options row: scale + mode-specific in one group --
        opts_group = QGroupBox("Options")
        opts_layout = QFormLayout(opts_group)
        opts_layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)

        self.scale_combo = QComboBox()
        for scale_val, scale_label in SCALE_OPTIONS:
            self.scale_combo.addItem(scale_label, scale_val)
        saved_scale = float(settings.value(f"{mode}/scale", DEFAULT_SCALE))
        for i in range(self.scale_combo.count()):
            if abs(self.scale_combo.itemData(i) - saved_scale) < 0.01:
                self.scale_combo.setCurrentIndex(i)
                break
        self.scale_combo.currentIndexChanged.connect(
            lambda _: self._settings.setValue(f"{self._mode}/scale", self.scale_combo.currentData())
        )

        scale_row = QHBoxLayout()
        scale_row.setSpacing(12)
        scale_row.addWidget(self.scale_combo, 1)

        if mode == "exr2video":
            self._slate_model = SlateModel(settings, mode, parent=self)

            self._slate_check = QCheckBox("Slate")
            self._slate_check.setToolTip("Prepend a 1-frame slate image before the video")
            self._slate_check.setChecked(self._slate_model.slate_enabled)
            scale_row.addWidget(self._slate_check)

            self._burnin_check = QCheckBox("Burn-in")
            self._burnin_check.setToolTip("Overlay semi-transparent text on every frame")
            self._burnin_check.setChecked(self._slate_model.burnin_enabled)
            scale_row.addWidget(self._burnin_check)

            self._watermark_check = QCheckBox("Watermark")
            self._watermark_check.setToolTip("Add a diagonal watermark across every frame")
            self._watermark_check.setChecked(self._slate_model.watermark_enabled)
            scale_row.addWidget(self._watermark_check)

            scale_row.addStretch()

            self._edit_slate_btn = QToolButton()
            self._edit_slate_btn.setText("\u270e")
            self._edit_slate_btn.setToolTip("Edit Slate & Overlay\u2026")
            self._edit_slate_btn.setAutoRaise(True)
            self._edit_slate_btn.setFixedWidth(28)
            self._edit_slate_btn.clicked.connect(self._open_slate_dialog)
            scale_row.addWidget(self._edit_slate_btn)
        else:
            self._slate_model = None
            self._slate_check = None
            self._burnin_check = None
            self._watermark_check = None

        opts_layout.addRow("Scale", scale_row)

        if mode == "video2exr":
            self.compression_combo = QComboBox()
            for c in EXR_COMPRESSIONS:
                self.compression_combo.addItem(c.upper(), c)
            saved_comp = settings.value(f"{mode}/exr_compression", DEFAULT_EXR_COMPRESSION)
            idx = EXR_COMPRESSIONS.index(saved_comp) if saved_comp in EXR_COMPRESSIONS else 0
            self.compression_combo.setCurrentIndex(idx)
            self.compression_combo.currentIndexChanged.connect(
                lambda _: self._settings.setValue(
                    f"{self._mode}/exr_compression",
                    self.compression_combo.currentData(),
                )
            )
            comp_row = QHBoxLayout()
            comp_row.setSpacing(4)
            comp_row.addWidget(self.compression_combo, 1)
            self._comp_settings_btn = QToolButton()
            self._comp_settings_btn.setText("\u2699")
            self._comp_settings_btn.setAutoRaise(True)
            self._comp_settings_btn.setFixedWidth(28)
            self._comp_settings_btn.setToolTip("Compression settings\u2026")
            self._comp_settings_btn.clicked.connect(self._open_compression_settings)
            self._update_comp_btn_state()
            self.compression_combo.currentIndexChanged.connect(
                lambda _: self._update_comp_btn_state()
            )
            comp_row.addWidget(self._comp_settings_btn)
            opts_layout.addRow("EXR Compression", comp_row)

            self.padding_spin = QSpinBox()
            self.padding_spin.setRange(1, 8)
            self.padding_spin.setValue(
                int(settings.value(f"{mode}/padding", DEFAULT_FRAME_PADDING))
            )
            self.padding_spin.setToolTip("Number of # digits in the frame number (e.g. #### = 4)")
            self.padding_spin.valueChanged.connect(
                lambda v: self._settings.setValue(f"{self._mode}/padding", v)
            )
            self.padding_spin.valueChanged.connect(lambda _: self._update_output_placeholder())

            self.start_frame_spin = QSpinBox()
            self.start_frame_spin.setRange(0, 999999)
            self.start_frame_spin.setValue(
                int(settings.value(f"{mode}/start_frame", DEFAULT_START_FRAME))
            )
            self.start_frame_spin.setToolTip("First frame number in the output sequence")
            self.start_frame_spin.valueChanged.connect(
                lambda v: self._settings.setValue(f"{self._mode}/start_frame", v)
            )

            frame_row = QHBoxLayout()
            frame_row.setSpacing(8)
            frame_row.addWidget(QLabel("Padding"))
            frame_row.addWidget(self.padding_spin)
            frame_row.addSpacing(12)
            frame_row.addWidget(QLabel("Start frame"))
            frame_row.addWidget(self.start_frame_spin)
            frame_row.addStretch()
            opts_layout.addRow("Frame numbering", frame_row)

            self.fps_widget = None
            self.codec_combo = None
        elif mode == "exr2video":
            self.compression_combo = None
            self.padding_spin = None
            self.start_frame_spin = None
            self.fps_widget = FpsCombo(settings, f"{mode}/fps")
            opts_layout.addRow("Frame rate", self.fps_widget)

            self.codec_combo = QComboBox()
            _populate_video_codec_combo(self.codec_combo)
            saved_codec = settings.value(f"{mode}/video_codec", DEFAULT_VIDEO_CODEC)
            _select_video_codec_combo_key(self.codec_combo, str(saved_codec))
            self.codec_combo.currentIndexChanged.connect(
                lambda _: self._settings.setValue(
                    f"{self._mode}/video_codec",
                    self.codec_combo.currentData(),
                )
            )
            codec_row = QHBoxLayout()
            codec_row.setSpacing(4)
            codec_row.addWidget(self.codec_combo, 1)
            self._codec_settings_btn = QToolButton()
            self._codec_settings_btn.setText("\u2699")
            self._codec_settings_btn.setAutoRaise(True)
            self._codec_settings_btn.setFixedWidth(28)
            self._codec_settings_btn.setToolTip("Codec settings\u2026")
            self._codec_settings_btn.clicked.connect(self._open_codec_settings)
            self._update_codec_btn_state()
            self.codec_combo.currentIndexChanged.connect(lambda _: self._update_codec_btn_state())
            self.codec_combo.currentIndexChanged.connect(lambda _: self._update_output_ext())
            self.codec_combo.currentIndexChanged.connect(lambda _: self._update_dst_for_codec())
            codec_row.addWidget(self._codec_settings_btn)
            opts_layout.addRow("Codec", codec_row)
        else:
            self.compression_combo = None
            self.padding_spin = None
            self.start_frame_spin = None
            self.fps_widget = None
            self.codec_combo = None

        layout.addWidget(opts_group)

        # Two-way binding between the master checkboxes and the model.
        # The lambdas push view → model; ``_on_model_changed`` reflects
        # model → view so external writers (the slate dialog, settings
        # restore, scripts) stay in sync.
        if self._slate_check is not None:
            self._slate_check.toggled.connect(self._slate_model.set_slate_enabled)
        if self._burnin_check is not None:
            self._burnin_check.toggled.connect(self._slate_model.set_burnin_enabled)
        if self._watermark_check is not None:
            self._watermark_check.toggled.connect(self._slate_model.set_watermark_enabled)
        if self._slate_model is not None:
            self._slate_model.changed.connect(self._on_slate_model_changed)

        layout.addStretch()

        # -- Tab order --
        tab_chain = [
            self.input_path,
            self._browse_in,
            self.src_btn,
            self._frame_range_edit,
            self._reset_range_btn,
            self.output_path,
            self._browse_out,
            self.dst_btn,
            self.scale_combo,
        ]
        if mode == "video2exr":
            tab_chain += [
                self.compression_combo,
                self._comp_settings_btn,
                self.padding_spin,
                self.start_frame_spin,
            ]
        elif mode == "exr2video":
            if self.fps_widget:
                tab_chain.append(self.fps_widget)
            tab_chain += [self.codec_combo, self._codec_settings_btn]
        for i in range(len(tab_chain) - 1):
            if tab_chain[i] and tab_chain[i + 1]:
                self.setTabOrder(tab_chain[i], tab_chain[i + 1])

        # -- Connections --
        self._browse_in.clicked.connect(lambda: self._on_browse_clicked(is_input=True))
        self._browse_out.clicked.connect(lambda: self._on_browse_clicked(is_input=False))
        self._install_path_field_menus()
        # Input settings are persisted by set_input(); no textChanged hook needed.
        self.output_path.textChanged.connect(
            lambda t: self._settings.setValue(f"{self._mode}/output", t)
        )
        self.src_btn.space_changed.connect(
            lambda n: self._settings.setValue(f"{self._mode}/src_space", n)
        )
        self.dst_btn.space_changed.connect(
            lambda n: self._settings.setValue(f"{self._mode}/dst_space", n)
        )

        self.input_path.textChanged.connect(self._on_input_text_changed)
        self.output_path.textChanged.connect(lambda _: self._emit_readiness())
        self.src_btn.space_changed.connect(lambda _: self._emit_readiness())
        self.dst_btn.space_changed.connect(lambda _: self._emit_readiness())

        # Validate any saved input path once the event loop starts — unless a
        # GUI launch path (--open / Nuke) already applied input on this tab.
        # The QLineEdit is only a view; readiness gates on _input_seq / _video_info
        # after a successful probe, so we must re-accept on every cold start.
        self._skip_saved_input_restore = False
        saved = self.input_path.text().strip()
        if saved:
            QTimer.singleShot(0, self, self._restore_saved_input_if_needed)

    def _restore_saved_input_if_needed(self) -> None:
        if getattr(self, "_skip_saved_input_restore", False):
            return
        saved = str(self.input_path.text() or "").strip()
        if not saved:
            return
        # Already accepted (e.g. browse completed before the deferred slot).
        if self._path_matches_current_input(saved) and self.is_ready():
            return
        self.log_message.emit("Restoring saved input…")
        self.set_input_async(saved)

    def suppress_saved_input_restore(self) -> None:
        """Cancel deferred QSettings input restore (used by ``--open`` / Nuke)."""
        self._skip_saved_input_restore = True

    def _path_matches_current_input(self, text: str) -> bool:
        """True if *text* is the same source as the validated model (path or #### view)."""
        text = (text or "").strip()
        if not text:
            return False
        if self._video_info is not None:
            return text == self._video_info.path
        if self._input_seq is not None:
            dirn = self._input_seq.dirname().rstrip("/\\")
            if text.rstrip("/\\") == dirn:
                return True
            pad = "#" * max(1, self._input_seq.zfill())
            display = (
                f"{self._input_seq.dirname()}{self._input_seq.basename()}"
                f"{pad}{self._input_seq.extension()}"
            )
            return text == display
        return False

    def _persist_input_path(self, path: str) -> None:
        """Write input path to QSettings and flush (survives kill / crash better)."""
        self._settings.setValue(f"{self._mode}/input", path)
        self._settings.sync()

    def _on_input_text_changed(self, text: str) -> None:
        """React to manual edits (typing / paste) in the input field.

        Does **not** clear a validated model when the field still shows the
        same source (real path *or* the Nuke-style ``####`` display string
        written by :meth:`set_input`).  Clearing first used to drop a loaded
        sequence the moment anything re-emitted ``textChanged`` with the
        display pattern, which is not a filesystem path.
        """
        text = clean_path_string(text)
        if not text:
            self._video_info = None
            self._input_seq = None
            self._full_input_range = ""
            self._frame_range_edit.clear()
            self._reset_range_btn.setEnabled(False)
            self._persist_input_path("")
            self._emit_readiness()
            return

        if self._path_matches_current_input(text):
            self._emit_readiness()
            return

        # Text no longer matches the model — drop model until re-validated.
        self._video_info = None
        self._input_seq = None

        p = Path(text).expanduser()
        if self._mode == "video2exr":
            if (
                p.is_file()
                and p.suffix.lower() in _VIDEO_EXTS
                and not is_ignored_media_filename(p.name)
            ):
                self.set_input(str(p))
                return
        else:
            if (
                p.is_dir()
                or (
                    p.is_file()
                    and is_image_sequence_ext(p.suffix)
                    and not is_ignored_media_filename(p.name)
                )
                or looks_like_sequence_pattern(text)
            ):
                # Nuke-style ``name.####.exr`` paste: resolve range + color space
                # the same way as Browse / Open.
                self.set_input(text)
                return

        self._full_input_range = ""
        self._frame_range_edit.clear()
        self._reset_range_btn.setEnabled(False)
        self._emit_readiness()

    def _emit_readiness(self) -> None:
        self.readiness_changed.emit(self.is_ready())

    def is_ready(self) -> bool:
        """True when all required fields are populated with validated inputs."""
        if self._mode == "video2exr" and self._video_info is None:
            return False
        if self._mode == "exr2video" and self._input_seq is None:
            return False
        if not self.output_path.text().strip():
            return False
        if not self.src_btn.is_valid():
            return False
        if not self.dst_btn.is_valid():
            return False
        return True

    def populate_spaces(
        self,
        families: dict[str, list[str]],
        ocio_cfg: object | None = None,
    ) -> None:
        """Rebuild color-space menus for a (possibly new) OCIO config.

        Keeps the current selection when possible, remaps via
        :func:`find_equivalent_space`, or marks the button invalid so Convert
        stays disabled until the user picks a space that exists in *ocio_cfg*.
        """
        self._ocio_cfg = ocio_cfg
        if self._mode == "video2exr":
            default_src, default_dst = DEFAULT_SRC_V2E, DEFAULT_DST_V2E
        else:
            default_src, default_dst = DEFAULT_SRC_E2V, DEFAULT_DST_E2V

        # Prefer the live selection (including an invalid “wanted” name) so a
        # config switch remaps what the user was using; fall back to settings.
        wanted_src = (
            self.src_btn.displayed_space()
            or self._settings.value(f"{self._mode}/src_space", default_src)
            or default_src
        )
        wanted_dst = (
            self.dst_btn.displayed_space()
            or self._settings.value(f"{self._mode}/dst_space", default_dst)
            or default_dst
        )
        # Strip invalid-state warning prefix if re-populating after a failed map.
        if isinstance(wanted_src, str) and wanted_src.startswith("⚠ "):
            wanted_src = wanted_src[2:].strip()
        if isinstance(wanted_dst, str) and wanted_dst.startswith("⚠ "):
            wanted_dst = wanted_dst[2:].strip()

        resolved_src = ""
        resolved_dst = ""
        if ocio_cfg is not None:
            resolved_src = find_equivalent_space(ocio_cfg, str(wanted_src))
            resolved_dst = find_equivalent_space(ocio_cfg, str(wanted_dst))
        else:
            # No config — only exact names in the (empty) families will work.
            resolved_src = str(wanted_src) if wanted_src else ""
            resolved_dst = str(wanted_dst) if wanted_dst else ""

        if resolved_src:
            self.src_btn.populate(families, resolved_src)
            if resolved_src != wanted_src:
                self.log_message.emit(f"Source color space remapped: {wanted_src} → {resolved_src}")
        else:
            self.src_btn.populate(families, "")
            self.src_btn.set_invalid(str(wanted_src) if wanted_src else "")
            if wanted_src:
                self.log_message.emit(
                    f"Source color space “{wanted_src}” not in new OCIO config — pick one"
                )

        if resolved_dst:
            self.dst_btn.populate(families, resolved_dst)
            if resolved_dst != wanted_dst:
                self.log_message.emit(
                    f"Destination color space remapped: {wanted_dst} → {resolved_dst}"
                )
        else:
            self.dst_btn.populate(families, "")
            self.dst_btn.set_invalid(str(wanted_dst) if wanted_dst else "")
            if wanted_dst:
                self.log_message.emit(
                    f"Destination color space “{wanted_dst}” not in new OCIO config — pick one"
                )

        # Persist only valid selections so we don't lock in a dead name.
        if self.src_btn.is_valid():
            self._settings.setValue(f"{self._mode}/src_space", self.src_btn.current_space())
        if self.dst_btn.is_valid():
            self._settings.setValue(f"{self._mode}/dst_space", self.dst_btn.current_space())
        self._emit_readiness()

    def get_fps(self) -> float:
        if self.fps_widget:
            return self.fps_widget.fps()
        return 24.0

    def get_compression(self) -> str:
        if self.compression_combo:
            return self.compression_combo.currentData() or DEFAULT_EXR_COMPRESSION
        return DEFAULT_EXR_COMPRESSION

    def get_scale(self) -> float:
        return float(self.scale_combo.currentData() or DEFAULT_SCALE)

    def get_padding(self) -> int:
        if self.padding_spin:
            return self.padding_spin.value()
        return DEFAULT_FRAME_PADDING

    def get_start_frame(self) -> int:
        if self.start_frame_spin:
            return self.start_frame_spin.value()
        return DEFAULT_START_FRAME

    def get_video_codec_info(self) -> tuple[str, str, str]:
        """Return (key, libav_codec, pix_fmt) for the selected video codec."""
        if not self.codec_combo:
            return ("h264", "libx264", "yuv420p")
        key = self.codec_combo.currentData() or DEFAULT_VIDEO_CODEC
        spec = video_codec_by_key(str(key))
        if spec is not None and spec.is_available():
            return (spec.key, spec.libav_codec, spec.pix_fmt)
        # Fall back to first available codec on this platform.
        avail = available_video_codecs()
        if avail:
            s = avail[0]
            return (s.key, s.libav_codec, s.pix_fmt)
        return ("h264", "libx264", "yuv420p")

    def get_exr_opts(self) -> dict[str, str]:
        """Return saved EXR compression options."""
        comp = self.get_compression()
        result: dict[str, str] = {}
        if comp in ("dwaa", "dwab"):
            level = float(self._settings.value("exr_opts/dwa_level", 45.0))
            result["dwa_compression_level"] = str(level)
        elif comp in ("zip", "zips"):
            level = int(self._settings.value("exr_opts/zip_level", 4))
            result["zip_level"] = str(level)
        return result

    def get_codec_opts(self) -> dict[str, str]:
        """Return saved video codec options for PyAV stream.options."""
        key = self.get_video_codec_info()[0]
        opts = dict(default_codec_opts(key))
        if key == "h264":
            crf = str(int(self._settings.value("codec_opts/h264_crf", 18)))
            preset = self._settings.value("codec_opts/h264_preset", "medium")
            opts.update({"crf": crf, "preset": str(preset)})
        elif key in ("hevc", "hevc_8", "hevc_12"):
            crf = str(int(self._settings.value("codec_opts/hevc_crf", 18)))
            preset = self._settings.value("codec_opts/hevc_preset", "medium")
            opts.update({"crf": crf, "preset": str(preset)})
        elif key in ("cineform", "cineform_rgb"):
            q = self._settings.value("codec_opts/cineform_quality", DEFAULT_CINEFORM_QUALITY)
            opts["quality"] = str(q)
        return opts

    def slate_model(self):
        """Return the per-tab :class:`SlateModel`, or ``None`` for non-slate modes."""
        return self._slate_model

    def slate_enabled(self) -> bool:
        # Overlays are EXR → video only; never on Video → EXR ingest.
        if self._mode != "exr2video":
            return False
        return self._slate_model is not None and self._slate_model.slate_enabled

    def get_slate_data(self) -> dict | None:
        """Return the rendered-shape slate data, or None if slate is disabled."""
        if not self.slate_enabled():
            return None
        return self._slate_model.slate_data_for_render()

    def get_slate_thumbnail_b64(self) -> str:
        """Return the base64-encoded thumbnail for the slate, or ''."""
        if not self.slate_enabled() or self._slate_model is None:
            return ""
        return self._slate_model.thumbnail_b64

    def get_slate_resolution(self) -> tuple[int, int] | None:
        """Return the slate resolution if slate is enabled."""
        if not self.slate_enabled() or self._slate_model is None:
            return None
        return self._slate_model.slate_resolution

    def burnin_enabled(self) -> bool:
        if self._mode != "exr2video":
            return False
        return self._slate_model is not None and self._slate_model.burnin_enabled

    def get_burnin_fields(self) -> dict[str, str] | None:
        """Return the user-typed burn-in fields, or None if burn-in is disabled."""
        if not self.burnin_enabled() or self._slate_model is None:
            return None
        return self._slate_model.burnin_fields

    def get_effective_burnin_fields(self, input_path: str = "") -> dict[str, str] | None:
        """Return burn-in fields for rendering (manual cells, else slate-derived)."""
        if not self.burnin_enabled() or self._slate_model is None:
            return None
        return self._slate_model.effective_burnin_fields(input_path)

    def watermark_enabled(self) -> bool:
        if self._mode != "exr2video":
            return False
        return self._slate_model is not None and self._slate_model.watermark_enabled

    def get_watermark_params(self) -> dict | None:
        """Return watermark styling, or ``None`` if the tab master switch is off.

        The tab **Watermark** checkbox is the master switch for export.  The
        editor's watermark group (``enabled`` inside the dict) is a secondary
        toggle: both must be on for :meth:`SlateModel.watermark_active` and
        for overlays to bake into the output.
        """
        if not self.watermark_enabled() or self._slate_model is None:
            return None
        return self._slate_model.watermark_params

    def _on_slate_model_changed(self, section: str) -> None:
        """Reflect model changes in the master checkboxes (model → view)."""
        if section == "slate_enabled" and self._slate_check is not None:
            self._slate_check.blockSignals(True)
            self._slate_check.setChecked(self._slate_model.slate_enabled)
            self._slate_check.blockSignals(False)
        elif section == "burnin_enabled" and self._burnin_check is not None:
            self._burnin_check.blockSignals(True)
            self._burnin_check.setChecked(self._slate_model.burnin_enabled)
            self._burnin_check.blockSignals(False)
        elif section == "watermark_enabled" and self._watermark_check is not None:
            self._watermark_check.blockSignals(True)
            self._watermark_check.setChecked(self._slate_model.watermark_enabled)
            self._watermark_check.blockSignals(False)

    def _open_slate_dialog(self) -> None:
        # Local import: slate_widgets pulls in the GPU OCIO preview plane and
        # player machinery, which should only load when the dialog is opened.
        from .slate_widgets import SlateDialog

        # Ingest (Video → EXR) never has slate / burn-in / watermark.
        if self._mode != "exr2video" or self._slate_model is None:
            return

        # Timeline + shot scrub require a validated EXR sequence. If the field
        # still shows a path but the model was never accepted (or was cleared),
        # re-probe before opening so the dialog is not missing the transport.
        inp = self.get_input_path()
        if not inp:
            raw = self.input_path.text().strip()
            if raw and self.set_input(raw):
                inp = self.get_input_path()
            elif raw:
                inp = raw

        locked_w, locked_h = self._detect_input_resolution()
        inferred_fps = self._infer_fps_from_input()
        dst_cs = self.dst_btn.current_space()
        src_cs = self.src_btn.current_space()

        dlg = SlateDialog(
            self._slate_model,
            locked_width=locked_w,
            locked_height=locked_h,
            input_path=inp,
            mode=self._mode,
            inferred_fps=inferred_fps,
            frame_range=self._full_input_range,
            dst_colorspace=dst_cs,
            ocio_cfg=self._ocio_cfg,
            src_colorspace=src_cs,
            parent=self,
        )
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.log_message.emit("Slate & overlay data updated")

    def _infer_fps_from_input(self) -> float:
        """Return frame rate from video probe or EXR→Video tab FPS control."""
        if self._video_info is not None and self._video_info.fps > 0:
            return float(self._video_info.fps)
        if self.fps_widget is not None:
            try:
                return float(self.get_fps() or 0.0)
            except Exception:
                return 0.0
        return 0.0

    def _detect_input_resolution(self) -> tuple[int, int]:
        """Return resolution from the validated input, or (0, 0)."""
        if self._video_info is not None:
            return self._video_info.width, self._video_info.height
        if self._input_seq is not None:
            try:
                # Local import: avoid loading OIIO at module scope for a check
                # that only runs when an EXR sequence is loaded.
                import OpenImageIO as oiio

                first_frame = sorted(self._input_seq.frameSet())[0]
                first_path = self._input_seq.frame(first_frame)
                inp_img = oiio.ImageInput.open(first_path)
                if inp_img:
                    spec = inp_img.spec()
                    w = spec.full_width if spec.full_width > 0 else spec.width
                    h = spec.full_height if spec.full_height > 0 else spec.height
                    inp_img.close()
                    return w, h
            except Exception:
                pass
        return 0, 0

    def _update_comp_btn_state(self) -> None:
        comp = self.get_compression()
        self._comp_settings_btn.setVisible(comp in _EXR_HAS_SETTINGS)

    def _update_codec_btn_state(self) -> None:
        key = self.get_video_codec_info()[0]
        self._codec_settings_btn.setVisible(key in _CODEC_HAS_SETTINGS)

    def _open_compression_settings(self) -> None:
        comp = self.get_compression()
        dlg = ExrCompressionSettingsDialog(comp, self._settings, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            opts = dlg.get_settings()
            if opts:
                parts = [f"{k}={v}" for k, v in opts.items()]
                self.log_message.emit(f"EXR compression ({comp.upper()}): {', '.join(parts)}")
            else:
                self.log_message.emit(f"EXR compression ({comp.upper()}): default settings")

    def _open_codec_settings(self) -> None:
        key = self.get_video_codec_info()[0]
        dlg = VideoCodecSettingsDialog(key, self._settings, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            opts = dlg.get_settings()
            if opts:
                parts = [f"{k}={v}" for k, v in opts.items()]
                self.log_message.emit(f"Codec ({key}): {', '.join(parts)}")
            else:
                self.log_message.emit(f"Codec ({key}): default settings")

    @staticmethod
    def _browse_button_tooltip() -> str:
        """Tooltip for Browse buttons including modifier-click reveal hint."""
        fm = file_manager_label()
        if sys.platform == "darwin":
            mod = "⌘-click"
        else:
            mod = "Ctrl-click"
        return f"Browse for a path.\n{mod} to {fm.lower()} when the field has a valid path."

    def _install_path_field_menus(self) -> None:
        """Replace the default line-edit menu with path-oriented actions."""
        for edit in (self.input_path, self.output_path):
            edit.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
            edit.customContextMenuRequested.connect(
                lambda pos, e=edit: self._show_path_context_menu(e, pos)
            )

    @staticmethod
    def _folder_path_from_field(text: str) -> str:
        """Directory to copy/open for a path field value (file, folder, or name.####.ext)."""
        return _folder_path_for_copy(text)

    def _show_path_context_menu(self, edit: QLineEdit, pos: QPoint) -> None:
        """Standard cut/copy/paste plus path helpers (replaces stock QLineEdit menu).

        Keyboard shortcuts (⌘/Ctrl+X/C/V) remain the QLineEdit defaults; this menu
        only customizes the right-click surface.
        """
        menu = QMenu(edit)
        text = edit.text().strip()
        has_sel = edit.hasSelectedText()
        editable = not edit.isReadOnly()
        folder = self._folder_path_from_field(text)

        # --- Standard edit actions (same semantics as QLineEdit default menu) ---
        cut = QAction("Cut", menu)
        cut.setShortcut(QKeySequence.StandardKey.Cut)
        cut.setShortcutVisibleInContextMenu(True)
        cut.setEnabled(editable and has_sel)
        cut.triggered.connect(edit.cut)
        menu.addAction(cut)

        copy = QAction("Copy", menu)
        copy.setShortcut(QKeySequence.StandardKey.Copy)
        copy.setShortcutVisibleInContextMenu(True)
        copy.setEnabled(has_sel)
        copy.triggered.connect(edit.copy)
        menu.addAction(copy)

        paste = QAction("Paste", menu)
        paste.setShortcut(QKeySequence.StandardKey.Paste)
        paste.setShortcutVisibleInContextMenu(True)
        # QLineEdit has no canPaste() in PySide6; check the clipboard ourselves.
        clip = QGuiApplication.clipboard()
        can_paste = bool(clip is not None and (clip.text() or "").strip())
        paste.setEnabled(editable and can_paste)
        paste.triggered.connect(edit.paste)
        menu.addAction(paste)

        select_all = QAction("Select All", menu)
        select_all.setShortcut(QKeySequence.StandardKey.SelectAll)
        select_all.setShortcutVisibleInContextMenu(True)
        select_all.setEnabled(bool(edit.text()))
        select_all.triggered.connect(edit.selectAll)
        menu.addAction(select_all)

        menu.addSeparator()

        # --- Path-specific helpers (same actions as file-browser row menus) ---
        _add_copy_path_actions(menu, file_path=text, folder_path=folder)

        open_act = QAction(file_manager_label(), menu)
        open_act.setEnabled(bool(folder) and path_is_revealable(folder))
        open_act.triggered.connect(lambda _=False, f=folder: self._try_reveal_path(f))
        menu.addAction(open_act)

        menu.exec(edit.mapToGlobal(pos))

    def _try_reveal_path(self, text: str) -> bool:
        """Open the path's folder in the OS file manager via QDesktopServices."""
        raw = (text or "").strip()
        if not raw:
            return False
        folder = self._folder_path_from_field(raw)
        if not folder:
            return False
        try:
            p = Path(folder).expanduser()
            if not p.is_dir():
                # Parent may exist even if the typed leaf does not.
                if p.parent.is_dir():
                    p = p.parent
                else:
                    return False
            url = QUrl.fromLocalFile(str(p.resolve()))
            if not QDesktopServices.openUrl(url):
                self.log_message.emit(f"Could not open file manager for {p}")
                return False
            self.log_message.emit(f"Opened folder: {p}")
            return True
        except Exception as e:
            log.exception("Open folder failed: %s", raw)
            self.log_message.emit(f"Could not open file manager: {e}")
            return False

    def _on_browse_clicked(self, *, is_input: bool) -> None:
        """Browse…, or ⌘/Ctrl-click to reveal the current path in the file manager."""
        mods = QGuiApplication.keyboardModifiers()
        if mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
            edit = self.input_path if is_input else self.output_path
            if self._try_reveal_path(edit.text()):
                return
            # No valid path — fall through to normal browse.
        if is_input:
            self._pick_input()
        else:
            self._pick_output()

    def _browser_preview_context(self) -> BrowserPreviewContext:
        """OCIO/rate for browser Preview — explicit inject, not parent poking."""
        fps = 24.0
        try:
            fps = float(self.get_fps() or 24.0)
        except Exception:
            fps = 24.0
        return BrowserPreviewContext(
            ocio_cfg=getattr(self, "_ocio_cfg", None),
            src_colorspace=self.src_btn.current_space() if self.src_btn.is_valid() else "",
            fps=fps if fps > 0 else 24.0,
        )

    def _pick_input(self) -> None:
        preview = self._browser_preview_context()
        if self._mode == "video2exr":
            start = self.input_path.text().strip() or str(Path.home())
            dlg = VideoBrowserDialog(start, parent=self, preview=preview)
            if dlg.exec() == QDialog.DialogCode.Accepted and dlg.selected_path():
                # Async probe — large MXFs must not freeze after Browse returns.
                self.set_input_async(dlg.selected_path())
        else:
            start = self.get_input_path() or str(Path.home())
            sel_name = ""
            if self._input_seq is not None:
                sel_name = self._input_seq.basename().rstrip("._")
            dlg = SequenceBrowserDialog(start, select_name=sel_name, parent=self, preview=preview)
            accepted = dlg.exec() == QDialog.DialogCode.Accepted
            # Prefer first-frame path so multi-sequence folders open the
            # row the user selected (directory alone always picks sorted[0]).
            path = (dlg.selected_path() or dlg.selected_directory()) if accepted else ""
            # Destroy the dialog before probing so its GL/prefetch teardown
            # is not interleaved with set_input work on the GUI thread.
            dlg.deleteLater()
            if path:
                QTimer.singleShot(0, lambda p=path: self.set_input_async(p))

    def _pick_output(self) -> None:
        if self._mode == "video2exr":
            path = QFileDialog.getExistingDirectory(
                self,
                "Output directory",
                self.output_path.text(),
            )
        else:
            codec_key = ""
            if self.codec_combo:
                codec_key = self.codec_combo.currentData() or ""
            if codec_key in ("prores", "prores_4444", "prores_xq") or str(codec_key).startswith(
                "prores_"
            ):
                filt = "Video (*.mov)"
            elif codec_key in ("ffv1", "ffv1_12"):
                filt = "Video (*.mkv *.avi)"
            elif codec_key in ("h264", "hevc", "hevc_8", "hevc_12"):
                filt = "Video (*.mp4 *.mov *.mkv)"
            else:
                filt = "Video (*.mp4 *.mov *.mkv)"
            path, _ = QFileDialog.getSaveFileName(
                self,
                "Save video as",
                self.output_path.text(),
                filt,
            )
        if path:
            self.output_path.setText(path)

    def handle_dropped_path(self, path: str) -> bool:
        """Accept a dropped path if valid for this tab's mode. Returns True if accepted."""
        p = Path(path)
        if self._mode == "video2exr":
            if (
                p.is_file()
                and p.suffix.lower() in _VIDEO_EXTS
                and not is_ignored_media_filename(p.name)
            ):
                return self.set_input(str(p))
        else:
            if p.is_dir() or (
                p.is_file()
                and is_image_sequence_ext(p.suffix)
                and not is_ignored_media_filename(p.name)
            ):
                return self.set_input(str(p))
        return False

    def _codec_ext(self) -> str:
        """Return the preferred file extension for the current codec."""
        codec_key = ""
        if self.codec_combo:
            codec_key = self.codec_combo.currentData() or ""
        if codec_key in ("ffv1", "ffv1_12"):
            return ".mkv"
        if codec_key in ("h264", "hevc", "hevc_8", "hevc_12"):
            return ".mp4"
        if str(codec_key).startswith("dnxhr"):
            return ".mxf"
        # ProRes (software + VideoToolbox), CineForm, etc.
        return ".mov"

    def _auto_fill_video_output(self, exr_dir: str) -> None:
        """Set output video path to <parent>/<dirname>.<ext> if not already set."""
        if self._mode != "exr2video":
            return
        if self.output_path.text().strip():
            return
        p = Path(exr_dir)
        out = p.parent / f"{p.name}{self._codec_ext()}"
        self.output_path.setText(str(out))

    def _auto_fill_exr_output(self, video_path: str) -> None:
        """Set output EXR path to ``<video_parent>/<stem>/<stem>.####.exr``."""
        if self._mode != "video2exr":
            return
        p = Path(video_path)
        out_dir = p.parent / p.stem
        pad = "#" * self.get_padding()
        # Always name.####.ext (dot pad) — never underscore.
        display = str(out_dir / f"{p.stem}.{pad}.exr")
        self.output_path.setText(display)

    def _auto_detect_video_colorspace(self, video_path: str) -> None:
        """Guess the source colorspace from video codec/format and select it."""
        if self._mode != "video2exr":
            return
        preferred = resolve_video_src_colorspace(video_path, getattr(self, "_ocio_cfg", None))
        if preferred and self.src_btn.try_select(preferred, auto=True):
            self.log_message.emit(f"Auto-detected source color space: {preferred}")

    def _flash_field(self, widget: QWidget) -> None:
        """Brief amber flash on a path field / button after an auto-edit."""
        widget.setStyleSheet("background-color: #3a3020;")
        # Context = *widget* so the clear always runs on that object (never sticky).
        QTimer.singleShot(500, widget, lambda w=widget: w.setStyleSheet(""))

    def _update_output_placeholder(self) -> None:
        """Update the output placeholder and current pattern to reflect padding."""
        if self._mode != "video2exr" or not self.padding_spin:
            return
        pat = "#" * self.padding_spin.value()
        self.output_path.setPlaceholderText(f"Output EXR sequence (name.{pat}.exr)")
        current = self.output_path.text()
        if current and re.search(r"#+\.exr$", current):
            updated = re.sub(r"#+\.exr$", f"{pat}.exr", current)
            if updated != current:
                self.output_path.setText(updated)
                self._flash_field(self.output_path)

    def _update_output_ext(self) -> None:
        """Update the output path extension to match the current codec."""
        if self._mode != "exr2video":
            return
        current = self.output_path.text().strip()
        if not current:
            return
        p = Path(current)
        new_ext = self._codec_ext()
        if p.suffix.lower() != new_ext:
            self.output_path.setText(str(p.with_suffix(new_ext)))
            self._flash_field(self.output_path)

    def _update_dst_for_codec(self) -> None:
        """Suggest a sensible destination colorspace for the selected codec."""
        if self._mode != "exr2video":
            return
        codec_key = self.codec_combo.currentData() if self.codec_combo else ""
        if codec_key in ("ffv1", "ffv1_12"):
            candidates = ["scene_linear"]
        else:
            candidates = [
                "Output - Rec.709",
                "Rec.1886 Rec.709 - Display",
            ]
        ocio_cfg = getattr(self, "_ocio_cfg", None)
        preferred = candidates[0]
        if ocio_cfg is not None:
            for name in candidates:
                resolved = resolve_alias(ocio_cfg, name)
                if resolved:
                    preferred = resolved
                    break
        if self.dst_btn.try_select(preferred, auto=True):
            pass  # amber flash owned by ColorSpaceButton

    def _auto_detect_colorspace(self, path_or_dir: str) -> None:
        """Probe or infer source colorspace for the loaded image sequence.

        *path_or_dir* should be the selected sequence's first frame when possible
        (multi-seq folders: directory alone re-probes sorted[0]).
        """
        if self._mode != "exr2video":
            return
        p = Path(path_or_dir)
        if p.is_file():
            cs = probe_pixel_colorspace(str(p))
        else:
            cs = probe_exr_colorspace(path_or_dir)
        if not cs:
            # Display-encoded stills (PNG/JPG/…) rarely carry OCIO tags — default sRGB.
            # Use the *selected* path (file or dir), not the parent alone: mixed
            # folders prefer EXR when probing a directory.
            try:
                scene = sequence_looks_scene_referred(path_or_dir)
            except Exception:
                scene = True
            if scene:
                return
            candidates = [
                "sRGB Encoded Rec.709 (sRGB)",
                "sRGB - Texture",
                "Utility - sRGB - Texture",
                "sRGB",
                "Output - Rec.709",
            ]
            ocio_cfg = getattr(self, "_ocio_cfg", None)
            preferred = candidates[0]
            if ocio_cfg is not None:
                for name in candidates:
                    resolved = resolve_alias(ocio_cfg, name)
                    if resolved:
                        preferred = resolved
                        break
            if self.src_btn.try_select(preferred, auto=True):
                self.log_message.emit(f"Display still sequence — source color space: {preferred}")
            return

        canonical = cs
        ocio_cfg = getattr(self, "_ocio_cfg", None)
        if ocio_cfg is not None:
            resolved = resolve_alias(ocio_cfg, cs)
            if resolved:
                canonical = resolved
        if self.src_btn.try_select(canonical, auto=True):
            self.log_message.emit(f"Auto-detected source color space: {canonical}")
        else:
            self.log_message.emit(f'Image color space "{cs}" not found in current OCIO config')

    # -- Input model --

    def set_input_async(self, path: str) -> None:
        """Probe *path* off the GUI thread, then apply results on the main thread.

        Heavy PyAV / OIIO probes on large MXFs must not freeze the UI.
        """
        path = path.strip()
        if not path:
            self.set_input(path)
            return

        # Bump generation so late results from a cancelled probe are ignored.
        self._probe_gen = getattr(self, "_probe_gen", 0) + 1
        gen = self._probe_gen

        # Replace any in-flight probe so a newer path wins.
        old_thr = getattr(self, "_probe_thread", None)
        old_worker = getattr(self, "_probe_worker", None)
        if old_worker is not None:
            try:
                old_worker.finished.disconnect()
                old_worker.failed.disconnect()
            except (RuntimeError, TypeError):
                pass
        if old_thr is not None and old_thr.isRunning():
            old_thr.quit()
            old_thr.wait(500)

        thr = QThread(self)
        worker = _InputProbeWorker(self._mode, path)
        worker.moveToThread(thr)
        thr.started.connect(worker.run)

        def _ok(result: object) -> None:
            if gen != getattr(self, "_probe_gen", 0):
                return
            self._apply_probe_result(result)

        def _fail(err: str) -> None:
            if gen != getattr(self, "_probe_gen", 0):
                return
            self.log_message.emit(f"Could not open input: {err}")
            # Sync fallback: directory/file probes are usually fast; try once
            # on the GUI thread so a restored path is not left "shown but
            # unaccepted" after a transient async failure.
            try:
                if self.set_input(path):
                    self.log_message.emit(f"Opened (retry): {path}")
            except Exception:
                pass

        def _cleanup() -> None:
            if getattr(self, "_probe_thread", None) is thr:
                self._probe_thread = None
                self._probe_worker = None

        worker.finished.connect(_ok, Qt.ConnectionType.QueuedConnection)
        worker.failed.connect(_fail, Qt.ConnectionType.QueuedConnection)
        worker.finished.connect(thr.quit)
        worker.failed.connect(thr.quit)
        thr.finished.connect(worker.deleteLater)
        thr.finished.connect(thr.deleteLater)
        thr.finished.connect(_cleanup)
        self._probe_thread = thr
        self._probe_worker = worker
        thr.start()

    def _apply_probe_result(self, result: object) -> None:
        """Apply a successful async probe result on the GUI thread."""
        if not isinstance(result, dict):
            return
        kind = result.get("kind")
        path = str(result.get("path", ""))

        # Probe mode must match this tab (guards against cross-tab races).
        if kind == "video" and self._mode != "video2exr":
            return
        if kind == "exr" and self._mode != "exr2video":
            return

        if kind == "video":
            self._video_info = VideoInput(
                path,
                int(result["w"]),
                int(result["h"]),
                float(result["fps"]),
                int(result["total"]),
            )
            self._input_seq = None
            frames = list(range(1, int(result["total"]) + 1))
            display = path
            actual = path
        elif kind == "exr":
            seq = result.get("seq")
            if seq is None:
                self.log_message.emit(f"Could not resolve image sequence: {path}")
                return
            self._input_seq = seq
            self._video_info = None
            frames = list(result.get("frame_nums") or [])
            pad = "#" * seq.zfill()
            display = f"{seq.dirname()}{seq.basename()}{pad}{seq.extension()}"
            # Identity-safe: first frame, not the parent directory alone.
            try:
                fs = sorted(seq.frameSet())
                actual = str(seq.frame(fs[0])) if fs else (path or seq.dirname().rstrip("/"))
            except Exception:
                actual = path or seq.dirname().rstrip("/")
        else:
            return

        self.input_path.blockSignals(True)
        self.input_path.setText(display)
        self.input_path.blockSignals(False)

        if frames:
            range_str = format_frame_range(frames)
            self._full_input_range = range_str
            self._frame_range_edit.setText(range_str)
            self._reset_range_btn.setEnabled(True)
        else:
            self._full_input_range = ""
            self._frame_range_edit.clear()
            self._reset_range_btn.setEnabled(False)

        if self._mode == "video2exr":
            self._auto_fill_exr_output(path)
            self._auto_detect_video_colorspace(path)
        elif self._input_seq is not None:
            exr_dir = self._input_seq.dirname().rstrip("/")
            self._auto_fill_video_output(exr_dir)
            # Probe the *selected* sequence (first frame), not sorted[0] in the dir.
            self._auto_detect_colorspace(actual or exr_dir)

        self._persist_input_path(actual)
        self.log_message.emit(f"Input ready: {display}")
        self._emit_readiness()

    def set_input(self, path: str) -> bool:
        """Validate *path* and adopt it as the current input.

        For ``video2exr`` the file is probed with PyAV and a
        :class:`VideoInput` is stored.  For ``exr2video`` the path is
        resolved to a :class:`~fileseq.FileSequence` on disk.

        The ``QLineEdit`` is updated to reflect the validated source and
        the frame-range, output path, and colorspace fields are
        auto-populated.  Returns ``True`` on success.
        """
        path = path.strip()
        if not path:
            self._video_info = None
            self._input_seq = None
            self._full_input_range = ""
            self._frame_range_edit.clear()
            self._reset_range_btn.setEnabled(False)
            self._settings.setValue(f"{self._mode}/input", "")
            self._emit_readiness()
            return False

        try:
            if self._mode == "video2exr":
                w, h, fps, total = probe_video(path)
                self._video_info = VideoInput(path, w, h, fps, total)
                self._input_seq = None
                frames = list(range(1, total + 1))
                display = path
            else:
                _paths, _name, frame_nums, _pad, seq = find_exr_sequence_info(path)
                self._input_seq = seq
                self._video_info = None
                frames = frame_nums
                pad = "#" * seq.zfill()
                display = f"{seq.dirname()}{seq.basename()}{pad}{seq.extension()}"
        except Exception:
            self._video_info = None
            self._input_seq = None
            self._full_input_range = ""
            self._frame_range_edit.clear()
            self._reset_range_btn.setEnabled(False)
            self._emit_readiness()
            return False

        # Update the view
        self.input_path.blockSignals(True)
        self.input_path.setText(display)
        self.input_path.blockSignals(False)

        # Frame range
        if frames:
            range_str = format_frame_range(frames)
            self._full_input_range = range_str
            self._frame_range_edit.setText(range_str)
            self._reset_range_btn.setEnabled(True)
        else:
            self._full_input_range = ""
            self._frame_range_edit.clear()
            self._reset_range_btn.setEnabled(False)

        # Auto-fill dependent fields
        if self._mode == "video2exr":
            self._auto_fill_exr_output(path)
            self._auto_detect_video_colorspace(path)
        else:
            exr_dir = self._input_seq.dirname().rstrip("/")
            self._auto_fill_video_output(exr_dir)
            self._auto_detect_colorspace(self.get_input_path() or exr_dir)

        # Persist an identity-safe path: video file, or first frame of the
        # selected sequence (directory alone always re-resolves to sorted[0]).
        self._persist_input_path(self.get_input_path())

        self._emit_readiness()
        return True

    def _reset_to_source_range(self) -> None:
        """Reset the frame range field to the full source range."""
        if self._full_input_range:
            self._frame_range_edit.setText(self._full_input_range)

    def get_input_path(self) -> str:
        """Return the validated filesystem path from the model, or ``""``.

        For image sequences returns the **first frame path** so multi-sequence
        folders keep the user's selection through convert / slate / restore.
        """
        if self._video_info is not None:
            return self._video_info.path
        if self._input_seq is not None:
            try:
                frames = sorted(self._input_seq.frameSet())
                if frames:
                    return str(self._input_seq.frame(frames[0]))
            except Exception:
                pass
            return self._input_seq.dirname().rstrip("/")
        return ""

    def get_output_path(self) -> str:
        """Return the real filesystem path for the output.

        For video2exr, the display shows a sequence pattern (e.g. name.####.exr)
        but the converter needs just the directory.
        """
        raw = self.output_path.text().strip()
        if self._mode == "video2exr" and raw:
            try:
                directory, _name, _pad = parse_dot_sequence_output(raw)
            except ValueError:
                # Fall back to parent-of-pattern for odd strings.
                p = Path(raw)
                return str(p.parent) if "#" in p.name or p.suffix else str(p)
            return directory or raw
        return raw

    def get_output_sequence_name(self) -> str:
        """Basename for Video → EXR ``name.####.exr`` writes (empty → video stem)."""
        if self._mode != "video2exr":
            return ""
        raw = self.output_path.text().strip()
        if not raw:
            return ""
        try:
            _directory, name, _pad = parse_dot_sequence_output(raw)
        except ValueError:
            return ""
        return name or ""

    def get_output_sequence_padding(self) -> int | None:
        """Padding width from a ``name.####.exr`` pattern, or None if not set."""
        if self._mode != "video2exr":
            return None
        raw = self.output_path.text().strip()
        if not raw:
            return None
        try:
            _directory, _name, pad = parse_dot_sequence_output(raw)
        except ValueError:
            return None
        return pad

    def get_frame_range(self) -> str:
        """Return the user-specified frame range string, or '' for all frames."""
        return self._frame_range_edit.text().strip()
