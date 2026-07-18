"""Tests for the IndexedDictProvider."""

import logging
import sqlite3
from pathlib import Path
from unittest.mock import patch

from anki_miner.services.dictionary.providers.indexed_provider import (
    _DISPLAY_LIMIT,
    IndexedDictProvider,
)
from anki_miner.services.dictionary.storage import (
    SCHEMA_VERSION,
    DictRow,
    TagMeta,
    bulk_insert,
    create_index,
    write_meta,
    write_tags,
)


def _seed_db(db_path: Path, rows: list[DictRow], schema_version: int = SCHEMA_VERSION):
    create_index(db_path)
    bulk_insert(db_path, rows)
    write_meta(db_path, {"schema_version": str(schema_version), "source_name": "Test"})


class TestIndexedDictProvider:
    def test_single_hit_single_sense_composes_lapis_shape(self, tmp_path: Path):
        db = tmp_path / "test.sqlite"
        _seed_db(
            db,
            [
                DictRow(
                    term="食べる",
                    reading="たべる",
                    content='<li class="gloss-item">eat</li>',
                    tags="v1 expr",
                    sequence=1,
                )
            ],
        )

        provider = IndexedDictProvider("test-dict", db, display_name="DictName")
        assert provider.load() is True
        assert provider.is_available() is True
        assert provider.name == "DictName"

        result = provider.lookup("食べる")
        assert result is not None
        assert '<div class="yomitan-glossary">' in result
        assert '<ol data-count="1">' in result
        assert '<li data-dictionary="DictName">' in result
        assert '<ul class="gloss-list" data-count="1">' in result
        assert "<i>(v1, expr, DictName)</i>" in result
        assert '<li class="gloss-item">eat</li>' in result

    def test_lookup_by_reading_fallback(self, tmp_path: Path):
        db = tmp_path / "test.sqlite"
        _seed_db(
            db,
            [
                DictRow(
                    term="食べる",
                    reading="たべる",
                    content='<li class="gloss-item">eat</li>',
                    tags="",
                    sequence=1,
                )
            ],
        )
        provider = IndexedDictProvider("test-dict", db, display_name="Test")
        provider.load()
        result = provider.lookup("たべる")
        assert result is not None
        assert '<li class="gloss-item">eat</li>' in result

    def test_unrelated_lexemes_render_as_separate_sequence_groups(self, tmp_path: Path):
        """Two different lexemes sharing a reading but with DIFFERENT sequences
        (橋 seq1 / 箸 seq2) render as two sub-blocks, each with its OWN tag line,
        inside one <li data-dictionary> — tags are no longer unioned across the
        unrelated lexemes (plan item 5.1 sequence grouping)."""
        db = tmp_path / "test.sqlite"
        _seed_db(
            db,
            [
                DictRow(
                    term="橋",
                    reading="はし",
                    content='<li class="gloss-item">bridge</li>',
                    tags="n common",
                    sequence=1,
                ),
                DictRow(
                    term="箸",
                    reading="はし",
                    content='<li class="gloss-item">chopsticks</li><li class="gloss-item">eating sticks</li>',
                    tags="common food",
                    sequence=2,
                ),
            ],
        )
        provider = IndexedDictProvider("test-dict", db, display_name="DictName")
        provider.load()
        result = provider.lookup("はし")

        assert result is not None
        # Still exactly one outer <li data-dictionary> envelope (CSS compat).
        assert result.count("<li data-dictionary=") == 1
        # Two per-group gloss-lists: bridge (1 item) and chopsticks (2 items).
        assert '<ul class="gloss-list" data-count="1"><li class="gloss-item">bridge</li></ul>' in result
        assert (
            '<ul class="gloss-list" data-count="2">'
            '<li class="gloss-item">chopsticks</li><li class="gloss-item">eating sticks</li></ul>'
        ) in result
        # Per-group tag lines — NOT unioned across the two lexemes.
        assert "<i>(n, common, DictName)</i>" in result
        assert "<i>(common, food, DictName)</i>" in result
        # The old cross-lexeme union must NOT appear.
        assert "<i>(n, common, food, DictName)</i>" not in result
        # Bridge (seq1) sub-block precedes chopsticks (seq2) sub-block.
        assert result.index("bridge") < result.index("chopsticks")
        assert "<hr>" not in result

    def test_lookup_miss_returns_none(self, tmp_path: Path):
        db = tmp_path / "test.sqlite"
        _seed_db(db, [])
        provider = IndexedDictProvider("test-dict", db, display_name="Test")
        provider.load()
        assert provider.lookup("無い") is None

    def test_html_escaping_in_dict_name(self, tmp_path: Path):
        db = tmp_path / "test.sqlite"
        _seed_db(
            db,
            [
                DictRow(
                    term="x",
                    reading=None,
                    content='<li class="gloss-item">x</li>',
                    tags="",
                    sequence=1,
                )
            ],
        )
        provider = IndexedDictProvider("test-dict", db, display_name="A&B<c>")
        provider.load()
        result = provider.lookup("x")

        assert result is not None
        # Attribute: quote=True encodes & < > (and quotes)
        assert 'data-dictionary="A&amp;B&lt;c&gt;"' in result
        # Italic line: same escaping
        assert "<i>(A&amp;B&lt;c&gt;)</i>" in result
        # Raw form must NOT appear unescaped in either spot
        assert 'data-dictionary="A&B<c>"' not in result
        assert "<i>(A&B<c>)</i>" not in result

    def test_empty_tags_produces_italic_with_only_dict_name(self, tmp_path: Path):
        db = tmp_path / "test.sqlite"
        _seed_db(
            db,
            [
                DictRow(
                    term="x",
                    reading=None,
                    content='<li class="gloss-item">x</li>',
                    tags="",
                    sequence=1,
                )
            ],
        )
        provider = IndexedDictProvider("test-dict", db, display_name="DictName")
        provider.load()
        result = provider.lookup("x")

        assert result is not None
        assert "<i>(DictName)</i>" in result
        # No leading comma
        assert "<i>(, " not in result

    def test_schema_version_mismatch_marks_unavailable(self, tmp_path: Path):
        db = tmp_path / "test.sqlite"
        _seed_db(db, [], schema_version=999)
        provider = IndexedDictProvider("test-dict", db, display_name="Test")
        assert provider.load() is False
        assert provider.is_available() is False

    def test_missing_file_marks_unavailable(self, tmp_path: Path):
        provider = IndexedDictProvider("test-dict", tmp_path / "missing.sqlite", display_name="Test")
        assert provider.load() is False
        assert provider.is_available() is False

    def test_double_load_is_idempotent(self, tmp_path: Path):
        db = tmp_path / "test.sqlite"
        _seed_db(
            db,
            [
                DictRow(
                    term="x",
                    reading=None,
                    content='<li class="gloss-item">x</li>',
                    sequence=1,
                )
            ],
        )

        provider = IndexedDictProvider("test-dict", db, display_name="Test")
        assert provider.load() is True
        conn_before = provider._conn
        assert provider.load() is True
        assert provider._conn is conn_before  # connection not reopened

    def test_close_then_lookup_returns_none(self, tmp_path: Path):
        db = tmp_path / "test.sqlite"
        _seed_db(
            db,
            [
                DictRow(
                    term="x",
                    reading=None,
                    content='<li class="gloss-item">x</li>',
                    sequence=1,
                )
            ],
        )

        provider = IndexedDictProvider("test-dict", db, display_name="Test")
        provider.load()
        result = provider.lookup("x")
        assert result is not None
        assert '<li class="gloss-item">x</li>' in result
        provider.close()
        assert provider.is_available() is False
        assert provider.lookup("x") is None
        # close() is idempotent
        provider.close()

    def test_corrupt_sqlite_marks_unavailable(self, tmp_path: Path):
        db = tmp_path / "corrupt.sqlite"
        db.write_bytes(b"this is not a sqlite database")

        provider = IndexedDictProvider("test-dict", db, display_name="Test")
        assert provider.load() is False
        assert provider.is_available() is False

    def test_load_on_one_thread_lookup_on_another(self, tmp_path: Path):
        """Provider must support load() on GUI thread + lookup() on worker thread.

        Regression test: service_factory builds providers on the GUI thread,
        but EpisodeWorkerThread runs lookups on a worker thread.
        """
        import threading

        db = tmp_path / "test.sqlite"
        _seed_db(
            db,
            [
                DictRow(
                    term="食べる",
                    reading="たべる",
                    content='<li class="gloss-item">eat</li>',
                    sequence=1,
                )
            ],
        )

        provider = IndexedDictProvider("test-dict", db, display_name="Test")
        assert provider.load() is True  # loaded on main thread

        result: list[str | None] = []
        error: list[Exception] = []

        def worker():
            try:
                result.append(provider.lookup("食べる"))
            except Exception as e:
                error.append(e)

        t = threading.Thread(target=worker)
        t.start()
        t.join()

        assert not error, f"Cross-thread lookup raised: {error}"
        assert len(result) == 1
        assert result[0] is not None
        assert '<li class="gloss-item">eat</li>' in result[0]


