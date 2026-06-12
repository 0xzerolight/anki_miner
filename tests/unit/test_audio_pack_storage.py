"""Tests for audio pack SQLite storage layer."""

import json
import os
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from anki_miner.services.audio_packs.storage import (
    SCHEMA_VERSION,
    AudioEntry,
    AudioPackRow,
    bulk_insert,
    create_index,
    lookup,
    open_readonly,
    read_meta,
    read_meta_cached,
    write_meta,
)


class TestCreateIndex:
    def test_creates_tables_and_indexes(self, tmp_path: Path):
        db_path = tmp_path / "index.sqlite"
        create_index(db_path)

        with sqlite3.connect(db_path) as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            assert {"entries", "meta"} <= tables

            indexes = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
            assert "idx_expr_reading" in indexes

    def test_schema_version_is_1(self):
        assert SCHEMA_VERSION == 1

    def test_entries_table_columns(self, tmp_path: Path):
        db_path = tmp_path / "index.sqlite"
        create_index(db_path)

        with sqlite3.connect(db_path) as conn:
            cols = {row[1]: row for row in conn.execute("PRAGMA table_info(entries)")}
            assert set(cols) >= {"id", "expression", "reading", "source", "speaker", "display", "file"}
            # expression and source and file are NOT NULL
            assert cols["expression"][3] == 1
            assert cols["source"][3] == 1
            assert cols["file"][3] == 1
            # reading, speaker, display allow NULL
            assert cols["reading"][3] == 0
            assert cols["speaker"][3] == 0
            assert cols["display"][3] == 0

    def test_create_index_idempotent(self, tmp_path: Path):
        db_path = tmp_path / "index.sqlite"
        create_index(db_path)
        create_index(db_path)  # second call must not raise


class TestBulkInsertAndLookup:
    def test_insert_and_lookup_round_trip(self, tmp_path: Path):
        db_path = tmp_path / "index.sqlite"
        create_index(db_path)
        bulk_insert(
            db_path,
            [
                AudioPackRow(
                    expression="食べる",
                    reading="たべる",
                    source="nhk",
                    speaker="f1",
                    file="food/taberu.mp3",
                ),
            ],
        )

        conn = open_readonly(db_path)
        try:
            results = lookup(conn, "食べる", "たべる")
            assert len(results) == 1
            assert results[0].file == "food/taberu.mp3"
            assert results[0].source == "nhk"
            assert results[0].speaker == "f1"
        finally:
            conn.close()

    def test_bulk_insert_returns_count(self, tmp_path: Path):
        db_path = tmp_path / "index.sqlite"
        create_index(db_path)
        rows = [
            AudioPackRow(expression="犬", reading="いぬ", source="src", file="inu.mp3"),
            AudioPackRow(expression="猫", reading="ねこ", source="src", file="neko.mp3"),
            AudioPackRow(expression="魚", reading="さかな", source="src", file="sakana.mp3"),
        ]
        count = bulk_insert(db_path, rows)
        assert count == 3

    def test_lookup_miss_returns_empty(self, tmp_path: Path):
        db_path = tmp_path / "index.sqlite"
        create_index(db_path)
        conn = open_readonly(db_path)
        try:
            assert lookup(conn, "ない言葉", "") == []
        finally:
            conn.close()

    def test_lookup_returns_audio_entry_dataclass(self, tmp_path: Path):
        db_path = tmp_path / "index.sqlite"
        create_index(db_path)
        bulk_insert(
            db_path,
            [AudioPackRow(expression="水", reading="みず", source="nhk", speaker="m1", file="mizu.mp3")],
        )
        conn = open_readonly(db_path)
        try:
            results = lookup(conn, "水", "みず")
            assert isinstance(results, list)
            assert len(results) == 1
            entry = results[0]
            assert isinstance(entry, AudioEntry)
            assert entry.file == "mizu.mp3"
            assert entry.source == "nhk"
            assert entry.speaker == "m1"
        finally:
            conn.close()

    def test_lookup_multiple_entries_same_expression(self, tmp_path: Path):
        db_path = tmp_path / "index.sqlite"
        create_index(db_path)
        bulk_insert(
            db_path,
            [
                AudioPackRow(expression="橋", reading="はし", source="src1", speaker="f1", file="hashi1.mp3"),
                AudioPackRow(expression="橋", reading="はし", source="src2", speaker="m1", file="hashi2.mp3"),
            ],
        )
        conn = open_readonly(db_path)
        try:
            results = lookup(conn, "橋", "はし")
            assert len(results) == 2
            files = {r.file for r in results}
            assert files == {"hashi1.mp3", "hashi2.mp3"}
        finally:
            conn.close()

    def test_bulk_insert_batches_large_input(self, tmp_path: Path):
        db_path = tmp_path / "index.sqlite"
        create_index(db_path)
        rows = [AudioPackRow(expression=f"w{i}", reading=f"r{i}", source="src", file=f"{i}.mp3") for i in range(6000)]
        count = bulk_insert(db_path, rows, batch_size=1000)
        assert count == 6000


