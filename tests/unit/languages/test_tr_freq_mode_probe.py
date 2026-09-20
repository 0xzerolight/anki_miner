"""S16: a hand-imported Turkish word-count list votes with Turkish terms, not with ja's (R6a)."""

from __future__ import annotations

from anki_miner.services.frequency import mode_probe, source_importer


def test_the_tables_are_ten_distinct_lowercase_terms_each_way():
    common, rare = mode_probe.MORE_COMMON_TERMS["tr"], mode_probe.LESS_COMMON_TERMS["tr"]
    assert len(set(common)) == len(common) == 10
    assert len(set(rare)) == len(rare) == 10
    assert not set(common) & set(rare)
    assert all(term == term.casefold() for term in common + rare)
    assert {"bir", "bu", "ne", "ve"} <= set(common)
    assert {"sincap", "pusula", "süpürge"} <= set(rare)


def test_an_occurrence_list_is_detected_from_its_own_terms():
    counts = {(term, None): 1_000_000 - i for i, term in enumerate(mode_probe.MORE_COMMON_TERMS["tr"])}
    counts.update({(term, None): 10 + i for i, term in enumerate(mode_probe.LESS_COMMON_TERMS["tr"])})

    _rows, converted = source_importer._iter_rank_rows(counts, "", "tr")

    assert converted is True


def test_a_rank_list_is_detected_from_its_own_terms():
    ranks = {(term, None): 1 + i for i, term in enumerate(mode_probe.MORE_COMMON_TERMS["tr"])}
    ranks.update({(term, None): 30_000 + i for i, term in enumerate(mode_probe.LESS_COMMON_TERMS["tr"])})

    _rows, converted = source_importer._iter_rank_rows(ranks, "", "tr")

    assert converted is False


def test_turkish_terms_never_steer_another_languages_source():
    counts = {(term, None): 1_000_000 for term in mode_probe.MORE_COMMON_TERMS["tr"]}
    counts.update({(term, None): 10 for term in mode_probe.LESS_COMMON_TERMS["tr"]})

    _rows, converted = source_importer._iter_rank_rows(counts, "", "ja")

    assert converted is False  # no ja probe term present => tie => rank-based
