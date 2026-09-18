"""S16: a hand-imported Swedish word-count list is recognised as occurrence counts by its own sv terms (B17)."""

from __future__ import annotations

from anki_miner.services.frequency import mode_probe, source_importer

#: Ranks in hermitdave FrequencyWords content/2018/sv/sv_50k.txt (the catalogue row's own file).
COMMON_RANKS = {"jag": 1, "det": 2, "är": 3, "du": 4, "att": 5, "inte": 6, "en": 7, "och": 8, "har": 9, "vi": 10}
RARE_RANKS = {
    "kikare": 14114, "kastrull": 27439, "grävling": 28954, "lykta": 30913, "valross": 31633,
    "bikupa": 33639, "igelkott": 34426, "skottkärra": 38214, "strykjärn": 41490, "spargris": 45483,
}  # fmt: skip


def test_the_tables_are_the_measured_terms():
    assert mode_probe.MORE_COMMON_TERMS["sv"] == list(COMMON_RANKS)
    assert sorted(mode_probe.LESS_COMMON_TERMS["sv"]) == sorted(RARE_RANKS)
    assert all(term == term.lower() for term in COMMON_RANKS | RARE_RANKS)


def test_an_occurrence_list_is_detected_from_its_own_terms():
    counts = {(term, None): 2_000_000 - rank for term, rank in COMMON_RANKS.items()}
    counts.update({(term, None): 60 - rank // 1000 for term, rank in RARE_RANKS.items()})
    _rows, converted = source_importer._iter_rank_rows(counts, "", "sv")
    assert converted is True


def test_a_rank_list_is_detected_from_its_own_terms():
    ranks = {(term, None): rank for term, rank in (COMMON_RANKS | RARE_RANKS).items()}
    _rows, converted = source_importer._iter_rank_rows(ranks, "", "sv")
    assert converted is False


def test_without_the_row_an_occurrence_list_falls_to_rank_based():
    """What the row fixes: a language with no terms votes with nothing, and the list would import inverted."""
    counts = {(term, None): 2_000_000 - rank for term, rank in COMMON_RANKS.items()}
    _rows, converted = source_importer._iter_rank_rows(counts, "", "xx")
    assert converted is False
