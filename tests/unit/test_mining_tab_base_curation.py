"""MiningTabBase curation bridge guards (Issue #60)."""

import contextlib
from unittest.mock import patch

import pytest
from PyQt6.QtCore import QThread, QTimer
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from anki_miner.gui.widgets._mining_tab_base import MiningTabBase
from anki_miner.gui.widgets.dialogs.word_curation_dialog import WordCurationDialog

MODULE = "anki_miner.gui.widgets._mining_tab_base"


@pytest.fixture
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


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


def test_default_build_curation_context_is_none_none(qapp):
    tab = _Bare()
    assert tab._build_curation_context() == (None, None)


def test_event_set_even_if_dialog_construction_raises(qapp):
    tab = _Bare()
    tab._init_curation_bridge()
    with (
        patch(
            "anki_miner.gui.widgets._mining_tab_base.WordCurationDialog",
            side_effect=RuntimeError("boom"),
        ),
        contextlib.suppress(RuntimeError),
    ):
        tab._on_curation_requested([])
    assert tab._curation_event.is_set()


def test_curation_bridge_delivers_dialog_on_gui_thread(qapp):
    """A worker-thread _curation_bridge emit must run the dialog on the GUI thread.

    This is the highest-risk contract of Issue #60: the popup must NOT be touched
    from the worker thread. Drives a real QThread + real queued signal delivery
    rather than calling the slot directly, so a regression to a direct
    (same-thread) call would surface here.
    """
    tab = _Bare()
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


def test_cancel_during_active_dialog_releases_worker(qapp):
    """Cancelling while the dialog is open must reject it and unblock the worker.

    The fake dialog's exec spins the event loop (like a real modal) until a
    scheduled _cancel_active_curation_dialog rejects it; the worker then resumes
    with ``None`` — the orchestrator's cancelled-result contract (distinct from
    an empty list, which means "confirmed with nothing selected").
    """
    tab = _Bare()
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
