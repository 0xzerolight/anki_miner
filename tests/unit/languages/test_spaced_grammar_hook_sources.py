"""GrammarTagHook(sources=...) and the article a gender set shares (nl plan N5; HTML shapes from real wty-nl-en rows)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages._spaced.grammar_hook import GENDER_SOURCES, GrammarTagHook

CONFIG = AnkiMinerConfig()
DE_HET = {"masc": "de", "fem": "de", "common": "de", "neut": "het"}
DER_DIE_DAS = {"masc": "der", "fem": "die", "neut": "das"}
DICTIONARY_FIRST = ("chips", "head", "morph")


def chip(category: str, name: str) -> str:
    return f'<span class="gloss-tag" data-category="{category}" title="{name}">{name}</span>'


def html(chips: str, head: str) -> str:
    return (
        '<div class="yomitan-glossary"><ol data-count="1">'
        f'<li data-dictionary="wty-nl-en" data-dictionary-id="wty-nl-en">{chips}<i>(wty-nl-en)</i>'
        '<ul class="gloss-list" data-count="1"><li class="gloss-item"><div class="gloss-content">'
        '<div class="gloss-sc-div" data-sc-content="preamble"><details class="gloss-sc-details" '
        'data-sc-content="details-entry-Grammar"><summary class="gloss-sc-summary" '
        'data-sc-content="summary-entry">Grammar</summary>'
        f'<div class="gloss-sc-div" data-sc-content="Grammar-content">{head}'
        "</div></details></div></div></li></ul></li></ol></div>"
    )


FEM, MASC, NEUT = chip("gender-feminine", "fem"), chip("gender-masculine", "masc"), chip("gender-neuter", "neut")
KOFFIE = html(FEM + MASC, "koffie f or m (plural koffies, diminutive koffietje n)")
HART = html(FEM + NEUT, "hart n or f (plural harten, diminutive hartje n)")
HOND = html(MASC + NEUT, "hond m (plural honden, diminutive hondje n)")
RAAM = html(FEM + MASC + NEUT, "raam n or f or m (plural ramen, diminutive raampje n)")


def word(definition_html: str = "", morph: str = "", mined_form: str = "") -> SimpleNamespace:
    return SimpleNamespace(pos="NOUN", morph=morph, definition_html=definition_html, mined_form=mined_form)


def render(w, **kwargs):
    return GrammarTagHook(("noun_gender", "noun_article"), **kwargs).render(w, config=CONFIG)


def test_the_default_order_is_the_approved_one():
    assert GENDER_SOURCES == ("morph", "chips", "head")
    hondje = word(HOND, "Gender=Neut|Number=Sing")  # lemma hond; neut is among the chips, so morph may lead
    assert render(hondje, article_map=DE_HET) == {"noun_gender": "neuter", "noun_article": "het"}


def test_dictionary_first_reads_the_head_line_before_the_token():
    hondje = word(HOND, "Gender=Neut|Number=Sing")
    assert render(hondje, article_map=DE_HET, sources=DICTIONARY_FIRST) == {
        "noun_gender": "masculine",
        "noun_article": "de",
    }


def test_morph_still_answers_last_and_still_yields_to_excluding_chips():
    assert render(word(HART, "Gender=Neut|Number=Sing"), article_map=DE_HET, sources=DICTIONARY_FIRST) == {
        "noun_gender": "neuter",
        "noun_article": "het",
    }
    assert render(word("", "Gender=Com|Number=Sing"), article_map=DE_HET, sources=DICTIONARY_FIRST) == {
        "noun_gender": "common",
        "noun_article": "de",
    }


def test_one_article_for_every_gender_of_a_source():
    assert render(word(KOFFIE), article_map=DE_HET) == {"noun_article": "de"}
    # Com is not among koffie's chips, so morph yields; the chips still share one article
    assert render(word(KOFFIE, "Gender=Com|Number=Sing"), article_map=DE_HET, sources=DICTIONARY_FIRST) == {
        "noun_article": "de"
    }
    assert render(word("", "Gender=Com,Neut|Number=Sing"), article_map=DE_HET) == {}
    assert render(word(HART), article_map=DE_HET, sources=DICTIONARY_FIRST) == {}


def test_the_first_gender_bearing_source_decides_and_every_or_letter_counts():
    """IMPLEMENT 007: raam's chips and its three-letter head line both disagree, so no article at all."""
    for morph in ("Number=Plur", ""):
        assert render(word(RAAM, morph), article_map=DE_HET) == {}
        assert render(word(RAAM, morph), article_map=DE_HET, sources=DICTIONARY_FIRST) == {}
    assert render(word(html("", "raam n or f or m (plural ramen)")), article_map=DE_HET) == {}
    assert render(word(html("", "koffie f or m (plural koffies)")), article_map=DE_HET) == {"noun_article": "de"}


def test_an_injective_map_never_shares_an_article():
    fuchs = html(FEM + MASC, "Fuchs m or f (proper noun, surname)")
    assert render(word(fuchs), article_map=DER_DIE_DAS) == {}


def test_an_article_rule_is_never_bypassed_by_the_shared_article():
    assert render(word(KOFFIE, mined_form="koffie"), article_rule=lambda gender, headword: "de") == {}


def test_sources_are_validated():
    with pytest.raises(ValueError, match="sources"):
        GrammarTagHook(("noun_gender",), sources=("morph", "tags"))
    with pytest.raises(ValueError, match="sources"):
        GrammarTagHook(("noun_gender",), sources=("morph", "morph"))
