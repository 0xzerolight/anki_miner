"""Real wty-uk-en rows (tests/fixtures/uk/) through import, lookup, the S24 stressed headword and the
grammar hook, under the Ukrainian keys. Hard-requires uk_core_news_sm and pymorphy3."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.registry import get_profile
from anki_miner.languages.switching import switch_language
from anki_miner.languages.uk.morphology import UK_GENDER_LABELS
from anki_miner.models.reading import ReadingUnit
from anki_miner.services.definition_service import DefinitionService
from anki_miner.services.dictionary.importers.yomitan_importer import import_yomitan_zip
from anki_miner.services.dictionary.providers.indexed_provider import IndexedDictProvider

FIXTURES = Path(__file__).parents[2] / "fixtures" / "uk"
A = "\N{COMBINING ACUTE ACCENT}"
RSQUO = "\N{RIGHT SINGLE QUOTATION MARK}"
SMOKE = "Студент учора прочитав цікаву книжку."


def _provider(tmp_path: Path) -> IndexedDictProvider:
    fixture_path = FIXTURES / "wty_row.json"
    if not fixture_path.exists():  # pragma: no cover - a missing fixture is a broken checkout
        pytest.fail(f"missing real-data fixture: {fixture_path}")
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    dict_id = "wty-uk-en"
    archive = tmp_path / f"{dict_id}.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("index.json", json.dumps({**fixture["index"], "title": dict_id}))
        zf.writestr("tag_bank_1.json", json.dumps(fixture["tag_bank"]))
        zf.writestr("term_bank_1.json", json.dumps(fixture["term_rows"]))
    import_yomitan_zip(archive, tmp_path / "dicts", dict_id=dict_id, language="uk")
    provider = IndexedDictProvider(
        dict_id, tmp_path / "dicts" / dict_id / "index.sqlite", keys=get_profile("uk").dict_keys
    )
    assert provider.load()
    return provider


@pytest.fixture(scope="module")
def config():
    return switch_language(AnkiMinerConfig(), "uk")


def _readings(config, providers, text: str) -> dict[str, tuple[str, str, str]]:
    service = DefinitionService(config, providers)
    parser = get_profile("uk").create_parser(config, reading_lookup=service.offline_term_readings)
    words, _index, _counts = parser.parse_text_units([ReadingUnit(text=text, index=0, location_label="t")], False)
    return {w.mined_form: (w.expression_reading, w.expression_furigana, w.resolved_reading) for w in words}


def test_the_fixture_carries_its_attribution():
    fixture = json.loads((FIXTURES / "wty_row.json").read_text(encoding="utf-8"))
    assert "CC BY-SA 4.0" in fixture["license"] and fixture["index"]["sourceLanguage"] == "uk"


def test_an_apostrophe_and_a_capital_query_find_the_folded_row(tmp_path):
    wty = _provider(tmp_path)
    assert wty.lookup(f"М{RSQUO}ЯЧ") == wty.lookup("м'яч") != ""
    assert wty.lookup(f"кни{A}жка") == wty.lookup("книжка") != ""


def test_wiktionary_puts_the_stressed_headword_on_the_smoke_words(tmp_path, config):
    """P7: the non-lemma rows attest the stress the lemma row omits - where wty files one at all."""
    got = _readings(config, [_provider(tmp_path)], SMOKE)
    assert {front: reading for front, (reading, _f, _r) in got.items()} == {
        "студент": f"студе{A}нт",
        "учора": f"учо{A}ра",
        "прочитати": f"прочита{A}ти",
        "цікавий": f"ціка{A}вий",
        # книжка is defined but filed only as a lemma row, so nothing is attested: the 40% gap,
        # pinned so an upstream revision that closes it is noticed rather than assumed.
        "книжка": "",
    }
    assert all(furigana == "" and resolved == "" for _reading, furigana, resolved in got.values())


def test_two_stresses_stay_blank(tmp_path, config):
    got = _readings(config, [_provider(tmp_path)], "Замок зачинений, а студент чекає.")
    # wty attests two readings, stressed on the first and on the second syllable (castle vs lock).
    assert got["замок"][0] == ""
    assert got["студент"][0] == f"студе{A}нт"  # one attested reading wins


def test_an_apostrophe_front_reaches_its_folded_row(tmp_path, config):
    """м'яч has no attested reading, but здоров'я does - both must reach their row through the fold."""
    wty = _provider(tmp_path)
    got = _readings(config, [wty], f"Він грає у м{RSQUO}яч, бо здоров{RSQUO}я найдорожче.")
    assert {"м'яч", "здоров'я"} <= set(got)
    assert got["здоров'я"][0] == f"здоро{A}в'я"
    assert wty.lookup(f"м{RSQUO}яч") != ""


def _render(pos: str, html: str, morph: str = "") -> dict[str, str]:
    hook = get_profile("uk").render_hooks[1]
    word = SimpleNamespace(pos=pos, morph=morph, definition_html=html, mined_form="")
    return hook.render(word, config=AnkiMinerConfig())


def test_the_head_line_names_gender_and_the_stressed_aspect_partner(tmp_path):
    wty = _provider(tmp_path)
    assert _render("NOUN", wty.lookup("книжка") or "") == {"noun_gender": "ж."}
    assert _render("NOUN", wty.lookup("м'яч") or "") == {"noun_gender": "ч."}
    assert _render("NOUN", wty.lookup("здоров'я") or "") == {"noun_gender": "с."}
    # `кіт • (kit) m animal`: uk passes no animacy_labels, so the plain gender label is used.
    assert _render("NOUN", wty.lookup("кіт") or "") == {"noun_gender": "ч."}
    assert _render("VERB", wty.lookup("читати") or "") == {"aspect_pair": f"imperfective (perfective: прочита{A}ти)"}
    assert _render("VERB", wty.lookup("прочитати") or "") == {
        "aspect_pair": f"perfective (imperfective: чита{A}ти or прочи{A}тувати)"
    }


def test_the_labels_are_the_ukrainian_abbreviations_not_the_russian_ones():
    assert dict(UK_GENDER_LABELS) == {"masc": "ч.", "fem": "ж.", "neut": "с."}
    assert dict(get_profile("ru").render_hooks[1]._gender_labels)["masc"] == "м."


def test_the_romanisation_is_folded_off_before_the_rules_read_the_head(tmp_path):
    """Without head_fold the romanisation clause sits where the rules expect the grammar words, and
    the partner is silently lost - the aspect survives, the pair does not."""
    from anki_miner.languages._spaced.grammar_hook import GrammarTagHook

    block = _provider(tmp_path).lookup("читати") or ""
    bare = GrammarTagHook(("noun_gender", "aspect_pair"), gender_labels=UK_GENDER_LABELS)
    word = SimpleNamespace(pos="VERB", morph="", definition_html=block, mined_form="")
    assert bare.render(word, config=AnkiMinerConfig()) == {"aspect_pair": "imperfective"}
    assert _render("VERB", block) == {"aspect_pair": f"imperfective (perfective: прочита{A}ти)"}


def test_a_non_noun_and_an_empty_block_render_nothing(tmp_path):
    wty = _provider(tmp_path)
    assert _render("ADV", wty.lookup("книжка") or "") == {}
    assert _render("NOUN", "") == {}
