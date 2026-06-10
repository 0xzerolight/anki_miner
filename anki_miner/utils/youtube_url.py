"""YouTube URL classification utilities.

Parses a YouTube URL and returns a :class:`YouTubeUrlInfo` describing what
the URL points to, without making any network requests.  ``urllib.parse`` is
used throughout so the logic is easy to follow and extend.

Supported URL forms:
    - ``https://www.youtube.com/watch?v=<id>``        → video
    - ``https://www.youtube.com/watch?v=<id>&list=<PL>``  → video_in_playlist
    - ``https://www.youtube.com/playlist?list=<PL>``  → playlist
    - ``https://youtu.be/<id>``                        → video
    - ``https://youtu.be/<id>?list=<PL>``             → video_in_playlist
    - ``https://www.youtube.com/shorts/<id>``          → video
    - ``https://www.youtube.com/shorts/<id>?list=<PL>`` → video_in_playlist
    - ``https://www.youtube.com/live/<id>``            → video
    - ``https://www.youtube.com/live/<id>?list=<PL>`` → video_in_playlist

Scheme-less URLs (``www.youtube.com/…``, ``youtube.com/…``, ``youtu.be/…``)
are handled by prepending ``https://`` before parsing.

``m.youtube.com`` and ``music.youtube.com`` are treated identically to
``www.youtube.com``.

YouTube Mix playlists (``list=RD…``):
    Auto-generated Mix playlist ids start with the two-character prefix ``RD``.
    Because Mixes are effectively unbounded and not useful for batch mining, the
    ``list`` parameter is ignored when a valid video id is present — the result
    is ``"video"``, not ``"video_in_playlist"``.  A mix-only URL with no video
    id returns ``"unknown"``.

yt-dlp remains the final validator for actual fetch-ability; this util is
intentionally conservative so that unrecognised URLs fall through to the
existing single-video probe path without any behaviour change for current users.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import parse_qs, urlparse

# ---------------------------------------------------------------------------
# Compiled patterns
# ---------------------------------------------------------------------------

# 11-character Base64url alphabet — same constraint as _VIDEO_ID_RE in
# anki_miner/services/youtube_fetcher.py.
_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")

# Playlist ids are at least 12 characters (real YouTube ids are 24-34 chars,
# but enforce only a lower bound so we stay forward-compatible).
_PLAYLIST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{12,}$")

# Mix playlists start with "RD" (Radio/auto-generated mixes).
_MIX_LIST_PREFIX = "RD"

# Recognised YouTube hostnames (normalised to lowercase before checking).
_YOUTUBE_HOSTS = frozenset(
    {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "music.youtube.com",
    }
)
_YOUTU_BE_HOST = "youtu.be"


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class YouTubeUrlInfo:
    """Result of classifying a YouTube URL.

    Attributes:
        kind: One of ``"video"``, ``"playlist"``, ``"video_in_playlist"``, or
            ``"unknown"``.  ``"unknown"`` means neither a valid video id nor a
            valid playlist id was found; callers should fall through to the
            existing single-video probe path.
        video_id: 11-character YouTube video id, or ``None``.
        playlist_id: YouTube playlist id (≥ 12 chars), or ``None``.
    """

    kind: Literal["video", "playlist", "video_in_playlist", "unknown"]
    video_id: str | None
    playlist_id: str | None


_UNKNOWN = YouTubeUrlInfo(kind="unknown", video_id=None, playlist_id=None)


# ---------------------------------------------------------------------------
# Implementation
# ---------------------------------------------------------------------------


def _normalise_url(url: str) -> str:
    """Prepend ``https://`` to scheme-less URLs so ``urlparse`` handles them."""
    url = url.strip()
    if not url:
        return url
    if "://" not in url:
        url = "https://" + url
    return url


def _extract_video_id_from_path_segment(segment: str) -> str | None:
    """Return the video id if *segment* is a valid 11-char id, else ``None``."""
    candidate = segment.strip("/")
    if _VIDEO_ID_RE.match(candidate):
        return candidate
    return None


