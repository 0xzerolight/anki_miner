"""Real wty-da-en rows import and render under the da keys, and the en/et hook reads them (must-resolve 3)."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages._spaced.grammar_hook import GrammarTagHook
from anki_miner.languages.registry import get_profile
from anki_miner.services.dictionary.importers.yomitan_importer import import_yomitan_zip
from anki_miner.services.dictionary.providers.indexed_provider import IndexedDictProvider

FIXTURE = json.loads((Path(__file__).parents[2] / "fixtures" / "da" / "wty_row.json").read_text(encoding="utf-8"))
RENDERED = FIXTURE["rendered_html"]


@pytest.fixture
def imported(tmp_path):
    archive = tmp_path / "wty-da-en.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("index.json", json.dumps(FIXTURE["index"]))
        zf.writestr("tag_bank_1.json", json.dumps(FIXTURE["tag_bank"]))
        zf.writestr("term_bank_1.json", json.dumps(FIXTURE["term_rows"]))
    result = import_yomitan_zip(archive, tmp_path / "dicts", dict_id="wty-da-en", language="da")
    provider = IndexedDictProvider(
        "wty-da-en", tmp_path / "dicts" / "wty-da-en" / "index.sqlite", keys=get_profile("da").dict_keys
    )
    assert provider.load()
    return result, provider


def hook() -> GrammarTagHook:
    (grammar,) = [h for h in get_profile("da").render_hooks if isinstance(h, GrammarTagHook)]
    return grammar


def noun(word: str, morph: str = "") -> SimpleNamespace:
    return SimpleNamespace(pos="NOUN", morph=morph, definition_html=RENDERED[word], mined_form=word)


def test_the_fixture_carries_its_licence_and_source():
    assert "CC BY-SA 4.0" in FIXTURE["license"] and FIXTURE["index"]["sourceLanguage"] == "da"
    assert FIXTURE["index"]["revision"] == "2026.08.29"


def test_every_row_renders_the_committed_html_and_the_da_stamp_is_no_mismatch(imported):
    result, provider = imported
    for word, html in RENDERED.items():
        assert provider.lookup(word) == html, word
    assert provider.lookup("Bog") == RENDERED["bog"]  # CasefoldDictKeys
    assert result.source_language_mismatch is False


def test_the_head_line_carries_the_gender_letter():
    assert "bog c (singular definite bogen" in RENDERED["bog"]
    assert "hus n (singular definite huset" in RENDERED["hus"]
    assert "gas c or n (singular definite gassen" in RENDERED["gas"]


@pytest.mark.parametrize(
    ("word", "morph", "expected"),
    [
        ("bog", "Definite=Ind|Gender=Com|Number=Sing", {"noun_article": "en", "noun_gender": "common"}),
        ("hus", "Definite=Ind|Gender=Neut|Number=Sing", {"noun_article": "et", "noun_gender": "neuter"}),
        # The head line leads: the model tags jakke Gender=Neut, wty says c (DA7: 85 wrong morph-first, 8 head-first).
        ("jakke", "Definite=Ind|Gender=Neut|Number=Sing", {"noun_article": "en", "noun_gender": "common"}),
        ("stykke", "", {"noun_article": "et", "noun_gender": "neuter"}),
    ],
)
def test_the_head_line_decides_the_article_and_the_gender(word, morph, expected):
    assert hook().render(noun(word, morph), config=AnkiMinerConfig()) == expected


def test_a_c_or_n_head_line_falls_to_the_neuter_chip():
    """gas is 'c or n': the head line cannot decide, so the gender chip does (DA7)."""
    assert hook().render(noun("gas"), config=AnkiMinerConfig()) == {"noun_article": "et", "noun_gender": "neuter"}


def test_the_hook_is_silent_for_a_word_that_is_not_a_noun():
    verb = SimpleNamespace(pos="VERB", morph="", definition_html=RENDERED["bog"], mined_form="bog")
    assert hook().render(verb, config=AnkiMinerConfig()) == {}


def test_the_hook_declares_exactly_the_two_danish_fields():
    assert hook().field_names() == ("noun_article", "noun_gender")
    assert get_profile("da").capabilities >= {"noun_article", "noun_gender"}
