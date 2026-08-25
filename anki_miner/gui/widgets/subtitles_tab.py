"""Utilities container tab — nests Generate, Retime, Condense, Card Backfill, Deck Filter, Download.

Wraps :class:`~anki_miner.gui.widgets.subtitle_creation_tab.SubtitleCreationTab`
(Generate), :class:`~anki_miner.gui.widgets.subtitle_retime_tab.SubtitleRetimeTab`
(Retime), :class:`~anki_miner.gui.widgets.condense_tab.CondenseTab` (Condense),
:class:`~anki_miner.gui.widgets.backfill_tab.CardBackfillTab` (Card Backfill),
:class:`~anki_miner.gui.widgets.deck_filter_tab.DeckFilterTab` (Deck Filter),
and :class:`~anki_miner.gui.widgets.download_tab.DownloadTab` (Download)
inside a single top-level tab so the main tab bar stays uncluttered.

Close contract:
- ``iter_close_workers()`` fans out to all children so
  :class:`~anki_miner.gui.controllers.background_tasks.BackgroundTaskController`
  can join any child's active worker on app close.
- No ``worker_thread`` attribute: workers are exposed exclusively via
  ``iter_close_workers``; ``background_tasks._collect_close_laggards`` falls
  back to ``getattr(tab, "worker_thread", None)`` → None, which is safe.
- No ``shutdown`` method: no child tab has one, so there is nothing to
  delegate (``getattr`` fallback handles absence in ``_collect_close_laggards``).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Iterator

from PyQt6.QtCore import QCoreApplication
from PyQt6.QtWidgets import QTabWidget, QVBoxLayout, QWidget

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.utils.run_off_thread import still_running
from anki_miner.gui.widgets.backfill_tab import CardBackfillTab
from anki_miner.gui.widgets.base import install_animated_tab_bar
from anki_miner.gui.widgets.condense_tab import CondenseTab
from anki_miner.gui.widgets.deck_filter_tab import DeckFilterTab
from anki_miner.gui.widgets.download_tab import DownloadTab
from anki_miner.gui.widgets.subtitle_creation_tab import SubtitleCreationTab
from anki_miner.gui.widgets.subtitle_retime_tab import SubtitleRetimeTab
from anki_miner.gui.workers.backfill_worker import BackfillScanWorker
from anki_miner.gui.workers.deck_filter_worker import DeckFilterScanWorker

if TYPE_CHECKING:
    from anki_miner.gui.workers.base_worker import CancellableWorker

logger = logging.getLogger(__name__)


class SubtitlesTab(QWidget):
    """Container tab holding Generate, Retime, Condense, Card Backfill inner tabs.

    Args:
        config: Frozen application configuration.
        parent: Optional parent widget.
    """

    def __init__(
        self,
        config: AnkiMinerConfig,
        parent: QWidget | None = None,
        *,
        suppress_optional_startup: bool = False,
    ) -> None:
        super().__init__(parent)
        self.config = config

        self.generate_tab = SubtitleCreationTab(config, suppress_optional_startup=suppress_optional_startup)
        self.retime_tab = SubtitleRetimeTab(config, suppress_optional_startup=suppress_optional_startup)
        self.condense_tab = CondenseTab(config, suppress_optional_startup=suppress_optional_startup)

        self._inner_tabs = QTabWidget()
        install_animated_tab_bar(self._inner_tabs)
        self._inner_tabs.addTab(
            self.generate_tab,
            QCoreApplication.translate("MainWindow", "Generate"),
        )
        self._inner_tabs.addTab(
            self.retime_tab,
            QCoreApplication.translate("MainWindow", "Retime"),
        )
        self._inner_tabs.addTab(
            self.condense_tab,
            QCoreApplication.translate("MainWindow", "Condense"),
        )
        self.backfill_tab = CardBackfillTab(config)
        self._inner_tabs.addTab(
            self.backfill_tab,
            QCoreApplication.translate("MainWindow", "Card Backfill"),
        )
        self.deck_filter_tab = DeckFilterTab(config)
        self._inner_tabs.addTab(
            self.deck_filter_tab,
            QCoreApplication.translate("MainWindow", "Deck Filter"),
        )
        self.download_tab = DownloadTab(config, suppress_optional_startup=suppress_optional_startup)
        self._inner_tabs.addTab(
            self.download_tab,
            QCoreApplication.translate("MainWindow", "Download"),
        )

        # Stable sub-tab keys for reveal_capability (see capabilities.SUBTAB_KEYS).
        self._subtab_index = {
            "generate": 0,
            "retime": 1,
            "condense": 2,
            "backfill": 3,
            "deckfilter": 4,
            "download": 5,
        }

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
        :data:`anki_miner.gui.capabilities.SUBTAB_KEYS` (``"generate"``,
        ``"retime"``, ``"condense"``, ``"backfill"``, ``"deckfilter"``,
        ``"download"``). Unknown keys are ignored so a stale caller can't
        crash the UI.
        """
        index = self._subtab_index.get(key)
        if index is not None:
            logger.debug("Utilities subtab opened: key=%s index=%d", key, index)
            self._inner_tabs.setCurrentIndex(index)

    def open_retime(self, video_path: Path, subtitle_path: Path) -> None:
        """Reveal Retime with a single-file pair already loaded (D35 hand-off).

        The subtitle timing viewer's "Align automatically" ends here: the viewer
        closes, and the user arrives at the aligner with both files in place and
        only the Retime button left to press. Nothing starts by itself.
        """
        self.retime_tab.set_single_inputs(video_path, subtitle_path)
        self.open_subtab("retime")

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
        """Fan out a new config to all child tabs."""
        self.config = config
        self.generate_tab.update_config(config)
        self.retime_tab.update_config(config)
        self.condense_tab.update_config(config)
        self.backfill_tab.update_config(config)
        self.deck_filter_tab.update_config(config)
        self.download_tab.update_config(config)

    def release_dictionary_resources(self) -> bool:
        """Refuse resource mutation while a backfill or deck-filter scan uses providers."""
        worker = self.backfill_tab.worker_thread
        if isinstance(worker, BackfillScanWorker) and still_running(worker):
            return False
        deck_filter_worker = self.deck_filter_tab.worker_thread
        return not (isinstance(deck_filter_worker, DeckFilterScanWorker) and still_running(deck_filter_worker))

    # ------------------------------------------------------------------
    # Close contract
    # ------------------------------------------------------------------

    def iter_close_workers(self) -> Iterator[CancellableWorker]:
        """Yield active workers from all children for BackgroundTaskController."""
        yield from self.generate_tab.iter_close_workers()
        yield from self.retime_tab.iter_close_workers()
        yield from self.condense_tab.iter_close_workers()
        yield from self.backfill_tab.iter_close_workers()
        yield from self.deck_filter_tab.iter_close_workers()
        yield from self.download_tab.iter_close_workers()
