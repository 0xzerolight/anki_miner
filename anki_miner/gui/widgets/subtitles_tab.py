"""Subtitles container tab — nests Generate and Retime as inner tabs.

Wraps :class:`~anki_miner.gui.widgets.subtitle_creation_tab.SubtitleCreationTab`
(Generate) and :class:`~anki_miner.gui.widgets.subtitle_retime_tab.SubtitleRetimeTab`
(Retime) inside a single top-level tab so the main tab bar stays uncluttered.

Close contract:
- ``iter_close_workers()`` fans out to both children so
  :class:`~anki_miner.gui.controllers.background_tasks.BackgroundTaskController`
  can join either child's active worker on app close.
- No ``worker_thread`` attribute: workers are exposed exclusively via
  ``iter_close_workers``; ``background_tasks._collect_close_laggards`` falls
  back to ``getattr(tab, "worker_thread", None)`` → None, which is safe.
- No ``shutdown`` method: neither child tab has one, so there is nothing to
  delegate (``getattr`` fallback handles absence in ``_collect_close_laggards``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterator

from PyQt6.QtCore import QCoreApplication
from PyQt6.QtWidgets import QTabWidget, QVBoxLayout, QWidget

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.subtitle_creation_tab import SubtitleCreationTab
from anki_miner.gui.widgets.subtitle_retime_tab import SubtitleRetimeTab

if TYPE_CHECKING:
    from anki_miner.gui.workers.subtitle_gen_worker import SubtitleGenWorker
    from anki_miner.gui.workers.subtitle_retime_worker import SubtitleRetimeWorker


class SubtitlesTab(QWidget):
    """Container tab holding Generate and Retime as inner tabs.

    Args:
        config: Frozen application configuration.
        parent: Optional parent widget.
    """

    def __init__(self, config: AnkiMinerConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.config = config

        self.generate_tab = SubtitleCreationTab(config)
        self.retime_tab = SubtitleRetimeTab(config)

        self._inner_tabs = QTabWidget()
        self._inner_tabs.addTab(
            self.generate_tab,
            QCoreApplication.translate("MainWindow", "Generate"),
        )
        self._inner_tabs.addTab(
            self.retime_tab,
            QCoreApplication.translate("MainWindow", "Retime"),
        )

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._inner_tabs)
        self.setLayout(layout)

    # ------------------------------------------------------------------
    # Config refresh
    # ------------------------------------------------------------------

    def update_config(self, config: AnkiMinerConfig) -> None:
        """Fan out a new config to both child tabs."""
        self.config = config
        self.generate_tab.update_config(config)
        self.retime_tab.update_config(config)

    # ------------------------------------------------------------------
    # Close contract
    # ------------------------------------------------------------------

    def iter_close_workers(
        self,
    ) -> Iterator[SubtitleGenWorker | SubtitleRetimeWorker]:
        """Yield active workers from both children for BackgroundTaskController."""
        yield from self.generate_tab.iter_close_workers()
        yield from self.retime_tab.iter_close_workers()
