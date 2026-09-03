"""Tests for the off-GUI-thread execution helpers.

Covers :func:`run_off_thread`, :func:`join_worker` and
:func:`join_tracked_workers` in ``gui/utils/run_off_thread.py``. Unlike the
``SingleCallWorker`` contract tests (which call ``run()`` synchronously), these
exercise the *real* Qt threading lifecycle: workers are ``start()``ed and the
result is delivered back on the GUI thread via queued signals. pytest-qt's
``qtbot`` drives the event loop.
"""

from __future__ import annotations

import logging
import threading
import time

import pytest
from PyQt6.QtCore import QObject

from anki_miner.gui.utils import run_off_thread as rot
from anki_miner.gui.utils.run_off_thread import (
    join_all_off_thread_workers,
    join_tracked_workers,
    join_worker,
    run_off_thread,
    still_running,
)
from anki_miner.gui.workers.base_worker import CancellableWorker


@pytest.fixture(autouse=True)
def _isolate_live_worker_registry():
    """Snapshot + restore the process-global live-worker set around each test.

    The tests mutate ``_LIVE_OFF_THREAD_WORKERS`` directly, and not every path
    cleans up; a leaked entry makes the empty-registry assertions order-
    dependent (the repo has documented pytest-qt order sensitivity). Snapshot
    before, restore after, so each test sees a pristine global set.
    """
    snapshot = set(rot._LIVE_OFF_THREAD_WORKERS)
    rot._LIVE_OFF_THREAD_WORKERS.clear()
    try:
        yield
    finally:
        rot._LIVE_OFF_THREAD_WORKERS.clear()
        rot._LIVE_OFF_THREAD_WORKERS.update(snapshot)


class _Sink(QObject):
    """A plain QObject to act as the worker parent / registry holder."""


class _SleepWorker(CancellableWorker):
    """A worker that ignores cancel and sleeps, to force join timeouts."""

    def __init__(self, seconds: float, *, respect_cancel: bool = False, parent=None) -> None:
        super().__init__(parent)
        self._seconds = seconds
        self._respect_cancel = respect_cancel

    def run(self) -> None:
        deadline = time.monotonic() + self._seconds
        while time.monotonic() < deadline:
            if self._respect_cancel and self.check_cancelled():
                return
            time.sleep(0.01)


# ===========================================================================
# run_off_thread
# ===========================================================================


def test_run_off_thread_delivers_result_to_on_done(qtbot):
    parent = _Sink()
    received: list = []
    sentinel = object()

    run_off_thread(parent, lambda: sentinel, received.append)

    qtbot.waitUntil(lambda: bool(received), timeout=3000)
    assert received == [sentinel]
    # Drain queued deleteLater so the C++ worker is torn down deterministically.
    qtbot.waitUntil(lambda: not parent._off_thread_workers, timeout=3000)


def test_run_off_thread_routes_exception_to_on_error_with_prefix(qtbot):
    parent = _Sink()
    results: list = []
    errors: list = []

    def _boom():
        raise RuntimeError("boom")

    run_off_thread(
        parent,
        _boom,
        results.append,
        errors.append,
        error_prefix="Could not: ",
    )

    qtbot.waitUntil(lambda: bool(errors), timeout=3000)
    assert errors == ["Could not: boom"]
    assert results == []
    qtbot.waitUntil(lambda: not parent._off_thread_workers, timeout=3000)


def test_run_off_thread_default_on_error_does_not_double_log(qtbot, caplog):
    """The failure is logged once, by the worker guard -- not again by the sink.

    The default sink used to log its own WARNING on top of
    ``SingleCallWorker``'s record, so every unhandled off-thread failure
    appeared twice in the log.
    """
    parent = _Sink()

    def _boom():
        raise ValueError("nope")

    with caplog.at_level(logging.DEBUG):
        run_off_thread(parent, _boom, lambda _v: None)
        qtbot.waitUntil(lambda: not parent._off_thread_workers, timeout=3000)

    worker_records = [r for r in caplog.records if r.name == "anki_miner.gui.workers.base_worker"]
    sink_records = [r for r in caplog.records if r.name == "anki_miner.gui.utils.run_off_thread"]
    # Skip the INFO start receipt; what this pins is that the FAILURE is
    # recorded once, by the worker guard, and not again by the sink.
    worker_failures = [r for r in worker_records if r.levelno >= logging.WARNING]
    assert [r.levelno for r in worker_failures] == [logging.ERROR]
    assert "nope" in str(worker_failures[0].exc_info[1])
    assert [r.levelno for r in sink_records] == [logging.DEBUG]


