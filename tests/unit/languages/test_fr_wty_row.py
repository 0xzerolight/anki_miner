"""Real wty-fr-en rows survive import + render under the French keys, and the gender hook reads them."""

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

FIXTURE = json.loads((Path(__file__).parents[2] / "fixtures" / "fr" / "wty_row.json").read_text(encoding="utf-8"))


def _hook() -> GrammarTagHook:
    (hook,) = [hook for hook in get_profile("fr").render_hooks if isinstance(hook, GrammarTagHook)]
    return hook


def _provider(tmp_path: Path) -> IndexedDictProvider:
    archive = tmp_path / "wty-fr-en.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("index.json", json.dumps(FIXTURE["index"]))
        zf.writestr("tag_bank_1.json", json.dumps(FIXTURE["tag_bank"]))
        zf.writestr("term_bank_1.json", json.dumps(FIXTURE["term_rows"]))
    import_yomitan_zip(archive, tmp_path / "dicts", dict_id="wty-fr-en", language="fr")
    provider = IndexedDictProvider(
        "wty-fr-en", tmp_path / "dicts" / "wty-fr-en" / "index.sqlite", keys=get_profile("fr").dict_keys
    )
    assert provider.load()
    return provider


def test_the_fixture_carries_its_licence():
    assert "CC BY-SA 4.0" in FIXTURE["license"] and FIXTURE["index"]["sourceLanguage"] == "fr"


def test_capitalised_queries_render_the_committed_html(tmp_path):
    provider = _provider(tmp_path)
    assert provider.lookup("Chaise") == FIXTURE["rendered_html"]["chaise"]
    assert provider.lookup("ÉLÈVE") == FIXTURE["rendered_html"]["élève"]


def test_the_hook_reads_gender_from_the_real_chip_and_head_line():
    word = SimpleNamespace(pos="NOUN", morph="", definition_html=FIXTURE["rendered_html"]["chaise"])
    assert _hook().render(word, config=AnkiMinerConfig()) == {"noun_gender": FIXTURE["expected_gender"]["chaise"]}


def test_a_noun_of_either_gender_takes_the_sentence_morph_or_prints_nothing():
    html = FIXTURE["rendered_html"]["élève"]
    unresolved = SimpleNamespace(pos="NOUN", morph="Number=Sing", definition_html=html)
    resolved = SimpleNamespace(pos="NOUN", morph="Gender=Masc|Number=Sing", definition_html=html)
    assert _hook().render(unresolved, config=AnkiMinerConfig()) == {}  # two chips, "m or f by sense"
    assert _hook().render(resolved, config=AnkiMinerConfig()) == {"noun_gender": "le"}