class TestIndexedDictProviderLookupMany:
    """lookup_many must produce byte-identical HTML to lookup per word."""

    def _seed(self, db_path: Path):
        _seed_db(
            db_path,
            [
                DictRow(
                    term="食べる",
                    reading="たべる",
                    content='<li class="gloss-item">eat</li>',
                    tags="v1 expr",
                    sequence=1,
                ),
                DictRow(
                    term="橋", reading="はし", content='<li class="gloss-item">bridge</li>', tags="n common", sequence=2
                ),
                DictRow(
                    term="箸",
                    reading="はし",
                    content='<li class="gloss-item">chopsticks</li><li class="gloss-item">eating sticks</li>',
                    tags="common food",
                    sequence=3,
                ),
            ]
            # word with >5 hits to lock LIMIT 5 + ordering
            + [
                DictRow(
                    term="多", reading="おおい", content=f'<li class="gloss-item">m{i}</li>', tags="n", sequence=10 + i
                )
                for i in range(7)
            ],
        )

    def test_byte_identical_to_single_lookup(self, tmp_path: Path):
        db = tmp_path / "test.sqlite"
        self._seed(db)
        provider = IndexedDictProvider("test-dict", db, display_name="DictName")
        provider.load()

        words = ["食べる", "たべる", "はし", "多", "missing"]
        batch = provider.lookup_many([(w, None) for w in words])
        for w in words:
            assert batch[w] == provider.lookup(w), f"HTML mismatch for {w!r}"

    def test_miss_is_none(self, tmp_path: Path):
        db = tmp_path / "test.sqlite"
        self._seed(db)
        provider = IndexedDictProvider("test-dict", db, display_name="DictName")
        provider.load()
        assert provider.lookup_many([("missing", None)])["missing"] is None

    def test_unloaded_provider_returns_none_for_all(self, tmp_path: Path):
        db = tmp_path / "test.sqlite"
        self._seed(db)
        provider = IndexedDictProvider("test-dict", db, display_name="DictName")
        # not loaded
        res = provider.lookup_many([("食べる", None), ("はし", None)])
        assert res == {"食べる": None, "はし": None}

    def test_empty_list(self, tmp_path: Path):
        db = tmp_path / "test.sqlite"
        self._seed(db)
        provider = IndexedDictProvider("test-dict", db, display_name="DictName")
        provider.load()
        assert provider.lookup_many([]) == {}


class TestIndexedDictProviderHomographScope:
    """lookup_many forwards ``scope_homographs`` to storage: the default scoped
    render drops wrong-homograph reading matches; scope_homographs=False keeps the
    unfiltered probe semantics."""

    def _seed(self, db_path: Path):
        _seed_db(
            db_path,
            [
                DictRow(term="レイド", reading="れいど", content='<li class="gloss-item">raid</li>', sequence=1),
                DictRow(term="零度", reading="れいど", content='<li class="gloss-item">zero degrees</li>', sequence=2),
            ],
        )

    def test_scoped_render_drops_reading_only_homograph(self, tmp_path: Path):
        db = tmp_path / "raid.sqlite"
        self._seed(db)
        provider = IndexedDictProvider("test-dict", db, display_name="DictName")
        provider.load()
        html = provider.lookup_many([("レイド", None)])["レイド"]
        assert html is not None
        assert "raid" in html
        assert "zero degrees" not in html
        # Single-word lookup is always scoped and byte-identical.
        assert provider.lookup("レイド") == html

    def test_unscoped_probe_keeps_reading_only_homograph(self, tmp_path: Path):
        db = tmp_path / "raid.sqlite"
        self._seed(db)
        provider = IndexedDictProvider("test-dict", db, display_name="DictName")
        provider.load()
        html = provider.lookup_many([("レイド", None)], scope_homographs=False)["レイド"]
        assert html is not None
        assert "raid" in html
        assert "zero degrees" in html


