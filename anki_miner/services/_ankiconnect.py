"""Shared AnkiConnect HTTP helper."""

from typing import Any

import requests

from anki_miner.exceptions import AnkiConnectionError


def post_action(
    ankiconnect_url: str,
    action: str,
    params: dict | None = None,
    timeout: int = 30,
) -> Any:
    """Send one AnkiConnect action and return the ``result`` payload.

    Args:
        ankiconnect_url: AnkiConnect endpoint, typically
            ``http://localhost:8765``.
        action: AnkiConnect action name (e.g. ``"findNotes"``).
        params: Action-specific parameters dict. ``None`` is sent as ``{}``.
        timeout: Request timeout in seconds.

    Returns:
        The ``result`` field from the AnkiConnect response.

    Raises:
        AnkiConnectionError: on connection failure, HTTP/JSON parse failure,
            or AnkiConnect-side error (where ``result["error"]`` is set).
    """
    try:
        response = requests.post(
            ankiconnect_url,
            json={"action": action, "version": 6, "params": params or {}},
            timeout=timeout,
        )
        result = response.json()
    except requests.exceptions.ConnectionError as e:
        raise AnkiConnectionError("Cannot connect to AnkiConnect. Is Anki running?") from e
    except (requests.RequestException, ValueError) as e:
        raise AnkiConnectionError(f"AnkiConnect call '{action}' failed: {e}") from e
    if result.get("error"):
        raise AnkiConnectionError(f"AnkiConnect error in '{action}': {result['error']}")
    return result.get("result")