class TestNullReadingWildcard:
    """NULL-reading rows act as wildcards: they match any requested reading."""

    def test_null_reading_row_matches_any_requested_reading(self, tmp_path: Path):
        db_path = tmp_path / "index.sqlite"
        create_index(db_path)
        # Row has no reading (e.g. legacy forvo style)
        bulk_insert(
            db_path,
            [AudioPackRow(expression="犬", reading=None, source="forvo", file="inu.mp3")],
        )
        conn = open_readonly(db_path)
        try:
            # Even when caller passes a specific reading, NULL-reading row matches
            results = lookup(conn, "犬", "いぬ")
            assert len(results) == 1
            assert results[0].file == "inu.mp3"
        finally:
            conn.close()

    def test_null_reading_row_matches_empty_reading(self, tmp_path: Path):
        db_path = tmp_path / "index.sqlite"
        create_index(db_path)
        bulk_insert(
            db_path,
            [AudioPackRow(expression="犬", reading=None, source="forvo", file="inu.mp3")],
        )
        conn = open_readonly(db_path)
        try:
            results = lookup(conn, "犬", "")
            assert len(results) == 1
            assert results[0].file == "inu.mp3"
        finally:
            conn.close()

    def test_null_reading_row_matches_none_reading(self, tmp_path: Path):
        """lookup(conn, expr, None) should also wildcard-match NULL-reading rows."""
        db_path = tmp_path / "index.sqlite"
        create_index(db_path)
        bulk_insert(
            db_path,
            [AudioPackRow(expression="犬", reading=None, source="forvo", file="inu.mp3")],
        )
        conn = open_readonly(db_path)
        try:
            results = lookup(conn, "犬", None)
            assert len(results) == 1
        finally:
            conn.close()


class TestEmptyReadingWildcard:
    """Empty requested reading is a wildcard: returns all readings for expression."""

    def test_empty_reading_matches_rows_with_nonnull_reading(self, tmp_path: Path):
        db_path = tmp_path / "index.sqlite"
        create_index(db_path)
        bulk_insert(
            db_path,
            [
                AudioPackRow(expression="橋", reading="はし", source="nhk", file="hashi.mp3"),
                AudioPackRow(expression="橋", reading="きょう", source="nhk", file="kyou.mp3"),
            ],
        )
        conn = open_readonly(db_path)
        try:
            results = lookup(conn, "橋", "")
            assert len(results) == 2
        finally:
            conn.close()

    def test_empty_reading_returns_all_speakers(self, tmp_path: Path):
        db_path = tmp_path / "index.sqlite"
        create_index(db_path)
        bulk_insert(
            db_path,
            [
                AudioPackRow(expression="水", reading="みず", source="src", speaker="f1", file="mizu_f.mp3"),
                AudioPackRow(expression="水", reading="みず", source="src", speaker="m1", file="mizu_m.mp3"),
            ],
        )
        conn = open_readonly(db_path)
        try:
            results = lookup(conn, "水", "")
            assert len(results) == 2
            speakers = {r.speaker for r in results}
            assert speakers == {"f1", "m1"}
        finally:
            conn.close()


