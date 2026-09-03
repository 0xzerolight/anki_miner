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
import time
from typing import Any

import requests

from anki_miner.exceptions import AnkiConnectionError
from anki_miner.utils.logging_ext import log_summary

logger = logging.getLogger(__name__)

# Protocol version this module speaks; echoed on the readiness receipt when the
# call was not the `version` action (AnkiConnect's envelope carries no version).
_API_VERSION = 6

# How much of a failing response body reaches the log. Enough to recognize a
# proxy error page, an HTML login wall, or another service answering on 8765;
# short enough that a multi-MB body cannot flood the ring.
_BODY_SNIPPET_CHARS = 200

# Once-per-process log state. A refused connection is the single most common
# AnkiConnect failure and every probe in the app retries it, so the per-call
# record stays DEBUG and exactly one WARNING names the endpoint that is not
# answering. `_ready_logged` likewise pins the first proven-good call.
# Guarded by _LOG_STATE_LOCK because validation/episode/backfill workers reach
# this from their own QThreads.
_LOG_STATE_LOCK = threading.Lock()
_connection_warning_logged = False
_ready_logged = False


def reset_connection_warning() -> None:
    """Re-arm the once-per-process connection WARNING.

    Exists for tests and for callers that want a fresh endpoint verdict after
    the user has been told to start Anki; it does not touch the readiness
    receipt.
    """
    global _connection_warning_logged
    with _LOG_STATE_LOCK:
        _connection_warning_logged = False


def _body_snippet(response: requests.Response | None) -> str:
    """Return the first ``_BODY_SNIPPET_CHARS`` of *response*'s body, whitespace-collapsed."""
    if response is None:
        return ""
    try:
        text = response.text
    except Exception:  # noqa: BLE001 — bucket unreadable body: evidence is optional, the real failure is not
        return ""
    if not isinstance(text, str):
        return ""
    return " ".join(text.split())[:_BODY_SNIPPET_CHARS]


def _log_connection_failure(url: str, action: str, exc: BaseException, elapsed_s: float) -> None:
    """DEBUG every refused connection; WARNING the first one in the process."""
    log_summary(
        logger,
        "AnkiConnect connection failed",
        level=logging.DEBUG,
        url=url,
        action=action,
        elapsed=f"{elapsed_s:.3f}s",
        exc_type=type(exc).__name__,
        error=str(exc),
    )
    global _connection_warning_logged
    with _LOG_STATE_LOCK:
        if _connection_warning_logged:
            return
        _connection_warning_logged = True
    log_summary(
        logger,
        "AnkiConnect unreachable",
        level=logging.WARNING,
        url=url,
        action=action,
        exc_type=type(exc).__name__,
        error=str(exc),
    )


def _log_ready(url: str, action: str, result: dict) -> None:
    """INFO the first validated response of the process: Anki answered here."""
    global _ready_logged
    with _LOG_STATE_LOCK:
        if _ready_logged:
            return
        _ready_logged = True
    version = result.get("result") if action == "version" else None
    if not isinstance(version, int):
        version = _API_VERSION
    log_summary(logger, "AnkiConnect ready", url=url, version=version)


