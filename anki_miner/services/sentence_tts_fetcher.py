"""Sentence-level TTS fetchers for reading sources (manga/novels).

Reading-sourced cards have no source audio, so sentence audio is synthesized:
Google Translate TTS (gtts) first, Naver Papago as fallback, walked by
:class:`ChainedSentenceAudioFetcher`. All fetchers implement the
:class:`~anki_miner.interfaces.SentenceAudioFetcher` protocol structurally.

Design notes (mirroring the expression-audio fetchers):

* **Never raises.** The Phase-3' loop has no try/except by design. This is
  load-bearing for Papago especially: it is an unofficial scraped endpoint
  (contract verified against HyperTTS v3.3.0 and a live smoke) whose response
  shape may drift — any drift must degrade to "no audio", never abort a run.
* **No ``.miss`` markers.** Synthesis failures are transient (network / rate
  limit) and retried next run — the googletts word-fetcher precedent.
* **Content-hash cache keys.** Sentences are long and contain characters
  unfit for filenames, so the cache stem hashes the NFC-normalized, stripped
  text: ``sentencetts_{provider}_{sha1[:16]}``. The stem doubles as the Anki
  media filename base, unique per (provider, sentence).
"""

import hashlib
import logging
import time
import unicodedata
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import requests

from anki_miner.services.audio_fetch_common import (
    aggregate_failure_stats as _aggregate_failure_stats,
)
from anki_miner.services.audio_fetch_common import (
    classify_request_exception as _classify_request_exception,
)
from anki_miner.services.audio_fetch_common import (
    close_all as _close_all,
)
from anki_miner.services.audio_fetch_common import (
    download_audio_to_cache,
)
from anki_miner.services.audio_fetch_common import (
    find_cached_by_stem as _find_cached_by_stem,
)
from anki_miner.services.audio_fetch_common import (
    new_browser_session as _new_browser_session,
)
from anki_miner.services.audio_fetch_common import (
    new_failure_counts as _new_failure_counts,
)
from anki_miner.services.google_translate_audio_fetcher import _synthesize_gtts_to_cache

if TYPE_CHECKING:
    from anki_miner.interfaces import SentenceAudioFetcher

logger = logging.getLogger(__name__)

# Input guard. Manga bubbles run 2-40 chars and novel sentences 10-60, but
# mokuro's _BLOCK_SPLIT_THRESHOLD (120) only *triggers* sentence splitting —
# a punctuation-free OCR block stays one piece of unbounded length. 300
# bounds those un-splittable runs and protects Papago's undocumented limit.
MAX_TTS_SENTENCE_CHARS = 300

PAPAGO_MAKE_ID_URL = "https://papago.naver.com/api/tts/makeID"
PAPAGO_TTS_URL = "https://papago.naver.com/api/tts/{id}"
PAPAGO_SPEAKER_JA = "yuri"
# The endpoint 403s requests missing the browser-ish header set (the old
# HMAC "PPG" auth died in the 2025 Next.js rewrite; these headers are all it
# checks now). Installed on the Session — not per-request — so the follow-up
# audio GET (which goes through download_audio_to_cache, which passes no
# per-request headers) carries them too.
_PAPAGO_HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Origin": "https://papago.naver.com",
    "Referer": "https://papago.naver.com/",
}


def _sentence_stem(provider: str, sentence: str) -> str:
    """Return the cache/media filename stem for *sentence* under *provider*."""
    text = unicodedata.normalize("NFC", sentence).strip()
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
    return f"sentencetts_{provider}_{digest}"


def _reject_input(sentence: str) -> bool:
    """True when *sentence* should be skipped without a failure-count bump."""
    stripped = sentence.strip()
    return not stripped or len(stripped) > MAX_TTS_SENTENCE_CHARS


class GoogleSentenceTtsFetcher:
    """Synthesizes sentence audio via Google Translate TTS (gtts)."""

    def __init__(self, cache_dir: Path, delay: float = 0.2):
        """Initialize with cache directory and politeness delay.

        Args:
            cache_dir: Directory for cached mp3s (caller passes
                ``~/.anki_miner/audio_cache/sentence_tts/``).
            delay: Seconds to wait before each synthesis request.
        """
        self._cache_dir = cache_dir
        # NaN must clamp to 0.0 (time.sleep(nan) raises); the >= comparison
        # is False for nan, so the else branch handles it.
        self._delay = delay if delay >= 0.0 else 0.0
        self._failure_counts = _new_failure_counts()

    def fetch(
        self,
        sentence: str,
        cancelled_check: Callable[[], bool] | None = None,
    ) -> Path | None:
        """Synthesize audio for *sentence*. Never raises.

        Feeds the surface text (kanji included) — unlike the word fetcher,
        which feeds the kana reading: full-sentence context is what lets the
        synthesizer disambiguate homographs.
        """
        if _reject_input(sentence):
            return None

        if cancelled_check is not None and cancelled_check():
            return None

        return _synthesize_gtts_to_cache(
            self._cache_dir,
            text=sentence,
            stem=_sentence_stem("google", sentence),
            delay=self._delay,
            failure_counts=self._failure_counts,
            cancelled_check=cancelled_check,
        )

    def stats(self) -> dict[str, int]:
        """Return a copy of this run's failure-cause counts (see FAILURE_KEYS)."""
        return dict(self._failure_counts)

    def close(self) -> None:
        """No-op: gtts opens a per-call connection, no persistent handle."""
        pass


