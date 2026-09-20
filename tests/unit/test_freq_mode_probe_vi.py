"""S16: Vietnamese votes with its own probe terms (ranks read from the opensubtitles-vi-word asset)."""

from __future__ import annotations

from anki_miner.languages.vi.keys import vi_fold_term
from anki_miner.services.frequency import mode_probe, source_importer


def test_tables_are_ten_distinct_folded_terms_each_way():
    common, rare = mode_probe.MORE_COMMON_TERMS["vi"], mode_probe.LESS_COMMON_TERMS["vi"]
    assert len(set(common)) == len(common) == 10 and len(set(rare)) == len(rare) == 10
    assert not set(common) & set(rare)
    assert all(term == vi_fold_term(term) for term in common + rare)


def test_an_occurrence_list_is_detected_from_its_own_terms():
    counts = {(term, None): 1_000_000 - i for i, term in enumerate(mode_probe.MORE_COMMON_TERMS["vi"])}
    counts.update({(term, None): 10 + i for i, term in enumerate(mode_probe.LESS_COMMON_TERMS["vi"])})
    _rows, converted = source_importer._iter_rank_rows(counts, "", "vi")
    assert converted is True


def test_a_rank_list_is_detected_from_its_own_terms():
    ranks = {(term, None): 1 + i for i, term in enumerate(mode_probe.MORE_COMMON_TERMS["vi"])}
    ranks.update({(term, None): 30_000 + i for i, term in enumerate(mode_probe.LESS_COMMON_TERMS["vi"])})
    _rows, converted = source_importer._iter_rank_rows(ranks, "", "vi")
    assert converted is False
