"""Polish data tables, token repairs, sentence rules and the comparison fold (no model)."""

from __future__ import annotations

import re

import pytest

from anki_miner.languages._spaced.pos import UPOS_ALLOWED
from anki_miner.languages._spaced.script import LATIN_SUBTITLE_REGEX
from anki_miner.languages._spaced.sentence import sentence_rules
from anki_miner.languages.pl.morphology import (
    PL_ABBREVIATIONS,
    PL_ALLOWED_POS,
    PL_ANIMACY_LABELS,
    PL_EXCLUDED_SUBTYPES,
    PL_GENDER_LABELS,
    PL_MODEL_PACKAGE,
    PL_POST_PASSES,
    PL_SENTENCE_RULES,
    PL_SPEAKER_PATTERN,
    PL_SUBTITLE_REGEX,
    PL_VERB_POS,
    abbreviation_cases,
    drop_agglutinate_tail,
    drop_plurale_tantum_gender,
    pl_dedup_fold,
)
from anki_miner.languages.token import LanguageToken
from anki_miner.services.reading.sentence_splitter import split_sentences


def _token(surface: str, pos1: str = "VERB", lemma: str = "", morph: str = "") -> LanguageToken:
    return LanguageToken(surface=surface, pos1=pos1, pos2="", lemma=lemma, morph=morph)


def test_the_tables():
    assert PL_MODEL_PACKAGE == "pl_core_news_sm" and PL_ALLOWED_POS == UPOS_ALLOWED
    assert PL_EXCLUDED_SUBTYPES == (
        "ADJA", "AGLT", "BURK", "COMP", "CONJ", "INTERJ", "INTERP", "NUMCOL", "PPRON12", "PPRON3", "PREP", "SIEBIE", "XXX",
    )  # fmt: skip
    # kept on the P4 evidence: real adverbs, quantifiers, predicatives and mislabelled real words
    assert not {"QUB", "NUM", "PRED", "BREV", "_SP", "WINIEN", "BEDZIE", "GER", "IMPS", "PCON", "PANT"} & set(
        PL_EXCLUDED_SUBTYPES
    )
    assert dict(PL_GENDER_LABELS) == {"masc": "m", "fem": "f", "neut": "n"}
    assert dict(PL_ANIMACY_LABELS) == {"pers": "m pers", "anim": "m anim", "inan": "m inan"}
    # pl's AUX is być/by and the agglutinate clitics - never an aspect-bearing headword (P7).
    assert frozenset({"VERB"}) == PL_VERB_POS
    assert list(PL_POST_PASSES) == [drop_agglutinate_tail, drop_plurale_tantum_gender]


def test_the_abbreviation_set_follows_the_pwn_rules():
    assert {"godz", "ul", "prof", "itd", "itp", "m.in", "p.o", "dr", "np", "tzw", "tzn", "str"} <= PL_ABBREVIATIONS
    assert not {"ok", "im", "min"} & PL_ABBREVIATIONS  # ordinary words that end sentences (OK., Powiedz im.)
    assert all(key == key.casefold() and not key.endswith(".") for key in PL_ABBREVIATIONS)


def test_abbreviation_cases_spell_each_key_with_its_dot():
    assert abbreviation_cases(frozenset({"godz", "m.in", "żeń"})) == sorted(
        {"godz.", "Godz.", "m.in.", "M.in.", "żeń.", "Żeń."}
    )


@pytest.mark.parametrize(
    ("lemma", "expected"),
    [
        ("widzieć być", "widzieć"),  # widziałem
        ("móc by być", "móc"),  # mógłbyś
        ("być by", "być"),  # byłoby
        ("pisać", "pisać"),
        ("na przykład", "na przykład"),  # np: an X abbreviation, not an agglutinate
        ("i tak dalej", "i tak dalej"),
    ],
)
def test_the_agglutinate_tail_leaves_the_verb(lemma, expected):
    (token,) = drop_agglutinate_tail([_token("x", lemma=lemma)])
    assert token.feature.lemma == expected


