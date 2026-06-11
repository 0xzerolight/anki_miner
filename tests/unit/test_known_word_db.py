"""Tests for KnownWordDB service."""

import pytest

from anki_miner.services.known_word_db import KnownWordDB


class TestInitialize:
    """Tests for initialize method."""

    def test_creates_database_file(self, tmp_path):
        """Should create the database file and parent directories."""
        db_path = tmp_path / "subdir" / "known_words.db"
        db = KnownWordDB(db_path)
        db.initialize()
        assert db_path.exists()

    def test_creates_schema(self, tmp_path):
        """Should create the known_words table."""
        db_path = tmp_path / "known_words.db"
        db = KnownWordDB(db_path)
        db.initialize()
        # Verify by inserting and reading back
        assert db.word_count() == 0

    def test_idempotent(self, tmp_path):
        """Should be safe to call multiple times."""
        db_path = tmp_path / "known_words.db"
        db = KnownWordDB(db_path)
        db.initialize()
        db.add_words({"食べる"})
        db.initialize()  # Should not drop existing data
        assert db.word_count() == 1


class TestIsAvailable:
    """Tests for is_available method."""

    def test_false_before_initialize(self, tmp_path):
        """Should return False when DB file doesn't exist."""
        db_path = tmp_path / "nonexistent.db"
        db = KnownWordDB(db_path)
        assert db.is_available() is False

    def test_true_after_initialize(self, tmp_path):
        """Should return True after initialization."""
        db_path = tmp_path / "known_words.db"
        db = KnownWordDB(db_path)
        db.initialize()
        assert db.is_available() is True

    def test_false_if_file_deleted(self, tmp_path):
        """Should return False if DB file is removed."""
        db_path = tmp_path / "known_words.db"
        db = KnownWordDB(db_path)
        db.initialize()
        db_path.unlink()
        assert db.is_available() is False


class TestAddWords:
    """Tests for add_words method."""

    def test_adds_words(self, tmp_path):
        """Should insert words into the database."""
        db = KnownWordDB(tmp_path / "known_words.db")
        db.initialize()
        count = db.add_words({"食べる", "飲む", "走る"})
        assert count == 3
        assert db.word_count() == 3

    def test_returns_new_count_only(self, tmp_path):
        """Should return only newly inserted count, ignoring duplicates."""
        db = KnownWordDB(tmp_path / "known_words.db")
        db.initialize()
        db.add_words({"食べる", "飲む"})
        count = db.add_words({"食べる", "走る"})  # 食べる is duplicate
        assert count == 1
        assert db.word_count() == 3

    def test_empty_set(self, tmp_path):
        """Should handle empty set gracefully."""
        db = KnownWordDB(tmp_path / "known_words.db")
        db.initialize()
        count = db.add_words(set())
        assert count == 0

    def test_stores_source(self, tmp_path):
        """Should store the source label for each word."""
        import sqlite3

        db_path = tmp_path / "known_words.db"
        db = KnownWordDB(db_path)
        db.initialize()
        db.add_words({"食べる"}, source="mined")

        conn = sqlite3.connect(db_path)
        row = conn.execute("SELECT source FROM known_words WHERE lemma = ?", ("食べる",)).fetchone()
        conn.close()
        assert row[0] == "mined"


class TestGetKnownWords:
    """Tests for get_known_words method."""

    def test_returns_all_lemmas(self, tmp_path):
        """Should return all lemmas as a set."""
        db = KnownWordDB(tmp_path / "known_words.db")
        db.initialize()
        db.add_words({"食べる", "飲む", "走る"})
        result = db.get_known_words()
        assert result == {"食べる", "飲む", "走る"}

    def test_empty_database(self, tmp_path):
        """Should return empty set when database is empty."""
        db = KnownWordDB(tmp_path / "known_words.db")
        db.initialize()
        result = db.get_known_words()
        assert result == set()


class TestSyncWithAnki:
    """Tests for sync_with_anki method."""

    def test_adds_new_words_from_anki(self, tmp_path):
        """Should add words from Anki that aren't in the DB."""
        db = KnownWordDB(tmp_path / "known_words.db")
        db.initialize()
        db.add_words({"食べる"})

        added, total = db.sync_with_anki({"食べる", "飲む", "走る"})
        assert added == 2
        assert total == 3

    def test_does_not_remove_old_words(self, tmp_path):
        """Should keep words that are in DB but not in Anki anymore."""
        db = KnownWordDB(tmp_path / "known_words.db")
        db.initialize()
        db.add_words({"食べる", "飲む", "走る"})

        # Anki only has 食べる now — the others should NOT be removed
        added, total = db.sync_with_anki({"食べる"})
        assert added == 0
        assert total == 3
        assert db.get_known_words() == {"食べる", "飲む", "走る"}

    def test_sync_empty_anki(self, tmp_path):
        """Should handle empty Anki vocabulary."""
        db = KnownWordDB(tmp_path / "known_words.db")
        db.initialize()
        db.add_words({"食べる"})

        added, total = db.sync_with_anki(set())
        assert added == 0
        assert total == 1

    def test_sync_empty_db(self, tmp_path):
        """Should add all Anki words to an empty DB."""
        db = KnownWordDB(tmp_path / "known_words.db")
        db.initialize()

        added, total = db.sync_with_anki({"食べる", "飲む"})
        assert added == 2
        assert total == 2


