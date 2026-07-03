"""Tests for the per-source frequency SQLite storage layer."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from anki_miner.services.frequency import storage


def _make_meta(entry_count: int = 2) -> dict[str, str]:
    return {
        "schema_version": str(storage.SCHEMA_VERSION),
        "format": "csv",
        "source_name": "Test",
        "source_revision": "",
        "import_date": "2026-01-01T00:00:00+00:00",
        "entry_count": str(entry_count),
    }


def build_v1_index(db_path: Path, rows: list[tuple[str, str | None, int]]) -> None:
    """Materialize a legacy v1 index (no ``display_value`` column, schema_version=1).

    Storage always writes the current (v2) schema, so a v1 fixture must be built
    with raw SQL. Shared by the backward-compatibility tests: a v1 index must
    stay fully readable after the 1→2 bump (display_value read as absent).
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            "CREATE TABLE entries (id INTEGER PRIMARY KEY, term TEXT NOT NULL, reading TEXT, rank INTEGER NOT NULL);"
            "CREATE INDEX idx_term ON entries(term);"
            "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);"
        )
        conn.executemany("INSERT INTO entries (term, reading, rank) VALUES (?, ?, ?)", rows)
        meta = {
            "schema_version": "1",
            "format": "csv",
            "source_name": db_path.parent.name,
            "entry_count": str(len(rows)),
        }
        for key, value in meta.items():
            conn.execute("INSERT INTO meta (key, value) VALUES (?, ?)", (key, value))
        conn.commit()
    finally:
        conn.close()


class TestBackwardCompatV1:
    """The 1→2 bump is additive; a v1 index must stay readable (see the registry
    and provider tests for the load/lookup gating that consumes this fixture)."""

    def test_v1_index_lacks_display_value_column(self, tmp_path: Path) -> None:
        db = tmp_path / "old" / "index.sqlite"
        build_v1_index(db, [("猫", "ねこ", 100)])
        conn = sqlite3.connect(db)
        try:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(entries)")]
        finally:
            conn.close()
        assert cols == ["id", "term", "reading", "rank"]
        assert storage.read_meta(db)["schema_version"] == "1"

    def test_v2_index_has_display_value_column(self, tmp_path: Path) -> None:
        db = tmp_path / "new" / "index.sqlite"
        storage.build_index(db, [("猫", "ねこ", 100, "100㋕")], _make_meta(1))
        conn = sqlite3.connect(db)
        try:
            got = conn.execute("SELECT display_value FROM entries").fetchall()
        finally:
            conn.close()
        assert got == [("100㋕",)]
        assert storage.read_meta(db)["schema_version"] == "2"


class TestSchema:
    def test_schema_version_is_two(self) -> None:
        assert storage.SCHEMA_VERSION == 2

    def test_create_index_creates_tables(self, tmp_path: Path) -> None:
        db = tmp_path / "index.sqlite"
        storage.create_index(db)
        conn = sqlite3.connect(db)
        try:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            assert {"entries", "meta"} <= tables
            cols = [r[1] for r in conn.execute("PRAGMA table_info(entries)")]
            assert cols == ["id", "term", "reading", "rank", "display_value"]
            indexes = {r[1] for r in conn.execute("PRAGMA index_list(entries)")}
            assert any("idx_term" in name for name in indexes)
        finally:
            conn.close()

    def test_create_index_idempotent(self, tmp_path: Path) -> None:
        db = tmp_path / "index.sqlite"
        storage.create_index(db)
        storage.create_index(db)  # no error


class TestBulkInsert:
    def test_inserts_rows_with_reading_and_display_value(self, tmp_path: Path) -> None:
        db = tmp_path / "index.sqlite"
        storage.create_index(db)
        rows = [("term", "よみ", 1, "1/9000"), ("plain", None, 2, None)]
        n = storage.bulk_insert(db, rows)
        assert n == 2
        conn = sqlite3.connect(db)
        try:
            got = conn.execute("SELECT term, reading, rank, display_value FROM entries ORDER BY rank").fetchall()
        finally:
            conn.close()
        assert got == [("term", "よみ", 1, "1/9000"), ("plain", None, 2, None)]

    def test_build_index_writes_rows_and_meta(self, tmp_path: Path) -> None:
        db = tmp_path / "index.sqlite"
        n = storage.build_index(db, [("a", None, 1, None), ("b", "び", 2, "2位")], _make_meta())
        assert n == 2
        assert storage.read_meta(db)["entry_count"] == "2"


class TestMeta:
    def test_write_then_read_meta(self, tmp_path: Path) -> None:
        db = tmp_path / "index.sqlite"
        storage.create_index(db)
        meta = _make_meta(5)
        storage.write_meta(db, meta)
        assert storage.read_meta(db) == meta

    def test_read_meta_missing_db_returns_empty(self, tmp_path: Path) -> None:
        assert storage.read_meta(tmp_path / "nope.sqlite") == {}

    def test_write_meta_creates_sidecar(self, tmp_path: Path) -> None:
        db = tmp_path / "index.sqlite"
        storage.create_index(db)
        meta = _make_meta()
        storage.write_meta(db, meta)
        sidecar = tmp_path / "meta.json"
        assert sidecar.is_file()
        assert json.loads(sidecar.read_text(encoding="utf-8")) == meta

    def test_read_meta_cached_returns_written_meta(self, tmp_path: Path) -> None:
        db = tmp_path / "index.sqlite"
        storage.create_index(db)
        meta = _make_meta(3)
        storage.write_meta(db, meta)
        assert storage.read_meta_cached(db) == meta

    def test_read_meta_cached_uses_sidecar_when_present(self, tmp_path: Path) -> None:
        db = tmp_path / "index.sqlite"
        storage.create_index(db)
        storage.write_meta(db, _make_meta())
        # Overwrite the sidecar with a sentinel and bump its mtime past the db's
        # so the cached read trusts the sidecar over the sqlite table.
        sidecar = tmp_path / "meta.json"
        sidecar.write_text(json.dumps({"sentinel": "yes"}), encoding="utf-8")
        import os
        import time

        future = time.time() + 10
        os.utime(sidecar, (future, future))
        assert storage.read_meta_cached(db) == {"sentinel": "yes"}

    def test_read_meta_cached_falls_back_when_db_newer(self, tmp_path: Path) -> None:
        db = tmp_path / "index.sqlite"
        storage.create_index(db)
        storage.write_meta(db, _make_meta())
        sidecar = tmp_path / "meta.json"
        # Make the sidecar stale (older than the db) -> falls back to sqlite.
        import os

        old = db.stat().st_mtime - 100
        os.utime(sidecar, (old, old))
        got = storage.read_meta_cached(db)
        assert got["format"] == "csv"

    def test_read_meta_cached_missing_db_returns_empty(self, tmp_path: Path) -> None:
        assert storage.read_meta_cached(tmp_path / "nope.sqlite") == {}
