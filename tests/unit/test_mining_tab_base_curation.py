"""MiningTabBase curation bridge guards (Issue #60).

The curator is presented with ``show()``, not ``exec()`` (decision D33), so the
fakes here are real ``QDialog`` subclasses: they carry real ``finished`` /
``destroyed`` signals, which is what the tab's resolver listens to. A fake with
only an ``exec()`` method would never resolve and every test would hang at the
gate. The window-lifecycle contract itself lives in
``test_mining_tab_base_curation_window.py``; this file keeps the surrounding
bridge guards.
"""

import contextlib
import threading
import time
from unittest.mock import Mock, patch

from PyQt6.QtCore import Qt, QThread
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog

from anki_miner.gui.widgets._mining_tab_base import MiningTabBase

MODULE = "anki_miner.gui.widgets._mining_tab_base"


class _Bare(MiningTabBase):
    config = None

    def _commit_known_words(self, forms):
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


def _fake_dialog_cls(*, decision="accept", selection=("picked",)):
    """Build a real-``QDialog`` curator stand-in plus its instance list.

    ``decision`` of ``"accept"``/``"reject"`` self-resolves the moment the tab
    shows the window, which is the closest analogue of the old inline ``exec()``
    for tests that only care what the tab does with the answer. ``"wait"``
    leaves it open so the test can drive Cancel/close/shutdown itself.
    """
    created: list = []

    class _FakeCurationDialog(QDialog):
        def __init__(self, words, parent=None, **kwargs):
            super().__init__(parent)
            self.words = list(words)
            self.kwargs = kwargs
            self.shown_on = None
            self.deleted_later = False
            created.append(self)

        def show(self):
            self.shown_on = QThread.currentThread()
            super().show()
            if decision == "accept":
                self.accept()
            elif decision == "reject":
                self.reject()

        def force_reject(self):
            """Stand-in for the real dialog's forced-shutdown path (D34-B)."""
            self.reject()

        def get_selected_words(self):
            return list(selection)

        def deleteLater(self):  # noqa: N802
            self.deleted_later = True
            super().deleteLater()

    return _FakeCurationDialog, created


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

    cls, created = _fake_dialog_cls()
    with patch(f"{MODULE}.WordCurationDialog", cls):
        tab._show_curation_dialog(["w"], None, None, 5)

    assert len(created) == 1
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

    cls, created = _fake_dialog_cls()
    worker = _CurationWorker(tab, ["w1", "w2"])
    with patch(f"{MODULE}.WordCurationDialog", cls):
        worker.start()
        assert _drain_until(worker.isFinished), "worker did not finish — bridge hung"
    worker.wait()

    # Dialog ran on the GUI thread, NOT the worker thread.
    assert created[0].shown_on is qapp.thread()
    assert created[0].shown_on is not worker.thread_obj
    # Worker unblocked and received the GUI's selection.
    assert worker.result == ["picked"]


def test_cancel_during_active_dialog_releases_worker(qapp, qtbot):
    """Cancelling while the window is open must reject it and unblock the worker.

    Non-modal presentation means the GUI thread is free while the curator sits
    open, so the cancel is simply issued from the test once the window exists;
    the worker then resumes with ``None`` — the orchestrator's cancelled-result
    contract (distinct from an empty list, "confirmed with nothing selected").
    """
    tab = _Bare()
    qtbot.addWidget(tab)
    tab._init_curation_bridge()

    cls, created = _fake_dialog_cls(decision="wait")
    worker = _CurationWorker(tab, ["w1"])
    with patch(f"{MODULE}.WordCurationDialog", cls):
        worker.start()
        assert _drain_until(lambda: bool(created)), "curation window never opened"
        assert not worker.isFinished(), "worker released before the user decided"
        tab._cancel_active_curation_dialog()
        assert _drain_until(worker.isFinished), "worker did not finish after cancel"
    worker.wait()

    assert created[0].shown_on is qapp.thread()
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
    """OVH-016: once the decision is in, the window is scheduled for deletion via
    deleteLater() so its Qt widget tree (table, player stack) is freed
    deterministically instead of accumulating per-session until Python GC."""
    tab = _Bare()
    qtbot.addWidget(tab)
    tab._init_curation_bridge()

    cls, created = _fake_dialog_cls(selection=())
    with patch(f"{MODULE}.WordCurationDialog", cls):
        tab._on_curation_requested(["w1"])
        assert _drain_until(lambda: bool(created) and created[0].deleted_later), "deleteLater() not called"

    assert created[0].deleted_later, "deleteLater() was not called on the curation window"


