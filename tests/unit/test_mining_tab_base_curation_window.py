"""Non-modal curation window lifecycle (decision D33, W5-T3).

The curator used to run through ``QDialog.exec()``: the GUI thread sat in a
nested modal loop while the mining worker parked on ``_curation_event``, and a
``finally`` block released the gate the instant ``exec()`` returned. Presenting
the curator with ``show()`` makes that ``finally`` fire *before the user has
decided anything*, which would cancel every item instantly.

So the release moved into a token-bound, exactly-once resolver connected to
``finished``, with ``destroyed`` as a guarded fallback. These tests pin the one
invariant the mining loop cannot survive losing: **``_curation_event`` is set on
every exit path, exactly once, and never by a superseded run.**

The fakes here are real ``QDialog`` subclasses, so ``finished``, ``destroyed``,
``reject()``, ``close()`` and Esc all behave exactly as they do in production.
"""

from __future__ import annotations

import contextlib
import threading
import time
from unittest.mock import Mock, patch

import pytest
from PyQt6.QtCore import QEvent, Qt, QThread
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QDialog

from anki_miner.gui.widgets._mining_tab_base import MiningTabBase

MODULE = "anki_miner.gui.widgets._mining_tab_base"


class _Bare(MiningTabBase):
    config = None

    def _commit_known_words(self, forms):
        return 0

    def _restore_buttons(self) -> None:  # used by _teardown_previous_run
        pass


class _CurationWorker(QThread):
    """Runs ``_curation_bridge`` off the GUI thread, exactly like a mining worker."""

    def __init__(self, tab, words):
        super().__init__()
        self._tab = tab
        self._words = words
        self.result = "<unset>"

    def run(self):
        self.result = self._tab._curation_bridge(self._words)


def _flush() -> None:
    """Deliver queued signals *and* pending ``deleteLater`` deletions.

    ``processEvents()`` alone never runs ``DeferredDelete`` events, so a
    ``destroyed`` fallback would look dead without the explicit send.
    """
    QApplication.processEvents()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    QApplication.processEvents()


def _drain_until(predicate, timeout_ms: int = 3000, step_ms: int = 10) -> bool:
    waited = 0
    while not predicate() and waited < timeout_ms:
        QTest.qWait(step_ms)
        waited += step_ms
    return predicate()


def _fake_dialog_cls(*, show_raises: bool = False):
    """Build a real-``QDialog`` stand-in plus the list of instances it creates.

    It mirrors ``WordCurationDialog``'s own ``finished -> _stop_player``
    connection made in ``__init__`` so the ordering guarantee (resource teardown
    first, resolver second) is exercised, not assumed.
    """
    created: list = []

    class _FakeCurationDialog(QDialog):
        def __init__(self, words, parent=None, **kwargs):
            super().__init__(parent)
            self.words = list(words)
            self.kwargs = kwargs
            self.selection: list = ["picked"]
            self.events: list[str] = []
            self.finished.connect(lambda _code: self.events.append("stop_player"))
            created.append(self)

        def exec(self):  # pragma: no cover - the assertion is the point
            raise AssertionError("the curator must be shown with show(), never exec()")

        def show(self):
            if show_raises:
                raise RuntimeError("presentation failed")
            super().show()

        def force_reject(self):
            """Stand-in for the real dialog's forced-shutdown path (D34-B)."""
            self.reject()

        def get_selected_words(self):
            self.events.append("get_selected_words")
            return self.selection

    return _FakeCurationDialog, created


def _show(tab, words=("w1",), token=None):
    """Present the curator and return the created fake dialog."""
    cls, created = _fake_dialog_cls()
    with patch(f"{MODULE}.WordCurationDialog", cls):
        tab._show_curation_dialog(list(words), None, None, token)
    assert created, "no dialog was constructed"
    return created[0]


@pytest.fixture
def tab(qapp, qtbot):
    widget = _Bare()
    qtbot.addWidget(widget)
    widget._init_curation_bridge()
    return widget


# ---------------------------------------------------------------------------
# Presentation: a non-modal window, not a nested modal loop
# ---------------------------------------------------------------------------


def test_curator_is_shown_not_exec(tab):
    """show(), never exec(). The fake asserts on exec()."""
    dialog = _show(tab)

    assert dialog.isVisible()


def test_curator_is_non_modal_and_leaves_the_app_usable(tab):
    """Nothing is application- or window-modal while the curator is up."""
    dialog = _show(tab)

    assert dialog.windowModality() == Qt.WindowModality.NonModal
    assert dialog.isModal() is False
    assert QApplication.activeModalWidget() is None
    assert QGuiApplication.modalWindow() is None
    assert tab.isEnabled()


def test_open_curator_is_retained_and_gate_stays_closed(tab):
    """The run waits: the dialog is retained and the worker gate is NOT released."""
    dialog = _show(tab)

    assert tab._active_curation_dialog is dialog
    assert not tab._curation_event.is_set(), "the gate released before the user decided"


