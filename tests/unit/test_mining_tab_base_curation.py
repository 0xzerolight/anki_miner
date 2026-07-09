"""MiningTabBase curation bridge guards (Issue #60)."""

import contextlib
import threading
import time
from unittest.mock import Mock, patch

from PyQt6.QtCore import Qt, QThread, QTimer
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from anki_miner.gui.widgets._mining_tab_base import MiningTabBase
from anki_miner.gui.widgets.dialogs.word_curation_dialog import WordCurationDialog

MODULE = "anki_miner.gui.widgets._mining_tab_base"


class _Bare(MiningTabBase):
    config = None

    def _mark_known(self, forms):
        return 0


class _CurationWorker(QThread):
    """Runs ``_curation_bridge`` off the GUI thread to exercise queued delivery."""

    def __init__(self, tab, words):
        super().__init__()
        self._tab = tab
        self._words = words
        self.thread_obj = None
        self.result = None

    def run(self):
        self.thread_obj = QThread.currentThread()
        self.result = self._tab._curation_bridge(self._words)


def _drain_until(predicate, timeout_ms=3000, step_ms=10):
    """Spin the GUI event loop (delivering queued signals) until predicate or timeout."""
    waited = 0
    while not predicate() and waited < timeout_ms:
        QTest.qWait(step_ms)
        waited += step_ms
    return predicate()


def test_default_build_curation_context_is_none_none(qapp, qtbot):
    tab = _Bare()
    qtbot.addWidget(tab)
    assert tab._build_curation_context() == (None, None)


def test_event_set_even_if_dialog_construction_raises(qapp, qtbot):
    tab = _Bare()
    qtbot.addWidget(tab)
    tab._init_curation_bridge()
    # Dialog construction now happens in the GUI-thread _show_curation_dialog
    # callback (the context build is off-thread). The finally must still set the
    # event even when construction raises, or the parked worker hangs forever.
    with (
        patch(
            "anki_miner.gui.widgets._mining_tab_base.WordCurationDialog",
            side_effect=RuntimeError("boom"),
        ),
        contextlib.suppress(RuntimeError),
    ):
        tab._show_curation_dialog([], None, None)
    assert tab._curation_event.is_set()


def test_stale_build_callback_after_new_run_does_not_pop_dialog(qapp, qtbot):
    """M3: a context-build callback from a torn-down run must NOT pop a dialog or
    touch the live run's event once a new run is active."""
    tab = _Bare()
    qtbot.addWidget(tab)
    tab._init_curation_bridge()

    # Run A becomes live (token 1), then is torn down (poison invalidates live).
    tab._curation_token = 1
    tab._curation_live_token = 1
    stale_token = 1
    tab._poison_curation_gate()  # teardown poison: live_token -> 0
    tab._reset_curation_gate()  # re-arm for the next run (clears poison flag)

    # Run B becomes live (token 2), parked waiting on its own (cleared) event.
    tab._curation_event.clear()
    tab._curation_result = None
    tab._curation_cancelled = False
    tab._curation_token = 2
    tab._curation_live_token = 2

    # Run A's stale build finishes and calls back with its now-superseded token.
    with patch(f"{MODULE}.WordCurationDialog") as dlg:
        tab._show_curation_dialog(["stale"], None, None, stale_token)

    dlg.assert_not_called()  # no dialog popped for the dead run
    assert not tab._curation_event.is_set()  # Run B's event left untouched
    assert tab._curation_result is None


def test_matching_token_build_callback_pops_dialog(qapp, qtbot):
    """M3: a build callback whose token matches the live run pops the dialog and
    releases the worker as usual."""
    tab = _Bare()
    qtbot.addWidget(tab)
    tab._init_curation_bridge()
    tab._curation_token = 5
    tab._curation_live_token = 5
    tab._curation_event.clear()

    with patch(f"{MODULE}.WordCurationDialog") as dlg:
        tab._show_curation_dialog(["w"], None, None, 5)

    dlg.assert_called_once()
    assert tab._curation_event.is_set()


