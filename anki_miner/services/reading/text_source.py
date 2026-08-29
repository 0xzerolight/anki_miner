"""Pasted-text source loader for the Reading → Text sub-tab.

The simplest reading source: ``ref.text`` already holds decoded plain text, so
there is no file I/O, no encoding sniffing, and no Aozora markup handling
(users pasting an Aozora-formatted file should mine it in the Novels tab).
Identity is deliberately constant — series/episode/title are all "Text" — per
the sub-tab's design: pasted snippets come from arbitrary places and derived
titles would be noise in history/stats.

Pasted text has no page of its own, so the sub-tab may hand over one optional
card image in ``ref.image_root``; every unit shares it, exactly as epub shares
a cover, and it lands in the Picture field of every card from the run.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from anki_miner.exceptions import OperationCancelled
from anki_miner.models.reading import ImageRef, ReadingDocument, ReadingSourceRef, ReadingUnit
from anki_miner.utils.logging_ext import log_summary

from .sentence_splitter import split_sentences

if TYPE_CHECKING:
    from anki_miner.languages.profile import SentenceRules

logger = logging.getLogger(__name__)


def _raise_if_cancelled(cancel_check: Callable[[], bool] | None) -> None:
    if cancel_check is not None and cancel_check():
        raise OperationCancelled("Reading load cancelled")


def load(
    ref: ReadingSourceRef,
    *,
    cancel_check: Callable[[], bool] | None = None,
    rules: SentenceRules | None = None,
) -> ReadingDocument:
    """Split pasted text into sentence units and return a book document.

    Blank lines delimit paragraphs (the ``¶N`` location label); each non-blank
    physical line is stripped (including full-width indents) and sentence-split.
    Empty or whitespace-only text yields an empty-units document —
    ``process_reading`` surfaces the "no words" outcome.

    ``rules`` is the mining language's sentence-splitting policy; ``None`` is
    the splitter's built-in Japanese one.
    """
    _raise_if_cancelled(cancel_check)
    # Physical lines only (\r\n / \r / \n), like aozora's _splitlines —
    # str.splitlines() would also break on \v/\f/NEL/U+2028 from PDF/web pastes.
    text = (ref.text or "").replace("\r\n", "\n").replace("\r", "\n")

    # One frozen ref shared by every unit — the shape epub uses for a book
    # cover, so phase-3's per-ref cache materializes the picked image once for
    # the whole run and every card carries the same Picture.
    image_ref = ImageRef(ref.image_root) if ref.image_root is not None else None

    units: list[ReadingUnit] = []
    index = 0
    para_no = 0
    skipped = 0
    for raw in text.split("\n"):
        _raise_if_cancelled(cancel_check)
        stripped = raw.strip()
        if not stripped:
            skipped += 1
            continue
        para_no += 1
        for sentence in split_sentences(stripped, rules=rules):
            _raise_if_cancelled(cancel_check)
            units.append(
                ReadingUnit(
                    text=sentence,
                    index=index,
                    location_label=f"¶{para_no}",
                    image_ref=image_ref,
                )
            )
            index += 1

    doc = ReadingDocument(
        title="Text",
        kind="book",
        series="Text",
        episode="Text",
        units=units,
    )
    log_summary(
        logger,
        "Text parse",
        file=ref.path or ref.title,
        paragraphs=para_no,
        units=index,
        chars=sum(len(unit.text) for unit in units),
        skipped=skipped,
    )
    return doc
