"""Pinyin readings for the zh card reading field (spec 9.1).

Readings go in their own field, never as ruby: pinyin is a full romanisation,
not a phonetic gloss of individual characters. Tones are citation tones —
third-tone sandhi (你好 nǐ hǎo, spoken ní hǎo) is deliberately NOT applied and
the 一/不 sandhi pypinyin's phrase dictionary ships is undone, matching every
surveyed deck and dictionary.
"""

from __future__ import annotations

import unicodedata
from typing import Any

from anki_miner.languages.zh.variants import to_simplified

# Combining diacritics a TONE-styled syllable carries, in NFD form. Neutral
# (5th) tone carries none, which is why the default is 5 rather than 0.
_TONE_BY_MARK = {"\u0304": 1, "\u0301": 2, "\u030c": 3, "\u0300": 4}
_MARK_BY_TONE = {tone: mark for mark, tone in _TONE_BY_MARK.items()}

# Words of two syllables or more ending in \u513f are erhua: the \u513f rhotacises the
# syllable before it (\u8fd9\u513f zh\u00e8r) instead of being one of its own. These are the
# exceptions, where \u513f is the noun head "child/son" and keeps a full \u00e9r \u2014 the
# CC-CEDICT headwords of that shape a learner meets. Proper-name
# transliterations (\u9999\u5948\u513f) and words whose commoner reading is erhua anyway
# (\u82b1\u513f hu\u0101r, \u7334\u513f h\u00f3ur) are deliberately absent.
_ER_IS_A_SYLLABLE = frozenset(
    {
        "\u4e5e\u513f",
        "\u4e73\u513f",
        "\u4ea7\u513f",
        "\u4f4e\u80fd\u513f",
        "\u4f84\u513f",
        "\u5065\u513f",
        "\u5973\u513f",
        "\u59bb\u513f",
        "\u5a03\u513f",
        "\u5a74\u513f",
        "\u5a74\u5e7c\u513f",
        "\u5b59\u513f",
        "\u5b64\u513f",
        "\u5ba0\u513f",
        "\u5c0f\u513f",
        "\u5c11\u513f",
        "\u5e72\u5973\u513f",
        "\u5e78\u8fd0\u513f",
        "\u5e7c\u513f",
        "\u5f92\u513f",
        "\u60a3\u513f",
        "\u6258\u513f",
        "\u65b0\u751f\u513f",
        "\u65e9\u4ea7\u513f",
        "\u6b8b\u75be\u513f",
        "\u6d41\u6d6a\u513f",
        "\u6df7\u8840\u513f",
        "\u7537\u513f",
        "\u7578\u5f62\u513f",
        "\u80b2\u513f",
        "\u80ce\u513f",
        "\u8bd5\u7ba1\u5a74\u513f",
        "\u8fde\u4f53\u5a74\u513f",
    }
)

# pypinyin's phrase dictionary bakes the spoken \u4e00/\u4e0d sandhi into some of its
# rows (\u4e00\u4e2a y\u00ed g\u00e8, \u4e0d\u662f b\u00fa sh\u00ec) and not others (\u4e00\u6837 y\u012b y\u00e0ng, \u4e0d\u9519 b\u00f9 cu\u00f2), so
# cards contradicted each other and CC-CEDICT. Source character -> the tone it
# came back with -> the citation tone it is restored to. Tone 5 is absent on
# purpose: \u5dee\u4e0d\u591a ch\u00e0 bu du\u014d is a lexical neutral, not sandhi.
_CITATION_TONES = {"\u4e00": {2: 1, 4: 1}, "\u4e0d": {2: 4}}


def syllable_tone(syllable: str) -> int:
    """Tone 1-5 of one TONE-styled pinyin syllable (5 = neutral)."""
    for char in unicodedata.normalize("NFD", syllable):
        tone = _TONE_BY_MARK.get(char)
        if tone is not None:
            return tone
    return 5


def _retone(syllable: str, tone: int) -> str:
    """``syllable`` with its tone mark swapped for ``tone``'s (yí -> yī)."""
    mark = _MARK_BY_TONE[tone]
    decomposed = unicodedata.normalize("NFD", syllable)
    return unicodedata.normalize("NFC", "".join(mark if char in _TONE_BY_MARK else char for char in decomposed))


def _placeholder_rows(chars: str) -> list[str]:
    """``errors`` handler: one empty row per character pypinyin cannot read.

    A ``str`` return — what the bundled stub declares and what ``errors="ignore"``
    is shorthand for — collapses the whole run into a single row or into none,
    either of which slides the rest of the word out of alignment with its source
    characters. ``handle_nopinyin`` unpacks a list into one row each.
    """
    return [""] * len(chars)


def _syllables(word: str) -> list[str]:
    """Per-syllable pinyin for ``word``, tone marks included, non-hanzi dropped.

    The whole word is handed to pypinyin in one call so its phrase dictionary
    can disambiguate polyphones; feeding characters one at a time would silently
    return the most common reading for every one of them. That dictionary is
    simplified-only, so the word goes in as its simplified spelling (銀行 would
    otherwise read yín xíng); both scripts share one pronunciation. It also
    carries the 一/不 sandhi, which is undone against the source character each
    row came from — hence the placeholder rows, which keep a Latin letter or a
    digit from shifting the alignment.
    """
    from pypinyin import Style, pinyin

    simplified = to_simplified(word)
    rows = pinyin(simplified, style=Style.TONE, heteronym=False, errors=_placeholder_rows)  # type: ignore[arg-type]
    syllables: list[str] = []
    for char, row in zip(simplified, rows, strict=True):
        syllable = row[0] if row else ""
        if not syllable:
            continue
        citation = _CITATION_TONES.get(char, {}).get(syllable_tone(syllable))
        syllables.append(syllable if citation is None else _retone(syllable, citation))
    if len(syllables) > 1 and simplified.endswith("儿") and simplified not in _ER_IS_A_SYLLABLE:
        syllables[-2:] = [syllables[-2] + "r"]
    return syllables


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