def test_curation_bridge_delivers_dialog_on_gui_thread(qapp, qtbot):
    """A worker-thread _curation_bridge emit must run the dialog on the GUI thread.

    This is the highest-risk contract of Issue #60: the popup must NOT be touched
    from the worker thread. Drives a real QThread + real queued signal delivery
    rather than calling the slot directly, so a regression to a direct
    (same-thread) call would surface here.
    """
    tab = _Bare()
    qtbot.addWidget(tab)
    tab._init_curation_bridge()

    recorded = {}

    class _FakeDialog:
        DialogCode = WordCurationDialog.DialogCode

        def __init__(self, *args, **kwargs):
            pass

        def exec(self):
            recorded["dialog_thread"] = QThread.currentThread()
            return WordCurationDialog.DialogCode.Accepted

        def get_selected_words(self):
            return ["picked"]

        def deleteLater(self):  # noqa: N802
            pass

    worker = _CurationWorker(tab, ["w1", "w2"])
    with patch(f"{MODULE}.WordCurationDialog", _FakeDialog):
        worker.start()
        assert _drain_until(worker.isFinished), "worker did not finish — bridge hung"
    worker.wait()

    # Dialog ran on the GUI thread, NOT the worker thread.
    assert recorded["dialog_thread"] is qapp.thread()
    assert recorded["dialog_thread"] is not worker.thread_obj
    # Worker unblocked and received the GUI's selection.
    assert worker.result == ["picked"]


def test_cancel_during_active_dialog_releases_worker(qapp, qtbot):
    """Cancelling while the dialog is open must reject it and unblock the worker.

    The fake dialog's exec spins the event loop (like a real modal) until a
    scheduled _cancel_active_curation_dialog rejects it; the worker then resumes
    with ``None`` — the orchestrator's cancelled-result contract (distinct from
    an empty list, which means "confirmed with nothing selected").
    """
    tab = _Bare()
    qtbot.addWidget(tab)
    tab._init_curation_bridge()

    state = {"rejected": False, "thread": None}

    class _BlockingFakeDialog:
        DialogCode = WordCurationDialog.DialogCode

        def __init__(self, *args, **kwargs):
            pass

        def exec(self):
            state["thread"] = QThread.currentThread()
            while not state["rejected"]:
                QApplication.processEvents()
            return WordCurationDialog.DialogCode.Rejected

        def reject(self):
            state["rejected"] = True

        def get_selected_words(self):  # pragma: no cover - not reached on reject
            return ["should-not-be-used"]

        def deleteLater(self):  # noqa: N802
            pass

    worker = _CurationWorker(tab, ["w1"])
    with patch(f"{MODULE}.WordCurationDialog", _BlockingFakeDialog):
        worker.start()
        # Fire the cancel from the GUI thread once the dialog is up (timer is
        # serviced by the exec() processEvents spin).
        QTimer.singleShot(100, tab._cancel_active_curation_dialog)
        assert _drain_until(worker.isFinished), "worker did not finish after cancel"
    worker.wait()

    assert state["thread"] is qapp.thread()
    assert worker.result is None  # cancelled → None (distinct from empty selection)


def test_poison_curation_gate_releases_parked_worker(qapp, qtbot):
    """A worker parked in _curation_event.wait() resumes with None after poisoning.

    Simulates app close: the GUI thread never spins its event loop (no
    _drain_until here), so the queued _curation_requested slot can never run —
    _poison_curation_gate() must release the worker directly (T-01 deadlock fix).
    """
    tab = _Bare()
    qtbot.addWidget(tab)
    tab._init_curation_bridge()

    # DirectConnection probe runs on the worker thread at emit time, right
    # before the bridge parks in _curation_event.wait(). Deliberately no
    # event-loop spin here — processing events would deliver the queued slot
    # and defeat the scenario.
    reached_gate = threading.Event()
    tab._curation_requested.connect(lambda words: reached_gate.set(), Qt.ConnectionType.DirectConnection)

    worker = _CurationWorker(tab, ["w1"])
    worker.start()
    assert reached_gate.wait(2.0), "worker never emitted the curation request"
    time.sleep(0.05)  # let it advance the final step into _curation_event.wait()
    assert not worker.isFinished(), "worker should be parked at the curation gate"

    tab._poison_curation_gate()

    assert worker.wait(3000), "poison did not release the parked worker"
    assert worker.result is None
    # Flush the stale queued signal; the poisoned slot must not pop a dialog.
    with patch(f"{MODULE}.WordCurationDialog") as dialog_cls:
        QApplication.processEvents()
    dialog_cls.assert_not_called()


