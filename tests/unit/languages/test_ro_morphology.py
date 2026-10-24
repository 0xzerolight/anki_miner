"""Romanian data tables: the cedilla fold, normalize, the tagger passes, the comparison fold, abbreviations, SDH."""

from __future__ import annotations

import unicodedata

import pytest

from anki_miner.languages._spaced.keys import CasefoldDictKeys, spaced_dedup_fold
from anki_miner.languages._spaced.pos import UPOS_ALLOWED
from anki_miner.languages._spaced.script import (
    BRACKETS_PATTERN,
    LATIN_SPEAKER_PATTERN,
    MUSIC_PATTERN,
    NORDIC_DIALOGUE_DASH_PATTERN,
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
    clitic_piece_pos,
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
    assert RO_GRAMMAR_SOURCES == ("chips", "head")  # UD RRT has no neuter: the model's gender never decides
    assert frozenset({"a", "un", "o", "niște", "se", "a-și", "a-i"}) == RO_LEADING_WORDS


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
        # letters Romanian never writes: cp1250 / ISO-8859-16 text read as cp1252, and OCR's ă
        ("s\u00e3 nu", "să nu"),
        ("\u00bai", "și"),
        ("a\u00fei", "ați"),
        ("\u00aaTEFAN", "ȘTEFAN"),
        ("\u00deARA", "ȚARA"),
        ("dac\u00e5", "dacă"),
        ("\u00c3", "Ă"),
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
        ("a se bucura", "bucura"),  # a reflexive citation meets the mined verb
        ("A se bucura", "bucura"),
        ("a-și aminti", "aminti"),
        ("a-\u015fi aminti", "aminti"),  # the cedilla a-și folds through the key fold
        ("a-i păsa", "păsa"),
        ("se", "se"),
        ("a se", "se"),
        ("a", "a"),
        ("carte", "carte"),
    ],
)
def test_the_fold_meets_the_mined_lemma(front, folded):
    assert FOLD(front) == folded


@pytest.mark.parametrize(
    "text", ["o carte", "a a merge", "ni\u015fte", "  ", "Un  Scaun!", CEDILLA_STIINTA, "a se bucura"]
)
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


def test_lone_letters_and_hyphen_pieces_leave_the_content_classes():
    tokens = [
        LanguageToken("s", "NOUN", "Ncms-n", "s"),  # caption ``s a`` for ``s-a``
        LanguageToken("e", "VERB", "Vmip3s", "fi"),  # the one-letter copula stays a verb
        LanguageToken("Ce-", "NOUN", "Ncmpry", "ce"),
        LanguageToken("-te", "NOUN", "Ncms-n", "tu"),
        LanguageToken("cărți", "NOUN", "Ncfp-n", "carte"),
        LanguageToken("-5", "NUM", "Mc-s-d", "-5"),
    ]
    assert clitic_piece_pos(tokens) is tokens
    assert [token.feature.pos1 for token in tokens] == ["X", "VERB", "PRON", "PRON", "NOUN", "NUM"]


def test_the_bare_i_of_ce_i_is_the_copula():
    """``Ce-i asta?``: the host keeps the hyphen (``Ce-``), and the model tags ``i`` a verb lemmatised ``i``."""
    token = LanguageToken("i", "VERB", "Vmip3s----y", "i")
    clitic_piece_pos([token])
    assert (token.feature.pos1, token.feature.lemma) == ("VERB", "fi")


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
        ("-Unde ești? -Aici.", "Unde ești? Aici."),  # unspaced turns
        ("-5 grade afară.", "-5 grade afară."),  # a negative number is no turn
        ("E bine – chiar foarte bine.", "E bine – chiar foarte bine."),
        ("Atenție: trenul pleacă!", "Atenție: trenul pleacă!"),
    ],
)
def test_the_romanian_sdh_default_strips_its_fixture(cue, kept):
    compiled = compile_subtitle_regex_filter(RO_SUBTITLE_REGEX, "")
    assert " ".join(compiled.sub("", cue).split()) == kept


def test_the_romanian_default_is_the_latin_one_with_our_speaker_rule_and_the_unspaced_dash():
    """The Latin speaker rule misses Romanian capitals, and Romanian writes unspaced turns (the pl/lt shape)."""
    import re

    assert re.match(LATIN_SPEAKER_PATTERN, "ȘTEFAN: Bună.") is None
    assert "Ș" in RO_SPEAKER_PATTERN and "\u015e" not in RO_SPEAKER_PATTERN
    parts = (BRACKETS_PATTERN, PARENS_PATTERN, MUSIC_PATTERN, RO_SPEAKER_PATTERN, NORDIC_DIALOGUE_DASH_PATTERN)
    assert "|".join(parts) == RO_SUBTITLE_REGEX
