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
    def test_load_then_lookup_returns_content(self, tmp_path: Path):
        db = tmp_path / "test.sqlite"
        _seed_db(
            db, [DictRow(term="食べる", reading="たべる", content="<div>eat</div>", sequence=1)]
        )

        provider = IndexedDictProvider("test-dict", db, display_name="Test")
        assert provider.load() is True
        assert provider.is_available() is True
        assert provider.name == "Test"
        assert provider.lookup("食べる") == "<div>eat</div>"

    def test_lookup_by_reading_fallback(self, tmp_path: Path):
        db = tmp_path / "test.sqlite"
        _seed_db(
            db, [DictRow(term="食べる", reading="たべる", content="<div>eat</div>", sequence=1)]
        )
        provider = IndexedDictProvider("test-dict", db, display_name="Test")
        provider.load()
        assert provider.lookup("たべる") == "<div>eat</div>"

    def test_lookup_homographs_concatenated_with_hr(self, tmp_path: Path):
        db = tmp_path / "test.sqlite"
        _seed_db(
            db,
            [
                DictRow(term="橋", reading="はし", content="<div>bridge</div>", sequence=1),
                DictRow(term="箸", reading="はし", content="<div>chopsticks</div>", sequence=2),
            ],
        )
        provider = IndexedDictProvider("test-dict", db, display_name="Test")
        provider.load()
        result = provider.lookup("はし")
        assert result is not None
        assert "<div>bridge</div>" in result
        assert "<div>chopsticks</div>" in result
        assert "<hr>" in result

    def test_lookup_miss_returns_none(self, tmp_path: Path):
        db = tmp_path / "test.sqlite"
        _seed_db(db, [])
        provider = IndexedDictProvider("test-dict", db, display_name="Test")
        provider.load()
        assert provider.lookup("無い") is None

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
        _seed_db(db, [DictRow(term="x", reading=None, content="<div>x</div>", sequence=1)])

        provider = IndexedDictProvider("test-dict", db, display_name="Test")
        assert provider.load() is True
        conn_before = provider._conn
        assert provider.load() is True
        assert provider._conn is conn_before  # connection not reopened

    def test_close_then_lookup_returns_none(self, tmp_path: Path):
        db = tmp_path / "test.sqlite"
        _seed_db(db, [DictRow(term="x", reading=None, content="<div>x</div>", sequence=1)])

        provider = IndexedDictProvider("test-dict", db, display_name="Test")
        provider.load()
        assert provider.lookup("x") == "<div>x</div>"
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
            db, [DictRow(term="食べる", reading="たべる", content="<div>eat</div>", sequence=1)]
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
        assert result == ["<div>eat</div>"]
