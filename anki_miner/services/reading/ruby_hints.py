"""Apply a book's own furigana to the words parsed from it (SK-31).

The EPUB loader keeps each ``<ruby>``'s ``(base, reading)`` pair on the unit
whose sentence holds the base (``ReadingUnit.ruby``). After the parser has
built a unit's words, :func:`apply_author_ruby` gives each word whose card
front is exactly a ruby base the author's reading, through the same four
fields the parser's own curated-override path sets
(``subtitle_parser._emit_word``'s ``reading_overridden`` branches):
``expression_reading`` and ``expression_furigana`` for the card, ``reading``
in katakana, and ``lemma_reading`` so the audio retry keeps it.

Only words whose card front is their surface take a hint: a verb or adjective
mines its dictionary form (``mined_form`` ≠ surface), and ruby over its stem
says nothing about that form's reading. The sentence furigana is left as the
tokenizer built it, as the curated-override path leaves it.

The parser calls this only for a profile that takes author ruby
(``LanguageProfile.takes_author_ruby``); the kana check here is what makes a
reading usable for such a profile.
"""

from __future__ import annotations

from collections.abc import Sequence

from anki_miner.models import TokenizedWord
from anki_miner.utils.text_utils import _format_furigana, hiragana_to_katakana, is_kana_only, katakana_to_hiragana


def _usable_readings(hints: Sequence[tuple[str, str]]) -> dict[str, str]:
    """Base → hiragana reading; a base given two different readings in one unit is dropped."""
    readings: dict[str, str | None] = {}
    for base, raw in hints:
        kana = katakana_to_hiragana(raw.strip())
        if not is_kana_only(kana):
            continue
        seen = readings.get(base, kana)
        readings[base] = kana if seen == kana else None
    return {base: kana for base, kana in readings.items() if kana is not None}


def apply_author_ruby(words: Sequence[TokenizedWord], hints: Sequence[tuple[str, str]]) -> list[str]:
    """Give each word whose card front is exactly a ruby base the author's reading.

    Args:
        words: The words the parser built from one unit (mutated in place).
        hints: That unit's ``(base, reading)`` pairs.

    Returns:
        The card fronts whose reading changed.
    """
    readings = _usable_readings(hints)
    changed: list[str] = []
    if not readings:
        return changed
    for word in words:
        front = word.mined_form
        kana = readings.get(word.surface)
        if kana is None or front != word.surface or kana == word.expression_reading:
            continue
        word.expression_reading = kana
        word.expression_furigana = _format_furigana(front, kana)
        word.reading = hiragana_to_katakana(kana)
        word.lemma_reading = kana
        changed.append(front)
    return changed
