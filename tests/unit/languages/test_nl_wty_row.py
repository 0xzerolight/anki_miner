"""Real wty-nl-en rows import and render under the Dutch keys, and the de/het hook reads them (N5)."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages._spaced.grammar_hook import GrammarTagHook
from anki_miner.languages.nl.morphology import NL_ARTICLE_MAP
from anki_miner.languages.nl.tokenizer import build_tagger
from anki_miner.languages.registry import get_profile
from anki_miner.services.dictionary.importers.yomitan_importer import import_yomitan_zip
from anki_miner.services.dictionary.providers.indexed_provider import IndexedDictProvider

FIXTURE = json.loads((Path(__file__).parents[2] / "fixtures" / "nl" / "wty_row.json").read_text(encoding="utf-8"))
RENDERED = FIXTURE["rendered_html"]


@pytest.fixture
def provider(tmp_path):
    archive = tmp_path / "wty-nl-en.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("index.json", json.dumps(FIXTURE["index"]))
        zf.writestr("tag_bank_1.json", json.dumps(FIXTURE["tag_bank"]))
        zf.writestr("term_bank_1.json", json.dumps(FIXTURE["term_rows"]))
    import_yomitan_zip(archive, tmp_path / "dicts", dict_id="wty-nl-en", language="nl")
    db_path = tmp_path / "dicts" / "wty-nl-en" / "index.sqlite"
    provider = IndexedDictProvider("wty-nl-en", db_path, keys=get_profile("nl").dict_keys)
    assert provider.load()
    return provider


def hook() -> GrammarTagHook:
    (grammar,) = [h for h in get_profile("nl").render_hooks if isinstance(h, GrammarTagHook)]
    return grammar


def noun(word: str, morph: str = "") -> SimpleNamespace:
    return SimpleNamespace(pos="NOUN", morph=morph, definition_html=RENDERED[word], mined_form=word)


def test_the_fixture_carries_its_licence_and_source():
    assert "CC BY-SA 4.0" in FIXTURE["license"] and FIXTURE["index"]["sourceLanguage"] == "nl"
    assert FIXTURE["index"]["revision"] == "2026.08.29"


def test_every_row_renders_the_committed_html_and_a_capital_finds_it(provider):
    for word, html in RENDERED.items():
        assert provider.lookup(word) == html, word
    assert provider.lookup("Koffie") == RENDERED["koffie"]


@pytest.mark.parametrize(
    ("word", "morph", "expected"),
    [
        ("boek", "Gender=Neut|Number=Sing", {"noun_article": "het", "noun_gender": "neuter"}),
        ("student", "Gender=Com|Number=Sing", {"noun_article": "de", "noun_gender": "masculine"}),
        # f or m: the article is certain, the gender is not; Com is not among the chips, so morph yields too
        ("koffie", "", {"noun_article": "de"}),
        ("koffie", "Gender=Com|Number=Sing", {"noun_article": "de"}),
        # the diminutive kopje lemmatises to kop and carries Gender=Neut: the masc chip excludes it
        ("kop", "Gender=Neut|Number=Sing", {"noun_article": "de", "noun_gender": "masculine"}),
        # the model says neuter for e-mail; the dictionary says m
        ("e-mail", "Gender=Neut|Number=Sing", {"noun_article": "de", "noun_gender": "masculine"}),
        # chips masc + an obsolete neuter row: the head line decides, with or without the diminutive's Neut
        ("hond", "", {"noun_article": "de", "noun_gender": "masculine"}),
        ("hond", "Gender=Neut|Number=Sing", {"noun_article": "de", "noun_gender": "masculine"}),
        # chips fem + neut, first head line n
        ("idee", "", {"noun_article": "het", "noun_gender": "neuter"}),
        # chips and head line both n or f: only morph can decide
        ("hart", "", {}),
        ("hart", "Gender=Neut|Number=Sing", {"noun_article": "het", "noun_gender": "neuter"}),
    ],
)
def test_de_het_from_real_rows(word, morph, expected):
    assert hook().render(noun(word, morph), config=AnkiMinerConfig()) == expected


@pytest.mark.parametrize(
    ("sentence", "surface", "lemma"),
    [("Wil je een kopje thee?", "kopje", "kop"), ("Het hondje speelt in de tuin.", "hondje", "hond")],
)
def test_a_real_diminutive_token_gets_its_base_nouns_article(sentence, surface, lemma):
    (token,) = [t for t in build_tagger()(sentence) if t.surface == surface]
    assert (token.feature.lemma, token.morph) == (lemma, "Gender=Neut|Number=Sing")
    word = SimpleNamespace(pos=token.feature.pos1, morph=token.morph, definition_html=RENDERED[lemma], mined_form=lemma)
    assert hook().render(word, config=AnkiMinerConfig()) == {"noun_article": "de", "noun_gender": "masculine"}


def test_the_approved_order_would_print_het_for_hondje():
    """Why nl passes sources: morph leads when its gender is among the chips, and hond's chips hold an obsolete neut."""
    default = GrammarTagHook(("noun_article", "noun_gender"), article_map=NL_ARTICLE_MAP)
    word = SimpleNamespace(
        pos="NOUN", morph="Gender=Neut|Number=Sing", definition_html=RENDERED["hond"], mined_form="hond"
    )
    assert default.render(word, config=AnkiMinerConfig()) == {"noun_article": "het", "noun_gender": "neuter"}


def test_a_verb_gets_nothing():
    word = SimpleNamespace(pos="VERB", morph="", definition_html=RENDERED["boek"], mined_form="boeken")
    assert hook().render(word, config=AnkiMinerConfig()) == {}
