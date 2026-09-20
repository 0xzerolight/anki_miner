"""Real opr-ru-en and wty-ru-en rows (tests/fixtures/ru/) through import, lookup, the S24 stressed headword
and the grammar hook, under the Russian keys. Hard-requires ru_core_news_sm and pymorphy3."""

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

FIXTURES = Path(__file__).parents[2] / "fixtures" / "ru"
A = "\N{COMBINING ACUTE ACCENT}"
SMOKE = "Студент вчера прочитал интересную книгу."


def _provider(tmp_path: Path, name: str) -> IndexedDictProvider:
    fixture = json.loads((FIXTURES / f"{name}_row.json").read_text(encoding="utf-8"))
    dict_id = f"{name}-ru-en"
    archive = tmp_path / f"{dict_id}.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("index.json", json.dumps({**fixture["index"], "title": dict_id}))
        zf.writestr("tag_bank_1.json", json.dumps(fixture["tag_bank"]))
        zf.writestr("term_bank_1.json", json.dumps(fixture["term_rows"]))
    import_yomitan_zip(archive, tmp_path / "dicts", dict_id=dict_id, language="ru")
    provider = IndexedDictProvider(
        dict_id, tmp_path / "dicts" / dict_id / "index.sqlite", keys=get_profile("ru").dict_keys
    )
    assert provider.load()
    return provider


@pytest.fixture(scope="module")
def config():
    return switch_language(AnkiMinerConfig(), "ru")


def _readings(config, providers, text: str) -> dict[str, tuple[str, str, str]]:
    service = DefinitionService(config, providers)
    parser = get_profile("ru").create_parser(config, reading_lookup=service.offline_term_readings)
    words, _index, _counts = parser.parse_text_units([ReadingUnit(text=text, index=0, location_label="t")], False)
    return {w.mined_form: (w.expression_reading, w.expression_furigana, w.resolved_reading) for w in words}


def test_the_fixtures_carry_their_attribution():
    for name in ("opr", "wty"):
        fixture = json.loads((FIXTURES / f"{name}_row.json").read_text(encoding="utf-8"))
        assert "CC BY-SA 4.0" in fixture["license"] and fixture["index"]["sourceLanguage"] == "ru"


def test_a_yo_and_a_capital_query_find_the_folded_row(tmp_path):
    opr = _provider(tmp_path, "opr")
    assert opr.lookup("Ёлка") and opr.lookup("Ёлка") == opr.lookup("елка")
    assert opr.lookup(f"кни{A}га") == opr.lookup("книга") != ""


def test_openrussian_first_puts_the_stressed_headword_on_every_smoke_word(tmp_path, config):
    got = _readings(config, [_provider(tmp_path, "opr"), _provider(tmp_path, "wty")], SMOKE)
    assert {front: reading for front, (reading, _f, _r) in got.items()} == {
        "студент": f"студе{A}нт",
        "вчера": f"вчера{A}",
        "прочитать": f"прочита{A}ть",
        "интересный": f"интере{A}сный",
        "книга": f"кни{A}га",
    }
    assert all(furigana == "" and resolved == "" for _reading, furigana, resolved in got.values())


def test_two_stresses_stay_blank_and_a_monosyllable_reads_plain(tmp_path, config):
    got = _readings(config, [_provider(tmp_path, "opr"), _provider(tmp_path, "wty")], "Замок стоит, а дом рядом.")
    assert got["замок"][0] == "" and got["дом"][0] == "дом"


def test_a_yo_front_reaches_its_folded_row(tmp_path, config):
    got = _readings(config, [_provider(tmp_path, "opr"), _provider(tmp_path, "wty")], "Черный кот и ёлка.")
    assert got["чёрный"][0] == "чёрный" and got["ёлка"][0] == "ёлка"


def test_wiktionary_alone(tmp_path, config):
    got = _readings(config, [_provider(tmp_path, "wty")], "Книга и черный дом.")
    assert got["книга"][0] == f"кни{A}га" and got["дом"][0] == "дом" and got["чёрный"][0] == ""


def _render(pos: str, html: str, morph: str = "") -> dict[str, str]:
    hook = get_profile("ru").render_hooks[1]
    word = SimpleNamespace(pos=pos, morph=morph, definition_html=html, mined_form="")
    return hook.render(word, config=AnkiMinerConfig())


def test_the_wiktionary_head_line_names_gender_aspect_and_partner(tmp_path):
    wty = _provider(tmp_path, "wty")
    assert _render("NOUN", wty.lookup("книга") or "") == {"noun_gender": "ж."}
    assert _render("NOUN", wty.lookup("дом") or "") == {"noun_gender": "м."}
    assert _render("VERB", wty.lookup("читать") or "") == {
        "aspect_pair": f"imperfective (perfective: прочита{A}ть or почита{A}ть or проче{A}сть)"
    }
    assert _render("VERB", wty.lookup("прочитать") or "") == {"aspect_pair": f"perfective (imperfective: чита{A}ть)"}


def test_the_yo_fold_merges_nebo_and_nyobo_on_purpose(tmp_path, config):
    """Plan D3 class 3: `ё`->`е` on terms makes небо (sky) and нёбо (palate) ONE key, so they share one
    definition block and one known-word identity, and their disagreeing readings leave S24 blank.
    The fold is on the dictionary key and on dedup, never on `mined_form`, so both words are still
    mined and each carries the merged block; dedup then keeps whichever the episode saw first.

    The fold is what earns every other `ё` lemma its reading (pymorphy3 normal forms keep `ё`, most
    indexed rows do not), so this merge is the price, recorded here as a decision rather than found
    later as a surprise. opr itself files the palate sense under the term небо with the reading нёбо.
    """
    opr = _provider(tmp_path, "opr")
    merged = opr.lookup("нёбо")
    assert merged and merged == opr.lookup("небо") == opr.lookup(f"Не{A}бо")
    got = _readings(config, [opr, _provider(tmp_path, "wty")], "Небо и нёбо.")
    # Two fronts: pymorphy3 keeps `ё` in the normal form, so the fold never reaches mined_form.
    assert set(got) == {"небо", "нёбо"}
    # One identity: dedup_fold collapses them, so the second is a duplicate of the first, and one
    # known-word row covers both. The stressed небо and нёбо are two readings for that one key, so
    # S24 keeps the field blank on both.
    fold = get_profile("ru").dedup_fold
    assert fold is not None and fold("небо") == fold("нёбо")
    assert got["небо"][0] == got["нёбо"][0] == ""


def test_an_openrussian_block_answers_from_the_morph_alone(tmp_path):
    """opr has no head line and uncategorised tags (plan D10): gender/aspect come from the tagger, no partner."""
    opr = _provider(tmp_path, "opr")
    assert _render("NOUN", opr.lookup("книга") or "", morph="Animacy=Inan|Case=Nom|Gender=Fem|Number=Sing") == {
        "noun_gender": "ж."
    }
    assert _render("VERB", opr.lookup("читать") or "", morph="Aspect=Imp|VerbForm=Inf") == {
        "aspect_pair": "imperfective"
    }
    assert _render("NOUN", opr.lookup("книга") or "") == {}
