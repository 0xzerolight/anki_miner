"""Factory for the note-type field-name fetch worker.

Short-lived: calls :meth:`AnkiService.get_note_type_fields` once via a shared
:class:`SingleCallWorker` and emits ``result_ready`` with the (possibly empty)
list on the main thread, keeping the GUI responsive while AnkiConnect's HTTP
call is in flight.

Emits ``result_ready([])`` when AnkiConnect responds without a field list
(note type missing, connection refused, etc.) — the slot must distinguish
empty from populated and surface a status message accordingly.
"""

from anki_miner.gui.workers.base_worker import SingleCallWorker
from anki_miner.services.anki_service import AnkiService


def FetchFieldsWorker(service: AnkiService, note_type: str, parent=None) -> SingleCallWorker:
    """Build a worker that fetches *note_type*'s field list from AnkiConnect.

    Args:
        service: AnkiService instance configured for the current AnkiConnect URL.
        note_type: Name of the note type whose fields to query.
        parent: Optional parent QObject for lifetime management.

    Returns:
        A :class:`SingleCallWorker` emitting ``result_ready(list[str])`` /
        ``error(str)``.
    """
    return SingleCallWorker(
        lambda: service.get_note_type_fields(note_type),
        error_prefix="Error fetching note type fields: ",
        parent=parent,
    )
