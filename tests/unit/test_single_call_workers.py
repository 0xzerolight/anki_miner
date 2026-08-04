"""Tests for the short-lived single-call worker family.

After the worker refactor, :class:`SingleCallWorker`
(``gui/workers/base_worker.py``) backs the AnkiConnect fetch factories
(:func:`FetchDecksWorker`, ``FetchFieldsWorker``). The contract is tested once
here against the merged class; the factory-specific payload (which service
method, which error prefix) is pinned per factory.

``UpdateWorkerThread`` and ``ValidationWorkerThread`` are NOT on
``SingleCallWorker`` — they are hand-rolled :class:`CancellableWorker`
subclasses with the same emit-or-stay-silent shape — so each gets its own
contract block.

Behavior contracts call ``run()`` directly. Logging contracts start the
``QThread`` and wait for it so records are verified at the real boundary.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

from anki_miner.gui.workers.base_worker import SingleCallWorker
from anki_miner.gui.workers.fetch_workers import FetchDecksWorker, FetchNotetypesWorker
from anki_miner.gui.workers.update_worker import UpdateWorkerThread
from anki_miner.gui.workers.validation_worker import ValidationWorkerThread


class _Capture:
    """Collect single-arg signal emissions for later inspection."""

    def __init__(self) -> None:
        self.calls: list = []

    def __call__(self, value) -> None:
        self.calls.append(value)


# ===========================================================================
# SingleCallWorker — shared contract (tested once on the merged class)
# ===========================================================================


def test_log_start_uses_concrete_worker_module(qtbot, caplog):
    """Start receipts keep the concrete worker's logger and summary shape."""
    del qtbot
    worker = UpdateWorkerThread(MagicMock())

    with caplog.at_level(logging.INFO, logger="anki_miner.gui.workers"):
        worker.log_start("UpdateProbeWorker", backend="github")

    assert worker.wait(3000)
    record = next(r for r in caplog.records if r.getMessage().startswith("UpdateProbeWorker started:"))
    assert record.levelno == logging.INFO
    assert record.name == "anki_miner.gui.workers.update_worker"
    assert record.name != "anki_miner.gui.workers.base_worker"
    assert "backend=github" in record.getMessage()


def test_single_call_context_names_start_and_failure(qtbot, caplog):
    """One supplied identity follows the worker through both boundary records."""
    del qtbot

    def boom():
        raise RuntimeError("connection refused")

    worker = SingleCallWorker(boom, context="FetchDecksWorker")

    with caplog.at_level(logging.INFO, logger="anki_miner.gui.workers"):
        worker.start()
        assert worker.wait(3000)

    start = next(r for r in caplog.records if r.getMessage().startswith("FetchDecksWorker started:"))
    failure = next(r for r in caplog.records if r.getMessage().startswith("FetchDecksWorker unhandled exception"))
    assert start.levelno == logging.INFO
    assert failure.levelno == logging.ERROR
    assert failure.exc_info is not None


def test_single_call_default_context_is_unchanged(qtbot, caplog):
    """Callers omitting context retain the historical SingleCallWorker identity."""
    del qtbot
    worker = SingleCallWorker(lambda: None)

    with caplog.at_level(logging.INFO, logger="anki_miner.gui.workers"):
        worker.start()
        assert worker.wait(3000)

    record = next(r for r in caplog.records if r.getMessage().startswith("SingleCallWorker started:"))
    assert record.levelno == logging.INFO


def test_single_call_emits_callable_return_verbatim():
    """result_ready carries exactly what the callable returns (object passthrough)."""
    sentinel = object()
    worker = SingleCallWorker(lambda: sentinel)
    results = _Capture()
    worker.result_ready.connect(results)

    worker.run()

    assert results.calls == [sentinel]


def test_single_call_invokes_work_once():
    """The zero-arg callable is invoked exactly once."""
    work = MagicMock(return_value=42)
    worker = SingleCallWorker(work)
    worker.run()

    work.assert_called_once_with()


def test_single_call_error_prefixes_exception_text():
    """On failure the error signal carries f'{prefix}{exc}', no result_ready."""
    worker = SingleCallWorker(
        lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        error_prefix="Could not fetch: ",
    )
    results = _Capture()
    errors = _Capture()
    worker.result_ready.connect(results)
    worker.error.connect(errors)

    worker.run()

    assert results.calls == []
    assert errors.calls == ["Could not fetch: boom"]


