"""Factory for the note-type (model) name fetch worker.

Short-lived: calls :meth:`AnkiService.get_model_names` once via a shared
:class:`SingleCallWorker` and emits ``result_ready`` with the (possibly empty)
list on the main thread, keeping the GUI responsive while AnkiConnect's HTTP
call is in flight. Used by the setup wizard's Note Type page.

Emits ``result_ready([])`` when AnkiConnect responds without model names
(connection refused, etc.) — the slot must distinguish empty from populated
and surface a status message accordingly.
"""

from anki_miner.gui.workers.base_worker import SingleCallWorker
from anki_miner.services.anki_service import AnkiService


def FetchNotetypesWorker(service: AnkiService, parent=None) -> SingleCallWorker:
    """Build a worker that fetches the collection's note type names from AnkiConnect.

    Args:
        service: AnkiService instance configured for the current AnkiConnect URL.
        parent: Optional parent QObject for lifetime management.

    Returns:
        A :class:`SingleCallWorker` emitting ``result_ready(list[str])`` /
        ``error(str)``.
    """
    return SingleCallWorker(
        lambda: service.get_model_names(),
        error_prefix="Error fetching note type names: ",
        parent=parent,
    )
