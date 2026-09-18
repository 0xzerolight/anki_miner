"""S16: hermitdave's Croatian word-count list is recognised as occurrence counts by its own hr terms."""

from __future__ import annotations

from anki_miner.services.frequency import mode_probe, source_importer

#: Ranks in hermitdave FrequencyWords content/2018/hr/hr_50k.txt (630,560 B, 202,438,751 occurrences).
COMMON_RANKS = {"je": 1, "da": 2, "ne": 3, "se": 4, "i": 5, "u": 6, "to": 7, "sam": 8, "\u0161to": 9, "na": 10}
RARE_RANKS = {
    "teleskop": 15130, "sidro": 16010, "svjetionik": 19133, "kanu": 26282, "ko\u0161nica": 26304,
    "\u010de\u0161alj": 27483, "lopata": 32678, "jazavac": 44137, "dvogled": 45172, "mor\u017e": 47081,
}  # fmt: skip


def test_the_tables_are_the_measured_terms():
    assert mode_probe.MORE_COMMON_TERMS["hr"] == list(COMMON_RANKS)
    assert sorted(mode_probe.LESS_COMMON_TERMS["hr"]) == sorted(RARE_RANKS)
    assert all(term == term.lower() for term in COMMON_RANKS | RARE_RANKS)
    assert not set(COMMON_RANKS) & set(RARE_RANKS)


def test_an_occurrence_list_is_detected_from_its_own_terms():
    counts = {(term, None): 8_500_000 - rank for term, rank in COMMON_RANKS.items()}
    counts.update({(term, None): 900 - rank // 100 for term, rank in RARE_RANKS.items()})
    _rows, converted = source_importer._iter_rank_rows(counts, "", "hr")
    assert converted is True


def test_a_rank_list_is_detected_from_its_own_terms():
    ranks = {(term, None): rank for term, rank in (COMMON_RANKS | RARE_RANKS).items()}
    _rows, converted = source_importer._iter_rank_rows(ranks, "", "hr")
    assert converted is False


def test_without_the_row_an_occurrence_list_falls_to_rank_based():
    """What the row fixes: with no hr terms nothing votes, so je (8,496,117 occurrences) would be stored as
    rank 8,496,117 and the catalogue's lemmatise=True would never run (ranks are never aggregated)."""
    counts = {(term, None): 8_500_000 - rank for term, rank in COMMON_RANKS.items()}
    _rows, converted = source_importer._iter_rank_rows(counts, "", "xx")
    assert converted is False
