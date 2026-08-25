"""Tests for MiningTabBase.shutdown() and the broadened background_tasks gate (OVH-003).

MiningTabBase.shutdown() must cancel any open curation dialog and poison the
curation gate so a parked worker can fall through and the close-join completes
without deadlocking.

background_tasks.BackgroundTaskController.shutdown() must call tab.shutdown()
for ANY tab exposing the method (duck-typed hasattr), not just YouTube/Audiobook.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import QThread
from PyQt6.QtWidgets import QTabWidget

from anki_miner.gui.widgets._mining_tab_base import MiningTabBase
from anki_miner.gui.widgets._queue_mining_tab_base import _QueueMiningTabBase

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Bare(MiningTabBase):
    """Minimal MiningTabBase subclass sufficient for shutdown tests."""

    config = None

    def _commit_known_words(self, forms):
        return 0


class _CurationWorker(QThread):
    """Worker that calls _curation_bridge and stores the result."""

    def __init__(self, tab: MiningTabBase, words: list) -> None:
        super().__init__()
        self._tab = tab
        self._words = words
        self.result = None

    def run(self) -> None:
        self.result = self._tab._curation_bridge(self._words)


class _LaggardWorker(QThread):
    """Real QThread that ignores cancellation until the test releases it."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()
        self.cancel_calls = 0

    def cancel(self) -> None:
        self.cancel_calls += 1

    def run(self) -> None:
        self.entered.set()
        self.release.wait()


class _BareQueue(_QueueMiningTabBase):
    """Minimal queue tab used to drive the real shutdown implementation."""


def _drain_until(predicate, timeout_ms: int = 3000, step_ms: int = 10) -> bool:
    from PyQt6.QtTest import QTest

    waited = 0
    while not predicate() and waited < timeout_ms:
        QTest.qWait(step_ms)
        waited += step_ms
    return predicate()


# ---------------------------------------------------------------------------
# MiningTabBase.shutdown() unit tests
# ---------------------------------------------------------------------------


