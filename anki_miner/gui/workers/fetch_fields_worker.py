"""Worker thread for fetching note type field names from AnkiConnect."""

from PyQt6.QtCore import pyqtSignal

from anki_miner.gui.workers.base_worker import CancellableWorker
from anki_miner.services.anki_service import AnkiService


class FetchFieldsWorker(CancellableWorker):
    """Worker thread for fetching a note type's field list from AnkiConnect.

    Short-lived: calls :meth:`AnkiService.get_note_type_fields` once and emits
    ``result_ready`` with the (possibly empty) list on the main thread. Keeps
    the GUI responsive while AnkiConnect's HTTP call is in flight.

    Emits ``result_ready([])`` when AnkiConnect responds without a field list
    (note type missing, connection refused, etc.) — the slot must distinguish
    empty from populated and surface a status message accordingly.
    """

    # Carries list[str] — typed as object so Qt's metatype system doesn't
    # require a custom registration for a parametric generic.
    result_ready = pyqtSignal(object)

    def __init__(self, service: AnkiService, note_type: str, parent=None):
        """Initialize the fetch-fields worker.

        Args:
            service: AnkiService instance configured for the current AnkiConnect URL.
            note_type: Name of the note type whose fields to query.
            parent: Optional parent QObject for lifetime management.
        """
        super().__init__(parent)
        self.service = service
        self.note_type = note_type

    def run(self) -> None:
        """Execute the field-name fetch in the background thread."""
        try:
            if self.check_cancelled():
                return

            fields = self.service.get_note_type_fields(self.note_type)

            if not self.check_cancelled():
                self.result_ready.emit(fields)
        except Exception as e:  # noqa: BLE001 — surface every failure to GUI
            if not self.check_cancelled():
                self.error.emit(f"Error fetching note type fields: {e}")
