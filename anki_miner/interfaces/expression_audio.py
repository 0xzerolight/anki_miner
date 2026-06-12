"""Protocol for expression (pronunciation) audio fetchers."""

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

    def fetch(self, mined_form: str, reading: str) -> Path | None:
        """Fetch pronunciation audio for a word.

        Args:
            mined_form: Word as mined onto the card (kanji/surface form).
            reading: Kana reading of the word (may be empty).

        Returns:
            Path to an audio file, or None if unavailable.  Never raises.
        """
        ...