class TestWordCount:
    """Tests for word_count method."""

    def test_zero_when_empty(self, tmp_path):
        """Should return 0 for empty database."""
        db = KnownWordDB(tmp_path / "known_words.db")
        db.initialize()
        assert db.word_count() == 0

    def test_correct_after_adds(self, tmp_path):
        """Should return correct count after multiple operations."""
        db = KnownWordDB(tmp_path / "known_words.db")
        db.initialize()
        db.add_words({"食べる", "飲む"})
        assert db.word_count() == 2
        db.add_words({"走る"})
        assert db.word_count() == 3
        db.add_words({"食べる"})  # duplicate
        assert db.word_count() == 3


class TestClear:
    """Tests for KnownWordDB.clear (Issue #38)."""

    def test_clear_empties_table_and_returns_count(self, tmp_path):
        """clear() removes all rows and returns how many were removed."""
        db = KnownWordDB(tmp_path / "known_words.db")
        db.initialize()
        db.add_words({"食べる", "飲む", "走る"})

        removed = db.clear()

        assert removed == 3
        assert db.word_count() == 0
        assert db.get_known_words() == set()

    def test_clear_empty_db_returns_zero(self, tmp_path):
        """Clearing an empty DB removes nothing."""
        db = KnownWordDB(tmp_path / "known_words.db")
        db.initialize()
        assert db.clear() == 0

    def test_clear_preserve_user_keeps_user_rows(self, tmp_path):
        """clear(preserve_user=True) removes synced rows but keeps source='user' (Issue #42)."""
        db = KnownWordDB(tmp_path / "known_words.db")
        db.initialize()
        db.add_words({"食べる", "飲む"}, source="anki")
        db.add_words({"ラーメン"}, source="user")

        removed = db.clear(preserve_user=True)

        assert removed == 2
        assert db.get_known_words() == {"ラーメン"}
        assert db.get_words_by_source("user") == {"ラーメン"}


class TestGetWordsBySource:
    """Tests for get_words_by_source (Issue #42)."""

    def test_returns_only_matching_source(self, tmp_path):
        db = KnownWordDB(tmp_path / "known_words.db")
        db.initialize()
        db.add_words({"食べる", "飲む"}, source="anki")
        db.add_words({"ラーメン", "カレー"}, source="user")
        assert db.get_words_by_source("user") == {"ラーメン", "カレー"}
        assert db.get_words_by_source("anki") == {"食べる", "飲む"}

    def test_empty_when_no_match(self, tmp_path):
        db = KnownWordDB(tmp_path / "known_words.db")
        db.initialize()
        db.add_words({"食べる"}, source="anki")
        assert db.get_words_by_source("user") == set()


class TestRemoveWords:
    """Tests for remove_words (Issue #42)."""

    def test_removes_and_returns_count(self, tmp_path):
        db = KnownWordDB(tmp_path / "known_words.db")
        db.initialize()
        db.add_words({"ラーメン", "カレー", "寿司"}, source="user")
        removed = db.remove_words({"ラーメン", "カレー"})
        assert removed == 2
        assert db.get_known_words() == {"寿司"}

    def test_ignores_unknown_words(self, tmp_path):
        db = KnownWordDB(tmp_path / "known_words.db")
        db.initialize()
        db.add_words({"寿司"}, source="user")
        removed = db.remove_words({"存在しない"})
        assert removed == 0
        assert db.get_known_words() == {"寿司"}

    def test_empty_set(self, tmp_path):
        db = KnownWordDB(tmp_path / "known_words.db")
        db.initialize()
        db.add_words({"寿司"}, source="user")
        assert db.remove_words(set()) == 0


class TestClearUser:
    """Tests for clear_user (Issue #42)."""

    def test_removes_only_user_rows(self, tmp_path):
        db = KnownWordDB(tmp_path / "known_words.db")
        db.initialize()
        db.add_words({"食べる"}, source="anki")
        db.add_words({"ラーメン", "カレー"}, source="user")
        removed = db.clear_user()
        assert removed == 2
        assert db.get_known_words() == {"食べる"}
        assert db.get_words_by_source("user") == set()


class TestExclusiveLock:
    """Pin the behaviour the caller must tolerate when the DB file is locked.

    Anki (or a parallel mining run) can hold ``known_words.db`` with an
    exclusive write lock; SQLite raises ``OperationalError('database is
    locked')`` for writers that can't acquire it. ``EpisodeProcessor`` wraps
    the post-create ``add_words`` so this no longer discards a successful run
    (T-19). These tests pin the raise so a future busy_timeout change is a
    conscious decision.
    """

    def test_add_words_raises_when_exclusively_locked(self, tmp_path):
        import sqlite3

        db_path = tmp_path / "known_words.db"
        db = KnownWordDB(db_path)
        db.initialize()

        holder = sqlite3.connect(db_path, isolation_level=None)
        try:
            holder.execute("BEGIN EXCLUSIVE")
            with pytest.raises(sqlite3.OperationalError, match="database is locked"):
                db.add_words({"食べる"})
        finally:
            holder.rollback()
            holder.close()

    def test_get_known_words_raises_when_exclusively_locked(self, tmp_path):
        import sqlite3

        db_path = tmp_path / "known_words.db"
        db = KnownWordDB(db_path)
        db.initialize()
        db.add_words({"飲む"})

        holder = sqlite3.connect(db_path, isolation_level=None)
        try:
            holder.execute("BEGIN EXCLUSIVE")
            with pytest.raises(sqlite3.OperationalError, match="database is locked"):
                db.get_known_words()
        finally:
            holder.rollback()
            holder.close()
