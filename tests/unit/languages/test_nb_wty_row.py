"""Real wty-nb-en rows import and render under the nb keys, and the en/ei/et hook reads them (must-resolve 2)."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages._spaced.grammar_hook import GrammarTagHook
from anki_miner.languages.nb.tokenizer import build_tagger
from anki_miner.languages.registry import get_profile
from anki_miner.services.dictionary.importers.yomitan_importer import import_yomitan_zip
from anki_miner.services.dictionary.providers.indexed_provider import IndexedDictProvider

FIXTURE = json.loads((Path(__file__).parents[2] / "fixtures" / "nb" / "wty_row.json").read_text(encoding="utf-8"))
RENDERED = FIXTURE["rendered_html"]


@pytest.fixture(scope="module")
def tagger():
    return build_tagger()


@pytest.fixture
def imported(tmp_path):
    archive = tmp_path / "wty-nb-en.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("index.json", json.dumps(FIXTURE["index"]))
        zf.writestr("tag_bank_1.json", json.dumps(FIXTURE["tag_bank"]))
        zf.writestr("term_bank_1.json", json.dumps(FIXTURE["term_rows"]))
    result = import_yomitan_zip(archive, tmp_path / "dicts", dict_id="wty-nb-en", language="nb")
    provider = IndexedDictProvider(
        "wty-nb-en", tmp_path / "dicts" / "wty-nb-en" / "index.sqlite", keys=get_profile("nb").dict_keys
    )
    assert provider.load()
    return result, provider


def hook() -> GrammarTagHook:
    (grammar,) = [h for h in get_profile("nb").render_hooks if isinstance(h, GrammarTagHook)]
    return grammar


def noun(word: str, morph: str = "") -> SimpleNamespace:
    return SimpleNamespace(pos="NOUN", morph=morph, definition_html=RENDERED[word], mined_form=word)


def test_the_fixture_carries_its_licence_and_source():
    assert "CC BY-SA 4.0" in FIXTURE["license"] and FIXTURE["index"]["sourceLanguage"] == "nb"
    assert FIXTURE["index"]["revision"] == "2026.08.29"


def test_every_row_renders_the_committed_html_and_the_nb_stamp_is_no_mismatch(imported):
    result, provider = imported
    for word, html in RENDERED.items():
        assert provider.lookup(word) == html, word
    assert provider.lookup("Bok") == RENDERED["bok"]
    assert result.source_language_mismatch is False  # sourceLanguage nb == profile code (R27: S19 keys on code)


def test_bok_is_f_or_m_in_the_dictionary():
    assert "bok f or m (definite singular boka or boken" in RENDERED["bok"]


@pytest.mark.parametrize(
    ("word", "morph", "expected"),
    [
        # f or m: the token's own gender decides (E.2.2)
        ("bok", "Definite=Def|Gender=Fem|Number=Sing", {"noun_gender": "feminine", "noun_article": "ei"}),
        ("bok", "Definite=Def|Gender=Masc|Number=Sing", {"noun_gender": "masculine", "noun_article": "en"}),
        ("jente", "Definite=Def|Gender=Fem|Number=Sing", {"noun_gender": "feminine", "noun_article": "ei"}),
        # no morph (Card Backfill): two chips, an "f or m" head line and two articles — nothing is printed
        ("bok", "", {}),
        ("hus", "", {"noun_gender": "neuter", "noun_article": "et"}),
        ("hus", "Definite=Def|Gender=Neut|Number=Sing", {"noun_gender": "neuter", "noun_article": "et"}),
        ("gutt", "Definite=Def|Gender=Masc|Number=Sing", {"noun_gender": "masculine", "noun_article": "en"}),
        # a wrong model gender the chips exclude falls through to the dictionary
        ("gutt", "Definite=Def|Gender=Fem|Number=Sing", {"noun_gender": "masculine", "noun_article": "en"}),
        ("eple", "Definite=Ind|Gender=Masc|Number=Plur", {"noun_gender": "neuter", "noun_article": "et"}),
        ("pære", "Definite=Ind|Gender=Neut|Number=Plur", {}),
    ],
)
def test_article_and_gender_from_real_rows(word, morph, expected):
    assert hook().render(noun(word, morph), config=AnkiMinerConfig()) == expected


@pytest.mark.parametrize(
    ("sentence", "surface", "expected"),
    [
        ("Jenta leste boka.", "boka", {"noun_gender": "feminine", "noun_article": "ei"}),
        ("Han leste boken.", "boken", {"noun_gender": "masculine", "noun_article": "en"}),
        # alone, "Vi kjøpte epler." lemmatises epler to epsel (B22); in a list the lemma is eple
        ("Vi kjøpte epler, pærer osv.", "epler", {"noun_gender": "neuter", "noun_article": "et"}),
    ],
)
def test_a_real_token_gets_its_article(tagger, sentence, surface, expected):
    (token,) = [t for t in tagger(sentence) if t.surface == surface]
    lemma = token.feature.lemma
    word = SimpleNamespace(pos=token.feature.pos1, morph=token.morph, definition_html=RENDERED[lemma], mined_form=lemma)
    assert hook().render(word, config=AnkiMinerConfig()) == expected


def test_a_verb_gets_nothing():
    word = SimpleNamespace(pos="VERB", morph="", definition_html=RENDERED["bok"], mined_form="bok")
    assert hook().render(word, config=AnkiMinerConfig()) == {}
