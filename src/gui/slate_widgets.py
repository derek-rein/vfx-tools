"""Slate editor widgets: form panel + preview dialog.

The ``SlateDialog`` is opened from the conversion tabs when the user checks
"Prepend slate" and clicks "Edit Slate…".  It contains a form on the left
and a live QPainter-driven preview on the right.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import (
    QEvent,
    QRegularExpression,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import (
    QRegularExpressionValidator,
)
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from ..render.slate import SLATE_COLORSPACE, render_slate_frame
from .nuke_slider import NukeSlider
from .player.preview_view import ZOOM_MAX, ZOOM_MIN
from .player.preview_view import ImagePreviewView as SlatePreviewView
from .player.sequence_player import OverlayHooks, SequencePlayer
from .player.shuttle_bar import ShuttleBar as _ShuttleBar
from .size_grip import SizeGrip
from .token_line_edit import TokenLineEdit

if TYPE_CHECKING:
    import numpy as np

log = logging.getLogger(__name__)

# Legacy re-exports (preview / shuttle / NukeSlider live under player / nuke_slider).
__all__ = [
    "NukeSlider",
    "OverlayHooks",
    "SequencePlayer",
    "SlateDialog",
    "SlateFormPanel",
    "SlatePreviewView",
    "ZOOM_MAX",
    "ZOOM_MIN",
    "_ShuttleBar",
    "extract_thumbnail_b64",
]


def _alpha_over_linear(bg_rgb_f32, overlay_rgba_lin_f32):
    """Straight alpha-over with an already-linearised RGBA float32 overlay."""
    if overlay_rgba_lin_f32 is None or overlay_rgba_lin_f32.shape[2] < 4:
        return bg_rgb_f32
    a = overlay_rgba_lin_f32[..., 3:4]
    fg = overlay_rgba_lin_f32[..., :3]
    return fg * a + bg_rgb_f32 * (1.0 - a)


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
    """Return the pixel colorspace for *path* (prefers our write attribute)."""
    try:
        from ..core.sequence import probe_pixel_colorspace

        return probe_pixel_colorspace(path)
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
# Slate dialog
# ---------------------------------------------------------------------------


class _SlateOverlayHooks:
    """OverlayHooks adapter: synthetic slate frame + burn-in/watermark layers."""

    def __init__(self, dialog: SlateDialog) -> None:
        self._d = dialog

    def is_synthetic_frame(self, frame: int) -> bool:
        return frame == self._d._slate_frame and self._d._slate_frame_active()

    def render_synthetic_frame(self, frame: int) -> tuple[object, str] | None:
        return self._d._render_slate_pixels()

    def gpu_overlay_rgba(self, w: int, h: int, frame: int) -> tuple[object | None, object]:
        return self._d._gpu_overlay_layer(w, h)

    def cpu_composite_overlays(self, working_f32: object, frame: int) -> object:
        return self._d._composite_overlays_working_space(working_f32, is_shot=True)

    def invalidate(self) -> None:
        self._d._invalidate_overlay_cache()


class SlateDialog(QDialog):
    """Modal dialog for editing slate + burn-in overlay data with live preview.

    Left: :class:`SlateFormPanel`. Center: :class:`SequencePlayer` (shot frames
    + optional slate marker). Right: burn-in / watermark controls.
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

        # Output metadata seeds the model once when the dialog opens.
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

        # Overlay linearisation cache (burn-in / watermark).
        self._overlay_lin_cache: dict[str, object] = {}
        self._working_space: str = ""

        # Shot range bookkeeping for markers / tokens (filled after load).
        self._first_shot: int | None = None
        self._last_shot: int | None = None
        self._slate_frame: int = 0
        self._has_sequence = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        # --- Left: slate metadata form ---
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

        # --- Center: reusable sequence player ---
        self._player = SequencePlayer(
            settings=self._model.settings,
            show_cache_ui=True,
        )
        self._overlay_hooks = _SlateOverlayHooks(self)
        self._player.set_overlay_hooks(self._overlay_hooks)

        w, h = self.resolution()
        self._player.set_resolution(w, h)
        fps = self.fps()
        if fps > 0:
            self._player.set_fps(fps)

        if input_path and mode == "exr2video":
            ok = self._player.load_sequence(
                input_path,
                fps=fps if fps > 0 else 24.0,
                ocio_cfg=self._ocio_cfg,
                src_colorspace=self._src_colorspace or "",
                resolution=(w, h),
            )
            if ok:
                self._has_sequence = True
                self._first_shot = self._player.first_shot_frame()
                self._last_shot = self._player.last_shot_frame()
                if self._first_shot is not None:
                    self._slate_frame = self._first_shot - 1
        else:
            # Still pass OCIO so slate-only preview gets a proper display transform.
            self._player.load_sequence(
                "",
                fps=fps if fps > 0 else 24.0,
                ocio_cfg=self._ocio_cfg,
                src_colorspace=self._src_colorspace or "",
                resolution=(w, h),
            )

        splitter.addWidget(self._player)

        # --- Right: burn-in + watermark ---
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
        self._status_bar = QStatusBar()
        self._status_bar.setSizeGripEnabled(False)
        self._status_bar.setContentsMargins(8, 0, 0, 0)
        self._status_bar.setStyleSheet("QStatusBar::item { border: 0; }")
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self._status_bar.addPermanentWidget(buttons)
        self._status_bar.addPermanentWidget(SizeGrip(self._status_bar))
        layout.addWidget(self._status_bar)

        # --- Live preview wiring ---
        self._refresh_timer = QTimer()
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(120)
        self._refresh_timer.timeout.connect(self._on_form_refresh)

        self._form.data_changed.connect(lambda _: self._refresh_timer.start())
        self._model.changed.connect(self._on_model_section_changed)

        QTimer.singleShot(0, self._apply_slate_visibility)
        QTimer.singleShot(0, self._on_form_refresh)
        QTimer.singleShot(0, self._player.fit_in_view)

    def _slate_frame_active(self) -> bool:
        """True when a synthetic slate frame is part of the scrub range."""
        if not self._has_sequence:
            return True
        return self._model is None or self._model.slate_enabled

    def _on_form_refresh(self) -> None:
        self._overlay_hooks.invalidate()
        self._player.refresh()

    def event(self, ev: QEvent) -> bool:
        if ev.type() == QEvent.Type.StatusTip:
            from PySide6.QtGui import QStatusTipEvent

            if isinstance(ev, QStatusTipEvent):
                self._status_bar.showMessage(ev.tip())
                return True
        return super().event(ev)

    def _on_model_section_changed(self, section: str) -> None:
        if section == "slate_enabled":
            self._apply_slate_visibility()

    def _apply_slate_visibility(self) -> None:
        """Add or remove the slate frame from the scrubbable timeline."""
        if not self._has_sequence:
            # Slate-only: single synthetic frame.
            self._slate_frame = 0
            self._player.set_range(0, 0)
            self._player.set_markers({0: "SLATE"} if self._slate_frame_active() else {})
            self._player.set_frame(0)
            self._player.refresh()
            return

        first = self._first_shot
        last = self._last_shot
        if first is None or last is None:
            return
        slate_on = self._slate_frame_active()
        if slate_on:
            self._player.set_range(self._slate_frame, last)
            self._player.set_markers({self._slate_frame: "SLATE"})
            # Park on the slate frame when first opening (or re-enabling).
            if self._player.frame() < first:
                self._player.set_frame(self._slate_frame)
        else:
            self._player.set_range(first, last)
            self._player.set_markers({})
            if self._player.frame() == self._slate_frame or self._player.frame() < first:
                self._player.set_frame(first)
        self._player.refresh()

    def done(self, result: int) -> None:
        self._player.shutdown()
        super().done(result)

    # -- Synthetic slate -----------------------------------------------------

    def _render_slate_pixels(self) -> tuple[object, str] | None:
        """Render slate at preview resolution; return (RGB float32, src colorspace)."""
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
            return None

        rgb = np.ascontiguousarray(rgba[..., :3].copy(), dtype=np.float32)
        return rgb, self._resolve_overlay_auth_space()

    # -- Working-space overlay composite -------------------------------------

    def _resolve_working_space(self) -> str:
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

    def _resolve_overlay_auth_space(self) -> str:
        if self._ocio_cfg is None:
            return SLATE_COLORSPACE
        try:
            from ..core.ocio_utils import get_overlay_authoring_space

            return get_overlay_authoring_space(self._ocio_cfg) or SLATE_COLORSPACE
        except Exception:
            log.exception("Could not resolve overlay authoring space")
            return SLATE_COLORSPACE

    def _alpha_over_rgba(self, bg_rgba, fg_rgba):
        import numpy as np

        if fg_rgba is None:
            return bg_rgba
        a = fg_rgba[..., 3:4]
        out = np.empty_like(bg_rgba, dtype=np.float32)
        out[..., :3] = fg_rgba[..., :3] * a + bg_rgba[..., :3] * (1.0 - a)
        out[..., 3:4] = a + bg_rgba[..., 3:4] * (1.0 - a)
        return out

    def _gpu_overlay_layer(self, w: int, h: int):
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
        h, w = working_f32.shape[:2]
        out = working_f32
        if is_shot:
            burnin_lin = self._cached_burnin_lin_rgba(w, h)
            if burnin_lin is not None:
                out = _alpha_over_linear(out, burnin_lin)

            wm_lin = self._cached_watermark_lin_rgba(w, h)
            if wm_lin is not None:
                out = _alpha_over_linear(out, wm_lin)
        return out

    def _linearise_overlay_cached(self, rgba_u8):
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
        self._overlay_lin_cache.clear()

    def _effective_burnin_fields(self) -> dict[str, str]:
        from ..render.burnin import burnin_fields_from_slate

        manual = self._model.burnin_fields if self._model is not None else {}
        if any((v or "").strip() for v in manual.values()):
            return manual
        return burnin_fields_from_slate(self._form.slate_data(), self._input_path)

    def _token_values(self) -> dict[str, str]:
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
            frame=self._player.frame(),
            frame_pad=pad,
            start_frame=start_f,
            end_frame=end_f,
            resolution=f"{w}x{h}",
        )

    # -- Shared public surface -----------------------------------------------

    def resolution(self) -> tuple[int, int]:
        return self._model.slate_resolution

    def fps(self) -> float:
        return self._model.slate_fps

    def watermark_params(self) -> dict:
        return self._form.watermark_params()

    def slate_data(self) -> dict:
        data = self._form.slate_data()
        data["colorspace"] = self._dst_colorspace or "\u2014"
        return data

    def thumbnail_b64(self) -> str:
        return self._form.thumbnail_b64()
