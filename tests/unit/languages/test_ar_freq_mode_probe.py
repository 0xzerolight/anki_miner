"""S16: a hand-imported Arabic word-count list votes with Arabic terms (hermitdave ranks)."""

from __future__ import annotations

from anki_miner.services.frequency import mode_probe, source_importer


def test_the_tables_are_ten_distinct_terms_each_way():
    common, rare = mode_probe.MORE_COMMON_TERMS["ar"], mode_probe.LESS_COMMON_TERMS["ar"]
    assert len(set(common)) == len(common) == 10
    assert len(set(rare)) == len(rare) == 10
    assert not set(common) & set(rare)
    assert {"\u0641\u064a", "\u0645\u0646", "\u0644\u0627"} <= set(common)  # fii, min, laa
    assert {
        "\u0645\u0631\u0648\u062d\u0629",
        "\u0634\u0645\u0639\u0629",
        "\u0639\u0646\u0643\u0628\u0648\u062a",
    } <= set(
        rare
    )  # marwaha, sham'a, 'ankabuut


def test_an_occurrence_list_is_detected_from_its_own_terms():
    counts = {(term, None): 1_000_000 - i for i, term in enumerate(mode_probe.MORE_COMMON_TERMS["ar"])}
    counts.update({(term, None): 10 + i for i, term in enumerate(mode_probe.LESS_COMMON_TERMS["ar"])})
    _rows, converted = source_importer._iter_rank_rows(counts, "", "ar")
    assert converted is True


def test_a_rank_list_is_detected_from_its_own_terms():
    ranks = {(term, None): 1 + i for i, term in enumerate(mode_probe.MORE_COMMON_TERMS["ar"])}
    ranks.update({(term, None): 30_000 + i for i, term in enumerate(mode_probe.LESS_COMMON_TERMS["ar"])})
    _rows, converted = source_importer._iter_rank_rows(ranks, "", "ar")
    assert converted is False
