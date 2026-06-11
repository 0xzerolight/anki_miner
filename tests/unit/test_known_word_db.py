"""Tests for KnownWordDB service."""

import os
import sqlite3
import stat

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


class TestSourceUpgrade:
    """Marking a word 'known' must upgrade an existing anki/mined row to 'user'
    so it survives Rebuild (Issue #42, T-27).

    The PRIMARY KEY is ``lemma`` and the old ``INSERT OR IGNORE`` no-op'd when
    the row already existed under ``source='anki'``; ``clear(preserve_user=True)``
    on Rebuild then deleted that anki row and the user's mark was lost. The fix
    promotes to 'user' on conflict but never downgrades a 'user' row.
    """

    def _source_of(self, tmp_path, db_path, lemma):
        import sqlite3

        conn = sqlite3.connect(db_path)
        try:
            row = conn.execute("SELECT source FROM known_words WHERE lemma = ?", (lemma,)).fetchone()
        finally:
            conn.close()
        return row[0] if row else None

    def test_user_mark_over_existing_anki_upgrades_source(self, tmp_path):
        db_path = tmp_path / "known_words.db"
        db = KnownWordDB(db_path)
        db.initialize()
        db.add_words({"食べる"}, source="anki")

        db.add_words({"食べる"}, source="user")

        assert self._source_of(tmp_path, db_path, "食べる") == "user"
        assert db.get_words_by_source("user") == {"食べる"}

    def test_user_mark_survives_rebuild(self, tmp_path):
        """The end-to-end invariant: anki row, marked user, survives Rebuild."""
        db = KnownWordDB(tmp_path / "known_words.db")
        db.initialize()
        db.add_words({"食べる"}, source="anki")
        db.add_words({"食べる"}, source="user")  # user marks it known

        db.clear(preserve_user=True)  # Rebuild Known Words DB

        assert db.get_known_words() == {"食べる"}
        assert db.get_words_by_source("user") == {"食べる"}

    def test_anki_over_existing_user_does_not_downgrade(self, tmp_path):
        """A later sync (source='anki'/'mined') must NOT clobber a 'user' row."""
        db_path = tmp_path / "known_words.db"
        db = KnownWordDB(db_path)
        db.initialize()
        db.add_words({"ラーメン"}, source="user")

        db.add_words({"ラーメン"}, source="anki")

        assert self._source_of(tmp_path, db_path, "ラーメン") == "user"
        assert db.get_words_by_source("user") == {"ラーメン"}

    def test_mined_over_existing_user_does_not_downgrade(self, tmp_path):
        db_path = tmp_path / "known_words.db"
        db = KnownWordDB(db_path)
        db.initialize()
        db.add_words({"寿司"}, source="user")

        db.add_words({"寿司"}, source="mined")

        assert self._source_of(tmp_path, db_path, "寿司") == "user"

    def test_user_mark_idempotent_returns_zero_new(self, tmp_path):
        """Re-marking an existing anki row as user adds no NEW rows."""
        db = KnownWordDB(tmp_path / "known_words.db")
        db.initialize()
        db.add_words({"食べる"}, source="anki")

        new_count = db.add_words({"食べる"}, source="user")

        assert new_count == 0
        assert db.word_count() == 1


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


class TestCorruptDatabaseFile:
    """A corrupt ``known_words.db`` (truncated download, disk fault, foreign
    file) must surface a hard SQLite error rather than silently returning an
    empty set — that would make every word look unknown and re-mine the whole
    collection. These pin the raise so any future "heal a corrupt DB" handling
    is a conscious change.

    Note ``is_available`` only checks existence + readability, so it reports
    True for a corrupt file; the failure manifests on first query/write.
    """

    @staticmethod
    def _corrupt(db_path):
        db_path.write_bytes(b"this is not a sqlite database " * 8)

    def test_is_available_true_even_when_corrupt(self, tmp_path):
        """is_available is a cheap existence probe, NOT an integrity check."""
        db_path = tmp_path / "known_words.db"
        self._corrupt(db_path)
        db = KnownWordDB(db_path)
        assert db.is_available() is True

    def test_initialize_on_corrupt_raises_database_error(self, tmp_path):
        db_path = tmp_path / "known_words.db"
        self._corrupt(db_path)
        db = KnownWordDB(db_path)
        with pytest.raises(sqlite3.DatabaseError):
            db.initialize()

    def test_get_known_words_on_corrupt_raises_database_error(self, tmp_path):
        db_path = tmp_path / "known_words.db"
        self._corrupt(db_path)
        db = KnownWordDB(db_path)
        with pytest.raises(sqlite3.DatabaseError):
            db.get_known_words()

    def test_word_count_on_corrupt_raises_database_error(self, tmp_path):
        db_path = tmp_path / "known_words.db"
        self._corrupt(db_path)
        db = KnownWordDB(db_path)
        with pytest.raises(sqlite3.DatabaseError):
            db.word_count()

    def test_add_words_on_corrupt_raises_database_error(self, tmp_path):
        db_path = tmp_path / "known_words.db"
        self._corrupt(db_path)
        db = KnownWordDB(db_path)
        with pytest.raises(sqlite3.DatabaseError):
            db.add_words({"食べる"})

    def test_get_words_by_source_on_corrupt_raises_database_error(self, tmp_path):
        db_path = tmp_path / "known_words.db"
        self._corrupt(db_path)
        db = KnownWordDB(db_path)
        with pytest.raises(sqlite3.DatabaseError):
            db.get_words_by_source("user")


@pytest.mark.skipif(
    os.geteuid() == 0,
    reason="root bypasses filesystem write permissions",
)
class TestUnwritableParent:
    """``initialize`` must not silently swallow a filesystem permission failure.

    A read-only ANKI_MINER_HOME (locked-down profile, mounted read-only) means
    the cache can never be created; the OSError must propagate so the caller can
    surface it rather than carry on with a phantom empty DB.
    """

    def test_initialize_creating_subdir_under_readonly_parent_raises(self, tmp_path):
        """``mkdir(parents=True)`` of a missing subdir under a read-only parent."""
        ro = tmp_path / "readonly"
        ro.mkdir()
        os.chmod(ro, stat.S_IRUSR | stat.S_IXUSR)  # r-x, no write
        target = ro / "sub" / "known_words.db"
        db = KnownWordDB(target)
        try:
            with pytest.raises(PermissionError):
                db.initialize()
        finally:
            os.chmod(ro, stat.S_IRWXU)  # restore so tmp_path cleanup works

    def test_initialize_connect_under_readonly_existing_parent_raises(self, tmp_path):
        """Parent exists but is read-only: ``mkdir(exist_ok=True)`` no-ops and the
        sqlite connect fails with ``unable to open database file``."""
        ro = tmp_path / "readonly"
        ro.mkdir()
        os.chmod(ro, stat.S_IRUSR | stat.S_IXUSR)
        target = ro / "known_words.db"
        db = KnownWordDB(target)
        try:
            with pytest.raises(sqlite3.OperationalError, match="unable to open database file"):
                db.initialize()
        finally:
            os.chmod(ro, stat.S_IRWXU)
