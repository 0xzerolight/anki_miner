"""Tests for IndexedFreqProvider (runtime read of a per-source freq index)."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import pytest

from anki_miner.services.frequency import storage
from anki_miner.services.frequency.providers import indexed_freq_provider as ifp_module
from anki_miner.services.frequency.providers.indexed_freq_provider import (
    IndexedFreqProvider,
)
from tests.unit.test_freq_storage import build_v1_index


def _build_source(root: Path, source_id: str, rows: list[tuple], *, schema_version: int | None = None) -> Path:
    """Build a real per-source index.sqlite under root/<source_id>/, return its db path.

    ``rows`` may be 3-tuples ``(term, reading, rank)`` — padded to the v2 shape
    with ``display_value=None`` — or full 4-tuples.
    """
    db_path = root / source_id / "index.sqlite"
    meta = {
        "schema_version": str(storage.SCHEMA_VERSION if schema_version is None else schema_version),
        "format": "csv",
        "source_name": source_id,
        "entry_count": str(len(rows)),
    }
    padded: list[storage.FreqRow] = [row if len(row) == 4 else (*row, None) for row in rows]
    storage.build_index(db_path, padded, meta)
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


def test_lookup_detail_returns_rank_and_display(tmp_path: Path):
    db = _build_source(tmp_path, "jpdb", [("猫", "ねこ", 1099, "1099/72000"), ("犬", "いぬ", 200, None)])
    provider = IndexedFreqProvider("jpdb", db, "JPDB")
    assert provider.load() is True
    assert provider.lookup_detail("猫") == (1099, "1099/72000")
    assert provider.lookup_detail("犬") == (200, None)
    assert provider.lookup_detail("存在しない") is None
    # lookup() stays rank-only.
    assert provider.lookup("猫") == 1099


def test_lookup_detail_reading_scoped_picks_winning_rows_display(tmp_path: Path):
    db = _build_source(
        tmp_path,
        "jpdb",
        [("方", "かた", 2000, "2000㋕"), ("方", "ほう", 30, "30㋕")],
    )
    provider = IndexedFreqProvider("jpdb", db, "JPDB")
    assert provider.load() is True
    assert provider.lookup_detail("方", "かた") == (2000, "2000㋕")
    assert provider.lookup_detail("方", "ほう") == (30, "30㋕")


def test_lookup_detail_before_load_returns_none(tmp_path: Path):
    db = _build_source(tmp_path, "jpdb", [("猫", "ねこ", 100, "x")])
    provider = IndexedFreqProvider("jpdb", db, "JPDB")
    assert provider.lookup_detail("猫") is None


def test_v1_index_loads_and_reads_with_absent_display(tmp_path: Path):
    # A legacy v1 index (no display_value column) must load after the 1->2 bump
    # and read as before, with display_value reported absent.
    db = tmp_path / "old" / "index.sqlite"
    build_v1_index(db, [("猫", "ねこ", 100), ("生", "せい", 80), ("生", "なま", 500)])
    provider = IndexedFreqProvider("old", db, "Old")
    assert provider.load() is True
    assert provider.is_available() is True
    assert provider.lookup("猫") == 100
    assert provider.lookup("生", "なま") == 500  # reading-scoping still works on v1
    assert provider.lookup_detail("猫") == (100, None)  # display absent on v1


# ---------------------------------------------------------------------------
# lookup_detail_many (batched detail lookups)
# ---------------------------------------------------------------------------


class _FlakyConn:
    """Delegates to a real connection, raising DatabaseError on chosen execute calls."""

    def __init__(self, real: sqlite3.Connection, fail_on: set[int]):
        self._real = real
        self._fail_on = fail_on
        self._calls = 0

    def execute(self, *args, **kwargs):
        self._calls += 1
        if self._calls in self._fail_on:
            raise sqlite3.DatabaseError("chunk boom")
        return self._real.execute(*args, **kwargs)


def test_lookup_detail_many_matches_repeated_lookup_detail(tmp_path: Path):
    db = _build_source(
        tmp_path,
        "jpdb",
        [
            ("猫", "ねこ", 1099, "1099/72000"),
            ("生", "せい", 80),
            ("生", "なま", 500),
            ("犬", None, 200),
        ],
    )
    provider = IndexedFreqProvider("jpdb", db, "JPDB")
    assert provider.load() is True
    pairs = [
        ("猫", "ねこ"),
        ("生", "せい"),
        ("生", "なま"),
        ("犬", "いぬ"),
        ("存在しない", None),
        ("猫", None),
    ]
    assert provider.lookup_detail_many(pairs) == [provider.lookup_detail(t, r) for t, r in pairs]


def test_lookup_detail_many_duplicate_term_distinct_readings(tmp_path: Path):
    # The dict-keyed lookup_many collapsed duplicate terms (last reading won);
    # the parallel-list API must resolve each pair independently.
    db = _build_source(tmp_path, "jpdb", [("方", "かた", 2000), ("方", "ほう", 30)])
    provider = IndexedFreqProvider("jpdb", db, "JPDB")
    assert provider.load() is True
    assert provider.lookup_detail_many([("方", "かた"), ("方", "ほう")]) == [(2000, None), (30, None)]


def test_lookup_detail_many_reading_cascade_tiers(tmp_path: Path):
    # Per pair: exact-reading row → bare row → term-only MIN, same as lookup_detail.
    db = _build_source(
        tmp_path,
        "mix",
        [("term", "よみ", 50), ("term", None, 200), ("生", "なま", 500), ("生", "せい", 80)],
    )
    provider = IndexedFreqProvider("mix", db, "MIX")
    assert provider.load() is True
    pairs = [("term", "よみ"), ("term", "ちがう"), ("term", None), ("生", "き")]
    assert provider.lookup_detail_many(pairs) == [(50, None), (200, None), (50, None), (80, None)]


def test_lookup_detail_many_katakana_normalized_both_sides(tmp_path: Path):
    db = _build_source(tmp_path, "bccwj", [("生", "ナマ", 500), ("生", "せい", 80)])
    provider = IndexedFreqProvider("bccwj", db, "BCCWJ")
    assert provider.load() is True
    pairs = [("生", "なま"), ("生", "ナマ"), ("生", "セイ")]
    assert provider.lookup_detail_many(pairs) == [(500, None), (500, None), (80, None)]


def test_lookup_detail_many_before_load_all_none(tmp_path: Path):
    db = _build_source(tmp_path, "jpdb", [("猫", "ねこ", 100)])
    provider = IndexedFreqProvider("jpdb", db, "JPDB")
    assert provider.lookup_detail_many([("猫", None), ("犬", None)]) == [None, None]


def test_lookup_detail_many_empty_pairs(tmp_path: Path):
    db = _build_source(tmp_path, "jpdb", [("猫", "ねこ", 100)])
    provider = IndexedFreqProvider("jpdb", db, "JPDB")
    assert provider.load() is True
    assert provider.lookup_detail_many([]) == []


def test_lookup_detail_many_v1_index_display_none(tmp_path: Path):
    db = tmp_path / "legacy" / "index.sqlite"
    build_v1_index(db, [("猫", "ねこ", 100), ("生", "せい", 80)])
    provider = IndexedFreqProvider("legacy", db, "Legacy")
    assert provider.load() is True
    assert provider.lookup_detail_many([("猫", "ねこ"), ("生", "せい")]) == [(100, None), (80, None)]


def test_lookup_detail_many_chunking_equivalence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    rows = [(f"語{i}", f"よみ{i}", 10 * (i + 1)) for i in range(5)]
    db = _build_source(tmp_path, "jpdb", rows)
    provider = IndexedFreqProvider("jpdb", db, "JPDB")
    assert provider.load() is True
    monkeypatch.setattr(ifp_module, "_BIND_CHUNK", 2)
    pairs = [(t, r) for t, r, _rank in rows] + [("存在しない", None)]
    assert provider.lookup_detail_many(pairs) == [provider.lookup_detail(t, r) for t, r in pairs]


def test_lookup_detail_many_database_error_per_chunk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    # 5 unique terms at _BIND_CHUNK=2 → chunks [t0,t1] [t2,t3] [t4]; fail the
    # SECOND chunk's query only: its pairs resolve None, other chunks intact.
    rows = [(f"語{i}", f"よみ{i}", 10 * (i + 1)) for i in range(5)]
    db = _build_source(tmp_path, "jpdb", rows)
    provider = IndexedFreqProvider("jpdb", db, "JPDB")
    assert provider.load() is True
    monkeypatch.setattr(ifp_module, "_BIND_CHUNK", 2)
    assert provider._conn is not None
    provider._conn = _FlakyConn(provider._conn, fail_on={2})  # type: ignore[assignment]
    pairs = [(t, r) for t, r, _rank in rows]
    with caplog.at_level(logging.WARNING):
        result = provider.lookup_detail_many(pairs)
    assert result == [(10, None), (20, None), None, None, (50, None)]
    warnings = [r for r in caplog.records if "lookup_detail_many" in r.getMessage()]
    assert len(warnings) == 1
    assert "jpdb" in warnings[0].getMessage()
