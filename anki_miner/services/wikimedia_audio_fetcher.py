"""Wikimedia Commons word-audio fetcher (the ``wikimedia`` audio kind, F-16).

Recordings of real speakers (Wiktionary uploads and Lingua Libre) through the
keyless MediaWiki API: the spec's Stage W. One ``generator=images`` query on the
en.wiktionary page titled with the word lists every file that page embeds. The
recordings among them are filtered by the mining language's
``AudioDefaults.wikimedia_prefixes`` (``Pl-``, ``LL-Q809 (pol)-``), and the
winner's MP3 transcode on Commons is what gets cached.

The name rules are pure functions, so they are tested without the network.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Sequence
from pathlib import Path

import requests

from anki_miner import __version__
from anki_miner.services.audio_fetch_common import (
    classify_request_exception,
    download_audio_to_cache,
    first_candidate_hit,
    log_fetch_outcome,
    new_failure_counts,
    scrub_url_secrets,
)
from anki_miner.services.expression_audio_fetcher import _miss_marker_expired
from anki_miner.utils.file_utils import safe_filename

logger = logging.getLogger(__name__)

#: The MediaWiki endpoint the page query goes to: one call per word.
WIKTIONARY_API_URL = "https://en.wiktionary.org/w/api.php"

#: Wikimedia's User-Agent policy asks for a client name, version and contact URL.
#: Anonymous clients are rate-limited within a handful of calls (HTTP 429 after
#: five calls 0.6 s apart without one, probed 2026-10-15).
USER_AGENT = (
    f"AnkiMiner/{__version__} (https://github.com/0xzerolight/anki_miner) python-requests/{requests.__version__}"
)

#: At most one request per this many seconds; a word costs one query, plus one download on a hit.
MIN_REQUEST_INTERVAL_S = 1.0

_TIMEOUT_S = 10
_SOURCE = "wikimedia"

# Module attributes so a test replaces the clock and the wait, not the ``time`` module.
_sleep = time.sleep
_monotonic = time.monotonic

#: File types Commons serves recordings in. A page also embeds icons and photos.
_AUDIO_EXTENSIONS = frozenset({".ogg", ".oga", ".wav", ".mp3", ".flac", ".opus"})

#: The slot that makes a prefix entry a whole-name template (spec R29, nb's bot uploads).
_TERM_SLOT = "{term}"

#: An original upload URL: the Commons base, the two hash directories, the file name.
_COMMONS_ORIGINAL = re.compile(r"^(https://upload\.wikimedia\.org/wikipedia/commons/)([0-9a-f]/[0-9a-f]{2}/)([^/?#]+)")


def canonical_file_name(title: str) -> str:
    """``File:pl-woda.ogg`` → ``Pl-woda.ogg``: the name as Commons stores it.

    A page links a file with whatever first letter its editor typed, and
    MediaWiki capitalises the first letter of every file name, so prefixes are
    compared against that form.
    """
    name = title.split(":", 1)[1] if ":" in title else title
    return name[:1].upper() + name[1:]


def _rank(name: str, term: str, entry: str) -> int | None:
    """0 = a recording of exactly *term*, 1 = a numbered take; None = no match under *entry*."""
    if _TERM_SLOT in entry:
        head, tail = entry.split(_TERM_SLOT, 1)
        if len(name) < len(head) + len(tail) or not (name.startswith(head) and name.endswith(tail)):
            return None
        slot = name[len(head) : len(name) - len(tail)]
        if slot == term:
            return 0
        return 1 if re.fullmatch(re.escape(term) + r"\d+", slot) else None
    stem, dot, extension = name.rpartition(".")
    if not dot or f".{extension.lower()}" not in _AUDIO_EXTENSIONS or not stem.startswith(entry):
        return None
    rest = stem[len(entry) :]
    if rest == term:
        return 0
    if re.fullmatch(re.escape(term) + r"-?\d+", rest):
        return 1
    # Lingua Libre puts the speaker between the language and the word:
    # "LL-Q809 (pol)-Olaf-tak.wav". A legacy prefix ("En-us-") never does.
    if entry.startswith("LL-") and rest.endswith(f"-{term}") and len(rest) > len(term) + 1:
        return 0
    return None


def match_commons_file(names: Sequence[str], term: str, prefixes: Sequence[str]) -> str | None:
    """The file to fetch for *term*, or None.

    The first prefix with any match wins: prefix order is the language's
    preference. Within it, the word itself beats a numbered take (``Pl-dom.ogg``
    over ``Pl-dom-2.ogg``), and a tie goes to the alphabetically first name, so
    the pick is the same on every run.
    """
    for entry in prefixes:
        ranked = sorted((rank, name) for name in names if (rank := _rank(name, term, entry)) is not None)
        if ranked:
            return ranked[0][1]
    return None


def transcoded_mp3_url(url: str) -> str | None:
    """Commons' MP3 transcode of an original upload, the URL itself for an MP3, None outside Commons.

    ``…/commons/c/c2/Sv-tak.ogg`` → ``…/commons/transcoded/c/c2/Sv-tak.ogg/Sv-tak.ogg.mp3``.
    Commons serves this transcode for its audio uploads (probed for ``.ogg``,
    ``.oga`` and ``.wav``), so every cached file is an MP3.
    """
    match = _COMMONS_ORIGINAL.match(url)
    if match is None:
        return None
    base, hashed, filename = match.groups()
    if filename.lower().endswith(".mp3"):
        return f"{base}{hashed}{filename}"
    return f"{base}transcoded/{hashed}{filename}/{filename}.mp3"


def _new_session() -> requests.Session:
    """A session that names Anki Miner, as Wikimedia's User-Agent policy asks."""
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})
    return session


