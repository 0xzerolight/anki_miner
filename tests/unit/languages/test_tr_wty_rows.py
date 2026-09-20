"""Real wty-tr-en rows import under the Turkish keys: both capital i's and the inflected-form row resolve (B.6, S4)."""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

import pytest

from anki_miner.languages._spaced.keys import CasefoldDictKeys
from anki_miner.languages.registry import get_profile
from anki_miner.services.dictionary.importers.yomitan_importer import import_yomitan_zip
from anki_miner.services.dictionary.providers.indexed_provider import IndexedDictProvider

FIXTURE = json.loads((Path(__file__).parents[2] / "fixtures" / "tr" / "wty_rows.json").read_text(encoding="utf-8"))


@pytest.fixture
def index(tmp_path):
    archive = tmp_path / "wty-tr-en.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("index.json", json.dumps(FIXTURE["index"]))
        zf.writestr("tag_bank_1.json", json.dumps(FIXTURE["tag_bank"]))
        zf.writestr("term_bank_1.json", json.dumps(FIXTURE["term_rows"]))
    result = import_yomitan_zip(archive, tmp_path / "dicts", dict_id="wty-tr-en", language="tr")
    assert result.entry_count == 9 and result.source_language_mismatch is False
    return tmp_path / "dicts" / "wty-tr-en" / "index.sqlite"


def _provider(index, keys):
    provider = IndexedDictProvider("wty-tr-en", index, keys=keys)
    assert provider.load()
    return provider


def test_the_fixture_carries_its_licence_and_source():
    assert "CC BY-SA 4.0" in FIXTURE["license"] and FIXTURE["index"]["sourceLanguage"] == "tr"
    assert FIXTURE["index"]["revision"] == "2026.09.19"
    assert sorted({row[0] for row in FIXTURE["term_rows"]}) == [
        "iyi",
        "kitap",
        "kitapları",
        "okumak",
        "İstanbul",
        "ışık",
    ]


@pytest.mark.parametrize(
    ("written", "row"),
    [
        ("IŞIK", "ışık"),
        ("Işık", "ışık"),
        ("İSTANBUL", "İstanbul"),
        ("istanbul", "İstanbul"),
        ("KİTAP", "kitap"),
        ("İyi", "iyi"),
    ],
)
def test_both_capital_is_find_their_rows(index, written, row):
    provider = _provider(index, get_profile("tr").dict_keys)
    assert provider.lookup(row) is not None and provider.lookup(written) == provider.lookup(row)


def test_the_locale_blind_fold_misses_them(index):
    """Negative control: a plain casefold queries IŞIK as işik and İSTANBUL with a combining dot."""
    provider = _provider(index, CasefoldDictKeys())
    assert provider.lookup("IŞIK") is None and provider.lookup("İSTANBUL") is None


def test_a_capital_dotless_i_is_not_the_dotted_one(index):
    """IYI is ıyı in Turkish, not iyi: the fold keeps the two letters apart."""
    assert _provider(index, get_profile("tr").dict_keys).lookup("IYI") is None


def test_the_mined_fronts_are_headwords(index):
    provider = _provider(index, get_profile("tr").dict_keys)
    assert all(provider.lookup(front) is not None for front in ("kitap", "okumak", "ışık", "iyi"))
    titles = set(re.findall(r'title="([^"]+)"', provider.lookup("iyi") or ""))
    assert {"adjective", "adverb", "interjection", "noun"} <= titles


def test_the_surface_rung_reaches_the_inflected_forms_row(index):
    """Plan decision 4: a wrong analyzer pick still finds the entry through the surface's non-lemma row."""
    provider = _provider(index, get_profile("tr").dict_keys)
    candidates = [text for text, _conditions in get_profile("tr").lookup.candidates("kitab", "KİTAPLARI", None)]
    assert candidates[0] == "KİTAPLARI"
    html = provider.lookup(candidates[0]) or ""
    assert "non-lemma" in html and "kitap" in html
