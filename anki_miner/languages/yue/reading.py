"""Jyutping readings for the yue card reading field (spec F.1).

Readings go in their own field, never as ruby: jyutping is a full romanisation,
not a phonetic gloss of individual characters.

The word is handed to ``characters_to_jyutping`` as a ONE-ELEMENT LIST so the
engine cannot re-segment it, and the second element of its answer is ``None``
(not ``""``) for an out-of-vocabulary item, which is why every caller goes
through :func:`word_jyutping`.
"""

from __future__ import annotations

from typing import Any


def word_jyutping(word: str) -> str:
    """Space-separated jyutping for ``word``; ``""`` when the engine has none."""
    if not word:
        return ""
    import pycantonese

    return pycantonese.characters_to_jyutping([word])[0][1] or ""


def jyutping_syllables(word: str) -> list[tuple[str, int]]:
    """``(syllable, tone)`` pairs -- the input the tone-colour render hook needs.

    The tone is the syllable's trailing digit; a syllable with none (which the
    engine does not emit for Han, but an imported reading may carry) reports 0
    and the hook falls back to the neutral colour.
    """
    pairs: list[tuple[str, int]] = []
    for syllable in word_jyutping(word).split():
        tone = int(syllable[-1]) if syllable[-1].isdigit() else 0
        pairs.append((syllable, tone))
    return pairs


class YueReadingSupport:
    """``ReadingSupport`` for yue: the token's LEMMA, read as one word.

    The lemma, not the surface: a surface joined across an interior space
    (``今 日``) is not a word the engine can look up.
    """

    def word_reading(self, token: Any) -> str:
        return word_jyutping(getattr(token.feature, "lemma", "") or token.surface)
