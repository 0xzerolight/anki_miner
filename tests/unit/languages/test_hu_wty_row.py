"""Real wty-hu-en rows import under the Hungarian keys, and the preverb rung reaches the bare verb (E.5, E.2.8)."""

from __future__ import annotations

import json
import unicodedata
import zipfile
from pathlib import Path

import pytest

from anki_miner.languages.registry import get_profile
from anki_miner.services.dictionary.importers.yomitan_importer import import_yomitan_zip
from anki_miner.services.dictionary.providers.indexed_provider import IndexedDictProvider

FIXTURE = json.loads((Path(__file__).parents[2] / "fixtures" / "hu" / "wty_row.json").read_text(encoding="utf-8"))
RENDERED = FIXTURE["rendered_html"]


@pytest.fixture
def provider(tmp_path):
    archive = tmp_path / "wty-hu-en.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("index.json", json.dumps(FIXTURE["index"]))
        zf.writestr("tag_bank_1.json", json.dumps(FIXTURE["tag_bank"]))
        zf.writestr("term_bank_1.json", json.dumps(FIXTURE["term_rows"]))
    import_yomitan_zip(archive, tmp_path / "dicts", dict_id="wty-hu-en", language="hu")
    provider = IndexedDictProvider(
        "wty-hu-en", tmp_path / "dicts" / "wty-hu-en" / "index.sqlite", keys=get_profile("hu").dict_keys
    )
    assert provider.load()
    return provider


def test_the_fixture_carries_its_licence_and_source():
    assert "CC BY-SA 4.0" in FIXTURE["license"] and FIXTURE["index"]["sourceLanguage"] == "hu"
    assert FIXTURE["index"]["revision"] == "2026.08.29"


def test_every_row_renders_the_committed_html_under_the_hungarian_keys(provider):
    for word, html in RENDERED.items():
        assert provider.lookup(word) == html, word


@pytest.mark.parametrize(
    ("written", "row"),
    [
        ("Ház", "ház"),  # a sentence-initial capital finds the row
        ("ŰRHAJÓ", "űrhajó"),  # an all-caps cue does too: casefold, never diacritic-strip
        ("Őszinte", "őszinte"),
        (unicodedata.normalize("NFD", "űrhajó"), "űrhajó"),  # a decomposed key composes to the same row
    ],
)
def test_a_capitalised_or_decomposed_front_finds_its_row(provider, written, row):
    assert provider.lookup(written) == RENDERED[row]


def test_a_diacritic_stripped_front_finds_nothing(provider):
    """R35: ő and ű are vowels of their own — urhajo is a different word, not a spelling variant."""
    assert provider.lookup("urhajo") is None and provider.lookup("oszinte") is None


def test_the_joined_preverb_verb_has_its_own_row(provider):
    assert provider.lookup("elolvas") == RENDERED["elolvas"]


def test_the_rung_reaches_the_bare_verb_for_a_preverb_verb_the_dictionary_lacks(provider):
    """The wty gap the rung exists for: elkap is absent, kap is present."""
    assert provider.lookup("elkap") is None
    candidates = [text for text, _conditions in get_profile("hu").lookup.candidates("elkap", "elkapta", None)]
    assert candidates[0] == "elkapta" and "kap" in candidates
    hit = next(provider.lookup(text) for text in candidates if provider.lookup(text) is not None)
    assert hit == RENDERED["kap"]


def test_the_inflected_row_the_dictionary_really_carries_is_reachable(provider):
    """wty-hu-en happens to carry házakban as its own entry; the front ház hits directly anyway."""
    assert provider.lookup("házakban") is not None and provider.lookup("ház") == RENDERED["ház"]