class TestMiningTabBaseShutdown:
    """MiningTabBase.shutdown() cancels dialog + poisons gate."""

    def test_shutdown_poisons_gate(self, qapp, qtbot):
        tab = _Bare()
        qtbot.addWidget(tab)
        tab._init_curation_bridge()

        tab.shutdown()

        assert tab._curation_gate_poisoned

    def test_shutdown_releases_parked_worker(self, qapp, qtbot):
        """A worker parked in _curation_event.wait() must be released by shutdown()."""
        tab = _Bare()
        qtbot.addWidget(tab)
        tab._init_curation_bridge()

        # Use DirectConnection to detect when the worker reaches the gate emit
        # without spinning the event loop (which would deliver the queued slot).
        reached_gate = threading.Event()
        tab._curation_requested.connect(
            lambda words: reached_gate.set(),
            (
                type(tab._curation_requested).DirectConnection
                if False
                else __import__("PyQt6.QtCore", fromlist=["Qt"]).Qt.ConnectionType.DirectConnection
            ),
        )

        worker = _CurationWorker(tab, ["word"])
        worker.start()
        assert reached_gate.wait(2.0), "worker never emitted the curation request"
        time.sleep(0.05)  # let it advance into _curation_event.wait()
        assert not worker.isFinished(), "worker should be parked at the curation gate"

        tab.shutdown()

        assert worker.wait(3000), "shutdown() did not release the parked worker"
        assert worker.result is None  # cancelled → None

    def test_shutdown_rejects_open_dialog(self, qapp, qtbot):
        """shutdown() must force-reject an open curation dialog.

        Forced, not plain: the curator refuses a normal reject while a staged
        Known Words write is in flight (D34-B), and a shutdown that respected
        that refusal would leave the parked worker unreleased forever.
        """
        tab = _Bare()
        qtbot.addWidget(tab)
        tab._init_curation_bridge()

        dialog = MagicMock()
        tab._active_curation_dialog = dialog

        tab.shutdown()

        dialog.force_reject.assert_called_once()

    def test_shutdown_idempotent_when_no_dialog(self, qapp, qtbot):
        """shutdown() must not raise when no dialog is open."""
        tab = _Bare()
        qtbot.addWidget(tab)
        tab._init_curation_bridge()

        # Must not raise
        tab.shutdown()
        tab.shutdown()

    def test_shutdown_sets_curation_cancelled(self, qapp, qtbot):
        """shutdown() must set _curation_cancelled so a pre-dialog cancel is remembered."""
        tab = _Bare()
        qtbot.addWidget(tab)
        tab._init_curation_bridge()

        tab.shutdown()

        assert tab._curation_cancelled

    def test_shutdown_reaps_finished_leaked_run(self, qapp, qtbot):
        """shutdown() sweeps a finished leaked run, closing its processor (Fix 2)."""
        tab = _Bare()
        qtbot.addWidget(tab)
        tab._init_curation_bridge()

        proc = MagicMock(name="processor")
        proc.close = MagicMock()
        leaked = MagicMock(name="leaked_worker")
        leaked.isRunning.return_value = False
        leaked.wait.return_value = True
        tab._leaked_runs = [(leaked, proc)]

        tab.shutdown()

        proc.close.assert_called_once()
        assert (leaked, proc) not in tab._leaked_runs

    def test_shutdown_bounded_joins_still_running_leaked_run(self, qapp, qtbot):
        """A still-running leaked worker is bounded-joined then closed at shutdown."""
        tab = _Bare()
        qtbot.addWidget(tab)
        tab._init_curation_bridge()

        proc = MagicMock(name="processor")
        proc.close = MagicMock()
        leaked = MagicMock(name="leaked_worker")
        leaked.isRunning.return_value = True
        leaked.cancel = MagicMock()

        # wait(0) (the reaper probe) reports still-running; the bounded join in
        # shutdown's loop (wait(_LEAKED_RUN_CLOSE_JOIN_MS)) succeeds.
        def _wait(timeout_ms):
            return timeout_ms != 0

        leaked.wait.side_effect = _wait
        tab._leaked_runs = [(leaked, proc)]

        tab.shutdown()

        leaked.cancel.assert_called_once()
        proc.close.assert_called_once()
        assert (leaked, proc) not in tab._leaked_runs


# ---------------------------------------------------------------------------
# YouTube/Audiobook shutdown() still poisons the gate (regression guard)
# ---------------------------------------------------------------------------