def test_run_off_thread_tracks_then_untracks_worker(qtbot):
    parent = _Sink()
    received: list = []

    worker = run_off_thread(parent, lambda: 1, received.append)

    # Tracked while running.
    assert worker in parent._off_thread_workers

    qtbot.waitUntil(lambda: bool(received), timeout=3000)
    # Removed after the finished signal fires.
    qtbot.waitUntil(lambda: worker not in parent._off_thread_workers, timeout=3000)
    assert worker not in parent._off_thread_workers


def test_run_off_thread_on_done_runs_on_gui_thread(qtbot):
    parent = _Sink()
    main_thread_id = threading.get_ident()
    slot_thread_id: list = []

    def _on_done(_value):
        slot_thread_id.append(threading.get_ident())

    run_off_thread(parent, lambda: "x", _on_done)

    qtbot.waitUntil(lambda: bool(slot_thread_id), timeout=3000)
    assert slot_thread_id == [main_thread_id]
    qtbot.waitUntil(lambda: not parent._off_thread_workers, timeout=3000)


# ===========================================================================
# join_worker
# ===========================================================================


def test_join_worker_none_returns_true():
    assert join_worker(None) is True


def test_deleted_wrapper_treated_as_stopped():
    class _Dead:
        def isRunning(self):  # noqa: N802
            raise RuntimeError("wrapped C/C++ object has been deleted")

    dead = _Dead()
    assert still_running(dead) is False
    assert join_worker(dead) is True


def test_join_worker_not_running_returns_true():
    worker = _SleepWorker(0.0)
    assert join_worker(worker) is True


def test_join_worker_quick_worker_returns_true(qtbot):
    worker = _SleepWorker(0.0)
    worker.start()
    assert join_worker(worker, timeout_ms=3000) is True
    assert worker.isFinished()


def test_join_worker_times_out_on_uncancellable_sleep(qtbot):
    worker = _SleepWorker(5.0, respect_cancel=False)
    worker.start()
    qtbot.waitUntil(lambda: worker.isRunning(), timeout=2000)

    assert join_worker(worker, timeout_ms=50) is False

    # Clean up: let it respect nothing but give it time to finish naturally is
    # too slow, so wait the full duration to avoid a leaked C++ thread.
    assert worker.wait(7000)


def test_join_worker_cancels_cooperative_worker(qtbot):
    worker = _SleepWorker(5.0, respect_cancel=True)
    worker.start()
    qtbot.waitUntil(lambda: worker.isRunning(), timeout=2000)

    # cancel() is issued by join_worker; the cooperative loop exits well before
    # the 5s sleep completes.
    assert join_worker(worker, timeout_ms=3000) is True
    assert worker.isFinished()


# ===========================================================================
# join_tracked_workers
# ===========================================================================


def test_join_tracked_workers_returns_laggards_and_clears_joined(qtbot):
    parent = _Sink()
    received: list = []

    quick = run_off_thread(parent, lambda: 1, received.append)
    qtbot.waitUntil(lambda: bool(received), timeout=3000)

    laggard = _SleepWorker(5.0, respect_cancel=False, parent=parent)
    parent._off_thread_workers.add(laggard)
    laggard.start()
    qtbot.waitUntil(lambda: laggard.isRunning(), timeout=2000)

    laggards = join_tracked_workers(parent, timeout_ms=50)

    assert laggard in laggards
    assert quick not in laggards
    # Joined workers were removed from the tracking set.
    assert quick not in parent._off_thread_workers

    # Clean up the leaked thread.
    assert laggard.wait(7000)


def test_join_tracked_workers_empty_parent_returns_empty_list():
    parent = _Sink()
    assert join_tracked_workers(parent) == []


def test_join_tracked_workers_suppresses_runtime_error(qtbot):
    """A deleted C++ object raising RuntimeError is treated as already gone."""
    parent = _Sink()

    class _Dead:
        def isRunning(self):  # noqa: N802 — Qt API name
            raise RuntimeError("wrapped C/C++ object has been deleted")

    parent._off_thread_workers = {_Dead()}

    # Must not raise; the dead worker is simply dropped.
    laggards = join_tracked_workers(parent, timeout_ms=50)
    assert laggards == []


# ===========================================================================
# join_all_off_thread_workers (global registry — app-close join)
# ===========================================================================


def test_join_all_off_thread_workers_empty_returns_empty():
    """No live workers → empty laggard list, no error."""
    assert join_all_off_thread_workers(timeout_ms=50) == []


def test_join_all_off_thread_workers_joins_finished_worker_already_gone(qtbot):
    """A finished worker self-discards from the global registry before close."""
    parent = _Sink()
    received: list = []

    worker = run_off_thread(parent, lambda: 1, received.append)
    qtbot.waitUntil(lambda: bool(received), timeout=3000)
    # The finished -> _teardown handler discards from the global set too.
    qtbot.waitUntil(lambda: worker not in parent._off_thread_workers, timeout=3000)

    laggards = join_all_off_thread_workers(timeout_ms=50)
    assert worker not in laggards


