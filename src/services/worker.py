from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Signal, Slot

from ..core.errors import ConversionCancelled


class ConvertWorker(QObject):
    """Runs a conversion on a worker :class:`~PySide6.QtCore.QThread`.

    Rebuilds its own OCIO config from ``config_source`` / ``config_path`` so the
    GUI thread's live :class:`OCIO.Config` is never shared across threads.
    """

    progress = Signal(int, int)
    log_message = Signal(str)
    failed = Signal(str)
    cancelled = Signal()
    finished_ok = Signal()

    def __init__(self, mode: str, kwargs: dict):
        super().__init__()
        self._mode = mode
        self._kwargs = kwargs
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def _cancel_check(self) -> bool:
        return self._cancel.is_set()

    def _emit_progress(self, cur: int, total: int) -> None:
        self.progress.emit(cur, total)

    def _log(self, msg: str) -> None:
        self.log_message.emit(msg)

    @staticmethod
    def _is_cancel_error(exc: BaseException) -> bool:
        """True when the pipeline stopped because the user hit Cancel."""
        if isinstance(exc, ConversionCancelled | InterruptedError):
            return True
        text = str(exc).strip().lower()
        return text in {"cancelled", "canceled", "cancelled.", "canceled."}

    @Slot()
    def run(self) -> None:
        try:
            from ..core.convert import run_exr_to_video, run_video_to_exr
            from ..core.ocio_utils import load_config_from_source_info

            kwargs = dict(self._kwargs)
            # Never use a live GUI-thread OCIO.Config — rebuild from paths.
            kwargs.pop("ocio_cfg", None)
            kwargs["ocio_cfg"] = load_config_from_source_info(
                kwargs.get("config_source", "") or "",
                kwargs.get("config_path", "") or "",
            )

            fn = run_video_to_exr if self._mode == "video2exr" else run_exr_to_video
            fn(
                progress=self._emit_progress,
                cancel_check=self._cancel_check,
                log=self._log,
                **kwargs,
            )
        except Exception as e:
            if self._cancel.is_set() or self._is_cancel_error(e):
                self.log_message.emit("Conversion cancelled.")
                self.cancelled.emit()
            else:
                self.log_message.emit(f"ERROR: {e}")
                self.failed.emit(str(e))
        else:
            self.log_message.emit("Conversion complete.")
            self.finished_ok.emit()