def test_indexed_provider_is_offline(tmp_path):
    db_path = tmp_path / "dummy.sqlite"
    provider = IndexedDictProvider(dict_id="x", db_path=db_path)
    assert provider.is_online is False


# ---------------------------------------------------------------------------
# 5.1: dedup-before-cap + display-cap structural guard
# ---------------------------------------------------------------------------


class TestDedupBeforeCap:
    """The display cap is applied AFTER content-dedup and grouping, over the
    storage over-fetch pool — so duplicate rows can't consume display slots."""

    def test_rendered_senses_capped_at_display_limit(self, tmp_path: Path):
        """8 distinct-content senses under distinct sequences → 8 groups; only
        _DISPLAY_LIMIT senses survive the cap (structural perf guard (c))."""
        db = tmp_path / "t.sqlite"
        _seed_db(
            db,
            [
                DictRow(
                    term="同",
                    reading="どう",
                    content=f'<li class="gloss-item">m{i}</li>',
                    tags="",
                    sequence=i,
                )
                for i in range(8)
            ],
        )
        provider = IndexedDictProvider("test-dict", db, display_name="D")
        provider.load()
        result = provider.lookup("同")
        assert result is not None
        # Exactly _DISPLAY_LIMIT gloss-items rendered (the first 5 by sequence).
        assert result.count('<li class="gloss-item">') == _DISPLAY_LIMIT
        present = [i for i in range(8) if f">m{i}</li>" in result]
        assert present == list(range(_DISPLAY_LIMIT))  # m0..m4 kept, m5..m7 dropped

    def test_single_group_capped_at_display_limit(self, tmp_path: Path):
        """8 senses under ONE shared sequence → one group, still capped at 5
        (打つ-style shared-sequence golden case)."""
        db = tmp_path / "t.sqlite"
        _seed_db(
            db,
            [
                DictRow(
                    term="打つ",
                    reading="うつ",
                    content=f'<li class="gloss-item">u{i}</li>',
                    tags="",
                    score=8 - i,
                    sequence=3,
                )
                for i in range(8)
            ],
        )
        provider = IndexedDictProvider("test-dict", db, display_name="D")
        provider.load()
        result = provider.lookup("打つ")
        assert result is not None
        assert result.count('<li class="gloss-item">') == _DISPLAY_LIMIT
        # One sequence ⇒ one group ⇒ one gloss-list.
        assert result.count('<ul class="gloss-list"') == 1

    def test_dedup_frees_slots_for_real_senses(self, tmp_path: Path):
        """Duplicate content collapses BEFORE the cap, so a real sense that a
        naive LIMIT-5-then-dedup would have dropped now survives."""
        db = tmp_path / "t.sqlite"
        # dup content appears twice at the front (score-forced), then 5 unique.
        rows = [
            DictRow(term="語", reading="ご", content='<li class="gloss-item">dup</li>', score=100, sequence=1),
            DictRow(term="語", reading="ご", content='<li class="gloss-item">dup</li>', score=99, sequence=1),
        ] + [
            DictRow(term="語", reading="ご", content=f'<li class="gloss-item">s{i}</li>', score=50 - i, sequence=1)
            for i in range(5)
        ]
        _seed_db(db, rows)
        provider = IndexedDictProvider("test-dict", db, display_name="D")
        provider.load()
        result = provider.lookup("語")
        assert result is not None
        # dup once + s0..s3 == 5 unique senses (naive LIMIT-5 pre-dedup would show
        # dup once + s0..s2 == 4). s3 surviving is the dedup-before-cap fix.
        assert result.count('<li class="gloss-item">') == _DISPLAY_LIMIT
        assert result.count('<li class="gloss-item">dup</li>') == 1
        assert ">s3</li>" in result


# ---------------------------------------------------------------------------
# OVH-026: kana lookup duplicate-content dedup
# ---------------------------------------------------------------------------


