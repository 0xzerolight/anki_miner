"""Dictionary labels for a field that stacks two or more dictionaries' blocks.

A provider's block carries no label: one dictionary's entry on a card needs no
name. When a field stacks several blocks (the Glossary, or a stacking profile's
Definition), each block gets its dictionary's short name in front of its body so
the reader can tell the entries apart.
"""

from __future__ import annotations

import html
import re

#: One trailing ``[…]`` suffix, or a ``(…)`` one that opens with a version or date
#: (``(v2)``, ``(2026-10-16)``); any other parenthesis is part of the name.
_TRAILING_SUFFIX_RE = re.compile(r"\s*(?:\[[^\[\]]*\]|\(v?\d[^()]*\))\s*$")

#: A provider block's envelope open tag; group 2 is its escaped dictionary title.
_ENVELOPE_OPEN_RE = re.compile(r'(<li data-dictionary="([^"]*)"[^>]*>)')


def display_title(title: str) -> str:
    """The dictionary title without one trailing revision suffix.

    ``"JMdict [2026-10-16]"`` reads ``"JMdict"``: a ``[…]`` suffix always goes, a
    ``(…)`` one only when it reads as a version or date (``"X (v2)"`` reads ``"X"``),
    so ``"JMdict (English)"`` keeps its qualifier. A title that is only a suffix
    stays whole, so the result is never empty.
    """
    match = _TRAILING_SUFFIX_RE.search(title)
    if match is None:
        return title
    return title[: match.start()].rstrip() or title


def _label_block(block: str) -> str:
    def insert(match: re.Match[str]) -> str:
        name = html.escape(display_title(html.unescape(match.group(2))), quote=False)
        return f"{match.group(1)}<i>({name})</i> "

    return _ENVELOPE_OPEN_RE.sub(insert, block, count=1)


def label_stacked(blocks: list[str]) -> str:
    """Join provider blocks; with two or more, name each block's dictionary.

    The label goes right after each block's envelope open tag. A block with no
    envelope passes through unchanged. Not idempotent: call it once, on the raw
    provider blocks, as its one call site does.
    """
    if len(blocks) < 2:
        return "".join(blocks)
    return "".join(_label_block(block) for block in blocks)
