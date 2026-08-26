"""Shared AnkiConnect HTTP helper.

Internal-but-tested: the leading underscore marks this as a private module, yet it
has no public facade because it is an implementation seam shared by the AnkiConnect
services. White-box unit tests import it directly and patch
``anki_miner.services._ankiconnect.requests.post`` at many sites (see
``tests/unit/test_anki_service.py``) to drive the HTTP layer without a live Anki. The
underscore therefore stays and the module path is a deliberately stable test surface;
do not rename it or reroute those patch targets.

In production, ``post_action``/``post_multi`` send through one shared, lazily
created ``requests.Session`` (see ``_post``) so the 20-200 calls a typical run
makes reuse a keep-alive TCP connection instead of paying a fresh
socket+TLS-free handshake per call. This is invisible to the patch seam above:
``_post`` compares the live ``requests.post`` against the original captured at
import time, and if a test has replaced it, routes the call through the patched
callable instead of the session. Do not call ``requests.post`` directly from new
code in this module - go through ``_post`` so both the keep-alive path and the
patch seam keep working.
"""

import logging
import threading
from typing import Any

import requests

from anki_miner.exceptions import AnkiConnectionError

logger = logging.getLogger(__name__)

# Stashed at import time so `_post` can detect a test having patched
# `requests.post` on this module (see module docstring) and honour it instead
# of the shared session below.
_ORIGINAL_POST = requests.post

# Lazily created, reused across calls to keep the AnkiConnect TCP connection
# alive instead of opening a fresh one per action. Guarded by _SESSION_LOCK
# (double-checked lock, mirroring tagger.py's get_shared_tagger()) since
# validation/episode/backfill/deck-filter/batch workers can all reach this
# from their own QThreads concurrently.
_SESSION_LOCK = threading.Lock()
_session: requests.Session | None = None


def _get_session() -> requests.Session:
    """Return the shared keep-alive session, building it once (double-checked lock)."""
    global _session
    if _session is None:
        with _SESSION_LOCK:
            if _session is None:
                _session = requests.Session()
    return _session


def _post(url: str, **kwargs: Any) -> requests.Response:
    """POST to AnkiConnect, reusing one session - unless a test has patched ``requests.post``.

    See the module docstring for the patch-seam contract this preserves.
    """
    if requests.post is not _ORIGINAL_POST:
        return requests.post(url, **kwargs)
    return _get_session().post(url, **kwargs)


# Cap the fully-buffered response body before JSON-decoding it. AnkiConnect can
# legitimately return a multi-hundred-MB payload (e.g. notesInfo over a large
# collection), so this stays generous - it exists only to fail closed on a
# pathological or wrong-service body (Anki hung mid-response, a proxy error
# page, another service answering on this port) instead of parsing one.
_MAX_RESPONSE_BYTES = 256 * 1024 * 1024  # 256 MiB


def _check_response_size(response: requests.Response, action: str) -> None:
    """Raise :class:`AnkiConnectionError` before an oversized body is parsed or acted on.

    ``requests`` has already buffered the full body into ``response.content``
    by the time this runs -- the check gates what happens next (JSON-decoding
    and using the result), not the buffering itself.
    """
    size = len(response.content)
    if size > _MAX_RESPONSE_BYTES:
        raise AnkiConnectionError(
            f"AnkiConnect '{action}' response is {size:,} bytes, exceeding the {_MAX_RESPONSE_BYTES:,}-byte cap"
        )


