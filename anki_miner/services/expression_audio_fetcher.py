"""Expression audio fetchers: JPod101 and chained composite."""

import contextlib
import hashlib
import logging
import os
import tempfile
import threading
import time
from collections import Counter
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from anki_miner.services.audio_fetch_common import (
    FAILURE_KEYS,
    MAX_AUDIO_BYTES,
    aggregate_failure_stats,
    audio_extension_for_media_type,
    classify_request_exception,
    close_all,
    download_audio_to_cache,
    find_cached_by_stem,
    first_candidate_hit,
    is_mp3,
    new_browser_session,
    new_failure_counts,
)
from anki_miner.utils.file_utils import safe_filename
from anki_miner.utils.text_utils import is_kana_only

if TYPE_CHECKING:
    from anki_miner.interfaces.expression_audio import ExpressionAudioFetcher

logger = logging.getLogger(__name__)

# Backwards-compatible aliases (ARC-019): the shared HTTP / cache / failure
# toolkit moved to ``audio_fetch_common``. These keep the historical
# underscore-prefixed names resolvable as attributes of this module — the
# JPod101 / chained method bodies below look them up as module globals, and
# existing test monkeypatch targets reference ``expression_audio_fetcher.<name>``.
_aggregate_failure_stats = aggregate_failure_stats
_classify_request_exception = classify_request_exception
_close_all = close_all
_find_cached_by_stem = find_cached_by_stem
_first_candidate_hit = first_candidate_hit
_is_mp3 = is_mp3
_new_browser_session = new_browser_session
_new_failure_counts = new_failure_counts

# Public surface, including the toolkit names re-exported above that this module
# no longer uses internally (imported so ``from expression_audio_fetcher import
# <name>`` keeps resolving); declaring them here marks the re-exports intentional.
__all__ = [
    "ChainedExpressionAudioFetcher",
    "FAILURE_KEYS",
    "JPOD101_NOT_FOUND_SHA256",
    "JPod101AudioFetcher",
    "MAX_AUDIO_BYTES",
    "MISS_MARKER_TTL_SECONDS",
    "STALE_PART_AGE_SECONDS",
    "audio_extension_for_media_type",
    "download_audio_to_cache",
    "purge_miss_markers",
]

JPOD101_AUDIO_URL = "https://assets.languagepod101.com/dictionary/japanese/audiomp3.php"

# JPod101 answers unknown words with HTTP 200 and a fixed "audio not
# available" placeholder mp3. This is the SHA-256 of that placeholder
# (same value Yomitan hardcodes) — matching bodies are treated as misses.
JPOD101_NOT_FOUND_SHA256 = "ae6398b5a27bc8c0a771df6c907ade794be15518174773c58c7c7ddd17098906"

# Stale .part files older than this threshold (seconds) are swept on
# cache-miss fetch() calls (warm-cache hits return before the sweep). Files
# younger than this are assumed to belong to a concurrent in-progress
# download and are left alone.
STALE_PART_AGE_SECONDS = 60

# .miss markers are permanent by design (batch mining must not re-hammer JPod101
# for genuinely-absent words on every run), but a marker can outlive the word
# actually gaining audio upstream. A marker whose mtime is older than this TTL is
# treated as expired at the .exists() gate and transparently re-fetched. The
# Settings -> Audio "Retry missing expression audio" button (purge_miss_markers)
# is the manual override; this constant is the automatic one.
MISS_MARKER_TTL_SECONDS = 180 * 24 * 60 * 60  # 180 days


def _miss_marker_expired(miss_path: Path) -> bool:
    """Return True if ``miss_path``'s mtime is older than ``MISS_MARKER_TTL_SECONDS``.

    A marker that cannot be stat'd is treated as NOT expired (leave it as a
    miss); the caller has already gated on ``.exists()``.
    """
    try:
        return time.time() - miss_path.stat().st_mtime > MISS_MARKER_TTL_SECONDS
    except OSError:
        return False


