"""Tests for dictionary SQLite storage layer."""

import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from anki_miner.services.dictionary.storage import (
    _BIND_CHUNK,
    _LOOKUP_LIMIT,
    SCHEMA_VERSION,
    DictRow,
    TagMeta,
    bulk_insert,
    create_index,
    lookup,
    lookup_many,
    open_readonly,
    read_meta,
    read_meta_cached,
    read_tags,
    terms_exist,
    write_meta,
    write_tags,
)


class _ExecSpy:
    """Wrap a real sqlite3 connection to count ``execute`` round-trips.

    lookup_many only calls ``conn.execute``; duck-typed so it can stand in for a
    connection in the structural per-word-round-trip perf guard (plan item 5.1)."""

    def __init__(self, conn):
        self._conn = conn
        self.calls = 0

    def execute(self, *args, **kwargs):
        self.calls += 1
        return self._conn.execute(*args, **kwargs)


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

    def test_schema_version_is_4(self):
        assert SCHEMA_VERSION == 4

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

    def test_entries_table_has_rules_column(self, tmp_path: Path):
        """schema v3 adds ``entries.rules TEXT NOT NULL DEFAULT ''``."""
        db_path = tmp_path / "test.sqlite"
        create_index(db_path)

        with sqlite3.connect(db_path) as conn:
            cols = {row[1]: row for row in conn.execute("PRAGMA table_info(entries)")}
            assert "rules" in cols
            rules_col = cols["rules"]
            assert rules_col[2] == "TEXT"
            assert rules_col[3] == 1  # NOT NULL
            assert rules_col[4] == "''"  # default empty string

    def test_tags_table_created(self, tmp_path: Path):
        """schema v3 adds a ``tags`` metadata table keyed by name."""
        db_path = tmp_path / "test.sqlite"
        create_index(db_path)

        with sqlite3.connect(db_path) as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            assert "tags" in tables
            cols = {row[1] for row in conn.execute("PRAGMA table_info(tags)")}
            assert cols == {"name", "category", "ord", "notes", "score"}


class TestRulesColumn:
    def test_rules_round_trip(self, tmp_path: Path):
        """DictRow.rules is written to the entries table (no reader in 4.6)."""
        db_path = tmp_path / "test.sqlite"
        create_index(db_path)
        bulk_insert(
            db_path,
            [DictRow(term="走る", reading="はしる", content="<div>run</div>", rules="v5r vi", sequence=1)],
        )
        conn = open_readonly(db_path)
        try:
            got = conn.execute("SELECT rules FROM entries WHERE term = ?", ("走る",)).fetchone()[0]
            assert got == "v5r vi"
        finally:
            conn.close()

    def test_rules_defaults_empty(self, tmp_path: Path):
        db_path = tmp_path / "test.sqlite"
        create_index(db_path)
        bulk_insert(db_path, [DictRow(term="空", reading="そら", content="<div>sky</div>", sequence=1)])
        conn = open_readonly(db_path)
        try:
            assert conn.execute("SELECT rules FROM entries WHERE term = ?", ("空",)).fetchone()[0] == ""
        finally:
            conn.close()


class TestTags:
    def test_write_then_read_tags(self, tmp_path: Path):
        db_path = tmp_path / "test.sqlite"
        create_index(db_path)
        write_tags(
            db_path,
            [
                TagMeta(name="uk", category="usage", ord=-2, notes="usually kana", score=0.0),
                TagMeta(name="n", category="partOfSpeech", ord=0, notes="noun", score=1.0),
            ],
        )
        conn = open_readonly(db_path)
        try:
            tags = read_tags(conn)
        finally:
            conn.close()
        assert set(tags) == {"uk", "n"}
        assert tags["uk"] == TagMeta(name="uk", category="usage", ord=-2, notes="usually kana", score=0.0)
        assert tags["n"].notes == "noun"

    def test_write_tags_last_wins_on_duplicate_name(self, tmp_path: Path):
        """A tag name appearing twice collapses to the last definition."""
        db_path = tmp_path / "test.sqlite"
        create_index(db_path)
        write_tags(db_path, [TagMeta("x", "a", 0, "first", 0.0)])
        write_tags(db_path, [TagMeta("x", "b", 1, "second", 2.0)])
        conn = open_readonly(db_path)
        try:
            tags = read_tags(conn)
        finally:
            conn.close()
        assert tags["x"] == TagMeta("x", "b", 1, "second", 2.0)

    def test_read_tags_empty_when_none_written(self, tmp_path: Path):
        db_path = tmp_path / "test.sqlite"
        create_index(db_path)
        conn = open_readonly(db_path)
        try:
            assert read_tags(conn) == {}
        finally:
            conn.close()


