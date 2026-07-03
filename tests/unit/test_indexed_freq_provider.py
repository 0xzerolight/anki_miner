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
    # Same term, two readings/ranks, NO reading given: MIN(rank) wins (legacy).
    db = _build_source(tmp_path, "jpdb", [("生", "なま", 500), ("生", "せい", 80)])
    provider = IndexedFreqProvider("jpdb", db, "JPDB")
    assert provider.load() is True
    assert provider.lookup("生") == 80


def test_lookup_reading_scoped_picks_exact_reading(tmp_path: Path):
    # 方: かた (rare, rank 2000) must NOT inherit ほう's (common, rank 30) rank.
    db = _build_source(tmp_path, "jpdb", [("方", "かた", 2000), ("方", "ほう", 30)])
    provider = IndexedFreqProvider("jpdb", db, "JPDB")
    assert provider.load() is True
    assert provider.lookup("方", "かた") == 2000
    assert provider.lookup("方", "ほう") == 30
    # No reading → legacy term-only MIN.
    assert provider.lookup("方") == 30


def test_lookup_reading_scoped_bare_row_applies_to_all_readings(tmp_path: Path):
    # A reading-less (NULL) row applies to every reading (Yomitan bare-row rule).
    db = _build_source(tmp_path, "csv", [("走る", None, 100)])
    provider = IndexedFreqProvider("csv", db, "CSV")
    assert provider.load() is True
    assert provider.lookup("走る", "はしる") == 100
    assert provider.lookup("走る", "でたらめ") == 100


def test_lookup_reading_scoped_prefers_exact_over_bare(tmp_path: Path):
    # Exact-reading row wins over a bare row; an unknown reading falls to bare.
    db = _build_source(tmp_path, "mix", [("term", "よみ", 50), ("term", None, 200)])
    provider = IndexedFreqProvider("mix", db, "MIX")
    assert provider.load() is True
    assert provider.lookup("term", "よみ") == 50
    assert provider.lookup("term", "ちがう") == 200
    assert provider.lookup("term") == 50  # no reading → MIN(50, 200)


def test_lookup_reading_scoped_falls_back_to_term_min(tmp_path: Path):
    # No exact match and no bare row → term-only MIN (reading-less compat path).
    db = _build_source(tmp_path, "jpdb", [("生", "なま", 500), ("生", "せい", 80)])
    provider = IndexedFreqProvider("jpdb", db, "JPDB")
    assert provider.load() is True
    assert provider.lookup("生", "き") == 80


def test_lookup_reading_scoped_normalizes_katakana_both_sides(tmp_path: Path):
    # BCCWJ envelopes may store katakana readings; a hiragana query must match,
    # and a katakana query against a hiragana store must match too.
    db = _build_source(tmp_path, "bccwj", [("生", "ナマ", 500), ("生", "せい", 80)])
    provider = IndexedFreqProvider("bccwj", db, "BCCWJ")
    assert provider.load() is True
    assert provider.lookup("生", "なま") == 500  # hiragana query vs katakana store
    assert provider.lookup("生", "ナマ") == 500  # katakana query vs katakana store
    assert provider.lookup("生", "セイ") == 80  # katakana query vs hiragana store


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


def test_lookup_many_reading_scoped_matches_repeated_lookup(tmp_path: Path):
    db = _build_source(tmp_path, "jpdb", [("方", "かた", 2000), ("方", "ほう", 30), ("生", "せい", 80)])
    provider = IndexedFreqProvider("jpdb", db, "JPDB")
    assert provider.load() is True
    terms = ["方", "方", "生"]
    readings = ["かた", "ほう", "せい"]
    # Duplicate term "方" with different readings: last reading (ほう→30) wins,
    # exactly as {t: lookup(t, r) for t, r in zip(...)} would collapse the dict.
    assert provider.lookup_many(terms, readings) == {"方": 30, "生": 80}


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
