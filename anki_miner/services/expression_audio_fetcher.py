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

# Valid words 301-redirect to the CloudFront CDN (cdn.innovativelanguage.com),
# which returns HTTP 403 + an HTML error page to the default
# "python-requests/x.y" User-Agent. A browser-style UA is required — the same a
# browser or Yomitan sends — otherwise EVERY present word fails the _is_mp3
# check and falls through to a synthetic fallback. (Genuinely-absent words are
# served the placeholder mp3 by the PHP endpoint directly, with no CDN redirect,
# so they still produce a correct .miss even with the default UA — which is why
# the symptom was "0 hits, a few misses, everything synthesized".)
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 " "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

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


# Per-run audio failure-cause buckets (Issue: audio-failure-cause-classification).
# Ported concept from Yomitan's Backend._getAudioDownloadError
# (ext/js/background/backend.js, upstream commit
# e2ed450c2f11a591922822e77f008e70a87daf0c), which maps error classes to distinct
# diagnoses — notably the historical expired-server-certificate incident. Here the
# never-raises fetchers tally why each transient failure happened so the pipeline
# can name the dominant cause instead of reporting an undiagnosable "X/Y available".
FAILURE_KEYS = ("ssl", "connection", "timeout", "http_status", "non_audio")


def _new_failure_counts() -> dict[str, int]:
    """Return a fresh, zeroed failure-cause counter for one run."""
    return dict.fromkeys(FAILURE_KEYS, 0)


def _classify_request_exception(exc: BaseException) -> str:
    """Map a raised request/OS exception to a failure-cause bucket.

    Checks are ordered most-specific first: ``SSLError`` subclasses
    ``ConnectionError`` and ``ConnectTimeout`` subclasses both ``Timeout`` and
    ``ConnectionError``, so a naive order would misfile the expired-certificate
    case (the whole point of this classification) as a plain connection error.
    Anything else (generic ``RequestException``, ``OSError``) falls to
    ``connection`` — a transport-family failure retried next run.
    """
    if isinstance(exc, requests.exceptions.SSLError):
        return "ssl"
    if isinstance(exc, requests.exceptions.Timeout):
        return "timeout"
    return "connection"


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

    The session sends a browser User-Agent: the CDN behind the endpoint's 301
    redirect 403s the default ``python-requests`` UA (see
    ``_BROWSER_USER_AGENT``).
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
        # The CDN behind the 301 redirect 403s the default python-requests UA;
        # present a browser UA so valid words actually download (see
        # _BROWSER_USER_AGENT note above).
        self._session.headers.update({"User-Agent": _BROWSER_USER_AGENT})
        # Per-run failure-cause tally (see FAILURE_KEYS). Bumped only in the
        # transient-failure branches below; a confirmed .miss (word genuinely
        # absent) is NOT a failure and never counted. Read via stats().
        self._failure_counts = _new_failure_counts()

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
                    self._failure_counts["http_status"] += 1
                    return None

                # A redirect that downgrades HTTPS → HTTP could expose audio
                # data in transit; treat as transient so it is retried next run.
                if not response.url.startswith("https://"):
                    self._failure_counts["connection"] += 1
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
                        self._failure_counts["non_audio"] += 1
                        return None
                    chunks.append(chunk)
                body = b"".join(chunks)

                # Zero-byte 200 is ambiguous (network glitch, premature close) —
                # treat as transient failure, not a confirmed miss.
                if not body:
                    self._failure_counts["connection"] += 1
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
                    self._failure_counts["non_audio"] += 1
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
            self._failure_counts[_classify_request_exception(exc)] += 1
            logger.debug("expression audio fetch failed for %s: %s", mined_form, exc)
            return None

    def fetch_candidates(
        self,
        candidates: list[tuple[str, str]],
        cancelled_check: Callable[[], bool] | None = None,
    ) -> Path | None:
        """Try each candidate form, returning the first JPod101 hit."""
        return _first_candidate_hit(self, candidates, cancelled_check)

    def stats(self) -> dict[str, int]:
        """Return a copy of this run's failure-cause counts (see FAILURE_KEYS).

        Duck-typed like ``close()`` (not on the ExpressionAudioFetcher
        Protocol); the chain fans it out to name the dominant failure cause in
        the pipeline summary. A copy is returned so callers cannot mutate the
        live tally.
        """
        return dict(self._failure_counts)

    def close(self) -> None:
        """Close the underlying ``requests.Session`` (sockets / file handles).

        Called between sequential mining runs so the per-run Session does not
        leak a live socket into the next run. On Windows those leaked sockets
        accumulate and contribute to the GUI-thread freeze when a user mines
        episodes back-to-back in one session.
        """
        self._session.close()


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

    def stats(self) -> dict[str, int]:
        """Aggregate per-run failure-cause counts across member fetchers.

        ``stats()`` is optional/duck-typed (not on the ExpressionAudioFetcher
        Protocol), exactly like ``close()``: members without it (e.g.
        LocalAudioPackFetcher) are skipped, and a member raising is suppressed
        so diagnostics never break a run. Unknown keys from a member are
        ignored; missing keys default to zero.
        """
        totals = _new_failure_counts()
        for fetcher in self._fetchers:
            stats = getattr(fetcher, "stats", None)
            if not callable(stats):
                continue
            with contextlib.suppress(Exception):
                for key, value in stats().items():
                    if key in totals:
                        totals[key] += value
        return totals

    def close(self) -> None:
        """Fan out ``close()`` to every member fetcher that defines one.

        ``close()`` is optional/duck-typed (not on the ExpressionAudioFetcher
        Protocol), so members without it are skipped. Called between sequential
        mining runs to release per-run sockets / sqlite handles before the next
        run opens new ones (Windows back-to-back-mining freeze).
        """
        for fetcher in self._fetchers:
            close = getattr(fetcher, "close", None)
            if callable(close):
                with contextlib.suppress(Exception):
                    close()