class TestKanaDedup:
    """A kana lookup that matches BOTH the kanji-keyed row (via reading col) and the
    reading-keyed row (via term col) must NOT render the same gloss twice."""

    def test_kana_lookup_renders_gloss_once(self, tmp_path: Path):
        """にほん: one row with term='日本', reading='にほん' produces one gloss, not two."""
        db = tmp_path / "test.sqlite"
        _seed_db(
            db,
            [
                DictRow(
                    term="日本",
                    reading="にほん",
                    content='<li class="gloss-item">Japan</li>',
                    tags="n",
                    sequence=1,
                )
            ],
        )
        provider = IndexedDictProvider("test-dict", db, display_name="DictName")
        provider.load()

        result = provider.lookup("にほん")
        assert result is not None
        # Gloss must appear exactly once in the rendered HTML
        assert result.count('<li class="gloss-item">Japan</li>') == 1

    def test_kana_lookup_many_renders_gloss_once(self, tmp_path: Path):
        """lookup_many path also deduplicates identical content rows."""
        db = tmp_path / "test.sqlite"
        _seed_db(
            db,
            [
                DictRow(
                    term="日本",
                    reading="にほん",
                    content='<li class="gloss-item">Japan</li>',
                    tags="n",
                    sequence=1,
                )
            ],
        )
        provider = IndexedDictProvider("test-dict", db, display_name="DictName")
        provider.load()

        result = provider.lookup_many([("にほん", None)])["にほん"]
        assert result is not None
        assert result.count('<li class="gloss-item">Japan</li>') == 1

    def test_dedup_produces_same_result_as_single_lookup(self, tmp_path: Path):
        """lookup_many and lookup must agree after dedup (byte-identical)."""
        db = tmp_path / "test.sqlite"
        _seed_db(
            db,
            [
                DictRow(
                    term="日本",
                    reading="にほん",
                    content='<li class="gloss-item">Japan</li>',
                    tags="n",
                    sequence=1,
                )
            ],
        )
        provider = IndexedDictProvider("test-dict", db, display_name="DictName")
        provider.load()

        assert provider.lookup_many([("にほん", None)])["にほん"] == provider.lookup("にほん")

    def test_distinct_content_rows_all_render(self, tmp_path: Path):
        """Multiple rows with DIFFERENT content still all render (dedup is content-keyed)."""
        db = tmp_path / "test.sqlite"
        _seed_db(
            db,
            [
                DictRow(
                    term="橋",
                    reading="はし",
                    content='<li class="gloss-item">bridge</li>',
                    tags="n",
                    sequence=1,
                ),
                DictRow(
                    term="箸",
                    reading="はし",
                    content='<li class="gloss-item">chopsticks</li>',
                    tags="n",
                    sequence=2,
                ),
            ],
        )
        provider = IndexedDictProvider("test-dict", db, display_name="DictName")
        provider.load()

        result = provider.lookup("はし")
        assert result is not None
        assert '<li class="gloss-item">bridge</li>' in result
        assert '<li class="gloss-item">chopsticks</li>' in result

    def test_dedup_still_unions_tags(self, tmp_path: Path):
        """When a duplicate content row has extra tags, they must be UNIONed in."""
        db = tmp_path / "test.sqlite"
        # Two rows with SAME content but different tags (simulates double-keyed import)
        _seed_db(
            db,
            [
                DictRow(
                    term="日本語",
                    reading="にほんご",
                    content='<li class="gloss-item">Japanese</li>',
                    tags="n lang",
                    sequence=1,
                ),
                DictRow(
                    term="にほんご",
                    reading=None,
                    content='<li class="gloss-item">Japanese</li>',
                    tags="common",
                    sequence=2,
                ),
            ],
        )
        provider = IndexedDictProvider("test-dict", db, display_name="DictName")
        provider.load()

        result = provider.lookup("にほんご")
        assert result is not None
        # Content appears exactly once
        assert result.count('<li class="gloss-item">Japanese</li>') == 1
        # Tags from both rows should be unioned (n, lang, common)
        assert "n" in result
        assert "lang" in result
        assert "common" in result


# ---------------------------------------------------------------------------
# OVH-027: score-based ranking
# ---------------------------------------------------------------------------


class TestScoreRanking:
    """Higher-scored entries must survive LIMIT 5 and lead lower-scored ones."""

    def test_higher_score_entry_leads_lower_score_after_limit(self, tmp_path: Path):
        """With 6 rows sharing the same term, the top-5 by score DESC win."""
        db = tmp_path / "test.sqlite"
        # 6 rows: scores 1..6 (higher = more relevant). Without score ordering,
        # insertion order / id would pick scores 1-5, dropping score=6.
        rows = [
            DictRow(
                term="テスト",
                reading="てすと",
                content=f'<li class="gloss-item">sense-score-{s}</li>',
                tags="",
                score=s,
                sequence=s,
            )
            for s in range(1, 7)
        ]
        _seed_db(db, rows)

        provider = IndexedDictProvider("test-dict", db, display_name="DictName")
        provider.load()

        result = provider.lookup("テスト")
        assert result is not None
        # score=6 (highest) must be present
        assert "sense-score-6" in result
        # score=1 (lowest) should have been dropped by LIMIT 5
        assert "sense-score-1" not in result

    def test_score_ranking_consistent_in_lookup_many(self, tmp_path: Path):
        """lookup_many must apply the same score-based ordering as lookup."""
        db = tmp_path / "test.sqlite"
        rows = [
            DictRow(
                term="テスト",
                reading="てすと",
                content=f'<li class="gloss-item">sense-score-{s}</li>',
                tags="",
                score=s,
                sequence=s,
            )
            for s in range(1, 7)
        ]
        _seed_db(db, rows)

        provider = IndexedDictProvider("test-dict", db, display_name="DictName")
        provider.load()

        # byte-identical to lookup
        assert provider.lookup_many([("テスト", None)])["テスト"] == provider.lookup("テスト")

    def test_jmdict_score_zero_no_op(self, tmp_path: Path):
        """All score=0 rows (JMdict): ordering unchanged by the new score key."""
        db = tmp_path / "test.sqlite"
        rows = [
            DictRow(
                term="水",
                reading="みず",
                content=f'<li class="gloss-item">water-{i}</li>',
                tags="",
                score=0,
                sequence=i,
            )
            for i in range(1, 7)
        ]
        _seed_db(db, rows)

        provider = IndexedDictProvider("test-dict", db, display_name="DictName")
        provider.load()

        single = provider.lookup("水")
        batch = provider.lookup_many([("水", None)])["水"]
        assert single == batch  # consistent with each other
        # The first 5 by sequence (1..5) win; sequence=6 is dropped
        assert "water-1" in single
        assert "water-5" in single
        assert "water-6" not in single


# ---------------------------------------------------------------------------
# OVH-047: IndexedDictProvider degrades on sqlite3.DatabaseError at query time
# ---------------------------------------------------------------------------


