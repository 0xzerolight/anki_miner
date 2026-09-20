"""S16: a hand-imported Hebrew word-count list votes with Hebrew terms (hermitdave he_50k ranks).

Both tables were checked against he_50k AND against wty-he-en: every rare term has at least one
noun lemma row, which is what stops the probe voting with a string the dictionary has never heard
of. ``gaash`` was dropped for exactly that reason -- rank 15,783 and zero rows, because it occurs
only bound.
"""

from __future__ import annotations

import unicodedata

from anki_miner.languages.he.script import he_fold, is_he_letter
from anki_miner.services.frequency import mode_probe, source_importer


def test_the_tables_are_ten_distinct_terms_each_way():
    common, rare = mode_probe.MORE_COMMON_TERMS["he"], mode_probe.LESS_COMMON_TERMS["he"]
    assert len(set(common)) == len(common) == 10
    assert len(set(rare)) == len(rare) == 10
    assert not set(common) & set(rare)


def test_every_probe_term_is_bare_hebrew_so_the_fold_cannot_move_it():
    """A pointed term would fold to a different string than the list stores and never match."""
    for term in (*mode_probe.MORE_COMMON_TERMS["he"], *mode_probe.LESS_COMMON_TERMS["he"]):
        assert term and all(is_he_letter(char) for char in term), term
        assert he_fold(term) == term
        assert unicodedata.normalize("NFC", term) == term


def test_an_occurrence_list_is_detected_from_its_own_terms():
    counts = {(term, None): 1_000_000 - i for i, term in enumerate(mode_probe.MORE_COMMON_TERMS["he"])}
    counts.update({(term, None): 10 + i for i, term in enumerate(mode_probe.LESS_COMMON_TERMS["he"])})
    _rows, converted = source_importer._iter_rank_rows(counts, "", "he")
    assert converted is True


def test_a_rank_list_is_detected_from_its_own_terms():
    ranks = {(term, None): 1 + i for i, term in enumerate(mode_probe.MORE_COMMON_TERMS["he"])}
    ranks.update({(term, None): 30_000 + i for i, term in enumerate(mode_probe.LESS_COMMON_TERMS["he"])})
    _rows, converted = source_importer._iter_rank_rows(ranks, "", "he")
    assert converted is False


def test_the_common_terms_are_the_function_words_a_subtitle_list_leads_with():
    """Nine of the ten are stopwords-iso entries; the probe is about rank, not about mining."""
    from anki_miner.languages.he.stopwords import HE_FUNCTION_WORDS

    common = mode_probe.MORE_COMMON_TERMS["he"]
    assert sum(1 for term in common if term in HE_FUNCTION_WORDS) >= 8
