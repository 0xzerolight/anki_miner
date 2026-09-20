"""S16: a hand-imported Russian word-count list votes with Russian probe terms (hermitdave ru_50k ranks, plan D12)."""

from __future__ import annotations

from anki_miner.services.frequency import mode_probe, source_importer

COMMON = ["я", "не", "что", "в", "и", "ты", "это", "на", "с", "он"]  # ru_50k ranks 1-10
RARE = ["компас", "вагон", "телескоп", "фонарь", "белка", "фонарик", "кружка", "улей", "бочка", "якорь"]


def test_the_russian_tables():
    assert mode_probe.MORE_COMMON_TERMS["ru"] == COMMON
    assert mode_probe.LESS_COMMON_TERMS["ru"] == RARE
    assert len(set(COMMON)) == len(set(RARE)) == 10 and not set(COMMON) & set(RARE)


def test_an_occurrence_list_is_detected_from_russian_terms():
    counts = {(term, None): 1_000_000 - i for i, term in enumerate(COMMON)}
    counts.update({(term, None): 10 + i for i, term in enumerate(RARE)})
    _rows, converted = source_importer._iter_rank_rows(counts, "", "ru")
    assert converted is True


def test_a_rank_list_is_detected_from_russian_terms():
    ranks = {(term, None): 1 + i for i, term in enumerate(COMMON)}
    ranks.update({(term, None): 30_000 + i for i, term in enumerate(RARE)})
    _rows, converted = source_importer._iter_rank_rows(ranks, "", "ru")
    assert converted is False
