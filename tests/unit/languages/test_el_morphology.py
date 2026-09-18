"""Greek profile data: abbreviations, the S3 table, gender labels, SDH regex, sentence rules, key folding."""

from __future__ import annotations

import pytest

from anki_miner.languages._spaced.keys import CasefoldDictKeys, spaced_dedup_fold
from anki_miner.languages._spaced.sentence import LATIN_TERMINATORS
from anki_miner.languages.el.morphology import (
    EL_ABBREVIATIONS,
    EL_EXCLUDED_SUBTYPES,
    EL_GENDER_LABELS,
    EL_LEADING_WORDS,
    EL_SUBTITLE_REGEX,
    EL_TERMINATORS,
    el_sentence_rules,
)
from anki_miner.services.reading.sentence_splitter import split_sentences
from anki_miner.services.subtitle_parser import compile_subtitle_regex_filter

KEYS = CasefoldDictKeys()


def test_abbreviations_are_spacy_greek_exceptions_minus_sentence_final_words():
    assert len(EL_ABBREVIATIONS) == 195
    assert {"κ.λπ", "π.χ", "κ", "χλμ", "δηλ", "βλ", "κτλ", "σελ"} <= EL_ABBREVIATIONS
    assert not {"αν", "καν", "εε", "εμ", "αλ", "ελ", "λι", "νικ", "πολ", "φιλ", "ασία"} & EL_ABBREVIATIONS
    assert all(key == key.casefold() and not key.endswith(".") for key in EL_ABBREVIATIONS)


def test_pos2_is_dead_config():
    assert EL_EXCLUDED_SUBTYPES == ()


@pytest.mark.parametrize(
    ("front", "mined"),
    [
        ("το βιβλίο", "βιβλίο"),
        ("Το Βιβλίο", "βιβλίο"),
        ("η οδός", "οδός"),
        ("ο δρόμος", "δρόμος"),
        ("οι φίλοι", "φίλοι"),
        ("Ένας φίλος", "φίλος"),
        ("μια γάτα", "γάτα"),
        ("μία γάτα", "γάτα"),
        ("να γράφω", "γράφω"),
        ("το", "το"),
    ],
)
def test_a_deck_front_with_an_article_meets_the_mined_lemma(front, mined):
    fold = spaced_dedup_fold(KEYS, EL_LEADING_WORDS)
    assert fold(front) == fold(mined)
    assert fold(fold(front)) == fold(front)


def test_final_sigma_folds_symmetrically_and_the_tonos_stays_a_key():
    """R34: casefold alone folds ς and Σ to σ; an accent-stripped key exists nowhere."""
    assert KEYS.fold_term("ΟΔΟΣ") == KEYS.fold_term("οδοσ") == "οδοσ"
    assert KEYS.fold_term("οδός") == KEYS.fold_term("ΟΔΌΣ") == KEYS.fold_term("οδ\u1f79σ") == "οδόσ"
    assert KEYS.fold_term("ΟΔΟΣ") != KEYS.fold_term("οδός")
    assert KEYS.fold_term("πότε") != KEYS.fold_term("ποτέ")


def test_gender_prints_the_article():
    assert dict(EL_GENDER_LABELS) == {"masc": "ο", "fem": "η", "neut": "το"}


@pytest.mark.parametrize(
    ("cue", "kept"),
    [
        ("ΓΙΑΝΝΗΣ: Πού είσαι;", "Πού είσαι;"),
        ("ΔΡ. ΠΑΠΑΣ: Καλημέρα.", "Καλημέρα."),
        ("JOHN: Γεια.", "Γεια."),
        ("-Θα μας λείψετε. -Γεια.", "Θα μας λείψετε. Γεια."),
        ("- Τι; - Τίποτα.", "Τι; Τίποτα."),
        ("[ανδρική φωνή] Έλα εδώ.", "Έλα εδώ."),
        ("(γελάει) Ναι.", "Ναι."),
        ("♪ Τραγούδι ♪", "Τραγούδι"),
        ("-5 βαθμοί σήμερα.", "-5 βαθμοί σήμερα."),
        ("Η ελληνο-τουρκική συμφωνία - ίσως.", "Η ελληνο-τουρκική συμφωνία - ίσως."),
        # Judge round 1 BLOCKER: a dash whose left context is a span the filter itself deletes.
        ("ΓΙΑΝΝΗΣ: [χτυπάει η πόρτα] -Η γάτα κοιμάται. -Ναι. ♪", "Η γάτα κοιμάται. Ναι."),
        ("ΓΙΑΝΝΗΣ: -Η γάτα κοιμάται.", "Η γάτα κοιμάται."),
        ("[πόρτα]-Η γάτα.", "Η γάτα."),
        ("♪ -Έλα μαζί μου ♪", "Έλα μαζί μου"),
        ("(γελάει) -Ναι. -Όχι.", "Ναι. Όχι."),
        ("Τι κάνεις\u037e -Καλά.", "Τι κάνεις\u037e Καλά."),
    ],
)
def test_the_greek_sdh_default(cue, kept):
    pattern = compile_subtitle_regex_filter(EL_SUBTITLE_REGEX, "")
    assert " ".join(pattern.sub("", cue).split()) == kept


def test_both_greek_question_marks_terminate():
    rules = el_sentence_rules()
    assert EL_TERMINATORS == LATIN_TERMINATORS | {";", "\u037e"} == rules.terminators
    assert rules.abbreviations == EL_ABBREVIATIONS and rules.space_aware is True
    assert split_sentences("Τι κάνεις; Καλά.", rules=rules) == ["Τι κάνεις;", "Καλά."]
    assert split_sentences("Τι κάνεις\u037e Καλά.", rules=rules) == ["Τι κάνεις\u037e", "Καλά."]
    assert split_sentences("Ήταν ωραία\u00b7 μετά φύγαμε.", rules=rules) == ["Ήταν ωραία\u00b7 μετά φύγαμε."]


def test_abbreviations_hold_the_sentence_and_names_end_it():
    rules = el_sentence_rules()
    assert split_sentences("Πήρα ψωμί, γάλα κ.λπ. από το μαγαζί. Μετά έφυγα.", rules=rules) == [
        "Πήρα ψωμί, γάλα κ.λπ. από το μαγαζί.",
        "Μετά έφυγα.",
    ]
    assert split_sentences("Ο κ. Παπαδόπουλος ήρθε. Ευχαριστώ, Νικ. Ούτε καν. Τέλος.", rules=rules) == [
        "Ο κ. Παπαδόπουλος ήρθε.",
        "Ευχαριστώ, Νικ.",
        "Ούτε καν.",
        "Τέλος.",
    ]