class TestReadingNormalization:
    """Readings are stored hiragana-folded; lookup folds the query so a
    katakana word still matches a kanji headword's reading (schema v3)."""

    def test_stored_reading_is_hiragana_folded(self, tmp_path: Path):
        db_path = tmp_path / "test.sqlite"
        create_index(db_path)
        # Kanji headword with a katakana reading (硝子 / ガラス, "glass").
        bulk_insert(db_path, [DictRow(term="硝子", reading="ガラス", content="<div>glass</div>", sequence=1)])
        conn = open_readonly(db_path)
        try:
            stored = conn.execute("SELECT reading FROM entries WHERE term = ?", ("硝子",)).fetchone()[0]
            assert stored == "がらす"  # folded at write
        finally:
            conn.close()

    def test_katakana_query_matches_folded_reading(self, tmp_path: Path):
        db_path = tmp_path / "test.sqlite"
        create_index(db_path)
        bulk_insert(db_path, [DictRow(term="硝子", reading="ガラス", content="<div>glass</div>", sequence=1)])
        conn = open_readonly(db_path)
        try:
            # Query with katakana AND hiragana — both fold to がらす and hit.
            assert lookup(conn, "ガラス") == [("<div>glass</div>", "", 1)]
            assert lookup(conn, "がらす") == [("<div>glass</div>", "", 1)]
        finally:
            conn.close()

    def test_lookup_many_equals_lookup_for_katakana_reading(self, tmp_path: Path):
        """The bucket reverse-map fold: a reading-only katakana hit must be
        assigned back in lookup_many exactly as single lookup returns it.

        This is the guard the brief pins — without the hiragana-keyed reverse
        map the katakana requested word is silently dropped by lookup_many
        while single lookup still returns it. Re-run against the pair signature
        with a wildcard (None) reading (plan item 5.1)."""
        db_path = tmp_path / "test.sqlite"
        create_index(db_path)
        bulk_insert(
            db_path,
            [
                DictRow(term="硝子", reading="ガラス", content="<div>glass</div>", sequence=1),
                DictRow(term="林檎", reading="リンゴ", content="<div>apple</div>", sequence=2),
            ],
        )
        words = ["ガラス", "がらす", "リンゴ", "りんご", "硝子", "missing"]
        conn = open_readonly(db_path)
        try:
            batch = lookup_many(conn, [(w, None) for w in words])
            for w in words:
                assert batch[w] == lookup(conn, w), f"mismatch for {w!r}"
            # The katakana reading-only hit is present, not dropped.
            assert batch["ガラス"] == [("<div>glass</div>", "", 1)]
        finally:
            conn.close()


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
            assert results == [("<div>to eat</div>", "", 1)]
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
            assert results == [("<div>to eat</div>", "", 1)]
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
            contents = [content for content, _tags, _seq in results]
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
            assert results[0] == ("<div>term-match</div>", "", 2)
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
            assert len(row) == 3
            content, tags, sequence = row
            assert isinstance(content, str)
            assert isinstance(tags, str)
            assert content == "<div>water</div>"
            assert tags == "n"
            assert sequence == 1
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
            assert results == [("<div>to run</div>", "v5r vi", 1)]
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


