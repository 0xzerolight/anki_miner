"""Greek review fixes: the enclitic accent fold, the closed-class retag, form-row fronts and X/PROPN recovery.

The engine-free half drives the pieces with duck tokens and rows in the rendered shape the Yomitan
importer stores (the wty-el-en 2026.08.29 rows for those keys, cut to what the rule reads). The
real-engine half runs ``el_core_news_sm``; its tagger is module-scoped because the autouse conftest
fixture clears the tagger cache around every test.
"""

from __future__ import annotations

import pytest

from anki_miner.languages.el.tokenizer import fold_enclitic_accent

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
# Real engine
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def el_tagger():
    from anki_miner.languages.el.tokenizer import build_tagger

    return build_tagger()
