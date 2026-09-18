"""S16: Lithuanian votes with its own probe terms (hermitdave lt_50k.txt ranks)."""

from __future__ import annotations

from anki_miner.services.frequency import mode_probe, source_importer


def test_tables_are_ten_distinct_lowercase_terms_each_way():
    common, rare = mode_probe.MORE_COMMON_TERMS["lt"], mode_probe.LESS_COMMON_TERMS["lt"]
    assert len(set(common)) == len(common) == 10 and len(set(rare)) == len(rare) == 10
    assert not set(common) & set(rare)
    assert all(term == term.lower() for term in common + rare)


def test_an_occurrence_list_is_detected_from_its_own_terms():
    counts = {(term, None): 1_000_000 - i for i, term in enumerate(mode_probe.MORE_COMMON_TERMS["lt"])}
    counts.update({(term, None): 10 + i for i, term in enumerate(mode_probe.LESS_COMMON_TERMS["lt"])})
    _rows, converted = source_importer._iter_rank_rows(counts, "", "lt")
    assert converted is True


def test_a_rank_list_is_detected_from_its_own_terms():
    ranks = {(term, None): 1 + i for i, term in enumerate(mode_probe.MORE_COMMON_TERMS["lt"])}
    ranks.update({(term, None): 30_000 + i for i, term in enumerate(mode_probe.LESS_COMMON_TERMS["lt"])})
    _rows, converted = source_importer._iter_rank_rows(ranks, "", "lt")
    assert converted is False
