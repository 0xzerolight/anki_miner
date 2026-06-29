"""Tests for the IndexedDictProvider."""

import logging
import sqlite3
from pathlib import Path
from unittest.mock import patch

from anki_miner.services.dictionary.providers.indexed_provider import IndexedDictProvider
from anki_miner.services.dictionary.storage import (
    SCHEMA_VERSION,
    DictRow,
    bulk_insert,
    create_index,
    write_meta,
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

    def test_multi_hit_same_dict_merges_into_single_li_with_combined_ul(self, tmp_path: Path):
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
        # Exactly one outer <li data-dictionary>
        assert result.count("<li data-dictionary=") == 1
        # Combined gloss-list with total sense count (1 + 2 = 3)
        assert '<ul class="gloss-list" data-count="3">' in result
        # All gloss-items present
        assert '<li class="gloss-item">bridge</li>' in result
        assert '<li class="gloss-item">chopsticks</li>' in result
        assert '<li class="gloss-item">eating sticks</li>' in result
        # Tag union preserves first-seen order: n, common, food (common deduped)
        assert "<i>(n, common, food, DictName)</i>" in result
        # No <hr> in output
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
        batch = provider.lookup_many(words)
        for w in words:
            assert batch[w] == provider.lookup(w), f"HTML mismatch for {w!r}"

    def test_miss_is_none(self, tmp_path: Path):
        db = tmp_path / "test.sqlite"
        self._seed(db)
        provider = IndexedDictProvider("test-dict", db, display_name="DictName")
        provider.load()
        assert provider.lookup_many(["missing"])["missing"] is None

    def test_unloaded_provider_returns_none_for_all(self, tmp_path: Path):
        db = tmp_path / "test.sqlite"
        self._seed(db)
        provider = IndexedDictProvider("test-dict", db, display_name="DictName")
        # not loaded
        res = provider.lookup_many(["食べる", "はし"])
        assert res == {"食べる": None, "はし": None}

    def test_empty_list(self, tmp_path: Path):
        db = tmp_path / "test.sqlite"
        self._seed(db)
        provider = IndexedDictProvider("test-dict", db, display_name="DictName")
        provider.load()
        assert provider.lookup_many([]) == {}


def test_indexed_provider_is_offline(tmp_path):
    db_path = tmp_path / "dummy.sqlite"
    provider = IndexedDictProvider(dict_id="x", db_path=db_path)
    assert provider.is_online is False


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

        result = provider.lookup_many(["にほん"])["にほん"]
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

        assert provider.lookup_many(["にほん"])["にほん"] == provider.lookup("にほん")

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
        assert provider.lookup_many(["テスト"])["テスト"] == provider.lookup("テスト")

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
        batch = provider.lookup_many(["水"])["水"]
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
            result = provider.lookup_many(["食べる", "水"])

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
            provider.lookup_many(["x"])

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
