# Derived from Yomitan (https://github.com/yomidevs/yomitan),
# ext/js/dom/text-source-generator.js (extractSentence) and the default
# terminationCharacters in ext/data/schemas/options-schema.json,
# commit e2ed450c2f11a591922822e77f008e70a87daf0c.
#
# Copyright (C) 2024-2026  Yomitan Authors
# Copyright (C) 2026  anki_miner contributors (Python port)
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Codepoint sentence-boundary extraction (port of Yomitan ``extractSentence``).

Yomitan's ``extractSentence`` walks backward and forward from a term anchor
over a codepoint array, tracking a nested-quote stack (「」『』…) and a
configurable terminator map, absorbing terminator runs and trimming whitespace
inside the anchor bounds. This is the single-string reduction of that DOM-range
routine: Python ``str`` is already codepoint-indexed, and the two anchors are
the term's ``[start, end)`` offsets within ``text`` rather than a live DOM
range.

Deviations from upstream, both deliberate:

- The forward walk's quote include-at-end loop re-probes ``backward_quote_map``
  (the SAME map its outer check used), fixing an upstream forward/backward
  asymmetry that re-probed ``forwardQuoteMap``. Inert under the default maps
  (every quote include-flag is false), so default output is byte-identical.
- DOM-only concerns (imposter elements, cross-node scanning, layout-aware
  scan-extent expansion) have no analog and are dropped.
