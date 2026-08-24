"""Display-only BudouX phrase wrapping for Qt plain-text views.

Japanese has no spaces, so Qt's UAX #14 line breaking may wrap a plain-text
view between any two characters — mid-word, mid-conjugation (行きま/しょう).
BudouX segments a sentence into phrase chunks; this module turns that into a
Qt-compatible wrapping contract by joining the characters *inside* each chunk
with WORD JOINER (U+2060, an invisible no-break character Qt's line breaker
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
    return "".join(WORD_JOINER.join(chunk) for chunk in chunks)
