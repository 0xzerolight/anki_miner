"""Real wty-de-en rows survive import + render under the German keys, and the German hooks read them."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.registry import get_profile
from anki_miner.services.dictionary.importers.yomitan_importer import import_yomitan_zip
from anki_miner.services.dictionary.providers.indexed_provider import IndexedDictProvider

FIXTURE = json.loads((Path(__file__).parents[2] / "fixtures" / "de" / "wty_row.json").read_text(encoding="utf-8"))
CONFIG = AnkiMinerConfig()

#: term -> (token morph, expected hook output). Morph values are what de_core_news_sm gives the noun in context.
EXPECTED = {
    "Fuchs": ("Case=Nom|Gender=Masc|Number=Sing", {"noun_gender": "der", "noun_plural": "Füchse"}),  # umlaut plural
    "Hund": ("Case=Nom|Gender=Masc|Number=Sing", {"noun_gender": "der", "noun_plural": "Hunde"}),
    "Kiefer": ("", {"noun_gender": "die", "noun_plural": "Kiefern"}),  # 3 gender chips -> the first head line
    "E-Mail": ("Case=Acc|Gender=Fem|Number=Sing", {"noun_gender": "die", "noun_plural": "E-Mails"}),
    "Ferien": ("Case=Acc|Number=Plur", {}),  # plurale tantum: no gender, no plural form
    "Straße": ("", {"noun_gender": "die", "noun_plural": "Straßen"}),
    "Hardware": ("", {"noun_gender": "die", "noun_plural": "Hardwares"}),  # "plural (uncommon) Hardwares"
    "Oma": ("Case=Nom|Gender=Masc|Number=Sing", {"noun_gender": "die", "noun_plural": "Omas"}),  # morph is wrong
}


@pytest.fixture(scope="module")
def provider(tmp_path_factory):
    root = tmp_path_factory.mktemp("wty_de")
    archive = root / "wty-de-en.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("index.json", json.dumps(FIXTURE["index"]))
        zf.writestr("tag_bank_1.json", json.dumps(FIXTURE["tag_bank"]))
        zf.writestr("term_bank_1.json", json.dumps(FIXTURE["term_rows"]))
    import_yomitan_zip(archive, root / "dicts", dict_id="wty-de-en", language="de")
    loaded = IndexedDictProvider(
        "wty-de-en", root / "dicts" / "wty-de-en" / "index.sqlite", keys=get_profile("de").dict_keys
    )
    assert loaded.load()
    yield loaded
    loaded.close()


def test_the_fixture_carries_its_licence():
    assert "CC BY-SA 4.0" in FIXTURE["license"] and FIXTURE["index"]["sourceLanguage"] == "de"


@pytest.mark.parametrize("term", sorted(FIXTURE["rendered_html"]))
def test_every_term_renders_the_committed_html(provider, term):
    assert provider.lookup(term) == FIXTURE["rendered_html"][term]


def test_sharp_s_and_capitals_meet_at_the_index(provider):
    assert provider.lookup("STRASSE") == provider.lookup("strasse") == FIXTURE["rendered_html"]["Straße"]


@pytest.mark.parametrize(("term", "morph", "expected"), [(term, *pair) for term, pair in EXPECTED.items()])
def test_the_grammar_hook_reads_the_real_entry(term, morph, expected):
    hook = get_profile("de").render_hooks[1]
    word = SimpleNamespace(pos="NOUN", morph=morph, definition_html=FIXTURE["rendered_html"][term])
    assert hook.render(word, config=CONFIG) == expected


def test_a_verb_entry_carries_a_part_of_speech_and_no_grammar():
    pos_hook, grammar_hook = get_profile("de").render_hooks
    word = SimpleNamespace(pos="VERB", morph="VerbForm=Inf", definition_html=FIXTURE["rendered_html"]["ansehen"])
    assert pos_hook.render(word, config=CONFIG) == {"pos": "verb"}
    assert grammar_hook.render(word, config=CONFIG) == {}
