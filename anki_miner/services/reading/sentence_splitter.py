"""Depth-gated sentence splitter for the reading-tab loaders.

Net-new and self-contained: this module owns the Japanese character policy
(there is no shared scanner to reuse — the old Yomitan-derived one was deleted
in 3e10353). A single left-to-right pass tracks bracket/quote depth; a
terminator run at depth 0 ends a sentence, a run inside brackets does not. A run
of two-or-more ``．`` (or the ellipsis marks ``…‥``) is an ellipsis, not a
terminator, so ``……。`` still splits on the ``。``. Only *matched* bracket pairs
gate depth: a pre-scan (``_matched_openers``) pairs openers to closers, so an
unmatched opener and an unmatched closer are both treated as ordinary characters
and cannot suppress splitting. The unterminated tail is flushed.

The module constants below are the Japanese policy and stay the behaviour of a
``rules=None`` call, byte for byte. Another language passes its profile's
``SentenceRules`` instead; ``space_aware`` is what lets a terminator set that
contains ``.`` (Korean's) leave ``3.14`` and ``Dr.`` alone, by requiring the run
to be followed by whitespace or end-of-text.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from anki_miner.languages.profile import SentenceRules

# Terminators that always end a sentence at depth 0.
_HARD_TERMINATORS = frozenset("。｡！？!?‼⁉⁇⁈")
# Full-width period: a lone one terminates, a run of 2+ is an ellipsis.
_DOT = "．"
# Pure ellipsis marks — never terminate on their own.
_ELLIPSIS = frozenset("…‥")
_SENTENCE_PUNCT = _HARD_TERMINATORS | _ELLIPSIS | {_DOT}

# Bracket/quote pairs; depth rises on an opener, falls on a matching closer.
_OPENERS = frozenset("「｢『（〔［｛〈《【([{｟〝")
_CLOSERS = frozenset("」｣』）〕］｝〉》】)]}｠〟")


def _policy(
    rules: SentenceRules | None,
) -> tuple[frozenset[str], frozenset[str], frozenset[str], frozenset[str], bool]:
    """(terminators, openers, closers, punct, space_aware) for this call.

    ``rules is None`` is the Japanese module constants, verbatim.
    """
    if rules is None:
        return _HARD_TERMINATORS, _OPENERS, _CLOSERS, _SENTENCE_PUNCT, False
    punct = rules.terminators | rules.ellipses | {_DOT}
    return rules.terminators, rules.openers, rules.closers, punct, rules.space_aware


def _run_is_terminating(run: str, terminators: frozenset[str]) -> bool:
    """Whether a maximal run of sentence punctuation ends a sentence.

    Hard terminators always do; a lone ``．`` does; a 2+ run of ``．`` and the
    ellipsis marks do not. The ``．`` rule is language-neutral.
    """
    if any(ch in terminators for ch in run):
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


def _matched_openers(text: str, openers: frozenset[str], closers: frozenset[str]) -> set[int]:
    """Indices of openers that have a matching closer later in ``text``.

    A plain LIFO stack: any closer pops the nearest still-open opener (bracket
    *family* is not checked — a shorter split on cross-family OCR garbage is
    harmless). Openers still on the stack at the end are unmatched and must not
    gate depth, so an unbalanced ``「`` no longer suppresses every terminator
    after it (the mokuro cover-blurb "wall of text" bug).
    """
    stack: list[int] = []
    matched: set[int] = set()
    for i, ch in enumerate(text):
        if ch in openers:
            stack.append(i)
        elif ch in closers and stack:
            matched.add(stack.pop())
    return matched


def split_sentences(
    text: str,
    *,
    split_adjacent_quotes: bool = False,
    rules: SentenceRules | None = None,
) -> list[str]:
    """Split ``text`` into sentences; empty/whitespace-only results dropped.

    ``split_adjacent_quotes`` inserts a break between an adjacent ``」「`` pair
    at depth 0 (used only by the mokuro overflow fallback). ``rules`` is the
    mining language's character policy; ``None`` is the Japanese module
    constants and the pre-multilanguage behaviour, verbatim.
    """
    terminators, openers, closers, punct, space_aware = _policy(rules)
    matched_openers = _matched_openers(text, openers, closers)
    segments: list[str] = []
    buf: list[str] = []
    depth = 0
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c in openers:
            if i in matched_openers:  # unmatched openers stay depth-neutral
                depth += 1
            buf.append(c)
            i += 1
        elif c in closers:
            if depth > 0:  # unmatched closer: never goes negative
                depth -= 1
            buf.append(c)
            i += 1
            if split_adjacent_quotes and c == "」" and depth == 0 and i < n and text[i] == "「":
                segments.append("".join(buf))
                buf = []
        elif depth == 0 and c in punct:
            j = i
            while j < n and text[j] in punct:  # absorb the run
                j += 1
            run = text[i:j]
            buf.append(run)
            i = j
            # space_aware: a terminator set containing "." only splits when the
            # run is followed by whitespace or end-of-text, so "3.14" survives.
            if _run_is_terminating(run, terminators) and (not space_aware or j >= n or text[j].isspace()):
                segments.append("".join(buf))
                buf = []
        else:
            buf.append(c)
            i += 1
    if buf:  # flush the unterminated tail
        segments.append("".join(buf))
    return [s for s in (seg.strip() for seg in segments) if s]
