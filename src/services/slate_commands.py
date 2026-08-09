"""QUndoCommand classes for :class:`~src.services.slate_model.SlateModel`.

Commands own before/after snapshots; they call model apply methods with
``record_undo=False`` so redo does not re-push. Views push commands; the
model owns the :class:`~PySide6.QtGui.QUndoStack`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtGui import QUndoCommand

if TYPE_CHECKING:
    from .slate_model import SlateModel


class _SlateCommand(QUndoCommand):
    def __init__(self, model: SlateModel, text: str = "") -> None:
        super().__init__(text)
        self._model = model


class SetSlateFieldsCommand(_SlateCommand):
    """Replace slate metadata (fields + version + fps + resolution)."""

    def __init__(
        self,
        model: SlateModel,
        before: dict[str, Any],
        after: dict[str, Any],
        text: str = "Edit slate",
    ) -> None:
        super().__init__(model, text)
        self._before = before
        self._after = after

    def undo(self) -> None:  # noqa: N802
        self._model.apply_slate_snapshot(self._before, record_undo=False)

    def redo(self) -> None:  # noqa: N802
        self._model.apply_slate_snapshot(self._after, record_undo=False)


class SetBurninFieldsCommand(_SlateCommand):
    def __init__(
        self,
        model: SlateModel,
        before: dict[str, str],
        after: dict[str, str],
        text: str = "Edit burn-in",
    ) -> None:
        super().__init__(model, text)
        self._before = dict(before)
        self._after = dict(after)

    def undo(self) -> None:  # noqa: N802
        self._model.set_burnin_fields(self._before, record_undo=False)

    def redo(self) -> None:  # noqa: N802
        self._model.set_burnin_fields(self._after, record_undo=False)


class SetWatermarkParamsCommand(_SlateCommand):
    def __init__(
        self,
        model: SlateModel,
        before: dict,
        after: dict,
        text: str = "Edit watermark",
    ) -> None:
        super().__init__(model, text)
        self._before = dict(before)
        self._after = dict(after)

    def undo(self) -> None:  # noqa: N802
        self._model.set_watermark_params(self._before, record_undo=False)

    def redo(self) -> None:  # noqa: N802
        self._model.set_watermark_params(self._after, record_undo=False)


class SetSlateFlagCommand(_SlateCommand):
    """Toggle slate / burn-in / watermark master enable flags."""

    def __init__(
        self,
        model: SlateModel,
        which: str,
        before: bool,
        after: bool,
        text: str = "",
    ) -> None:
        label = text or f"Toggle {which}"
        super().__init__(model, label)
        self._which = which
        self._before = bool(before)
        self._after = bool(after)

    def _apply(self, value: bool) -> None:
        if self._which == "slate":
            self._model.set_slate_enabled(value, record_undo=False)
        elif self._which == "burnin":
            self._model.set_burnin_enabled(value, record_undo=False)
        elif self._which == "watermark":
            self._model.set_watermark_enabled(value, record_undo=False)

    def undo(self) -> None:  # noqa: N802
        self._apply(self._before)

    def redo(self) -> None:  # noqa: N802
        self._apply(self._after)


__all__ = [
    "SetBurninFieldsCommand",
    "SetSlateFieldsCommand",
    "SetSlateFlagCommand",
    "SetWatermarkParamsCommand",
]