class TestExactReadingFilter:
    """Non-empty reading filters out rows with a different non-NULL reading."""

    def test_exact_reading_excludes_other_readings(self, tmp_path: Path):
        db_path = tmp_path / "index.sqlite"
        create_index(db_path)
        bulk_insert(
            db_path,
            [
                AudioPackRow(expression="上", reading="うえ", source="nhk", file="ue.mp3"),
                AudioPackRow(expression="上", reading="じょう", source="nhk", file="jou.mp3"),
                AudioPackRow(expression="上", reading=None, source="forvo", file="ue_forvo.mp3"),
            ],
        )
        conn = open_readonly(db_path)
        try:
            results = lookup(conn, "上", "うえ")
            files = {r.file for r in results}
            assert "ue.mp3" in files
            assert "ue_forvo.mp3" in files  # NULL-reading is a wildcard
            assert "jou.mp3" not in files
        finally:
            conn.close()

    def test_exact_reading_returns_only_matching_and_null(self, tmp_path: Path):
        db_path = tmp_path / "index.sqlite"
        create_index(db_path)
        bulk_insert(
            db_path,
            [
                AudioPackRow(expression="下", reading="した", source="nhk", file="shita.mp3"),
                AudioPackRow(expression="下", reading="か", source="nhk", file="ka.mp3"),
            ],
        )
        conn = open_readonly(db_path)
        try:
            results = lookup(conn, "下", "した")
            assert len(results) == 1
            assert results[0].file == "shita.mp3"
        finally:
            conn.close()


class TestDeterministicOrdering:
    """Results are ordered by id (insertion order) — fully deterministic."""

    def test_order_by_id(self, tmp_path: Path):
        db_path = tmp_path / "index.sqlite"
        create_index(db_path)
        # Insert in a specific order; results should come back same order
        bulk_insert(
            db_path,
            [
                AudioPackRow(expression="星", reading=None, source="src_c", file="c.mp3"),
                AudioPackRow(expression="星", reading=None, source="src_a", file="a.mp3"),
                AudioPackRow(expression="星", reading=None, source="src_b", file="b.mp3"),
            ],
        )
        conn = open_readonly(db_path)
        try:
            results = lookup(conn, "星", "")
            assert [r.file for r in results] == ["c.mp3", "a.mp3", "b.mp3"]
        finally:
            conn.close()


class TestOpenReadonly:
    def test_opens_db_with_hash_in_path(self, tmp_path: Path):
        weird_dir = tmp_path / "packs#frag"
        weird_dir.mkdir()
        db_path = weird_dir / "index.sqlite"
        create_index(db_path)
        bulk_insert(db_path, [AudioPackRow(expression="犬", reading="いぬ", source="src", file="inu.mp3")])

        conn = open_readonly(db_path)
        try:
            results = lookup(conn, "犬", "いぬ")
            assert len(results) == 1
        finally:
            conn.close()

    def test_connection_is_read_only(self, tmp_path: Path):
        db_path = tmp_path / "ro.sqlite"
        create_index(db_path)

        conn = open_readonly(db_path)
        try:
            with pytest.raises(sqlite3.OperationalError):
                conn.execute("INSERT INTO entries (expression, source, file) VALUES ('x', 'y', 'z')")
        finally:
            conn.close()


