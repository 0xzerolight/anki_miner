"""Romanian data tables: the cedilla fold, normalize, the main-verb pass, the comparison fold, abbreviations, SDH."""

from __future__ import annotations

import unicodedata

import pytest

from anki_miner.languages._spaced.keys import CasefoldDictKeys, spaced_dedup_fold
from anki_miner.languages._spaced.pos import UPOS_ALLOWED
from anki_miner.languages._spaced.script import (
    BRACKETS_PATTERN,
    DIALOGUE_DASH_PATTERN,
    LATIN_SPEAKER_PATTERN,
    MUSIC_PATTERN,
    PARENS_PATTERN,
)
from anki_miner.languages._spaced.sentence import sentence_rules
from anki_miner.languages.ro.morphology import (
    RO_ABBREVIATIONS,
    RO_ALLOWED_POS,
    RO_CEDILLA_FOLD,
    RO_EXCLUDED_SUBTYPES,
    RO_GRAMMAR_SOURCES,
    RO_LEADING_WORDS,
    RO_MODEL_PACKAGE,
    RO_SPEAKER_PATTERN,
    RO_SUBTITLE_REGEX,
    main_verb_pos,
    ro_fold_cedilla,
    ro_normalize,
)
from anki_miner.languages.token import LanguageToken
from anki_miner.services.reading.sentence_splitter import split_sentences
from anki_miner.services.subtitle_parser import compile_subtitle_regex_filter

CEDILLA = "\u015f\u0163\u015e\u0162"  # the legacy cedilla letters
COMMA_BELOW = "șțȘȚ"  # ș ț Ș Ț
KEYS = CasefoldDictKeys(extra_fold=ro_fold_cedilla)
FOLD = spaced_dedup_fold(KEYS, RO_LEADING_WORDS)
STIINTA = "știință"  # comma-below spelling
CEDILLA_STIINTA = "\u015ftiin\u0163ă"  # the cedilla spelling of "știință"


def test_the_pos_gate_model_and_gender_tables():
    assert RO_MODEL_PACKAGE == "ro_core_news_sm"
    assert RO_ALLOWED_POS == UPOS_ALLOWED
    assert RO_EXCLUDED_SUBTYPES == ("Rc", "Ya", "Yn", "Ynfsoy", "Ynfsry", "Ynmsoy", "Ynmsry", "Yr")
    assert RO_GRAMMAR_SOURCES == ("chips", "head", "morph")  # the hook's own labels name all three genders
    assert frozenset({"a", "un", "o", "niște"}) == RO_LEADING_WORDS


def test_the_cedilla_table_maps_one_code_point_to_one():
    assert dict(RO_CEDILLA_FOLD) == dict(zip(CEDILLA, COMMA_BELOW, strict=True))
    # R35: neither NFC nor casefold unifies the pairs, so the map is needed at all
    assert unicodedata.normalize("NFC", CEDILLA) == CEDILLA and CEDILLA.casefold() != COMMA_BELOW.casefold()
    assert ro_fold_cedilla(CEDILLA_STIINTA + " \u015e\u0162") == f"{STIINTA} ȘȚ"


@pytest.mark.parametrize(
    ("text", "normalized"),
    [
        (CEDILLA_STIINTA, STIINTA),
        ("\u015e\u0162", "ȘȚ"),
        ("\u015fi \u0163ara", "și țara"),
        ("s\u0327i", "și"),  # NFD cedilla composes, then folds
        ("și", "și"),  # NFC comma below is already itself
        ("carte\u00a0bună", "carte bună"),
        ("căr\u00adți", "cărți"),  # a soft hyphen splits no word
        ("S-a dus într-un autobuz.", "S-a dus într-un autobuz."),  # hyphens stay
    ],
)
def test_normalize_composes_folds_cedillas_and_cleans_spaces(text, normalized):
    assert ro_normalize(text) == normalized


def test_the_key_fold_is_symmetric_across_both_spellings():
    assert KEYS.fold_term("\u015etiin\u0163ă") == KEYS.fold_term(STIINTA) == STIINTA
    assert KEYS.fold_term("ȘTIINȚĂ") == STIINTA


