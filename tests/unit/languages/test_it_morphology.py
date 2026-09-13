"""Italian data for the shared substrate: no engine, except spaCy's own tokenizer-exception table."""

from __future__ import annotations

import pytest

from anki_miner.languages._spaced.keys import CasefoldDictKeys, spaced_dedup_fold
from anki_miner.languages._spaced.morphology import EncliticRung, LatinLookupStrategy
from anki_miner.languages._spaced.pos import UPOS_ALLOWED
from anki_miner.languages.it.morphology import (
    IT_ABBREVIATIONS,
    IT_ALLOWED_POS,
    IT_ENCLITIC_CLUSTERS,
    IT_EXCLUDED_SUBTYPES,
    IT_LEADING_WORDS,
    it_relemmatize,
    italian_article,
    keep_lemma_head,
)
from anki_miner.languages.token import LanguageToken

#: A.1's titles and the "p. es." halves: not spaCy it tokenizer exceptions (D5).
ADDITIONS = frozenset({"sig", "sig.ra", "sig.na", "sigg", "dott", "dott.ssa", "prof.ssa", "ing", "es", "p", "p.es"})
#: Words that end Italian sentences; none may be a non-terminator.
NEVER = frozenset({"a", "e", "i", "o", "ho", "è", "no", "sì", "va", "sta", "fa"})


def test_the_pos_gate():
    assert IT_ALLOWED_POS == UPOS_ALLOWED
    assert IT_EXCLUDED_SUBTYPES == ("BN",)


def test_abbreviations_come_from_spacy_plus_the_titles():
    from spacy.lang.it.tokenizer_exceptions import TOKENIZER_EXCEPTIONS

    dotted = {text[:-1].casefold() for text in TOKENIZER_EXCEPTIONS if text.endswith(".") and len(text) > 2}
    internal = {
        text.casefold()
        for text in TOKENIZER_EXCEPTIONS
        if text[:1].isalpha() and "." in text[1:-1] and not text.endswith(".")
    }
    spacy_keys = {key for key in dotted | internal if key[:1].isalpha()}
    assert IT_ABBREVIATIONS - spacy_keys <= ADDITIONS
    assert {"ecc", "prof", "sig", "sig.ra", "dott", "dott.ssa", "es", "s.p.a", "s.r.l", "al", "col"} <= IT_ABBREVIATIONS
    assert not IT_ABBREVIATIONS & NEVER
    assert all(key == key.casefold() and not key.endswith(".") for key in IT_ABBREVIATIONS)


def test_a_known_word_front_with_an_article_meets_the_mined_lemma():
    fold = spaced_dedup_fold(CasefoldDictKeys(), IT_LEADING_WORDS)
    assert (
        frozenset({"il", "lo", "la", "i", "gli", "le", "un", "uno", "una", "l'", "l’", "un'", "un’"})
        == IT_LEADING_WORDS
    )
    assert fold("il gatto") == fold("gatto") == "gatto"
    assert fold("l'acqua") == fold("L’Acqua") == fold("acqua") == "acqua"
    assert fold("un'amica") == "amica"
    assert fold("lo") == "lo"  # nothing would remain


@pytest.mark.parametrize(
    "text", ["l'acqua", "L’uomo", "il gatto.", "l'l'acqua", "un' ", "l'", "gli amici", "Dell'Arte"]
)
def test_the_fold_is_idempotent(text):
    fold = spaced_dedup_fold(CasefoldDictKeys(), IT_LEADING_WORDS)
    assert fold(fold(text)) == fold(text)


def test_the_enclitic_table():
    assert len(IT_ENCLITIC_CLUSTERS) == len(set(IT_ENCLITIC_CLUSTERS)) == 41
    assert {"mi", "si", "gli", "ne", "melo", "telo", "cene", "glielo", "gliene"} <= set(IT_ENCLITIC_CLUSTERS)


@pytest.mark.parametrize(
    ("stem", "expected"),
    [
        ("lavar", ["lavare", "lavarre"]),
        ("por", ["pore", "porre"]),
        ("dam", ["dare"]),
        ("dim", ["dire"]),
        ("fam", ["fare"]),
        ("stam", ["stare"]),
        ("vat", ["andare"]),
        ("dir", ["dire", "dirre"]),
        ("guardando", []),
        ("porta", []),
    ],
)
def test_the_relemmatizer(stem, expected):
    assert it_relemmatize(stem) == expected


def test_the_italian_ladder_strips_the_surface():
    ladder = LatinLookupStrategy(extra_rungs=(EncliticRung(IT_ENCLITIC_CLUSTERS, relemmatize=it_relemmatize),))
    # (front, surface): the mining path hands the ladder the token surface (contract items 1-4)
    assert ladder.candidates("fammare", "fammi", None) == [("fammi", 0), ("fam", 0), ("fare", 0)]
    assert ladder.candidates("lavare", "lavarsi", None) == [("lavarsi", 0), ("lavar", 0), ("lavarre", 0)]
    assert ladder.candidates("dire", "Dimmelo", None) == [("Dimmelo", 0), ("dimmelo", 0), ("dim", 0), ("dimme", 0)]
    assert ladder.candidates("dammelo", "dammelo", None) == [("dam", 0), ("dare", 0), ("damme", 0)]
    assert ladder.candidates("nord-est", "nord-est", None) == [("nord", 0), ("est", 0)]
    assert ladder.candidates("mangiato", "", None) == []  # the contract probe word


@pytest.mark.parametrize(
    ("gender", "headword", "article"),
    [
        ("masc", "gatto", "il"),
        ("masc", "whisky", "il"),
        ("masc", "nord-est", "il"),
        ("masc", "zio", "lo"),
        ("masc", "studente", "lo"),
        ("masc", "sport", "lo"),
        ("masc", "psicologo", "lo"),
        ("masc", "pneumatico", "lo"),
        ("masc", "gnomo", "lo"),
        ("masc", "xilofono", "lo"),
        ("masc", "yogurt", "lo"),
        ("masc", "iato", "lo"),
        ("masc", "tsunami", "lo"),
        ("masc", "albero", "l'"),
        ("masc", "uomo", "l'"),
        ("masc", "hotel", "l'"),
        ("fem", "sedia", "la"),
        ("fem", "zia", "la"),
        ("fem", "iena", "la"),
        ("fem", "acqua", "l'"),
        ("fem", "Isola", "l'"),
        ("neut", "gatto", ""),
        ("masc", "", ""),
    ],
)
def test_the_article_rule(gender, headword, article):
    assert italian_article(gender, headword) == article


def test_the_lemma_head_pass_keeps_the_first_word_of_a_multi_word_lemma():
    tokens = [
        LanguageToken("lavarsi", "VERB", "V_PC", "lavare si"),
        LanguageToken("andarmene", "VERB", "V_PC_PC", "andare me ne"),
        LanguageToken("mani", "NOUN", "S", "mano"),
        LanguageToken("ciao", "INTJ", "I", " "),
    ]
    assert keep_lemma_head(tokens) is tokens
    assert [token.feature.lemma for token in tokens] == ["lavare", "andare", "mano", " "]
