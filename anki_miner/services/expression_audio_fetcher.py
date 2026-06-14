"""Expression audio fetchers: JPod101 and chained composite."""

import contextlib
import hashlib
import logging
import os
import tempfile
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import requests

from anki_miner.utils.file_utils import safe_filename

if TYPE_CHECKING:
    from anki_miner.interfaces.expression_audio import ExpressionAudioFetcher

logger = logging.getLogger(__name__)

JPOD101_AUDIO_URL = "https://assets.languagepod101.com/dictionary/japanese/audiomp3.php"

# JPod101 answers unknown words with HTTP 200 and a fixed "audio not
# available" placeholder mp3. This is the SHA-256 of that placeholder
# (same value Yomitan hardcodes) — matching bodies are treated as misses.
JPOD101_NOT_FOUND_SHA256 = "ae6398b5a27bc8c0a771df6c907ade794be15518174773c58c7c7ddd17098906"

# Real word audio is ~10–100 KB. 5 MB is a generous upper bound; anything
# larger is almost certainly an error page or CDN redirect body.
MAX_AUDIO_BYTES = 5 * 1024 * 1024

# Stale .part files older than this threshold (seconds) are swept on
# cache-miss fetch() calls (warm-cache hits return before the sweep). Files
# younger than this are assumed to belong to a concurrent in-progress
# download and are left alone.
STALE_PART_AGE_SECONDS = 60


def _is_mp3(body: bytes) -> bool:
    """Return True if *body* looks like MP3 audio.

    Accepts either an ID3v2 tag header (b"ID3") or a raw MPEG frame-sync
    sequence (first byte 0xFF, top 3 bits of second byte all set).
    """
    if len(body) < 2:
        return False
    if body[:3] == b"ID3":
        return True
    # MPEG frame sync: 0xFF followed by a byte whose top 3 bits are all 1.
    return bool(body[0] == 0xFF and (body[1] & 0xE0) == 0xE0)


def _first_candidate_hit(
    fetcher: "ExpressionAudioFetcher",
    candidates: list[tuple[str, str]],
    cancelled_check: Callable[[], bool] | None,
) -> Path | None:
    """Try each candidate via ``fetcher.fetch``, returning the first hit.

    Shared leaf implementation of ``fetch_candidates``: a single source
    exhausts its retry ladder (surface, katakana, lemma, ...) before the
    caller moves on.  Checks ``cancelled_check`` between candidates so a leaf
    used standalone honors cancellation like the composite does.
    """
    for mined_form, reading in candidates:
        if cancelled_check is not None and cancelled_check():
            return None
        result = fetcher.fetch(mined_form, reading, cancelled_check)
        if result is not None:
            return result
    return None


