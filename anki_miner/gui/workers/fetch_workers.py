"""Factories for the short-lived AnkiConnect fetch workers.

Each factory builds a shared :class:`SingleCallWorker` that calls one
``AnkiService`` getter once and emits ``result_ready`` with the (possibly
empty) list on the main thread, keeping the GUI responsive while
AnkiConnect's HTTP call is in flight. The per-factory ``result_ready([])``
contracts are documented on each function below.
"""

from anki_miner.gui.workers.base_worker import SingleCallWorker
from anki_miner.services.anki_service import AnkiService


def FetchNotetypesWorker(service: AnkiService, parent=None) -> SingleCallWorker:
    """Build a worker that fetches the collection's note type names from AnkiConnect.

    Used by the setup wizard's Note Type page.

    Emits ``result_ready([])`` when AnkiConnect responds without model names
    (connection refused, etc.) — the slot must distinguish empty from populated
    and surface a status message accordingly.

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


def FetchFieldsWorker(service: AnkiService, note_type: str, parent=None) -> SingleCallWorker:
    """Build a worker that fetches *note_type*'s field list from AnkiConnect.

    Emits ``result_ready([])`` when AnkiConnect responds without a field list
    (note type missing, connection refused, etc.) — the slot must distinguish
    empty from populated and surface a status message accordingly.

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


def FetchDecksWorker(service: AnkiService, parent=None) -> SingleCallWorker:
    """Build a worker that fetches the collection's deck names from AnkiConnect.

    Used by the excluded-decks picker (Issue #38).

    Emits ``result_ready([])`` when AnkiConnect responds without deck names
    (connection refused, etc.) — the slot must distinguish empty from populated
    and surface a status message accordingly.

    Args:
        service: AnkiService instance configured for the current AnkiConnect URL.
        parent: Optional parent QObject for lifetime management.

    Returns:
        A :class:`SingleCallWorker` emitting ``result_ready(list[str])`` /
        ``error(str)``.
    """
    return SingleCallWorker(
        lambda: service.get_deck_names(),
        error_prefix="Error fetching deck names: ",
        parent=parent,
    )