# ---------------------------------------------------------------------------
# The event is set on EVERY exit path
# ---------------------------------------------------------------------------


def test_accept_releases_gate_with_the_selection(tab):
    tab.worker_thread = Mock()
    dialog = _show(tab)
    dialog.selection = ["食べる"]

    dialog.accept()

    assert tab._curation_result == ["食べる"]
    assert tab._curation_event.is_set()
    tab.worker_thread.cancel.assert_not_called()


def test_accept_with_empty_selection_is_a_skip_not_a_cancel(tab):
    """[] means "confirmed, nothing selected" — the queue continues."""
    tab.worker_thread = Mock()
    dialog = _show(tab)
    dialog.selection = []

    dialog.accept()

    assert tab._curation_result == []
    assert tab._curation_event.is_set()
    tab.worker_thread.cancel.assert_not_called()


def test_reject_releases_gate_as_cancelled_and_stops_the_run(tab):
    tab.worker_thread = Mock()
    dialog = _show(tab)

    dialog.reject()

    assert tab._curation_result is None
    assert tab._curation_event.is_set()
    assert tab._cancel_requested is True
    tab.worker_thread.cancel.assert_called_once()


def test_window_close_releases_gate_as_cancelled(tab):
    tab.worker_thread = Mock()
    dialog = _show(tab)

    dialog.close()

    assert tab._curation_result is None
    assert tab._curation_event.is_set()
    tab.worker_thread.cancel.assert_called_once()


def test_escape_releases_gate_as_cancelled(tab):
    tab.worker_thread = Mock()
    dialog = _show(tab)

    QTest.keyClick(dialog, Qt.Key.Key_Escape)

    assert tab._curation_result is None
    assert tab._curation_event.is_set()


def test_construction_failure_releases_gate(tab):
    """Nothing will ever emit ``finished``, so the failing frame must release."""
    with (
        patch(f"{MODULE}.WordCurationDialog", side_effect=RuntimeError("boom")),
        contextlib.suppress(RuntimeError),
    ):
        tab._show_curation_dialog(["w1"], None, None)

    assert tab._curation_event.is_set()
    assert tab._curation_result is None
    assert tab._active_curation_dialog is None


def test_presentation_failure_releases_gate_and_discards_the_dialog(tab):
    """A dialog that was built but could not be shown must not be retained."""
    cls, created = _fake_dialog_cls(show_raises=True)
    with patch(f"{MODULE}.WordCurationDialog", cls), contextlib.suppress(RuntimeError):
        tab._show_curation_dialog(["w1"], None, None)

    assert tab._curation_event.is_set()
    assert tab._curation_result is None
    assert tab._active_curation_dialog is None
    dialog = created[0]
    _flush()
    with pytest.raises(RuntimeError):
        dialog.isVisible()  # deleteLater() was scheduled for the orphan


def test_destroyed_without_finished_releases_gate(tab):
    """Guarded fallback: the window went away without ever emitting ``finished``."""
    dialog = _show(tab)

    dialog.deleteLater()  # no accept/reject: only ``destroyed`` will fire
    _flush()

    assert tab._curation_event.is_set()
    assert tab._curation_result is None
    assert tab._active_curation_dialog is None


def test_gate_released_when_the_owning_tab_is_destroyed(qapp):
    """The window can outlive its tab; the fallback must still run on a dead owner.

    ``sip`` raises ``RuntimeError`` from ``__getattr__`` for a missing name on a
    deleted wrapper, which ``getattr(..., default)`` does not swallow — so the
    resolver reached this path and died in the Qt event loop instead of
    releasing the worker.
    """
    from PyQt6 import sip

    owner = _Bare()
    owner._init_curation_bridge()
    gate = owner._curation_event  # captured: the tab is unreachable afterwards
    _show(owner)

    sip.delete(owner)  # destroys the child window without any decision
    _flush()

    assert gate.is_set()


def test_context_build_error_still_presents_and_releases(tab):
    """A failed off-thread context build falls back to a table-only curator."""

    class _FailingBuildTab(_Bare):
        def _build_curation_context(self):
            raise RuntimeError("subtitle parse exploded")

    failing = _FailingBuildTab()
    failing._init_curation_bridge()
    cls, created = _fake_dialog_cls()
    with patch(f"{MODULE}.WordCurationDialog", cls):
        failing._on_curation_requested(["w1"])
        assert _drain_until(lambda: bool(created)), "no table-only curator was presented"
        assert not failing._curation_event.is_set()
        created[0].accept()

    assert created[0].kwargs["media_context"] is None
    assert failing._curation_event.is_set()


# ---------------------------------------------------------------------------
# Exactly-once, token-bound resolution
# ---------------------------------------------------------------------------


