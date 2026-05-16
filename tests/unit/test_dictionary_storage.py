"""Tests for dictionary SQLite storage layer."""

import sqlite3
from pathlib import Path

from anki_miner.services.dictionary.storage import (
    SCHEMA_VERSION,
    DictRow,
    bulk_insert,
    create_index,
    lookup,
    open_readonly,
    read_meta,
    write_meta,
)


class TestCreateIndex:
    def test_creates_tables_and_indexes(self, tmp_path: Path):
        db_path = tmp_path / "test.sqlite"
        create_index(db_path)

        with sqlite3.connect(db_path) as conn:
            tables = {
                row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            assert {"entries", "meta"} <= tables

            indexes = {
                row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
            }
            assert "idx_term" in indexes
            assert "idx_reading" in indexes

    def test_schema_version_is_2(self):
        assert SCHEMA_VERSION == 2

    def test_entries_table_has_tags_column(self, tmp_path: Path):
        db_path = tmp_path / "test.sqlite"
        create_index(db_path)

        with sqlite3.connect(db_path) as conn:
            cols = {row[1]: row for row in conn.execute("PRAGMA table_info(entries)")}
            assert "tags" in cols
            # PRAGMA table_info row: (cid, name, type, notnull, dflt_value, pk)
            tags_col = cols["tags"]
            assert tags_col[2] == "TEXT"
            assert tags_col[3] == 1  # NOT NULL
            assert tags_col[4] == "''"  # default empty string


class TestBulkInsertAndLookup:
    def test_insert_and_lookup_by_term(self, tmp_path: Path):
        db_path = tmp_path / "test.sqlite"
        create_index(db_path)
        bulk_insert(
            db_path,
            [
                DictRow(term="食べる", reading="たべる", content="<div>to eat</div>", sequence=1),
                DictRow(term="飲む", reading="のむ", content="<div>to drink</div>", sequence=2),
            ],
        )

        conn = open_readonly(db_path)
        try:
            results = lookup(conn, "食べる")
            assert results == [("<div>to eat</div>", "")]
        finally:
            conn.close()

    def test_lookup_by_reading_fallback(self, tmp_path: Path):
        db_path = tmp_path / "test.sqlite"
        create_index(db_path)
        bulk_insert(
            db_path,
            [DictRow(term="食べる", reading="たべる", content="<div>to eat</div>", sequence=1)],
        )

        conn = open_readonly(db_path)
        try:
            results = lookup(conn, "たべる")
            assert results == [("<div>to eat</div>", "")]
        finally:
            conn.close()

    def test_lookup_multi_row_homograph(self, tmp_path: Path):
        db_path = tmp_path / "test.sqlite"
        create_index(db_path)
        bulk_insert(
            db_path,
            [
                DictRow(term="橋", reading="はし", content="<div>bridge</div>", sequence=1),
                DictRow(term="箸", reading="はし", content="<div>chopsticks</div>", sequence=2),
            ],
        )

        conn = open_readonly(db_path)
        try:
            results = lookup(conn, "はし")
            contents = [content for content, _tags in results]
            assert "<div>bridge</div>" in contents
            assert "<div>chopsticks</div>" in contents
        finally:
            conn.close()

    def test_lookup_term_priority_over_reading(self, tmp_path: Path):
        """Exact term match should sort before reading-only match."""
        db_path = tmp_path / "test.sqlite"
        create_index(db_path)
        bulk_insert(
            db_path,
            [
                DictRow(term="A", reading="はし", content="<div>reading-match</div>", sequence=1),
                DictRow(term="はし", reading=None, content="<div>term-match</div>", sequence=2),
            ],
        )

        conn = open_readonly(db_path)
        try:
            results = lookup(conn, "はし")
            assert results[0] == ("<div>term-match</div>", "")
        finally:
            conn.close()

    def test_lookup_miss_returns_empty(self, tmp_path: Path):
        db_path = tmp_path / "test.sqlite"
        create_index(db_path)

        conn = open_readonly(db_path)
        try:
            assert lookup(conn, "ない言葉") == []
        finally:
            conn.close()

    def test_lookup_returns_list_of_tuples(self, tmp_path: Path):
        """lookup return type is list[tuple[str, str]] — shape, length, types."""
        db_path = tmp_path / "test.sqlite"
        create_index(db_path)
        bulk_insert(
            db_path,
            [
                DictRow(
                    term="水", reading="みず", content="<div>water</div>", tags="n", sequence=1
                ),
            ],
        )

        conn = open_readonly(db_path)
        try:
            results = lookup(conn, "水")
            assert isinstance(results, list)
            assert len(results) == 1
            row = results[0]
            assert isinstance(row, tuple)
            assert len(row) == 2
            content, tags = row
            assert isinstance(content, str)
            assert isinstance(tags, str)
            assert content == "<div>water</div>"
            assert tags == "n"
        finally:
            conn.close()

    def test_bulk_insert_round_trips_tags(self, tmp_path: Path):
        """tags written via bulk_insert come back through lookup unchanged."""
        db_path = tmp_path / "test.sqlite"
        create_index(db_path)
        bulk_insert(
            db_path,
            [
                DictRow(
                    term="走る",
                    reading="はしる",
                    content="<div>to run</div>",
                    tags="v5r vi",
                    sequence=1,
                ),
            ],
        )

        conn = open_readonly(db_path)
        try:
            results = lookup(conn, "走る")
            assert results == [("<div>to run</div>", "v5r vi")]
        finally:
            conn.close()

    def test_default_empty_tags(self, tmp_path: Path):
        """DictRow without tags defaults to '' and round-trips as '' in the tuple tail."""
        db_path = tmp_path / "test.sqlite"
        create_index(db_path)
        bulk_insert(
            db_path,
            [
                DictRow(term="空", reading="そら", content="<div>sky</div>", sequence=1),
            ],
        )

        conn = open_readonly(db_path)
        try:
            results = lookup(conn, "空")
            assert len(results) == 1
            assert results[0][1] == ""
        finally:
            conn.close()


class TestMeta:
    def test_write_then_read(self, tmp_path: Path):
        db_path = tmp_path / "test.sqlite"
        create_index(db_path)
        write_meta(
            db_path,
            {
                "schema_version": str(SCHEMA_VERSION),
                "source_name": "Test Dict",
                "format": "yomitan",
            },
        )

        meta = read_meta(db_path)
        assert meta["schema_version"] == str(SCHEMA_VERSION)
        assert meta["source_name"] == "Test Dict"
        assert meta["format"] == "yomitan"

    def test_read_missing_file(self, tmp_path: Path):
        assert read_meta(tmp_path / "nonexistent.sqlite") == {}