def _timeout_message(action: str, timeout: int) -> str:
    """User-facing copy for a read timeout: connected, but Anki never answered.

    AnkiConnect accepts the TCP connection regardless of what Anki is doing (the
    kernel completes the handshake), but the action itself runs on Anki's main
    thread against the collection. A sync in progress, an open dialog, or a
    database check therefore holds the response past the deadline while every
    quick "is Anki connected?" probe still looks green — so the message must
    name the busy state, not the network.
    """
    return (
        f"AnkiConnect call '{action}' timed out after {timeout}s. "
        "Anki accepted the connection but did not respond - it is likely busy "
        "(syncing, showing a dialog, or checking the database). "
        "Wait for Anki to finish and try again."
    )


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
    logger.debug(
        "AnkiConnect request: action=%s params=%d timeout=%d",
        action,
        len(params or {}),
        timeout,
    )
    try:
        response = _post(
            ankiconnect_url,
            json={"action": action, "version": 6, "params": params or {}},
            timeout=timeout,
        )
        response.raise_for_status()
        _check_response_size(response, action)
        result = response.json()
    except requests.exceptions.ConnectionError as e:
        logger.debug(
            "AnkiConnect connection failed: url=%s action=%s exc=%s",
            ankiconnect_url,
            action,
            type(e).__name__,
        )
        raise AnkiConnectionError("Cannot connect to AnkiConnect. Is Anki running?") from e
    except requests.exceptions.Timeout as e:
        # Only read timeouts reach here: ConnectTimeout is also a
        # ConnectionError, so the branch above already claimed it.
        logger.warning(
            "AnkiConnect request timed out: action=%s timeout=%d",
            action,
            timeout,
        )
        raise AnkiConnectionError(_timeout_message(action, timeout)) from e
    except (requests.RequestException, ValueError) as e:
        logger.warning(
            "AnkiConnect request failed: action=%s status=%s exc=%s",
            action,
            getattr(getattr(e, "response", None), "status_code", None),
            type(e).__name__,
        )
        raise AnkiConnectionError(f"AnkiConnect call '{action}' failed: {e}") from e
    if not isinstance(result, dict):
        # A non-object body (wrong service on the port, a proxy error page that
        # still parses as JSON) would otherwise crash on `result.get(...)`.
        logger.warning(
            "AnkiConnect response invalid: action=%s type=%s",
            action,
            type(result).__name__,
        )
        raise AnkiConnectionError(
            f"AnkiConnect '{action}' returned a non-object response "
            f"({type(result).__name__}); is another service listening on this port?"
        )
    if result.get("error"):
        logger.warning(
            "AnkiConnect error: action=%s error=%s",
            action,
            result["error"],
        )
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
    logger.debug(
        "AnkiConnect request: action=multi actions=%d timeout=%d",
        len(actions),
        timeout,
    )
    try:
        response = _post(
            ankiconnect_url,
            json={"action": "multi", "version": 6, "params": {"actions": actions}},
            timeout=timeout,
        )
        response.raise_for_status()
        _check_response_size(response, "multi")
        result = response.json()
    except requests.exceptions.ConnectionError as e:
        logger.debug(
            "AnkiConnect connection failed: url=%s action=multi exc=%s",
            ankiconnect_url,
            type(e).__name__,
        )
        raise AnkiConnectionError("Cannot connect to AnkiConnect. Is Anki running?") from e
    except requests.exceptions.Timeout as e:
        # Only read timeouts reach here: ConnectTimeout is also a
        # ConnectionError, so the branch above already claimed it.
        logger.warning(
            "AnkiConnect request timed out: action=multi timeout=%d",
            timeout,
        )
        raise AnkiConnectionError(_timeout_message("multi", timeout)) from e
    except (requests.RequestException, ValueError) as e:
        logger.warning(
            "AnkiConnect request failed: action=multi status=%s exc=%s",
            getattr(getattr(e, "response", None), "status_code", None),
            type(e).__name__,
        )
        raise AnkiConnectionError(f"AnkiConnect call 'multi' failed: {e}") from e
    if not isinstance(result, dict):
        logger.warning(
            "AnkiConnect response invalid: action=multi type=%s",
            type(result).__name__,
        )
        raise AnkiConnectionError(
            f"AnkiConnect 'multi' returned a non-object response "
            f"({type(result).__name__}); is another service listening on this port?"
        )
    if result.get("error"):
        logger.warning(
            "AnkiConnect error: action=multi error=%s",
            result["error"],
        )
        raise AnkiConnectionError(f"AnkiConnect error in 'multi': {result['error']}")
    return _expect_list(result.get("result"), "multi", len(actions))


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
        logger.warning(
            "AnkiConnect response shape invalid: action=%s type=%s expected=list",
            action,
            type(result).__name__,
        )
        raise AnkiConnectionError(f"AnkiConnect '{action}' returned {type(result).__name__}, expected a list")
    if expected_len >= 0 and len(result) != expected_len:
        logger.warning(
            "AnkiConnect response shape invalid: action=%s length=%d expected=%d",
            action,
            len(result),
            expected_len,
        )
        raise AnkiConnectionError(f"AnkiConnect '{action}' returned {len(result)} item(s), expected {expected_len}")
    if elem_type is not None:
        for i, item in enumerate(result):
            if not isinstance(item, elem_type):
                logger.warning(
                    "AnkiConnect response shape invalid: action=%s index=%d type=%s expected=%s",
                    action,
                    i,
                    type(item).__name__,
                    _expected_type_name(elem_type),
                )
                raise AnkiConnectionError(
                    f"AnkiConnect '{action}' item at index {i} is "
                    f"{type(item).__name__}, expected {_expected_type_name(elem_type)}"
                )
    return result
