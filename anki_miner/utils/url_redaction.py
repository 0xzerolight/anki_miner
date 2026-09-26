"""Shared fail-closed core for the URL-redaction wrappers.

Extracted from ``services/audio_fetch_common.py``, ``utils/youtube_url.py``
and ``diagnostics/bundle.py``, which each redact a URL for a log line but
differ in what they do with the query string. Lives in ``utils`` rather than
``services`` because ``utils/youtube_url.py`` must not import ``services``,
and in a plain module rather than deeper in either existing file so all
three importers sit at the same distance from it.
"""

from urllib.parse import SplitResult, unquote, urlsplit


def split_loggable_url(url: str) -> tuple[SplitResult, str] | None:
    """Return ``(parts, netloc)`` safe to log, or ``None`` to redact fully.

    Fails closed to ``None`` when userinfo is present (plain ``user:pass@``,
    ``user@``, or a percent-encoded ``@`` hiding in the netloc), when *url*
    does not parse, when the port is out of range, or when the scheme or host
    is missing. A URL that fails any of these checks cannot be proven free of
    credentials, so callers must not log any part of it.

    *netloc* rebuilds the host in bracket form when it is an IPv6 literal
    (``parts.hostname`` strips the brackets ``urlsplit`` requires on input),
    with the port re-appended when present.
    """
    try:
        parts = urlsplit(url)
        if parts.username is not None or "@" in unquote(parts.netloc):
            return None
        hostname = parts.hostname
        port = parts.port
    except ValueError:
        return None
    if not parts.scheme or hostname is None:
        return None
    host = f"[{hostname}]" if ":" in hostname else hostname
    netloc = f"{host}:{port}" if port is not None else host
    return parts, netloc
