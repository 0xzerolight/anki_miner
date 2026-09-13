"""Real wty-es-en rows survive import + render under the Spanish keys, and the gender field reads them (plan D3)."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.registry import get_profile
from anki_miner.languages.tagger_provider import get_tagger
from anki_miner.services.dictionary.importers.yomitan_importer import import_yomitan_zip
from anki_miner.services.dictionary.providers.indexed_provider import IndexedDictProvider

FIXTURE = json.loads((Path(__file__).parents[2] / "fixtures" / "es" / "wty_row.json").read_text(encoding="utf-8"))
ENTRIES = {entry["query"]: entry for entry in FIXTURE["entries"]}
LABELS = {"masc": "el", "fem": "la", None: None}
#: What es_core_news_sm really puts on each noun in a plain sentence (`El mapa es grande.` -> Fem, a miss).
MORPH = {
    "nieve": "Gender=Fem|Number=Sing",
    "mapa": "Gender=Fem|Number=Sing",
    "mano": "Gender=Fem|Number=Sing",
    "estudiante": "Number=Sing",
}


def _render(tmp_path: Path, entry: dict, query: str) -> str | None:
    archive = tmp_path / "wty-es-en.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("index.json", json.dumps(FIXTURE["index"]))
        zf.writestr("tag_bank_1.json", json.dumps(FIXTURE["tag_bank"]))
        zf.writestr("term_bank_1.json", json.dumps(entry["term_rows"]))
    import_yomitan_zip(archive, tmp_path / "dicts", dict_id="wty-es-en", language="es")
    provider = IndexedDictProvider(
        "wty-es-en", tmp_path / "dicts" / "wty-es-en" / "index.sqlite", keys=get_profile("es").dict_keys
    )
    assert provider.load()
    return provider.lookup(query)


def test_the_fixture_carries_its_licence():
    assert "CC BY-SA 4.0" in FIXTURE["license"] and FIXTURE["index"]["sourceLanguage"] == "es"
    assert set(ENTRIES) == {"nieve", "mapa", "mano", "estudiante"}


@pytest.mark.parametrize("query", sorted(ENTRIES))
def test_a_capitalised_query_renders_the_committed_html(tmp_path, query):
    entry = ENTRIES[query]
    assert _render(tmp_path, entry, query.capitalize()) == entry["rendered_html"]


@pytest.mark.parametrize("query", sorted(ENTRIES))
def test_the_gender_field_uses_morph_only_where_the_chips_allow_it(tmp_path, query):
    """en contract item 9: mapa's Fem is excluded by its single masc chip; mano's Fem is inside fem+masc."""
    entry = ENTRIES[query]
    word = SimpleNamespace(pos="NOUN", morph=MORPH[query], definition_html=_render(tmp_path, entry, query))
    expected = LABELS[entry["expected_gender"]]
    rendered = get_profile("es").render_hooks[1].render(word, config=AnkiMinerConfig())
    assert rendered == ({"noun_gender": expected} if expected else {})


def test_the_model_really_guesses_mapa_feminine():
    """Why the chip rule matters for Spanish: the small morphologizer reads the -a ending, not the article."""
    (mapa,) = [token for token in get_tagger("es")("El mapa es grande.") if token.surface == "mapa"]
    assert "Gender=Fem" in mapa.morph  # a LanguageToken slot, not a feature field
