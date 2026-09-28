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
to be followed by whitespace or end-of-text. ``abbreviations`` adds the period
model for a language whose ``.`` is also an abbreviation dot (``Dr.``, ``z.B.``)
and whose ``...`` is an ellipsis; an empty set keeps that model off. Under
``space_aware``, a run followed by whitespace, an optional dialogue dash and a
lowercase letter does not end the sentence either (``A 3. emeleten``,
``¿Vienes? —preguntó``): a cased sentence never starts lowercase, and an
uncased script has no lowercase letter to trip it.
"""

from __future__ import annotations

import re
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
# The ASCII period of a language's period model (SentenceRules.abbreviations).
_ASCII_DOT = "."
_WHITESPACE_RE = re.compile(r"\s")
# What follows a space_aware terminator run: whitespace, an optional dialogue
# dash (hyphen, en dash, em dash, or the "--" plain-text books write for the em
# dash) and its optional space, then the next character.
_NEXT_WORD_RE = re.compile(r"\s+(?:(?:--?|[–—])\s*)?(\S)")
# The number of an ordinal (``am 3. Oktober``) that SentenceRules.ordinal_leads gates.
_ORDINAL_RE = re.compile(r"[0-9]{1,3}")

# Bracket/quote pairs; depth rises on an opener, falls on a matching closer.
_OPENERS = frozenset("「｢『（〔［｛〈《【([{｟〝")
_CLOSERS = frozenset("」｣』）〕］｝〉》】)]}｠〟")


def _policy(
    rules: SentenceRules | None,
) -> tuple[frozenset[str], frozenset[str], frozenset[str], frozenset[str], bool, frozenset[str]]:
    """(terminators, openers, closers, punct, space_aware, abbreviations) for this call.

    ``rules is None`` is the Japanese module constants, verbatim.
    """
    if rules is None:
        return _HARD_TERMINATORS, _OPENERS, _CLOSERS, _SENTENCE_PUNCT, False, frozenset()
    punct = rules.terminators | rules.ellipses | {_DOT}
    return rules.terminators, rules.openers, rules.closers, punct, rules.space_aware, rules.abbreviations


def _period_continues(
    run: str,
    buf: list[str],
    abbreviations: frozenset[str],
    openers: frozenset[str],
    ordinal_leads: frozenset[str],
) -> bool:
    """Whether a terminating ASCII-dot run is an abbreviation dot, an ordinal dot or an ellipsis.

    Only consulted when a language declares abbreviations (the period model).
    A run of two or more ASCII dots and nothing else is an ellipsis — the Latin
    mirror of the full-width ``．．`` rule. A lone dot continues the sentence
    when the text back to the previous whitespace, minus leading openers and
    the dot itself, casefolds to a declared key, or is a 1-3 digit number whose
    preceding word casefolds to one of ``ordinal_leads`` (``am 3.``, not ``ist 30.``).
    """
    if len(run) >= 2 and set(run) == {_ASCII_DOT}:
        return True
    if run != _ASCII_DOT:
        return False
    before = "".join(buf)[: -len(run)]
    strip = "".join(openers)
    word = _WHITESPACE_RE.split(before)[-1].lstrip(strip)
    if not word:
        return False
    if word.casefold() in abbreviations:
        return True
    if not (ordinal_leads and _ORDINAL_RE.fullmatch(word)):
        return False
    words = before.split()
    return len(words) >= 2 and words[-2].lstrip(strip).casefold() in ordinal_leads


def _lowercase_follows(text: str, j: int) -> bool:
    """Whether the word after the whitespace at ``j`` (past a dialogue dash) starts lowercase.

    A cased sentence never starts lowercase, so such a word continues the
    sentence: the rest of a date or ordinal (hu ``2003. szeptember``, hr
    ``12. svibnja``, nb ``17. mai``) or a speech tag after ``?``/``!`` (es
    ``¿Vienes? —preguntó``, pl ``— zapytała``, tr ``Nereye? diye sordu``).
    """
    match = _NEXT_WORD_RE.match(text, j)
    return match is not None and match.group(1).islower()


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
    terminators, openers, closers, punct, space_aware, abbreviations = _policy(rules)
    # S9, read from `rules` rather than through _policy: services/cue_merge.py:71
    # unpacks the same six values, so the tuple's shape is shared API.
    split_on_whitespace = rules is not None and rules.split_on_whitespace
    ordinal_leads = rules.ordinal_leads if rules is not None else frozenset()
    joiners = rules.whitespace_joiners if rules is not None else frozenset()
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
            # run is followed by whitespace or end-of-text, so "3.14" survives,
            # and not when the next word (past a dialogue dash) is lowercase.
            # The period model (abbreviations) keeps "Dr." and "..." in the sentence.
            terminates = _run_is_terminating(run, terminators) and (
                not space_aware or j >= n or (text[j].isspace() and not _lowercase_follows(text, j))
            )
            if terminates and not (
                abbreviations and _period_continues(run, buf, abbreviations, openers, ordinal_leads)
            ):
                segments.append("".join(buf))
                buf = []
        elif split_on_whitespace and depth == 0 and c.isspace():
            # A whitespace RUN is one boundary, and it belongs to neither side:
            # the trailing .strip() would drop it anyway, and absorbing it here
            # keeps a multi-space gap from yielding an empty middle segment.
            # A joiner on either side (th ๆ, a numeral) keeps the run in the sentence.
            start = i
            while i < n and text[i].isspace():
                i += 1
            if joiners and ((start > 0 and text[start - 1] in joiners) or (i < n and text[i] in joiners)):
                buf.append(text[start:i])
            elif buf:
                segments.append("".join(buf))
                buf = []
        else:
            buf.append(c)
            i += 1
    if buf:  # flush the unterminated tail
        segments.append("".join(buf))
    return [s for s in (seg.strip() for seg in segments) if s]
