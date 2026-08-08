"""Single source of truth for slate / burn-in / watermark editing state.

The model owns everything the conversion pipeline needs to bake a slate
frame, a burn-in overlay, and / or a watermark, plus the three master
toggles (one per feature) that gate inclusion.

Two views currently bind to this model:

- :class:`~src.gui.widgets.BaseModeTab` — tab-level master checkboxes (Slate,
  Burnin, Watermark) drive the ``*_enabled`` flags.
- :class:`~src.gui.slate_widgets.SlateFormPanel` — the editor that lets the
  user set every field.

Both views observe :attr:`changed` and push their edits back through
``set_*`` setters; the model takes care of persistence (``QSettings``)
and dedup (no signal storms when a setter receives the same value).
"""

from __future__ import annotations

import os

from PySide6.QtCore import QObject, QSettings, Signal

from ..render.burnin import burnin_fields_from_slate

_BURNIN_KEYS = (
    "top_left",
    "top_center",
    "top_right",
    "bottom_left",
    "bottom_center",
    "bottom_right",
)

_DEFAULT_WATERMARK = {
    "enabled": False,
    "text": "FOR REVIEW ONLY",
    "opacity": 40,
    "size_pct": 9.0,
    "angle": 30.0,
    "tiled": True,
}

# Slate metadata fields persisted under ``slate/...`` settings keys.  Each
# entry is (settings_key_suffix, env_var_or_None, default_str).  Stored in
# the order they are emitted via :meth:`SlateModel.slate_data` (camelCase
# is the dict-key name used by the renderer).
_SLATE_FIELDS: tuple[tuple[str, str | None, str], ...] = (
    ("show", "SHOW", ""),
    ("sequence", "SEQ", ""),
    ("shot", "SHOT", ""),
    ("artist", None, ""),
    ("vendor", None, ""),
    ("take", None, ""),
    ("submit_for", None, "WIP"),
    ("shot_types", None, ""),
    ("scope", None, ""),
    ("logo", None, ""),
    ("notes", None, ""),
    ("frame_range", None, ""),
)


def _load_slate_field(s: QSettings, key: str, env: str | None, default: str) -> str:
    saved = str(s.value(f"slate/{key}", "") or "")
    if saved:
        return saved
    if env:
        envv = os.environ.get(env)
        if envv:
            return envv
    return default


