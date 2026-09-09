"""The Word Curator's audio prefetch: started with the window, joined before phase 3.

The invariant these pin: the prefetch is CANCELLED on the GUI thread before the
curation gate opens, and JOINED on the mining thread the instant it unparks —
so phase 3's own fetch_expression_audio can never share the run's chained
fetcher with the prefetch.
"""

from __future__ import annotations

import threading
from unittest.mock import Mock, patch

import pytest
from PyQt6.QtCore import QThread
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QDialog

from anki_miner.gui.utils.run_off_thread import join_all_off_thread_workers, still_running
from anki_miner.gui.widgets._mining_tab_base import MiningTabBase
from anki_miner.models import TokenizedWord

MODULE = "anki_miner.gui.widgets._mining_tab_base"


class _Bare(MiningTabBase):
    config = None

    def _commit_known_words(self, forms):
        return 0

    def _restore_buttons(self) -> None:
        pass


class _FakeDialog(QDialog):
    def __init__(self, words, parent=None, **kwargs):
        super().__init__(parent)
        self.words = list(words)
        self.kwargs = kwargs
        self.selection: list = ["picked"]
        self.states: list[tuple[int, bool]] = []

    def set_expression_audio_state(self, index, found):
        self.states.append((index, found))

    def force_reject(self):
        self.reject()

    def get_selected_words(self):
        return self.selection


class _CurationWorker(QThread):
    """Runs ``_curation_bridge`` off the GUI thread, exactly like a mining worker."""

    def __init__(self, tab, words):
        super().__init__()
        self._tab = tab
        self._words = words
        self.result = "<unset>"

    def run(self):
        self.result = self._tab._curation_bridge(self._words)


def _word(lemma="食べる", available=None):
    word = TokenizedWord(
        surface=lemma,
        lemma=lemma,
        reading="たべる",
        sentence=f"{lemma}のテスト",
        start_time=0.0,
        end_time=1.0,
        duration=1.0,
    )
    word.expression_audio_available = available
    return word


def _drain_until(predicate, timeout_ms=3000, step_ms=10):
    waited = 0
    while not predicate() and waited < timeout_ms:
        QTest.qWait(step_ms)
        waited += step_ms
    return predicate()


@pytest.fixture
def tab(qapp, qtbot):
    widget = _Bare()
    qtbot.addWidget(widget)
    widget._init_curation_bridge()
    return widget


def _with_fetch_fn(tab, fetch_fn):
    """Give the tab a worker whose processor exposes ``fetch_fn``."""
    worker = Mock()
    worker.curation_processor = Mock()
    worker.curation_processor.expression_audio_curation_fn = fetch_fn
    tab.worker_thread = worker
    return worker


def _show(tab, words):
    with patch(f"{MODULE}.WordCurationDialog", _FakeDialog):
        tab._show_curation_dialog(list(words), None, None, None)
    return tab._active_curation_dialog


def _prefetch_worker(tab):
    """The dispatched worker behind ``tab._curation_prefetch``, or None."""
    return None if tab._curation_prefetch is None else tab._curation_prefetch[1]


def test_no_fetch_fn_starts_no_prefetch_and_hides_the_column(tab):
    _with_fetch_fn(tab, None)

    dialog = _show(tab, [_word()])

    assert dialog.kwargs["expression_audio_fetch_fn"] is None
    assert tab._curation_prefetch is None


def test_only_unknown_rows_are_queued(tab):
    asked: list[str] = []

    def _fetch(word, cancelled_check=None):
        asked.append(word.lemma)
        return True

    _with_fetch_fn(tab, _fetch)

    dialog = _show(tab, [_word("食べる", True), _word("猫", None), _word("犬", False)])
    assert _drain_until(lambda: len(dialog.states) == 1)
    tab._join_curation_prefetch(tab._curation_live_token)

    assert asked == ["猫"]
    assert dialog.states == [(1, True)]


def test_every_row_already_answered_starts_no_worker(tab):
    _with_fetch_fn(tab, lambda word, _c=None: True)

    _show(tab, [_word("食べる", True), _word("猫", False)])

    assert tab._curation_prefetch is None