def test_curation_bridge_after_poison_returns_none_without_blocking(qapp, qtbot):
    """A worker that reaches the gate after poisoning falls through immediately.

    Covers the shutdown race where the worker passes its pipeline cancel
    checkpoint just before cancel is set: it must not clear the event and park
    with nobody left to release it.
    """
    tab = _Bare()
    qtbot.addWidget(tab)
    tab._init_curation_bridge()
    tab._poison_curation_gate()

    emitted: list = []
    tab._curation_requested.connect(lambda words: emitted.append(words))

    assert tab._curation_bridge(["w1"]) is None
    assert emitted == []  # no request signal once the gate is poisoned


def test_on_curation_requested_after_poison_releases_without_dialog(qapp, qtbot):
    """Late slot delivery after poisoning releases the worker, no dialog."""
    tab = _Bare()
    qtbot.addWidget(tab)
    tab._init_curation_bridge()
    tab._curation_event.clear()
    tab._poison_curation_gate()

    with patch(f"{MODULE}.WordCurationDialog") as dialog_cls:
        tab._on_curation_requested(["w1"])

    dialog_cls.assert_not_called()
    assert tab._curation_event.is_set()
    assert tab._curation_result is None


# ---------------------------------------------------------------------------
# OVH-016 — WordCurationDialog deleteLater scheduling
# ---------------------------------------------------------------------------


def test_on_curation_requested_schedules_dialog_delete_later(qapp, qtbot):
    """OVH-016: after exec() the dialog is scheduled for deletion via deleteLater()
    so its Qt widget tree (table, player stack) is freed deterministically instead
    of accumulating per-session until Python GC."""
    tab = _Bare()
    qtbot.addWidget(tab)
    tab._init_curation_bridge()

    delete_later_called = []

    class _FakeDialog:
        DialogCode = WordCurationDialog.DialogCode

        def exec(self):
            return WordCurationDialog.DialogCode.Accepted

        def get_selected_words(self):
            return []

        def deleteLater(self):  # noqa: N802
            delete_later_called.append(True)

    with patch(f"{MODULE}.WordCurationDialog", return_value=_FakeDialog()):
        tab._on_curation_requested(["w1"])
        assert _drain_until(lambda: bool(delete_later_called)), "deleteLater() not called (off-thread)"

    assert delete_later_called, "deleteLater() was not called on the curation dialog"


def test_on_curation_requested_schedules_delete_later_on_reject(qapp, qtbot):
    """deleteLater must also be called when the dialog is rejected (None result)."""
    tab = _Bare()
    qtbot.addWidget(tab)
    tab._init_curation_bridge()

    delete_later_called = []

    class _FakeDialog:
        DialogCode = WordCurationDialog.DialogCode

        def exec(self):
            return WordCurationDialog.DialogCode.Rejected

        def get_selected_words(self):  # pragma: no cover
            return []

        def deleteLater(self):  # noqa: N802
            delete_later_called.append(True)

    with patch(f"{MODULE}.WordCurationDialog", return_value=_FakeDialog()):
        tab._on_curation_requested(["w1"])
        assert _drain_until(lambda: bool(delete_later_called)), "deleteLater() not called (off-thread)"

    assert delete_later_called, "deleteLater() must be called on rejection too"


# ---------------------------------------------------------------------------
# GUI-freeze hardening — curation context build runs off the GUI thread
# ---------------------------------------------------------------------------


def test_build_curation_context_runs_off_gui_thread(qapp, qtbot):
    """_on_curation_requested must build the context (subtitle parse) off-thread.

    A large episode subtitle takes ~1s to parse; parsing it inline on the GUI
    thread freezes the UI while the dialog is being prepared.
    """
    build_thread: dict = {}

    class _RecordingTab(MiningTabBase):
        config = None

        def _mark_known(self, forms):
            return 0

        def _build_curation_context(self):
            build_thread["id"] = threading.get_ident()
            return (None, None)

    tab = _RecordingTab()
    qtbot.addWidget(tab)
    tab._init_curation_bridge()

    dialog_thread: dict = {}

    class _FakeDialog:
        DialogCode = WordCurationDialog.DialogCode

        def __init__(self, *args, **kwargs):
            pass

        def exec(self):
            dialog_thread["id"] = threading.get_ident()
            return WordCurationDialog.DialogCode.Rejected

        def get_selected_words(self):  # pragma: no cover
            return []

        def deleteLater(self):  # noqa: N802
            pass

    with patch(f"{MODULE}.WordCurationDialog", _FakeDialog):
        tab._on_curation_requested(["w1"])
        assert _drain_until(tab._curation_event.is_set), "event never set"

    # Build ran off the GUI thread; the dialog ran ON the GUI thread.
    assert build_thread["id"] != threading.get_ident()
    assert dialog_thread["id"] == threading.get_ident()