class TestOpenReadonly:
    """open_readonly must build the sqlite ``file:`` URI safely (T-42)."""

    def test_opens_dict_under_path_with_hash(self, tmp_path: Path):
        """A dicts_root path containing ``#`` (URI fragment delimiter) must still
        open read-only. A raw f-string ``file:{path}?mode=ro`` truncates at the
        ``#`` and points sqlite at the wrong file."""
        weird_dir = tmp_path / "dicts#frag"
        weird_dir.mkdir()
        db_path = weird_dir / "index.sqlite"
        create_index(db_path)
        bulk_insert(db_path, [DictRow(term="犬", reading="いぬ", content="<div>dog</div>", sequence=1)])

        conn = open_readonly(db_path)
        try:
            assert lookup(conn, "犬") == [("<div>dog</div>", "", 1)]
        finally:
            conn.close()

    def test_opens_dict_under_path_with_uri_metachars(self, tmp_path: Path):
        """``?`` and ``%`` are also URI-significant; a path carrying them must
        open the intended database rather than misparse the query string."""
        weird_dir = tmp_path / "d?q%2e"
        weird_dir.mkdir()
        db_path = weird_dir / "index.sqlite"
        create_index(db_path)
        bulk_insert(db_path, [DictRow(term="猫", reading="ねこ", content="<div>cat</div>", sequence=1)])

        conn = open_readonly(db_path)
        try:
            assert lookup(conn, "猫") == [("<div>cat</div>", "", 1)]
        finally:
            conn.close()

    def test_connection_is_read_only(self, tmp_path: Path):
        """The fix must preserve read-only mode: writes must be rejected even
        for a path with no special characters."""
        db_path = tmp_path / "ro.sqlite"
        create_index(db_path)

        conn = open_readonly(db_path)
        try:
            with pytest.raises(sqlite3.OperationalError):
                conn.execute("INSERT INTO entries (term, content) VALUES ('x', 'y')")
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
        # word with 8 matches (< pool bound) to exercise ordering
        for i in range(8):
            rows.append(DictRow(term="多", reading="おおい", content=f"<div>many-{i}</div>", sequence=100 + i))
        bulk_insert(db_path, rows)

    def test_matches_lookup_per_word(self, tmp_path: Path):
        db_path = tmp_path / "test.sqlite"
        self._seed(db_path)
        words = ["食べる", "たべる", "飲む", "はし", "ほし", "多", "missing"]

        conn = open_readonly(db_path)
        try:
            batch = lookup_many(conn, [(w, None) for w in words])
            for w in words:
                assert batch[w] == lookup(conn, w), f"mismatch for {w!r}"
        finally:
            conn.close()

    def test_pool_bounded_by_lookup_limit(self, tmp_path: Path):
        """Storage returns at most _LOOKUP_LIMIT rows per word (the candidate
        pool); the display cap is applied later by the provider's _render."""
        db_path = tmp_path / "test.sqlite"
        create_index(db_path)
        # 30 same-term rows: pool must clamp to _LOOKUP_LIMIT (20), not all 30.
        bulk_insert(
            db_path,
            [DictRow(term="多", reading="おおい", content=f"<div>m{i}</div>", sequence=i) for i in range(30)],
        )
        conn = open_readonly(db_path)
        try:
            assert len(lookup(conn, "多")) == _LOOKUP_LIMIT
            assert len(lookup_many(conn, [("多", None)])["多"]) == _LOOKUP_LIMIT
        finally:
            conn.close()

    def test_term_priority_over_reading(self, tmp_path: Path):
        db_path = tmp_path / "test.sqlite"
        self._seed(db_path)
        conn = open_readonly(db_path)
        try:
            assert lookup_many(conn, [("ほし", None)])["ほし"][0] == ("<div>term-match</div>", "", 6)
        finally:
            conn.close()

    def test_every_requested_word_present(self, tmp_path: Path):
        db_path = tmp_path / "test.sqlite"
        self._seed(db_path)
        conn = open_readonly(db_path)
        try:
            res = lookup_many(conn, [("食べる", None), ("missing", None), ("飲む", None)])
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
            batch = lookup_many(conn, [(w, None) for w in words])
            for w in words:
                assert batch[w] == lookup(conn, w)
        finally:
            conn.close()

    def test_duplicate_words_in_request(self, tmp_path: Path):
        db_path = tmp_path / "test.sqlite"
        self._seed(db_path)
        conn = open_readonly(db_path)
        try:
            res = lookup_many(conn, [("飲む", None), ("飲む", None)])
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
            assert lookup_many(conn, [("はし", None)])["はし"] == lookup(conn, "はし")
            assert len(lookup_many(conn, [("はし", None)])["はし"]) == 1
        finally:
            conn.close()

    def test_fuzz_matches_lookup(self, tmp_path: Path):
        """Randomized stress: NULL sequences, duplicate sequences, term/reading
        collisions, dual-match rows, AND a per-word reading boost. lookup_many
        must equal lookup(word, reading) per word for every trial (locks the
        rowid tiebreak + reading-boost ordering + pool bound)."""
        import random

        terms = ["はし", "橋", "箸", "端", "ほし", "星"]
        boost_choices = ["はし", "ほし", "おおい", None]
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
                        score=random.choice([0, 0, 1, 5]),
                        sequence=seq,
                    )
                )
            random.shuffle(rows)
            bulk_insert(db_path, rows)
            conn = open_readonly(db_path)
            try:
                # Unique words so each carries one boost reading (lookup_many
                # dedups words, first reading wins — a duplicate word with a
                # different reading is not a supported divergence).
                unique_words = list(dict.fromkeys(terms + ["はし", "ほし", "nope"]))
                pairs = [(w, random.choice(boost_choices)) for w in unique_words]
                batch = lookup_many(conn, pairs)
                for w, r in pairs:
                    assert batch[w] == lookup(conn, w, r), f"trial {trial} word {w!r} reading {r!r}"
            finally:
                conn.close()


