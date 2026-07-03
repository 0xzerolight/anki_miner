"""Shared CSV/TSV frequency-row parsing + Yomitan rank normalization.

Single source of truth for the row-shape helpers that the per-source frequency
importer relies on. Originally extracted from the legacy single-CSV loader so
there is exactly one implementation of each.

Two concerns live here:

* **CSV column extraction** — ``_extract_word_rank`` / ``_is_word_first_header``
  / ``_WORD_FIRST_HEADER_COLS``: figure out which column is the word and which
  is the rank from a delimiter-split row, honouring an importer-written
  word-first header when present.
* **Yomitan ``freq`` rank normalization** — ``normalize_freq_rank`` and its
  helpers: collapse Yomitan's five spec-defined ``freq`` data shapes to an
  ``int`` rank (or ``None`` for display-only / invalid entries).
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any

logger = logging.getLogger(__name__)

# Ported from Yomitan Translator._numberRegex / _convertStringToNumber
# (ext/js/language/translator.js, upstream commit e2ed450): the first
# float-shaped run in a display string is its sortable rank. Lets string payloads
# like "1099/72000" or JPDB "1234㋕" keep the human string AND yield a number,
# instead of being rejected wholesale as display-only.
_NUMBER_RE = re.compile(r"[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?")

# Header first-column values that unambiguously declare (word, rank) column order.
# The Yomitan freq importer writes ``['term', 'rank']`` as its header; we recognise
# both ``term`` and ``word`` so user-exported variants are also covered.
_WORD_FIRST_HEADER_COLS = {"term", "word"}


def _is_word_first_header(row: list[str]) -> bool:
    """Return True when the header's first column names the word column.

    Detects the column-order contract written by the Yomitan freq importer
    (``['term', 'rank']``) so the CSV loader can skip the ambiguous int-first
    auto-detect for those files.
    """
    return bool(row) and row[0].strip().lower() in _WORD_FIRST_HEADER_COLS


def _extract_word_rank(row: list[str], *, word_first: bool = False) -> tuple[str, int | None]:
    """Extract a word and numeric rank from a row with any number of columns.

    When *word_first* is True the caller has already determined that col-0 is
    the word (e.g. because the file carries a recognised header whose first
    column is ``term`` or ``word``).  In that case the ambiguous int-first
    auto-detect path is skipped, which prevents fullwidth / ASCII digit-only
    terms like ``'１０'`` or ``'2020'`` from being misread as rank values in
    the standard 2-column case.

    For 2-column ``word_first`` files: ``(col-0, int(col-1))``.
    For 3+-column ``word_first`` files: col-0 is fixed as the word; the first
    numeric cell among the remaining columns becomes the rank.  This supports
    multi-column exports like ``term, reading, frequency, …`` where the word is
    always in col-0 but the rank is not in col-1.

    Without *word_first*, the legacy auto-detect logic is preserved:
    ``(rank, word)`` is tried first, then ``(word, rank)``, then a
    column-position-agnostic scan for files with 3+ columns.

    Args:
        row: CSV/TSV row as list of strings.
        word_first: When True, treat col-0 as the word and skip int-first detect.

    Returns:
        Tuple of (word, rank) or ("", None) if no valid pair found.
    """
    if word_first:
        word = row[0].strip() if row else ""
        if not word:
            return "", None
        if len(row) == 2:
            # Standard importer output: (word, rank) — col-1 must be numeric.
            try:
                return word, int(row[1].strip())
            except ValueError:
                return "", None
        # Multi-column with word-first header: scan cols 1+ for the first int.
        for cell in row[1:]:
            val = cell.strip()
            if not val:
                continue
            try:
                return word, int(val)
            except ValueError:
                continue
        return "", None

    # Try standard 2-column formats first: (rank, word) then (word, rank)
    try:
        rank = int(row[0].strip())
        word = row[1].strip()
        if word:
            return word, rank
    except ValueError:
        pass

    try:
        word = row[0].strip()
        rank = int(row[1].strip())
        if word:
            return word, rank
    except ValueError:
        pass

    # For multi-column files (e.g. term, reading, frequency, ...),
    # find first non-numeric column as word and first numeric column as rank
    if len(row) > 2:
        first_word = ""
        first_rank: int | None = None
        for cell in row:
            val = cell.strip()
            if not val:
                continue
            try:
                num = int(val)
                if first_rank is None:
                    first_rank = num
            except ValueError:
                if not first_word:
                    first_word = val
            if first_word and first_rank is not None:
                return first_word, first_rank

    return "", None


def normalize_freq_rank(data: Any) -> tuple[int | None, str | None]:
    """Extract ``(rank, display_value)`` from any of Yomitan's ``freq`` shapes.

    ``rank`` is the sortable integer; ``display_value`` is the human string a
    card should show instead of the bare rank (Yomitan's ``displayValue`` — set
    for string payloads and ``{value, displayValue}`` objects, None for plain
    ints). Returns ``(None, None)`` for display-only entries (e.g. ``"①"``, which
    has no numeric content) and for ranks outside the valid range (rank must be
    >= 1; a "0th most common word" is nonsense) — such entries are unusable and
    the importer skips them.
    """
    rank, display_value = _normalize_freq_rank_raw(data)
    if rank is None or rank < 1:
        return None, None
    return rank, display_value


def _normalize_freq_rank_raw(data: Any) -> tuple[int | None, str | None]:
    """Raw shape-dispatch for the spec-defined ``freq`` data shapes.

    Returns ``(rank, display_value)``; validity gating (rank >= 1) is applied by
    :func:`normalize_freq_rank`.
    """
    if isinstance(data, bool):
        # bool is a subclass of int; reject before the int branch below.
        return None, None

    if isinstance(data, int):
        return data, None

    if isinstance(data, str):
        # String payload: the whole string is the display value; the rank is its
        # first float-shaped run (Yomitan _getFrequencyInfo string branch).
        return _string_to_rank(data), (data.strip() or None)

    if isinstance(data, dict):
        # Outer envelope with `reading` + `frequency` (BCCWJ-style entries).
        # The reading itself is handled by the caller (it inspects the envelope
        # separately); here we recurse into `frequency` for the rank + display.
        if "frequency" in data:
            return _normalize_freq_rank_raw(data["frequency"])
        # Inner `GenericFrequencyData`: `{value, displayValue?}`.
        if "value" in data:
            value = data["value"]
            raw_display = data.get("displayValue")
            display = raw_display if isinstance(raw_display, str) else None
            if isinstance(value, bool):
                return None, display
            if isinstance(value, int):
                return value, display
            if isinstance(value, str):
                return _string_to_rank(value), display

    return None, None


def extract_envelope_reading(data: Any) -> str | None:
    """Return the BCCWJ-style envelope ``reading`` when present, else ``None``.

    Yomitan ``freq`` data may be an outer envelope dict carrying both a
    ``reading`` and a ``frequency``. The per-source importer keeps that reading
    (stored in its own column), unlike the legacy single-source CSV which
    discarded it. Only a non-empty string reading is returned.
    """
    if isinstance(data, dict):
        reading = data.get("reading")
        if isinstance(reading, str):
            reading = reading.strip()
            return reading or None
    return None


def _string_to_rank(s: str) -> int | None:
    """Sortable integer rank from a display string, or None if it has no number.

    Uses Yomitan's float-regex (:data:`_NUMBER_RE`) so "1099/72000" → 1099 and
    "1234㋕" → 1234, where the old ``int()`` parse rejected the whole string.
    Pure display-only markers ("①", "高") have no float-shaped run → None. The
    matched float is truncated toward zero (ranks are integers); a non-positive
    result is returned as-is and left for ``normalize_freq_rank`` to reject
    (rank must be >= 1).
    """
    match = _NUMBER_RE.search(s)
    if match is None:
        return None
    try:
        value = float(match.group(0))
    except ValueError:  # pragma: no cover - regex guarantees a valid float literal
        return None
    if not math.isfinite(value):
        return None
    return int(value)
