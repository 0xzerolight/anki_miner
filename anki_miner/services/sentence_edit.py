"""Materialise a Word Curator sentence edit into a rebuilt ``TokenizedWord``.

The curator stamps :class:`~anki_miner.models.word.SentenceEdit` as *intent*
(the rewritten text plus the span of the word the user picked). This module is
the only reader of that field, mirroring ``word_filter.expand_word_lines`` for
``line_expansion``: one resolver, one copy of the rules, called by the processor
after line expansions have materialised. The editor dialog uses the two small
helpers for its live preselection, so what it highlights is what resolves.

``parse_line`` is the mining parser's per-sentence entry
(``EpisodeProcessor.parse_sentence_fn`` → ``SubtitleParserService.parse_text_units``
over one ``ReadingUnit``). It normalises the text the same way the edited
preview was produced, so the stored span lands on the same token.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable, Sequence

from anki_miner.models.word import TokenizedWord

logger = logging.getLogger(__name__)

ParseLine = Callable[[str], list[TokenizedWord]]


def pick_target(tokens: Sequence[TokenizedWord], start: int, end: int) -> TokenizedWord | None:
    """The token whose surface span is ``[start, end)``, else the first overlapping one.

    Tokens with untracked offsets (``-1``) can never match: a word the parser
    could not locate in its own sentence cannot be the one the user pointed at.
    """
    for token in tokens:
        if token.surface_start >= 0 and (token.surface_start, token.surface_end) == (start, end):
            return token
    for token in tokens:
        if token.surface_start >= 0 and token.surface_start < end and token.surface_end > start:
            return token
    return None


def nearest_token(tokens: Sequence[TokenizedWord], start: int) -> TokenizedWord | None:
    """The token starting closest to ``start``; ties go to the earlier token."""
    best: TokenizedWord | None = None
    best_distance = -1
    for token in tokens:
        if token.surface_start < 0:
            continue
        distance = abs(token.surface_start - start)
        if best is None or distance < best_distance:
            best, best_distance = token, distance
    return best


def resolve_sentence_edit(word: TokenizedWord, parse_line: ParseLine) -> TokenizedWord:
    """Rebuild ``word`` from the token the user chose inside the edited sentence.

    Returns ``word`` unchanged when it carries no edit. On a resolved span the
    result is the parsed token (surface, lemma, readings, POS, mined form, bold
    span, furigana — everything text-derived) carrying the original word's
    timing, media file and curator overrides. Frequency fields are left at
    their parse-time defaults (``None``/empty) for the processor to re-attach,
    since the rank belonged to the old word. The intent is absorbed
    (``sentence_edit=None``) so a second pass cannot double-apply it.

    A span that resolves to no token (a different parser configuration than
    the one the editor used, which the dialog never allows) keeps the original
    word, minus the intent, and says so at WARNING — the card is still made,
    with the sentence the user saw first.
    """
    edit = word.sentence_edit
    if edit is None:
        return word
    tokens = parse_line(edit.text)
    target = pick_target(tokens, edit.target_start, edit.target_end)
    if target is None:
        logger.warning(
            "sentence edit: no mineable word at %d-%d in %r; keeping the original word %r",
            edit.target_start,
            edit.target_end,
            edit.text,
            word.mined_form,
        )
        return dataclasses.replace(word, sentence_edit=None)
    return dataclasses.replace(
        target,
        start_time=word.start_time,
        end_time=word.end_time,
        duration=word.duration,
        video_file=word.video_file,
        clip_override=word.clip_override,
        screenshot_override=word.screenshot_override,
        line_expansion=(0, 0),
        sentence_translation=word.sentence_translation,
        sentence_candidates=[],
        sentence_edit=None,
    )