# ---------------------------------------------------------------------------
# OVH-027: score-based ranking in storage layer
# ---------------------------------------------------------------------------


class TestScoreOrdering:
    """score DESC must be the leading non-term/non-reading tiebreak in _LOOKUP_SQL
    and mirrored in lookup_many's Python sort. The display cap moved to the
    provider (dedup-before-cap), so storage keeps the whole pool in score order."""

    def test_higher_score_leads_within_pool(self, tmp_path: Path):
        """6 rows sharing the same term: all survive the pool, ordered score DESC."""
        db_path = tmp_path / "test.sqlite"
        create_index(db_path)
        rows = [
            DictRow(term="テスト", reading="てすと", content=f"<div>s{s}</div>", score=s, sequence=s)
            for s in range(1, 7)
        ]
        bulk_insert(db_path, rows)

        conn = open_readonly(db_path)
        try:
            results = lookup(conn, "テスト")
            contents = [c for c, _, _ in results]
        finally:
            conn.close()

        # No storage-side drop anymore: all 6 present, highest score first.
        assert contents == [f"<div>s{s}</div>" for s in range(6, 0, -1)]

    def test_top_scores_survive_pool_limit(self, tmp_path: Path):
        """>pool rows sharing a term: the top _LOOKUP_LIMIT by score DESC survive."""
        db_path = tmp_path / "test.sqlite"
        create_index(db_path)
        rows = [
            DictRow(term="テスト", reading="てすと", content=f"<div>s{s}</div>", score=s, sequence=s)
            for s in range(1, 31)
        ]
        bulk_insert(db_path, rows)

        conn = open_readonly(db_path)
        try:
            results = lookup(conn, "テスト")
            contents = [c for c, _, _ in results]
        finally:
            conn.close()

        assert len(results) == _LOOKUP_LIMIT
        assert "<div>s30</div>" in contents  # highest score survives the pool
        assert "<div>s1</div>" not in contents  # lowest is beyond the pool bound

    def test_lookup_many_mirrors_lookup_score_order(self, tmp_path: Path):
        """lookup_many must reproduce the same score-ordered results as lookup."""
        db_path = tmp_path / "test.sqlite"
        create_index(db_path)
        rows = [
            DictRow(term="テスト", reading="てすと", content=f"<div>s{s}</div>", score=s, sequence=s)
            for s in range(1, 7)
        ]
        bulk_insert(db_path, rows)

        conn = open_readonly(db_path)
        try:
            single = lookup(conn, "テスト")
            batch = lookup_many(conn, [("テスト", None)])["テスト"]
        finally:
            conn.close()

        assert batch == single

    def test_score_zero_preserves_sequence_order(self, tmp_path: Path):
        """All score=0 rows (JMdict): existing sequence-based ordering still governs."""
        db_path = tmp_path / "test.sqlite"
        create_index(db_path)
        rows = [
            DictRow(term="水", reading="みず", content=f"<div>w{i}</div>", score=0, sequence=i) for i in range(1, 7)
        ]
        bulk_insert(db_path, rows)

        conn = open_readonly(db_path)
        try:
            results = lookup(conn, "水")
            contents = [c for c, _, _ in results]
        finally:
            conn.close()

        # All 6 kept (under the pool bound), in ascending sequence order.
        assert contents == [f"<div>w{i}</div>" for i in range(1, 7)]


