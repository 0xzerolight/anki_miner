"""S16: every Western 8 language votes with its own probe terms."""

from __future__ import annotations

import pytest

from anki_miner.services.frequency import mode_probe, source_importer

WESTERN_8 = ("en", "de", "fr", "es", "it", "pt", "nl", "ca")


@pytest.mark.parametrize("code", WESTERN_8)
def test_tables_are_ten_distinct_terms_each_way(code):
    common, rare = mode_probe.MORE_COMMON_TERMS[code], mode_probe.LESS_COMMON_TERMS[code]
    assert len(set(common)) == len(common) == 10
    assert len(set(rare)) == len(rare) == 10
    assert not set(common) & set(rare)
    assert all(term == term.lower() for term in common + rare)


@pytest.mark.parametrize("code", WESTERN_8)
def test_an_occurrence_list_is_detected_from_its_own_terms(code):
    counts = {(term, None): 1_000_000 - i for i, term in enumerate(mode_probe.MORE_COMMON_TERMS[code])}
    counts.update({(term, None): 10 + i for i, term in enumerate(mode_probe.LESS_COMMON_TERMS[code])})

    _rows, converted = source_importer._iter_rank_rows(counts, "", code)

    assert converted is True


@pytest.mark.parametrize("code", WESTERN_8)
def test_a_rank_list_is_detected_from_its_own_terms(code):
    ranks = {(term, None): 1 + i for i, term in enumerate(mode_probe.MORE_COMMON_TERMS[code])}
    ranks.update({(term, None): 30_000 + i for i, term in enumerate(mode_probe.LESS_COMMON_TERMS[code])})

    _rows, converted = source_importer._iter_rank_rows(ranks, "", code)

    assert converted is False


def test_japanese_chinese_and_korean_rows_are_unchanged():
    assert mode_probe.MORE_COMMON_TERMS["ja"][:3] == ["来る", "言う", "出る"]
    assert mode_probe.LESS_COMMON_TERMS["ko"][-1] == "미나리"
    assert len(mode_probe.MORE_COMMON_TERMS["zh"]) == 15
