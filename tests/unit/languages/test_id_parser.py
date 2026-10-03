"""Indonesian end to end: the parser, the dictionary-validated ladder and the hooks over real wty-id-en rows."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.id import ID_SENTENCE_RULES
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.models.reading import ReadingUnit
from anki_miner.services.definition_service import DefinitionService
from anki_miner.services.dictionary.importers.yomitan_importer import import_yomitan_zip
from anki_miner.services.dictionary.providers.indexed_provider import IndexedDictProvider
from anki_miner.services.frequency.lemmatize import build_frequency_lemmatizer
from anki_miner.services.reading.sentence_splitter import split_sentences

FIXTURE = json.loads((Path(__file__).parents[2] / "fixtures" / "id" / "wty_rows.json").read_text(encoding="utf-8"))
CONFIG = switch_language(AnkiMinerConfig(), "id")


@pytest.fixture(scope="module")
def parser():
    return get_profile("id").create_parser(CONFIG)


def _nowrap(*parts: str) -> str:
    return " + ".join(f'<span style="white-space:nowrap">{part}</span>' for part in parts)


def _fronts(parser, text: str) -> list[str]:
    words, _index, _counts = parser.parse_text_units([ReadingUnit(text=text, index=0, location_label="t")], False)
    return [word.mined_form for word in words]


def test_the_smoke_sentence_mines_its_content_words(parser):
    assert _fronts(parser, "Saya sedang membaca buku di rumah.") == ["membaca", "buku", "rumah"]


def test_fronts_are_folded_surfaces_and_function_words_names_and_numbers_stay_out(parser):
    fronts = _fronts(parser, "Gue nggak ngerti, beliin aja buku-buku itu dari Jakarta, 3 kali.")
    assert fronts == ["ngerti", "beliin", "buku-buku", "kali"]


def test_derived_verbs_are_mined_and_pronouns_and_articles_are_not(parser):
    assert _fronts(parser, "Aku mengira kau sudah pulang.") == ["mengira", "sudah", "pulang"]
    assert _fronts(parser, "Kue ini dibuat oleh si nenek untuk sang raja.") == ["kue", "dibuat", "nenek", "raja"]


def test_the_honorifics_are_abbreviations_so_the_name_after_them_stays_out(parser):
    """``Tn.``/``Ny.``/``Nn.`` (Tuan, Nyonya, Nona) render Mr./Mrs./Miss in dubbed-film subtitles."""
    text = "Tn. Harris sudah menunggu di lobi. Ny. Wijaya, silakan masuk. Nn. Parker belum datang."
    assert _fronts(parser, text) == ["sudah", "menunggu", "lobi", "silakan", "masuk", "datang"]
    assert split_sentences("Tn. Harris membuka pintu perlahan. Ny. Wijaya diam.", rules=ID_SENTENCE_RULES) == [
        "Tn. Harris membuka pintu perlahan.",
        "Ny. Wijaya diam.",
    ]


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
        ("membeli", {"root": "beli", "affixes": _nowrap("meng-", "beli")}),
        ("merugikan", {"root": "rugi", "affixes": _nowrap("meng-", "rugi", "-kan")}),
        ("keadaban", {"root": "adab", "affixes": _nowrap("ke-", "adab", "-an")}),
        ("berjalan", {"root": "jalan", "affixes": _nowrap("ber-", "jalan")}),
        ("beli", {}),
        # Form rows only: the card shows the meN- entry they name, whose meng- the di- front does not carry.
        ("dibeli", {}),
        ("dijual", {}),
        ("ditulis", {}),  # names tulis and menulis
        ("menulis", {"root": "tulis", "affixes": _nowrap("meng-", "tulis")}),
        ("nggak", {}),
    ],
)
def test_the_root_hook_reads_the_real_etymology_lines(provider, headword, fields):
    hook = get_profile("id").render_hooks[0]
    word = SimpleNamespace(definition_html=provider.lookup(headword) or "", mined_form=headword)
    assert hook.render(word, config=CONFIG) == fields


def test_form_rows_never_reach_the_card(provider):
    """membeli's own form rows (naming beli and dibeli) go beside its lemma row; dibeli, a form row only,
    reads the membeli entry it names, one hop (storage form-of rows)."""
    membeli = provider.lookup("membeli")
    assert membeli is not None and "non-lemma" not in membeli and "to buy" in membeli
    assert provider.lookup("dibeli") == membeli


def test_the_frequency_lemmatizer_only_folds():
    lemmatize = build_frequency_lemmatizer("id")
    assert lemmatize(["nggak", "bukunya", "kunang-kunang", "Rumah"]) == ["nggak", "bukunya", "kunang-kunang", "rumah"]