def test_join_all_off_thread_workers_returns_laggard_for_stuck_worker(qtbot):
    """A still-running, uncancellable worker is returned as a laggard."""
    parent = _Sink()
    laggard = _SleepWorker(5.0, respect_cancel=False, parent=parent)
    # run_off_thread registers in the global set; emulate by going through it for
    # a quick worker, then directly add the stuck one to both registries.
    from anki_miner.gui.utils import run_off_thread as rot

    rot._LIVE_OFF_THREAD_WORKERS.add(laggard)
    parent._off_thread_workers = {laggard}
    laggard.start()
    qtbot.waitUntil(lambda: laggard.isRunning(), timeout=2000)

    try:
        laggards = join_all_off_thread_workers(timeout_ms=50)
        assert laggard in laggards
    finally:
        # Clean up the leaked thread + global registry entry.
        assert laggard.wait(7000)
        rot._LIVE_OFF_THREAD_WORKERS.discard(laggard)


def test_join_all_off_thread_workers_cancels_cooperative_worker(qtbot):
    """A cooperative live worker is cancelled+joined, not returned as a laggard."""
    parent = _Sink()
    worker = _SleepWorker(5.0, respect_cancel=True, parent=parent)
    from anki_miner.gui.utils import run_off_thread as rot

    rot._LIVE_OFF_THREAD_WORKERS.add(worker)
    worker.start()
    qtbot.waitUntil(lambda: worker.isRunning(), timeout=2000)

    laggards = join_all_off_thread_workers(timeout_ms=3000)
    assert worker not in laggards
    assert worker.isFinished()


def test_join_all_off_thread_workers_suppresses_runtime_error():
    """A deleted C++ object raising RuntimeError is treated as already gone."""
    from anki_miner.gui.utils import run_off_thread as rot

    class _Dead:
        def isRunning(self):  # noqa: N802 — Qt API name
            raise RuntimeError("wrapped C/C++ object has been deleted")

    dead = _Dead()
    rot._LIVE_OFF_THREAD_WORKERS.add(dead)
    try:
        laggards = join_all_off_thread_workers(timeout_ms=50)
        assert laggards == []
        assert dead not in rot._LIVE_OFF_THREAD_WORKERS
    finally:
        rot._LIVE_OFF_THREAD_WORKERS.discard(dead)


def test_run_off_thread_registers_in_global_set(qtbot):
    """A dispatched worker is tracked in the module-global live set while running."""
    from anki_miner.gui.utils import run_off_thread as rot

    parent = _Sink()
    received: list = []

    worker = run_off_thread(parent, lambda: 1, received.append)
    assert worker in rot._LIVE_OFF_THREAD_WORKERS

    qtbot.waitUntil(lambda: bool(received), timeout=3000)
    qtbot.waitUntil(lambda: worker not in rot._LIVE_OFF_THREAD_WORKERS, timeout=3000)


# ===========================================================================
# Dispatch identity + laggard diagnostics
# ===========================================================================


class _Host(QObject):
    """A named parent so the dispatch identity string is predictable."""


def test_describe_work_prefixes_parent_type():
    def load_decks():
        return None

    assert rot.describe_work(load_decks, _Host()) == "_Host.load_decks"


def test_describe_work_unwraps_partial_and_keeps_lambda_name():
    import functools

    def load_decks(a, b):
        return None

    bound = functools.partial(functools.partial(load_decks, 1), 2)
    assert rot.describe_work(bound, _Host()) == "_Host.load_decks"
    assert rot.describe_work(lambda: None, _Host()) == "_Host.<lambda>"


def test_describe_work_falls_back_to_type_name_for_callable_object():
    class _Job:
        def __call__(self):
            return None

    assert rot.describe_work(_Job(), _Host()) == "_Host._Job"


def test_worker_context_prefers_context_then_type_name():
    class _W:
        _context = "Host.load_decks"

    class _Bare:
        pass

    assert rot.worker_context(_W()) == "Host.load_decks"
    assert rot.worker_context(_Bare()) == "_Bare"


def test_run_off_thread_names_the_dispatch_in_the_start_receipt(qtbot, caplog):
    """The worker's start receipt names parent + work, not ``SingleCallWorker``."""
    parent = _Host()
    received: list = []

    def load_decks():
        return 1

    with caplog.at_level(logging.INFO, logger="anki_miner.gui.workers.base_worker"):
        run_off_thread(parent, load_decks, received.append)
        qtbot.waitUntil(lambda: bool(received), timeout=3000)
        qtbot.waitUntil(lambda: not parent._off_thread_workers, timeout=3000)

    starts = [r.getMessage() for r in caplog.records if "started" in r.getMessage()]
    assert starts == ["_Host.load_decks started:"]