def _parse_video_id(qs: dict[str, list[str]]) -> str | None:
    """Return the video id from parsed query-string ``v`` param, or ``None``."""
    values = qs.get("v", [])
    if not values:
        return None
    candidate = values[0]
    if _VIDEO_ID_RE.match(candidate):
        return candidate
    return None


def _parse_playlist_id(qs: dict[str, list[str]]) -> str | None:
    """Return the playlist id from parsed ``list`` param, or ``None``.

    Returns ``None`` for Mix playlist ids (``list=RD…``) — callers that want
    to suppress Mixes should check the return value rather than checking the
    raw param themselves.
    """
    values = qs.get("list", [])
    if not values:
        return None
    candidate = values[0]
    # Ignore Mix playlists (auto-generated, unbounded).
    if candidate.startswith(_MIX_LIST_PREFIX):
        return None
    if _PLAYLIST_ID_RE.match(candidate):
        return candidate
    return None


def classify_youtube_url(url: str) -> YouTubeUrlInfo:
    """Classify a YouTube URL without making any network requests.

    Args:
        url: A YouTube URL in any of the supported forms (see module docstring).
            Scheme-less URLs like ``www.youtube.com/watch?v=…`` are accepted.
            Non-YouTube URLs, empty strings, and unrecognised forms return
            ``YouTubeUrlInfo(kind="unknown", …)``.

    Returns:
        A :class:`YouTubeUrlInfo` describing the URL.

    Note on YouTube Mixes (``list=RD…``):
        When the ``list`` query parameter starts with ``RD``, the URL points to
        an auto-generated Mix playlist which is effectively unbounded.  This
        function ignores such ``list`` parameters: if a valid video id is also
        present the result is ``"video"``; if no video id is present the result
        is ``"unknown"``.
    """
    normalised = _normalise_url(url)
    if not normalised:
        return _UNKNOWN

    try:
        parsed = urlparse(normalised)
    except Exception:
        return _UNKNOWN

    host = (parsed.hostname or "").lower()

    # -----------------------------------------------------------------------
    # youtu.be short links
    # -----------------------------------------------------------------------
    if host == _YOUTU_BE_HOST:
        # The video id is the path component, e.g. /dQw4w9WgXcQ
        video_id = _extract_video_id_from_path_segment(parsed.path)
        if not video_id:
            return _UNKNOWN
        qs = parse_qs(parsed.query)
        playlist_id = _parse_playlist_id(qs)
        if playlist_id:
            return YouTubeUrlInfo(kind="video_in_playlist", video_id=video_id, playlist_id=playlist_id)
        return YouTubeUrlInfo(kind="video", video_id=video_id, playlist_id=None)

    # -----------------------------------------------------------------------
    # youtube.com family
    # -----------------------------------------------------------------------
    if host not in _YOUTUBE_HOSTS:
        return _UNKNOWN

    path = parsed.path.rstrip("/")
    qs = parse_qs(parsed.query)

    # /watch?v=…
    if path == "/watch":
        video_id = _parse_video_id(qs)
        if not video_id:
            return _UNKNOWN
        playlist_id = _parse_playlist_id(qs)
        if playlist_id:
            return YouTubeUrlInfo(kind="video_in_playlist", video_id=video_id, playlist_id=playlist_id)
        return YouTubeUrlInfo(kind="video", video_id=video_id, playlist_id=None)

    # /playlist?list=…
    if path == "/playlist":
        playlist_id = _parse_playlist_id(qs)
        if not playlist_id:
            return _UNKNOWN
        return YouTubeUrlInfo(kind="playlist", video_id=None, playlist_id=playlist_id)

    # /shorts/<id>  and  /live/<id>
    for prefix in ("/shorts/", "/live/"):
        if path.startswith(prefix):
            segment = path[len(prefix) :]
            video_id = _extract_video_id_from_path_segment(segment)
            if not video_id:
                return _UNKNOWN
            playlist_id = _parse_playlist_id(qs)
            if playlist_id:
                return YouTubeUrlInfo(kind="video_in_playlist", video_id=video_id, playlist_id=playlist_id)
            return YouTubeUrlInfo(kind="video", video_id=video_id, playlist_id=None)

    return _UNKNOWN