def purge_miss_markers(cache_dir: Path) -> int:
    """Delete every ``*.miss`` marker under ``cache_dir``; return the count removed.

    Backs the Settings -> Audio "Retry missing expression audio" affordance:
    clearing the markers makes the next mining run re-request those words from
    JPod101. A missing directory yields 0; a per-file unlink error is ignored so
    one locked marker cannot abort the whole sweep.
    """
    if not cache_dir.is_dir():
        return 0
    removed = 0
    for marker in cache_dir.glob("*.miss"):
        try:
            marker.unlink()
            removed += 1
        except OSError:
            pass
    return removed


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
    ``audio_fetch_common``).
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
        # The CDN behind the 301 redirect 403s the default python-requests UA;
        # _new_browser_session presents a browser UA so valid words download.
        self._session = _new_browser_session()
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
            reading: Kana reading of the word.  A reading that is empty,
                whitespace-only, or not pure kana (the tokenizer's OOV fallback
                is the kanji surface) skips the fetch entirely: without a real
                ``kana`` the JPod101 endpoint guesses a reading for the kanji,
                which picks the wrong pronunciation for homographs (e.g. 辛い →
                からい vs つらい) and caches that incorrect audio permanently
                under the word's key.
            cancelled_check: Optional zero-argument callable that returns True
                when the caller has requested cancellation.  Consulted after
                the input guards, again immediately before ``time.sleep``, and
                once more before the network request and between response chunks.
                When it returns True this method returns None immediately — no
                cache writes, no .miss marker. The read timeout still bounds a
                peer that stops yielding chunks.

        Returns:
            Path to a cached mp3, or None if unavailable.
        """
        if not mined_form.strip() or not is_kana_only(reading.strip()):
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
            # An expired marker (older than MISS_MARKER_TTL_SECONDS) falls
            # through to a re-fetch; a still-not-found word re-touches it below,
            # resetting the TTL clock.
            if miss_path.exists() and not _miss_marker_expired(miss_path):
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
                    if cancelled_check is not None and cancelled_check():
                        return None
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
                    # Confirmed not-found: marker prevents re-requesting. touch()
                    # (not touch-if-absent) so re-confirming an expired marker
                    # resets its TTL clock. Markers self-heal after
                    # MISS_MARKER_TTL_SECONDS; Settings -> Audio "Retry missing
                    # expression audio" (purge_miss_markers) clears them on demand.
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

        # Broad Exception is intentional and correct: pathological mined_form/
        # reading input can raise ValueError/UnicodeEncodeError from requests'
        # URL-encoding, and a malformed response.url can raise AttributeError/
        # TypeError from the str.startswith check above — audio_stage.py's
        # phase-3 loop calls fetch() with no try/except by design, so this
        # fetcher must own every failure mode, not just network/OS ones.
        except Exception as exc:  # noqa: BLE001 — never raise per the fetcher protocol contract
            self._failure_counts[_classify_request_exception(exc)] += 1
            identity = hashlib.sha256(f"{mined_form}\0{reading}".encode()).hexdigest()[:12]
            logger.debug(
                "expression audio fetch failed identity=%s error=%s",
                identity,
                type(exc).__name__,
            )
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

    Every per-word walk runs under :attr:`PER_WORD_BUDGET_SECONDS` of wall
    clock.  This is the ONLY real bound on the stage: a member's own
    ``timeout=`` is a per-socket-operation limit, not a wall-clock one, so it
    does not cover name resolution, and a redirect hop starts a fresh budget.
    A reported run spent 796s fetching audio for a SINGLE word that ultimately
    SUCCEEDED — every transport counter read zero, because nothing failed; the
    endpoint 301-redirects hits to a second host with ``Connection: close``, so
    each hit forces a fresh un-poolable connection, and the cost of that
    connection escalated with request volume (35s -> 50s -> 599s -> 797s per
    hit) and did not reset across app restarts.  Mining 11 episodes unattended
    turned into hours with no failure and no diagnosis.  Bounding the walk is
    what makes the run's worst case a function of word count rather than of
    whatever the network decides to do.
    """

    #: Wall-clock ceiling for one word's whole chain walk (every source, every
    #: candidate form). Deliberately generous: a healthy cold hit measures well
    #: under 2s, and a member's own request timeout is 10s, so this still
    #: accommodates two sequential timed-out requests before giving up. Not a
    #: setting — an escape hatch nobody should have to find and tune.
    PER_WORD_BUDGET_SECONDS = 20.0

    def __init__(
        self,
        fetchers: "Sequence[ExpressionAudioFetcher]",
        *,
        candidates: "Callable[[Any], list[tuple[str, str]]] | None" = None,
    ) -> None:
        """Initialize with an ordered list of fetchers.

        Args:
            fetchers: Fetchers tried left-to-right; first non-None Path wins.
            candidates: The active language's ladder builder
                (``AudioDefaults.candidates``). None keeps the Japanese ladder,
                which is what every pre-multilanguage caller got.
        """
        self._fetchers: list[ExpressionAudioFetcher] = list(fetchers)
        self._candidates = candidates
        # Chain-owned tally, merged into stats() alongside the members'. Only
        # the "slow" bucket is ever bumped here; transport buckets belong to
        # whichever member actually made the request.
        self._failure_counts = _new_failure_counts()
        # Budget expiries per local pack id. A "slow" count alone cannot say
        # WHICH of several packs sits on the slow medium, and that is the one
        # thing the user has to know to fix it (see slowest_pack_id).
        self._slow_packs: Counter[str] = Counter()

    def candidates_for(self, word: Any) -> list[tuple[str, str]]:
        """The ``(term, reading)`` ladder to feed :meth:`fetch_candidates`."""
        if self._candidates is not None:
            return self._candidates(word)
        from anki_miner.services.audio_fetch_common import expression_audio_candidates

        return expression_audio_candidates(word)

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
        return self._budgeted(
            lambda active: self._walk(lambda f: f.fetch(mined_form, reading, cancelled_check), cancelled_check, active),
            identity=f"{mined_form}/{reading}",
        )

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

        The whole walk runs under one :attr:`PER_WORD_BUDGET_SECONDS` budget —
        not one per source — so a chain of slow sources cannot multiply the
        ceiling by its own length.
        """
        return self._budgeted(
            lambda active: self._walk(
                lambda f: f.fetch_candidates(candidates, cancelled_check), cancelled_check, active
            ),
            identity=candidates[0][0] if candidates else "",
        )

    def _walk(
        self,
        attempt: "Callable[[ExpressionAudioFetcher], Path | None]",
        cancelled_check: Callable[[], bool] | None,
        active: list[object],
    ) -> Path | None:
        """Try each member in priority order; first non-None wins.

        ``active`` is the walk's own one-slot holder, written with the member
        about to be attempted so a budget expiry can name it. Per walk, never
        an instance attribute: an abandoned walk keeps running after its
        budget and must not relabel the next word's expiry.
        """
        for fetcher in self._fetchers:
            if cancelled_check is not None and cancelled_check():
                return None
            active[0] = fetcher
            result = attempt(fetcher)
            if result is not None:
                return result
        return None

    def _budgeted(self, walk: "Callable[[list[object]], Path | None]", identity: str) -> Path | None:
        """Run *walk* under the per-word wall-clock budget; None when it expires.

        The walk runs on a daemon thread and is ABANDONED rather than
        cancelled: a thread blocked in ``getaddrinfo`` or a socket read cannot
        be interrupted from outside, and the member fetchers already write
        their caches atomically, so an abandoned walk that later succeeds
        simply warms the cache for the next run instead of corrupting
        anything. Daemon so a stuck resolver can never hold up interpreter
        exit. The orphan count is bounded by the number of budget expiries in
        a run, and each one ends on its own once the network answers.
        """
        outcome: list[Path | None] = [None]
        active: list[object] = [None]

        def _target() -> None:
            try:
                outcome[0] = walk(active)
            except Exception as exc:  # noqa: BLE001 — protocol contract: never raise
                logger.debug("expression audio chain walk failed identity=%s error=%s", identity, type(exc).__name__)

        worker = threading.Thread(target=_target, name="expression-audio-fetch", daemon=True)
        worker.start()
        worker.join(self.PER_WORD_BUDGET_SECONDS)
        if worker.is_alive():
            # The abandoned walk may still be running; reading its one-slot
            # holder is a benign race (a single reference assignment) and at
            # worst names the member it moved on to, which is still the
            # member that was blocking when the budget ran out.
            member = active[0]
            self._failure_counts["slow"] += 1
            pack_id = getattr(member, "pack_id", None)
            if isinstance(pack_id, str) and pack_id:
                self._slow_packs[pack_id] += 1
            logger.warning(
                "expression audio exceeded the %.0fs per-word budget identity=%s member=%s; "
                "treating as a miss and continuing (the fetch is abandoned, not cancelled)",
                self.PER_WORD_BUDGET_SECONDS,
                identity,
                _member_label(member),
            )
            return None
        # join() returned without a timeout, so the write to outcome[0]
        # happens-before this read.
        return outcome[0]

    def stats(self) -> dict[str, int]:
        """Aggregate per-run failure-cause counts across member fetchers.

        ``stats()`` is optional/duck-typed (not on the ExpressionAudioFetcher
        Protocol), exactly like ``close()``: members without it (e.g.
        LocalAudioPackFetcher) are skipped. See ``aggregate_failure_stats``.

        The chain's own "slow" tally is folded in on top: budget expiries are
        the chain's to count, because no member knows it was abandoned.
        """
        totals = _aggregate_failure_stats(self._fetchers)
        totals["slow"] = totals.get("slow", 0) + self._failure_counts["slow"]
        return totals

    def slowest_pack_id(self) -> str | None:
        """The local pack with the most budget expiries so far, or None.

        Duck-typed like ``stats()``/``close()``: the audio stage looks it up
        with ``getattr`` so a bare Protocol fetcher never has to provide it.
        Only packs are tracked. An online member that blows the budget has the
        generic remedy (reorder or disable it); a pack's remedy is its folder,
        so the diagnosis has to name the pack.
        """
        if not self._slow_packs:
            return None
        return max(self._slow_packs, key=lambda pack_id: self._slow_packs[pack_id])

    def close(self) -> None:
        """Fan out ``close()`` to every member fetcher that defines one.

        ``close()`` is optional/duck-typed (not on the ExpressionAudioFetcher
        Protocol), so members without it are skipped. See ``close_all``.
        """
        _close_all(self._fetchers)


def _member_label(member: object) -> str:
    """Name a chain member for the budget-expiry log line.

    A local pack is named by its id AND its source folder: a diagnostics
    bundle carries no pack paths otherwise, and "which folder sits on the slow
    medium" is the one question that line exists to answer. Online members
    are named by class.
    """
    if member is None:
        return "<none>"
    pack_id = getattr(member, "pack_id", None)
    if isinstance(pack_id, str) and pack_id:
        pack_dir = getattr(member, "pack_dir", None)
        if pack_dir is not None:
            return f"audio pack '{pack_id}' ({pack_dir})"
        return f"audio pack '{pack_id}'"
    return type(member).__name__