def test_run_off_thread_rejected_dispatch_warns_with_identity(qtbot, caplog):
    """A dispatch after shutdown is a WARNING naming the parent and the work."""
    parent = _Host()
    rot.close_off_thread_dispatch(parent)

    with caplog.at_level(logging.DEBUG, logger="anki_miner.gui.utils.run_off_thread"):
        worker = run_off_thread(parent, lambda: None, lambda _v: None)

    assert worker.is_cancelled
    records = [r for r in caplog.records if "dispatch rejected" in r.getMessage()]
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    assert records[0].getMessage() == ("Off-thread dispatch rejected during shutdown: parent=_Host work=<lambda>")


def test_run_off_thread_no_handler_debug_carries_the_context(qtbot, caplog):
    parent = _Host()

    def load_decks():
        raise ValueError("nope")

    with caplog.at_level(logging.DEBUG, logger="anki_miner.gui.utils.run_off_thread"):
        run_off_thread(parent, load_decks, lambda _v: None)
        qtbot.waitUntil(lambda: not parent._off_thread_workers, timeout=3000)

    messages = [r.getMessage() for r in caplog.records if "no handler" in r.getMessage()]
    assert len(messages) == 1
    assert "_Host.load_decks" in messages[0]
    assert "nope" in messages[0]


def test_join_all_off_thread_workers_warns_once_about_laggards(qtbot, caplog):
    """Laggards produce exactly one WARNING naming the count and the workers."""
    parent = _Host()
    laggard = _SleepWorker(5.0, respect_cancel=False, parent=parent)
    laggard._context = "_Host.load_decks"
    rot._LIVE_OFF_THREAD_WORKERS.add(laggard)
    laggard.start()
    qtbot.waitUntil(lambda: laggard.isRunning(), timeout=2000)

    try:
        with caplog.at_level(logging.DEBUG, logger="anki_miner.gui.utils.run_off_thread"):
            assert join_all_off_thread_workers(timeout_ms=0) == [laggard]
        records = [r for r in caplog.records if "still running" in r.getMessage()]
        assert len(records) == 1
        assert records[0].levelno == logging.WARNING
        assert "count=1" in records[0].getMessage()
        assert "_Host.load_decks" in records[0].getMessage()
    finally:
        assert laggard.wait(7000)
        rot._LIVE_OFF_THREAD_WORKERS.discard(laggard)


def test_join_all_off_thread_workers_no_laggards_writes_no_warning(qtbot, caplog):
    with caplog.at_level(logging.DEBUG, logger="anki_miner.gui.utils.run_off_thread"):
        assert join_all_off_thread_workers(timeout_ms=50) == []
    assert [r for r in caplog.records if "still running" in r.getMessage()] == []


def test_join_tracked_workers_warns_once_about_laggards(qtbot, caplog):
    parent = _Host()
    laggard = _SleepWorker(5.0, respect_cancel=False, parent=parent)
    parent._off_thread_workers = {laggard}
    laggard.start()
    qtbot.waitUntil(lambda: laggard.isRunning(), timeout=2000)

    try:
        with caplog.at_level(logging.DEBUG, logger="anki_miner.gui.utils.run_off_thread"):
            assert join_tracked_workers(parent, timeout_ms=0) == [laggard]
        records = [r for r in caplog.records if "still running" in r.getMessage()]
        assert len(records) == 1
        assert records[0].levelno == logging.WARNING
        assert "count=1" in records[0].getMessage()
        assert "_SleepWorker" in records[0].getMessage()
    finally:
        assert laggard.wait(7000)


def test_deleted_worker_discard_logs_at_debug(caplog):
    """A RuntimeError-raising wrapper is dropped with a DEBUG breadcrumb."""

    class _Dead:
        _context = "_Host.load_decks"

        def isRunning(self):  # noqa: N802 — Qt API name
            raise RuntimeError("wrapped C/C++ object has been deleted")

    dead = _Dead()
    parent = _Host()
    parent._off_thread_workers = {dead}
    rot._LIVE_OFF_THREAD_WORKERS.add(dead)

    with caplog.at_level(logging.DEBUG, logger="anki_miner.gui.utils.run_off_thread"):
        assert join_tracked_workers(parent, timeout_ms=0) == []
        assert join_all_off_thread_workers(timeout_ms=0) == []

    messages = [r.getMessage() for r in caplog.records if "already deleted" in r.getMessage()]
    assert messages == [
        "Off-thread worker already deleted: context=_Host.load_decks",
        "Off-thread worker already deleted: context=_Host.load_decks",
    ]