# ---------------------------------------------------------------------------
# 5.1: reading boost (matchPrimaryReading) — a sort boost, never a filter
# ---------------------------------------------------------------------------


class TestReadingBoost:
    """The token's contextual reading boosts matching-reading rows to the front
    of the ranking WITHOUT dropping the other homograph's senses (Yomitan
    matchPrimaryReading is the leading sort key, not a row filter)."""

    def _seed_utsu(self, db_path: Path) -> None:
        """打つ with うつ/ぶつ readings × score-varied shared-sequence rows,
        modeled on Yomitan valid-dictionary1/term_bank_1.json."""
        create_index(db_path)
        bulk_insert(
            db_path,
            [
                DictRow(term="打つ", reading="うつ", content="<div>utsu-1</div>", score=10, sequence=3),
                DictRow(term="打つ", reading="うつ", content="<div>utsu-2</div>", score=1, sequence=3),
                DictRow(term="打つ", reading="ぶつ", content="<div>butsu-1</div>", score=10, sequence=3),
                DictRow(term="打つ", reading="ぶつ", content="<div>butsu-2</div>", score=1, sequence=3),
            ],
        )

    def test_matching_reading_leads_others_survive(self, tmp_path: Path):
        db_path = tmp_path / "t.sqlite"
        self._seed_utsu(db_path)
        conn = open_readonly(db_path)
        try:
            results = lookup(conn, "打つ", "うつ")
            contents = [c for c, _, _ in results]
        finally:
            conn.close()
        # Both うつ senses lead — even the low-score one (utsu-2, score 1) sorts
        # ahead of the high-score ぶつ sense (butsu-1, score 10): the boost, not
        # score, governs the leading partition.
        assert contents[:2] == ["<div>utsu-1</div>", "<div>utsu-2</div>"]
        # Boost-not-filter canary: the other reading's senses still survive below.
        assert "<div>butsu-1</div>" in contents
        assert "<div>butsu-2</div>" in contents

    def test_opposite_boost_flips_lead(self, tmp_path: Path):
        db_path = tmp_path / "t.sqlite"
        self._seed_utsu(db_path)
        conn = open_readonly(db_path)
        try:
            contents = [c for c, _, _ in lookup(conn, "打つ", "ぶつ")]
        finally:
            conn.close()
        assert contents[:2] == ["<div>butsu-1</div>", "<div>butsu-2</div>"]
        assert "<div>utsu-1</div>" in contents

    def test_wildcard_reading_preserves_score_order(self, tmp_path: Path):
        """reading=None ⇒ no boost ⇒ pre-5.1 (score DESC, sequence, id) order."""
        db_path = tmp_path / "t.sqlite"
        self._seed_utsu(db_path)
        conn = open_readonly(db_path)
        try:
            contents = [c for c, _, _ in lookup(conn, "打つ", None)]
        finally:
            conn.close()
        # score DESC (10s first), ties by id: utsu-1, butsu-1, utsu-2, butsu-2.
        assert contents == [
            "<div>utsu-1</div>",
            "<div>butsu-1</div>",
            "<div>utsu-2</div>",
            "<div>butsu-2</div>",
        ]

    def test_homograph_kanji_boost_canary(self, tmp_path: Path):
        """辛い(からい/つらい): the sentence's reading leads, the other survives."""
        db_path = tmp_path / "t.sqlite"
        create_index(db_path)
        bulk_insert(
            db_path,
            [
                DictRow(term="辛い", reading="からい", content="<div>spicy</div>", score=5, sequence=1),
                DictRow(term="辛い", reading="つらい", content="<div>painful</div>", score=5, sequence=2),
            ],
        )
        conn = open_readonly(db_path)
        try:
            contents = [c for c, _, _ in lookup(conn, "辛い", "からい")]
        finally:
            conn.close()
        assert contents[0] == "<div>spicy</div>"  # からい (sentence reading) leads
        assert "<div>painful</div>" in contents  # つらい survives below

    def test_boost_on_reading_less_dictionary_still_hits(self, tmp_path: Path):
        """A dictionary storing rows with NULL reading still resolves when a boost
        reading is supplied — the boost is inert, the term match stands."""
        db_path = tmp_path / "t.sqlite"
        create_index(db_path)
        bulk_insert(db_path, [DictRow(term="猫", reading=None, content="<div>cat</div>", sequence=1)])
        conn = open_readonly(db_path)
        try:
            assert lookup(conn, "猫", "ねこ") == [("<div>cat</div>", "", 1)]
        finally:
            conn.close()

    def test_lookup_many_mirrors_reading_boost(self, tmp_path: Path):
        """lookup_many reproduces lookup(word, reading) with the boost applied."""
        db_path = tmp_path / "t.sqlite"
        self._seed_utsu(db_path)
        conn = open_readonly(db_path)
        try:
            batch = lookup_many(conn, [("打つ", "うつ")])
            assert batch["打つ"] == lookup(conn, "打つ", "うつ")
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# 5.1: structural perf guards (no wall-time assertions — see brief)
# ---------------------------------------------------------------------------