class TestIndexedDictProviderDatabaseErrorGuard:
    """A corrupt page that only surfaces on first query must degrade to a miss,
    not propagate the DatabaseError to the caller (OVH-047)."""

    def _make_loaded_provider(self, tmp_path: Path) -> IndexedDictProvider:
        db = tmp_path / "test.sqlite"
        _seed_db(db, [DictRow(term="食べる", reading="たべる", content="<li>eat</li>", sequence=1)])
        provider = IndexedDictProvider("test-dict", db, display_name="Test")
        provider.load()
        assert provider.is_available()
        return provider

    def test_lookup_returns_none_on_database_error(self, tmp_path: Path, caplog):
        """lookup() catches sqlite3.DatabaseError and returns None."""
        provider = self._make_loaded_provider(tmp_path)
        with patch(
            "anki_miner.services.dictionary.providers.indexed_provider.storage_lookup",
            side_effect=sqlite3.DatabaseError("database disk image is malformed"),
        ):
            caplog.set_level(logging.WARNING)
            result = provider.lookup("食べる")

        assert result is None
        assert "test-dict" in caplog.text

    def test_lookup_many_returns_all_miss_on_database_error(self, tmp_path: Path, caplog):
        """lookup_many() catches sqlite3.DatabaseError and returns all-miss dict."""
        provider = self._make_loaded_provider(tmp_path)
        with patch(
            "anki_miner.services.dictionary.providers.indexed_provider.storage_lookup_many",
            side_effect=sqlite3.DatabaseError("database disk image is malformed"),
        ):
            caplog.set_level(logging.WARNING)
            result = provider.lookup_many([("食べる", None), ("水", None)])

        assert result == {"食べる": None, "水": None}
        assert "test-dict" in caplog.text

    def test_lookup_logs_dict_id_and_db_path(self, tmp_path: Path, caplog):
        """Warning log includes dict_id AND db_path for diagnostics."""
        provider = self._make_loaded_provider(tmp_path)
        with patch(
            "anki_miner.services.dictionary.providers.indexed_provider.storage_lookup",
            side_effect=sqlite3.DatabaseError("malformed"),
        ):
            caplog.set_level(logging.WARNING)
            provider.lookup("x")

        assert "test-dict" in caplog.text
        # db_path is included (as string)
        assert str(tmp_path / "test.sqlite") in caplog.text

    def test_lookup_many_logs_dict_id_and_db_path(self, tmp_path: Path, caplog):
        """lookup_many warning log includes dict_id AND db_path."""
        provider = self._make_loaded_provider(tmp_path)
        with patch(
            "anki_miner.services.dictionary.providers.indexed_provider.storage_lookup_many",
            side_effect=sqlite3.DatabaseError("malformed"),
        ):
            caplog.set_level(logging.WARNING)
            provider.lookup_many([("x", None)])

        assert "test-dict" in caplog.text
        assert str(tmp_path / "test.sqlite") in caplog.text


class TestDictionaryCss:
    """Per-dictionary styles.css exposed (scoped) via ``dictionary_css``.

    The scoped CSS is folded into the shared note-type managed block by
    ``collect_dictionary_css`` — it is NOT injected per card. ``_render`` must
    never emit a ``<style>`` block.
    """

    def _seed(self, db_path: Path, *, styles_css: str | None) -> None:
        create_index(db_path)
        bulk_insert(
            db_path,
            [DictRow(term="食べる", reading="たべる", content='<li class="gloss-item">eat</li>', sequence=1)],
        )
        meta = {"schema_version": str(SCHEMA_VERSION), "source_name": "Jitendex.org [2026-06-06]"}
        if styles_css is not None:
            meta["styles_css"] = styles_css
        write_meta(db_path, meta)

    def test_dictionary_css_is_scoped_and_render_has_no_style_block(self, tmp_path: Path):
        db = tmp_path / "t.sqlite"
        self._seed(db, styles_css='span[data-sc-class="tag"] { color: red }')
        provider = IndexedDictProvider("jitendex", db, display_name="Jitendex.org [2026-06-06]")
        assert provider.load() is True
        # Scoped CSS exposed bare (no <style> wrapper), scoped to the dict.
        assert provider.dictionary_css == (
            '.yomitan-glossary [data-dictionary="Jitendex.org [2026-06-06]"] ' 'span[data-sc-class="tag"] {color: red}'
        )
        out = provider.lookup("食べる")
        assert out is not None
        assert "<style>" not in out

    def test_no_styles_css_empty_dictionary_css(self, tmp_path: Path):
        db = tmp_path / "t.sqlite"
        self._seed(db, styles_css=None)
        provider = IndexedDictProvider("jitendex", db, display_name="Jitendex.org [2026-06-06]")
        assert provider.load() is True
        assert provider.dictionary_css == ""
        out = provider.lookup("食べる")
        assert out is not None
        assert "<style>" not in out

    def test_unsafe_styles_css_scoped_to_empty(self, tmp_path: Path):
        db = tmp_path / "t.sqlite"
        self._seed(db, styles_css="a { background: url(http://evil/x.png) }")
        provider = IndexedDictProvider("jitendex", db, display_name="Jitendex.org [2026-06-06]")
        assert provider.load() is True
        assert provider.dictionary_css == ""
        out = provider.lookup("食べる")
        assert out is not None
        assert "<style>" not in out
        assert "evil" not in out