class TestYouTubeAudiobookShutdownStillPoison:
    """YouTube/Audiobook overrides must still poison the curation gate."""

    def test_youtube_shutdown_poisons_gate(self, qapp, qtbot):
        """YouTubeTab.shutdown() must call _poison_curation_gate() when worker_thread is set."""
        from anki_miner.gui.widgets.youtube_tab import YouTubeTab

        fake_worker = MagicMock()
        fake_worker.cancel = MagicMock()
        fake_worker.quit = MagicMock()
        fake_worker.wait.return_value = True

        poison_calls: list[bool] = []
        tab = MagicMock(spec=YouTubeTab)
        tab.worker_thread = fake_worker
        tab._poison_curation_gate = lambda: poison_calls.append(True)
        tab._cancel_active_curation_dialog = MagicMock()
        tab._add_flow = MagicMock()

        YouTubeTab.shutdown(tab)

        assert poison_calls, "YouTubeTab.shutdown must poison curation gate when worker_thread is set"

    def test_audiobook_shutdown_poisons_gate(self, qapp, qtbot):
        """AudiobookTab.shutdown() must call _poison_curation_gate()."""
        from anki_miner.gui.widgets.audiobook_tab import AudiobookTab

        fake_worker = MagicMock()
        fake_worker.cancel = MagicMock()
        fake_worker.quit = MagicMock()
        fake_worker.wait.return_value = True

        poison_calls: list[bool] = []
        tab = MagicMock(spec=AudiobookTab)
        tab.worker_thread = fake_worker
        tab._poison_curation_gate = lambda: poison_calls.append(True)
        tab._cancel_active_curation_dialog = MagicMock()

        AudiobookTab.shutdown(tab)

        assert poison_calls, "AudiobookTab.shutdown must poison curation gate"

    def test_reading_manga_shutdown_poisons_gate(self, qapp, qtbot):
        """ReadingMangaTab.shutdown() (inherited from the base) must poison the gate.

        The container ReadingTab has no worker of its own — it fans shutdown out
        to the two sub-tabs, which inherit ``_ReadingMiningTabBase.shutdown``. So
        the poison-gate contract is verified on each concrete sub-tab.
        """
        from anki_miner.gui.widgets.reading_manga_tab import ReadingMangaTab

        fake_worker = MagicMock()
        fake_worker.cancel = MagicMock()
        fake_worker.quit = MagicMock()
        fake_worker.wait.return_value = True

        poison_calls: list[bool] = []
        tab = MagicMock(spec=ReadingMangaTab)
        tab.worker_thread = fake_worker
        tab._poison_curation_gate = lambda: poison_calls.append(True)
        tab._cancel_active_curation_dialog = MagicMock()

        ReadingMangaTab.shutdown(tab)

        assert poison_calls, "ReadingMangaTab.shutdown must poison curation gate when worker_thread is set"

    def test_reading_novels_shutdown_poisons_gate(self, qapp, qtbot):
        """ReadingNovelsTab.shutdown() (inherited from the base) must poison the gate."""
        from anki_miner.gui.widgets.reading_novels_tab import ReadingNovelsTab

        fake_worker = MagicMock()
        fake_worker.cancel = MagicMock()
        fake_worker.quit = MagicMock()
        fake_worker.wait.return_value = True

        poison_calls: list[bool] = []
        tab = MagicMock(spec=ReadingNovelsTab)
        tab.worker_thread = fake_worker
        tab._poison_curation_gate = lambda: poison_calls.append(True)
        tab._cancel_active_curation_dialog = MagicMock()

        ReadingNovelsTab.shutdown(tab)

        assert poison_calls, "ReadingNovelsTab.shutdown must poison curation gate when worker_thread is set"


# ---------------------------------------------------------------------------
# Close-grace budget: a stuck queue worker must not hold the GUI thread
# ---------------------------------------------------------------------------


class TestQueueShutdownCloseBudget:
    """``_QueueMiningTabBase.shutdown()`` is bounded by the close-grace budget.

    The tab close join used to be a 30 s bound per queue tab (the Reading
    container fans out to four children, so ~4x that at app close). It must now
    return within the same grace the window close-join sweep uses, handing the
    still-running worker to the deferred-close reaper instead of blocking.
    """

    def test_shutdown_wait_matches_close_join_grace(self):
        """The tab's join bound is the window close-grace budget, not a longer one."""
        from anki_miner.gui.controllers.background_tasks import _CLOSE_JOIN_GRACE_MS
        from anki_miner.gui.widgets._queue_mining_tab_base import _SHUTDOWN_WAIT_MS

        assert _SHUTDOWN_WAIT_MS == _CLOSE_JOIN_GRACE_MS

    def test_stuck_worker_shutdown_returns_promptly_and_retains_laggard(self, qtbot, test_config):
        """A worker that never finishes is cancelled, retained, and left discoverable.

        Wall-clock ceiling is deliberately generous (10 s, an order of magnitude
        under the 30 s it replaces) so a loaded xdist worker cannot flake it.
        """
        tab = _BareQueue(test_config)
        qtbot.addWidget(tab)
        worker = _LaggardWorker()
        tab.worker_thread = worker
        worker.start()
        try:
            assert worker.entered.wait(2.0), "worker never started"

            started = time.monotonic()
            tab.shutdown()
            elapsed = time.monotonic() - started

            assert elapsed < 10.0, f"shutdown() blocked the GUI thread for {elapsed:.1f}s"
            assert worker.cancel_calls >= 1, "the stuck worker was never cancelled"
            # Retained, and on the attribute background_tasks.shutdown enumerates,
            # so the laggard reaches defer_close instead of being destroyed live.
            assert tab.worker_thread is worker
            assert worker.isRunning()
        finally:
            worker.release.set()
            assert worker.wait(2000)
            tab.worker_thread = None


