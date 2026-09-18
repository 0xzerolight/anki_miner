"""Real wty-sv-en rows import and render under the Swedish keys, and the en/ett hook reads them (N5)."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages._spaced.grammar_hook import GrammarTagHook
from anki_miner.languages.registry import get_profile
from anki_miner.languages.sv.tokenizer import build_tagger
from anki_miner.services.dictionary.importers.yomitan_importer import import_yomitan_zip
from anki_miner.services.dictionary.providers.indexed_provider import IndexedDictProvider

FIXTURE = json.loads((Path(__file__).parents[2] / "fixtures" / "sv" / "wty_row.json").read_text(encoding="utf-8"))
RENDERED = FIXTURE["rendered_html"]


@pytest.fixture
def provider(tmp_path):
    archive = tmp_path / "wty-sv-en.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("index.json", json.dumps(FIXTURE["index"]))
        zf.writestr("tag_bank_1.json", json.dumps(FIXTURE["tag_bank"]))
        zf.writestr("term_bank_1.json", json.dumps(FIXTURE["term_rows"]))
    import_yomitan_zip(archive, tmp_path / "dicts", dict_id="wty-sv-en", language="sv")
    provider = IndexedDictProvider(
        "wty-sv-en", tmp_path / "dicts" / "wty-sv-en" / "index.sqlite", keys=get_profile("sv").dict_keys
    )
    assert provider.load()
    return provider


def hook() -> GrammarTagHook:
    (grammar,) = [h for h in get_profile("sv").render_hooks if isinstance(h, GrammarTagHook)]
    return grammar


def noun(word: str, morph: str = "") -> SimpleNamespace:
    return SimpleNamespace(pos="NOUN", morph=morph, definition_html=RENDERED[word], mined_form=word)


def test_the_fixture_carries_its_licence_and_source():
    assert "CC BY-SA 4.0" in FIXTURE["license"] and FIXTURE["index"]["sourceLanguage"] == "sv"
    assert FIXTURE["index"]["revision"] == "2026.08.29"


def test_every_row_renders_the_committed_html_and_a_capital_finds_it(provider):
    for word, html in RENDERED.items():
        assert provider.lookup(word) == html, word
    assert provider.lookup("Huset".lower()) == RENDERED["hus"] or provider.lookup("Hus") == RENDERED["hus"]


@pytest.mark.parametrize(
    ("word", "morph", "expected"),
    [
        # the model's own morph for each word, from a real sentence
        ("hus", "Case=Nom|Definite=Ind|Gender=Neut|Number=Sing", {"noun_article": "ett"}),
        ("bok", "Case=Nom|Definite=Ind|Gender=Com|Number=Sing", {"noun_article": "en"}),
        ("katt", "Case=Nom|Definite=Ind|Gender=Com|Number=Sing", {"noun_article": "en"}),
        ("bord", "Case=Nom|Definite=Ind|Gender=Neut|Number=Sing", {"noun_article": "ett"}),
        # a Grammar head line fills the plural too, and answers without any morph
        ("ord", "Case=Nom|Definite=Ind|Gender=Neut|Number=Sing", {"noun_article": "ett", "noun_plural": "ord"}),
        ("apa", "Case=Nom|Definite=Ind|Gender=Com|Number=Sing", {"noun_article": "en", "noun_plural": "apor"}),
        ("ord", "", {"noun_article": "ett", "noun_plural": "ord"}),
        ("apa", "", {"noun_article": "en", "noun_plural": "apor"}),
        # no chip, no head line: only the morph can say "common", so a token without one prints nothing
        ("bok", "", {}),
        ("katt", "", {}),
        # the neuter chip alone answers
        ("hus", "", {"noun_article": "ett"}),
        # a single fem chip on an ordinary common-gender noun: masc and fem both map to en, so the row that
        # would otherwise print no article prints the right one
        ("maka", "", {"noun_article": "en"}),
        ("maka", "Case=Nom|Definite=Ind|Gender=Com|Number=Sing", {"noun_article": "en"}),
    ],
)
def test_en_ett_and_the_plural_from_real_rows(word, morph, expected):
    assert hook().render(noun(word, morph), config=AnkiMinerConfig()) == expected


def test_a_real_token_carries_the_morph_the_hook_needs():
    (token,) = [t for t in build_tagger()("En bok ligger på bordet.") if t.surface == "bok"]
    assert token.feature.lemma == "bok" and "Gender=Com" in token.morph
    word = SimpleNamespace(pos=token.feature.pos1, morph=token.morph, definition_html=RENDERED["bok"], mined_form="bok")
    assert hook().render(word, config=AnkiMinerConfig()) == {"noun_article": "en"}


def test_a_verb_gets_nothing():
    verb = SimpleNamespace(pos="VERB", morph="", definition_html=RENDERED["apa"], mined_form="apa")
    assert hook().render(verb, config=AnkiMinerConfig()) == {}
