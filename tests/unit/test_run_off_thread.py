"""Tests for the off-GUI-thread execution helpers.

Covers :func:`run_off_thread`, :func:`join_worker` and
:func:`join_tracked_workers` in ``gui/utils/run_off_thread.py``. Unlike the
``SingleCallWorker`` contract tests (which call ``run()`` synchronously), these
exercise the *real* Qt threading lifecycle: workers are ``start()``ed and the
result is delivered back on the GUI thread via queued signals. pytest-qt's
``qtbot`` drives the event loop.
"""

from __future__ import annotations

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


def test_run_off_thread_default_on_error_logs_warning(qtbot, caplog):
    parent = _Sink()

    def _boom():
        raise ValueError("nope")

    with caplog.at_level("WARNING", logger="anki_miner.gui.utils.run_off_thread"):
        run_off_thread(parent, _boom, lambda _v: None)
        qtbot.waitUntil(lambda: not parent._off_thread_workers, timeout=3000)

    assert any("nope" in rec.getMessage() for rec in caplog.records)


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
