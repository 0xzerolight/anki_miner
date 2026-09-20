"""The Hebrew tokenizer and the POS defaults (spec F.2).

Every line and every expected token comes from ``tests/fixtures/he/tokens.jsonl``: this module
carries no Hebrew character of its own.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from anki_miner.languages.he.pos import (
    HE_ALLOWED_POS,
    HE_EXCLUDED_SUBTYPES,
    HE_POS_LABELS,
    HE_TAG_TO_POS,
    pos_from_tags,
)
from anki_miner.languages.he.tokenizer import TOKEN_RE, HebrewTagger, build_tagger, to_duck_tokens
from anki_miner.services.tagger import LockedTagger

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "he"
ROWS = [json.loads(line) for line in (FIXTURES / "tokens.jsonl").read_text(encoding="utf-8").splitlines()]
BY_ID = {row["id"]: row for row in ROWS}


def _as_rows(tokens):
    return [[t.surface, t.feature.pos1, t.feature.pos2, t.feature.lemma] for t in tokens]


@pytest.mark.parametrize("row", ROWS, ids=[row["id"] for row in ROWS])
def test_every_pinned_line_tokenises_exactly(row):
    assert _as_rows(to_duck_tokens(row["sentence"])) == row["tokens"], row["note"]


@pytest.mark.parametrize("row", ROWS, ids=[row["id"] for row in ROWS])
def test_every_surface_is_a_verbatim_slice_of_the_line(row):
    line = row["sentence"]
    for match in TOKEN_RE.finditer(line):
        assert line[match.start() : match.end()] == match.group()


def test_a_vocalised_line_is_four_tokens_and_not_one_per_character():
    """Hebrew points are Mn, which \\w does not match: the spec's own regex made this 21 tokens."""
    tokens = to_duck_tokens(BY_ID["he02"]["sentence"])
    assert len(tokens) == 4
    assert [t.feature.pos1 for t in tokens] == ["WORD", "WORD", "WORD", "PUNCT"]


def test_a_vocalised_maqaf_compound_is_one_token():
    assert len(to_duck_tokens(BY_ID["he03"]["sentence"])) == 1


def test_an_ascii_hyphen_compound_is_one_token():
    assert len(to_duck_tokens(BY_ID["he04"]["sentence"])) == 1


def test_a_sof_pasuq_is_punctuation_and_not_part_of_the_word_before_it():
    """U+05C3 sits inside U+0591-U+05C7 but it is Po, not Mn."""
    tokens = to_duck_tokens(BY_ID["he09"]["sentence"])
    assert len(tokens) == 4
    assert tokens[-1].feature.pos1 == "PUNCT"
    assert len(tokens[-1].surface) == 1


def test_the_abbreviation_subtype_covers_both_quote_shapes():
    row = BY_ID["he05"]
    tokens = to_duck_tokens(row["sentence"])
    marked = [t.surface for t in tokens if t.feature.pos2 == "abbrev"]
    assert marked == [row["tokens"][0][0], row["tokens"][4][0]]


def test_latin_numbers_and_punctuation_are_told_apart():
    tokens = {t.surface: t.feature.pos1 for t in to_duck_tokens(BY_ID["he06"]["sentence"])}
    assert tokens["Netflix"] == "LATIN"
    assert tokens["2024"] == "NUM"
    assert tokens[","] == "PUNCT"
    assert tokens["!"] == "PUNCT"


def test_the_stopword_list_and_the_particle_list_share_one_subtype():
    tokens = to_duck_tokens(BY_ID["he07"]["sentence"])
    subtypes = {t.surface: t.feature.pos2 for t in tokens}
    assert subtypes[BY_ID["he07"]["tokens"][0][0]] == "stopword"  # a stopwords-iso entry
    assert subtypes[BY_ID["he07"]["tokens"][4][0]] == "stopword"  # a first-party particle


def test_a_pointed_surface_carries_its_folded_provisional_lemma():
    tokens = to_duck_tokens(BY_ID["he02"]["sentence"])
    assert all(t.feature.lemma and t.feature.lemma != t.surface for t in tokens[:3])
    assert all(t.feature.kana == "" for t in tokens)


def test_only_a_word_token_can_carry_a_subtype():
    for row in ROWS:
        for token in to_duck_tokens(row["sentence"]):
            if token.feature.pos1 != "WORD":
                assert token.feature.pos2 == ""


# --------------------------------------------------------------------------
# The tagger entry point
# --------------------------------------------------------------------------


def test_build_tagger_returns_the_lock_guarded_tokenizer():
    tagger = build_tagger()
    assert isinstance(tagger, LockedTagger)
    assert _as_rows(tagger(BY_ID["he01"]["sentence"])) == BY_ID["he01"]["tokens"]


def test_the_tagger_exposes_the_fugashi_parse_alias():
    assert _as_rows(HebrewTagger().parse(BY_ID["he01"]["sentence"])) == BY_ID["he01"]["tokens"]


def test_the_provider_resolves_hebrew_with_no_per_language_code():
    from anki_miner.languages.tagger_provider import evict, get_tagger

    try:
        assert isinstance(get_tagger("he"), LockedTagger)
    finally:
        evict("he")


# --------------------------------------------------------------------------
# POS defaults
# --------------------------------------------------------------------------


def test_the_allowed_pos_is_the_spec_five_and_abbrev_is_not_excluded():
    assert HE_ALLOWED_POS == ("WORD", "NOUN", "VERB", "ADJ", "ADV")
    assert HE_EXCLUDED_SUBTYPES == ("stopword",)
    assert "abbrev" not in HE_EXCLUDED_SUBTYPES


def test_every_allowed_pos_and_subtype_has_a_label():
    for name in (*HE_ALLOWED_POS, *HE_EXCLUDED_SUBTYPES, "PROPN", "FUNC", "NUM", "PUNCT", "LATIN", "abbrev"):
        assert HE_POS_LABELS[name]


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        ("n masc", "NOUN"),
        ("n fem", "NOUN"),
        ("v", "VERB"),
        ("v masc ptcpl sg", "VERB"),
        ("adj", "ADJ"),
        ("adv", "ADV"),
        ("name masc", "PROPN"),
        ("prep", "FUNC"),
        ("pron fem masc", "FUNC"),
        ("r", "FUNC"),
        ("", "WORD"),
        ("non-lemma", "WORD"),
        ("something-unmapped", "WORD"),
    ],
)
def test_the_tag_map_reads_the_first_token_only(tags, expected):
    assert pos_from_tags(tags) == expected


def test_every_mapped_pos_is_a_labelled_one():
    assert set(HE_TAG_TO_POS.values()) <= set(HE_POS_LABELS)
