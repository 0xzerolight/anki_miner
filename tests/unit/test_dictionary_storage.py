"""Tests for dictionary SQLite storage layer."""

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

from anki_miner.services.dictionary.storage import (
    SCHEMA_VERSION,
    DictRow,
    bulk_insert,
    create_index,
    lookup,
    lookup_many,
    open_readonly,
    read_meta,
    read_meta_cached,
    write_meta,
)


class TestCreateIndex:
    def test_creates_tables_and_indexes(self, tmp_path: Path):
        db_path = tmp_path / "test.sqlite"
        create_index(db_path)

        with sqlite3.connect(db_path) as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            assert {"entries", "meta"} <= tables

            indexes = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
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
                DictRow(term="水", reading="みず", content="<div>water</div>", tags="n", sequence=1),
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


class TestLookupMany:
    """lookup_many must reproduce lookup() per word, row-for-row."""

    def _seed(self, db_path: Path) -> None:
        create_index(db_path)
        rows = [
            DictRow(term="食べる", reading="たべる", content="<div>to eat</div>", tags="v1", sequence=1),
            DictRow(term="飲む", reading="のむ", content="<div>to drink</div>", tags="v5m", sequence=2),
            # homograph reading は し
            DictRow(term="橋", reading="はし", content="<div>bridge</div>", sequence=3),
            DictRow(term="箸", reading="はし", content="<div>chopsticks</div>", sequence=4),
            # term-vs-reading priority
            DictRow(term="A", reading="ほし", content="<div>reading-match</div>", sequence=5),
            DictRow(term="ほし", reading=None, content="<div>term-match</div>", sequence=6),
        ]
        # word with >5 matches to exercise LIMIT 5 + ordering
        for i in range(8):
            rows.append(DictRow(term="多", reading="おおい", content=f"<div>many-{i}</div>", sequence=100 + i))
        bulk_insert(db_path, rows)

    def test_matches_lookup_per_word(self, tmp_path: Path):
        db_path = tmp_path / "test.sqlite"
        self._seed(db_path)
        words = ["食べる", "たべる", "飲む", "はし", "ほし", "多", "missing"]

        conn = open_readonly(db_path)
        try:
            batch = lookup_many(conn, words)
            for w in words:
                assert batch[w] == lookup(conn, w), f"mismatch for {w!r}"
        finally:
            conn.close()

    def test_limit_5_enforced(self, tmp_path: Path):
        db_path = tmp_path / "test.sqlite"
        self._seed(db_path)
        conn = open_readonly(db_path)
        try:
            assert len(lookup_many(conn, ["多"])["多"]) == 5
        finally:
            conn.close()

    def test_term_priority_over_reading(self, tmp_path: Path):
        db_path = tmp_path / "test.sqlite"
        self._seed(db_path)
        conn = open_readonly(db_path)
        try:
            assert lookup_many(conn, ["ほし"])["ほし"][0] == ("<div>term-match</div>", "")
        finally:
            conn.close()

    def test_every_requested_word_present(self, tmp_path: Path):
        db_path = tmp_path / "test.sqlite"
        self._seed(db_path)
        conn = open_readonly(db_path)
        try:
            res = lookup_many(conn, ["食べる", "missing", "飲む"])
            assert set(res.keys()) == {"食べる", "missing", "飲む"}
            assert res["missing"] == []
        finally:
            conn.close()

    def test_empty_word_list(self, tmp_path: Path):
        db_path = tmp_path / "test.sqlite"
        self._seed(db_path)
        conn = open_readonly(db_path)
        try:
            assert lookup_many(conn, []) == {}
        finally:
            conn.close()

    def test_chunking_over_999_bind_cap(self, tmp_path: Path):
        """A word list large enough to force >1 chunk still matches per-word lookup."""
        db_path = tmp_path / "test.sqlite"
        create_index(db_path)
        rows = [DictRow(term=f"w{i}", reading=None, content=f"<div>{i}</div>", sequence=i) for i in range(600)]
        bulk_insert(db_path, rows)
        words = [f"w{i}" for i in range(600)] + ["nope"]

        conn = open_readonly(db_path)
        try:
            batch = lookup_many(conn, words)
            for w in words:
                assert batch[w] == lookup(conn, w)
        finally:
            conn.close()

    def test_duplicate_words_in_request(self, tmp_path: Path):
        db_path = tmp_path / "test.sqlite"
        self._seed(db_path)
        conn = open_readonly(db_path)
        try:
            res = lookup_many(conn, ["飲む", "飲む"])
            assert res["飲む"] == lookup(conn, "飲む")
        finally:
            conn.close()

    def test_dual_match_row_counted_once(self, tmp_path: Path):
        """A row whose term and reading both equal the word appears ONCE,
        matching _LOOKUP_SQL's ``term=? OR reading=?``."""
        db_path = tmp_path / "test.sqlite"
        create_index(db_path)
        bulk_insert(db_path, [DictRow(term="はし", reading="はし", content="<div>x</div>", sequence=1)])
        conn = open_readonly(db_path)
        try:
            assert lookup_many(conn, ["はし"])["はし"] == lookup(conn, "はし")
            assert len(lookup_many(conn, ["はし"])["はし"]) == 1
        finally:
            conn.close()

    def test_fuzz_matches_lookup(self, tmp_path: Path):
        """Randomized stress: NULL sequences, duplicate sequences, term/reading
        collisions, and dual-match rows. lookup_many must equal lookup per word
        for every trial (locks the rowid tiebreak + LIMIT 5 ordering)."""
        import random

        terms = ["はし", "橋", "箸", "端", "ほし", "星"]
        for trial in range(40):
            random.seed(trial)
            db_path = tmp_path / f"fuzz_{trial}.sqlite"
            create_index(db_path)
            rows = []
            for i in range(random.randint(0, 50)):
                seq = random.choice([None, 1, 1, 2, 2, 3])
                term = random.choice(terms)
                reading = random.choice([term, "はし", "ほし", None])
                rows.append(
                    DictRow(
                        term=term,
                        reading=reading,
                        content=f"C{trial}_{i}",
                        tags=random.choice(["t", "", "a b"]),
                        sequence=seq,
                    )
                )
            random.shuffle(rows)
            bulk_insert(db_path, rows)
            conn = open_readonly(db_path)
            try:
                words = terms + ["はし", "ほし", "nope"]
                batch = lookup_many(conn, words)
                for w in words:
                    assert batch[w] == lookup(conn, w), f"trial {trial} word {w!r}"
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


