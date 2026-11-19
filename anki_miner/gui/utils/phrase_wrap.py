"""Display-only BudouX phrase wrapping for Qt plain-text views.

Japanese has no spaces, so Qt's UAX #14 line breaking may wrap a plain-text
view between any two characters — mid-word, mid-conjugation (行きま/しょう).
BudouX segments a sentence into phrase chunks; this module turns that into a
Qt-compatible wrapping contract by joining the grapheme clusters *inside* each
chunk with WORD JOINER (U+2060, an invisible no-break character Qt's line breaker
honours) and concatenating chunks bare, so the only remaining break
opportunities are the phrase boundaries.

The transform is strictly a display affair. Callers must keep the pristine
string on every other surface — ``COPY_ROLE``, tooltips, and above all model
data — so no invisible character can ever reach the clipboard or a card (the
U+202A-in-card-text dedup bug is the cautionary tale). Stripping
:data:`WORD_JOINER` from the output always yields the input unchanged.
"""

from __future__ import annotations

import logging
import unicodedata
from typing import TYPE_CHECKING

from anki_miner.utils.ja_normalize import is_cjk_ideograph

if TYPE_CHECKING:
    import budoux

logger = logging.getLogger(__name__)

WORD_JOINER = "⁠"

# Hiragana, fullwidth katakana (incl. ー), halfwidth katakana. Kanji comes from
# is_cjk_ideograph so the gate matches the project's Yomitan-ported ranges.
_KANA_RANGES: tuple[tuple[int, int], ...] = (
    (0x3041, 0x309F),
    (0x30A0, 0x30FF),
    (0xFF66, 0xFF9D),
)

_ZWJ = "\u200d"

# Code points that extend the cluster before them: variation selectors (VS16
# emoji presentation, IVS kanji variants), emoji skin-tone modifiers and the
# tag characters of subdivision flags. Combining marks are matched by category.
_EXTENDING_RANGES: tuple[tuple[int, int], ...] = (
    (0xFE00, 0xFE0F),
    (0xE0100, 0xE01EF),
    (0x1F3FB, 0x1F3FF),
    (0xE0020, 0xE007F),
)
_REGIONAL_INDICATORS = (0x1F1E6, 0x1F1FF)

_parser: budoux.Parser | None = None


def _japanese_parser() -> budoux.Parser:
    """Load the default Japanese parser once, on first use (~15 KB of JSON).

    budoux is imported here, not at module level: it reads ``skip_nodes.json``
    at import time, and a broken install (say, a bundle missing the data files)
    should degrade this feature to plain unwrapped display via the caller's
    catch — never take app startup down with it.
    """
    global _parser
    if _parser is None:
        import budoux

        _parser = budoux.load_default_japanese_parser()
    return _parser


def _contains_japanese(text: str) -> bool:
    return any(any(low <= ord(char) <= high for low, high in _KANA_RANGES) or is_cjk_ideograph(char) for char in text)


def _is_regional_indicator(char: str) -> bool:
    return _REGIONAL_INDICATORS[0] <= ord(char) <= _REGIONAL_INDICATORS[1]


def _graphemes(chunk: str) -> list[str]:
    """Split *chunk* into the clusters a joiner must not land inside.

    A stdlib approximation of UAX #29, enough for what subtitles carry: a
    combining mark, ZWJ, variation selector, skin-tone modifier or tag joins
    the cluster before it, as does the code point after a ZWJ and the second
    regional indicator of a flag pair.
    """
    clusters: list[str] = []
    for char in chunk:
        if clusters and (
            char == _ZWJ
            or clusters[-1].endswith(_ZWJ)
            or unicodedata.category(char).startswith("M")
            or any(low <= ord(char) <= high for low, high in _EXTENDING_RANGES)
            or (_is_regional_indicator(char) and len(clusters[-1]) == 1 and _is_regional_indicator(clusters[-1]))
        ):
            clusters[-1] += char
        else:
            clusters.append(char)
    return clusters


def phrase_wrap_ja(text: str) -> str:
    """Return ``text`` with line breaks suppressed inside BudouX phrases.

    Text without a single Japanese character passes through untouched: BudouX
    would return ASCII as one chunk, and welding "hello world" into an
    unbreakable run is worse than Qt's default space-based wrapping. Any
    parser failure also returns the input — neater wrapping is never worth an
    exception in a populate path.

    Args:
        text: The sentence to prepare for display.

    Returns:
        The display string; ``result.replace(WORD_JOINER, "") == text`` always.
    """
    if not text or not _contains_japanese(text):
        return text
    try:
        chunks = _japanese_parser().parse(text)
    except Exception:
        logger.debug("BudouX parse failed; showing the sentence unwrapped", exc_info=True)
        return text
    return "".join(WORD_JOINER.join(_graphemes(chunk)) for chunk in chunks)
