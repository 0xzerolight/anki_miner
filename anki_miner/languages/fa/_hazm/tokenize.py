"""hazm's word split (``word_tokenizer.py:63``), as spans.

Upstream pads its punctuation pattern with spaces and splits the result, which
loses every offset. Mining needs the offsets: a surface is always a verbatim
slice of the normalised line, so the same alternation runs as a scanner here and
the token list is derived from the spans, not the other way round.

``join_verb_parts`` is deliberately absent: it glues "gofte shode ast" into one
underscore-joined token, which is not a slice of any line.
"""

from __future__ import annotations

import re

#: hazm's pattern, alternative for alternative:
#: ``([\N{ARABIC QUESTION MARK}!?]+|[\d.:]+|[:.,;>)]}"<([{/\\])`` -- a run of
#: terminators, a number/clock run, then a single punctuation mark. The last
#: alternative is this port's addition: the run of everything that is neither
#: whitespace nor one of those marks, which is what upstream's split on spaces
#: leaves behind. A ZWNJ is in no class, so it stays inside its word.
_PUNCTUATION = (
    ":."
    "\N{ARABIC COMMA}"
    "\N{ARABIC SEMICOLON}"
    "\N{RIGHT-POINTING DOUBLE ANGLE QUOTATION MARK}"
    "\N{LEFT-POINTING DOUBLE ANGLE QUOTATION MARK}"
    r"\]\)\}\[\({/\\\""
)
_TERMINATORS = "\N{ARABIC QUESTION MARK}" + "!?"

_TOKEN_RE = re.compile(f"[{_TERMINATORS}]+" r"|[\d.:]+" f"|[{_PUNCTUATION}]" f"|[^\\s{_TERMINATORS}{_PUNCTUATION}]+")


def iter_spans(text: str) -> list[tuple[int, int]]:
    """``(start, end)`` for every token, in order, never overlapping."""
    return [match.span() for match in _TOKEN_RE.finditer(text)]


def split_words(text: str) -> list[str]:
    """The tokens themselves, each a verbatim slice of *text*."""
    return [text[start:end] for start, end in iter_spans(text)]
