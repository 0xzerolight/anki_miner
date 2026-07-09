"""Video container tab — nests Single, Batch, and YouTube as inner tabs.

Wraps :class:`~anki_miner.gui.widgets.single_episode_tab.SingleEpisodeTab`
(Single), :class:`~anki_miner.gui.widgets.batch_processing_tab.BatchProcessingTab`
(Batch), and :class:`~anki_miner.gui.widgets.youtube_tab.YouTubeTab` (YouTube)
inside a single top-level tab so the main tab bar stays uncluttered. This
container only routes config refreshes, shutdown, dictionary-release, and
sub-tab reveal down to the children; each child keeps its own worker lifecycle.

Close contract (differs from ``ReadingTab`` — read before changing):
- ``shutdown()`` fans out to ALL THREE children, each guarded independently:
  an exception raised while stopping one child must not strand another child's
  still-running worker at app close.
- Timing: only YouTube joins its worker inline (``YouTubeTab.shutdown`` bounded
  ``wait()`` at ``_SHUTDOWN_WAIT_MS`` = 30s, and it nulls ``worker_thread`` on
  both join-OK and timeout). Single/Batch inherit the ``MiningTabBase``
  ``shutdown()`` which poisons the curation gate WITHOUT joining — their live
  workers are cancelled+joined later by the controller at
  ``_CLOSE_JOIN_GRACE_MS`` (2s each), so worst-case close is ~30s + 2x2s,
  not 3x30s.
- Ordering: YouTube's inline join runs before Single/Batch workers are
  cancelled. No deadlock — the base ``shutdown()`` gate-poison *unparks* a
  curation-blocked worker rather than cancelling it, so nothing waits on a
  worker that cannot make progress.
- NO ``worker_thread`` attribute on the container, but — unlike ``ReadingTab``
  — it DOES expose ``iter_close_workers()``. Nested, the controller's
  top-level ``getattr(tab, "worker_thread", None)`` probe hits the container
  and yields ``None``, and the Single/Batch base ``shutdown()`` never joins
  their workers; without ``iter_close_workers`` those live workers would be
  destroyed mid-run at close.
  :class:`~anki_miner.gui.controllers.background_tasks.BackgroundTaskController`
  routes each yielded worker through the same cancel → bounded-join →
  laggard-deferral policy they got as top-level tabs. YouTube's worker is
  naturally skipped: its own ``shutdown()`` already joined and nulled it.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PyQt6.QtCore import QCoreApplication
from PyQt6.QtWidgets import QTabWidget, QVBoxLayout, QWidget

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.batch_processing_tab import BatchProcessingTab
from anki_miner.gui.widgets.single_episode_tab import SingleEpisodeTab
from anki_miner.gui.widgets.youtube_tab import YouTubeTab

if TYPE_CHECKING:
    from collections.abc import Iterator

    from PyQt6.QtCore import QThread

    from anki_miner.gui.presenters import GUIPresenter, GUIProgressCallback
    from anki_miner.services.youtube_fetcher import YouTubeFetcherService

logger = logging.getLogger(__name__)


class VideoTab(QWidget):
    """Container tab holding Single, Batch, and YouTube as inner tabs.

    The class name is load-bearing: ``main_window._MAIN_TAB_CLASSES["video"]``
    resolves this tab by type name, so it must stay ``VideoTab``.

    Unlike ``ReadingTab``, each child gets its OWN presenter (and Single/Batch
    their own progress callback): Single and Batch wire presenter signals into
    their per-tab log widgets, so a shared presenter would cross-post log lines
    between sub-tabs.

    Close contract: see the module docstring — ``shutdown()`` fan-out plus
    ``iter_close_workers()``, and deliberately no ``worker_thread`` attribute.

    Args:
        config: Frozen application configuration.
        episode_presenter: Presenter owned by the Single sub-tab.
        episode_progress: Progress callback owned by the Single sub-tab.
        batch_presenter: Presenter owned by the Batch sub-tab.
        batch_progress: Progress callback owned by the Batch sub-tab.
        youtube_presenter: Presenter owned by the YouTube sub-tab.
        youtube_fetcher: YouTube fetcher service handed to the YouTube sub-tab.
        stats_service: Optional ``StatsService`` shared by all children.
        parent: Optional parent widget.
    """

    def __init__(
        self,
        config: AnkiMinerConfig,
        *,
        episode_presenter: GUIPresenter,
        episode_progress: GUIProgressCallback,
        batch_presenter: GUIPresenter,
        batch_progress: GUIProgressCallback,
        youtube_presenter: GUIPresenter,
        youtube_fetcher: YouTubeFetcherService,
        stats_service: object | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.config = config

        self.single_tab = SingleEpisodeTab(
            config,
            episode_presenter,
            episode_progress,
            stats_service=stats_service,
        )
        self.batch_tab = BatchProcessingTab(
            config,
            batch_presenter,
            batch_progress,
            stats_service=stats_service,
        )
        # processor=None: the YouTube tab defers its dictionary-chain build to
        # the first run so the window paints faster (same startup optimization
        # app.py used when the tab was top-level).
        self.youtube_tab = YouTubeTab(
            config=config,
            processor=None,
            fetcher=youtube_fetcher,
            presenter=youtube_presenter,
            stats_service=stats_service,
        )

        self._inner_tabs = QTabWidget()
        self._inner_tabs.addTab(
            self.single_tab,
            QCoreApplication.translate("MainWindow", "Single"),
        )
        self._inner_tabs.addTab(
            self.batch_tab,
            QCoreApplication.translate("MainWindow", "Batch"),
        )
        self._inner_tabs.addTab(
            self.youtube_tab,
            QCoreApplication.translate("MainWindow", "YouTube"),
        )

        # Stable sub-tab keys for reveal_capability (see capabilities.SUBTAB_KEYS).
        self._subtab_index = {"single": 0, "batch": 1, "youtube": 2}

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._inner_tabs)
        self.setLayout(layout)

    # ------------------------------------------------------------------
    # Sub-tab reveal
    # ------------------------------------------------------------------

    def open_subtab(self, key: str) -> None:
        """Switch the inner tab to the one named by ``key``.

        ``key`` is a stable identifier from
        :data:`anki_miner.gui.capabilities.SUBTAB_KEYS` (``"single"``,
        ``"batch"``, ``"youtube"``). Unknown keys are ignored so a stale caller
        can't crash the UI.
        """
        index = self._subtab_index.get(key)
        if index is not None:
            self._inner_tabs.setCurrentIndex(index)

    # ------------------------------------------------------------------
    # Config refresh
    # ------------------------------------------------------------------

    def update_config(self, config: AnkiMinerConfig) -> None:
        """Store a new config and fan it out to all three child tabs."""
        self.config = config
        self.single_tab.update_config(config)
        self.batch_tab.update_config(config)
        self.youtube_tab.update_config(config)

    # ------------------------------------------------------------------
    # Close contract
    # ------------------------------------------------------------------

    def shutdown(self) -> None:
        """Stop all three children, each guarded independently.

        An exception raised stopping one child must not strand another child's
        still-running worker at app close, so each child's ``shutdown`` runs in
        its own ``try``/``except`` (the failure is logged). See the module
        docstring for the timing/ordering contract: only YouTube joins inline;
        Single/Batch workers are joined by the controller via
        :meth:`iter_close_workers`.
        """
        for child in (self.single_tab, self.batch_tab, self.youtube_tab):
            try:
                child.shutdown()
            except Exception:  # noqa: BLE001 - one child must not strand the others
                logger.exception("Video sub-tab shutdown failed")

    def iter_close_workers(self) -> Iterator[QThread]:
        """Yield each child's still-live worker for the controller's close join.

        Called by ``BackgroundTaskController.shutdown`` AFTER :meth:`shutdown`
        has poisoned the curation gates. Single/Batch leave a running
        ``worker_thread`` live (their base ``shutdown()`` never joins); YouTube
        joined and nulled its own, so ``None`` entries are skipped and nothing
        is cancelled twice.
        """
        for child in (self.single_tab, self.batch_tab, self.youtube_tab):
            worker = getattr(child, "worker_thread", None)
            if worker is not None:
                yield worker

    def release_dictionary_resources(self) -> bool:
        """Release cached dictionary handles in all children (no short-circuit).

        Used by Settings → Dictionary Settings → Remove to drop SQLite handles
        before ``rmtree`` (Issue #30). Evaluates ALL children before combining,
        so later children's handles are always released even when an earlier
        one refuses (a run in flight), then returns their ``and``: ``True``
        only when every child released (or had nothing to release).
        """
        released = [
            self.single_tab.release_dictionary_resources(),
            self.batch_tab.release_dictionary_resources(),
            self.youtube_tab.release_dictionary_resources(),
        ]
        return all(released)
