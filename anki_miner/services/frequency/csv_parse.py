"""Shared CSV/TSV frequency-row parsing + Yomitan rank normalization.

Single source of truth for the row-shape helpers that the per-source frequency
importer relies on. Originally extracted from the legacy single-CSV loader and
``yomitan_freq_importer`` so there is exactly one implementation of each.

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
from typing import Any

logger = logging.getLogger(__name__)

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


def normalize_freq_rank(data: Any) -> int | None:
    """Extract an integer rank from any of Yomitan's five ``freq`` data shapes.

    Returns ``None`` for display-only entries (e.g. ``"①"``) and for ranks
    outside the valid range (rank must be >= 1; a "0th most common word" is
    nonsense). Invalid-rank entries are lumped into the caller's display-only
    count by design — they're equally unusable downstream.
    """
    rank = _normalize_freq_rank_raw(data)
    if rank is None or rank < 1:
        return None
    return rank


def _normalize_freq_rank_raw(data: Any) -> int | None:
    """Raw shape-dispatch for the five spec-defined ``freq`` data shapes.

    Validity gating (rank >= 1) is applied by :func:`normalize_freq_rank`.
    """
    if isinstance(data, bool):
        # bool is a subclass of int; reject before the int branch below.
        return None

    if isinstance(data, int):
        return data

    if isinstance(data, str):
        return _try_int(data)

    if isinstance(data, dict):
        # Outer envelope with `reading` + `frequency` (BCCWJ-style entries).
        # The reading itself is handled by the caller (it inspects the envelope
        # separately); here we recurse into `frequency` for the rank only.
        if "frequency" in data:
            return _normalize_freq_rank_raw(data["frequency"])
        # Inner `GenericFrequencyData`: `{value, displayValue?}`.
        if "value" in data:
            value = data["value"]
            if isinstance(value, bool):
                return None
            if isinstance(value, int):
                return value
            if isinstance(value, str):
                return _try_int(value)

    return None


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


def _try_int(s: str) -> int | None:
    """Best-effort string→int conversion that tolerates surrounding whitespace.

    Returns ``None`` for display-only markers like ``"①"`` or ``"高"`` that
    have no integer interpretation.
    """
    s = s.strip()
    if not s:
        return None
    try:
        return int(s)
    except ValueError:
        return None
