"""Real wty-sh-en and wty-pl-en verb rows survive import + render, and GrammarTagHook reads their aspect (Ruling S1)."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages._spaced.grammar_hook import GrammarTagHook
from anki_miner.languages._spaced.keys import CasefoldDictKeys
from anki_miner.services.dictionary.importers.yomitan_importer import import_yomitan_zip
from anki_miner.services.dictionary.providers.indexed_provider import IndexedDictProvider

FIXTURE = json.loads(
    (Path(__file__).parents[2] / "fixtures" / "dictionary" / "wty_aspect_rows.json").read_text(encoding="utf-8")
)
HOOK = GrammarTagHook(("aspect_pair",))


@pytest.fixture(scope="module", params=sorted(FIXTURE["dictionaries"]))
def loaded(request, tmp_path_factory):
    dict_id = request.param
    data = FIXTURE["dictionaries"][dict_id]
    root = tmp_path_factory.mktemp(dict_id)
    archive = root / f"{dict_id}.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("index.json", json.dumps(data["index"]))
        zf.writestr("tag_bank_1.json", json.dumps(data["tag_bank"]))
        zf.writestr("term_bank_1.json", json.dumps(data["term_rows"]))
    import_yomitan_zip(archive, root / "dicts", dict_id=dict_id, language="en")
    provider = IndexedDictProvider(dict_id, root / "dicts" / dict_id / "index.sqlite", keys=CasefoldDictKeys())
    assert provider.load()
    yield data, provider
    provider.close()


def test_the_fixture_carries_its_licence_and_revision():
    assert "CC BY-SA 4.0" in FIXTURE["license"]
    assert {data["index"]["sourceLanguage"] for data in FIXTURE["dictionaries"].values()} == {"sh", "pl"}
    assert {data["index"]["revision"] for data in FIXTURE["dictionaries"].values()} == {"2026.08.29"}


def test_every_term_renders_the_committed_html(loaded):
    data, provider = loaded
    for term, html in data["rendered_html"].items():
        assert provider.lookup(term) == html, term


def _verb(dict_id: str, term: str, morph: str = "") -> SimpleNamespace:
    html = FIXTURE["dictionaries"][dict_id]["rendered_html"][term]
    return SimpleNamespace(pos="VERB", morph=morph, definition_html=html, mined_form=term)


@pytest.mark.parametrize(
    ("dict_id", "term", "morph", "expected"),
    [
        ("wty-sh-en", "čitati", "", "imperfective (perfective: pročìtati)"),
        ("wty-sh-en", "pročitati", "", "perfective (imperfective: čìtati)"),
        ("wty-sh-en", "kupiti", "", "perfective"),  # C1: chips impf+pf (two lexemes), first head line pf
        ("wty-sh-en", "imenovati", "", "imperfective or perfective"),
        ("wty-pl-en", "przeczytać", "Aspect=Perf|Gender=Masc|Number=Sing", "perfective (imperfective: czytać)"),
        ("wty-pl-en", "kupić", "", "perfective (imperfective: kupować or kupać)"),
        ("wty-pl-en", "mieszkać", "Aspect=Imp|Mood=Ind", "imperfective"),  # morph within the chips
        ("wty-pl-en", "mieszkać", "", None),  # C1: both chips and no head line: nothing, not "both"
    ],
)
def test_the_hook_reads_the_real_entries(dict_id, term, morph, expected):
    out = HOOK.render(_verb(dict_id, term, morph), config=AnkiMinerConfig())
    assert out == ({} if expected is None else {"aspect_pair": expected})