class TestReadMetaCached:
    """Sidecar cache for ``meta.json`` — skips SQLite open when fresh."""

    def _setup_dict(self, tmp_path: Path) -> Path:
        db_path = tmp_path / "test.sqlite"
        create_index(db_path)
        write_meta(
            db_path,
            {
                "schema_version": str(SCHEMA_VERSION),
                "source_name": "Test Dict",
                "format": "yomitan",
                "entry_count": "42",
            },
        )
        return db_path

    def test_write_meta_creates_sidecar(self, tmp_path: Path):
        db_path = self._setup_dict(tmp_path)
        sidecar = db_path.parent / "meta.json"
        assert sidecar.is_file()
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        assert data["source_name"] == "Test Dict"
        assert data["entry_count"] == "42"

    def test_cached_read_skips_sqlite_when_sidecar_fresh(self, tmp_path: Path):
        """The hot startup path must not open SQLite when the sidecar is up to date."""
        db_path = self._setup_dict(tmp_path)
        with patch(
            "anki_miner.services.dictionary.storage.read_meta",
            wraps=read_meta,
        ) as wrapped:
            meta = read_meta_cached(db_path)
        assert wrapped.call_count == 0
        assert meta["source_name"] == "Test Dict"

    def test_cached_read_falls_back_when_sidecar_missing(self, tmp_path: Path):
        db_path = self._setup_dict(tmp_path)
        sidecar = db_path.parent / "meta.json"
        sidecar.unlink()
        meta = read_meta_cached(db_path)
        assert meta["source_name"] == "Test Dict"
        # Fall-through rewrites the sidecar.
        assert sidecar.is_file()

    def test_cached_read_falls_back_when_sqlite_newer(self, tmp_path: Path):
        db_path = self._setup_dict(tmp_path)
        sidecar = db_path.parent / "meta.json"
        # Backdate the sidecar so the SQLite file is "newer".
        import os

        old = sidecar.stat().st_mtime - 100
        os.utime(sidecar, (old, old))
        with patch(
            "anki_miner.services.dictionary.storage.read_meta",
            wraps=read_meta,
        ) as wrapped:
            read_meta_cached(db_path)
        assert wrapped.call_count == 1
        # Sidecar gets rewritten with current mtime.
        assert sidecar.stat().st_mtime > old

    def test_cached_read_handles_corrupt_sidecar(self, tmp_path: Path):
        db_path = self._setup_dict(tmp_path)
        sidecar = db_path.parent / "meta.json"
        sidecar.write_text("{not valid json", encoding="utf-8")
        meta = read_meta_cached(db_path)
        assert meta["source_name"] == "Test Dict"
        # Sidecar is rewritten with valid JSON.
        assert json.loads(sidecar.read_text(encoding="utf-8"))["source_name"] == "Test Dict"

    def test_cached_read_missing_db(self, tmp_path: Path):
        assert read_meta_cached(tmp_path / "nonexistent.sqlite") == {}


