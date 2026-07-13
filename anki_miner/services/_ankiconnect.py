"""Shared AnkiConnect HTTP helper.

Internal-but-tested: the leading underscore marks this as a private module, yet it
has no public facade because it is an implementation seam shared by the AnkiConnect
services. White-box unit tests import it directly and patch
``anki_miner.services._ankiconnect.requests.post`` at many sites (see
``tests/unit/test_anki_service.py``) to drive the HTTP layer without a live Anki. The
underscore therefore stays and the module path is a deliberately stable test surface;
do not rename it or reroute those patch targets.
"""

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
    if not isinstance(result, dict):
        # A non-object body (wrong service on the port, a proxy error page that
        # still parses as JSON) would otherwise crash on `result.get(...)`.
        raise AnkiConnectionError(
            f"AnkiConnect '{action}' returned a non-object response "
            f"({type(result).__name__}); is another service listening on this port?"
        )
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
    if not isinstance(result, dict):
        raise AnkiConnectionError(
            f"AnkiConnect 'multi' returned a non-object response "
            f"({type(result).__name__}); is another service listening on this port?"
        )
    if result.get("error"):
        raise AnkiConnectionError(f"AnkiConnect error in 'multi': {result['error']}")
    return result.get("result") or []


def _expected_type_name(elem_type: type | tuple[type, ...]) -> str:
    """Render one type or a tuple of types as a readable ``a or b`` name."""
    if isinstance(elem_type, tuple):
        return " or ".join(t.__name__ for t in elem_type)
    return elem_type.__name__


def _expect_list(
    result: Any,
    action: str,
    expected_len: int = -1,
    elem_type: type | tuple[type, ...] | None = None,
) -> list:
    """Validate an AnkiConnect ``result`` is a list of the expected shape.

    Ported from Yomitan's ``AnkiConnect._normalizeArray``
    (``ext/js/comm/anki-connect.js``, function ``_normalizeArray``) at upstream
    commit e2ed450. Turns a malformed response (wrong service on the port, a
    truncated array, wrong element types) into a typed
    :class:`AnkiConnectionError` naming the offending index, instead of letting
    it surface as an ``AttributeError``/``TypeError`` deeper in a consumer.

    Args:
        result: The ``result`` payload from :func:`post_action`.
        action: Action name, used in error messages.
        expected_len: Required length; a negative value accepts any length
            (recording the observed length, as upstream does).
        elem_type: If given, every element must be an instance of it (a type or
            tuple of types). ``None`` skips per-element type checks.

    Returns:
        The validated list (the same object, unmodified).

    Raises:
        AnkiConnectionError: ``result`` is not a list, its length differs from a
            non-negative ``expected_len``, or an element has the wrong type.
    """
    if not isinstance(result, list):
        raise AnkiConnectionError(f"AnkiConnect '{action}' returned {type(result).__name__}, expected a list")
    if expected_len >= 0 and len(result) != expected_len:
        raise AnkiConnectionError(f"AnkiConnect '{action}' returned {len(result)} item(s), expected {expected_len}")
    if elem_type is not None:
        for i, item in enumerate(result):
            if not isinstance(item, elem_type):
                raise AnkiConnectionError(
                    f"AnkiConnect '{action}' item at index {i} is "
                    f"{type(item).__name__}, expected {_expected_type_name(elem_type)}"
                )
    return result