# ---------------------------------------------------------------------------
# background_tasks.shutdown() duck-typed gate (OVH-003)
# ---------------------------------------------------------------------------


class _FakeTabWithShutdown:
    """Stand-in for a mining tab that has shutdown() but no worker_thread."""

    def __init__(self) -> None:
        self.shutdown_called = False
        # No worker_thread attribute — tests duck-typed tab.shutdown()

    def shutdown(self) -> None:
        self.shutdown_called = True


class _FakeTabWithoutShutdown:
    """Stand-in for a tab that does NOT have shutdown()."""

    def __init__(self) -> None:
        self.shutdown_called = False  # must not be set by BackgroundTaskController


class TestBackgroundTasksShutdownDuckTyped:
    """BackgroundTaskController.shutdown() calls tab.shutdown() via hasattr, not isinstance."""

    @pytest.fixture
    def controller(self, monkeypatch):
        """BackgroundTaskController with all window-level workers set to None."""
        from anki_miner.gui.controllers.background_tasks import BackgroundTaskController

        ctrl = MagicMock(spec=BackgroundTaskController)
        # Bind the real shutdown method
        ctrl.shutdown = BackgroundTaskController.shutdown.__get__(ctrl)
        ctrl.validation_worker = None
        ctrl.update_worker = None
        ctrl.ytdlp_update_worker = None
        ctrl.jmdict_migration_worker = None
        ctrl.asr_model_download_worker = None
        ctrl.alass_install_worker = None
        ctrl.cuda_pack_download_worker = None
        ctrl.onnx_pack_download_worker = None
        ctrl.vulkan_model_download_worker = None
        ctrl.restyle_cards_worker = None
        ctrl.resource_download_worker = None
        ctrl.prewarm_worker = None
        ctrl._join_worker_for_close = MagicMock(return_value=True)
        return ctrl

    def test_shutdown_calls_tab_shutdown_via_duck_type(self, controller):
        """Any tab with a shutdown() method must have it called."""
        tab = _FakeTabWithShutdown()
        tabs = MagicMock(spec=QTabWidget)
        tabs.count.return_value = 1
        tabs.widget.return_value = tab

        controller.shutdown(tabs)

        assert tab.shutdown_called

    def test_shutdown_skips_tabs_without_shutdown(self, controller):
        """Tabs lacking shutdown() must not trigger AttributeError."""
        tab = _FakeTabWithoutShutdown()
        tabs = MagicMock(spec=QTabWidget)
        tabs.count.return_value = 1
        tabs.widget.return_value = tab

        controller.shutdown(tabs)  # must not raise

        # The attribute was never set to True by the controller
        assert not tab.shutdown_called

    def test_queue_timeout_worker_is_returned_for_deferred_close(self, controller, qtbot, test_config, monkeypatch):
        from anki_miner.gui.utils.run_off_thread import join_or_retain

        tab = _BareQueue(test_config)
        qtbot.addWidget(tab)
        worker = _LaggardWorker()
        tab.worker_thread = worker
        worker.start()
        monkeypatch.setattr("anki_miner.gui.widgets._queue_mining_tab_base._SHUTDOWN_WAIT_MS", 10)
        monkeypatch.setattr(
            "anki_miner.gui.controllers.background_tasks.join_all_off_thread_workers",
            lambda timeout_ms=2000: [],
        )
        controller._join_worker_for_close.side_effect = lambda candidate, *, timeout_ms: (
            join_or_retain(candidate, 10) is None
        )
        tabs = MagicMock(spec=QTabWidget)
        tabs.count.return_value = 1
        tabs.widget.return_value = tab

        try:
            assert worker.entered.wait(1.0)
            laggards = controller.shutdown(tabs)
            assert tab.worker_thread is worker
            assert laggards == [worker]
        finally:
            worker.release.set()
            assert worker.wait(1000)
            tab.worker_thread = None

    def test_single_episode_tab_shutdown_called(self, controller, qapp, qtbot):
        """SingleEpisodeTab inherits MiningTabBase.shutdown() — must be called."""
        from anki_miner.gui.widgets.single_episode_tab import SingleEpisodeTab

        class _FakeSingleTab(SingleEpisodeTab):
            def __init__(self) -> None:
                from PyQt6.QtWidgets import QWidget

                QWidget.__init__(self)
                self._init_curation_bridge()
                self.worker_thread = None
                self.shutdown_called = False

            def shutdown(self) -> None:
                self.shutdown_called = True
                super().shutdown()

        tab = _FakeSingleTab()
        qtbot.addWidget(tab)

        tabs = MagicMock(spec=QTabWidget)
        tabs.count.return_value = 1
        tabs.widget.return_value = tab

        controller.shutdown(tabs)

        assert tab.shutdown_called

    def test_batch_tab_shutdown_called(self, controller, qapp, qtbot):
        """BatchProcessingTab inherits MiningTabBase.shutdown() — must be called."""
        from anki_miner.gui.widgets.batch_processing_tab import BatchProcessingTab

        class _FakeBatchTab(BatchProcessingTab):
            def __init__(self) -> None:
                from PyQt6.QtWidgets import QWidget

                QWidget.__init__(self)
                self._init_curation_bridge()
                self.worker_thread = None
                self.shutdown_called = False

            def shutdown(self) -> None:
                self.shutdown_called = True
                super().shutdown()

        tab = _FakeBatchTab()
        qtbot.addWidget(tab)

        tabs = MagicMock(spec=QTabWidget)
        tabs.count.return_value = 1
        tabs.widget.return_value = tab

        controller.shutdown(tabs)

        assert tab.shutdown_called


