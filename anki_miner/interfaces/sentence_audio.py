"""Protocol for sentence-level TTS audio fetchers (reading sources)."""

from collections.abc import Callable
from pathlib import Path
from typing import Protocol


class SentenceAudioFetcher(Protocol):
    """Fetches synthesized audio for a full sentence.

    Sentence analogue of :class:`ExpressionAudioFetcher`, deliberately
    simpler: one text input, no kana reading, no candidate ladder (a sentence
    has no retry forms). Same hard contract:

    * **Never raises.** The Phase-3' reading loop that calls ``fetch`` has no
      try/except by design — implementations own all error handling and
      return None for anything unresolvable.
    * The returned path's ``.name`` is used verbatim as the Anki media
      filename, so it must be filesystem-safe and globally unique per
      (provider, sentence text) — implementations key it on a content hash.
    * ``stats()`` and ``close()`` are duck-typed extras (not on this
      Protocol), aggregated/fanned-out by the chain exactly like the
      expression-audio chain.
    """

    def fetch(
        self,
        sentence: str,
        cancelled_check: Callable[[], bool] | None = None,
    ) -> Path | None:
        """Return a cached audio file for *sentence*, or None. Never raises."""
        ...
