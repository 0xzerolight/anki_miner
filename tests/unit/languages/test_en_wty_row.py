"""A real wty-en-en row survives import + render under the English keys, and the shared hook reads it."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages._spaced.grammar_hook import GrammarTagHook
from anki_miner.languages.registry import get_profile
from anki_miner.services.dictionary.importers.yomitan_importer import import_yomitan_zip
from anki_miner.services.dictionary.providers.indexed_provider import IndexedDictProvider

FIXTURE = json.loads((Path(__file__).parents[2] / "fixtures" / "en" / "wty_row.json").read_text(encoding="utf-8"))


def _render(tmp_path: Path, query: str) -> str | None:
    archive = tmp_path / "wty-en-en.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("index.json", json.dumps(FIXTURE["index"]))
        zf.writestr("tag_bank_1.json", json.dumps(FIXTURE["tag_bank"]))
        zf.writestr("term_bank_1.json", json.dumps(FIXTURE["term_rows"]))
    import_yomitan_zip(archive, tmp_path / "dicts", dict_id="wty-en-en", language="en")
    provider = IndexedDictProvider(
        "wty-en-en", tmp_path / "dicts" / "wty-en-en" / "index.sqlite", keys=get_profile("en").dict_keys
    )
    assert provider.load()
    return provider.lookup(query)


def test_the_fixture_carries_its_licence():
    assert "CC BY-SA 4.0" in FIXTURE["license"] and FIXTURE["index"]["sourceLanguage"] == "en"


def test_a_capitalised_query_renders_the_committed_html(tmp_path):
    assert _render(tmp_path, "Dog") == FIXTURE["rendered_html"]


def test_the_grammar_hook_reads_the_real_head_line(tmp_path):
    rendered = _render(tmp_path, "dog")
    word = SimpleNamespace(pos="NOUN", morph="Number=Sing", definition_html=rendered)
    assert GrammarTagHook(("noun_plural",)).render(word, config=AnkiMinerConfig()) == {
        "noun_plural": FIXTURE["expected_plural"]
    }
