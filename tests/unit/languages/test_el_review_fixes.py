"""Greek review fixes: the enclitic accent fold, the closed-class retag, form-row fronts and X/PROPN recovery.

The engine-free half drives the pieces with duck tokens and rows in the rendered shape the Yomitan
importer stores (the wty-el-en 2026.08.29 rows for those keys, cut to what the rule reads). The
real-engine half runs ``el_core_news_sm``; its tagger is module-scoped because the autouse conftest
fixture clears the tagger cache around every test.
"""

from __future__ import annotations

import pytest

from anki_miner.languages.el.morphology import EL_CLOSED_CLASS
from anki_miner.languages.el.tokenizer import fold_enclitic_accent, retag_greek_tokens
from anki_miner.languages.token import LanguageToken

# --------------------------------------------------------------------------
# EL-02: the enclitic second accent, folded in the tagging copy
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "folded"),
    [
        ("το αυτοκίνητό μου", "το αυτοκίνητο μου"),
        ("Άκουσέ με", "Άκουσε με"),
        ("τα πράγματά σου", "τα πράγματα σου"),
        ("πίστεψέ με", "πίστεψε με"),
        ("νωρίς—αλλά", "νωρίς—αλλά"),
        ("το όνομα μου", "το όνομα μου"),
        ("ΑΥΤΟΚΙΝΗΤΟ", "ΑΥΤΟΚΙΝΗΤΟ"),
        ("προϊόντα", "προϊόντα"),
    ],
)
def test_a_letter_run_keeps_only_its_first_accent(text, folded):
    assert fold_enclitic_accent(text) == folded
    assert len(fold_enclitic_accent(text)) == len(text)


def test_the_tagger_hook_tags_the_folded_copy_and_slices_the_original(el_tagger):
    tokens = {token.surface: token.feature for token in el_tagger("Το αυτοκίνητό μου χάλασε.")}
    assert (tokens["αυτοκίνητό"].pos1, tokens["αυτοκίνητό"].lemma) == ("NOUN", "αυτοκίνητο")
    tokens = {token.surface: token.feature for token in el_tagger("Τα πράγματά σου είναι εδώ.")}
    assert (tokens["πράγματά"].pos1, tokens["πράγματά"].lemma) == ("NOUN", "πράγμα")


def test_a_line_initial_imperative_before_an_enclitic_is_a_verb(el_tagger):
    listen, _me, _dot = el_tagger("Άκουσέ με.")
    assert (listen.surface, listen.feature.pos1) == ("Άκουσέ", "VERB")


def test_the_shared_hook_runs_after_the_character_map():
    import spacy

    from anki_miner.languages._spaced.tokenizer import SpacyTagger

    blank = spacy.blank("el")
    seen: list[str] = []

    def nlp(text: str):
        seen.append(text)
        return blank(text)

    tagger = SpacyTagger(nlp, tag_char_map={"’": "'"}, tag_fold=fold_enclitic_accent)
    tokens = tagger("σ’ το αυτοκίνητό")
    assert seen == ["σ' το αυτοκίνητο"]
    assert [token.surface for token in tokens] == ["σ’", "το", "αυτοκίνητό"]


# --------------------------------------------------------------------------
# EL-04: closed-class words the model tags as content
# --------------------------------------------------------------------------


def tok(surface: str, pos1: str, lemma_: str = "") -> LanguageToken:
    return LanguageToken(surface=surface, pos1=pos1, lemma=lemma_ or surface.lower())


def _retagged(*tokens: LanguageToken) -> list[tuple[str, str]]:
    return [(token.surface, token.feature.pos1) for token in retag_greek_tokens(list(tokens))]


def test_a_closed_class_word_tagged_as_content_is_retagged():
    assert _retagged(tok("σου", "NOUN"), tok("Εσύ", "VERB"), tok("Μην", "ADV"), tok("Ποιος", "ADJ")) == [
        ("σου", "PRON"),
        ("Εσύ", "PRON"),
        ("Μην", "PART"),
        ("Ποιος", "PRON"),
    ]
    assert _retagged(tok("είσαι", "ADV"), tok("Ήμασταν", "PROPN"), tok("που", "ADV")) == [
        ("είσαι", "AUX"),
        ("Ήμασταν", "AUX"),
        ("που", "PRON"),
    ]


def test_the_table_is_accent_sensitive():
    """Interrogative ``πού`` (where) is an adverb; ``κάνεις`` (you do) is a verb, ``κανείς`` (nobody) a pronoun."""
    assert EL_CLOSED_CLASS["που"] == "PRON" and "πού" not in EL_CLOSED_CLASS
    assert EL_CLOSED_CLASS["κανείς"] == "PRON" and "κάνεις" not in EL_CLOSED_CLASS
    assert _retagged(tok("πού", "ADV"), tok("κάνεις", "NOUN")) == [("πού", "ADV"), ("κάνεις", "NOUN")]


def test_a_closed_class_tag_the_model_chose_is_kept():
    """``το`` is an article (DET) or a clitic (PRON); only a content, X or PROPN tag is the model's mistake."""
    assert _retagged(tok("το", "DET"), tok("με", "ADP"), tok("δεν", "PART")) == [
        ("το", "DET"),
        ("με", "ADP"),
        ("δεν", "PART"),
    ]


def test_the_retag_keeps_the_lemma():
    (token,) = retag_greek_tokens([tok("σου", "NOUN", "σου")])
    assert token.feature.lemma == "σου"


@pytest.mark.parametrize(
    ("sentence", "surface", "pos"),
    [
        ("Τα πράγματά σου είναι εδώ.", "σου", "PRON"),
        ("Εσύ τι λες;", "Εσύ", "PRON"),
        ("Μην ανησυχείς.", "Μην", "PART"),
        ("Ποιος είναι;", "Ποιος", "PRON"),
        ("Είσαι καλά;", "Είσαι", "AUX"),
        ("Ήμασταν εκεί.", "Ήμασταν", "AUX"),
    ],
)
def test_the_real_tagger_retags_the_closed_classes(el_tagger, sentence, surface, pos):
    assert {token.surface: token.feature.pos1 for token in el_tagger(sentence)}[surface] == pos


# --------------------------------------------------------------------------
# Real engine
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def el_tagger():
    from anki_miner.languages.el.tokenizer import build_tagger

    return build_tagger()
