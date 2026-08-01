"""Tests for CancellableWorker.report_failure — the shared worker failure guard.

The guard exists because expected conditions were producing ERROR-level
tracebacks: Anki simply not running wrote a 40-line ``AnkiConnectionError``
traceback per attempt, and pressing Cancel on an import wrote another.
"""

import logging

import pytest

from anki_miner.exceptions import AnkiConnectionError, AnkiMinerException, OperationCancelled, SetupError
from anki_miner.gui.workers.base_worker import CancellableWorker, SingleCallWorker


class _ProbeWorker(CancellableWorker):
    """Minimal worker used only to exercise report_failure."""

    def run(self) -> None:  # pragma: no cover - never started
        raise NotImplementedError


class TestOperationCancelledType:
    """The cancel type must stay catchable by every pre-existing handler."""

    def test_subclasses_setup_error(self):
        assert issubclass(OperationCancelled, SetupError)

    def test_subclasses_anki_miner_exception(self):
        assert issubclass(OperationCancelled, AnkiMinerException)

    def test_is_not_the_stdlib_cancelled_error(self):
        """Deliberately not named CancelledError; must not be a BaseException-only type."""
        assert issubclass(OperationCancelled, Exception)

    def test_resource_downloader_string_discriminator_is_gone(self):
        """The type replaced a str(exc) == "Download cancelled" compare."""
        from anki_miner.services import resource_downloader

        assert not hasattr(resource_downloader, "_is_cancellation")


