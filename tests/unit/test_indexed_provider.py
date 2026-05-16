"""Tests for the IndexedDictProvider."""

from pathlib import Path

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
        provider = IndexedDictProvider(
            "test-dict", tmp_path / "missing.sqlite", display_name="Test"
        )
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