class TestPerfGuards:
    """Structural guards for the hottest per-word path: one SQL round-trip per
    chunk (no per-word regression) and a bounded candidate pool per word."""

    def test_lookup_many_one_roundtrip_per_chunk(self, tmp_path: Path):
        db_path = tmp_path / "t.sqlite"
        create_index(db_path)
        bulk_insert(
            db_path,
            [DictRow(term=f"w{i}", reading=None, content=f"<div>{i}</div>", sequence=i) for i in range(5)],
        )
        conn = open_readonly(db_path)
        try:
            spy = _ExecSpy(conn)
            lookup_many(spy, [(f"w{i}", None) for i in range(5)])
            # A single chunk of words issues exactly ONE query, not one per word.
            assert spy.calls == 1
        finally:
            conn.close()

    def test_lookup_many_one_roundtrip_per_chunk_when_spanning_chunks(self, tmp_path: Path):
        db_path = tmp_path / "t.sqlite"
        create_index(db_path)
        n = _BIND_CHUNK + 10  # forces exactly 2 bind chunks
        bulk_insert(
            db_path,
            [DictRow(term=f"w{i}", reading=None, content=f"<div>{i}</div>", sequence=i) for i in range(n)],
        )
        conn = open_readonly(db_path)
        try:
            spy = _ExecSpy(conn)
            lookup_many(spy, [(f"w{i}", None) for i in range(n)])
            # ceil(n / _BIND_CHUNK) == 2 queries: one round-trip per chunk.
            assert spy.calls == 2
        finally:
            conn.close()

    def test_candidate_pool_bounded_per_word(self, tmp_path: Path):
        """Even a term with far more than the pool bound of rows fetches at most
        _LOOKUP_LIMIT per word (single and batch)."""
        db_path = tmp_path / "t.sqlite"
        create_index(db_path)
        bulk_insert(
            db_path,
            [DictRow(term="同", reading="どう", content=f"<div>{i}</div>", sequence=i) for i in range(100)],
        )
        conn = open_readonly(db_path)
        try:
            assert len(lookup(conn, "同")) <= _LOOKUP_LIMIT
            assert len(lookup_many(conn, [("同", None)])["同"]) <= _LOOKUP_LIMIT
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
            assert results == [("<div>to e�at</div>", "", 1)]
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
            assert results == [("<div>x</div>", "", 1)]
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
            assert results == [(f"<div>{self.VALID_EXT_B}</div>", "", 1)]
        finally:
            conn.close()

    def test_write_meta_scrubs_surrogate_in_value(self, tmp_path: Path):
        db_path = tmp_path / "test.sqlite"
        create_index(db_path)
        write_meta(db_path, {"source_name": "Dict\ud867Name", "format": "yomitan"})

        meta = read_meta(db_path)
        assert meta["source_name"] == "Dict�Name"
        assert meta["format"] == "yomitan"


