"""Reading container tab — nests Manga, Novels, and Subtitles as inner tabs.

Wraps :class:`~anki_miner.gui.widgets.reading_manga_tab.ReadingMangaTab` (Manga),
:class:`~anki_miner.gui.widgets.reading_novels_tab.ReadingNovelsTab` (Novels),
and :class:`~anki_miner.gui.widgets.reading_subtitles_tab.ReadingSubtitlesTab`
(Subtitles) inside a single top-level tab so the main tab bar stays uncluttered.
Each child owns its own
:class:`~anki_miner.gui.workers.reading_queue_worker.ReadingQueueWorker`
/ :class:`~anki_miner.orchestration.episode_processor.EpisodeProcessor` lifecycle
via :class:`~anki_miner.gui.widgets._reading_mining_base._ReadingMiningTabBase`;
this container only routes config refreshes, shutdown, and dictionary-release
down to every child.

Close contract:
- ``shutdown()`` fans out to ALL children, each guarded independently: an
  exception raised while stopping one child must not strand another child's
  still-running worker at app close (the same service-all principle as the
  release fan-out). Each child's ``shutdown`` bounded-joins its worker at
  ``_SHUTDOWN_WAIT_MS`` (30s), so the container's worst-case close is ~3x30s.
- NO ``worker_thread`` attribute and NO ``iter_close_workers`` method.
  :class:`~anki_miner.gui.controllers.background_tasks.BackgroundTaskController`
  calls ``tab.shutdown()`` FIRST, which joins (or abandons-with-warning and
  nulls) each child's worker; the subsequent ``getattr(tab, "worker_thread",
  None)`` then yields ``None`` (safe) and a post-shutdown ``iter_close_workers``
  would always be vestigial. (``SubtitlesTab`` needs ``iter_close_workers`` only
  because its children have no ``shutdown()``; the reading children do.)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PyQt6.QtCore import QCoreApplication
from PyQt6.QtWidgets import QTabWidget, QVBoxLayout, QWidget

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.reading_manga_tab import ReadingMangaTab
from anki_miner.gui.widgets.reading_novels_tab import ReadingNovelsTab
from anki_miner.gui.widgets.reading_subtitles_tab import ReadingSubtitlesTab

if TYPE_CHECKING:
    from anki_miner.interfaces.presenter import PresenterProtocol

logger = logging.getLogger(__name__)


class ReadingTab(QWidget):
    """Container tab holding Manga, Novels, and Subtitles as inner tabs.

    The class name is load-bearing: ``main_window._MAIN_TAB_CLASSES["reading"]``
    resolves this tab by type name, so it must stay ``ReadingTab``.

    One shared presenter is handed to every child — safe because the reading
    sub-tabs never wire presenter signals into their log widgets (presenter
    output goes to the window status bar / dialogs only), so sharing it within
    the container crosses no wires.

    Close contract (see the module docstring): this container exposes
    ``shutdown()`` but deliberately provides neither a ``worker_thread``
    attribute nor an ``iter_close_workers`` method. The controller calls
    ``shutdown()`` first, which bounded-joins each child's worker (30s each →
    ~3x30s worst case); a later ``worker_thread`` / ``iter_close_workers`` probe
    would be vestigial.

    Args:
        config: Frozen application configuration.
        presenter: Optional presenter shared by every child.
        stats_service: Optional ``StatsService`` shared by every child.
        parent: Optional parent widget.
    """

    def __init__(
        self,
        config: AnkiMinerConfig,
        presenter: PresenterProtocol | None = None,
        stats_service: object | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.config = config

        # processor=None: each child defers its dictionary-chain build to the
        # first Mine click. One prebuilt processor cannot be shared across
        # concurrently-runnable sub-tabs, so the container never builds one.
        self.manga_tab = ReadingMangaTab(
            config,
            processor=None,
            presenter=presenter,
            stats_service=stats_service,
        )
        self.novels_tab = ReadingNovelsTab(
            config,
            processor=None,
            presenter=presenter,
            stats_service=stats_service,
        )
        self.subtitles_tab = ReadingSubtitlesTab(
            config,
            processor=None,
            presenter=presenter,
            stats_service=stats_service,
        )

        self._inner_tabs = QTabWidget()
        self._inner_tabs.addTab(
            self.manga_tab,
            QCoreApplication.translate("MainWindow", "Manga"),
        )
        self._inner_tabs.addTab(
            self.novels_tab,
            QCoreApplication.translate("MainWindow", "Novels"),
        )
        self._inner_tabs.addTab(
            self.subtitles_tab,
            QCoreApplication.translate("MainWindow", "Subtitles"),
        )

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._inner_tabs)
        self.setLayout(layout)

    # ------------------------------------------------------------------
    # Config refresh
    # ------------------------------------------------------------------

    def update_config(self, config: AnkiMinerConfig) -> None:
        """Store a new config and fan it out to every child tab."""
        self.config = config
        self.manga_tab.update_config(config)
        self.novels_tab.update_config(config)
        self.subtitles_tab.update_config(config)

    # ------------------------------------------------------------------
    # Close contract
    # ------------------------------------------------------------------

    def shutdown(self) -> None:
        """Stop every child's worker, each guarded independently.

        An exception raised stopping one child must not strand another child's
        still-running worker at app close, so each child's ``shutdown`` runs in
        its own ``try``/``except`` (the failure is logged). Each child
        bounded-joins its worker at ``_SHUTDOWN_WAIT_MS`` (30s), so the
        container's worst-case close is ~3x30s.
        """
        for child in (self.manga_tab, self.novels_tab, self.subtitles_tab):
            try:
                child.shutdown()
            except Exception:  # noqa: BLE001 - one child must not strand the others
                logger.exception("Reading sub-tab shutdown failed")

    def release_dictionary_resources(self) -> bool:
        """Release cached dictionary handles in every child (no short-circuit).

        Used by Settings → Dictionary Settings → Remove to drop SQLite handles
        before ``rmtree`` (Issue #30). Evaluates ALL children before combining,
        so later children's handles are always released even when an earlier one
        refuses (a run in flight), then returns their ``and``: ``True`` only when
        all released (or had nothing to release).
        """
        manga_released = self.manga_tab.release_dictionary_resources()
        novels_released = self.novels_tab.release_dictionary_resources()
        subtitles_released = self.subtitles_tab.release_dictionary_resources()
        return manga_released and novels_released and subtitles_released