class JPod101AudioFetcher:
    """Fetches word pronunciation audio from JapanesePod101.

    Results are cached on disk: successful downloads as ``.mp3`` files,
    confirmed not-found words as zero-byte ``.miss`` markers so they are
    never re-requested. Transient failures (timeouts, non-200 status,
    HTTPS downgrade, oversized body, non-audio body) are not cached and
    will be retried on the next call.

    Non-audio bodies such as HTML rate-limit pages are treated as transient
    failures — no ``.miss`` marker is written — so affected words are
    retried automatically on the next run.
    """

    def __init__(self, cache_dir: Path, delay: float = 0.2):
        """Initialize with cache directory and rate-limiting delay.

        Args:
            cache_dir: Directory for cached mp3s and miss markers.
            delay: Seconds to wait before each network request.
        """
        self._cache_dir = cache_dir
        # NaN must clamp to 0.0 (time.sleep(nan) raises). max(0.0, delay)
        # keeps 0.0 for nan only by argument-order accident; the explicit
        # comparison states the intent.
        self._delay = delay if delay >= 0.0 else 0.0
        # Not thread-safe; safe because each processor builds its own fetcher
        # (service_factory creates fresh Services per create_episode_processor call).
        self._session = requests.Session()

    def fetch(
        self,
        mined_form: str,
        reading: str,
        cancelled_check: Callable[[], bool] | None = None,
    ) -> Path | None:
        """Fetch pronunciation audio for a word.

        Args:
            mined_form: Word as mined onto the card (kanji/surface form).
            reading: Kana reading of the word.  An empty or whitespace-only
                reading skips the fetch entirely: without ``kana`` the JPod101
                endpoint guesses a reading for the kanji, which picks the wrong
                pronunciation for homographs (e.g. 辛い → からい vs つらい) and
                caches that incorrect audio permanently under the word's key.
            cancelled_check: Optional zero-argument callable that returns True
                when the caller has requested cancellation.  Consulted after
                the input guards, again immediately before ``time.sleep``, and
                once more before the network request.  When it returns True this
                method returns None immediately — no cache writes, no .miss
                marker.  Mid-request cancellation is NOT attempted: the timeout
                (10 s) already bounds the worst-case stall per word.

        Returns:
            Path to a cached mp3, or None if unavailable.
        """
        if not mined_form.strip() or not reading.strip():
            return None

        if cancelled_check is not None and cancelled_check():
            return None

        stem = safe_filename(f"jpod101_{mined_form}_{reading}")
        mp3_path = self._cache_dir / f"{stem}.mp3"
        miss_path = self._cache_dir / f"{stem}.miss"

        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)

            if mp3_path.exists() and mp3_path.stat().st_size > 0:
                return mp3_path
            if miss_path.exists():
                return None

            # Sweep orphaned .part files left by previous crashes. Only runs on
            # cold paths (cache miss), so warm-cache calls skip the glob entirely.
            # Only removes files older than STALE_PART_AGE_SECONDS to avoid
            # deleting a live stage file from a concurrent worker on the same word.
            now = time.time()
            for part_file in self._cache_dir.glob("*.part"):
                try:
                    if now - part_file.stat().st_mtime > STALE_PART_AGE_SECONDS:
                        part_file.unlink()
                except OSError:
                    pass

            if cancelled_check is not None and cancelled_check():
                return None

            time.sleep(self._delay)

            if cancelled_check is not None and cancelled_check():
                return None

            # Valid words 301-redirect to a CDN mp3; requests follows
            # redirects by default, so the final body is the audio itself.
            # stream=True lets us cap the body size before buffering it all.
            response = self._session.get(
                JPOD101_AUDIO_URL,
                params={"kanji": mined_form, "kana": reading},
                timeout=10,
                stream=True,
            )

            try:
                if response.status_code != 200:
                    return None

                # A redirect that downgrades HTTPS → HTTP could expose audio
                # data in transit; treat as transient so it is retried next run.
                if not response.url.startswith("https://"):
                    return None

                # Read the body in chunks, aborting if it exceeds MAX_AUDIO_BYTES.
                # Real word audio is ~10–100 KB; anything larger is almost certainly
                # an error page or unexpected CDN response.
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_content(chunk_size=8192):
                    total += len(chunk)
                    if total > MAX_AUDIO_BYTES:
                        # Oversized — transient failure, nothing written.
                        return None
                    chunks.append(chunk)
                body = b"".join(chunks)

                # Zero-byte 200 is ambiguous (network glitch, premature close) —
                # treat as transient failure, not a confirmed miss.
                if not body:
                    return None

                if hashlib.sha256(body).hexdigest() == JPOD101_NOT_FOUND_SHA256:
                    # Confirmed not-found: marker prevents re-requesting.
                    # Miss markers are permanent by design (Yomitan-style); delete
                    # the cache dir to retry words that were incorrectly marked.
                    miss_path.touch()
                    return None

                # Reject non-audio bodies (HTML error pages, CDN text responses,
                # etc.) as transient failures. No .miss marker so the word is
                # retried on the next run once the rate-limit clears.
                if not _is_mp3(body):
                    return None

                # Write atomically: stage to a unique temp file then rename so
                # a killed process cannot leave a truncated mp3 that passes the
                # st_size > 0 cache-hit check on the next run. Unique names
                # (via NamedTemporaryFile) prevent two concurrent workers
                # fetching the same uncached word from interleaving writes into
                # the same stage file and corrupting the cached result.
                with tempfile.NamedTemporaryFile(dir=self._cache_dir, suffix=".part", delete=False) as tmp_fd:
                    tmp_name = tmp_fd.name
                    try:
                        tmp_fd.write(body)
                    except OSError:
                        with contextlib.suppress(OSError):
                            Path(tmp_name).unlink()
                        raise
                try:
                    os.replace(tmp_name, mp3_path)
                except OSError:
                    with contextlib.suppress(OSError):
                        Path(tmp_name).unlink()
                    raise
                return mp3_path
            finally:
                response.close()

        except (requests.RequestException, OSError) as exc:
            logger.debug("expression audio fetch failed for %s: %s", mined_form, exc)
            return None

    def fetch_candidates(
        self,
        candidates: list[tuple[str, str]],
        cancelled_check: Callable[[], bool] | None = None,
    ) -> Path | None:
        """Try each candidate form, returning the first JPod101 hit."""
        return _first_candidate_hit(self, candidates, cancelled_check)


class ChainedExpressionAudioFetcher:
    """Composite fetcher that walks a sequence of fetchers, first hit wins.

    Implements the :class:`~anki_miner.interfaces.ExpressionAudioFetcher`
    protocol structurally.  An empty chain returns None.  Members are assumed
    to honor the protocol contract (never raise); no try/except is added here.
    """

    def __init__(self, fetchers: "Sequence[ExpressionAudioFetcher]") -> None:
        """Initialize with an ordered list of fetchers.

        Args:
            fetchers: Fetchers tried left-to-right; first non-None Path wins.
        """
        self._fetchers: list[ExpressionAudioFetcher] = list(fetchers)

    def fetch(
        self,
        mined_form: str,
        reading: str,
        cancelled_check: Callable[[], bool] | None = None,
    ) -> Path | None:
        """Return the first non-None result from the fetcher chain.

        Args:
            mined_form: Word as mined onto the card (kanji/surface form).
            reading: Kana reading of the word (may be empty).
            cancelled_check: Optional zero-argument callable that returns True
                when the caller has requested cancellation.  Forwarded to every
                member fetcher and also consulted between members, so a chain
                stops walking as soon as cancellation is observed.  Returns
                None immediately on cancellation.

        Returns:
            Path to an audio file from the first matching fetcher, or None.
        """
        for fetcher in self._fetchers:
            if cancelled_check is not None and cancelled_check():
                return None
            result = fetcher.fetch(mined_form, reading, cancelled_check)
            if result is not None:
                return result
        return None

    def fetch_candidates(
        self,
        candidates: list[tuple[str, str]],
        cancelled_check: Callable[[], bool] | None = None,
    ) -> Path | None:
        """Return the first hit, source-priority outer / candidate-ladder inner.

        Each member fetcher tries ALL candidate forms (via its own
        ``fetch_candidates``) before the chain falls through to the next, lower-
        priority source.  This is the fix for the inverted nesting that let a
        synthetic fallback satisfy the surface form before a higher-priority
        source ever saw the lemma it actually has.
        """
        for fetcher in self._fetchers:
            if cancelled_check is not None and cancelled_check():
                return None
            result = fetcher.fetch_candidates(candidates, cancelled_check)
            if result is not None:
                return result
        return None