def test_resolution_is_idempotent_across_finished_then_destroyed(tab):
    """The normal accept path ends in deleteLater(); its ``destroyed`` must no-op."""
    tab.worker_thread = Mock()
    dialog = _show(tab)
    dialog.accept()
    assert tab._curation_event.is_set()

    # Re-arm as if the next item had parked, then let the deferred delete run.
    tab._curation_event.clear()
    tab._curation_result = ["untouched"]
    _flush()

    assert not tab._curation_event.is_set(), "destroyed re-resolved an already-resolved dialog"
    assert tab._curation_result == ["untouched"]
    tab.worker_thread.cancel.assert_not_called()


def test_second_finished_emission_cannot_overwrite_the_result(tab):
    tab.worker_thread = Mock()
    dialog = _show(tab)
    dialog.selection = ["kept"]
    dialog.accept()
    tab._curation_event.clear()

    dialog.reject()  # a stray second emission from the same dialog

    assert tab._curation_result == ["kept"]
    assert not tab._curation_event.is_set()
    tab.worker_thread.cancel.assert_not_called()


def test_stale_dialog_cannot_resolve_a_later_run(tab):
    """A superseded curator's late callback must not release the live item."""
    tab.worker_thread = Mock()
    stale = _show(tab, ["old"])
    stale.reject()  # run A resolves normally
    tab.worker_thread.reset_mock()

    # Run B parks and presents its own curator.
    tab._curation_event.clear()
    tab._curation_result = None
    tab._curation_cancelled = False
    live = _show(tab, ["new"])

    _flush()  # run A's deleteLater lands here -> destroyed fires with a stale token

    assert not tab._curation_event.is_set(), "a dead run's dialog released the live gate"
    assert tab._active_curation_dialog is live
    tab.worker_thread.cancel.assert_not_called()


def test_result_is_written_before_the_gate_opens(tab):
    """``_curation_bridge`` reads ``_curation_result`` after ``wait()`` returns."""

    class _RecordingEvent(threading.Event):
        def __init__(self, owner):
            super().__init__()
            self._owner = owner
            self.result_at_set: object = "<never set>"

        def set(self):
            self.result_at_set = self._owner._curation_result
            super().set()

    recorder = _RecordingEvent(tab)
    tab._curation_event = recorder
    dialog = _show(tab)
    dialog.selection = ["ordered"]

    dialog.accept()

    assert recorder.result_at_set == ["ordered"]


def test_player_teardown_runs_before_the_resolver(tab):
    """``WordCurationDialog`` connects ``finished -> _stop_player`` in __init__.

    Qt runs direct connections in connection order, so the resolver (connected
    afterwards, in the tab) must never read the selection before the dialog has
    released its mpv core.
    """
    dialog = _show(tab)

    dialog.accept()

    assert dialog.events == ["stop_player", "get_selected_words"]


# ---------------------------------------------------------------------------
# Real parked worker: shutdown, teardown, and the happy path
# ---------------------------------------------------------------------------


def _park_worker(tab, words=("w1",)):
    """Start a real worker, let it park, and return (worker, dialog)."""
    cls, created = _fake_dialog_cls()
    patcher = patch(f"{MODULE}.WordCurationDialog", cls)
    patcher.start()
    worker = _CurationWorker(tab, list(words))
    worker.start()
    assert _drain_until(lambda: bool(created)), "the curator never opened"
    time.sleep(0.05)
    assert not worker.isFinished(), "the worker should still be parked at the gate"
    return worker, created[0], patcher


def test_parked_worker_receives_the_selection_after_accept(tab):
    worker, dialog, patcher = _park_worker(tab)
    try:
        dialog.selection = ["猫"]
        dialog.accept()
        assert _drain_until(worker.isFinished), "the worker was never released"
    finally:
        patcher.stop()
        worker.wait(3000)

    assert worker.result == ["猫"]


def test_shutdown_releases_a_worker_parked_behind_an_open_curator(tab):
    worker, dialog, patcher = _park_worker(tab)
    try:
        tab.shutdown()
        assert worker.wait(3000), "shutdown() did not release the parked worker"
    finally:
        patcher.stop()
        worker.wait(3000)

    assert worker.result is None
    assert tab._active_curation_dialog is None
    assert tab._curation_gate_poisoned


def test_teardown_previous_run_releases_a_worker_parked_behind_the_curator(tab):
    """A failed/abandoned run tears down while the curator is still open."""
    worker, dialog, patcher = _park_worker(tab)
    try:
        worker.cancel = Mock()  # the gate, not cancellation, is the real block
        worker.curation_processor = None
        tab.worker_thread = worker
        tab._teardown_previous_run("test")
        assert worker.wait(3000), "teardown did not release the parked worker"
    finally:
        patcher.stop()
        tab.worker_thread = None
        worker.wait(3000)

    assert worker.result is None
    assert tab._active_curation_dialog is None