class TestHasStylesStamp:
    """``data-has-styles`` gates the base sheet's data-sc-* gap-fillers.

    The envelope is stamped iff the dictionary shipped usable (non-empty after
    scoping/sanitizing) styles.css, so glossary.css's
    ``li[data-dictionary]:not([data-has-styles])`` rules switch off exactly for
    entries the dictionary styles itself.
    """

    def _seed(self, db_path: Path, *, styles_css: str | None, name: str = "Jitendex.org [2026-06-06]") -> None:
        create_index(db_path)
        bulk_insert(
            db_path,
            [DictRow(term="食べる", reading="たべる", content='<li class="gloss-item">eat</li>', sequence=1)],
        )
        meta = {"schema_version": str(SCHEMA_VERSION), "source_name": name}
        if styles_css is not None:
            meta["styles_css"] = styles_css
        write_meta(db_path, meta)

    def test_stamped_when_styles_css_present(self, tmp_path: Path):
        db = tmp_path / "t.sqlite"
        self._seed(db, styles_css='span[data-sc-class="tag"] { color: red }')
        provider = IndexedDictProvider("jitendex", db, display_name="Jitendex.org [2026-06-06]")
        assert provider.load() is True
        out = provider.lookup("食べる")
        assert out is not None
        assert '<li data-dictionary="Jitendex.org [2026-06-06]" data-has-styles="">' in out

    def test_unstamped_without_styles_css(self, tmp_path: Path):
        db = tmp_path / "t.sqlite"
        self._seed(db, styles_css=None)
        provider = IndexedDictProvider("jitendex", db, display_name="Jitendex.org [2026-06-06]")
        assert provider.load() is True
        out = provider.lookup("食べる")
        assert out is not None
        assert '<li data-dictionary="Jitendex.org [2026-06-06]">' in out
        assert "data-has-styles" not in out

    def test_unstamped_when_sanitizer_rejects_styles_css(self, tmp_path: Path):
        db = tmp_path / "t.sqlite"
        self._seed(db, styles_css="a { background: url(http://evil/x.png) }")
        provider = IndexedDictProvider("jitendex", db, display_name="Jitendex.org [2026-06-06]")
        assert provider.load() is True
        out = provider.lookup("食べる")
        assert out is not None
        assert '<li data-dictionary="Jitendex.org [2026-06-06]">' in out
        assert "data-has-styles" not in out

    def test_mixed_field_stamps_exactly_the_styled_envelope(self, tmp_path: Path):
        styled_db = tmp_path / "styled.sqlite"
        plain_db = tmp_path / "plain.sqlite"
        self._seed(styled_db, styles_css="li { color: red }", name="Styled Dict")
        self._seed(plain_db, styles_css=None, name="Plain Dict")
        styled = IndexedDictProvider("styled", styled_db, display_name="Styled Dict")
        plain = IndexedDictProvider("plain", plain_db, display_name="Plain Dict")
        assert styled.load() is True
        assert plain.load() is True
        field = (styled.lookup("食べる") or "") + (plain.lookup("食べる") or "")
        assert '<li data-dictionary="Styled Dict" data-has-styles="">' in field
        assert '<li data-dictionary="Plain Dict">' in field
        assert field.count("data-has-styles") == 1


# ---------------------------------------------------------------------------
# schema v3: tag chips + lazy tag-meta cache
# ---------------------------------------------------------------------------


class TestTagChips:
    """A unioned tag with a tags-table row renders as a hover chip; tags with
    no row keep the italic fallback (byte-identical to pre-v3)."""

    def _seed_with_tags(self, db_path: Path, rows, tags):
        create_index(db_path)
        bulk_insert(db_path, rows)
        if tags:
            write_tags(db_path, tags)
        write_meta(db_path, {"schema_version": str(SCHEMA_VERSION), "source_name": "Test"})

    def test_tag_with_row_renders_as_chip(self, tmp_path: Path):
        db = tmp_path / "t.sqlite"
        self._seed_with_tags(
            db,
            [
                DictRow(
                    term="食べる", reading="たべる", content='<li class="gloss-item">eat</li>', tags="uk", sequence=1
                )
            ],
            [TagMeta(name="uk", category="usage", ord=-2, notes="usually kana", score=0.0)],
        )
        provider = IndexedDictProvider("test-dict", db, display_name="DictName")
        provider.load()
        result = provider.lookup("食べる")
        assert result is not None
        assert '<span class="gloss-tag" data-category="usage" title="usually kana">uk</span>' in result
        # The chip tag no longer appears in the italic token line.
        assert "<i>(DictName)</i>" in result
        assert "uk," not in result

    def test_tag_without_row_stays_in_italic(self, tmp_path: Path):
        db = tmp_path / "t.sqlite"
        # tags table empty → v1 has no row → italic fallback (pre-v3 output).
        self._seed_with_tags(
            db,
            [DictRow(term="x", reading=None, content='<li class="gloss-item">x</li>', tags="v1 expr", sequence=1)],
            [],
        )
        provider = IndexedDictProvider("test-dict", db, display_name="DictName")
        provider.load()
        result = provider.lookup("x")
        assert result is not None
        assert "<i>(v1, expr, DictName)</i>" in result
        assert "gloss-tag" not in result

    def test_mixed_chip_and_fallback(self, tmp_path: Path):
        db = tmp_path / "t.sqlite"
        self._seed_with_tags(
            db,
            [DictRow(term="x", reading=None, content='<li class="gloss-item">x</li>', tags="n custom", sequence=1)],
            [TagMeta(name="n", category="partOfSpeech", ord=0, notes="noun", score=0.0)],
        )
        provider = IndexedDictProvider("test-dict", db, display_name="DictName")
        provider.load()
        result = provider.lookup("x")
        assert result is not None
        # 'n' has a row → chip; 'custom' has none → stays in italic with dict name.
        assert '<span class="gloss-tag" data-category="partOfSpeech" title="noun">n</span>' in result
        assert "<i>(custom, DictName)</i>" in result

    def test_chips_sorted_by_ord_then_score_then_name(self, tmp_path: Path):
        db = tmp_path / "t.sqlite"
        self._seed_with_tags(
            db,
            [DictRow(term="x", reading=None, content='<li class="gloss-item">x</li>', tags="c a b", sequence=1)],
            [
                # ord governs first: a(ord2), b(ord1), c(ord1). Among ord1, higher
                # score first (c score5 before b score0); then name.
                TagMeta(name="a", category="", ord=2, notes="", score=0.0),
                TagMeta(name="b", category="", ord=1, notes="", score=0.0),
                TagMeta(name="c", category="", ord=1, notes="", score=5.0),
            ],
        )
        provider = IndexedDictProvider("test-dict", db, display_name="D")
        provider.load()
        result = provider.lookup("x")
        assert result is not None
        pos_c = result.index(">c</span>")
        pos_b = result.index(">b</span>")
        pos_a = result.index(">a</span>")
        # Expected order: c (ord1, score5) < b (ord1, score0) < a (ord2)
        assert pos_c < pos_b < pos_a

    def test_lookup_many_matches_lookup_with_chips(self, tmp_path: Path):
        db = tmp_path / "t.sqlite"
        self._seed_with_tags(
            db,
            [
                DictRow(
                    term="食べる", reading="たべる", content='<li class="gloss-item">eat</li>', tags="uk n", sequence=1
                ),
                DictRow(term="x", reading=None, content='<li class="gloss-item">x</li>', tags="custom", sequence=2),
            ],
            [
                TagMeta(name="uk", category="usage", ord=-2, notes="kana", score=0.0),
                TagMeta(name="n", category="pos", ord=0, notes="noun", score=0.0),
            ],
        )
        provider = IndexedDictProvider("test-dict", db, display_name="D")
        provider.load()
        words = ["食べる", "x", "missing"]
        batch = provider.lookup_many([(w, None) for w in words])
        for w in words:
            assert batch[w] == provider.lookup(w), f"mismatch for {w!r}"

    def test_chip_attrs_escaped(self, tmp_path: Path):
        db = tmp_path / "t.sqlite"
        self._seed_with_tags(
            db,
            [DictRow(term="x", reading=None, content='<li class="gloss-item">x</li>', tags="t", sequence=1)],
            [TagMeta(name="t", category='c"&', ord=0, notes='a<b>"', score=0.0)],
        )
        provider = IndexedDictProvider("test-dict", db, display_name="D")
        provider.load()
        result = provider.lookup("x")
        assert result is not None
        assert 'data-category="c&quot;&amp;"' in result
        assert 'title="a&lt;b&gt;&quot;"' in result

    def test_tag_cache_lazy_and_reused(self, tmp_path: Path):
        db = tmp_path / "t.sqlite"
        self._seed_with_tags(
            db,
            [DictRow(term="x", reading=None, content='<li class="gloss-item">x</li>', tags="uk", sequence=1)],
            [TagMeta(name="uk", category="usage", ord=0, notes="kana", score=0.0)],
        )
        provider = IndexedDictProvider("test-dict", db, display_name="D")
        provider.load()
        assert provider._tag_cache is None  # not read until first render
        provider.lookup("x")
        assert provider._tag_cache is not None and "uk" in provider._tag_cache


