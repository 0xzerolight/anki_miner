"""Statistical probe for a frequency source's *direction* (rank- vs occurrence-based).

Rank-based lists (JPDB, BCCWJ) number the *most* frequent word ``1`` — smaller is
more common. Occurrence-based lists store a raw count — *larger* is more common,
so filtering/sorting a card by ``max_frequency_rank`` silently inverts unless the
list is first re-ranked. Yomitan zips can *declare* ``frequencyMode``; plain CSVs
and undeclared zips cannot, so we fall back to the statistical probe below.

The heuristic is a paired-sign vote over two curated 10-term Japanese lists
(known-common vs known-rare). For every common/rare pair that both carry a value
it accumulates ``sign(common.max - rare.min) + sign(common.min - rare.max)``. A
positive total means the common terms hold the *larger* numbers (larger = more
frequent → occurrence-based / ``descending``); negative means the *smaller*
numbers (rank-based / ``ascending``); zero (e.g. no probe terms present) is
ambiguous → ``None``. The pairing makes it robust to partial coverage: a source
that only contains a few of the probe terms still votes with whatever it has.

Ported from Yomitan
``ext/js/pages/settings/sort-frequency-dictionary-controller.js``
(``SortFrequencyDictionaryController._getFrequencyOrder``, lines 154-236) at
upstream commit ``e2ed450``. Term lists and the paired-sign accumulation are
verbatim; the DB round-trip is replaced by a caller-supplied ``lookup``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence

from anki_miner.services.frequency.storage import FreqRow

# Declared ``frequencyMode`` values (Yomitan ``index.json``).
OCCURRENCE_BASED = "occurrence-based"
RANK_BASED = "rank-based"

# Probe verdicts (Yomitan sort orders): larger value = more common (occurrence)
# vs smaller value = more common (rank).
DESCENDING = "descending"
ASCENDING = "ascending"

# Curated probe term lists, keyed by source language. Verbatim from Yomitan's
# _getFrequencyOrder (moreCommonTerms / lessCommonTerms).
MORE_COMMON_TERMS: dict[str, list[str]] = {
    "ja": ["来る", "言う", "出る", "入る", "方", "男", "女", "今", "何", "時"],
}
LESS_COMMON_TERMS: dict[str, list[str]] = {
    "ja": ["行なう", "論じる", "過す", "行方", "人口", "猫", "犬", "滝", "理", "暁"],
}


def _sign(value: int) -> int:
    """``Math.sign`` for ints: -1, 0, or 1."""
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def _terms_for(table: dict[str, list[str]], source_language: str) -> list[str]:
    """Probe terms for ``source_language``; aggregate all when it is unknown/blank.

    Mirrors Yomitan's ``dictionaryLang === ''`` branch, which pools every
    language's terms when the source declares no language.
    """
    if source_language and source_language in table:
        return list(table[source_language])
    pooled: list[str] = []
    for terms in table.values():
        pooled.extend(terms)
    return pooled


def _min_max(values: Iterable[int]) -> tuple[bool, int, int]:
    """Return ``(has_value, min, max)`` over ``values`` (has_value False if empty)."""
    has_value = False
    min_value = 0
    max_value = 0
    for v in values:
        if not has_value:
            min_value = max_value = v
            has_value = True
        else:
            if v < min_value:
                min_value = v
            if v > max_value:
                max_value = v
    return has_value, min_value, max_value


def probe_direction(
    lookup: Callable[[str], Sequence[int]],
    source_language: str = "ja",
) -> str | None:
    """Vote on a source's direction from its numeric values.

    Args:
        lookup: Maps a probe term to the numeric values stored for it (empty if
            the term is absent). A term may carry several values (one per
            reading); min/max across them are used.
        source_language: Language of the source; selects the probe term lists.

    Returns:
        :data:`DESCENDING` (occurrence-based), :data:`ASCENDING` (rank-based), or
        ``None`` when the vote is a tie (typically no probe terms present).
    """
    more_terms = _terms_for(MORE_COMMON_TERMS, source_language)
    less_terms = _terms_for(LESS_COMMON_TERMS, source_language)

    more_details = [_min_max(lookup(term)) for term in more_terms]
    less_details = [_min_max(lookup(term)) for term in less_terms]

    result = 0
    for has1, min1, max1 in more_details:
        if not has1:
            continue
        for has2, min2, max2 in less_details:
            if not has2:
                continue
            result += _sign(max1 - min2) + _sign(min1 - max2)

    if result > 0:
        return DESCENDING
    if result < 0:
        return ASCENDING
    return None


def resolve_is_occurrence(
    declared_mode: str,
    term_values: Mapping[str, Sequence[int]],
    source_language: str = "ja",
) -> bool:
    """Decide whether a source is occurrence-based (higher value = more common).

    A declared ``frequencyMode`` is authoritative; the statistical probe runs
    only when the mode is undeclared (blank/unknown).

    Args:
        declared_mode: The source's declared ``frequencyMode`` (``""`` for CSVs
            and undeclared zips).
        term_values: Maps every stored term to its numeric values.
        source_language: Language passed through to :func:`probe_direction`.
    """
    if declared_mode == OCCURRENCE_BASED:
        return True
    if declared_mode == RANK_BASED:
        return False
    return probe_direction(lambda term: term_values.get(term, ()), source_language) == DESCENDING


def convert_to_ranks(rows: Iterable[FreqRow]) -> list[FreqRow]:
    """Re-rank occurrence-based rows: sort by value descending, assign ``1..n``.

    The incoming ``rank`` column holds the raw occurrence value; the largest
    value becomes rank 1. Ties break by ``(term, reading)`` for a deterministic,
    human-scannable order. ``display_value`` (the original figure, e.g. a count)
    is preserved unchanged.
    """
    ordered = sorted(rows, key=lambda r: (-r[2], r[0], r[1] or ""))
    return [(term, reading, new_rank, display) for new_rank, (term, reading, _value, display) in enumerate(ordered, 1)]
