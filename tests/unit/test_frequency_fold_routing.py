"""S4: frequency keys fold through the language's fold_term at both ends."""

from __future__ import annotations

import dataclasses
import sqlite3
import unicodedata
from pathlib import Path

from anki_miner.config import AnkiMinerConfig, FreqEntry
from anki_miner.services.frequency import storage
from anki_miner.services.frequency.providers.indexed_freq_provider import IndexedFreqProvider
from anki_miner.services.frequency.registry import FrequencySourceRegistry
from anki_miner.services.frequency.source_importer import import_frequency_source
from tests.unit.languages.stub_registry import register_stub_profile


class _CasefoldKeys:
    def fold_term(self, s: str) -> str:
        return unicodedata.normalize("NFC", s).casefold()

    def fold_reading(self, s: str | None) -> str | None:
        return None if s is None else unicodedata.normalize("NFC", s)

    def homograph_keep_mask(self, word, rows, lemma=None):
        return [True] * len(rows)


def _import(tmp_path: Path, text: str, language: str):
    csv_path = tmp_path / "list.csv"
    csv_path.write_text(text, encoding="utf-8")
    return import_frequency_source(csv_path, tmp_path / "freqs", language=language)


def _provider(tmp_path: Path, source_id: str, **kwargs) -> IndexedFreqProvider:
    return IndexedFreqProvider(source_id, tmp_path / "freqs" / source_id / "index.sqlite", "x", **kwargs)


def test_import_and_lookup_fold_symmetrically(tmp_path, monkeypatch):
    keys = _CasefoldKeys()
    register_stub_profile(monkeypatch, "zh", dict_keys=keys)
    result = _import(tmp_path, "Der,1\nUND,2\n", "zh")

    provider = _provider(tmp_path, result.source_id, keys=keys)
    assert provider.load()

    assert provider.lookup("der") == 1 and provider.lookup("DER") == 1
    assert provider.lookup_detail_many([("und", None), ("Und", None)]) == [(2, None), (2, None)]


def test_folded_duplicates_collapse_to_one_row(tmp_path, monkeypatch):
    register_stub_profile(monkeypatch, "zh", dict_keys=_CasefoldKeys())
    result = _import(tmp_path, "Der,1\nder,5\n", "zh")

    assert result.entry_count == 1


def test_japanese_keys_stay_nfc(tmp_path):
    nfd = unicodedata.normalize("NFD", "ガッコウ")
    result = _import(tmp_path, f"{nfd},7\n", "ja")

    provider = _provider(tmp_path, result.source_id)
    assert provider.load()

    assert provider.lookup(unicodedata.normalize("NFC", "ガッコウ")) == 7
    assert provider.lookup(nfd) == 7


def test_japanese_rows_are_stored_byte_identical_to_nfc(tmp_path):
    result = _import(tmp_path, "Der,1\n", "ja")

    with sqlite3.connect(tmp_path / "freqs" / result.source_id / "index.sqlite") as conn:
        assert conn.execute("SELECT term FROM entries").fetchone()[0] == "Der"


def test_storage_defaults_to_nfc(tmp_path):
    db = tmp_path / "index.sqlite"
    storage.create_index(db)
    storage.bulk_insert(db, [(unicodedata.normalize("NFD", "é"), None, 1, None)])

    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT term FROM entries").fetchone()[0] == "é"


def test_registry_hands_the_profile_keys_to_each_provider(tmp_path, monkeypatch):
    keys = _CasefoldKeys()
    register_stub_profile(monkeypatch, "zh", dict_keys=keys)
    result = _import(tmp_path, "Der,1\n", "zh")
    config = dataclasses.replace(
        AnkiMinerConfig(),
        language="zh",
        freqs_root=tmp_path / "freqs",
        frequency_chain=(FreqEntry(source_id=result.source_id),),
    )
    registry = FrequencySourceRegistry(config.freqs_root)
    registry.load()

    (provider,) = registry.build_sources(config)

    assert provider._fold_term == keys.fold_term
