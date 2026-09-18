"""S16: a hand-imported Polish word-count list votes with Polish probe terms (hermitdave pl_50k ranks, plan P10)."""

from __future__ import annotations

from anki_miner.services.frequency import mode_probe, source_importer

COMMON = ["nie", "to", "się", "w", "na", "i", "że", "z", "co", "jest"]  # pl_50k ranks 1-10
RARE = ["wiadro", "młotek", "kompas", "wagon", "teleskop", "latarnia", "wiewiórka", "latarka", "kufel", "ul"]


def test_the_polish_tables():
    assert mode_probe.MORE_COMMON_TERMS["pl"] == COMMON
    assert mode_probe.LESS_COMMON_TERMS["pl"] == RARE
    assert len(set(COMMON)) == len(set(RARE)) == 10 and not set(COMMON) & set(RARE)


def test_an_occurrence_list_is_detected_from_polish_terms():
    counts = {(term, None): 1_000_000 - i for i, term in enumerate(COMMON)}
    counts.update({(term, None): 10 + i for i, term in enumerate(RARE)})
    _rows, converted = source_importer._iter_rank_rows(counts, "", "pl")
    assert converted is True


def test_a_rank_list_is_detected_from_polish_terms():
    ranks = {(term, None): 1 + i for i, term in enumerate(COMMON)}
    ranks.update({(term, None): 30_000 + i for i, term in enumerate(RARE)})
    _rows, converted = source_importer._iter_rank_rows(ranks, "", "pl")
    assert converted is False