def test_single_call_empty_prefix_is_bare_exception_text():
    """An empty error_prefix yields just str(exc)."""
    worker = SingleCallWorker(lambda: (_ for _ in ()).throw(ValueError("nope")))
    errors = _Capture()
    worker.error.connect(errors)

    worker.run()

    assert errors.calls == ["nope"]


def test_single_call_cancel_before_run_skips_work_and_emit():
    """A pre-run cancel returns immediately: callable never runs, no emit."""
    work = MagicMock(return_value="x")
    worker = SingleCallWorker(work)
    results = _Capture()
    worker.result_ready.connect(results)

    worker.cancel()
    worker.run()

    work.assert_not_called()
    assert results.calls == []


def test_single_call_result_suppressed_when_cancelled_during_work():
    """A cancel landing during the callable suppresses the result emit."""
    worker_box: dict = {}

    def _work():
        worker_box["w"].cancel()
        return "late"

    worker = SingleCallWorker(_work)
    worker_box["w"] = worker
    results = _Capture()
    worker.result_ready.connect(results)

    worker.run()

    assert results.calls == []


def test_single_call_can_pass_live_cancellation_predicate():
    """Long callables can stop work instead of only suppressing publication."""
    worker_box: dict = {}
    observed: list[bool] = []

    def _work(is_cancelled):
        observed.append(is_cancelled())
        worker_box["w"].cancel()
        observed.append(is_cancelled())
        return "cancelled"

    worker = SingleCallWorker(_work, pass_cancel_check=True)
    worker_box["w"] = worker
    results = _Capture()
    worker.result_ready.connect(results)

    worker.run()

    assert observed == [False, True]
    assert results.calls == []


def test_single_call_error_suppressed_when_cancelled_during_failure():
    """A raise coinciding with a cancel stays silent — no error emit."""
    worker_box: dict = {}

    def _work():
        worker_box["w"].cancel()
        raise RuntimeError("boom")

    worker = SingleCallWorker(_work)
    worker_box["w"] = worker
    errors = _Capture()
    worker.error.connect(errors)

    worker.run()

    assert errors.calls == []


# ===========================================================================
# FetchDecksWorker — factory-specific payload over SingleCallWorker
# ===========================================================================


def test_fetch_decks_factory_returns_single_call_worker():
    worker = FetchDecksWorker(MagicMock())
    assert isinstance(worker, SingleCallWorker)


def test_fetch_decks_calls_get_deck_names_and_emits():
    service = MagicMock()
    service.get_deck_names.return_value = ["Default", "JP::Anime"]
    worker = FetchDecksWorker(service)
    results = _Capture()
    worker.result_ready.connect(results)

    worker.run()

    service.get_deck_names.assert_called_once_with()
    assert results.calls == [["Default", "JP::Anime"]]


def test_fetch_decks_empty_list_emitted_not_error():
    """AnkiConnect-refused returns []: result_ready([]), distinguishable by the slot."""
    service = MagicMock()
    service.get_deck_names.return_value = []
    worker = FetchDecksWorker(service)
    results = _Capture()
    errors = _Capture()
    worker.result_ready.connect(results)
    worker.error.connect(errors)

    worker.run()

    assert results.calls == [[]]
    assert errors.calls == []


def test_fetch_decks_uses_its_error_prefix():
    service = MagicMock()
    service.get_deck_names.side_effect = RuntimeError("connection refused")
    worker = FetchDecksWorker(service)
    errors = _Capture()
    worker.error.connect(errors)

    worker.run()

    assert errors.calls == ["Error fetching deck names: connection refused"]


# ===========================================================================
# FetchNotetypesWorker — factory-specific payload over SingleCallWorker
# ===========================================================================


def test_fetch_notetypes_factory_returns_single_call_worker():
    worker = FetchNotetypesWorker(MagicMock())
    assert isinstance(worker, SingleCallWorker)


