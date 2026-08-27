"""Word-audio candidate construction for Card Backfill.

A mined ``TokenizedWord`` carries ``expression_reading``, ``lemma`` and
``lemma_reading`` straight off the parse. An existing Anki note carries none of
them: backfill recovers a reading from a stored field, from furigana brackets,
or from a context-free tokenizer parse (``card_backfiller._resolve_context``),
and a lemma from a single-token parse. This module turns that recovered triple
into the same ``(kanji, kana)`` ladder mining feeds the fetcher chain, so a
backfilled card resolves through exactly the same sources — and lands on
exactly the same media entry — a mined card would have.

Takes plain strings rather than ``card_backfiller._NoteContext`` on purpose:
``card_backfiller`` imports this module, so a context-typed signature would
close an import cycle.

Synthesis follows ``deck_filter._build_word``: ``pos=None`` and ``orth_base=""``
make ``TokenizedWord.mined_form`` return ``surface`` verbatim, so the ladder is
built around the spelling the card actually has rather than a re-derived one.
"""

import logging
from typing import Any

from anki_miner.models import TokenizedWord
from anki_miner.services.audio_fetch_common import expression_audio_candidates
from anki_miner.utils.text_utils import generate_reading, katakana_to_hiragana

logger = logging.getLogger(__name__)


def _lemma_reading(lemma: str, tagger: Any | None) -> str:
    """Kana reading for ``lemma``, or "" when it cannot be produced.

    Only ever called for a lemma that differs from the mined form, so the cost
    is bounded to the words that can actually contribute a second candidate.
    A tagger failure is environmental (a half-installed UniDic, a sibling
    session's pip install) and must degrade to no lemma candidate rather than
    sinking the whole scan.
    """
    if not lemma or tagger is None:
        return ""
    try:
        return katakana_to_hiragana(generate_reading(lemma, tagger))
    except Exception as exc:  # pragma: no cover - tagger failure is environmental
        logger.debug("Lemma reading unavailable for %s: %s", lemma, exc)
        return ""


def word_audio_candidates(
    mined_form: str,
    reading: str,
    lemma: str,
    tagger: Any | None,
) -> list[tuple[str, str]]:
    """Ordered ``(kanji, kana)`` query pairs for one existing note.

    Returns ``[]`` when there is nothing to query — a blank expression or a
    blank reading. The empty list is a legitimate result the caller treats as
    "no audio proposal", not an error.
    """
    if not mined_form.strip() or not reading.strip():
        return []
    word = TokenizedWord(
        surface=mined_form,
        lemma=lemma or mined_form,
        reading=reading,
        sentence="",
        start_time=0.0,
        end_time=0.0,
        duration=0.0,
        pos=None,
        orth_base="",
        expression_reading=reading,
        lemma_reading=_lemma_reading(lemma if lemma != mined_form else "", tagger),
    )
    return expression_audio_candidates(word)
