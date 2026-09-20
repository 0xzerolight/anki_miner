"""Indonesian end to end: the parser, the dictionary-validated ladder and the hooks over real wty-id-en rows."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.models.reading import ReadingUnit
from anki_miner.services.definition_service import DefinitionService
from anki_miner.services.dictionary.importers.yomitan_importer import import_yomitan_zip
from anki_miner.services.dictionary.providers.indexed_provider import IndexedDictProvider
from anki_miner.services.frequency.lemmatize import build_frequency_lemmatizer

FIXTURE = json.loads((Path(__file__).parents[2] / "fixtures" / "id" / "wty_rows.json").read_text(encoding="utf-8"))
CONFIG = switch_language(AnkiMinerConfig(), "id")


@pytest.fixture(scope="module")
def parser():
    return get_profile("id").create_parser(CONFIG)


def _fronts(parser, text: str) -> list[str]:
    words, _index, _counts = parser.parse_text_units([ReadingUnit(text=text, index=0, location_label="t")], False)
    return [word.mined_form for word in words]


def test_the_smoke_sentence_mines_its_content_words(parser):
    assert _fronts(parser, "Saya sedang membaca buku di rumah.") == ["membaca", "buku", "rumah"]


def test_fronts_are_folded_surfaces_and_function_words_names_and_numbers_stay_out(parser):
    fronts = _fronts(parser, "Gue nggak ngerti, beliin aja buku-buku itu dari Jakarta, 3 kali.")
    assert fronts == ["ngerti", "beliin", "buku-buku", "kali"]


@pytest.fixture
def provider(tmp_path):
    archive = tmp_path / "wty-id-en.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("index.json", json.dumps(FIXTURE["index"]))
        zf.writestr("tag_bank_1.json", json.dumps(FIXTURE["tag_bank"]))
        zf.writestr("term_bank_1.json", json.dumps(FIXTURE["term_rows"]))
    import_yomitan_zip(archive, tmp_path / "dicts", dict_id="wty-id-en", language="id")
    loaded = IndexedDictProvider(
        "wty-id-en", tmp_path / "dicts" / "wty-id-en" / "index.sqlite", keys=get_profile("id").dict_keys
    )
    assert loaded.load()
    yield loaded
    loaded.close()


def test_the_fixture_carries_its_attribution():
    assert "CC BY-SA 4.0" in FIXTURE["license"] and FIXTURE["index"]["sourceLanguage"] == "id"


def _definition(provider, word: str) -> str | None:
    service = DefinitionService(CONFIG, [provider], lookup=get_profile("id").lookup)
    (html,) = service.get_definitions_batch([(word, None)], fallback_context={word: (word, None)})
    return html


@pytest.mark.parametrize(
    ("front", "headword"),
    [
        ("beliin", "membelikan"),  # colloquial -in through the table
        ("mengertilah", "mengerti"),  # the particle strips; never on to erti
        ("bukunya", "buku"),
        ("dirumah", "rumah"),  # a glued preposition
        ("mengérti", "mengerti"),  # the key fold, no ladder needed
    ],
)
def test_a_miss_reaches_the_first_headword_the_dictionary_knows(provider, front, headword):
    html = _definition(provider, front)
    assert html is not None and html == provider.lookup(headword)


def test_an_unequal_reduplication_is_looked_up_whole(provider):
    assert _definition(provider, "sayur-mayur") == provider.lookup("sayur-mayur") is not None


@pytest.mark.parametrize(
    ("headword", "fields"),
    [
        ("membeli", {"root": "beli", "affixes": "meng- + beli"}),
        ("merugikan", {"root": "rugi", "affixes": "meng- + rugi + -kan"}),
        ("keadaban", {"root": "adab", "affixes": "ke- + adab + -an"}),
        ("berjalan", {"root": "jalan", "affixes": "ber- + jalan"}),
        ("beli", {}),
        ("dibeli", {}),  # a non-lemma row: no etymology, no guessed root
        ("nggak", {}),
    ],
)
def test_the_root_hook_reads_the_real_etymology_lines(provider, headword, fields):
    hook = get_profile("id").render_hooks[0]
    word = SimpleNamespace(definition_html=provider.lookup(headword) or "", mined_form=headword)
    assert hook.render(word, config=CONFIG) == fields


def test_the_frequency_lemmatizer_only_folds():
    lemmatize = build_frequency_lemmatizer("id")
    assert lemmatize(["nggak", "bukunya", "kunang-kunang", "Rumah"]) == ["nggak", "bukunya", "kunang-kunang", "rumah"]