# ---------------------------------------------------------------------------
# Worker parked at gate is released via shutdown() called from controller
# ---------------------------------------------------------------------------


class TestCurationGatePoisonedByControllerShutdown:
    """Simulate the OVH-003 scenario: worker parked at gate, controller.shutdown() releases it."""

    def test_parked_worker_released_via_shutdown_poison(self, qapp, qtbot):
        """BackgroundTaskController.shutdown calling tab.shutdown() unparks the worker.

        Scenario (OVH-003 for SingleEpisodeTab / BatchProcessingTab):
        1. Worker emits _curation_requested and parks in _curation_event.wait().
        2. GUI thread calls controller.shutdown() — which calls tab.shutdown().
        3. tab.shutdown() poisons the gate → worker unparks with result=None.
        """
        tab = _Bare()
        qtbot.addWidget(tab)
        tab._init_curation_bridge()
        tab.worker_thread = None  # no main process worker

        from PyQt6.QtCore import Qt

        reached_gate = threading.Event()
        tab._curation_requested.connect(
            lambda words: reached_gate.set(),
            Qt.ConnectionType.DirectConnection,
        )

        worker = _CurationWorker(tab, ["w1"])
        worker.start()
        assert reached_gate.wait(2.0), "worker never emitted curation request"
        time.sleep(0.05)
        assert not worker.isFinished(), "worker should be parked at gate"

        # Simulate what BackgroundTaskController.shutdown does:
        # join(worker_thread=None) → no-op, then tab.shutdown() → poison
        tab.shutdown()

        assert worker.wait(3000), "parked worker not released after shutdown"
        assert worker.result is None
