"""S16: a hand-imported Norwegian word-count list is recognised as occurrence counts by its own nb terms (B17)."""

from __future__ import annotations

from anki_miner.services.frequency import mode_probe, source_importer

#: Ranks in hermitdave FrequencyWords content/2018/no/no_50k.txt (commit 525f9b56).
COMMON_RANKS = {"jeg": 1, "det": 2, "er": 3, "du": 4, "ikke": 5, "en": 6, "og": 7, "i": 8, "har": 9, "vi": 10}
RARE_RANKS = {
    "kikkert": 18657, "grevling": 23245, "fyrtårn": 25323, "pinnsvin": 25668, "bikube": 32550,
    "hvalross": 36131, "strykejern": 38398, "sparegris": 38964, "lanterne": 39094, "meisel": 45654,
}  # fmt: skip


def test_the_tables_are_the_measured_terms():
    assert mode_probe.MORE_COMMON_TERMS["nb"] == list(COMMON_RANKS)
    assert sorted(mode_probe.LESS_COMMON_TERMS["nb"]) == sorted(RARE_RANKS)
    assert all(term == term.lower() for term in COMMON_RANKS | RARE_RANKS)


def test_an_occurrence_list_is_detected_from_its_own_terms():
    counts = {(term, None): 2_000_000 - rank for term, rank in COMMON_RANKS.items()}
    counts.update({(term, None): 60 - rank // 1000 for term, rank in RARE_RANKS.items()})
    _rows, converted = source_importer._iter_rank_rows(counts, "", "nb")
    assert converted is True


def test_a_rank_list_is_detected_from_its_own_terms():
    ranks = {(term, None): rank for term, rank in (COMMON_RANKS | RARE_RANKS).items()}
    _rows, converted = source_importer._iter_rank_rows(ranks, "", "nb")
    assert converted is False


def test_without_the_row_an_occurrence_list_falls_to_rank_based():
    """What the row fixes: a language with no terms votes with nothing, and the list would import inverted."""
    counts = {(term, None): 2_000_000 - rank for term, rank in COMMON_RANKS.items()}
    _rows, converted = source_importer._iter_rank_rows(counts, "", "xx")
    assert converted is False
