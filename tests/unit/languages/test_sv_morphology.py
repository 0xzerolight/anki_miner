"""Swedish data for the shared substrate: the abbreviation set, the particle order, normalisation (B.2)."""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest

from anki_miner.languages._spaced.pos import UPOS_ALLOWED
from anki_miner.languages._spaced.script import (
    DIALOGUE_DASH_PATTERN,
    LATIN_SUBTITLE_REGEX,
    NORDIC_DIALOGUE_DASH_PATTERN,
)
from anki_miner.languages.sv.abbreviations import (
    SV_ABBREVIATION_ADDITIONS,
    SV_ABBREVIATION_DROPS,
    SV_ABBREVIATIONS,
)
from anki_miner.languages.sv.morphology import (
    SV_ALLOWED_POS,
    SV_ARTICLE_MAP,
    SV_EXCLUDED_SUBTYPES,
    SV_LEADING_WORDS,
    SV_MODEL_PACKAGE,
    SV_SEPARABLE_VERB_DEPS,
    SV_SUBTITLE_REGEX,
    sv_normalize,
    swedish_particle_candidates,
)


def test_the_abbreviation_set_is_spacys_dotted_exceptions_minus_the_drops():
    from spacy.lang.sv.tokenizer_exceptions import TOKENIZER_EXCEPTIONS

    seeded = {
        key[:-1].casefold() for key in TOKENIZER_EXCEPTIONS if key.endswith(".") and any(c.isalpha() for c in key)
    }
    assert (seeded - SV_ABBREVIATION_DROPS) | SV_ABBREVIATION_ADDITIONS == SV_ABBREVIATIONS
    assert len(SV_ABBREVIATIONS) == 81
    assert {"t.ex", "bl.a", "kl", "kr", "osv", "dvs", "m.m"} <= SV_ABBREVIATIONS
    assert not {"i", "m", "ung", "lat", "min", "max"} & SV_ABBREVIATIONS
    assert all(entry == entry.casefold() and not entry.endswith(".") for entry in SV_ABBREVIATIONS)


def _head(lemma: str, particle: str):
    return SimpleNamespace(feature=SimpleNamespace(lemma=lemma, particle=particle), surface=lemma)


def test_the_data_is_the_spaced_defaults_plus_the_swedish_tables():
    assert SV_MODEL_PACKAGE == "sv_core_news_sm"
    assert SV_ALLOWED_POS == UPOS_ALLOWED
    assert SV_EXCLUDED_SUBTYPES == ("AB|AN", "IN", "NN|AN", "RO|NOM")
    assert "PL" not in SV_EXCLUDED_SUBTYPES  # a stranded particle (upp, ner, in) is ordinary vocabulary
    assert frozenset({"compound:prt"}) == SV_SEPARABLE_VERB_DEPS
    assert SV_ARTICLE_MAP == {"common": "en", "masc": "en", "fem": "en", "neut": "ett"}
    assert frozenset({"en", "ett", "att"}) == SV_LEADING_WORDS


def test_a_particle_verb_joins_with_a_space_and_never_offers_the_concatenation():
    assert swedish_particle_candidates(_head("gå", "ut")) == ["gå ut"]
    assert swedish_particle_candidates(_head("ta", "av")) == ["ta av"]


@pytest.mark.parametrize("particle", ["―", "/", "→", "fransso\u0364ske"])
def test_a_non_word_particle_offers_no_join(particle):
    """Real corpus arcs: bo ―, lägga /, bikini →. Without the guard each becomes a card front."""
    assert swedish_particle_candidates(_head("bo", particle)) == []


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("-Kom hit.", "Kom hit."),
        ("- Kom hit.", "Kom hit."),
        ("Hej. -Kom hit.", "Hej. Kom hit."),
        ("-5 grader ute.", "-5 grader ute."),
        ("Det var -10 igår. -20 i natt.", "Det var -10 igår. -20 i natt."),
        ("Ett e-postmeddelande.", "Ett e-postmeddelande."),
        ("[dörren stängs] Hej.", " Hej."),
    ],
)
def test_the_swedish_subtitle_default_also_strips_an_unspaced_dialogue_dash(line, expected):
    assert re.sub(SV_SUBTITLE_REGEX, "", line) == expected
    assert re.sub(LATIN_SUBTITLE_REGEX, "", "-Kom hit.") == "-Kom hit."  # the shared preset needs the space


def test_the_dash_is_the_shared_nordic_constant_not_an_sv_copy():
    assert SV_SUBTITLE_REGEX.endswith(NORDIC_DIALOGUE_DASH_PATTERN)
    assert LATIN_SUBTITLE_REGEX.replace(DIALOGUE_DASH_PATTERN, NORDIC_DIALOGUE_DASH_PATTERN) == SV_SUBTITLE_REGEX


def test_normalisation_is_nfc_without_nbsp_or_soft_hyphens():
    assert sv_normalize("va\u00adnlig dag") == "vanlig dag"
    assert sv_normalize("o\u0308") == "ö"