def test_on_curation_requested_schedules_delete_later_on_reject(qapp, qtbot):
    """deleteLater must also be called when the window is rejected (None result)."""
    tab = _Bare()
    qtbot.addWidget(tab)
    tab._init_curation_bridge()

    cls, created = _fake_dialog_cls(decision="reject")
    with patch(f"{MODULE}.WordCurationDialog", cls):
        tab._on_curation_requested(["w1"])
        assert _drain_until(lambda: bool(created) and created[0].deleted_later), "deleteLater() not called"

    assert created[0].deleted_later, "deleteLater() must be called on rejection too"


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

        def _commit_known_words(self, forms):
            return 0

        def _build_curation_context(self):
            build_thread["id"] = threading.get_ident()
            return (None, None)

    tab = _RecordingTab()
    qtbot.addWidget(tab)
    tab._init_curation_bridge()

    cls, created = _fake_dialog_cls(decision="reject")
    with patch(f"{MODULE}.WordCurationDialog", cls):
        tab._on_curation_requested(["w1"])
        assert _drain_until(tab._curation_event.is_set), "event never set"

    # Build ran off the GUI thread; the window was shown ON the GUI thread.
    assert build_thread["id"] != threading.get_ident()
    assert created[0].shown_on is qapp.thread()


def test_cancel_during_off_thread_build_releases_worker_without_dialog(qapp, qtbot):
    """A cancel landing while the context build is in flight must release the
    worker (event set, result None) and NOT pop a dialog.

    The build is held until cancel is signalled, simulating a cancel that
    arrives during the off-thread parse window — the most subtle hang path.
    """
    release_build = threading.Event()

    class _SlowTab(MiningTabBase):
        config = None

        def _commit_known_words(self, forms):
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


def test_reject_cancels_running_worker(qapp, qtbot):
    """A user reject cancels the running worker so the queue loop's between-items
    _cancel_event check stops the run instead of re-popping the curator per item."""
    tab = _Bare()
    qtbot.addWidget(tab)
    tab._init_curation_bridge()
    tab.worker_thread = Mock()  # _Bare has no worker; a queue tab supplies one

    cls, _created = _fake_dialog_cls(decision="reject")
    with patch(f"{MODULE}.WordCurationDialog", cls):
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

    cls, _created = _fake_dialog_cls(selection=())
    with patch(f"{MODULE}.WordCurationDialog", cls):
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

    cls, _created = _fake_dialog_cls(decision="reject")
    with patch(f"{MODULE}.WordCurationDialog", cls):
        tab._show_curation_dialog(["w1"], None, None)  # must not raise

    assert tab._curation_result is None
    assert tab._curation_event.is_set()


def test_make_curation_media_context_parses_the_second_track(test_config, tmp_path):
    from anki_miner.gui.widgets._mining_tab_base import MiningTabBase

    video = tmp_path / "ep.mkv"
    video.write_bytes(b"\x00")
    sub = tmp_path / "ep.srt"
    sub.write_text("1\n00:00:01,000 --> 00:00:03,000\n食べるのテスト\n", encoding="utf-8")
    second = tmp_path / "ep.en.srt"
    second.write_text("1\n00:00:01,000 --> 00:00:03,000\nA test.\n", encoding="utf-8")

    ctx = MiningTabBase._make_curation_media_context(
        test_config, video, sub, 0.0, secondary_subtitle=second, secondary_offset=0.5
    )

    assert ctx is not None
    assert ctx.secondary_entries == [(1.0, 3.0, "A test.")]
    assert ctx.secondary_offset == 0.5
    without = MiningTabBase._make_curation_media_context(test_config, video, sub, 0.0)
    assert without is not None and without.secondary_entries == []

    missing = MiningTabBase._make_curation_media_context(
        test_config, video, sub, 0.0, secondary_subtitle=tmp_path / "gone.srt", secondary_offset=0.5
    )
    assert missing is not None and missing.secondary_entries == []  # the preview survives


def test_make_curation_media_context_decodes_the_second_track_by_detection(test_config, tmp_path):
    """The second track is not in the mining language, so cp1252 (via detection, not
    the ja ladder's cp932) must decode a curly apostrophe correctly."""
    from anki_miner.gui.widgets._mining_tab_base import MiningTabBase

    video = tmp_path / "ep.mkv"
    video.write_bytes(b"\x00")
    sub = tmp_path / "ep.srt"
    sub.write_text("1\n00:00:01,000 --> 00:00:03,000\n食べるのテスト\n", encoding="utf-8")
    second = tmp_path / "ep.en.srt"
    second.write_bytes("1\n00:00:01,000 --> 00:00:03,000\nI don’t know.\n".encode("cp1252"))

    ctx = MiningTabBase._make_curation_media_context(
        test_config, video, sub, 0.0, secondary_subtitle=second, secondary_offset=0.0
    )

    assert ctx is not None
    assert ctx.secondary_entries == [(1.0, 3.0, "I don’t know.")]


