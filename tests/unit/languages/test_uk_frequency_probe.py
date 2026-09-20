"""S16: a hand-imported Ukrainian word-count list votes with Ukrainian probe terms (hermitdave uk_50k, P12)."""

from __future__ import annotations

from anki_miner.services.frequency import mode_probe, source_importer

#: uk_50k.txt ranks 1-10. `что` and `ты` are Russian rows the OpenSubtitles list really carries.
COMMON = ["я", "не", "в", "що", "на", "це", "ти", "что", "так", "у"]
#: Rare-but-real nouns at ranks 17,205 / 18,678 / 19,300 / 22,881 / 28,842 / 33,676 / 34,228 /
#: 36,395 / 40,643 / 44,521.
RARE = ["телескоп", "вагон", "компас", "ліхтарик", "якір", "бочка", "цвях", "ліхтар", "кухоль", "білка"]


def test_the_ukrainian_tables():
    assert mode_probe.MORE_COMMON_TERMS["uk"] == COMMON
    assert mode_probe.LESS_COMMON_TERMS["uk"] == RARE
    assert len(set(COMMON)) == len(set(RARE)) == 10 and not set(COMMON) & set(RARE)


def test_an_occurrence_list_is_detected_from_ukrainian_terms():
    counts = {(term, None): 1_000_000 - i for i, term in enumerate(COMMON)}
    counts.update({(term, None): 10 + i for i, term in enumerate(RARE)})
    _rows, converted = source_importer._iter_rank_rows(counts, "", "uk")
    assert converted is True


def test_a_rank_list_is_detected_from_ukrainian_terms():
    ranks = {(term, None): 1 + i for i, term in enumerate(COMMON)}
    ranks.update({(term, None): 30_000 + i for i, term in enumerate(RARE)})
    _rows, converted = source_importer._iter_rank_rows(ranks, "", "uk")
    assert converted is False


def test_the_probe_terms_avoid_the_apostrophe_gap():
    """P12: hermitdave's list STRIPS the apostrophe (пять, мяч, імя) instead of folding it, so an
    apostrophe term would vote with whatever the stripped spelling happens to rank. Folding the
    apostrophe away in the key would win those ranks back and lose every dictionary hit, so the
    limit is recorded in the catalogue and kept out of the probe tables."""
    import anki_miner.languages.uk.catalog as catalog

    assert not any("'" in term for term in COMMON + RARE)
    assert catalog.__doc__ is not None and "strips the Ukrainian apostrophe" in catalog.__doc__
