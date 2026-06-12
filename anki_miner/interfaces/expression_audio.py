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
