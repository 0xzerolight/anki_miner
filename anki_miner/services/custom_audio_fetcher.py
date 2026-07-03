"""Custom URL / custom-JSON expression-audio fetcher.

The local-audio-yomichan integration contract: a user points anki_miner at a
URL template containing ``{term}`` / ``{reading}`` / ``{language}`` placeholders.

* ``custom``      — the templated URL returns the audio bytes directly.
* ``custom_json`` — the templated URL returns an ``audioSourceList`` JSON
  document (``{"type": "audioSourceList", "audioSources": [{"url": ...}, ...]}``);
  each listed URL is tried in order, first successful download wins.

Design mirrors :class:`JPod101AudioFetcher` (shared Session/UA/size-cap plumbing
via ``download_audio_to_cache``) with two deliberate differences: NO ``.miss``
negative markers (a custom server's contents change, so a miss now may be a hit
later), and the cache directory is per-source (``audio_cache/custom_<slug>/``).
Never raises — the Phase-3 pipeline loop has no try/except by design.
"""

import hashlib
import logging
import re
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urljoin

import requests

from anki_miner.services.expression_audio_fetcher import (
    _find_cached_by_stem,
    _first_candidate_hit,
    _new_browser_session,
    _new_failure_counts,
    download_audio_to_cache,
)
from anki_miner.utils.file_utils import safe_filename

logger = logging.getLogger(__name__)

# Matches a ``{placeholder}`` token. Ported (with _substitute_custom_url below)
# from Yomitan ext/js/media/audio-downloader.js AudioDownloader._getCustomUrl,
# upstream commit e2ed450c2f11a591922822e77f008e70a87daf0c.
_PLACEHOLDER_RE = re.compile(r"\{([^}]*)\}")


def _substitute_custom_url(template: str, term: str, reading: str, language: str) -> str:
    """Substitute ``{term}``/``{reading}``/``{language}`` in *template*.

    Ported from Yomitan ``AudioDownloader._getCustomUrl`` (commit e2ed450). An
    unknown ``{name}`` placeholder is left intact, exactly as upstream does.
    """
    data = {"term": term, "reading": reading, "language": language}

    def _replace(match: "re.Match[str]") -> str:
        key = match.group(1)
        return data[key] if key in data else match.group(0)

    return _PLACEHOLDER_RE.sub(_replace, template)


def custom_audio_slug(url_template: str) -> str:
    """Return a short, stable, filesystem-safe slug for a custom-source URL.

    Keys the per-source cache directory (``custom_<slug>``) and the cached Anki
    media filename so two custom sources never collide. A hash (not the raw URL)
    keeps the name short and free of URL metacharacters.
    """
    return hashlib.sha1(url_template.encode("utf-8")).hexdigest()[:10]


class CustomAudioFetcher:
    """Fetches word pronunciation audio from a user-configured URL template.

    Conforms to the :class:`~anki_miner.interfaces.ExpressionAudioFetcher`
    Protocol structurally (never raises; returns Path or None).
    """

    def __init__(
        self,
        url_template: str,
        kind: str,
        cache_dir: Path,
        file_prefix: str,
        delay: float = 0.2,
        language: str = "ja",
    ) -> None:
        """Initialize the fetcher.

        Args:
            url_template: The user's URL with ``{term}``/``{reading}``/
                ``{language}`` placeholders.
            kind: ``"custom"`` (direct audio) or ``"custom_json"`` (audioSourceList).
            cache_dir: Per-source cache directory (``audio_cache/custom_<slug>/``).
            file_prefix: Prefix for the cached Anki media filename; must be
                globally unique across sources (the factory uses
                ``custom_<slug>``).
            delay: Seconds to wait before the first network request per word.
            language: Value substituted for ``{language}`` (fixed "ja" here).
        """
        self._url_template = url_template
        self._kind = kind
        self._cache_dir = cache_dir
        self._file_prefix = file_prefix
        # NaN must clamp to 0.0 (time.sleep(nan) raises); the >= comparison is
        # False for nan, so the else branch handles it.
        self._delay = delay if delay >= 0.0 else 0.0
        self._language = language
        self._session = _new_browser_session()
        self._failure_counts = _new_failure_counts()

    def fetch(
        self,
        mined_form: str,
        reading: str,
        cancelled_check: Callable[[], bool] | None = None,
    ) -> Path | None:
        """Fetch pronunciation audio for a word. Never raises.

        An empty/whitespace ``mined_form`` or ``reading`` skips the fetch: the
        reading feeds the URL template and disambiguates homographs, matching the
        empty-reading skip every other fetcher applies.
        """
        if not mined_form.strip() or not reading.strip():
            return None
        if cancelled_check is not None and cancelled_check():
            return None

        stem = safe_filename(f"{self._file_prefix}_{mined_form}_{reading}")
        existing = _find_cached_by_stem(self._cache_dir, stem)
        if existing is not None:
            return existing

        if cancelled_check is not None and cancelled_check():
            return None
        time.sleep(self._delay)
        if cancelled_check is not None and cancelled_check():
            return None

        endpoint = _substitute_custom_url(self._url_template, mined_form, reading, self._language)
        audio_urls = self._resolve_json_sources(endpoint) if self._kind == "custom_json" else [endpoint]

        for audio_url in audio_urls:
            if cancelled_check is not None and cancelled_check():
                return None
            result = download_audio_to_cache(
                self._session,
                audio_url,
                self._cache_dir,
                stem,
                failure_counts=self._failure_counts,
            )
            if result is not None:
                return result
        return None

    def _resolve_json_sources(self, url: str) -> list[str]:
        """GET the custom-json endpoint and return its audio URLs (never raises).

        Manual shape check (no JSON-Schema engine): the document must be an
        object with ``type == "audioSourceList"`` and an ``audioSources`` list;
        each item contributes its string ``url`` (relative URLs normalized
        against the endpoint). Any malformed/failed response yields ``[]``.
        """
        try:
            response = self._session.get(url, timeout=10)
            try:
                if response.status_code != 200:
                    self._failure_counts["http_status"] += 1
                    return []
                data = response.json()
                base = response.url
            finally:
                response.close()
        except (requests.RequestException, OSError, ValueError) as exc:
            # ValueError covers json.JSONDecodeError (non-JSON body).
            self._failure_counts["non_audio"] += 1
            logger.debug("custom_json fetch failed for %s: %s", url, exc)
            return []

        if not isinstance(data, dict) or data.get("type") != "audioSourceList":
            return []
        sources = data.get("audioSources")
        if not isinstance(sources, list):
            return []
        urls: list[str] = []
        for item in sources:
            if not isinstance(item, dict):
                continue
            candidate = item.get("url")
            if isinstance(candidate, str) and candidate:
                urls.append(urljoin(base, candidate))
        return urls

    def fetch_candidates(
        self,
        candidates: list[tuple[str, str]],
        cancelled_check: Callable[[], bool] | None = None,
    ) -> Path | None:
        """Try each candidate form, returning the first custom-source hit."""
        return _first_candidate_hit(self, candidates, cancelled_check)

    def stats(self) -> dict[str, int]:
        """Return a copy of this run's failure-cause counts (see FAILURE_KEYS)."""
        return dict(self._failure_counts)

    def close(self) -> None:
        """Close the underlying ``requests.Session`` (release the per-run socket)."""
        self._session.close()