class TestMeta:
    def test_write_then_read(self, tmp_path: Path):
        db_path = tmp_path / "index.sqlite"
        create_index(db_path)
        write_meta(
            db_path,
            {
                "pack_id": "nhk16",
                "source": "NHK 2016",
                "format": "local-audio-yomichan",
                "entry_count": "55000",
                "schema_version": str(SCHEMA_VERSION),
                "pack_dir": "/data/packs/nhk16",
            },
        )

        meta = read_meta(db_path)
        assert meta["pack_id"] == "nhk16"
        assert meta["source"] == "NHK 2016"
        assert meta["format"] == "local-audio-yomichan"
        assert meta["entry_count"] == "55000"
        assert meta["schema_version"] == str(SCHEMA_VERSION)
        assert meta["pack_dir"] == "/data/packs/nhk16"

    def test_read_missing_file(self, tmp_path: Path):
        assert read_meta(tmp_path / "nonexistent.sqlite") == {}

    def test_write_meta_upserts(self, tmp_path: Path):
        db_path = tmp_path / "index.sqlite"
        create_index(db_path)
        write_meta(db_path, {"source": "old"})
        write_meta(db_path, {"source": "new"})
        meta = read_meta(db_path)
        assert meta["source"] == "new"


class TestReadMetaCached:
    """Sidecar cache for ``meta.json`` — skips SQLite open when fresh."""

    def _setup_pack(self, tmp_path: Path) -> Path:
        db_path = tmp_path / "index.sqlite"
        create_index(db_path)
        write_meta(
            db_path,
            {
                "pack_id": "nhk16",
                "source": "NHK 2016",
                "format": "local-audio-yomichan",
                "entry_count": "55000",
                "schema_version": str(SCHEMA_VERSION),
            },
        )
        return db_path

    def test_write_meta_creates_sidecar(self, tmp_path: Path):
        db_path = self._setup_pack(tmp_path)
        sidecar = db_path.parent / "meta.json"
        assert sidecar.is_file()
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        assert data["pack_id"] == "nhk16"
        assert data["entry_count"] == "55000"

    def test_cached_read_skips_sqlite_when_sidecar_fresh(self, tmp_path: Path):
        """Hot startup path must not open SQLite when sidecar is up to date."""
        db_path = self._setup_pack(tmp_path)
        with patch(
            "anki_miner.services.audio_packs.storage.read_meta",
            wraps=read_meta,
        ) as wrapped:
            meta = read_meta_cached(db_path)
        assert wrapped.call_count == 0
        assert meta["pack_id"] == "nhk16"

    def test_cached_read_falls_back_when_sidecar_missing(self, tmp_path: Path):
        db_path = self._setup_pack(tmp_path)
        sidecar = db_path.parent / "meta.json"
        sidecar.unlink()
        meta = read_meta_cached(db_path)
        assert meta["pack_id"] == "nhk16"
        # Fall-through rewrites the sidecar.
        assert sidecar.is_file()

    def test_cached_read_falls_back_when_sqlite_newer(self, tmp_path: Path):
        db_path = self._setup_pack(tmp_path)
        sidecar = db_path.parent / "meta.json"
        old = sidecar.stat().st_mtime - 100
        os.utime(sidecar, (old, old))
        with patch(
            "anki_miner.services.audio_packs.storage.read_meta",
            wraps=read_meta,
        ) as wrapped:
            read_meta_cached(db_path)
        assert wrapped.call_count == 1
        # Sidecar gets rewritten with current mtime.
        assert sidecar.stat().st_mtime > old

    def test_cached_read_handles_corrupt_sidecar(self, tmp_path: Path):
        db_path = self._setup_pack(tmp_path)
        sidecar = db_path.parent / "meta.json"
        sidecar.write_text("{not valid json", encoding="utf-8")
        meta = read_meta_cached(db_path)
        assert meta["pack_id"] == "nhk16"
        # Sidecar is rewritten with valid JSON.
        assert json.loads(sidecar.read_text(encoding="utf-8"))["pack_id"] == "nhk16"

    def test_cached_read_missing_db(self, tmp_path: Path):
        assert read_meta_cached(tmp_path / "nonexistent.sqlite") == {}