class TestStagedKnownWordsGate:
    """D34-B — the staged Known Words write gates the whole curation result.

    These drive the REAL ``WordCurationDialog`` behind a REAL parked worker,
    because the invariant being protected spans both: a failed write must never
    let the card selection reach the pipeline, and it must never release the
    parked worker either — the review stays open so the user can retry.
    """

    def _park(self, tab, qtbot, words):
        """Start a worker at the curation gate and return it with the live dialog."""
        worker = _CurationWorker(tab, words)
        worker.start()
        qtbot.waitUntil(lambda: tab._active_curation_dialog is not None, timeout=5000)
        return worker, tab._active_curation_dialog

    def _tab(self, qtbot, commit):
        tab = _Bare()
        qtbot.addWidget(tab)
        tab._init_curation_bridge()
        tab._commit_known_words = commit
        return tab

    def test_failed_write_leaves_the_gate_closed_and_leaks_no_selection(self, qapp, qtbot, make_tokenized_words):
        calls: list[set] = []

        def commit(forms):
            calls.append(set(forms))
            raise RuntimeError("known-words DB is locked")

        tab = self._tab(qtbot, commit)
        worker, dialog = self._park(tab, qtbot, make_tokenized_words(2))
        try:
            dialog.table.setCurrentCell(0, 0)
            dialog._on_add_to_known()
            dialog.accept()
            qtbot.waitUntil(lambda: dialog.issue_banner().current_issue() is not None, timeout=5000)

            assert calls, "the commit was never attempted"
            assert not tab._curation_event.is_set(), "a failed write released the gate"
            assert not worker.isFinished()
            assert tab._curation_result is None

            # Cancel after the failure is still a clean cancel: None, once.
            dialog.reject()
            assert worker.wait(5000)
            assert worker.result is None
        finally:
            tab.shutdown()
            worker.wait(5000)

    def test_gate_stays_closed_while_the_write_is_in_flight(self, qapp, qtbot, make_tokenized_words):
        """The worker must stay parked for the whole duration of the write.

        Releasing it when Confirm is pressed rather than when the write lands
        would let the pipeline start creating cards while the Known Words rows
        are still unsaved — the exact overlap this staging exists to prevent.
        """
        started = threading.Event()
        release = threading.Event()

        def commit(forms):
            started.set()
            release.wait(5)
            return len(forms)

        tab = self._tab(qtbot, commit)
        worker, dialog = self._park(tab, qtbot, make_tokenized_words(2))
        try:
            dialog.table.setCurrentCell(0, 0)
            dialog._on_add_to_known()
            dialog.accept()

            assert started.wait(5), "the write never started"
            assert not tab._curation_event.is_set(), "the gate opened before the write finished"
            assert not worker.isFinished()
            # Both decisions are refused mid-write, so the user cannot resolve
            # the review into a state neither kept nor discarded.
            assert not dialog.confirm_button.isEnabled()
            assert not dialog.cancel_button.isEnabled()
            dialog.reject()  # refused
            assert not tab._curation_event.is_set()

            release.set()
            assert _drain_until(worker.isFinished, 5000)
            assert worker.result is not None
        finally:
            release.set()
            tab.shutdown()
            worker.wait(5000)

    def test_successful_write_releases_exactly_the_selected_words(self, qapp, qtbot, make_tokenized_words):
        calls: list[set] = []
        tab = self._tab(qtbot, lambda forms: calls.append(set(forms)) or len(forms))
        words = make_tokenized_words(2)
        worker, dialog = self._park(tab, qtbot, words)
        try:
            staged = dialog.table.item(0, 1).text()
            dialog.table.setCurrentCell(0, 0)
            dialog._on_add_to_known()
            dialog.accept()

            # Drained, not waited on: the commit runs off-thread and delivers its
            # result as a queued signal, so blocking the GUI thread here would
            # deadlock the very release being asserted.
            assert _drain_until(worker.isFinished, 5000)
            assert worker.wait(5000)
            assert calls == [{staged}]
            # The staged word is excluded; the other one is the whole result.
            assert worker.result is not None
            assert staged not in {w.mined_form for w in worker.result}
            assert len(worker.result) == len(words) - 1
        finally:
            tab.shutdown()
            worker.wait(5000)

    def test_shutdown_mid_review_discards_the_stage_and_cancels(self, qapp, qtbot, make_tokenized_words):
        calls: list[set] = []
        tab = self._tab(qtbot, lambda forms: calls.append(set(forms)) or len(forms))
        worker, dialog = self._park(tab, qtbot, make_tokenized_words(2))
        try:
            dialog.table.setCurrentCell(0, 0)
            dialog._on_add_to_known()

            tab.shutdown()

            assert worker.wait(5000)
            assert worker.result is None, "shutdown must cancel, not confirm"
            assert calls == [], "a stage that was never confirmed must not be written"
        finally:
            worker.wait(5000)