def _log_request_failed(
    url: str,
    action: str,
    exc: BaseException,
    elapsed_s: float,
    response: requests.Response | None,
) -> None:
    """WARNING a transport/decode failure with the endpoint, timing, and body evidence."""
    error_response = getattr(exc, "response", None)
    if error_response is None:
        error_response = response
    log_summary(
        logger,
        "AnkiConnect request failed",
        level=logging.WARNING,
        action=action,
        url=url,
        status=getattr(error_response, "status_code", None),
        elapsed=f"{elapsed_s:.3f}s",
        exc_type=type(exc).__name__,
        error=str(exc),
        body=_body_snippet(error_response),
    )


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
    started = time.monotonic()
    response: requests.Response | None = None
    try:
        response = _post(
            ankiconnect_url,
            json={"action": action, "version": _API_VERSION, "params": params or {}},
            timeout=timeout,
        )
        response.raise_for_status()
        _check_response_size(response, action)
        result = response.json()
    except requests.exceptions.ConnectionError as e:
        _log_connection_failure(ankiconnect_url, action, e, time.monotonic() - started)
        raise AnkiConnectionError("Cannot connect to AnkiConnect. Is Anki running?") from e
    except requests.exceptions.Timeout as e:
        # Only read timeouts reach here: ConnectTimeout is also a
        # ConnectionError, so the branch above already claimed it.
        log_summary(
            logger,
            "AnkiConnect request timed out",
            level=logging.WARNING,
            action=action,
            url=ankiconnect_url,
            timeout=timeout,
            elapsed=f"{time.monotonic() - started:.3f}s",
            exc_type=type(e).__name__,
        )
        raise AnkiConnectionError(_timeout_message(action, timeout)) from e
    except (requests.RequestException, ValueError) as e:
        _log_request_failed(ankiconnect_url, action, e, time.monotonic() - started, response)
        raise AnkiConnectionError(f"AnkiConnect call '{action}' failed: {e}") from e
    if not isinstance(result, dict):
        # A non-object body (wrong service on the port, a proxy error page that
        # still parses as JSON) would otherwise crash on `result.get(...)`.
        log_summary(
            logger,
            "AnkiConnect response invalid",
            level=logging.WARNING,
            action=action,
            url=ankiconnect_url,
            type=type(result).__name__,
            body=_body_snippet(response),
        )
        raise AnkiConnectionError(
            f"AnkiConnect '{action}' returned a non-object response "
            f"({type(result).__name__}); is another service listening on this port?"
        )
    if result.get("error"):
        # Hand-rolled, not `log_summary`: the AnkiConnect error string is the
        # whole diagnosis and must stay unquoted at the end of the line, where
        # both readers and the pinned test look for it verbatim.
        logger.warning(
            "AnkiConnect error: action=%s url=%s elapsed=%.3fs error=%s",
            action,
            ankiconnect_url,
            time.monotonic() - started,
            result["error"],
        )
        raise AnkiConnectionError(f"AnkiConnect error in '{action}': {result['error']}")
    _log_ready(ankiconnect_url, action, result)
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
    started = time.monotonic()
    response: requests.Response | None = None
    try:
        response = _post(
            ankiconnect_url,
            json={"action": "multi", "version": _API_VERSION, "params": {"actions": actions}},
            timeout=timeout,
        )
        response.raise_for_status()
        _check_response_size(response, "multi")
        result = response.json()
    except requests.exceptions.ConnectionError as e:
        _log_connection_failure(ankiconnect_url, "multi", e, time.monotonic() - started)
        raise AnkiConnectionError("Cannot connect to AnkiConnect. Is Anki running?") from e
    except requests.exceptions.Timeout as e:
        # Only read timeouts reach here: ConnectTimeout is also a
        # ConnectionError, so the branch above already claimed it.
        log_summary(
            logger,
            "AnkiConnect request timed out",
            level=logging.WARNING,
            action="multi",
            url=ankiconnect_url,
            timeout=timeout,
            elapsed=f"{time.monotonic() - started:.3f}s",
            exc_type=type(e).__name__,
        )
        raise AnkiConnectionError(_timeout_message("multi", timeout)) from e
    except (requests.RequestException, ValueError) as e:
        _log_request_failed(ankiconnect_url, "multi", e, time.monotonic() - started, response)
        raise AnkiConnectionError(f"AnkiConnect call 'multi' failed: {e}") from e
    if not isinstance(result, dict):
        log_summary(
            logger,
            "AnkiConnect response invalid",
            level=logging.WARNING,
            action="multi",
            url=ankiconnect_url,
            type=type(result).__name__,
            body=_body_snippet(response),
        )
        raise AnkiConnectionError(
            f"AnkiConnect 'multi' returned a non-object response "
            f"({type(result).__name__}); is another service listening on this port?"
        )
    if result.get("error"):
        # See post_action: the error string stays verbatim at end of line.
        logger.warning(
            "AnkiConnect error: action=multi url=%s elapsed=%.3fs error=%s",
            ankiconnect_url,
            time.monotonic() - started,
            result["error"],
        )
        raise AnkiConnectionError(f"AnkiConnect error in 'multi': {result['error']}")
    _log_ready(ankiconnect_url, "multi", result)
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