class SlateModel(QObject):
    """Reactive container for slate / burn-in / watermark state.

    A single ``changed`` signal is emitted whenever any field changes; it
    carries the *name* of the changed section so observers can be
    selective.  Section names: ``slate_enabled``, ``burnin_enabled``,
    ``watermark_enabled``, ``slate_data``, ``thumbnail``,
    ``burnin_fields``, ``watermark_params``.
    """

    changed = Signal(str)

    def __init__(self, settings: QSettings, mode: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._settings = settings
        self._mode = mode

        self._slate_enabled = bool(settings.value(f"{mode}/slate_enabled", False))
        self._burnin_enabled = bool(settings.value(f"{mode}/burnin_enabled", False))
        self._watermark_enabled = bool(settings.value(f"{mode}/watermark_enabled", False))

        # Slate metadata: per-field persistence keys live under ``slate/...``
        # so they survive across modes (slate text is not mode-specific).
        self._slate_data: dict[str, str] = {}
        for key, env, default in _SLATE_FIELDS:
            self._slate_data[key] = _load_slate_field(settings, key, env, default)
        self._slate_version = int(settings.value("slate/version_num", 1))
        self._slate_fps = float(settings.value("slate/fps", 24.0))
        self._slate_resolution = (
            int(settings.value("slate/res_w", 1920)),
            int(settings.value("slate/res_h", 1080)),
        )

        self._thumbnail_b64: str = ""
        self._burnin_fields: dict[str, str] = {k: "" for k in _BURNIN_KEYS}
        for k in _BURNIN_KEYS:
            self._burnin_fields[k] = str(settings.value(f"slate/burnin_{k}", "") or "")

        self._watermark_params: dict = dict(_DEFAULT_WATERMARK)
        self._watermark_params["enabled"] = bool(int(settings.value("slate/wm_enabled", 0)))
        self._watermark_params["text"] = str(
            settings.value("slate/wm_text", _DEFAULT_WATERMARK["text"])
        )
        self._watermark_params["opacity"] = int(
            settings.value("slate/wm_opacity", _DEFAULT_WATERMARK["opacity"])
        )
        self._watermark_params["size_pct"] = float(
            settings.value("slate/wm_size", _DEFAULT_WATERMARK["size_pct"])
        )
        self._watermark_params["angle"] = float(
            settings.value("slate/wm_angle", _DEFAULT_WATERMARK["angle"])
        )
        self._watermark_params["tiled"] = bool(
            int(settings.value("slate/wm_tiled", int(_DEFAULT_WATERMARK["tiled"])))
        )

    @property
    def settings(self) -> QSettings:
        """Underlying :class:`QSettings` for persistence (slate + cache prefs)."""
        return self._settings

    # ------------------------------------------------------------------
    # Flags (master switches surfaced as tab-level checkboxes)
    # ------------------------------------------------------------------

    @property
    def slate_enabled(self) -> bool:
        return self._slate_enabled

    @property
    def burnin_enabled(self) -> bool:
        return self._burnin_enabled

    @property
    def watermark_enabled(self) -> bool:
        return self._watermark_enabled

    def set_slate_enabled(self, value: bool) -> None:
        if bool(value) == self._slate_enabled:
            return
        self._slate_enabled = bool(value)
        self._settings.setValue(f"{self._mode}/slate_enabled", self._slate_enabled)
        self.changed.emit("slate_enabled")

    def set_burnin_enabled(self, value: bool) -> None:
        if bool(value) == self._burnin_enabled:
            return
        self._burnin_enabled = bool(value)
        self._settings.setValue(f"{self._mode}/burnin_enabled", self._burnin_enabled)
        self.changed.emit("burnin_enabled")

    def set_watermark_enabled(self, value: bool) -> None:
        if bool(value) == self._watermark_enabled:
            return
        self._watermark_enabled = bool(value)
        self._settings.setValue(f"{self._mode}/watermark_enabled", self._watermark_enabled)
        self.changed.emit("watermark_enabled")

    # ------------------------------------------------------------------
    # Slate metadata (raw fields)
    # ------------------------------------------------------------------

    @property
    def slate_fields(self) -> dict[str, str]:
        """Return the raw user-typed metadata (keys match _SLATE_FIELDS)."""
        return dict(self._slate_data)

    @property
    def slate_version(self) -> int:
        return self._slate_version

    @property
    def slate_fps(self) -> float:
        return self._slate_fps

    @property
    def slate_resolution(self) -> tuple[int, int]:
        return self._slate_resolution

    def set_slate_fields(
        self,
        fields: dict[str, str],
        version: int | None = None,
        fps: float | None = None,
        resolution: tuple[int, int] | None = None,
    ) -> None:
        """Bulk update the slate metadata from a flat dict + optional extras."""
        merged = dict(self._slate_data)
        for key, _env, default in _SLATE_FIELDS:
            if key in fields:
                merged[key] = str(fields[key] or default)
        next_version = int(version) if version is not None else self._slate_version
        next_fps = float(fps) if fps is not None else self._slate_fps
        next_res = tuple(resolution) if resolution is not None else self._slate_resolution

        if (
            merged == self._slate_data
            and next_version == self._slate_version
            and next_fps == self._slate_fps
            and next_res == self._slate_resolution
        ):
            return

        self._slate_data = merged
        self._slate_version = next_version
        self._slate_fps = next_fps
        self._slate_resolution = (int(next_res[0]), int(next_res[1]))

        s = self._settings
        for key in self._slate_data:
            s.setValue(f"slate/{key}", self._slate_data[key])
        s.setValue("slate/version_num", self._slate_version)
        s.setValue("slate/fps", self._slate_fps)
        s.setValue("slate/res_w", self._slate_resolution[0])
        s.setValue("slate/res_h", self._slate_resolution[1])

        self.changed.emit("slate_data")

    @property
    def thumbnail_b64(self) -> str:
        return self._thumbnail_b64

    def set_thumbnail_b64(self, b64: str) -> None:
        if b64 == self._thumbnail_b64:
            return
        self._thumbnail_b64 = b64
        self.changed.emit("thumbnail")

    # ------------------------------------------------------------------
    # Renderer-shape data
    # ------------------------------------------------------------------

    def slate_data_for_render(self) -> dict:
        """Return a dict in the camelCase shape the slate renderer expects.

        The renderer was originally fed by a JS template and uses fields
        like ``submitFor``, ``shotTypes``, ``frameRange``, ``resolution``,
        and a pre-formatted ``version`` string.  This method shapes the
        raw metadata into that structure so neither the form nor the
        renderer have to know about the other's naming convention.
        """
        import time

        d = self._slate_data
        w, h = self._slate_resolution
        fps = self._slate_fps
        version_str = f"v{self._slate_version:04d}"

        return {
            "show": d.get("show") or "SHOW",
            "sequence": d.get("sequence") or "SEQ",
            "shot": d.get("shot") or "SHOT",
            "version": version_str,
            "take": d.get("take", ""),
            "submitFor": d.get("submit_for") or "WIP",
            "artist": d.get("artist") or "\u2014",
            "vendor": d.get("vendor", ""),
            "shotTypes": d.get("shot_types", ""),
            "scope": d.get("scope", ""),
            "logo": d.get("logo", ""),
            "date": time.strftime("%Y-%m-%d"),
            "fps": str(int(fps)) if fps == int(fps) else f"{fps:.3f}",
            "resolution": f"{w}\u00d7{h}",
            "frameRange": d.get("frame_range") or "\u2014",
            "notes": d.get("notes", ""),
        }

    # ------------------------------------------------------------------
    # Burn-in fields (six-corner text overlay)
    # ------------------------------------------------------------------

    @property
    def burnin_fields(self) -> dict[str, str]:
        return dict(self._burnin_fields)

    def set_burnin_fields(self, fields: dict[str, str]) -> None:
        clean = {k: str(fields.get(k, "") or "") for k in _BURNIN_KEYS}
        if clean == self._burnin_fields:
            return
        self._burnin_fields = clean
        for k, v in clean.items():
            self._settings.setValue(f"slate/burnin_{k}", v)
        self.changed.emit("burnin_fields")

    def reset_burnin_from_slate(self, input_path: str = "") -> None:
        """Fill burn-in fields from current slate data via the existing helper.

        Called from a 'Fill from slate' button in the form.  Replaces the
        manual values; the user can edit afterwards.
        """
        if not self._slate_data:
            return
        fields = burnin_fields_from_slate(self.slate_data_for_render(), input_path)
        self.set_burnin_fields(fields)

    def effective_burnin_fields(self, input_path: str = "") -> dict[str, str]:
        """Burn-in text for rendering: manual cells first, slate-derived fallback.

        If every corner is blank the legacy :func:`burnin_fields_from_slate`
        helper fills sensible defaults from slate metadata.
        """
        manual = self.burnin_fields
        if any((v or "").strip() for v in manual.values()):
            return manual
        return burnin_fields_from_slate(self.slate_data_for_render(), input_path)

    def watermark_active(self) -> bool:
        """True when the watermark master switch is on.

        A single flag (``watermark_enabled``) is shared by the tab checkbox and
        the editor's checkable group box, so both views always agree.
        """
        return self.watermark_enabled

    # ------------------------------------------------------------------
    # Watermark params
    # ------------------------------------------------------------------

    @property
    def watermark_params(self) -> dict:
        # ``enabled`` is mirrored from the single master flag so every consumer
        # (renderer, preview, persistence) agrees with the tab + editor toggles.
        p = dict(self._watermark_params)
        p["enabled"] = self._watermark_enabled
        return p

    def set_watermark_params(self, params: dict) -> None:
        merged = dict(self._watermark_params)
        for k in _DEFAULT_WATERMARK:
            if k in params:
                merged[k] = params[k]
        if merged == self._watermark_params:
            return
        self._watermark_params = merged
        s = self._settings
        s.setValue("slate/wm_enabled", int(bool(merged["enabled"])))
        s.setValue("slate/wm_text", str(merged["text"]))
        s.setValue("slate/wm_opacity", int(merged["opacity"]))
        s.setValue("slate/wm_size", float(merged["size_pct"]))
        s.setValue("slate/wm_angle", float(merged["angle"]))
        s.setValue("slate/wm_tiled", int(bool(merged["tiled"])))
        self.changed.emit("watermark_params")


__all__ = ["SlateModel"]