class TestSurrogateScrubbing:
    """Lone UTF-16 surrogates have no UTF-8 encoding and crash sqlite3 on insert
    (Issue #67). bulk_insert / write_meta scrub them to U+FFFD before binding."""

    # Lone high surrogate from the bug report ('\ud867'); a real above-BMP char
    # (𩨽 = U+29A3D) must survive untouched to prove we only hit lone surrogates.
    LONE = "to e\ud867at"
    VALID_EXT_B = "\U00029a3d"  # 𩨽

    def test_bulk_insert_scrubs_surrogate_in_content(self, tmp_path: Path):
        db_path = tmp_path / "test.sqlite"
        create_index(db_path)
        count = bulk_insert(
            db_path,
            [DictRow(term="食べる", reading="たべる", content=f"<div>{self.LONE}</div>", sequence=1)],
        )
        assert count == 1

        conn = open_readonly(db_path)
        try:
            results = lookup(conn, "食べる")
            assert results == [("<div>to e�at</div>", "")]
            assert "\ud867" not in results[0][0]
        finally:
            conn.close()

    def test_bulk_insert_scrubs_surrogate_in_term(self, tmp_path: Path):
        db_path = tmp_path / "test.sqlite"
        create_index(db_path)
        count = bulk_insert(
            db_path,
            [DictRow(term="a\ud867b", reading=None, content="<div>x</div>", sequence=1)],
        )
        assert count == 1

        conn = open_readonly(db_path)
        try:
            results = lookup(conn, "a�b")
            assert results == [("<div>x</div>", "")]
        finally:
            conn.close()

    def test_bulk_insert_preserves_valid_above_bmp_char(self, tmp_path: Path):
        """A legitimate CJK Extension B code point must pass through unchanged."""
        db_path = tmp_path / "test.sqlite"
        create_index(db_path)
        bulk_insert(
            db_path,
            [DictRow(term=self.VALID_EXT_B, reading=None, content=f"<div>{self.VALID_EXT_B}</div>", sequence=1)],
        )

        conn = open_readonly(db_path)
        try:
            results = lookup(conn, self.VALID_EXT_B)
            assert results == [(f"<div>{self.VALID_EXT_B}</div>", "")]
        finally:
            conn.close()

    def test_write_meta_scrubs_surrogate_in_value(self, tmp_path: Path):
        db_path = tmp_path / "test.sqlite"
        create_index(db_path)
        write_meta(db_path, {"source_name": "Dict\ud867Name", "format": "yomitan"})

        meta = read_meta(db_path)
        assert meta["source_name"] == "Dict�Name"
        assert meta["format"] == "yomitan"