def test_cancel_during_off_thread_build_releases_worker_without_dialog(qapp, qtbot):
    """A cancel landing while the context build is in flight must release the
    worker (event set, result None) and NOT pop a dialog.

    The build is held until cancel is signalled, simulating a cancel that
    arrives during the off-thread parse window — the most subtle hang path.
    """
    release_build = threading.Event()

    class _SlowTab(MiningTabBase):
        config = None

        def _mark_known(self, forms):
            return 0

        def _build_curation_context(self):
            # Block until the test cancels, so the cancel lands mid-build.
            release_build.wait(2.0)
            return (None, None)

    tab = _SlowTab()
    qtbot.addWidget(tab)
    tab._init_curation_bridge()

    with patch(f"{MODULE}.WordCurationDialog") as dialog_cls:
        tab._on_curation_requested(["w1"])
        # Cancel arrives while the off-thread build is still parked.
        tab._cancel_active_curation_dialog()
        release_build.set()
        assert _drain_until(tab._curation_event.is_set), "worker not released after cancel"

    dialog_cls.assert_not_called()  # no dialog popped for a cancelled run
    assert tab._curation_result is None  # cancelled → None


# ---------------------------------------------------------------------------
# Curator reject stops the whole run (not just this item)
# ---------------------------------------------------------------------------


def _reject_dialog_cls():
    """A fake WordCurationDialog whose exec() rejects (Cancel / window-X / Esc)."""

    class _RejectDialog:
        DialogCode = WordCurationDialog.DialogCode

        def __init__(self, *args, **kwargs):
            pass

        def exec(self):
            return WordCurationDialog.DialogCode.Rejected

        def get_selected_words(self):  # pragma: no cover - not consumed on reject
            return ["should-not-be-used"]

        def deleteLater(self):  # noqa: N802
            pass

    return _RejectDialog


def test_reject_cancels_running_worker(qapp, qtbot):
    """A user reject cancels the running worker so the queue loop's between-items
    _cancel_event check stops the run instead of re-popping the curator per item."""
    tab = _Bare()
    qtbot.addWidget(tab)
    tab._init_curation_bridge()
    tab.worker_thread = Mock()  # _Bare has no worker; a queue tab supplies one

    with patch(f"{MODULE}.WordCurationDialog", _reject_dialog_cls()):
        tab._show_curation_dialog(["w1"], None, None)

    tab.worker_thread.cancel.assert_called_once()
    assert tab._curation_result is None  # cancelled → None downstream
    assert tab._curation_event.is_set()  # worker still released
    # Reject is a cancel origin: the tab flag must be set so the terminal
    # handler shows "Cancelled" instead of a success summary (result slots
    # are suppressed on cancelled runs).
    assert tab._cancel_requested is True


def test_empty_accept_continues_without_cancel(qapp, qtbot):
    """Confirm-with-nothing-selected ([] ≠ None) is the per-item skip: the worker
    is NOT cancelled and the queue continues. This is the linchpin the fix keeps."""
    tab = _Bare()
    qtbot.addWidget(tab)
    tab._init_curation_bridge()
    tab.worker_thread = Mock()

    class _EmptyAcceptDialog:
        DialogCode = WordCurationDialog.DialogCode

        def __init__(self, *args, **kwargs):
            pass

        def exec(self):
            return WordCurationDialog.DialogCode.Accepted

        def get_selected_words(self):
            return []  # confirmed, nothing selected

        def deleteLater(self):  # noqa: N802
            pass

    with patch(f"{MODULE}.WordCurationDialog", _EmptyAcceptDialog):
        tab._show_curation_dialog(["w1"], None, None)

    tab.worker_thread.cancel.assert_not_called()
    assert tab._curation_result == []  # empty selection, NOT None


def test_reject_without_worker_thread_does_not_raise(qapp, qtbot):
    """Tabs with no worker_thread (base/container/non-mining) must not blow up on
    reject: getattr(...) is None, so the cancel is skipped."""
    tab = _Bare()
    qtbot.addWidget(tab)
    tab._init_curation_bridge()
    assert getattr(tab, "worker_thread", "MISSING") == "MISSING"  # no such attr

    with patch(f"{MODULE}.WordCurationDialog", _reject_dialog_cls()):
        tab._show_curation_dialog(["w1"], None, None)  # must not raise

    assert tab._curation_result is None
    assert tab._curation_event.is_set()