class TestTermsExist:
    def _seed(self, tmp_path: Path) -> Path:
        db_path = tmp_path / "test.sqlite"
        create_index(db_path)
        bulk_insert(
            db_path,
            [
                DictRow(term="走り出す", reading="はしりだす", content="<div>a</div>", sequence=1),
                DictRow(term="応急処置", reading="おうきゅうしょち", content="<div>b</div>", sequence=2),
                DictRow(term="気がする", reading="きがする", content="<div>c</div>", sequence=3),
            ],
        )
        return db_path

    def test_exact_term_matches_only(self, tmp_path: Path):
        conn = open_readonly(self._seed(tmp_path))
        try:
            found = terms_exist(conn, ["走り出す", "気がする", "存在しない語"])
            assert found == {"走り出す", "気がする"}
        finally:
            conn.close()

    def test_reading_only_match_does_not_count(self, tmp_path: Path):
        conn = open_readonly(self._seed(tmp_path))
        try:
            # はしりだす is a reading, not a headword — must NOT be attested.
            assert terms_exist(conn, ["はしりだす"]) == set()
        finally:
            conn.close()

    def test_empty_and_duplicate_input(self, tmp_path: Path):
        conn = open_readonly(self._seed(tmp_path))
        try:
            assert terms_exist(conn, []) == set()
            assert terms_exist(conn, ["応急処置", "応急処置", "応急処置"]) == {"応急処置"}
        finally:
            conn.close()

    def test_chunking_over_900_terms(self, tmp_path: Path):
        db_path = tmp_path / "big.sqlite"
        create_index(db_path)
        bulk_insert(
            db_path,
            [DictRow(term=f"語{i}", reading=None, content="<div>x</div>", sequence=i) for i in range(1000)],
        )
        conn = open_readonly(db_path)
        try:
            queries = [f"語{i}" for i in range(1500)]  # 1000 hits + 500 misses, spans 2 chunks
            found = terms_exist(conn, queries)
            assert found == {f"語{i}" for i in range(1000)}
        finally:
            conn.close()


class TestLookupWithRules:
    """``lookup_with_rules`` — rows plus the rules column for the 5.2 fallback."""

    def test_returns_rules_column(self, tmp_path: Path):
        from anki_miner.services.dictionary.storage import lookup_with_rules

        db_path = tmp_path / "d.sqlite"
        create_index(db_path)
        bulk_insert(db_path, [DictRow(term="食べる", reading="たべる", content="<li>eat</li>", rules="v1", sequence=1)])
        conn = open_readonly(db_path)
        try:
            rows = lookup_with_rules(conn, "食べる")
            assert rows == [("<li>eat</li>", "", 1, "v1")]
        finally:
            conn.close()

    def test_absent_rules_is_empty_string(self, tmp_path: Path):
        from anki_miner.services.dictionary.storage import lookup_with_rules

        db_path = tmp_path / "d.sqlite"
        create_index(db_path)
        # A row inserted without rules gets the schema DEFAULT '' (older-import shape).
        bulk_insert(db_path, [DictRow(term="犬", reading="いぬ", content="<li>dog</li>", sequence=1)])
        conn = open_readonly(db_path)
        try:
            rows = lookup_with_rules(conn, "犬")
            assert rows == [("<li>dog</li>", "", 1, "")]
        finally:
            conn.close()

    def test_katakana_query_matches_folded_reading(self, tmp_path: Path):
        from anki_miner.services.dictionary.storage import lookup_with_rules

        db_path = tmp_path / "d.sqlite"
        create_index(db_path)
        bulk_insert(db_path, [DictRow(term="猫", reading="ネコ", content="<li>cat</li>", rules="", sequence=1)])
        conn = open_readonly(db_path)
        try:
            # Reading stored folded to ねこ; a katakana query folds too and matches.
            rows = lookup_with_rules(conn, "ネコ")
            assert rows == [("<li>cat</li>", "", 1, "")]
        finally:
            conn.close()
