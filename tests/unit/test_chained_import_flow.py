"""Shared chained-modal import lifecycle tests."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget

from anki_miner.gui.controllers import import_flow_common
from anki_miner.gui.controllers.import_flow_common import ModalImportFlowMixin


class _Signal:
    def __init__(self) -> None:
        self._slots: list[Callable[..., None]] = []

    def connect(self, slot: Callable[..., None]) -> None:
        self._slots.append(slot)

    def disconnect(self) -> None:
        self._slots.clear()

    def emit(self, *args: object) -> None:
        for slot in tuple(self._slots):
            slot(*args)


class _Worker:
    def __init__(self, *, start_error: Exception | None = None) -> None:
        self.progress = _Signal()
        self.import_finished = _Signal()
        self.failed = _Signal()
        self.cancelled = _Signal()
        self.finished = _Signal()
        self.running = False
        self.is_cancelled = False
        self.cancel_calls = 0
        self.start_calls = 0
        self.delete_later_calls = 0
        self.trace_ids: list[str] = []
        self._start_error = start_error

    def set_trace_id(self, trace_id: str) -> None:
        self.trace_ids.append(trace_id)

    def start(self) -> None:
        self.start_calls += 1
        if self._start_error is not None:
            raise self._start_error
        self.running = True

    def cancel(self) -> None:
        self.cancel_calls += 1
        self.is_cancelled = True

    def isRunning(self) -> bool:  # noqa: N802 - Qt API
        return self.running

    def finish(self) -> None:
        self.running = False
        self.finished.emit()

    def deleteLater(self) -> None:  # noqa: N802 - Qt API
        self.delete_later_calls += 1


class _Dialog:
    def __init__(
        self,
        label: str,
        cancel_label: str,
        minimum: int,
        maximum: int,
        parent: QWidget,
    ) -> None:
        self.label = label
        self.cancel_label = cancel_label
        self.minimum = minimum
        self.maximum = maximum
        self.value = minimum
        self.parent = parent
        self.canceled = _Signal()
        self.visible = False
        self.auto_close = True
        self.auto_reset = True
        self.modality = Qt.WindowModality.NonModal
        self.cancel_button_visible = True
        self.close_calls = 0
        self.delete_later_calls = 0
        self.set_value_callback: Callable[[int], None] | None = None

    def setWindowModality(self, modality: Qt.WindowModality) -> None:  # noqa: N802
        self.modality = modality

    def setAutoClose(self, enabled: bool) -> None:  # noqa: N802
        self.auto_close = enabled

    def setAutoReset(self, enabled: bool) -> None:  # noqa: N802
        self.auto_reset = enabled

    def setLabelText(self, label: str) -> None:  # noqa: N802
        self.label = label

    def setRange(self, minimum: int, maximum: int) -> None:  # noqa: N802
        self.minimum = minimum
        self.maximum = maximum

    def setMaximum(self, maximum: int) -> None:  # noqa: N802
        self.maximum = maximum

    def setValue(self, value: int) -> None:  # noqa: N802
        self.value = value
        if self.set_value_callback is not None:
            self.set_value_callback(value)

    def setCancelButton(self, button: object | None) -> None:  # noqa: N802
        self.cancel_button_visible = button is not None

    def show(self) -> None:
        self.visible = True

    def close(self) -> None:
        self.close_calls += 1
        self.canceled.emit()
        self.visible = False

    def deleteLater(self) -> None:  # noqa: N802
        self.delete_later_calls += 1


class _Timer:
    scheduled: list[Callable[[], None]] = []

    def __init__(self, parent: _Dialog) -> None:
        self.parent = parent
        self.timeout = _Signal()
        self.single_shot = False
        self.interval = 0
        self.start_calls = 0
        self.stop_calls = 0
        self.delete_later_calls = 0
        self.active = False
        self.start_callback: Callable[[], None] | None = None

    def setSingleShot(self, enabled: bool) -> None:  # noqa: N802
        self.single_shot = enabled

    def setInterval(self, interval: int) -> None:  # noqa: N802
        self.interval = interval

    def start(self) -> None:
        self.start_calls += 1
        self.active = True
        if self.start_callback is not None:
            self.start_callback()

    def stop(self) -> None:
        self.stop_calls += 1
        self.active = False

    def deleteLater(self) -> None:  # noqa: N802
        self.delete_later_calls += 1

    @classmethod
    def singleShot(cls, _delay: int, callback: Callable[[], None]) -> None:  # noqa: N802
        cls.scheduled.append(callback)

    @classmethod
    def drain(cls) -> None:
        while cls.scheduled:
            cls.scheduled.pop(0)()


class _Harness(ModalImportFlowMixin, QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._parent = self
        self._active_import_worker = None
        self._retained_import_workers = []
        self.buttons_enabled = True
        self.button_states: list[bool] = []

    def _set_import_buttons_enabled(self, enabled: bool) -> None:
        self.buttons_enabled = enabled
        self.button_states.append(enabled)


@pytest.fixture
def spine(monkeypatch: pytest.MonkeyPatch, qtbot: Any) -> tuple[_Harness, list[_Dialog], list[_Timer]]:
    dialogs: list[_Dialog] = []
    timers: list[_Timer] = []
    _Timer.scheduled = []

    def make_dialog(*args: Any, **_kwargs: Any) -> _Dialog:
        dialog = _Dialog(*args)
        dialogs.append(dialog)
        return dialog

    def make_timer(parent: _Dialog) -> _Timer:
        timer = _Timer(parent)
        timers.append(timer)
        return timer

    monkeypatch.setattr(import_flow_common, "QProgressDialog", make_dialog)
    monkeypatch.setattr(import_flow_common, "QTimer", make_timer)
    make_timer.singleShot = _Timer.singleShot  # type: ignore[attr-defined]
    harness = _Harness()
    qtbot.addWidget(harness)
    return harness, dialogs, timers


def _run(
    flow: _Harness,
    workers: list[_Worker],
    *,
    jobs: tuple[str, ...] = ("one",),
    make_worker: Callable[[str], _Worker] | None = None,
    on_finished: Callable[[Any], None] | None = None,
    on_finished_error: Callable[[Exception, Any], None] | None = None,
) -> list[Any]:
    results: list[Any] = []
    worker_iter = iter(workers)

    def default_factory(_job: str) -> _Worker:
        return next(worker_iter)

    flow._run_chained_imports(
        jobs=jobs,
        make_worker=make_worker or default_factory,
        format_label=lambda index, total, job, message: (
            f"Job {index} of {total}: {job}" + (f"\n{message}" if message is not None else "")
        ),
        cancel_label="Cancel",
        cancelling_label="Cancelling…",
        determinate=True,
        join_noun="test import worker",
        failure_summary="Import Failed",
        missing_result_message="Missing result",
        trace_id="trace123",
        on_finished=on_finished or results.append,
        on_finished_error=on_finished_error,
    )
    return results


def test_01_determinate_progress_reaches_maximum_without_hiding(
    spine: tuple[_Harness, list[_Dialog], list[_Timer]],
) -> None:
    flow, dialogs, _timers = spine
    worker = _Worker()

    results = _run(flow, [worker])
    dialog = dialogs[0]
    worker.progress.emit(100, 100, "Complete")

    assert dialog.value == 100
    assert dialog.maximum == 100
    assert dialog.visible
    assert dialog.auto_close is False
    assert dialog.auto_reset is False
    assert results == []


def test_02_natural_completion_does_not_cancel_worker_or_batch(
    spine: tuple[_Harness, list[_Dialog], list[_Timer]],
) -> None:
    flow, _dialogs, _timers = spine
    worker = _Worker()

    results = _run(flow, [worker])
    worker.import_finished.emit("one-id", {})
    worker.finish()

    assert worker.cancel_calls == 0
    assert results[0].cancelled is False


def test_03_domain_success_and_failure_wait_for_native_finished(
    spine: tuple[_Harness, list[_Dialog], list[_Timer]],
) -> None:
    flow, _dialogs, _timers = spine
    first = _Worker()
    second = _Worker()

    results = _run(flow, [first, second], jobs=("one", "two"))
    first.import_finished.emit("one-id", {})
    assert second.start_calls == 0
    assert results == []

    first.finish()
    second.failed.emit("boom")
    assert results == []
    assert flow.buttons_enabled is False

    second.finish()
    assert [item[0] for item in results[0].successes] == ["one"]
    assert results[0].failures == (("two", "boom"),)


def test_04_duplicate_and_racing_domain_signals_accumulate_once(
    spine: tuple[_Harness, list[_Dialog], list[_Timer]],
) -> None:
    flow, _dialogs, _timers = spine
    worker = _Worker()

    results = _run(flow, [worker])
    worker.import_finished.emit("first", {"value": 1})
    worker.import_finished.emit("duplicate", {"value": 2})
    worker.failed.emit("late")
    worker.cancelled.emit()
    worker.finish()

    assert results[0].successes == (("one", "first", {"value": 1}),)
    assert results[0].failures == ()
    assert results[0].cancelled is False


def test_05_late_callbacks_from_prior_job_are_ignored(
    spine: tuple[_Harness, list[_Dialog], list[_Timer]],
) -> None:
    flow, _dialogs, _timers = spine
    first = _Worker()
    second = _Worker()

    results = _run(flow, [first, second], jobs=("one", "two"))
    first.import_finished.emit("one-id", {})
    first.finish()
    assert second.start_calls == 1

    first.failed.emit("late")
    first.finish()
    second.import_finished.emit("two-id", {})
    second.finish()

    assert tuple(item[0] for item in results[0].successes) == ("one", "two")
    assert results[0].failures == ()


def test_06_signal_less_finish_records_missing_result_and_continues(
    spine: tuple[_Harness, list[_Dialog], list[_Timer]],
) -> None:
    flow, _dialogs, _timers = spine
    first = _Worker()
    second = _Worker()

    results = _run(flow, [first, second], jobs=("one", "two"))
    first.finish()
    assert second.start_calls == 1
    second.import_finished.emit("two-id", {})
    second.finish()

    assert results[0].failures == (("one", "Missing result"),)
    assert results[0].successes == (("two", "two-id", {}),)


def test_07_cancel_button_stays_locked_and_visible_until_native_finish(
    spine: tuple[_Harness, list[_Dialog], list[_Timer]],
) -> None:
    flow, dialogs, _timers = spine
    worker = _Worker()

    results = _run(flow, [worker])
    dialog = dialogs[0]
    dialog.canceled.emit()
    _Timer.drain()
    worker.cancelled.emit()

    assert worker.cancel_calls == 1
    assert dialog.visible
    assert dialog.label == "Cancelling…"
    assert dialog.cancel_button_visible is False
    assert flow.buttons_enabled is False
    assert results == []

    worker.finish()
    assert results[0].cancelled is True


def test_08_title_bar_close_reshows_locked_dialog(
    spine: tuple[_Harness, list[_Dialog], list[_Timer]],
) -> None:
    flow, dialogs, _timers = spine
    worker = _Worker()

    _run(flow, [worker])
    dialog = dialogs[0]
    dialog.close()
    assert dialog.visible is False

    _Timer.drain()
    assert dialog.visible
    assert dialog.label == "Cancelling…"
    assert dialog.cancel_button_visible is False


def test_09_late_progress_cannot_overwrite_cancelling_label(
    spine: tuple[_Harness, list[_Dialog], list[_Timer]],
) -> None:
    flow, dialogs, timers = spine
    worker = _Worker()

    _run(flow, [worker])
    dialog = dialogs[0]
    dialog.canceled.emit()
    starts_before = timers[0].start_calls
    worker.progress.emit(1, 2, "Late progress")

    assert dialog.label == "Cancelling…"
    assert timers[0].start_calls == starts_before


def test_10_success_then_cancel_retains_success_without_successor(
    spine: tuple[_Harness, list[_Dialog], list[_Timer]],
) -> None:
    flow, dialogs, _timers = spine
    first = _Worker()
    second = _Worker()

    results = _run(flow, [first, second], jobs=("one", "two"))
    first.import_finished.emit("one-id", {})
    dialogs[0].canceled.emit()
    first.finish()

    assert results[0].successes == (("one", "one-id", {}),)
    assert results[0].cancelled is True
    assert second.start_calls == 0


def test_11_failure_then_cancel_retains_failure_without_successor(
    spine: tuple[_Harness, list[_Dialog], list[_Timer]],
) -> None:
    flow, dialogs, _timers = spine
    first = _Worker()
    second = _Worker()

    results = _run(flow, [first, second], jobs=("one", "two"))
    first.failed.emit("boom")
    dialogs[0].canceled.emit()
    first.finish()

    assert results[0].failures == (("one", "boom"),)
    assert results[0].cancelled is True
    assert second.start_calls == 0


def test_12_domain_cancel_without_dialog_cancel_stops_batch(
    spine: tuple[_Harness, list[_Dialog], list[_Timer]],
) -> None:
    flow, _dialogs, _timers = spine
    first = _Worker()
    second = _Worker()

    results = _run(flow, [first, second], jobs=("one", "two"))
    first.cancelled.emit()
    assert results == []
    first.finish()

    assert results[0].cancelled is True
    assert results[0].failures == ()
    assert second.start_calls == 0


def test_13_direct_worker_cancel_then_success_marks_batch_cancelled(
    spine: tuple[_Harness, list[_Dialog], list[_Timer]],
) -> None:
    flow, _dialogs, _timers = spine
    first = _Worker()
    second = _Worker()

    results = _run(flow, [first, second], jobs=("one", "two"))
    first.cancel()
    first.import_finished.emit("one-id", {})
    first.finish()

    assert results[0].successes == (("one", "one-id", {}),)
    assert results[0].cancelled is True
    assert second.start_calls == 0


def test_14_cancel_while_waiting_does_not_cancel_predecessor_or_finish_early(
    spine: tuple[_Harness, list[_Dialog], list[_Timer]],
) -> None:
    flow, dialogs, timers = spine
    predecessor = _Worker()
    predecessor.running = True
    flow._active_import_worker = predecessor  # type: ignore[assignment]
    made: list[str] = []

    results = _run(
        flow,
        [],
        make_worker=lambda job: made.append(job) or _Worker(),
    )
    dialogs[0].canceled.emit()
    _Timer.drain()

    assert predecessor.cancel_calls == 0
    assert results == []
    assert made == []
    assert flow.buttons_enabled is False
    assert timers[0].start_calls == 0

    predecessor.finish()
    assert results[0].cancelled is True
    assert made == []


def test_15_reentrant_cancel_during_initial_value_update_starts_no_worker(
    spine: tuple[_Harness, list[_Dialog], list[_Timer]],
) -> None:
    flow, dialogs, _timers = spine
    made: list[str] = []

    original_factory = import_flow_common.QProgressDialog

    def make_reentrant_dialog(*args: Any, **kwargs: Any) -> _Dialog:
        dialog = original_factory(*args, **kwargs)
        dialog.set_value_callback = lambda _value: dialog.canceled.emit()
        return dialog

    import_flow_common.QProgressDialog = make_reentrant_dialog
    results = _run(
        flow,
        [],
        make_worker=lambda job: made.append(job) or _Worker(),
    )

    assert made == []
    assert results[0].cancelled is True
    assert dialogs[0].cancel_button_visible is False


def test_construct_rechecks_reentrant_cancel_before_wiring(
    spine: tuple[_Harness, list[_Dialog], list[_Timer]],
) -> None:
    flow, dialogs, timers = spine
    worker = _Worker()

    def make_worker(_job: str) -> _Worker:
        dialogs[0].canceled.emit()
        return worker

    results = _run(flow, [], make_worker=make_worker)

    assert worker.cancel_calls == 1
    assert worker.start_calls == 0
    assert worker.delete_later_calls == 1
    assert timers[0].start_calls == 0
    # Stopped at the re-check right after construction, before set_trace_id;
    # without this the next re-check would end the batch in the same state.
    assert worker.trace_ids == []
    assert results[0].cancelled is True


@pytest.mark.parametrize(
    ("setter", "never_called"),
    [("setLabelText", ("setRange", "setValue")), ("setRange", ("setValue",))],
)
def test_reentrant_cancel_during_job_setup_setter_starts_no_worker(
    spine: tuple[_Harness, list[_Dialog], list[_Timer]],
    monkeypatch: pytest.MonkeyPatch,
    setter: str,
    never_called: tuple[str, ...],
) -> None:
    """The two setters test_15 does not cover: each has its own re-check.

    Asserting that no later setter ran is what makes each case bite: without
    it, the next setter's re-check would catch the cancel and the batch would
    end in the same state.
    """
    flow, dialogs, _timers = spine
    made: list[str] = []
    calls: list[str] = []
    original_factory = import_flow_common.QProgressDialog

    def make_reentrant_dialog(*args: Any, **kwargs: Any) -> _Dialog:
        dialog = original_factory(*args, **kwargs)
        for name in ("setLabelText", "setRange", "setValue"):
            real_setter = getattr(dialog, name)

            def recording(*setter_args: Any, _name: str = name, _real: Callable[..., None] = real_setter) -> None:
                calls.append(_name)
                _real(*setter_args)
                if _name == setter and calls.count(setter) == 1:
                    dialog.canceled.emit()

            setattr(dialog, name, recording)
        return dialog

    monkeypatch.setattr(import_flow_common, "QProgressDialog", make_reentrant_dialog)
    results = _run(flow, [], make_worker=lambda job: made.append(job) or _Worker())

    assert [name for name in calls if name in never_called] == []
    assert made == []
    assert results[0].cancelled is True
    assert dialogs[0].label == "Cancelling…"
    assert dialogs[0].cancel_button_visible is False


_COMMON_LOGGER = "anki_miner.gui.controllers.import_flow_common"


class _CancelOnRecord(logging.Handler):
    """Cancel from inside the logger call whose message carries ``marker``."""

    def __init__(self, marker: str, cancel: Callable[[], None]) -> None:
        super().__init__(level=logging.INFO)
        self._marker = marker
        self._cancel = cancel
        self.fired = False

    def emit(self, record: logging.LogRecord) -> None:
        if not self.fired and self._marker in record.getMessage():
            self.fired = True
            self._cancel()


@pytest.mark.parametrize(
    ("reentry", "cancel_calls", "watchdog_starts", "start_logged"),
    [
        ("set_trace_id", 2, 0, False),  # re-check #2
        ("watchdog_start", 1, 1, False),  # re-check #3: stops before the start log line
        ("worker_start_log", 1, 1, True),  # re-check #4: the log line itself cancels
    ],
)
def test_reentrant_cancel_between_construct_and_start_starts_no_worker(
    spine: tuple[_Harness, list[_Dialog], list[_Timer]],
    caplog: pytest.LogCaptureFixture,
    reentry: str,
    cancel_calls: int,
    watchdog_starts: int,
    start_logged: bool,
) -> None:
    """Pins the re-checks after set_trace_id, the step wiring and the start log line."""
    flow, dialogs, timers = spine
    worker = _Worker()
    caplog.set_level(logging.INFO, logger=_COMMON_LOGGER)
    common_logger = logging.getLogger(_COMMON_LOGGER)
    cancel_on_start_log = _CancelOnRecord("worker start index", lambda: dialogs[0].canceled.emit())

    def make_worker(_job: str) -> _Worker:
        if reentry == "set_trace_id":
            record = worker.set_trace_id

            def set_trace_id(trace_id: str) -> None:
                record(trace_id)
                dialogs[0].canceled.emit()

            worker.set_trace_id = set_trace_id  # type: ignore[method-assign]
        elif reentry == "watchdog_start":
            timers[0].start_callback = dialogs[0].canceled.emit
        else:
            common_logger.addHandler(cancel_on_start_log)
        return worker

    try:
        results = _run(flow, [], make_worker=make_worker)
    finally:
        common_logger.removeHandler(cancel_on_start_log)

    assert worker.start_calls == 0
    assert worker.cancel_calls == cancel_calls
    assert worker.delete_later_calls == 1
    assert timers[0].start_calls == watchdog_starts
    assert results[0].cancelled is True
    start_records = [record for record in caplog.records if "worker start index" in record.getMessage()]
    assert bool(start_records) is start_logged


def test_batch_cancel_hook_lives_exactly_as_long_as_the_batch(
    spine: tuple[_Harness, list[_Dialog], list[_Timer]],
) -> None:
    """SettingsTab shutdown reaches the live batch through cancel_active_batch()."""
    flow, _dialogs, _timers = spine
    first = _Worker()
    second = _Worker()

    results = _run(flow, [first, second], jobs=("one", "two"))
    assert flow._active_batch_cancel_hook is not None
    flow.cancel_active_batch()

    assert first.cancel_calls == 1
    assert results == []
    first.finish()

    assert results[0].cancelled is True
    assert second.start_calls == 0
    assert flow._active_batch_cancel_hook is None
    flow.cancel_active_batch()
    assert first.cancel_calls == 1


def _run_modal(flow: _Harness, worker: _Worker, successes: list[tuple[str, dict]]) -> None:
    flow._run_modal_import(
        worker=worker,  # type: ignore[arg-type]
        progress_label="Importing",
        cancel_label="Cancel",
        determinate=True,
        join_noun="test import worker",
        failure_summary="Import Failed",
        refusal_message="Busy",
        cancelling_label="Cancelling…",
        missing_result_message="Missing result",
        trace_id="trace123",
        on_success=lambda resource_id, meta: successes.append((resource_id, meta)),
    )


@pytest.mark.parametrize("cancel_action", ["button", "title_bar"])
def test_modal_import_cancel_locks_dialog_until_native_finish(
    spine: tuple[_Harness, list[_Dialog], list[_Timer]],
    cancel_action: str,
) -> None:
    """The single-worker spine shares the cancel wiring with the chained one."""
    flow, dialogs, _timers = spine
    worker = _Worker()
    successes: list[tuple[str, dict]] = []

    _run_modal(flow, worker, successes)
    dialog = dialogs[0]
    if cancel_action == "button":
        dialog.canceled.emit()
    else:
        dialog.close()
    _Timer.drain()
    worker.cancelled.emit()

    assert worker.cancel_calls == 1
    assert dialog.visible
    assert dialog.label == "Cancelling…"
    assert dialog.cancel_button_visible is False
    assert flow.buttons_enabled is False

    worker.finish()

    assert successes == []
    assert flow.buttons_enabled is True
    assert dialog.delete_later_calls == 1
    assert worker.delete_later_calls == 1


def test_16_factory_exception_becomes_failure_and_batch_continues(
    spine: tuple[_Harness, list[_Dialog], list[_Timer]],
) -> None:
    flow, _dialogs, _timers = spine
    second = _Worker()
    calls = 0

    def make_worker(_job: str) -> _Worker:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("factory boom")
        return second

    results = _run(flow, [], jobs=("one", "two"), make_worker=make_worker)
    assert results == []
    _Timer.drain()
    second.import_finished.emit("two-id", {})
    second.finish()

    assert results[0].failures == (("one", "factory boom"),)
    assert results[0].successes == (("two", "two-id", {}),)


def test_17_start_exception_releases_worker_and_batch_continues(
    spine: tuple[_Harness, list[_Dialog], list[_Timer]],
) -> None:
    flow, _dialogs, _timers = spine
    first = _Worker(start_error=RuntimeError("start boom"))
    second = _Worker()

    results = _run(flow, [first, second], jobs=("one", "two"))
    assert first.delete_later_calls == 1
    _Timer.drain()
    second.import_finished.emit("two-id", {})
    second.finish()

    assert results[0].failures == (("one", "start boom"),)
    assert results[0].successes == (("two", "two-id", {}),)


def test_17b_start_exception_from_live_worker_waits_for_native_finish(
    spine: tuple[_Harness, list[_Dialog], list[_Timer]],
) -> None:
    flow, _dialogs, _timers = spine

    class _LiveThenRaisesWorker(_Worker):
        def start(self) -> None:
            self.start_calls += 1
            self.running = True
            raise RuntimeError("live start boom")

    first = _LiveThenRaisesWorker()
    second = _Worker()

    results = _run(flow, [first, second], jobs=("one", "two"))

    assert results == []
    assert first.delete_later_calls == 0
    assert second.start_calls == 0

    first.finish()
    assert first.delete_later_calls == 1
    assert second.start_calls == 1
    second.import_finished.emit("two-id", {})
    second.finish()

    assert results[0].failures == (("one", "live start boom"),)
    assert results[0].successes == (("two", "two-id", {}),)


def test_18_watchdog_and_first_progress_reset_per_job_and_wait_unarmed(
    spine: tuple[_Harness, list[_Dialog], list[_Timer]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    flow, _dialogs, timers = spine
    predecessor = _Worker()
    predecessor.running = True
    flow._active_import_worker = predecessor  # type: ignore[assignment]
    first = _Worker()
    second = _Worker()
    caplog.set_level("INFO", logger="anki_miner.gui.controllers.import_flow_common")

    results = _run(flow, [first, second], jobs=("one", "two"))
    assert timers[0].start_calls == 0

    predecessor.finish()
    assert timers[0].start_calls == 1
    timers[0].timeout.emit()
    first.progress.emit(1, 2, "First")
    first.import_finished.emit("one-id", {})
    first.finish()
    assert timers[0].start_calls == 3
    timers[0].timeout.emit()
    second.progress.emit(1, 2, "Second")
    second.import_finished.emit("two-id", {})
    second.finish()

    assert len(results) == 1
    for marker in ("no progress for 10 s", "first progress", "domain latch", "native finished"):
        messages = [record.getMessage() for record in caplog.records if marker in record.getMessage()]
        assert len(messages) == 2
        assert "index=0" in messages[0]
        assert "index=1" in messages[1]


def test_19_batch_callback_failure_runs_error_callback_once_and_cleans_up(
    spine: tuple[_Harness, list[_Dialog], list[_Timer]],
) -> None:
    flow, dialogs, timers = spine
    worker = _Worker()
    errors: list[tuple[Exception, Any]] = []

    def fail_finished(_result: Any) -> None:
        raise RuntimeError("persist boom")

    _run(flow, [worker], on_finished=fail_finished, on_finished_error=lambda exc, result: errors.append((exc, result)))
    worker.import_finished.emit("one-id", {})
    worker.finish()

    assert len(errors) == 1
    assert str(errors[0][0]) == "persist boom"
    assert flow.buttons_enabled
    assert dialogs[0].delete_later_calls == 1
    assert timers[0].delete_later_calls == 1
    assert worker.delete_later_calls == 1


def test_20_error_callback_failure_still_cleans_up(
    spine: tuple[_Harness, list[_Dialog], list[_Timer]],
) -> None:
    flow, dialogs, timers = spine
    worker = _Worker()

    def fail_finished(_result: Any) -> None:
        raise RuntimeError("persist boom")

    def fail_error(_exc: Exception, _result: Any) -> None:
        raise RuntimeError("warning boom")

    _run(flow, [worker], on_finished=fail_finished, on_finished_error=fail_error)
    worker.import_finished.emit("one-id", {})
    with pytest.raises(RuntimeError, match="warning boom"):
        worker.finish()

    assert flow.buttons_enabled
    assert dialogs[0].delete_later_calls == 1
    assert timers[0].delete_later_calls == 1
    assert worker.delete_later_calls == 1


def test_21_dialog_timer_and_workers_are_deleted_exactly_once(
    spine: tuple[_Harness, list[_Dialog], list[_Timer]],
) -> None:
    flow, dialogs, timers = spine
    first = _Worker()
    second = _Worker()

    results = _run(flow, [first, second], jobs=("one", "two"))
    first.import_finished.emit("one-id", {})
    first.finish()
    first.finish()
    second.import_finished.emit("two-id", {})
    second.finish()
    second.finish()

    assert len(results) == 1
    assert dialogs[0].delete_later_calls == 1
    assert timers[0].delete_later_calls == 1
    assert first.delete_later_calls == 1
    assert second.delete_later_calls == 1


def test_terminal_qt_dialog_and_timer_are_deleted(
    monkeypatch: pytest.MonkeyPatch,
    qtbot: Any,
) -> None:
    from PyQt6 import sip
    from PyQt6.QtCore import QTimer
    from PyQt6.QtWidgets import QProgressDialog

    dialogs: list[QProgressDialog] = []
    timers: list[QTimer] = []

    def make_dialog(*args: Any, **kwargs: Any) -> QProgressDialog:
        dialog = QProgressDialog(*args, **kwargs)
        # Production owns and self-deletes this child dialog; qtbot owns only
        # the top-level harness to avoid a double-close during teardown.
        dialogs.append(dialog)
        return dialog

    def make_timer(parent: QProgressDialog) -> QTimer:
        timer = QTimer(parent)
        timers.append(timer)
        return timer

    monkeypatch.setattr(import_flow_common, "QProgressDialog", make_dialog)
    monkeypatch.setattr(import_flow_common, "QTimer", make_timer)
    make_timer.singleShot = QTimer.singleShot  # type: ignore[attr-defined]
    flow = _Harness()
    qtbot.addWidget(flow)
    worker = _Worker()

    _run(flow, [worker])
    dialog = dialogs[0]
    timer = timers[0]
    worker.import_finished.emit("one-id", {})
    worker.finish()

    qtbot.waitUntil(lambda: sip.isdeleted(dialog), timeout=3000)
    qtbot.waitUntil(lambda: sip.isdeleted(timer), timeout=3000)
    assert worker.delete_later_calls == 1


def test_format_batch_summary_separates_sections_and_falls_back_to_empty():
    from anki_miner.gui.controllers.import_flow_common import format_batch_summary

    assert format_batch_summary([], cancelled_note=None, empty="Done.") == "Done."
    assert (
        format_batch_summary(
            [("Imported 1:", ["  • a"]), ("Failed:", ["  • b: boom"])],
            cancelled_note="Cancelled before remaining.",
            empty="Done.",
        )
        == "Imported 1:\n  • a\n\nFailed:\n  • b: boom\n\nCancelled before remaining."
    )
    # A section with no items contributes nothing, not a dangling header.
    assert format_batch_summary([("Imported 0:", [])], cancelled_note=None, empty="Done.") == "Done."
