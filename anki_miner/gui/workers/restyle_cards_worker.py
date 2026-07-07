"""Worker thread for the Restyle Mined Cards tool (re-apply latest styling)."""

import logging

from PyQt6.QtCore import pyqtSignal

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.workers.base_worker import CancellableWorker
from anki_miner.services.anki_service import AnkiService
from anki_miner.services.card_restyler import restyle_mined_cards

logger = logging.getLogger(__name__)


class RestyleCardsWorker(CancellableWorker):
    """Runs ``restyle_mined_cards`` off the GUI thread.

    The AnkiConnect reads/writes plus ``collect_dictionary_css``'s SQLite I/O all
    happen here. Cancellation is honored between note chunks (committed chunks
    stay written; a re-run resumes since the write is idempotent).
    """

    progress = pyqtSignal(int, int)  # (scanned, total)
    result_ready = pyqtSignal(object)  # RestyleResult

    def __init__(self, service: AnkiService, config: AnkiMinerConfig, parent=None) -> None:
        super().__init__(parent)
        self.service = service
        self.config = config

    def run(self) -> None:
        try:
            if self.check_cancelled():
                return
            result = restyle_mined_cards(
                self.service,
                self.config,
                progress=self.progress.emit,
                is_cancelled=self.check_cancelled,
            )
            if not self.check_cancelled():
                self.result_ready.emit(result)
        except Exception as e:  # noqa: BLE001 — surface every failure to the GUI
            logger.exception("RestyleCardsWorker unhandled exception")
            if not self.check_cancelled():
                self.error.emit(f"Restyle failed: {e}")