@pytest.mark.parametrize(
    ("front", "folded"),
    [
        ("o carte", "carte"),
        ("a merge", "merge"),
        ("un scaun.", "scaun"),
        ("niște mere", "mere"),
        ("ni\u015fte mere", "mere"),  # a cedilla deck front
        ("\u015etiin\u0163ă", STIINTA),
        ("a", "a"),
        ("carte", "carte"),
    ],
)
def test_the_fold_meets_the_mined_lemma(front, folded):
    assert FOLD(front) == folded


@pytest.mark.parametrize("text", ["o carte", "a a merge", "ni\u015fte", "  ", "Un  Scaun!", CEDILLA_STIINTA])
def test_the_fold_is_idempotent(text):
    assert FOLD(FOLD(text)) == FOLD(text)


def test_main_verb_tags_leave_the_auxiliary_class():
    tokens = [
        LanguageToken("știu", "AUX", "Vmip1s", "ști"),
        LanguageToken("sunt", "AUX", "Vaip3p", "fi"),
        LanguageToken("citit", "VERB", "Vmp--sm", "citi"),
        LanguageToken("5", "", "Mc-s-d", "5"),
    ]
    assert main_verb_pos(tokens) is tokens
    assert [token.feature.pos1 for token in tokens] == ["VERB", "AUX", "VERB", ""]


def test_abbreviations_come_from_spacy_romanian_plus_titles_minus_real_words():
    from spacy.lang.ro.tokenizer_exceptions import TOKENIZER_EXCEPTIONS

    spacy_keys = {text[:-1].casefold() for text in TOKENIZER_EXCEPTIONS if text.endswith(".") and len(text) > 1}
    assert RO_ABBREVIATIONS - spacy_keys == {"dl", "dna", "dra"}
    unused = {key for key in spacy_keys if len(key) > 1 and not set(key) & set(CEDILLA)} - RO_ABBREVIATIONS
    assert unused == {"ex", "ian", "rom", "._", "°c", "°f", "°k"}
    assert len(RO_ABBREVIATIONS) == 45
    assert {"dr", "nr", "etc", "ș.a.m.d", "s.a.m.d", "prof"} <= RO_ABBREVIATIONS
    assert all(key == key.casefold() and not key.endswith(".") for key in RO_ABBREVIATIONS)


@pytest.mark.parametrize(
    ("text", "sentences"),
    [
        ("Dl. Popescu a venit. Bine.", ["Dl. Popescu a venit.", "Bine."]),
        ("Avem mere, pere ș.a.m.d. în coș. Gata.", ["Avem mere, pere ș.a.m.d. în coș.", "Gata."]),
        ("L-am văzut pe Ian. Apoi am plecat.", ["L-am văzut pe Ian.", "Apoi am plecat."]),
        ("Vreau rom. Și cola.", ["Vreau rom.", "Și cola."]),
    ],
)
def test_the_abbreviations_drive_the_splitter(text, sentences):
    assert split_sentences(text, rules=sentence_rules(RO_ABBREVIATIONS)) == sentences


@pytest.mark.parametrize(
    ("cue", "kept"),
    [
        ("ȘTEFAN: Bună ziua.", "Bună ziua."),
        ("POLIȚISTUL: Stai pe loc!", "Stai pe loc!"),
        ("MĂRIA: Da.", "Da."),
        ("ION: Da.", "Da."),
        ("[ușă trântită] ♪ Salut ♪", "Salut"),
        ("- Bună. - Salut.", "Bună. Salut."),  # Netflix ro: hyphen plus space
        ("E bine – chiar foarte bine.", "E bine – chiar foarte bine."),
        ("Atenție: trenul pleacă!", "Atenție: trenul pleacă!"),
    ],
)
def test_the_romanian_sdh_default_strips_its_fixture(cue, kept):
    compiled = compile_subtitle_regex_filter(RO_SUBTITLE_REGEX, "")
    assert " ".join(compiled.sub("", cue).split()) == kept


def test_the_latin_speaker_rule_misses_romanian_capitals_and_ours_is_the_only_change():
    import re

    assert re.match(LATIN_SPEAKER_PATTERN, "ȘTEFAN: Bună.") is None
    assert "Ș" in RO_SPEAKER_PATTERN and "\u015e" not in RO_SPEAKER_PATTERN
    parts = (BRACKETS_PATTERN, PARENS_PATTERN, MUSIC_PATTERN, RO_SPEAKER_PATTERN, DIALOGUE_DASH_PATTERN)
    assert "|".join(parts) == RO_SUBTITLE_REGEX
