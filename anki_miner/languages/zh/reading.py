"""Pinyin readings for the zh card reading field (spec 9.1).

Readings go in their own field, never as ruby: pinyin is a full romanisation,
not a phonetic gloss of individual characters. Tones are the dictionary's own —
third-tone sandhi (你好 nǐ hǎo, spoken ní hǎo) is deliberately NOT applied,
matching every surveyed deck and dictionary.
"""

from __future__ import annotations

import unicodedata
from typing import Any

# Combining diacritics a TONE-styled syllable carries, in NFD form. Neutral
# (5th) tone carries none, which is why the default is 5 rather than 0.
_TONE_BY_MARK = {"\u0304": 1, "\u0301": 2, "\u030c": 3, "\u0300": 4}


def syllable_tone(syllable: str) -> int:
    """Tone 1-5 of one TONE-styled pinyin syllable (5 = neutral)."""
    for char in unicodedata.normalize("NFD", syllable):
        tone = _TONE_BY_MARK.get(char)
        if tone is not None:
            return tone
    return 5


def _syllables(word: str) -> list[str]:
    """Per-syllable pinyin for ``word``, tone marks included, non-hanzi dropped.

    The whole word is handed to pypinyin in one call so its phrase dictionary
    can disambiguate polyphones; feeding characters one at a time would silently
    return the most common reading for every one of them.
    """
    from pypinyin import Style, pinyin

    rows = pinyin(word, style=Style.TONE, heteronym=False, errors="ignore")
    return [row[0] for row in rows if row and row[0]]


def word_pinyin(word: str) -> str:
    """Space-separated tone-marked pinyin for ``word``; ``""`` when it has no hanzi."""
    return " ".join(_syllables(word))


def pinyin_syllables(word: str) -> list[tuple[str, int]]:
    """``(syllable, tone)`` pairs — the input the tone-colour render hook needs."""
    return [(syllable, syllable_tone(syllable)) for syllable in _syllables(word)]


class ZhReadingSupport:
    """``ReadingSupport`` for zh: the token's surface, read as one word."""

    def word_reading(self, token: Any) -> str:
        return word_pinyin(token.surface)
