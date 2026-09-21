"""Pinyin readings for the zh card reading field (spec 9.1).

Readings go in their own field, never as ruby: pinyin is a full romanisation,
not a phonetic gloss of individual characters. Tones are citation tones —
third-tone sandhi (你好 nǐ hǎo, spoken ní hǎo) is deliberately NOT applied and
the 一/不 sandhi pypinyin's phrase dictionary ships is undone, matching every
surveyed deck and dictionary.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from functools import cache
from itertools import groupby
from typing import Any

from anki_miner.languages.zh.variants import to_simplified
from anki_miner.utils.ja_normalize import is_cjk_ideograph

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


def _syllables(word: str) -> list[str]:
    """Per-syllable pinyin for ``word``, tone marks included, non-hanzi kept.

    The whole word is handed to pypinyin in one call so its phrase dictionary
    can disambiguate polyphones; feeding characters one at a time would silently
    return the most common reading for every one of them. That dictionary is
    simplified-only, so the word goes in as its simplified spelling (銀行 would
    otherwise read yín xíng); both scripts share one pronunciation.

    ``errors="default"`` hands back the source text for whatever pypinyin
    cannot read, as ONE row per run — so a Latin run (T恤, WIFI密码) survives as
    the syllable it is spoken as, but the rows no longer line up with the
    characters they came from and the 一/不 citation retone below needs that
    alignment. Hence the cursor: a row that is literally there at the cursor
    consumes its own length; anything else consumes one character. An
    unreadable HANZI comes back the same way and must not reach the card, so an
    ideograph row is dropped rather than emitted (OpenCC maps a few rare
    traditional characters onto simplified ones pypinyin has no entry for).

    The length check keeps the walk on the characters the rows were generated
    from, the way ``JiebaTagger._segments`` keeps its spans on theirs.
    """
    from pypinyin import Style, pinyin

    if not any(is_cjk_ideograph(char) for char in word):
        return []
    simplified = to_simplified(word)
    if len(simplified) != len(word):
        simplified = word
    rows = pinyin(simplified, style=Style.TONE, heteronym=False, errors="default")
    syllables: list[str] = []
    cursor = 0
    for row in rows:
        text = row[0] if row else ""
        if not text:
            cursor += 1
            continue
        if any(is_cjk_ideograph(char) for char in text):
            cursor += 1
            continue
        if simplified.startswith(text, cursor):
            syllables.append(text)
            cursor += len(text)
            continue
        char = simplified[cursor]
        cursor += 1
        citation = _CITATION_TONES.get(char, {}).get(syllable_tone(text))
        syllables.append(text if citation is None else _retone(text, citation))
    if len(syllables) > 1 and simplified.endswith("儿") and simplified not in _ER_IS_A_SYLLABLE:
        syllables[-2:] = [syllables[-2] + "r"]
    return syllables


def word_pinyin(word: str) -> str:
    """Space-separated tone-marked pinyin for ``word``; ``""`` when it has no hanzi."""
    return " ".join(_syllables(word))


def pinyin_syllables(word: str) -> list[tuple[str, int]]:
    """``(syllable, tone)`` pairs — the input the tone-colour render hook needs."""
    return [(syllable, syllable_tone(syllable)) for syllable in _syllables(word)]


# The two spellings of the rhotacising suffix, whose syllable a dictionary may
# write as a bare ``r`` glued to the syllable before it (这儿 zhèr).
_ER_CHARS = frozenset("儿兒")
# CC-CEDICT ports that keep numbered pinyin (m2shá) or the ``u:`` umlaut
# spelling cannot be walked against tone-marked candidates at all.
_UNWALKABLE = re.compile(r"[1-4:]")
# A neutral syllable may carry an explicit 5 in the stored reading (zhèr5).
_NEUTRAL_MARKER = "5"
# More than one distinct split is a refusal, so the enumeration only has to
# outlive the first duplicate; the cap keeps a pathological word bounded.
_MAX_SOLUTIONS = 8


def _strip_tone(syllable: str) -> str:
    """``syllable`` without its tone diacritic (guó -> guo)."""
    decomposed = unicodedata.normalize("NFD", syllable)
    return unicodedata.normalize("NFC", "".join(c for c in decomposed if c not in _TONE_BY_MARK))


@cache
def _char_candidates(char: str) -> tuple[str, ...]:
    """Every pinyin string ``char`` may contribute to an attested reading.

    Heteronyms of the character AND of its simplified fold, casefolded: a
    traditional headword is stored under its own spelling but read through the
    simplified table (隻 vs 只), and taking both raises the walk's parse rate.
    Each candidate's toneless form joins it because a dictionary writes a
    neutral syllable without a mark (xuésheng), and 儿/兒 gains a bare ``r``
    for the rhotacised spelling.
    """
    from pypinyin import Style, pinyin

    sources = [char]
    simplified = to_simplified(char)
    if simplified and simplified != char:
        sources.append(simplified)
    out: list[str] = []
    for source in sources:
        for row in pinyin(source, style=Style.TONE, heteronym=True, errors="ignore"):
            for syllable in row:
                folded = unicodedata.normalize("NFC", syllable).casefold()
                out.append(folded)
                bare = _strip_tone(folded)
                if bare != folded:
                    out.append(bare)
    if char in _ER_CHARS:
        out.append("r")
    return tuple(dict.fromkeys(out))


def _reading_units(word: str) -> list[str]:
    """``word`` cut into the pieces one emitted syllable covers.

    One unit per ideograph, and one unit per run of anything else — a Latin run
    is a single syllable of the reading (AA制 aa zhì), so splitting it per
    character would space it out on the card.
    """
    units: list[str] = []
    for ideograph, chars in groupby(word, is_cjk_ideograph):
        if ideograph:
            units.extend(chars)
        else:
            units.append("".join(chars))
    return units


def _unit_candidates(unit: str) -> tuple[str, ...]:
    """Pinyin strings ``unit`` may cover; a non-hanzi run stands for itself."""
    if len(unit) == 1 and is_cjk_ideograph(unit):
        return _char_candidates(unit)
    return (unit.casefold(),)


def _normalize_attested(reading: str) -> str | None:
    """The attested string the walk consumes, or None when it cannot be walked."""
    folded = unicodedata.normalize("NFC", reading).casefold()
    folded = "".join(folded.replace("·", "").replace(",", "").split())
    if not folded or _UNWALKABLE.search(folded):
        return None
    return folded


def _merge_erhua(syllables: list[str]) -> list[str]:
    """A trailing bare ``r`` rhotacises the syllable before it, as in ``_syllables``."""
    if len(syllables) > 1 and syllables[-1] == "r":
        return [*syllables[:-2], syllables[-2] + "r"]
    return syllables


def reconcile_reading(word: str, reading: str, attested: Sequence[str]) -> str:
    """``reading`` re-derived from the ONE reading the dictionary attests.

    pypinyin reads a word out of context, so it picks the wrong heteronym
    (流血 liú xiě) and the full tone where the dictionary records a neutral one
    (先生 xiān shēng). When exactly one reading is attested for the card front,
    that string is authoritative — but it is stored unspaced, and the card's
    reading field, its tone colours and its audio filename all need syllables.
    So walk it: consume it left to right, taking one of each unit's candidate
    pinyin strings, and emit what was consumed.

    Every split is enumerated, and two or more distinct ones REFUSE: an
    overlapping candidate set (xi|an vs xia|n) has no right answer without
    context, and picking the first would be the homograph guess bulk mining
    must not make. Every other failure — nothing attested, several readings, a
    reading no candidate covers — refuses the same way and returns ``reading``
    untouched.

    The emitted syllables re-join to the attested string, so a reading that
    matched the dictionary before still matches it after. The one step past
    that point is the module's own 一/不 citation retone, which undoes the
    sandhi tone a dictionary records exactly as ``_syllables`` undoes
    pypinyin's.
    """
    distinct = list(dict.fromkeys(unicodedata.normalize("NFC", r) for r in attested if r))
    if len(distinct) != 1:
        return reading
    target = _normalize_attested(distinct[0])
    if target is None:
        return reading
    units = _reading_units(word)
    solutions: list[tuple[str, ...]] = []

    def consume(index: int, pos: int, emitted: list[str]) -> None:
        if len(solutions) >= _MAX_SOLUTIONS:
            return
        if index == len(units):
            if pos == len(target):
                solutions.append(tuple(emitted))
            return
        unit = units[index]
        literal = len(unit) != 1 or not is_cjk_ideograph(unit)
        for candidate in _unit_candidates(unit):
            for consumed in (candidate, candidate + _NEUTRAL_MARKER):
                if not target.startswith(consumed, pos):
                    continue
                emitted.append(unit if literal else candidate)
                consume(index + 1, pos + len(consumed), emitted)
                emitted.pop()

    consume(0, 0, [])
    walked = list(dict.fromkeys(solutions))
    if len(walked) != 1:
        return reading
    syllables: list[str] = []
    for unit, syllable in zip(units, walked[0], strict=True):
        citation = _CITATION_TONES.get(unit, {}).get(syllable_tone(syllable))
        syllables.append(syllable if citation is None else _retone(syllable, citation))
    return " ".join(_merge_erhua(syllables))


class ZhReadingSupport:
    """``ReadingSupport`` for zh: the token's surface, read as one word."""

    def word_reading(self, token: Any) -> str:
        return word_pinyin(token.surface)

    def reconcile(self, form: str, reading: str, attested: Sequence[str]) -> str:
        """Optional ``ReadingSupport`` seam: the dictionary's own reading, re-spaced.

        Probed with ``getattr`` at the emit site rather than declared on the
        Protocol — seven classes implement ``word_reading`` and only this one
        has a dictionary whose readings are the same romanisation its engine
        produces.
        """
        return reconcile_reading(form, reading, attested)
