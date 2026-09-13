"""S19: a dictionary that declares another language gets a receipt note, never a refusal."""

from __future__ import annotations

from pathlib import Path

import pytest

from anki_miner.gui.controllers.dictionary_import_flow import DictionaryImportFlow
from anki_miner.services.dictionary.importers.yomitan_importer import declares_other_language, import_yomitan_zip
from tests.fixtures.dictionary.build_yomitan_fixture import build_yomitan_zip
from tests.unit.languages.stub_registry import register_stub_profile


@pytest.mark.parametrize(
    ("source", "code", "wiktionary", "expected"),
    [
        ("", "ja", "", False),
        ("ja", "ja", "", False),
        ("JA", "ja", "", False),
        ("en", "ja", "", True),
        ("en-US", "en", "", False),
        ("sh", "hr", "sh", False),
        ("hr", "hr", "sh", False),
        ("sr", "hr", "sh", True),
    ],
)
def test_primary_subtag_is_compared_with_code_and_wiktionary_code(source, code, wiktionary, expected):
    assert declares_other_language(source, code, wiktionary) is expected


def test_import_records_the_declared_language(tmp_path: Path):
    zip_path = build_yomitan_zip(tmp_path / "en.zip", index_extra={"sourceLanguage": "en"})

    result = import_yomitan_zip(zip_path, tmp_path / "dicts")

    assert (result.source_language, result.source_language_mismatch) == ("en", True)


def test_a_dictionary_declaring_nothing_gets_no_note(tmp_path: Path):
    result = import_yomitan_zip(build_yomitan_zip(tmp_path / "ja.zip"), tmp_path / "dicts")
    assert (result.source_language, result.source_language_mismatch) == ("", False)


def test_the_wiktionary_code_counts_as_the_active_language(tmp_path: Path, monkeypatch):
    register_stub_profile(monkeypatch, "zh", wiktionary_code="sh")
    zip_path = build_yomitan_zip(tmp_path / "sh.zip", index_extra={"sourceLanguage": "sh"})

    result = import_yomitan_zip(zip_path, tmp_path / "dicts", language="zh")

    assert result.source_language_mismatch is False


def test_the_receipt_names_the_declared_language():
    note = DictionaryImportFlow._import_notes(None, {"source_language": "en", "source_language_mismatch": True})
    assert "en" in note
    assert DictionaryImportFlow._import_notes(None, {"source_language": "ja", "source_language_mismatch": False}) == ""
