"""Worker thread for fetching deck names from AnkiConnect."""

from PyQt6.QtCore import pyqtSignal

from anki_miner.gui.workers.base_worker import CancellableWorker
from anki_miner.services.anki_service import AnkiService


class FetchDecksWorker(CancellableWorker):
    """Worker thread for fetching the collection's deck names from AnkiConnect.

    Short-lived: calls :meth:`AnkiService.get_deck_names` once and emits
    ``result_ready`` with the (possibly empty) list on the main thread. Keeps
    the GUI responsive while AnkiConnect's HTTP call is in flight. Used by the
    excluded-decks picker (Issue #38).

    Emits ``result_ready([])`` when AnkiConnect responds without deck names
    (connection refused, etc.) — the slot must distinguish empty from populated
    and surface a status message accordingly.
    """

    # Carries list[str] — typed as object so Qt's metatype system doesn't
    # require a custom registration for a parametric generic.
    result_ready = pyqtSignal(object)

    def __init__(self, service: AnkiService, parent=None):
        """Initialize the fetch-decks worker.

        Args:
            service: AnkiService instance configured for the current AnkiConnect URL.
            parent: Optional parent QObject for lifetime management.
        """
        super().__init__(parent)
        self.service = service

    def run(self) -> None:
        """Execute the deck-name fetch in the background thread."""
        try:
            if self.check_cancelled():
                return

            decks = self.service.get_deck_names()

            if not self.check_cancelled():
                self.result_ready.emit(decks)
        except Exception as e:  # noqa: BLE001 — surface every failure to GUI
            if not self.check_cancelled():
                self.error.emit(f"Error fetching deck names: {e}")
