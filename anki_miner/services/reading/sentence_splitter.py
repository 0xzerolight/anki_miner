"""Depth-gated Japanese sentence splitter for the reading-tab loaders.

Net-new and self-contained: this module owns the character policy (there is no
shared scanner to reuse — the old Yomitan-derived one was deleted in 3e10353).
A single left-to-right pass tracks bracket/quote depth; a terminator run at
depth 0 ends a sentence, a run inside brackets does not. A run of two-or-more
``．`` (or the ellipsis marks ``…‥``) is an ellipsis, not a terminator, so
``……。`` still splits on the ``。``. Unmatched closers are ignored, the
unterminated tail is flushed, and callers pass one paragraph at a time so an
unbalanced ``「`` only damages its own paragraph.
"""

from __future__ import annotations

# Terminators that always end a sentence at depth 0.
_HARD_TERMINATORS = frozenset("。！？!?‼⁉⁇⁈")
# Full-width period: a lone one terminates, a run of 2+ is an ellipsis.
_DOT = "．"
# Pure ellipsis marks — never terminate on their own.
_ELLIPSIS = frozenset("…‥")
_SENTENCE_PUNCT = _HARD_TERMINATORS | _ELLIPSIS | {_DOT}

# Bracket/quote pairs; depth rises on an opener, falls on a matching closer.
_OPENERS = frozenset("「『（〔［｛〈《【([{｟〝")
_CLOSERS = frozenset("」』）〕］｝〉》】)]}｠〟")


def _run_is_terminating(run: str) -> bool:
    """Whether a maximal run of sentence punctuation ends a sentence.

    Hard terminators always do; a lone ``．`` does; a 2+ run of ``．`` and the
    ellipsis marks do not.
    """
    if any(ch in _HARD_TERMINATORS for ch in run):
        return True
    i = 0
    n = len(run)
    while i < n:
        if run[i] == _DOT:
            j = i
            while j < n and run[j] == _DOT:
                j += 1
            if j - i == 1:  # a lone full-width period terminates
                return True
            i = j
        else:
            i += 1
    return False


def split_sentences(text: str, *, split_adjacent_quotes: bool = False) -> list[str]:
    """Split ``text`` into sentences; empty/whitespace-only results dropped.

    ``split_adjacent_quotes`` inserts a break between an adjacent ``」「`` pair
    at depth 0 (used only by the mokuro overflow fallback).
    """
    segments: list[str] = []
    buf: list[str] = []
    depth = 0
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c in _OPENERS:
            depth += 1
            buf.append(c)
            i += 1
        elif c in _CLOSERS:
            if depth > 0:  # unmatched closer: never goes negative
                depth -= 1
            buf.append(c)
            i += 1
            if split_adjacent_quotes and c == "」" and depth == 0 and i < n and text[i] == "「":
                segments.append("".join(buf))
                buf = []
        elif depth == 0 and c in _SENTENCE_PUNCT:
            j = i
            while j < n and text[j] in _SENTENCE_PUNCT:  # absorb the run
                j += 1
            run = text[i:j]
            buf.append(run)
            i = j
            if _run_is_terminating(run):
                segments.append("".join(buf))
                buf = []
        else:
            buf.append(c)
            i += 1
    if buf:  # flush the unterminated tail
        segments.append("".join(buf))
    return [s for s in (seg.strip() for seg in segments) if s]
