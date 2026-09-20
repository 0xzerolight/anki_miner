"""Real wty-vi-en rows survive import + render under the vi keys; the Hán Việt hook reads the etymology."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.languages.registry import get_profile
from anki_miner.languages.vi.render import HANVIET_FIELD, HanVietHook
from anki_miner.services.dictionary.importers.yomitan_importer import import_yomitan_zip
from anki_miner.services.dictionary.providers.indexed_provider import IndexedDictProvider

FIXTURE = json.loads((Path(__file__).parents[2] / "fixtures" / "vi" / "wty_rows.json").read_text(encoding="utf-8"))


@pytest.fixture
def provider(tmp_path):
    archive = tmp_path / "wty-vi-en.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("index.json", json.dumps(FIXTURE["index"]))
        zf.writestr("tag_bank_1.json", json.dumps(FIXTURE["tag_bank"]))
        zf.writestr("term_bank_1.json", json.dumps(FIXTURE["term_rows"]))
    import_yomitan_zip(archive, tmp_path / "dicts", dict_id="wty-vi-en", language="vi")
    loaded = IndexedDictProvider(
        "wty-vi-en", tmp_path / "dicts" / "wty-vi-en" / "index.sqlite", keys=get_profile("vi").dict_keys
    )
    assert loaded.load()
    return loaded


def _hanviet(html: str | None) -> dict[str, str]:
    word = SimpleNamespace(mined_form="x", surface="x", definition_html=html or "")
    return HanVietHook().render(word, config=AnkiMinerConfig())


def test_the_fixture_carries_its_licence():
    assert "CC BY-SA 4.0" in FIXTURE["license"] and FIXTURE["index"]["sourceLanguage"] == "vi"


@pytest.mark.parametrize("query", ["hòa bình", "hoà bình", "HÒA BÌNH", "Hoà Bình"])
def test_both_tone_styles_and_every_case_meet_one_key(provider, query):
    html = provider.lookup(query)
    assert html is not None and "和平" in html
    assert html == provider.lookup("hòa bình")


def test_the_y_spelling_hits_a_form_stub_and_the_ladder_reaches_the_lemma(provider):
    """Decision 9: kỹ thuật exists only as a non-lemma row naming kĩ thuật; the y→i rung reaches the entry."""
    stub = provider.lookup("kỹ thuật")
    assert stub is not None and "kĩ thuật" in stub and "技術" not in stub
    candidate, conditions = get_profile("vi").lookup.candidates("kỹ thuật", "", None)[0]
    assert (candidate, conditions) == ("kĩ thuật", 0)
    lemma = provider.lookup(candidate)
    assert lemma is not None and "技術" in lemma


@pytest.mark.parametrize(("term", "hanzi"), [("hòa bình", "和平"), ("bác sĩ", "博士"), ("kĩ thuật", "技術")])
def test_the_hook_reads_the_sino_vietnamese_etymology(provider, term, hanzi):
    assert _hanviet(provider.lookup(term)) == {"hanviet": hanzi}


@pytest.mark.parametrize("term", ["muôn", "đẹp", "đẹp đẽ", "sức khỏe"])
def test_a_native_word_or_a_non_sino_reading_leaves_the_field_blank(provider, term):
    """muôn is 'Non-Sino-Vietnamese reading of Chinese 萬': not a Hán Việt word, so no field."""
    assert _hanviet(provider.lookup(term)) == {}


def test_the_hook_is_declared_and_gated():
    profile = get_profile("vi")
    assert profile.extra_card_fields == (HANVIET_FIELD,)
    assert HANVIET_FIELD.key == "hanviet" and HANVIET_FIELD.capability == "hanviet"
    assert profile.card_field_defaults["hanviet"] == ""
    assert [hook.field_names() for hook in profile.render_hooks] == [("hanviet",)]
    assert "hanviet" in profile.capabilities
