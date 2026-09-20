"""Real wty-ar-en rows imported and queried with the Arabic key fold (spec C.1 dict keys, hooks)."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from anki_miner.languages.ar.render import arabic_grammar_line
from anki_miner.languages.registry import get_profile
from anki_miner.services.dictionary.importers.yomitan_importer import import_yomitan_zip
from anki_miner.services.dictionary.providers.indexed_provider import IndexedDictProvider

WTY = json.loads(
    (Path(__file__).resolve().parents[2] / "fixtures" / "ar" / "wty_rows.json").read_text(encoding="utf-8")
)


@pytest.fixture(scope="module")
def provider(tmp_path_factory) -> IndexedDictProvider:
    tmp = tmp_path_factory.mktemp("wty_ar")
    archive = tmp / "wty.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("index.json", json.dumps(WTY["index"]))
        zf.writestr("tag_bank_1.json", json.dumps(WTY["tag_bank"]))
        zf.writestr("term_bank_1.json", json.dumps(WTY["term_rows"]))
    import_yomitan_zip(archive, tmp / "dicts", dict_id="wty-ar-en", language="ar")
    found = IndexedDictProvider(
        "wty-ar-en", tmp / "dicts" / "wty-ar-en" / "index.sqlite", keys=get_profile("ar").dict_keys
    )
    assert found.load()
    return found


@pytest.mark.parametrize(
    ("word", "line"),
    [
        ("\u0643\u062a\u0627\u0628", "m (plural \u0643\u064f\u062a\u064f\u0628)"),  # kitaab
        (
            "\u0645\u062f\u0631\u0633\u0629",
            "f (plural \u0645\u064e\u062f\u0652\u0631\u064e\u0633\u064e\u0627\u062a or \u0645\u064e\u062f\u064e\u0627\u0631\u0650\u0633)",
        ),  # madrasa
        (
            "\u0630\u0647\u0628",
            "I (non-past \u064a\u064e\u0630\u0652\u0647\u064e\u0628\u064f, verbal noun \u0630\u064e\u0647\u064e\u0627\u0628 or \u0645\u064e\u0630\u0652\u0647\u064e\u0628)",
        ),  # dhahaba
        ("\u0637\u0644\u0627\u0628", ""),  # tullaab: a form-of row has no head line
    ],
)
def test_the_grammar_line_on_real_rows(provider, word, line):
    assert arabic_grammar_line(provider.lookup(word) or "") == line


def test_a_vocalised_query_and_a_bare_one_meet_the_same_rows(provider):
    bare = provider.lookup("\u0643\u062a\u0627\u0628")  # kitaab
    assert bare and provider.lookup("\u0643\u0650\u062a\u064e\u0627\u0628") == bare


def test_a_broken_plural_resolves_through_its_form_of_row(provider):
    assert "\u0637\u064e\u0627\u0644\u0650\u0628" in (
        provider.lookup("\u0637\u0644\u0627\u0628") or ""
    )  # tullaab -> taalib
