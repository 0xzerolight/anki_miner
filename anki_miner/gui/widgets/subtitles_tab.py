"""Subtitles container tab — nests Generate, Retime, and Condense as inner tabs.

Wraps :class:`~anki_miner.gui.widgets.subtitle_creation_tab.SubtitleCreationTab`
(Generate), :class:`~anki_miner.gui.widgets.subtitle_retime_tab.SubtitleRetimeTab`
(Retime), and :class:`~anki_miner.gui.widgets.condense_tab.CondenseTab` (Condense)
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
from anki_miner.gui.widgets.condense_tab import CondenseTab
from anki_miner.gui.widgets.subtitle_creation_tab import SubtitleCreationTab
from anki_miner.gui.widgets.subtitle_retime_tab import SubtitleRetimeTab

if TYPE_CHECKING:
    from anki_miner.gui.workers.condense_worker import CondenseWorker
    from anki_miner.gui.workers.subtitle_gen_worker import SubtitleGenWorker
    from anki_miner.gui.workers.subtitle_retime_worker import SubtitleRetimeWorker


class SubtitlesTab(QWidget):
    """Container tab holding Generate, Retime, and Condense as inner tabs.

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

        # Stable sub-tab keys for reveal_capability (see capabilities.SUBTAB_KEYS).
        self._subtab_index = {"generate": 0, "retime": 1, "condense": 2}

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
        ``"retime"``, ``"condense"``). Unknown keys are ignored so a stale
        caller can't crash the UI.
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

    # ------------------------------------------------------------------
    # Close contract
    # ------------------------------------------------------------------

    def iter_close_workers(
        self,
    ) -> Iterator[SubtitleGenWorker | SubtitleRetimeWorker | CondenseWorker]:
        """Yield active workers from all children for BackgroundTaskController."""
        yield from self.generate_tab.iter_close_workers()
        yield from self.retime_tab.iter_close_workers()
        yield from self.condense_tab.iter_close_workers()
