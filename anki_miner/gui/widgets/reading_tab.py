"""Reading container tab — nests Manga, Novels, Subtitles, and Text inner tabs.

Wraps :class:`~anki_miner.gui.widgets.reading_manga_tab.ReadingMangaTab` (Manga),
:class:`~anki_miner.gui.widgets.reading_novels_tab.ReadingNovelsTab` (Novels),
:class:`~anki_miner.gui.widgets.reading_subtitles_tab.ReadingSubtitlesTab`
(Subtitles), and :class:`~anki_miner.gui.widgets.reading_text_tab.ReadingTextTab`
(Text) inside a single top-level tab so the main tab bar stays uncluttered.
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
  ``_SHUTDOWN_WAIT_MS``, the window close-grace budget, so the container's
  worst-case close is ~4x that rather than the ~4x30s it once was.
- NO ``worker_thread`` attribute, but ``iter_close_workers()`` exposes workers
  retained by children whose bounded join timed out.
  :class:`~anki_miner.gui.controllers.background_tasks.BackgroundTaskController`
  calls ``tab.shutdown()`` first, then routes each retained worker through its
  deferred-close policy instead of destroying the container while it runs.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PyQt6.QtCore import QCoreApplication
from PyQt6.QtWidgets import QTabWidget, QVBoxLayout, QWidget

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.base import install_animated_tab_bar
from anki_miner.gui.widgets.reading_manga_tab import ReadingMangaTab
from anki_miner.gui.widgets.reading_novels_tab import ReadingNovelsTab
from anki_miner.gui.widgets.reading_subtitles_tab import ReadingSubtitlesTab
from anki_miner.gui.widgets.reading_text_tab import ReadingTextTab

if TYPE_CHECKING:
    from collections.abc import Iterator

    from PyQt6.QtCore import QThread

    from anki_miner.interfaces.presenter import PresenterProtocol

logger = logging.getLogger(__name__)


class ReadingTab(QWidget):
    """Container tab holding Manga, Novels, Subtitles, and Text as inner tabs.

    The class name is load-bearing: ``main_window._MAIN_TAB_CLASSES["reading"]``
    resolves this tab by type name, so it must stay ``ReadingTab``.

    One shared presenter is handed to every child — safe because the reading
    sub-tabs never wire presenter signals into their log widgets (presenter
    output goes to the window status bar / dialogs only), so sharing it within
    the container crosses no wires.

    Close contract (see the module docstring): this container exposes
    ``shutdown()`` and ``iter_close_workers()`` but deliberately provides no
    ``worker_thread`` attribute. The controller calls ``shutdown()`` first,
    then defers close for any child worker retained after its 30-second join.

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
        self.text_tab = ReadingTextTab(
            config,
            processor=None,
            presenter=presenter,
            stats_service=stats_service,
        )

        self._inner_tabs = QTabWidget()
        install_animated_tab_bar(self._inner_tabs)
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
            QCoreApplication.translate("MainWindow", "Subtitle Files"),
        )
        self._inner_tabs.addTab(
            self.text_tab,
            QCoreApplication.translate("MainWindow", "Text"),
        )

        # Stable sub-tab keys for reveal_capability (see capabilities.SUBTAB_KEYS).
        self._subtab_index = {"manga": 0, "novels": 1, "subtitles": 2, "text": 3}

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
        :data:`anki_miner.gui.capabilities.SUBTAB_KEYS` (``"manga"``,
        ``"novels"``). Unknown keys are ignored so a stale caller can't crash
        the UI.
        """
        index = self._subtab_index.get(key)
        if index is not None:
            self._inner_tabs.setCurrentIndex(index)

    def current_subtab_key(self) -> str | None:
        """The stable key of the sub-tab on show, or ``None`` if unmappable.

        The inverse of :meth:`open_subtab`, used to persist where the user was
        (D7). Returns the key, never the index or the translated label: the
        index moves when tabs are reordered and the label moves with the UI
        language, and either would resume the wrong screen.
        """
        current = self._inner_tabs.currentIndex()
        for key, index in self._subtab_index.items():
            if index == current:
                return key
        return None

    # ------------------------------------------------------------------
    # Config refresh
    # ------------------------------------------------------------------

    def update_config(self, config: AnkiMinerConfig) -> None:
        """Store a new config and fan it out to every child tab."""
        self.config = config
        self.manga_tab.update_config(config)
        self.novels_tab.update_config(config)
        self.subtitles_tab.update_config(config)
        self.text_tab.update_config(config)

    # ------------------------------------------------------------------
    # Close contract
    # ------------------------------------------------------------------

    def shutdown(self) -> None:
        """Stop every child's worker, each guarded independently.

        An exception raised stopping one child must not strand another child's
        still-running worker at app close, so each child's ``shutdown`` runs in
        its own ``try``/``except`` (the failure is logged). Each child
        bounded-joins its worker at ``_SHUTDOWN_WAIT_MS``, the window
        close-grace budget, so the container's worst-case close is ~4x that;
        children whose join times out are handed to the deferred-close reaper
        via :meth:`iter_close_workers`.
        """
        for child in (self.manga_tab, self.novels_tab, self.subtitles_tab, self.text_tab):
            try:
                child.shutdown()
            except Exception:  # noqa: BLE001 - one child must not strand the others
                logger.exception("Reading sub-tab shutdown failed")

    def iter_close_workers(self) -> Iterator[QThread]:
        """Yield child workers retained after their bounded shutdown joins."""
        for child in (self.manga_tab, self.novels_tab, self.subtitles_tab, self.text_tab):
            worker = getattr(child, "worker_thread", None)
            if worker is not None:
                yield worker

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
        text_released = self.text_tab.release_dictionary_resources()
        return manga_released and novels_released and subtitles_released and text_released