class TestReportFailure:
    """Classification: cancel -> INFO, domain error -> WARNING, bug -> traceback."""

    @pytest.fixture
    def worker(self, qapp, qtbot):
        del qapp, qtbot  # QThread, not a widget; fixtures only ensure a QApplication
        return _ProbeWorker()

    @pytest.fixture
    def sinks(self):
        errors: list[str] = []
        cancels: list[bool] = []
        return errors, cancels

    def _records(self, caplog, worker):
        name = type(worker).__module__
        return [r for r in caplog.records if r.name == name]

    def test_cancel_exception_logs_info_and_fires_on_cancelled(self, worker, sinks, caplog):
        errors, cancels = sinks
        with caplog.at_level(logging.INFO, logger=type(worker).__module__):
            worker.report_failure(
                OperationCancelled("Import cancelled"),
                context="ProbeWorker",
                on_error=errors.append,
                on_cancelled=lambda: cancels.append(True),
            )

        records = self._records(caplog, worker)
        assert [r.levelno for r in records] == [logging.INFO]
        assert records[0].exc_info is None
        assert cancels == [True]
        assert errors == []

    def test_cancel_without_on_cancelled_stays_silent(self, worker, sinks, caplog):
        """Workers with no ``cancelled`` signal must not emit an error on cancel."""
        errors, _ = sinks
        with caplog.at_level(logging.INFO, logger=type(worker).__module__):
            worker.report_failure(
                OperationCancelled("Download cancelled"),
                context="ProbeWorker",
                on_error=errors.append,
            )

        assert errors == []

    def test_cancelled_flag_suppresses_an_abandoned_run_by_default(self, worker, sinks, caplog):
        """The user walked away; a teardown error from an abandoned run is not worth a dialog."""
        errors, cancels = sinks
        worker.cancel()
        with caplog.at_level(logging.INFO, logger=type(worker).__module__):
            worker.report_failure(
                RuntimeError("torn down mid-read"),
                context="ProbeWorker",
                on_error=errors.append,
                on_cancelled=lambda: cancels.append(True),
            )

        assert [r.levelno for r in self._records(caplog, worker)] == [logging.INFO]
        assert cancels == [True]
        assert errors == []

    def test_cancel_flag_does_not_hide_a_genuine_failure_when_opted_out(self, worker, sinks, caplog):
        """Workers whose terminal signal drives UI state must still hear a real failure.

        The flag alone is not proof of a cancel: a worker can set it and then
        fail for an unrelated reason (ImportWorker after promotion).
        """
        errors, cancels = sinks
        worker.cancel()
        with caplog.at_level(logging.INFO, logger=type(worker).__module__):
            worker.report_failure(
                RuntimeError("failure after promotion"),
                context="ProbeWorker",
                on_error=errors.append,
                on_cancelled=lambda: cancels.append(True),
                cancel_flag_suppresses_error=False,
            )

        records = self._records(caplog, worker)
        assert [r.levelno for r in records] == [logging.ERROR]
        assert records[0].exc_info is not None
        assert errors == ["failure after promotion"]
        assert cancels == []

    def test_typed_cancel_still_wins_over_the_opt_out(self, worker, sinks, caplog):
        """Opting out of flag suppression must not turn a real cancel into an error."""
        errors, cancels = sinks
        worker.cancel()
        with caplog.at_level(logging.INFO, logger=type(worker).__module__):
            worker.report_failure(
                OperationCancelled("Import cancelled"),
                context="ProbeWorker",
                on_error=errors.append,
                on_cancelled=lambda: cancels.append(True),
                cancel_flag_suppresses_error=False,
            )

        assert [r.levelno for r in self._records(caplog, worker)] == [logging.INFO]
        assert cancels == [True]
        assert errors == []

    def test_domain_exception_logs_warning_without_traceback(self, worker, sinks, caplog):
        """Anki not running is a user state, not a bug — one WARNING line, no traceback."""
        errors, cancels = sinks
        exc = AnkiConnectionError("Cannot connect to AnkiConnect. Is Anki running?")
        with caplog.at_level(logging.INFO, logger=type(worker).__module__):
            worker.report_failure(
                exc,
                context="ProbeWorker",
                on_error=errors.append,
                on_cancelled=lambda: cancels.append(True),
            )

        records = self._records(caplog, worker)
        assert [r.levelno for r in records] == [logging.WARNING]
        assert records[0].exc_info is None
        assert "Cannot connect to AnkiConnect" in records[0].getMessage()
        assert errors == ["Cannot connect to AnkiConnect. Is Anki running?"]
        assert cancels == []

    def test_unexpected_exception_keeps_the_traceback(self, worker, sinks, caplog):
        errors, _ = sinks
        with caplog.at_level(logging.INFO, logger=type(worker).__module__):
            worker.report_failure(
                KeyError("missing_key"),
                context="ProbeWorker",
                on_error=errors.append,
            )

        records = self._records(caplog, worker)
        assert [r.levelno for r in records] == [logging.ERROR]
        assert records[0].exc_info is not None
        assert isinstance(records[0].exc_info[1], KeyError)
        assert errors and "missing_key" in errors[0]

    def test_memory_error_is_not_re_raised(self, worker, sinks, caplog):
        """EpisodeProcessor re-raises MemoryError *to* this guard; re-raising again aborts the process."""
        errors, _ = sinks
        with caplog.at_level(logging.INFO, logger=type(worker).__module__):
            worker.report_failure(MemoryError("out of memory"), context="ProbeWorker", on_error=errors.append)

        assert [r.levelno for r in self._records(caplog, worker)] == [logging.ERROR]
        assert errors == ["out of memory"]

    def test_record_keeps_the_subclass_module_name(self, worker, sinks, caplog):
        """Provenance: records must not collapse onto base_worker."""
        errors, _ = sinks
        with caplog.at_level(logging.INFO, logger=type(worker).__module__):
            worker.report_failure(RuntimeError("boom"), context="ProbeWorker", on_error=errors.append)

        assert self._records(caplog, worker), "no record on the subclass logger"
        assert not [r for r in caplog.records if r.name == "anki_miner.gui.workers.base_worker"]


class TestSingleCallWorkerRouting:
    """SingleCallWorker is the loudest AnkiConnectionError source (deck/notetype fetchers)."""

    def test_domain_failure_logs_warning_and_keeps_the_prefix(self, qapp, caplog):
        del qapp

        def boom():
            raise AnkiConnectionError("Is Anki running?")

        worker = SingleCallWorker(boom, error_prefix="Could not load decks: ")
        seen: list[str] = []
        worker.error.connect(seen.append)

        with caplog.at_level(logging.INFO, logger="anki_miner.gui.workers.base_worker"):
            worker.run()

        records = [r for r in caplog.records if r.name == "anki_miner.gui.workers.base_worker"]
        assert [r.levelno for r in records] == [logging.WARNING]
        assert records[0].exc_info is None
        assert seen == ["Could not load decks: Is Anki running?"]
