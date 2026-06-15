"""Policy tests for the E2E :class:`AutoCurationResponder`.

Drives the REAL curation bridge (``MiningTabBase._curation_bridge`` →
``_on_curation_requested``) through the responder, modelled on
``tests/unit/test_mining_tab_base_curation.py``: a real ``QThread`` calls the
worker-side bridge, the responder patches ``WordCurationDialog`` at its import
site in ``_mining_tab_base``, and we spin the GUI event loop with ``_drain_until``
until the worker returns. This proves the fake's ``DialogCode.Accepted`` equals
the value its ``exec()`` returns in the real slot, and that each policy maps the
offered words to the right selection.

Qt-only (no Anki / no ffmpeg) → default suite, no pytest marker.
"""

from PyQt6.QtCore import QThread
from PyQt6.QtTest import QTest

from anki_miner.gui.widgets._mining_tab_base import MiningTabBase
from tests.e2e.curation import AutoCurationResponder


class _Bare(MiningTabBase):
    """Minimal concrete tab: just enough to exercise the curation bridge.

    Mirrors the ``_Bare`` harness in ``tests/unit/test_mining_tab_base_curation.py``
    (a ``config`` attribute and a stub ``_mark_known``); the real bridge wiring
    comes from ``_init_curation_bridge()``.
    """

    config = None

    def _mark_known(self, forms):  # pragma: no cover - not reached by these tests
        return 0


class _CurationWorker(QThread):
    """Runs ``_curation_bridge`` off the GUI thread to exercise queued delivery."""

    def __init__(self, tab, words):
        super().__init__()
        self._tab = tab
        self._words = words
        self.result = None

    def run(self):
        self.result = self._tab._curation_bridge(self._words)


def _drain_until(predicate, timeout_ms=3000, step_ms=10):
    """Spin the GUI event loop (delivering queued signals) until predicate or timeout."""
    waited = 0
    while not predicate() and waited < timeout_ms:
        QTest.qWait(step_ms)
        waited += step_ms
    return predicate()


def _run_bridge(tab, words):
    """Start a worker through the bridge and return its result once it finishes."""
    worker = _CurationWorker(tab, words)
    worker.start()
    assert _drain_until(worker.isFinished), "worker did not finish — bridge hung"
    worker.wait()
    return worker.result


def test_policy_all_returns_every_offered_word(qapp, qtbot):
    tab = _Bare()
    qtbot.addWidget(tab)
    tab._init_curation_bridge()

    with AutoCurationResponder(policy="all") as responder:
        result = _run_bridge(tab, ["w1", "w2", "w3"])

    assert result == ["w1", "w2", "w3"]
    assert responder.offered == [["w1", "w2", "w3"]]


def test_policy_first_n_returns_prefix(qapp, qtbot):
    tab = _Bare()
    qtbot.addWidget(tab)
    tab._init_curation_bridge()

    with AutoCurationResponder(policy="first_n", first_n=2) as responder:
        result = _run_bridge(tab, ["w1", "w2", "w3"])

    assert result == ["w1", "w2"]
    assert responder.offered == [["w1", "w2", "w3"]]


def test_policy_none_returns_empty_list(qapp, qtbot):
    tab = _Bare()
    qtbot.addWidget(tab)
    tab._init_curation_bridge()

    with AutoCurationResponder(policy="none") as responder:
        result = _run_bridge(tab, ["w1", "w2", "w3"])

    # Empty list = "confirmed with nothing selected" (completed, 0 cards),
    # which is distinct from None (cancelled). The dialog was still accepted.
    assert result == []
    assert responder.offered == [["w1", "w2", "w3"]]


def test_offered_accumulates_across_sessions(qapp, qtbot):
    """Each bridge invocation records its offered list (re-offered words = symptom)."""
    tab = _Bare()
    qtbot.addWidget(tab)
    tab._init_curation_bridge()

    with AutoCurationResponder(policy="all") as responder:
        _run_bridge(tab, ["a", "b"])
        _run_bridge(tab, ["a", "b", "c"])

    assert responder.offered == [["a", "b"], ["a", "b", "c"]]


def test_exit_restores_original_dialog(qapp, qtbot):
    """``__exit__`` must restore the real ``WordCurationDialog`` symbol."""
    import anki_miner.gui.widgets._mining_tab_base as base

    original = base.WordCurationDialog
    with AutoCurationResponder(policy="all"):
        assert base.WordCurationDialog is not original
    assert base.WordCurationDialog is original


def test_exit_restores_on_exception(qapp, qtbot):
    """Patches are torn down even when the body raises."""
    import anki_miner.gui.widgets._mining_tab_base as base

    original = base.WordCurationDialog
    try:
        with AutoCurationResponder(policy="all"):
            assert base.WordCurationDialog is not original
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert base.WordCurationDialog is original


def test_dialogcode_accepted_is_real_enum(qapp, qtbot):
    """The fake's DialogCode.Accepted must be the real enum member.

    The slot compares ``dialog.exec() == WordCurationDialog.DialogCode.Accepted``
    where ``WordCurationDialog`` is the patched fake, so the fake's enum and the
    value returned by ``exec()`` must be identical objects — guaranteed by reusing
    the real enum.
    """
    import anki_miner.gui.widgets._mining_tab_base as base
    from anki_miner.gui.widgets.dialogs.word_curation_dialog import WordCurationDialog as RealDialog

    with AutoCurationResponder(policy="all"):
        fake = base.WordCurationDialog
        assert fake.DialogCode is RealDialog.DialogCode
        inst = fake(["x"], None)
        assert inst.exec() == fake.DialogCode.Accepted
