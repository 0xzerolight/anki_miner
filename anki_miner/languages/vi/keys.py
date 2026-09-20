"""Vietnamese index keys and comparison fold (spec C.4, R6/R7 in the R33 shape).

One function at both seams, the ar/fa/th/vi/id/he convention: ``fold_term`` builds
dictionary and frequency keys on import and query, and the profile's ``dedup_fold`` is the
same function (Vietnamese has no article to strip). NFC -> word-scoped old-style tone
placement (``fold_tone_placement``, never the raw port: a key fold that moved the tilde of
a Spanish name would put that spelling on the card front, because the front is
``vi_fold_term(surface)``) -> casefold; the tone move and casefold commute because every
precomposed Vietnamese vowel lowercases to its own precomposed lowercase. Rule A then
Rule A' homograph scope comes with :class:`CasefoldDictKeys`; the lemma is the folded
surface, so A' adds nothing. ``fold_reading`` stays NFC: wty-vi-en carries no readings.
"""

from __future__ import annotations

from anki_miner.languages._spaced.keys import CasefoldDictKeys
from anki_miner.languages.vi.script import fold_tone_placement

VI_KEYS = CasefoldDictKeys(extra_fold=fold_tone_placement)


def vi_fold_term(text: str) -> str:
    """``VI_KEYS.fold_term``: the key fold and the known-words comparison fold (idempotent)."""
    return VI_KEYS.fold_term(text)
