"""Word-audio defaults for spaCy languages (S1, S2).

The ladder asks for the card front and nothing else. Every shipped ladder
retries the SAME word (ja okurigana, ko respelling, zh script variant); the
inflected surface is a different word, so offering it would put ``went``'s
audio on a ``go`` card whenever a pack or cache missed ``go``.
"""

from __future__ import annotations

from typing import Any


def spaced_audio_candidates(word: Any) -> list[tuple[str, str]]:
    """``[(mined_form, mined_form)]``: the reading slot is what gets spoken (spaCy languages have no reading)."""
    term = str(getattr(word, "mined_form", "") or "")
    return [(term, term)] if term else []


def spaced_speakable(term: str, reading: str) -> str | None:
    """``AudioDefaults.speakable`` (S1): speak the reading, else the term.

    A spaCy language's ``expression_reading`` is ``""``, so this speaks the
    orthographic word; Latin script has no zh-style polyphone tradeoff.
    """
    return reading or term or None