class TestTermsReadings:
    """terms_readings mirrors has_terms: batch probe, never raises."""

    def test_readings_for_existing_terms(self, tmp_path):
        db = tmp_path / "tr.sqlite"
        _seed_db(
            db,
            [
                DictRow(term="バカ力", reading="ばかぢから", content="<div>a</div>", sequence=1),
                DictRow(term="せん越", reading=None, content="<div>b</div>", sequence=2),
            ],
        )
        p = IndexedDictProvider("d", db)
        assert p.load()
        assert p.terms_readings(["バカ力", "せん越", "無い語"]) == {"バカ力": ["ばかぢから"]}

    def test_unloaded_provider_returns_empty(self, tmp_path):
        p = IndexedDictProvider("d", tmp_path / "missing.sqlite")
        assert p.terms_readings(["バカ力"]) == {}


# ---------------------------------------------------------------------------
# U10: commonness_aware + attest_quality (foundation, zero behavior change)
# ---------------------------------------------------------------------------


def _seed_tagged(db_path: Path, rows, tags):
    create_index(db_path)
    bulk_insert(db_path, rows)
    if tags:
        write_tags(db_path, tags)
    write_meta(db_path, {"schema_version": str(SCHEMA_VERSION), "source_name": "Test"})


class TestCommonnessAware:
    """``commonness_aware`` is category-based: any tag in {frequent, popular}."""

    def test_jitendex_like_is_aware(self, tmp_path: Path):
        db = tmp_path / "jit.sqlite"
        _seed_tagged(
            db,
            [DictRow(term="有る", reading="ある", content="<div>be</div>", tags="popular", rules="v5", sequence=1)],
            [
                TagMeta(name="popular", category="popular", ord=0, notes="", score=0.0),
                TagMeta(name="frequent", category="frequent", ord=0, notes="", score=0.0),
                TagMeta(name="expression", category="expression", ord=0, notes="", score=0.0),
            ],
        )
        p = IndexedDictProvider("jit", db, display_name="Jitendex")
        assert p.load() is True
        assert p.commonness_aware is True

    def test_jmdict_like_partofspeech_only_is_unaware(self, tmp_path: Path):
        """jmdict tags are 'partOfSpeech'/'name'/'' — no commonness category, so
        a partOfSpeech-only tags table stays UNAWARE (judge-blocking: NOT
        table-presence)."""
        db = tmp_path / "jm.sqlite"
        _seed_tagged(
            db,
            [DictRow(term="日本", reading="にほん", content="<div>Japan</div>", tags="n", rules="", sequence=1)],
            [
                TagMeta(name="n", category="partOfSpeech", ord=0, notes="noun", score=0.0),
                TagMeta(name="place", category="name", ord=0, notes="", score=0.0),
                TagMeta(name="blank", category="", ord=0, notes="", score=0.0),
            ],
        )
        p = IndexedDictProvider("jm", db, display_name="JMdict")
        assert p.load() is True
        assert p.commonness_aware is False

    def test_empty_tags_table_is_unaware(self, tmp_path: Path):
        """A monolingual dict (oukoku11) with an EMPTY tags table is unaware."""
        db = tmp_path / "mono.sqlite"
        _seed_tagged(
            db,
            [DictRow(term="漢語", reading="かんご", content="<div>x</div>", tags="", rules="", sequence=1)],
            [],
        )
        p = IndexedDictProvider("mono", db, display_name="Mono")
        assert p.load() is True
        assert p.commonness_aware is False

    def test_unloaded_provider_is_unaware(self, tmp_path: Path):
        p = IndexedDictProvider("x", tmp_path / "missing.sqlite", display_name="X")
        assert p.commonness_aware is False


