"""Tests for IndexedFreqProvider (runtime read of a per-source freq index)."""

from __future__ import annotations

from pathlib import Path

from anki_miner.services.frequency import storage
from anki_miner.services.frequency.providers.indexed_freq_provider import (
    IndexedFreqProvider,
)


def _build_source(
    root: Path, source_id: str, rows: list[storage.FreqRow], *, schema_version: int | None = None
) -> Path:
    """Build a real per-source index.sqlite under root/<source_id>/, return its db path."""
    db_path = root / source_id / "index.sqlite"
    meta = {
        "schema_version": str(storage.SCHEMA_VERSION if schema_version is None else schema_version),
        "format": "csv",
        "source_name": source_id,
        "entry_count": str(len(rows)),
    }
    storage.build_index(db_path, rows, meta)
    return db_path


def test_lookup_returns_rank(tmp_path: Path):
    db = _build_source(tmp_path, "jpdb", [("猫", "ねこ", 100), ("犬", "いぬ", 200)])
    provider = IndexedFreqProvider("jpdb", db, "JPDB")
    assert provider.load() is True
    assert provider.lookup("猫") == 100
    assert provider.lookup("犬") == 200


def test_lookup_min_over_homographs(tmp_path: Path):
    # Same term, two readings/ranks: MIN(rank) wins.
    db = _build_source(tmp_path, "jpdb", [("生", "なま", 500), ("生", "せい", 80)])
    provider = IndexedFreqProvider("jpdb", db, "JPDB")
    assert provider.load() is True
    assert provider.lookup("生") == 80


def test_lookup_missing_term_returns_none(tmp_path: Path):
    db = _build_source(tmp_path, "jpdb", [("猫", "ねこ", 100)])
    provider = IndexedFreqProvider("jpdb", db, "JPDB")
    assert provider.load() is True
    assert provider.lookup("存在しない") is None


def test_lookup_before_load_returns_none(tmp_path: Path):
    db = _build_source(tmp_path, "jpdb", [("猫", "ねこ", 100)])
    provider = IndexedFreqProvider("jpdb", db, "JPDB")
    assert provider.lookup("猫") is None  # not loaded yet


def test_reading_column_ignored_by_lookup(tmp_path: Path):
    # Looking up by the reading must NOT hit; key is term only.
    db = _build_source(tmp_path, "jpdb", [("猫", "ねこ", 100)])
    provider = IndexedFreqProvider("jpdb", db, "JPDB")
    assert provider.load() is True
    assert provider.lookup("ねこ") is None
    assert provider.lookup("猫") == 100


def test_lookup_many_matches_repeated_lookup(tmp_path: Path):
    db = _build_source(tmp_path, "jpdb", [("猫", "ねこ", 100), ("生", "せい", 80), ("生", "なま", 500)])
    provider = IndexedFreqProvider("jpdb", db, "JPDB")
    assert provider.load() is True
    terms = ["猫", "生", "存在しない"]
    expected = {t: provider.lookup(t) for t in terms}
    assert provider.lookup_many(terms) == expected
    assert provider.lookup_many(terms) == {"猫": 100, "生": 80, "存在しない": None}


def test_lookup_many_before_load(tmp_path: Path):
    db = _build_source(tmp_path, "jpdb", [("猫", "ねこ", 100)])
    provider = IndexedFreqProvider("jpdb", db, "JPDB")
    assert provider.lookup_many(["猫", "犬"]) == {"猫": None, "犬": None}


def test_load_false_on_schema_mismatch(tmp_path: Path):
    db = _build_source(tmp_path, "jpdb", [("猫", "ねこ", 100)], schema_version=storage.SCHEMA_VERSION + 99)
    provider = IndexedFreqProvider("jpdb", db, "JPDB")
    assert provider.load() is False
    assert provider.is_available() is False
    assert provider.lookup("猫") is None


def test_load_false_on_missing_db(tmp_path: Path):
    provider = IndexedFreqProvider("ghost", tmp_path / "ghost" / "index.sqlite", "Ghost")
    assert provider.load() is False
    assert provider.is_available() is False


def test_name_property(tmp_path: Path):
    db = _build_source(tmp_path, "jpdb", [("猫", "ねこ", 100)])
    provider = IndexedFreqProvider("jpdb", db, "JPDB Display")
    assert provider.name == "JPDB Display"


def test_is_available_reflects_load(tmp_path: Path):
    db = _build_source(tmp_path, "jpdb", [("猫", "ねこ", 100)])
    provider = IndexedFreqProvider("jpdb", db, "JPDB")
    assert provider.is_available() is False
    assert provider.load() is True
    assert provider.is_available() is True