"""

from __future__ import annotations

from typing import Mapping

# Default terminator / quote sets, transcribed from options-schema.json's
# default ``terminationCharacters`` (commit e2ed450). Structure mirrors
# text-scanner.js's map build (lines 339-346):
#   character2 is None  -> terminator:  char -> (include_at_start, include_at_end)
#   character2 present  -> quote pair:  forward  char1 -> (char2, include_at_start)
#                                       backward char2 -> (char1, include_at_end)
# (character1, character2, include_at_start, include_at_end)
_TERMINATION_CHARACTERS: tuple[tuple[str, str | None, bool, bool], ...] = (
    ("「", "」", False, False),
    ("『", "』", False, False),
    ('"', '"', False, False),
    ("'", "'", False, False),
    (".", None, False, True),
    ("!", None, False, True),
    ("?", None, False, True),
    ("．", None, False, True),
    ("。", None, False, True),
    ("！", None, False, True),
    ("？", None, False, True),
    ("…", None, False, True),
    ("︒", None, False, True),  # vertical ideographic full stop
    ("︕", None, False, True),  # vertical exclamation mark
    ("︖", None, False, True),  # vertical question mark
    ("︙", None, False, True),  # vertical ellipsis
)


def _build_maps() -> tuple[
    dict[str, tuple[bool, bool]],
    dict[str, tuple[str, bool]],
    dict[str, tuple[str, bool]],
]:
    terminator_map: dict[str, tuple[bool, bool]] = {}
    forward_quote_map: dict[str, tuple[str, bool]] = {}
    backward_quote_map: dict[str, tuple[str, bool]] = {}
    for char1, char2, include_at_start, include_at_end in _TERMINATION_CHARACTERS:
        if char2 is None:
            terminator_map[char1] = (include_at_start, include_at_end)
        else:
            forward_quote_map[char1] = (char2, include_at_start)
            backward_quote_map[char2] = (char1, include_at_end)
    return terminator_map, forward_quote_map, backward_quote_map


_DEFAULT_TERMINATOR_MAP, _DEFAULT_FORWARD_QUOTE_MAP, _DEFAULT_BACKWARD_QUOTE_MAP = _build_maps()


def _is_whitespace(char: str) -> bool:
    """Mirror Yomitan ``_isWhitespace`` (``string.trim().length === 0``)."""
    return char.strip() == ""


def extract_sentence(
    text: str,
    term_start: int,
    term_end: int,
    *,
    terminate_at_newlines: bool = True,
    terminator_map: Mapping[str, tuple[bool, bool]] = _DEFAULT_TERMINATOR_MAP,
    forward_quote_map: Mapping[str, tuple[str, bool]] = _DEFAULT_FORWARD_QUOTE_MAP,
    backward_quote_map: Mapping[str, tuple[str, bool]] = _DEFAULT_BACKWARD_QUOTE_MAP,
) -> tuple[str, int]:
    """Extract the sentence containing ``text[term_start:term_end]``.

    Returns ``(sentence, offset)`` where ``sentence`` is the trimmed
    surrounding sentence and ``offset`` is the term's start position within it
    (upstream ``{text, offset}``). Offsets outside ``0 <= start <= end <= len``
    return ``(text, term_start)`` unchanged (a no-op the caller can detect via
    ``sentence == text``).
    """
    text_length = len(text)
    if not (0 <= term_start <= term_end <= text_length):
        return text, term_start

    start_length = term_start
    text_end_anchor = term_end
    cursor_start = start_length
    cursor_end = text_end_anchor

    # Move backward.
    quote_stack: list[str] = []
    while cursor_start > 0:
        c = text[cursor_start - 1]
        if c == "\n" and terminate_at_newlines:
            break

        if not quote_stack:
            terminator_info = terminator_map.get(c)
            if terminator_info is not None:
                # Consume the terminator run while it is included at start.
                while terminator_info[0] and cursor_start > 0:
                    cursor_start -= 1
                    if cursor_start == 0:
                        break
                    c = text[cursor_start - 1]
                    terminator_info = terminator_map.get(c)
                    if terminator_info is None:
                        break
                break

        quote_info = forward_quote_map.get(c)
        if quote_info is not None:
            if not quote_stack:
                while quote_info[1] and cursor_start > 0:
                    cursor_start -= 1
                    if cursor_start == 0:
                        break
                    c = text[cursor_start - 1]
                    quote_info = forward_quote_map.get(c)
                    if quote_info is None:
                        break
                break
            elif quote_stack[0] == c:
                quote_stack.pop()
                cursor_start -= 1
                continue

        quote_info = backward_quote_map.get(c)
        if quote_info is not None:
            quote_stack.insert(0, quote_info[0])
        cursor_start -= 1

    # Move forward.
    quote_stack = []
    while cursor_end < text_length:
        c = text[cursor_end]
        if c == "\n" and terminate_at_newlines:
            break

        if not quote_stack:
            terminator_info = terminator_map.get(c)
            if terminator_info is not None:
                # Consume the terminator run while it is included at end.
                while terminator_info[1] and cursor_end < text_length:
                    cursor_end += 1
                    if cursor_end == text_length:
                        break
                    c = text[cursor_end]
                    terminator_info = terminator_map.get(c)
                    if terminator_info is None:
                        break
                break

        quote_info = backward_quote_map.get(c)
        if quote_info is not None:
            if not quote_stack:
                while quote_info[1] and cursor_end < text_length:
                    cursor_end += 1
                    if cursor_end == text_length:
                        break
                    c = text[cursor_end]
                    # Symmetry fix: re-probe the SAME (backward) map the outer
                    # check used; upstream re-probed forward_quote_map here.
                    quote_info = backward_quote_map.get(c)
                    if quote_info is None:
                        break
                break
            elif quote_stack[0] == c:
                quote_stack.pop()
                cursor_end += 1
                continue

        quote_info = forward_quote_map.get(c)
        if quote_info is not None:
            quote_stack.insert(0, quote_info[0])
        cursor_end += 1

    # Trim whitespace within the anchor bounds only.
    while cursor_start < start_length and _is_whitespace(text[cursor_start]):
        cursor_start += 1
    while cursor_end > text_end_anchor and _is_whitespace(text[cursor_end - 1]):
        cursor_end -= 1

    return text[cursor_start:cursor_end], start_length - cursor_start
