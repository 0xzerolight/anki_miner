"""Protocol for expression (pronunciation) audio fetchers."""

from collections.abc import Callable
from pathlib import Path
from typing import Protocol


class ExpressionAudioFetcher(Protocol):
    """Interface for fetching word pronunciation audio.

    Implementations must never raise — the Phase 3 pipeline loop that calls
    ``fetch`` contains no try/except and no sleep by design; callers rely on
    error-free execution and expect None for any unresolvable word.

    The returned path's ``name`` is used verbatim as the Anki media filename,
    so it must be both filesystem-safe and globally unique per
    (source, mined_form, reading) to prevent collisions across fetcher
    implementations.
    """

    def fetch(
        self,
        mined_form: str,
        reading: str,
        cancelled_check: Callable[[], bool] | None = None,
    ) -> Path | None:
        """Fetch pronunciation audio for a word.

        Args:
            mined_form: Word as mined onto the card (kanji/surface form).
            reading: Kana reading of the word (may be empty).
            cancelled_check: Optional zero-argument callable that returns True
                when the caller has requested cancellation.  Implementations
                consult it at safe checkpoints (composite fetchers also between
                members) and return None promptly when it fires — never raising,
                writing no cache artifacts for the cancelled word.

        Returns:
            Path to an audio file, or None if unavailable.  Never raises.
        """
        ...

    def fetch_candidates(
        self,
        candidates: list[tuple[str, str]],
        cancelled_check: Callable[[], bool] | None = None,
    ) -> Path | None:
        """Fetch audio for the first resolvable ``(mined_form, reading)`` pair.

        Tries each candidate form in order and returns the first hit.  The
        candidate list is the audio retry ladder (surface form, katakana
        variant, unidic lemma, lemma-katakana) — see
        ``orchestration.audio_stage._expression_audio_candidates``.

        This source-first ordering is the whole point: a leaf fetcher exhausts
        ALL candidate forms before a composite chain falls through to a
        lower-priority source.  Walking it candidate-first instead would let a
        synthetic fallback (e.g. Google TTS) satisfy the surface form before a
        higher-priority real-audio source ever sees the lemma it actually has.

        Args:
            candidates: Ordered ``(mined_form, reading)`` pairs to try.  An
                empty list yields None.
            cancelled_check: Optional zero-argument callable that returns True
                when the caller has requested cancellation.  Consulted between
                candidates (composite fetchers also between members) and
                forwarded to each ``fetch``.  Returns None promptly when it
                fires, writing no cache artifacts.  Never raises.

        Returns:
            Path to an audio file, or None if no candidate resolves.
        """
        ...
