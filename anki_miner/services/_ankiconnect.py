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


def post_multi(
    ankiconnect_url: str,
    actions: list[dict],
    timeout: int = 30,
) -> list[Any]:
    """Send a ``multi`` envelope to AnkiConnect and return per-action results.

    Per-sub-action errors are returned in the list as-is (dicts with an
    ``"error"`` key); only top-level transport / AnkiConnect failures raise.

    Args:
        ankiconnect_url: AnkiConnect endpoint, typically ``http://localhost:8765``.
        actions: List of action dicts, each shaped like
            ``{"action": "...", "version": 6, "params": {...}}``.
        timeout: Request timeout in seconds.

    Returns:
        List of per-action results in the same order as ``actions``.

    Raises:
        AnkiConnectionError: on connection failure, HTTP/JSON parse failure,
            or a top-level AnkiConnect error on the ``multi`` envelope itself.
    """
    try:
        response = requests.post(
            ankiconnect_url,
            json={"action": "multi", "version": 6, "params": {"actions": actions}},
            timeout=timeout,
        )
        result = response.json()
    except requests.exceptions.ConnectionError as e:
        raise AnkiConnectionError("Cannot connect to AnkiConnect. Is Anki running?") from e
    except (requests.RequestException, ValueError) as e:
        raise AnkiConnectionError(f"AnkiConnect call 'multi' failed: {e}") from e
    if result.get("error"):
        raise AnkiConnectionError(f"AnkiConnect error in 'multi': {result['error']}")
    return result.get("result") or []
