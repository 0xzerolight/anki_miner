"""Subtitles container tab — nests Generate, Retime, Condense, Card Backfill.

Wraps :class:`~anki_miner.gui.widgets.subtitle_creation_tab.SubtitleCreationTab`
(Generate), :class:`~anki_miner.gui.widgets.subtitle_retime_tab.SubtitleRetimeTab`
(Retime), :class:`~anki_miner.gui.widgets.condense_tab.CondenseTab` (Condense),
and :class:`~anki_miner.gui.widgets.backfill_tab.CardBackfillTab` (Card Backfill)
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

from typing import TYPE_CHECKING, Iterator

from PyQt6.QtCore import QCoreApplication
from PyQt6.QtWidgets import QTabWidget, QVBoxLayout, QWidget

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.backfill_tab import CardBackfillTab
from anki_miner.gui.widgets.condense_tab import CondenseTab
from anki_miner.gui.widgets.subtitle_creation_tab import SubtitleCreationTab
from anki_miner.gui.widgets.subtitle_retime_tab import SubtitleRetimeTab

if TYPE_CHECKING:
    from anki_miner.gui.workers.base_worker import CancellableWorker


class SubtitlesTab(QWidget):
    """Container tab holding Generate, Retime, Condense, Card Backfill inner tabs.

    Args:
        config: Frozen application configuration.
        parent: Optional parent widget.
    """

    def __init__(self, config: AnkiMinerConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = config

        self.generate_tab = SubtitleCreationTab(config)
        self.retime_tab = SubtitleRetimeTab(config)
        self.condense_tab = CondenseTab(config)

        self._inner_tabs = QTabWidget()
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

        # Stable sub-tab keys for reveal_capability (see capabilities.SUBTAB_KEYS).
        self._subtab_index = {"generate": 0, "retime": 1, "condense": 2, "backfill": 3}

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
        ``"retime"``, ``"condense"``, ``"backfill"``). Unknown keys are ignored
        so a stale caller can't crash the UI.
        """
        index = self._subtab_index.get(key)
        if index is not None:
            self._inner_tabs.setCurrentIndex(index)

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

    # ------------------------------------------------------------------
    # Close contract
    # ------------------------------------------------------------------

    def iter_close_workers(self) -> Iterator[CancellableWorker]:
        """Yield active workers from all children for BackgroundTaskController."""
        yield from self.generate_tab.iter_close_workers()
        yield from self.retime_tab.iter_close_workers()
        yield from self.condense_tab.iter_close_workers()
        yield from self.backfill_tab.iter_close_workers()
