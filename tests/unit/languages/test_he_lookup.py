"""The Hebrew proclitic ladder and the function-word tier (spec F.2).

Every Hebrew literal comes from ``tests/fixtures/he/``; this module carries none of its own.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from anki_miner.languages.he.proclitics import (
    HE_SINGLES,
    HE_STACKS,
    MAX_CANDIDATES,
    MIN_STEM,
    HebrewLookupStrategy,
    rungs,
)
from anki_miner.languages.he.script import GERESH, GERSHAYIM, MAQAF, he_fold
from anki_miner.languages.he.stopwords import HE_FUNCTION_WORDS, HE_PARTICLES, HE_STOPWORDS

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "he"
TOKENS = {
    row["id"]: row for row in (json.loads(line) for line in (FIXTURES / "tokens.jsonl").read_text("utf-8").splitlines())
}


def _surface(line_id: str, index: int) -> str:
    return TOKENS[line_id]["tokens"][index][0]


# --------------------------------------------------------------------------
# The rung list
# --------------------------------------------------------------------------


def test_a_word_with_no_proclitic_and_no_punctuation_has_no_rungs():
    """kara, sefer: nothing to strip, so the ladder is empty and the lookup is the word itself."""
    assert rungs(_surface("he01", 1)) == []
    assert rungs(_surface("he01", 2)) == []


def test_a_single_proclitic_is_stripped_once():
    """b-sefer -> sefer, and nothing further."""
    b_sefer = _surface("he08", 0)
    assert rungs(b_sefer) == [b_sefer[1:]]


def test_the_stack_comes_before_the_single_and_both_are_offered():
    """ve-ha-yeladim yields the stack strip first, then the one-letter strip."""
    word = he_fold("\N{HEBREW LETTER VAV}\N{HEBREW LETTER HE}") + _surface("he08", 4)
    assert rungs(word)[0] == word[2:]
    assert rungs(word)[1] == word[1:]


def test_the_ascii_quote_rung_reaches_the_gershayim_key():
    abbreviation = _surface("he05", 0)  # typed with an ASCII double quote
    assert '"' in abbreviation
    assert rungs(abbreviation)[0] == abbreviation.replace('"', GERSHAYIM)


def test_the_ascii_apostrophe_rung_reaches_the_geresh_key():
    abbreviation = _surface("he05", 4)  # trailing ASCII apostrophe
    assert "'" in abbreviation
    assert rungs(abbreviation)[0] == abbreviation.replace("'", GERESH)


def test_the_hyphen_rung_offers_both_the_maqaf_and_the_space_spelling():
    compound = _surface("he04", 0)
    assert rungs(compound)[:2] == [compound.replace("-", MAQAF), compound.replace("-", " ")]


def test_a_construct_surface_offers_its_maqaf_stripped_form():
    compound = _surface("he03", 0)
    folded = he_fold(compound)
    assert MAQAF in folded
    assert folded.replace(MAQAF, "") in rungs(folded)


def test_no_rung_leaves_fewer_than_two_characters():
    for stack in HE_STACKS + HE_SINGLES:
        short = stack + _surface("he01", 1)[:1]
        assert all(len(rung) >= MIN_STEM for rung in rungs(short))


def test_the_ladder_is_capped_and_deduplicated():
    word = "".join(HE_SINGLES) + _surface("he01", 2)
    found = rungs(word)
    assert len(found) <= MAX_CANDIDATES
    assert len(found) == len(set(found))


# --------------------------------------------------------------------------
# The LookupStrategy contract
# --------------------------------------------------------------------------


def test_the_strategy_never_offers_the_word_itself_and_never_repeats():
    """The two halves of tests/unit/languages/test_language_contract.py's lookup case."""
    for row in TOKENS.values():
        for surface, *_ in row["tokens"]:
            found = HebrewLookupStrategy().candidates(surface, "", None)
            texts = [text for text, _ in found]
            assert surface not in texts
            assert len(texts) == len(set(texts))
            assert all(conditions == 0 for _, conditions in found)


def test_a_pointed_surface_offers_its_folded_spelling_first():
    pointed = _surface("he02", 0)
    found = HebrewLookupStrategy().candidates(pointed, "", None)
    assert found[0] == (he_fold(pointed), 0)


def test_a_japanese_probe_word_yields_nothing():
    assert HebrewLookupStrategy().candidates("食べた", "", None) == []


# --------------------------------------------------------------------------
# The function-word tier
# --------------------------------------------------------------------------


def test_the_stopword_list_is_the_upstream_one_unchanged():
    assert len(HE_STOPWORDS) == 194
    assert HE_FUNCTION_WORDS == HE_STOPWORDS | HE_PARTICLES
    assert HE_STOPWORDS.isdisjoint(HE_PARTICLES)


def test_every_entry_is_plain_hebrew_letters():
    """No point, no maqaf, no format character — which is why this ships as a .py frozenset."""
    from anki_miner.languages.he.script import is_he_letter

    for word in HE_FUNCTION_WORDS:
        assert word and all(is_he_letter(char) for char in word), word
        assert he_fold(word) == word


@pytest.mark.parametrize("line_id,index", [("he07", 0), ("he07", 2), ("he08", 3)])
def test_the_fixture_stopwords_are_in_the_list(line_id, index):
    assert he_fold(_surface(line_id, index)) in HE_FUNCTION_WORDS


def test_the_fixture_particle_is_in_the_list():
    assert he_fold(_surface("he07", 4)) in HE_PARTICLES


def test_the_content_words_the_frequency_list_puts_in_the_top_fifty_stay_mineable():
    """tov, rotze, yodea are he_50k top-50 forms that stopwords-iso does NOT carry."""
    tov = "\N{HEBREW LETTER TET}\N{HEBREW LETTER VAV}\N{HEBREW LETTER BET}"
    rotze = "\N{HEBREW LETTER RESH}\N{HEBREW LETTER VAV}\N{HEBREW LETTER TSADI}\N{HEBREW LETTER HE}"
    yodea = "\N{HEBREW LETTER YOD}\N{HEBREW LETTER VAV}" "\N{HEBREW LETTER DALET}\N{HEBREW LETTER AYIN}"
    assert {tov, rotze, yodea}.isdisjoint(HE_FUNCTION_WORDS)