def test_a_plurale_tantum_loses_its_conventional_gender_and_nothing_else_changes():
    drzwi = _token("Drzwi", "NOUN", "drzwi", "Case=Nom|Gender=Neut|Number=Ptan")
    stole = _token("stole", "NOUN", "stół", "Animacy=Inan|Case=Loc|Gender=Masc|Number=Sing")
    dzieci = _token("Dzieci", "NOUN", "dziecko", "Case=Nom|Gender=Neut|NumType=Sets|Number=Plur")
    conj = _token("i", "CCONJ", "i")
    assert [token.morph for token in drop_plurale_tantum_gender([drzwi, stole, dzieci, conj])] == [
        "Case=Nom|Number=Ptan",
        "Animacy=Inan|Case=Loc|Gender=Masc|Number=Sing",
        "Case=Nom|Gender=Neut|NumType=Sets|Number=Plur",
        "",
    ]


@pytest.mark.parametrize(
    ("text", "sentences"),
    [
        ("Spotkanie o godz. 18 w pok. 5. Potem kino.", ["Spotkanie o godz. 18 w pok. 5.", "Potem kino."]),
        ("Urodził się w 1990 r. w Krakowie.", ["Urodził się w 1990 r. w Krakowie."]),
        ("To jest m.in. mój brat.", ["To jest m.in. mój brat."]),
        ("Powiedz im. Oni wiedzą.", ["Powiedz im.", "Oni wiedzą."]),
        ("OK. Zrobię to.", ["OK.", "Zrobię to."]),
        ("„Tak. Nie.” Poszedł.", ["„Tak. Nie.” Poszedł."]),  # „ opens a quotation (P13)
        ("Czekaj... już idę.", ["Czekaj... już idę."]),
    ],
)
def test_polish_sentence_rules(text, sentences):
    assert split_sentences(text, rules=PL_SENTENCE_RULES) == sentences


def test_the_rules_keep_the_shared_latin_shape():
    assert PL_SENTENCE_RULES.abbreviations == PL_ABBREVIATIONS
    assert sentence_rules(PL_ABBREVIATIONS).openers < PL_SENTENCE_RULES.openers  # additive (P13)
    assert "„" in PL_SENTENCE_RULES.openers and "”" in PL_SENTENCE_RULES.closers
    assert PL_SENTENCE_RULES.space_aware is True


@pytest.mark.parametrize(
    ("cue", "cleaned"),
    [
        ("ŁUKASZ: Chodź tutaj.", "Chodź tutaj."),  # the shared preset leaves this label in the cue
        ("MAŁGORZATA: Nie.", "Nie."),
        ("ŚWIADEK: Widziałem go.", "Widziałem go."),
        ("MAREK: Tak.", "Tak."),  # the shared rule already covers an ASCII label
        ("[dzwonek] Halo?", " Halo?"),  # the bracket rule leaves the gap; the caller flattens whitespace
        ("- Co robisz? - Nic.", "Co robisz? Nic."),
        ("Łódź płynie.", "Łódź płynie."),  # a sentence-initial capital is not a label
    ],
)
def test_the_polish_sdh_preset_strips_a_polish_speaker_label(cue, cleaned):
    assert re.sub(PL_SUBTITLE_REGEX, "", cue) == cleaned


def test_the_shared_preset_cannot_strip_a_polish_label():
    """Why pl carries its own preset (R11): the shared capital class has no Ą Ć Ę Ł Ń Ś Ź Ż."""
    assert re.sub(LATIN_SUBTITLE_REGEX, "", "ŁUKASZ: Chodź tutaj.") == "ŁUKASZ: Chodź tutaj."
    assert re.sub(LATIN_SUBTITLE_REGEX, "", "MAREK: Tak.") == "Tak."
    assert PL_SPEAKER_PATTERN in PL_SUBTITLE_REGEX and LATIN_SUBTITLE_REGEX != PL_SUBTITLE_REGEX
    re.compile(PL_SUBTITLE_REGEX)  # no inline flags: a preset that cannot compile disables the filter


@pytest.mark.parametrize(
    ("front", "key"),
    [
        ("bać się", "bać"),
        ("Bać się!", "bać"),
        ("uczyć się", "uczyć"),
        ("Książka", "książka"),
        ("się", "się"),  # nothing else remains: the word itself stays
        ("wziąć się w garść", "wziąć się w garść"),  # not trailing
    ],
)
def test_a_reflexive_deck_front_meets_the_mined_verb(front, key):
    assert pl_dedup_fold(front) == key
    assert pl_dedup_fold(key) == key  # idempotent: folded keys are stored and folded again
