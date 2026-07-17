"""Pasted-text source loader for the Reading → Text sub-tab.

The simplest reading source: ``ref.text`` already holds decoded plain text, so
there is no file I/O, no encoding sniffing, and no Aozora markup handling
(users pasting an Aozora-formatted file should mine it in the Novels tab).
Identity is deliberately constant — series/episode/title are all "Text" — per
the sub-tab's design: pasted snippets come from arbitrary places and derived
titles would be noise in history/stats.
"""

from __future__ import annotations

from anki_miner.models.reading import ReadingDocument, ReadingSourceRef, ReadingUnit

from .sentence_splitter import split_sentences


def load(ref: ReadingSourceRef) -> ReadingDocument:
    """Split pasted text into sentence units and return a book document.

    Blank lines delimit paragraphs (the ``¶N`` location label); each non-blank
    physical line is stripped (including full-width indents) and sentence-split.
    Empty or whitespace-only text yields an empty-units document —
    ``process_reading`` surfaces the "no words" outcome.
    """
    # Physical lines only (\r\n / \r / \n), like aozora's _splitlines —
    # str.splitlines() would also break on \v/\f/NEL/U+2028 from PDF/web pastes.
    text = (ref.text or "").replace("\r\n", "\n").replace("\r", "\n")

    units: list[ReadingUnit] = []
    index = 0
    para_no = 0
    for raw in text.split("\n"):
        stripped = raw.strip()
        if not stripped:
            continue
        para_no += 1
        for sentence in split_sentences(stripped):
            units.append(
                ReadingUnit(
                    text=sentence,
                    index=index,
                    location_label=f"¶{para_no}",
                    image_ref=None,
                )
            )
            index += 1

    return ReadingDocument(
        title="Text",
        kind="book",
        series="Text",
        episode="Text",
        units=units,
    )
