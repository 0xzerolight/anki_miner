# Derived from Yomitan (https://github.com/yomidevs/yomitan),
# ext/js/media/audio-downloader.js (AudioDownloader._getInfoLanguagePod101,
# _validateLanguagePod101Row, _getInfoJisho), commit
# e2ed450c2f11a591922822e77f008e70a87daf0c.
#
# Copyright (C) 2023-2026  Yomitan Authors
# Copyright (C) 2026  anki_miner contributors (Python port)
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""HTML-scrape expression-audio fetchers (JapanesePod101 dictionary, Jisho.org).

These port Yomitan's default Japanese real-recording sources beyond the thin
``audiomp3.php`` endpoint. Both are fragile by nature — a site redesign breaks
the CSS-class/element-id extraction — so they are NOT part of the default audio
chain (a user adds them explicitly) and mirror Yomitan's swallow-and-return-empty
semantics: any parse/network failure degrades to "no candidates", never an abort.

The JapanesePod101 dictionary source ports the per-row ``dc-vocab_kana`` reading
validation verbatim: that check is what prevents a homograph's wrong recording
(辛い → からい vs つらい) being pasted onto an unreviewed batch card.

Parsing uses ``lxml.html`` (already a project dependency): the extraction is
nested and class/id-based (row → ``<audio>`` → ``<source>``; row → span.dc-vocab_kana),
which a flat stdlib ``html.parser`` SAX walk handles far less robustly. Never
raises — the Phase-3 pipeline loop has no try/except by design.
"""

import logging
import time
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote, urljoin

import requests
from lxml import html as lxml_html  # type: ignore[import-untyped]  # lxml ships no stubs; lxml-stubs not a dep

from anki_miner.services.expression_audio_fetcher import (
    _classify_request_exception,
    _find_cached_by_stem,
    _first_candidate_hit,
    _new_browser_session,
    _new_failure_counts,
    download_audio_to_cache,
)
from anki_miner.utils.file_utils import safe_filename

logger = logging.getLogger(__name__)


def _validate_jpod101_row(row: "lxml_html.HtmlElement", term: str, reading: str) -> bool:
    """Return True if a ``dc-result-row`` matches the requested reading.

    Ported verbatim from Yomitan ``_validateLanguagePod101Row`` (Japanese
    branch), commit e2ed450: a kana word (``reading == term``) needs no kana
    check; otherwise the row's ``dc-vocab_kana`` text must equal the reading, so
    a homograph's other pronunciation is rejected.
    """
    html_readings = row.find_class("dc-vocab_kana")
    if not html_readings:
        return False
    html_reading = html_readings[0].text_content()
    if not html_reading or not html_reading.strip():
        return False
    # Valid iff a kana word (reading == term, no kana check) OR the row's kana
    # equals the requested reading (homograph guard) — ported from upstream's
    # `reading !== term && reading !== htmlReading` rejection.
    return reading == term or reading == html_reading.strip()


def _extract_jpod101_audio_urls(html_text: str, base_url: str, term: str, reading: str) -> list[str]:
    """Extract validated audio URLs from a JapanesePod101 dictionary response.

    Ported from Yomitan ``_getInfoLanguagePod101`` (commit e2ed450): each
    ``dc-result-row``'s first ``<audio>`` ``<source>`` src is taken iff the row
    passes ``_validate_jpod101_row``, then normalized against the response URL.
    Any parse failure yields ``[]`` (swallow-and-return-empty).
    """
    try:
        doc = lxml_html.fromstring(html_text)
    except Exception:
        return []
    urls: list[str] = []
    for row in doc.find_class("dc-result-row"):
        src_list = row.xpath(".//audio//source/@src")
        if not src_list:
            continue
        src = src_list[0]
        if not src:
            continue
        if not _validate_jpod101_row(row, term, reading):
            continue
        urls.append(urljoin(base_url, src))
    return urls


def _extract_jisho_audio_urls(html_text: str, base_url: str, term: str, reading: str) -> list[str]:
    """Extract the audio URL for ``audio_<term>:<reading>`` from a Jisho page.

    Ported from Yomitan ``_getInfoJisho`` (commit e2ed450): the element whose id
    is ``audio_<term>:<reading>`` holds the recording, so the id itself is the
    reading guard. Any parse failure or missing element yields ``[]``. The id
    lookup uses an xpath variable binding so a term with quotes cannot break the
    query.
    """
    try:
        doc = lxml_html.fromstring(html_text)
    except Exception:
        return []
    audio_id = f"audio_{term}:{reading}"
    elements = doc.xpath("//*[@id=$aid]", aid=audio_id)
    if not elements:
        return []
    src_list = elements[0].xpath(".//source/@src")
    if not src_list or not src_list[0]:
        return []
    return [urljoin(base_url, src_list[0])]


class JPod101DictionaryScrapeFetcher:
    """Scrapes real word recordings from the JapanesePod101 online dictionary.

    Conforms to the :class:`~anki_miner.interfaces.ExpressionAudioFetcher`
    Protocol structurally (never raises; returns Path or None). No ``.miss``
    markers — a scrape miss now may be a hit later, and the source is fragile.
    """

    _FETCH_URL = "https://www.japanesepod101.com/learningcenter/reference/dictionary_post"
    _FILE_PREFIX = "jpod101scrape"

    def __init__(self, cache_dir: Path, delay: float = 0.2) -> None:
        self._cache_dir = cache_dir
        # NaN clamps to 0.0 (time.sleep(nan) raises); >= is False for nan.
        self._delay = delay if delay >= 0.0 else 0.0
        self._session = _new_browser_session()
        self._failure_counts = _new_failure_counts()

    def fetch(
        self,
        mined_form: str,
        reading: str,
        cancelled_check: Callable[[], bool] | None = None,
    ) -> Path | None:
        """Scrape + download a recording for a word. Never raises.

        Empty ``mined_form``/``reading`` skips (the reading is the homograph
        guard, matching every other fetcher's empty-reading skip).
        """
        if not mined_form.strip() or not reading.strip():
            return None
        if cancelled_check is not None and cancelled_check():
            return None

        stem = safe_filename(f"{self._FILE_PREFIX}_{mined_form}_{reading}")
        existing = _find_cached_by_stem(self._cache_dir, stem)
        if existing is not None:
            return existing

        if cancelled_check is not None and cancelled_check():
            return None
        time.sleep(self._delay)
        if cancelled_check is not None and cancelled_check():
            return None

        for url in self._scrape_urls(mined_form, reading):
            if cancelled_check is not None and cancelled_check():
                return None
            result = download_audio_to_cache(
                self._session, url, self._cache_dir, stem, failure_counts=self._failure_counts
            )
            if result is not None:
                return result
        return None

    def _scrape_urls(self, term: str, reading: str) -> list[str]:
        """POST the dictionary query and return validated audio URLs ([] on error)."""
        try:
            response = self._session.post(
                self._FETCH_URL,
                data={
                    "post": "dictionary_reference",
                    "match_type": "exact",
                    "search_query": term,
                    "vulgar": "true",
                },
                timeout=10,
            )
            try:
                if response.status_code != 200:
                    self._failure_counts["http_status"] += 1
                    return []
                text = response.text
                base = response.url
            finally:
                response.close()
        except (requests.RequestException, OSError) as exc:
            self._failure_counts[_classify_request_exception(exc)] += 1
            logger.debug("jpod101 dictionary scrape failed for %s: %s", term, exc)
            return []
        return _extract_jpod101_audio_urls(text, base, term, reading)

    def fetch_candidates(
        self,
        candidates: list[tuple[str, str]],
        cancelled_check: Callable[[], bool] | None = None,
    ) -> Path | None:
        """Try each candidate form, returning the first scrape hit."""
        return _first_candidate_hit(self, candidates, cancelled_check)

    def stats(self) -> dict[str, int]:
        """Return a copy of this run's failure-cause counts (see FAILURE_KEYS)."""
        return dict(self._failure_counts)

    def close(self) -> None:
        """Close the underlying ``requests.Session`` (release the per-run socket)."""
        self._session.close()


class JishoScrapeFetcher:
    """Scrapes real word recordings from Jisho.org search pages.

    Conforms to the :class:`~anki_miner.interfaces.ExpressionAudioFetcher`
    Protocol structurally (never raises; returns Path or None). No ``.miss``
    markers.
    """

    _FILE_PREFIX = "jishoscrape"

    def __init__(self, cache_dir: Path, delay: float = 0.2) -> None:
        self._cache_dir = cache_dir
        self._delay = delay if delay >= 0.0 else 0.0
        self._session = _new_browser_session()
        self._failure_counts = _new_failure_counts()

    def fetch(
        self,
        mined_form: str,
        reading: str,
        cancelled_check: Callable[[], bool] | None = None,
    ) -> Path | None:
        """Scrape + download a Jisho recording for a word. Never raises."""
        if not mined_form.strip() or not reading.strip():
            return None
        if cancelled_check is not None and cancelled_check():
            return None

        stem = safe_filename(f"{self._FILE_PREFIX}_{mined_form}_{reading}")
        existing = _find_cached_by_stem(self._cache_dir, stem)
        if existing is not None:
            return existing

        if cancelled_check is not None and cancelled_check():
            return None
        time.sleep(self._delay)
        if cancelled_check is not None and cancelled_check():
            return None

        for url in self._scrape_urls(mined_form, reading):
            if cancelled_check is not None and cancelled_check():
                return None
            result = download_audio_to_cache(
                self._session, url, self._cache_dir, stem, failure_counts=self._failure_counts
            )
            if result is not None:
                return result
        return None

    def _scrape_urls(self, term: str, reading: str) -> list[str]:
        """GET the Jisho search page and return the matching audio URL ([] on error)."""
        url = f"https://jisho.org/search/{quote(term)}"
        try:
            response = self._session.get(url, timeout=10)
            try:
                if response.status_code != 200:
                    self._failure_counts["http_status"] += 1
                    return []
                text = response.text
                base = response.url
            finally:
                response.close()
        except (requests.RequestException, OSError) as exc:
            self._failure_counts[_classify_request_exception(exc)] += 1
            logger.debug("jisho scrape failed for %s: %s", term, exc)
            return []
        return _extract_jisho_audio_urls(text, base, term, reading)

    def fetch_candidates(
        self,
        candidates: list[tuple[str, str]],
        cancelled_check: Callable[[], bool] | None = None,
    ) -> Path | None:
        """Try each candidate form, returning the first scrape hit."""
        return _first_candidate_hit(self, candidates, cancelled_check)

    def stats(self) -> dict[str, int]:
        """Return a copy of this run's failure-cause counts (see FAILURE_KEYS)."""
        return dict(self._failure_counts)

    def close(self) -> None:
        """Close the underlying ``requests.Session`` (release the per-run socket)."""
        self._session.close()
