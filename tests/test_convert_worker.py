"""Unit tests for ConvertWorker cancel classification and signals."""

from __future__ import annotations

from unittest.mock import patch

from src.core.errors import ConversionCancelled
from src.services.worker import ConvertWorker


class TestIsCancelError:
    def test_typed_conversion_cancelled(self) -> None:
        assert ConvertWorker._is_cancel_error(ConversionCancelled())

    def test_interrupted_error(self) -> None:
        assert ConvertWorker._is_cancel_error(InterruptedError())

    def test_legacy_string_messages(self) -> None:
        assert ConvertWorker._is_cancel_error(RuntimeError("Cancelled"))
        assert ConvertWorker._is_cancel_error(RuntimeError("canceled."))

    def test_real_failures_are_not_cancel(self) -> None:
        assert not ConvertWorker._is_cancel_error(RuntimeError("No frames decoded"))
        assert not ConvertWorker._is_cancel_error(ValueError("bad codec"))


class TestConvertWorkerCancelSignal:
    def test_conversion_cancelled_emits_cancelled(self, qapp) -> None:
        seen: list[str] = []
        worker = ConvertWorker(
            "video2exr",
            {"config_source": "", "config_path": ""},
        )
        worker.cancelled.connect(lambda: seen.append("cancelled"))
        worker.failed.connect(lambda _m: seen.append("failed"))
        worker.finished_ok.connect(lambda: seen.append("ok"))

        with (
            patch("src.core.convert.run_video_to_exr", side_effect=ConversionCancelled()),
            patch(
                "src.core.ocio_utils.load_config_from_source_info",
                return_value=object(),
            ),
        ):
            worker.run()

        assert seen == ["cancelled"]

    def test_real_error_emits_failed(self, qapp) -> None:
        seen: list[str] = []
        worker = ConvertWorker(
            "video2exr",
            {"config_source": "", "config_path": ""},
        )
        worker.cancelled.connect(lambda: seen.append("cancelled"))
        worker.failed.connect(lambda m: seen.append(f"failed:{m}"))
        worker.finished_ok.connect(lambda: seen.append("ok"))

        with (
            patch("src.core.convert.run_video_to_exr", side_effect=RuntimeError("boom")),
            patch(
                "src.core.ocio_utils.load_config_from_source_info",
                return_value=object(),
            ),
        ):
            worker.run()

        assert seen == ["failed:boom"]