def test_resolve_cancels_before_the_gate_opens(tab):
    """The GUI-side release must have cancelled the worker first."""
    entered = threading.Event()
    release = threading.Event()
    cancelled_when_gate_opened: list[bool] = []

    def _fetch(word, cancelled_check=None):
        entered.set()
        release.wait(5)
        return False

    _with_fetch_fn(tab, _fetch)
    dialog = _show(tab, [_word("猫", None)])
    assert entered.wait(5)
    worker = _prefetch_worker(tab)
    assert worker is not None

    dialog.accept()
    cancelled_when_gate_opened.append(worker.is_cancelled)
    release.set()
    assert worker.wait(5000)

    assert cancelled_when_gate_opened == [True]


def test_the_bridge_joins_the_prefetch_before_returning(tab):
    """The mining thread does the waiting — the gate cannot outrun the prefetch.

    The patch is held across the whole drain, not just the show: the bridge
    reaches the window asynchronously (``_curation_requested`` -> a queued
    ``_on_curation_requested`` -> a ``run_off_thread`` context build -> the
    GUI-thread ``_show_curation_dialog``), so a patch scoped to one call would
    let the REAL curator be built over a Mock processor.
    """
    entered = threading.Event()
    release = threading.Event()

    def _fetch(word, cancelled_check=None):
        entered.set()
        release.wait(5)
        return False

    _with_fetch_fn(tab, _fetch)
    with patch(f"{MODULE}.WordCurationDialog", _FakeDialog):
        mining = _CurationWorker(tab, [_word("猫", None)])
        mining.start()
        assert _drain_until(lambda: tab._active_curation_dialog is not None)
        dialog = tab._active_curation_dialog
        assert isinstance(dialog, _FakeDialog)
        assert entered.wait(5)
        prefetch = _prefetch_worker(tab)
        assert prefetch is not None

        dialog.accept()
        release.set()
        assert _drain_until(lambda: mining.isFinished(), timeout_ms=10000)
        mining.wait(5000)

    # still_running(), not isFinished(): run_off_thread's teardown calls
    # deleteLater() on the worker and the drain above pumps the GUI loop, so the
    # wrapper may already be dangling — still_running absorbs that RuntimeError.
    assert not still_running(prefetch)
    assert tab._curation_prefetch is None


def test_a_superseded_bridge_does_not_steal_the_live_run_s_prefetch(tab):
    """A leaked run's bridge unparks late; the token is what stops it."""
    _with_fetch_fn(tab, lambda word, _c=None: False)
    _show(tab, [_word("猫", None)])
    live_token = tab._curation_live_token
    worker = _prefetch_worker(tab)
    assert worker is not None

    tab._join_curation_prefetch(live_token - 1)

    assert tab._curation_prefetch is not None
    assert _prefetch_worker(tab) is worker
    assert not worker.is_cancelled

    tab._join_curation_prefetch(live_token)
    assert tab._curation_prefetch is None


def test_poisoning_the_gate_cancels_the_prefetch(tab):
    entered = threading.Event()
    release = threading.Event()

    def _fetch(word, cancelled_check=None):
        entered.set()
        release.wait(5)
        return False

    _with_fetch_fn(tab, _fetch)
    _show(tab, [_word("猫", None)])
    assert entered.wait(5)
    worker = _prefetch_worker(tab)

    tab._poison_curation_gate()

    assert worker.is_cancelled
    release.set()
    assert worker.wait(5000)


def test_app_close_join_reaps_the_prefetch(tab):
    """No tab-side reaper: the run_off_thread live registry owns the app-close join.

    The drain runs while ``_fetch`` is still blocked, on purpose.
    ``join_or_retain`` returns at its ``if not still_running(worker)`` guard
    BEFORE it ever calls ``cancel()``, so a worker that has already finished is
    reaped without being cancelled — releasing first would make the assertion
    depend on who wins the race.
    """
    entered = threading.Event()
    release = threading.Event()

    def _fetch(word, cancelled_check=None):
        entered.set()
        release.wait(5)
        return False

    _with_fetch_fn(tab, _fetch)
    _show(tab, [_word("猫", None)])
    assert entered.wait(5)
    worker = _prefetch_worker(tab)

    join_all_off_thread_workers(timeout_ms=5000)
    release.set()

    assert worker.is_cancelled
    assert _drain_until(lambda: not still_running(worker))
