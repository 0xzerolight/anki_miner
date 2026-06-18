"""Tests for _teardown_previous_run curation-gate hardening (OVH-081).

_teardown_previous_run must call _cancel_active_curation_dialog() and
_poison_curation_gate() BEFORE the cancel/join so a worker parked in
_curation_event.wait() is released regardless of caller state.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

from PyQt6.QtCore import Qt, QThread

from anki_miner.gui.widgets._mining_tab_base import MiningTabBase


class _Bare(MiningTabBase):
    config = None

    def _mark_known(self, forms):
        return 0

    def _restore_buttons(self) -> None:
        pass


class _CurationWorker(QThread):
    """Worker that calls _curation_bridge and parks until released."""

    def __init__(self, tab: MiningTabBase, words: list) -> None:
        super().__init__()
        self._tab = tab
        self._words = words
        self.result = None

    def run(self) -> None:
        self.result = self._tab._curation_bridge(self._words)


def _drain_until(predicate, timeout_ms: int = 3000, step_ms: int = 10) -> bool:
    from PyQt6.QtTest import QTest

    waited = 0
    while not predicate() and waited < timeout_ms:
        QTest.qWait(step_ms)
        waited += step_ms
    return predicate()


def _fake_worker(*, running: bool = False, wait_result: bool = True, name: str = "w") -> MagicMock:
    w = MagicMock(name=name)
    w.isRunning.return_value = running
    w.cancel = MagicMock()
    w.finished = MagicMock()
    w.curation_processor = None
    w.wait.side_effect = lambda *a: (setattr(w, "_stopped", True) or wait_result)
    return w


# ---------------------------------------------------------------------------
# Poison-before-join ordering
# ---------------------------------------------------------------------------


class TestTeardownPreviousRunPoisonsGate:
    """_teardown_previous_run poisons the curation gate before cancel/join."""

    def test_poison_called_before_cancel(self, qapp, qtbot):
        """Gate must be poisoned before worker.cancel() is called."""
        tab = _Bare()
        qtbot.addWidget(tab)
        tab._init_curation_bridge()

        order: list[str] = []

        tab._poison_curation_gate = lambda: order.append("poison")

        worker = MagicMock(name="w")
        worker.isRunning.return_value = True
        worker.finished = MagicMock()
        worker.curation_processor = None
        worker.cancel.side_effect = lambda: order.append("cancel")
        worker.wait.return_value = True

        tab.worker_thread = worker
        tab._teardown_previous_run("test")

        # Poison must precede cancel
        assert order.index("poison") < order.index("cancel"), f"Expected poison before cancel; got order={order}"

    def test_gate_rearmed_after_teardown(self, qapp, qtbot):
        """Teardown's poison is transient: the gate must be re-armed afterward (F1).

        The poison releases the *previous* run's parked worker, but must not carry
        into the next run — otherwise the 2nd Process mine in a session skips
        curation and silently produces zero cards. Permanent poisoning is reserved
        for shutdown().
        """
        tab = _Bare()
        qtbot.addWidget(tab)
        tab._init_curation_bridge()

        worker = MagicMock(name="w")
        worker.isRunning.return_value = False
        worker.finished = MagicMock()
        worker.curation_processor = None
        worker.wait.return_value = True

        tab.worker_thread = worker
        tab._teardown_previous_run("test")

        # Gate must be re-armed (worker-side check) so the next run can curate.
        assert not tab._curation_gate_poisoned
        # _curation_cancelled is deliberately left set: it guards a stale queued
        # _on_curation_requested from the torn-down worker. The next run's
        # _curation_bridge clears it before emitting.
        assert tab._curation_cancelled

    def test_second_run_curation_not_short_circuited(self, qapp, qtbot):
        """Regression (F1): after a teardown, the next run's _curation_bridge emits.

        Before the fix _teardown_previous_run permanently poisoned the gate, so the
        2nd Process mine's _curation_bridge returned None immediately (no dialog, no
        cards). Here we simulate the start-of-run-2 teardown of a finished run-1
        worker (worker_thread is never nulled after a run), then drive the bridge and
        assert it reaches the emit path with a real selection result.
        """
        tab = _Bare()
        qtbot.addWidget(tab)
        tab._init_curation_bridge()

        # A finished run-1 worker still referenced (never nulled by the finish slots).
        worker = MagicMock(name="w")
        worker.isRunning.return_value = False
        worker.finished = MagicMock()
        worker.curation_processor = None
        worker.wait.return_value = True
        tab.worker_thread = worker

        tab._teardown_previous_run("test")
        assert not tab._curation_gate_poisoned, "gate must be re-armed for the next run"

        # Drive the bridge directly: swap the dialog-exec'ing slot for a stub that
        # records the emission and releases the worker with a selection.
        tab._curation_requested.disconnect(tab._on_curation_requested)
        emitted: list[list] = []

        def _stub(words: list) -> None:
            emitted.append(words)
            tab._curation_result = ["picked"]
            tab._curation_event.set()

        tab._curation_requested.connect(_stub, Qt.ConnectionType.DirectConnection)

        result = tab._curation_bridge(["w1"])

        assert emitted == [["w1"]], "2nd-run curation must emit, not short-circuit to None"
        assert result == ["picked"]

    def test_dialog_cancelled_before_join(self, qapp, qtbot):
        """Any open curation dialog is rejected before the worker join."""
        tab = _Bare()
        qtbot.addWidget(tab)
        tab._init_curation_bridge()

        order: list[str] = []
        dialog = MagicMock()
        dialog.reject.side_effect = lambda: order.append("dialog_reject")
        tab._active_curation_dialog = dialog

        worker = MagicMock(name="w")
        worker.isRunning.return_value = True
        worker.finished = MagicMock()
        worker.curation_processor = None
        worker.cancel.side_effect = lambda: order.append("cancel")
        worker.wait.return_value = True

        tab.worker_thread = worker
        tab._teardown_previous_run("test")

        assert order.index("dialog_reject") < order.index(
            "cancel"
        ), f"Expected dialog reject before cancel; got order={order}"


# ---------------------------------------------------------------------------
# Parked-worker scenario: teardown must not deadlock
# ---------------------------------------------------------------------------


class TestTeardownDoesNotDeadlockWithGateParkedWorker:
    """_teardown_previous_run invoked while a worker is parked in the gate.

    The poison-before-cancel fix ensures the real event is set so the worker
    unparks and the bounded join succeeds.
    """

    def test_parked_worker_unparked_by_teardown(self, qapp, qtbot):
        """Real worker parked at the curation gate is unparked by _teardown_previous_run.

        Uses a _CurationWorker as the parked thread; assigns it to worker_thread
        so _teardown_previous_run sees it and calls cancel()+wait().  The
        poison fired before cancel releases the curation event so wait() returns
        promptly (no deadlock).
        """
        tab = _Bare()
        qtbot.addWidget(tab)
        tab._init_curation_bridge()

        # Park a real curation worker
        reached_gate = threading.Event()
        tab._curation_requested.connect(
            lambda words: reached_gate.set(),
            Qt.ConnectionType.DirectConnection,
        )

        curation_worker = _CurationWorker(tab, ["w1"])
        curation_worker.start()
        assert reached_gate.wait(2.0), "worker never reached the curation gate"
        time.sleep(0.05)  # let it advance into _curation_event.wait()
        assert not curation_worker.isFinished(), "worker should be parked"

        # Assign the curation worker as the tab's worker_thread so teardown sees it
        curation_worker.cancel = MagicMock()  # noop cancel — gate is the real block
        curation_worker.curation_processor = None
        curation_worker.finished = MagicMock()
        tab.worker_thread = curation_worker

        # _teardown_previous_run must poison the gate first, so wait() returns
        tab._teardown_previous_run("test")

        # After teardown, the parked worker must have been released
        assert curation_worker.wait(3000), "parked worker was not released by teardown"
        assert curation_worker.result is None

    def test_none_worker_thread_is_no_op(self, qapp, qtbot):
        """When worker_thread is None, teardown returns without error."""
        tab = _Bare()
        qtbot.addWidget(tab)
        tab._init_curation_bridge()
        tab.worker_thread = None

        # Must not raise
        tab._teardown_previous_run("test")
