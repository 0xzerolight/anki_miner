"""Google Translate TTS expression-audio fetcher.

Synthetic text-to-speech fallback for word pronunciation audio, backed by the
``gtts`` (Google Translate TTS) library. Slotted into the audio chain AFTER
JPod101 (native recorded audio); this only fills gaps where no real recording
exists.

Design notes mirroring :class:`JPod101AudioFetcher` with deliberate
differences:

* **Fed the reading (kana), never the kanji.** Synthesizing from the kana
  reading guarantees correct pronunciation and sidesteps kanji homograph
  misreads (e.g. 辛い → からい vs つらい). An empty/whitespace reading therefore
  skips synthesis entirely.
* **No ``.miss`` negative-cache markers.** Unlike JPod101, synthetic TTS
  effectively always succeeds for valid input; any failure is transient
  (network / HTTP 429 rate limit) and must be retried on the next run, so no
  negative marker is ever written.
* **Never raises.** The Phase-3 pipeline loop that calls ``fetch`` has no
  try/except by design, so this fetcher owns all error handling and returns
  None for any unresolvable word.
"""

import contextlib
import io
import logging
import os
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

import gtts  # type: ignore[import-untyped]

from anki_miner.services.expression_audio_fetcher import (
    MAX_AUDIO_BYTES,
    _classify_request_exception,
    _first_candidate_hit,
    _is_mp3,
    _new_failure_counts,
)
from anki_miner.utils.file_utils import safe_filename

logger = logging.getLogger(__name__)


class GoogleTranslateAudioFetcher:
    """Synthesizes word pronunciation audio via Google Translate TTS.

    Successful syntheses are cached on disk as ``.mp3`` files. No negative
    markers are written: failures are transient and retried on the next run.
    Implements the :class:`~anki_miner.interfaces.ExpressionAudioFetcher`
    protocol structurally; ``fetch`` never raises.
    """

    def __init__(self, cache_dir: Path, delay: float = 0.2):
        """Initialize with cache directory and rate-limiting delay.

        Args:
            cache_dir: Directory for cached mp3s (caller passes
                ``~/.anki_miner/audio_cache/googletts/``).
            delay: Seconds to wait before each synthesis request.
        """
        self._cache_dir = cache_dir
        # NaN must clamp to 0.0 (time.sleep(nan) raises); the >= comparison
        # is False for nan, so the else branch handles it.
        self._delay = delay if delay >= 0.0 else 0.0
        # Per-run failure-cause tally (see FAILURE_KEYS). Synthetic TTS keeps no
        # negative markers, so every non-hit is a transient failure; bumped only
        # in the branches below. Read via stats().
        self._failure_counts = _new_failure_counts()

    def fetch(
        self,
        mined_form: str,
        reading: str,
        cancelled_check: Callable[[], bool] | None = None,
    ) -> Path | None:
        """Synthesize pronunciation audio for a word.

        Args:
            mined_form: Word as mined onto the card (kanji/surface form). Used
                only to key the cache filename; never sent to the synthesizer.
            reading: Kana reading of the word. This is what is fed to gTTS so
                the pronunciation is correct and homograph-safe. An empty or
                whitespace-only reading skips synthesis entirely and returns
                None.
            cancelled_check: Optional zero-argument callable that returns True
                when the caller has requested cancellation. Consulted after the
                input guards, again immediately before ``time.sleep``, and once
                more before synthesis. Returns None immediately when it fires —
                no cache writes.

        Returns:
            Path to a cached mp3, or None if unavailable. Never raises.
        """
        if not mined_form.strip() or not reading.strip():
            return None

        if cancelled_check is not None and cancelled_check():
            return None

        stem = safe_filename(f"googletts_{mined_form}_{reading}")
        mp3_path = self._cache_dir / f"{stem}.mp3"

        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)

            if mp3_path.exists() and mp3_path.stat().st_size > 0:
                return mp3_path

            # No .miss markers: failures are transient (network / 429) and must
            # be retried next run, so nothing negative is ever cached.

            if cancelled_check is not None and cancelled_check():
                return None

            time.sleep(self._delay)

            if cancelled_check is not None and cancelled_check():
                return None

            # Synthesize from the kana reading. lang="ja" is fixed; calling
            # gtts.lang.tts_langs() would make a network request, so it is
            # deliberately avoided.
            buffer = io.BytesIO()
            tts = gtts.gTTS(text=reading, lang="ja")
            tts.write_to_fp(buffer)
            body = buffer.getvalue()

            # Oversized body is almost certainly an error response — transient,
            # nothing written.
            if len(body) > MAX_AUDIO_BYTES:
                self._failure_counts["non_audio"] += 1
                return None

            # Empty body is a transient failure (premature close, etc.).
            if not body:
                self._failure_counts["connection"] += 1
                return None

            # Reject non-audio bodies (HTML error / rate-limit pages) as
            # transient; no marker so the word is retried next run.
            if not _is_mp3(body):
                self._failure_counts["non_audio"] += 1
                return None

            # Write atomically: stage to a unique temp file then rename so a
            # killed process cannot leave a truncated mp3 that passes the
            # st_size > 0 cache-hit check on the next run.
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

        # Broad Exception is intentional and correct: gtts raises gTTSError and
        # assorted network/value exceptions, and the processor loop has no
        # try/except by design — the fetcher owns all error handling and must
        # never raise per the ExpressionAudioFetcher contract.
        except Exception as exc:
            # gtts wraps requests, so a network/SSL failure surfaces as a
            # requests exception _classify_request_exception recognizes; a
            # gTTSError or other synthesis fault falls to "connection".
            self._failure_counts[_classify_request_exception(exc)] += 1
            logger.debug("google translate audio fetch failed for %s: %s", mined_form, exc)
            return None

    def fetch_candidates(
        self,
        candidates: list[tuple[str, str]],
        cancelled_check: Callable[[], bool] | None = None,
    ) -> Path | None:
        """Try each candidate form, returning the first synthesized hit."""
        return _first_candidate_hit(self, candidates, cancelled_check)

    def stats(self) -> dict[str, int]:
        """Return a copy of this run's failure-cause counts (see FAILURE_KEYS).

        Duck-typed like ``close()``; the chain aggregates it to diagnose the
        dominant audio-failure cause in the pipeline summary.
        """
        return dict(self._failure_counts)

    def close(self) -> None:
        """No-op: gtts opens a per-call connection, no persistent handle to release.

        Present so the chain's duck-typed ``close()`` fan-out (between
        sequential mining runs) treats this fetcher uniformly.
        """
        # Nothing to close — gtts manages its own per-synthesis HTTP request.
        pass