class WikimediaAudioFetcher:
    """Recordings of a word from Wikimedia Commons (the ``wikimedia`` chain kind).

    Implements :class:`~anki_miner.interfaces.ExpressionAudioFetcher` structurally.
    The contract it shares with ``JPod101AudioFetcher``:

    * **Never raises** (``MemoryError`` excepted): the Phase-3 loop has no try/except.
    * **``.miss`` markers** for a word the API answered for but no file matched,
      on JPod101's 180-day rule. A transport, HTTP or JSON failure writes none,
      and the word is asked again on the next run.
    * **Polite.** An identifying User-Agent, at most one request per
      ``MIN_REQUEST_INTERVAL_S`` (or ``delay`` when larger), and after one HTTP
      429 or one transport failure on the page query no further request for
      the rest of the run, so a rate-limited, blocked or hanging Commons falls
      through to the next leg instead of spending a timeout per word (two
      10 s timeouts exhaust the chain's 20 s per-word budget).
    """

    def __init__(
        self,
        cache_dir: Path,
        delay: float = 0.2,
        *,
        prefixes: tuple[str, ...],
        language: str,
        session: requests.Session | None = None,
    ) -> None:
        """Initialize with the cache directory, politeness delay and the language's prefixes.

        Args:
            cache_dir: ``audio_cache/wikimedia/``.
            delay: ``expression_audio_delay``; the throttle never goes below
                ``MIN_REQUEST_INTERVAL_S``.
            prefixes: ``AudioDefaults.wikimedia_prefixes``; empty makes every fetch a no-op.
            language: ``AudioDefaults.custom_fetcher_language``. It namespaces the
                cache stem, which doubles as the Anki media name (``die`` is a word
                in de and en).
            session: Injected by tests; a fresh identifying session otherwise.
        """
        self._cache_dir = cache_dir
        # NaN must clamp to 0.0 (time.sleep(nan) raises); nan >= 0.0 is False.
        self._delay = delay if delay >= 0.0 else 0.0
        self._prefixes = prefixes
        self._language = language
        self._session = session if session is not None else _new_session()
        self._failure_counts = new_failure_counts()
        self._last_request_at: float | None = None
        # The run-long breaker: set by an HTTP 429 or a transport failure on the page query.
        self._disabled = False

    def _stem(self, term: str) -> str:
        return safe_filename(f"wikimedia_{self._language}_{term}")

    def _wait_turn(self) -> None:
        """Sleep until the next request is allowed, then claim the slot."""
        interval = max(self._delay, MIN_REQUEST_INTERVAL_S)
        if self._last_request_at is not None:
            wait = self._last_request_at + interval - _monotonic()
            if wait > 0:
                _sleep(wait)
        self._last_request_at = _monotonic()

    def _file_urls(self, term: str) -> dict[str, str] | None:
        """Every file the en.wiktionary page *term* embeds, as canonical name → original URL.

        ``{}`` when the page is absent or embeds nothing: a definite answer.
        None on a failure worth asking again next run.
        """
        params = {
            "action": "query",
            "titles": term,
            "generator": "images",
            "gimlimit": "100",
            "prop": "imageinfo",
            "iiprop": "url|mime",
            "format": "json",
            "formatversion": "2",
        }
        self._wait_turn()
        try:
            response = self._session.get(WIKTIONARY_API_URL, params=params, timeout=_TIMEOUT_S)
        except requests.RequestException as exc:
            # Commons blocked, unreachable or hanging fails every later call the
            # same way, each at up to _TIMEOUT_S of the 20 s per-word budget, so
            # the next leg (Google TTS) would never be reached. Same breaker as a
            # 429; no .miss, so the word is asked again next run.
            self._disabled = True
            self._failure_counts[classify_request_exception(exc)] += 1
            log_fetch_outcome(
                logger,
                _SOURCE,
                term,
                "",
                WIKTIONARY_API_URL,
                reason="transport",
                error=f"{type(exc).__name__}: {scrub_url_secrets(str(exc), WIKTIONARY_API_URL, _SOURCE)}",
            )
            return None
        try:
            if response.status_code == 429:
                self._disabled = True
                self._failure_counts["http_status"] += 1
                log_fetch_outcome(logger, _SOURCE, term, "", WIKTIONARY_API_URL, status=429, reason="rate_limited")
                return None
            if response.status_code != 200:
                self._failure_counts["http_status"] += 1
                log_fetch_outcome(
                    logger, _SOURCE, term, "", WIKTIONARY_API_URL, status=response.status_code, reason="http_status"
                )
                return None
            try:
                data = response.json()
            except ValueError:
                self._failure_counts["non_audio"] += 1
                log_fetch_outcome(logger, _SOURCE, term, "", WIKTIONARY_API_URL, reason="not_json")
                return None
        finally:
            response.close()
        if not isinstance(data, dict) or "error" in data:
            self._failure_counts["non_audio"] += 1
            log_fetch_outcome(logger, _SOURCE, term, "", WIKTIONARY_API_URL, reason="api_error")
            return None
        query = data.get("query")
        pages = query.get("pages") if isinstance(query, dict) else None
        urls: dict[str, str] = {}
        for page in pages if isinstance(pages, list) else []:
            if not isinstance(page, dict):
                continue
            title = page.get("title")
            info = page.get("imageinfo")
            if not isinstance(title, str) or not isinstance(info, list) or not info or not isinstance(info[0], dict):
                continue
            url = info[0].get("url")
            if isinstance(url, str):
                urls[canonical_file_name(title)] = url.split("?", 1)[0]
        return urls

    def fetch(
        self,
        mined_form: str,
        reading: str,
        cancelled_check: Callable[[], bool] | None = None,
    ) -> Path | None:
        """A cached MP3 recording of *mined_form*, or None. Never raises.

        *reading* is unused: Commons names a recording by the written word.
        """
        term = mined_form.strip()
        if not term or not self._prefixes:
            return None
        if cancelled_check is not None and cancelled_check():
            return None
        stem = self._stem(term)
        mp3_path = self._cache_dir / f"{stem}.mp3"
        miss_path = self._cache_dir / f"{stem}.miss"
        try:
            if mp3_path.exists() and mp3_path.stat().st_size > 0:
                return mp3_path
            if miss_path.exists() and not _miss_marker_expired(miss_path):
                return None
            if self._disabled:
                return None
            urls = self._file_urls(term)
            if urls is None:
                return None
            name = match_commons_file(list(urls), term, self._prefixes)
            mp3_url = transcoded_mp3_url(urls[name]) if name is not None else None
            if mp3_url is None:
                # Confirmed absent: the API answered and no recording matches.
                # touch() also restarts an expired marker's clock.
                self._cache_dir.mkdir(parents=True, exist_ok=True)
                miss_path.touch()
                log_fetch_outcome(logger, _SOURCE, term, "", WIKTIONARY_API_URL, reason="miss_marker_written")
                return None
            if cancelled_check is not None and cancelled_check():
                return None
            self._wait_turn()
            return download_audio_to_cache(
                self._session,
                mp3_url,
                self._cache_dir,
                stem,
                timeout=_TIMEOUT_S,
                failure_counts=self._failure_counts,
                cancelled_check=cancelled_check,
                source=_SOURCE,
                word=term,
            )
        # MemoryError is not part of the never-raises contract: a starved
        # interpreter must abort the run, not keep writing cards.
        except MemoryError:
            raise
        # Broad on purpose: requests, json and the file system raise many types,
        # and the Phase-3 loop has no try/except by design.
        except Exception as exc:  # noqa: BLE001
            self._failure_counts[classify_request_exception(exc)] += 1
            log_fetch_outcome(
                logger,
                _SOURCE,
                term,
                "",
                WIKTIONARY_API_URL,
                reason="transport",
                error=f"{type(exc).__name__}: {scrub_url_secrets(str(exc), WIKTIONARY_API_URL, _SOURCE)}",
            )
            return None

    def fetch_candidates(
        self,
        candidates: list[tuple[str, str]],
        cancelled_check: Callable[[], bool] | None = None,
    ) -> Path | None:
        """Try each candidate form, returning the first recording found."""
        return first_candidate_hit(self, candidates, cancelled_check)

    def has_cached(self, mined_form: str, reading: str) -> bool | None:
        """Disk only: True for a cached recording, False for a live miss or no prefixes, else None."""
        term = mined_form.strip()
        if not term or not self._prefixes:
            return False
        stem = self._stem(term)
        try:
            mp3_path = self._cache_dir / f"{stem}.mp3"
            if mp3_path.exists() and mp3_path.stat().st_size > 0:
                return True
            miss_path = self._cache_dir / f"{stem}.miss"
            if miss_path.exists() and not _miss_marker_expired(miss_path):
                return False
        except OSError:
            return None
        return None

    def stats(self) -> dict[str, int]:
        """A copy of this run's failure-cause counts (see ``FAILURE_KEYS``)."""
        return dict(self._failure_counts)

    def close(self) -> None:
        """Close the HTTP session between runs."""
        self._session.close()