class TestAttestQuality:
    """``attest_quality`` reduces attest_detail rows to term_rules/common_rules."""

    def _seed_jitendex(self, db_path: Path) -> None:
        _seed_tagged(
            db_path,
            [
                # verb, term-exact, common (popular), rules v5
                DictRow(term="有る", reading="ある", content="<div>be</div>", tags="popular", rules="v5", sequence=1),
                # kanji headword reachable by reading あく; common, rules v5
                DictRow(
                    term="開く", reading="あく", content="<div>open</div>", tags="frequent", rules="v5", sequence=2
                ),
                # noun, term-exact, common, EMPTY rules
                DictRow(
                    term="日本", reading="にほん", content="<div>Japan</div>", tags="popular", rules="", sequence=3
                ),
                # verb, term-exact, NOT common (only a POS tag), rules v1
                DictRow(term="見る", reading="みる", content="<div>see</div>", tags="n", rules="v1", sequence=4),
            ],
            [
                TagMeta(name="popular", category="popular", ord=0, notes="", score=0.0),
                TagMeta(name="frequent", category="frequent", ord=0, notes="", score=0.0),
                TagMeta(name="n", category="partOfSpeech", ord=0, notes="noun", score=0.0),
            ],
        )

    def test_term_rules_are_raw_rules_strings(self, tmp_path: Path):
        db = tmp_path / "t.sqlite"
        self._seed_jitendex(db)
        p = IndexedDictProvider("d", db, display_name="D")
        p.load()
        q = p.attest_quality(["有る"], include_readings=False)
        assert q["有る"]["term_rules"] == frozenset({"v5"})
        assert q["有る"]["common_rules"] == frozenset({"v5"})

    def test_common_noun_empty_rules_yields_empty_string_token(self, tmp_path: Path):
        """A common noun with rules='' still marks common_rules non-empty (holds
        ''), so the service can detect 'has a common term row' for empty-rules
        rows."""
        db = tmp_path / "t.sqlite"
        self._seed_jitendex(db)
        p = IndexedDictProvider("d", db, display_name="D")
        p.load()
        q = p.attest_quality(["日本"], include_readings=False)
        assert q["日本"]["term_rules"] == frozenset({""})
        assert q["日本"]["common_rules"] == frozenset({""})

    def test_non_common_term_has_empty_common_rules(self, tmp_path: Path):
        db = tmp_path / "t.sqlite"
        self._seed_jitendex(db)
        p = IndexedDictProvider("d", db, display_name="D")
        p.load()
        q = p.attest_quality(["見る"], include_readings=False)
        assert q["見る"]["term_rules"] == frozenset({"v1"})
        assert q["見る"]["common_rules"] == frozenset()

    def test_reading_arm_off_gives_no_term_rules_for_reading_only_match(self, tmp_path: Path):
        db = tmp_path / "t.sqlite"
        self._seed_jitendex(db)
        p = IndexedDictProvider("d", db, display_name="D")
        p.load()
        # あく is only a reading (term is 開く); readings off → nothing attested.
        q = p.attest_quality(["あく"], include_readings=False)
        assert q["あく"] == {"term_rules": frozenset(), "common_rules": frozenset()}

    def test_reading_arm_on_attests_reading_row_common(self, tmp_path: Path):
        db = tmp_path / "t.sqlite"
        self._seed_jitendex(db)
        p = IndexedDictProvider("d", db, display_name="D")
        p.load()
        q = p.attest_quality(["あく"], include_readings=True)
        # reading-only match → term_rules empty, but the row is common → common_rules.
        assert q["あく"]["term_rules"] == frozenset()
        assert q["あく"]["common_rules"] == frozenset({"v5"})

    def test_unaware_dict_has_empty_common_rules(self, tmp_path: Path):
        """An unaware dict (partOfSpeech-only tags) never marks a row common, so
        common_rules is always empty even for a term-exact hit."""
        db = tmp_path / "jm.sqlite"
        _seed_tagged(
            db,
            [DictRow(term="日本", reading="にほん", content="<div>Japan</div>", tags="n", rules="", sequence=1)],
            [TagMeta(name="n", category="partOfSpeech", ord=0, notes="noun", score=0.0)],
        )
        p = IndexedDictProvider("jm", db, display_name="JMdict")
        p.load()
        q = p.attest_quality(["日本"], include_readings=True)
        assert q["日本"]["term_rules"] == frozenset({""})
        assert q["日本"]["common_rules"] == frozenset()

    def test_nbsp_tag_name_marks_common(self, tmp_path: Path):
        """A common tag whose NAME contains an internal NBSP is matched whole."""
        db = tmp_path / "t.sqlite"
        _seed_tagged(
            db,
            [
                DictRow(
                    term="語",
                    reading="ご",
                    content="<div>x</div>",
                    tags="priority form",
                    rules="v5",
                    sequence=1,
                )
            ],
            [TagMeta(name="priority form", category="popular", ord=0, notes="", score=0.0)],
        )
        p = IndexedDictProvider("d", db, display_name="D")
        p.load()
        assert p.commonness_aware is True
        q = p.attest_quality(["語"], include_readings=False)
        assert q["語"]["common_rules"] == frozenset({"v5"})

    def test_miss_present_with_empty_sets(self, tmp_path: Path):
        db = tmp_path / "t.sqlite"
        self._seed_jitendex(db)
        p = IndexedDictProvider("d", db, display_name="D")
        p.load()
        q = p.attest_quality(["有る", "無い語"], include_readings=False)
        assert q["無い語"] == {"term_rules": frozenset(), "common_rules": frozenset()}

    def test_duplicate_words_collapse(self, tmp_path: Path):
        db = tmp_path / "t.sqlite"
        self._seed_jitendex(db)
        p = IndexedDictProvider("d", db, display_name="D")
        p.load()
        q = p.attest_quality(["有る", "有る"], include_readings=False)
        assert list(q.keys()) == ["有る"]

    def test_unloaded_provider_all_miss(self, tmp_path: Path):
        p = IndexedDictProvider("x", tmp_path / "missing.sqlite", display_name="X")
        q = p.attest_quality(["有る", "見る"], include_readings=True)
        assert q == {
            "有る": {"term_rules": frozenset(), "common_rules": frozenset()},
            "見る": {"term_rules": frozenset(), "common_rules": frozenset()},
        }

    def test_database_error_degrades_to_all_miss(self, tmp_path: Path, caplog):
        db = tmp_path / "t.sqlite"
        self._seed_jitendex(db)
        p = IndexedDictProvider("boom-dict", db, display_name="D")
        p.load()
        with patch(
            "anki_miner.services.dictionary.providers.indexed_provider.storage_attest_detail",
            side_effect=sqlite3.DatabaseError("database disk image is malformed"),
        ):
            caplog.set_level(logging.WARNING)
            q = p.attest_quality(["有る", "日本"], include_readings=True)
        assert q == {
            "有る": {"term_rules": frozenset(), "common_rules": frozenset()},
            "日本": {"term_rules": frozenset(), "common_rules": frozenset()},
        }
        assert "boom-dict" in caplog.text