def test_fetch_notetypes_calls_get_model_names_and_emits():
    service = MagicMock()
    service.get_model_names.return_value = ["Basic", "Lapis"]
    worker = FetchNotetypesWorker(service)
    results = _Capture()
    worker.result_ready.connect(results)

    worker.run()

    service.get_model_names.assert_called_once_with()
    assert results.calls == [["Basic", "Lapis"]]


def test_fetch_notetypes_empty_list_emitted_not_error():
    service = MagicMock()
    service.get_model_names.return_value = []
    worker = FetchNotetypesWorker(service)
    results = _Capture()
    errors = _Capture()
    worker.result_ready.connect(results)
    worker.error.connect(errors)

    worker.run()

    assert results.calls == [[]]
    assert errors.calls == []


def test_fetch_notetypes_uses_its_error_prefix():
    service = MagicMock()
    service.get_model_names.side_effect = RuntimeError("connection refused")
    worker = FetchNotetypesWorker(service)
    errors = _Capture()
    worker.error.connect(errors)

    worker.run()

    assert errors.calls == ["Error fetching note type names: connection refused"]


# ===========================================================================
# UpdateWorkerThread — hand-rolled CancellableWorker
# ===========================================================================


def test_update_worker_emits_info_on_success():
    checker = MagicMock()
    info = MagicMock(name="UpdateInfo")
    checker.check_for_update.return_value = info
    worker = UpdateWorkerThread(checker)
    results = _Capture()
    worker.result_ready.connect(results)

    worker.run()

    checker.check_for_update.assert_called_once_with()
    assert results.calls == [info]


def test_update_worker_emits_none_when_no_update():
    """None must still be emitted so the main-thread slot takes its single path."""
    checker = MagicMock()
    checker.check_for_update.return_value = None
    worker = UpdateWorkerThread(checker)
    results = _Capture()
    worker.result_ready.connect(results)

    worker.run()

    assert results.calls == [None]


def test_update_worker_emits_error_on_exception():
    checker = MagicMock()
    checker.check_for_update.side_effect = OSError("network down")
    worker = UpdateWorkerThread(checker)
    results = _Capture()
    errors = _Capture()
    worker.result_ready.connect(results)
    worker.error.connect(errors)

    worker.run()

    assert results.calls == []
    assert errors.calls == ["Error checking for updates: network down"]


def test_update_worker_cancel_before_run_stays_silent():
    checker = MagicMock()
    checker.check_for_update.return_value = MagicMock()
    worker = UpdateWorkerThread(checker)
    results = _Capture()
    worker.result_ready.connect(results)

    worker.cancel()
    worker.run()

    checker.check_for_update.assert_not_called()
    assert results.calls == []


# ===========================================================================
# ValidationWorkerThread — hand-rolled CancellableWorker
# ===========================================================================


def test_validation_worker_emits_result_on_success():
    validator = MagicMock()
    result = MagicMock(name="ValidationResult")
    validator.validate_setup.return_value = result
    worker = ValidationWorkerThread(validator)
    results = _Capture()
    worker.result_ready.connect(results)

    worker.run()

    validator.validate_setup.assert_called_once_with()
    assert results.calls == [result]


def test_validation_worker_emits_error_on_exception():
    validator = MagicMock()
    validator.validate_setup.side_effect = RuntimeError("ffmpeg missing")
    worker = ValidationWorkerThread(validator)
    results = _Capture()
    errors = _Capture()
    worker.result_ready.connect(results)
    worker.error.connect(errors)

    worker.run()

    assert results.calls == []
    assert errors.calls == ["Error during validation: ffmpeg missing"]


def test_validation_worker_cancel_before_run_stays_silent():
    validator = MagicMock()
    validator.validate_setup.return_value = MagicMock()
    worker = ValidationWorkerThread(validator)
    results = _Capture()
    worker.result_ready.connect(results)

    worker.cancel()
    worker.run()

    validator.validate_setup.assert_not_called()
    assert results.calls == []


def test_validation_worker_result_suppressed_when_cancelled_mid_run():
    validator = MagicMock()

    def _validate():
        worker.cancel()
        return MagicMock(name="ValidationResult")

    validator.validate_setup.side_effect = _validate
    worker = ValidationWorkerThread(validator)
    results = _Capture()
    worker.result_ready.connect(results)

    worker.run()

    assert results.calls == []