class PapagoSentenceTtsFetcher:
    """Synthesizes sentence audio via Naver Papago's public TTS endpoint.

    Two-step contract (no auth): POST ``makeID`` with the text and speaker,
    receive ``{"id": ...}``, then GET the audio at ``/api/tts/{id}``.
    """

    def __init__(self, cache_dir: Path, delay: float = 0.2):
        """Initialize with cache directory and politeness delay.

        The politeness sleep runs once before the makeID POST; the follow-up
        GET is the second half of the same logical fetch (like following a
        redirect) and is not delayed again.
        """
        self._cache_dir = cache_dir
        # NaN clamp, same as the gtts fetchers.
        self._delay = delay if delay >= 0.0 else 0.0
        self._failure_counts = _new_failure_counts()
        self._session = _new_browser_session()
        self._session.headers.update(_PAPAGO_HEADERS)

    def fetch(
        self,
        sentence: str,
        cancelled_check: Callable[[], bool] | None = None,
    ) -> Path | None:
        """Synthesize audio for *sentence*. Never raises."""
        if _reject_input(sentence):
            return None

        if cancelled_check is not None and cancelled_check():
            return None

        stem = _sentence_stem("papago", sentence)

        # Extension may vary by Content-Type (download_audio_to_cache picks
        # it), so match any suffix for the cache hit.
        cached = _find_cached_by_stem(self._cache_dir, stem)
        if cached is not None:
            return cached

        # The except tuple is deliberately broad: on a scraped endpoint a
        # response-shape drift (JSON list instead of dict, error object,
        # non-JSON HTML) must bucket as a transient failure, never raise —
        # a raise here would propagate through the phase-3' loop and abort
        # the whole reading run.
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)

            if cancelled_check is not None and cancelled_check():
                return None

            time.sleep(self._delay)

            if cancelled_check is not None and cancelled_check():
                return None

            response = self._session.post(
                PAPAGO_MAKE_ID_URL,
                data={
                    "alpha": 0,
                    "pitch": 0,
                    "speaker": PAPAGO_SPEAKER_JA,
                    "speed": 0,
                    "text": sentence,
                },
                timeout=10,
            )
            if response.status_code != 200:
                self._failure_counts["http_status"] += 1
                return None

            # A rate-limit/HTML body or a shape drift is the same bucket the
            # other non-audio-response paths use: non_audio (not connection).
            try:
                data = response.json()
            except ValueError:
                self._failure_counts["non_audio"] += 1
                return None
            tts_id = data.get("id") if isinstance(data, dict) else None
            if not isinstance(tts_id, str) or not tts_id:
                self._failure_counts["non_audio"] += 1
                return None

            if cancelled_check is not None and cancelled_check():
                return None

            return download_audio_to_cache(
                self._session,
                PAPAGO_TTS_URL.format(id=tts_id),
                self._cache_dir,
                stem,
                failure_counts=self._failure_counts,
            )
        except (requests.RequestException, OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            self._failure_counts[_classify_request_exception(exc)] += 1
            logger.debug("papago sentence tts failed for %s: %s", stem, exc)
            return None

    def stats(self) -> dict[str, int]:
        """Return a copy of this run's failure-cause counts (see FAILURE_KEYS)."""
        return dict(self._failure_counts)

    def close(self) -> None:
        """Release the per-run HTTP session (Windows back-to-back-run hygiene)."""
        self._session.close()


class ChainedSentenceAudioFetcher:
    """Composite sentence fetcher: walks members left-to-right, first hit wins.

    Sentence analogue of :class:`ChainedExpressionAudioFetcher` (no candidate
    ladder). An empty chain returns None. Members are assumed to honor the
    never-raises contract; no try/except is added here.
    """

    def __init__(self, fetchers: "Sequence[SentenceAudioFetcher]") -> None:
        self._fetchers: list[SentenceAudioFetcher] = list(fetchers)

    def fetch(
        self,
        sentence: str,
        cancelled_check: Callable[[], bool] | None = None,
    ) -> Path | None:
        """Return the first non-None result from the chain. Never raises."""
        for fetcher in self._fetchers:
            if cancelled_check is not None and cancelled_check():
                return None
            result = fetcher.fetch(sentence, cancelled_check)
            if result is not None:
                return result
        return None

    def stats(self) -> dict[str, int]:
        """Aggregate per-run failure-cause counts across members (duck-typed)."""
        return _aggregate_failure_stats(self._fetchers)

    def close(self) -> None:
        """Fan out ``close()`` to every member that defines one (duck-typed)."""
        _close_all(self._fetchers)
