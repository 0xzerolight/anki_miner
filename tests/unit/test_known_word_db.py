"""Tests for KnownWordDB service."""

import os
import sqlite3
import stat
import sys
import threading

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

    def test_add_words_with_receipt_returns_only_new_forms(self, tmp_path):
        db = KnownWordDB(tmp_path / "known_words.db")
        db.initialize()
        db.add_words({"食べる"}, source="mined")

        inserted = db.add_words_with_receipt({"食べる", "猫"}, source="mined")

        assert inserted == {"猫"}


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
    sys.platform == "win32" or os.geteuid() == 0,
    reason="POSIX permission bits not applicable on Windows; root bypasses them",
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


# ---------------------------------------------------------------------------
# OVH-030 — remove_words with source filter
# ---------------------------------------------------------------------------


class TestRemoveWordsSourceFilter:
    """remove_words(source=...) deletes only rows with matching source (OVH-030).

    Issue #42 invariant: source='user' rows are NEVER touched by the 'mined'
    removal path; a word present under both 'mined' and 'anki' keeps its 'anki' row.
    """

    @pytest.fixture
    def db(self, tmp_path):
        db = KnownWordDB(tmp_path / "known_words.db")
        db.initialize()
        return db

    def test_remove_mined_leaves_user_row(self, db):
        """Removing source='mined' must never delete source='user' entries."""
        # Add as mined first (INSERT OR IGNORE), then upgrade to user.
        db.add_words({"食べる"}, source="mined")
        # Manually insert a user row for a different word.
        db.add_words({"ラーメン"}, source="user")

        removed = db.remove_words({"食べる", "ラーメン"}, source="mined")

        assert removed == 1  # only the mined row
        assert db.get_known_words() == {"ラーメン"}
        assert db.get_words_by_source("user") == {"ラーメン"}

    def test_remove_mined_leaves_anki_row(self, db):
        """A word stored as 'anki' is not touched when removing source='mined'."""
        db.add_words({"走る"}, source="anki")

        removed = db.remove_words({"走る"}, source="mined")

        assert removed == 0
        assert db.get_known_words() == {"走る"}

    def test_remove_mined_only_removes_mined(self, db):
        """Only mined rows are removed when source='mined' is specified."""
        db.add_words({"猫"}, source="mined")
        db.add_words({"犬"}, source="anki")

        removed = db.remove_words({"猫", "犬"}, source="mined")

        assert removed == 1
        assert db.get_known_words() == {"犬"}

    def test_remove_without_source_removes_all_matching(self, db):
        """Without a source filter, all rows for the given lemmas are removed."""
        db.add_words({"食べる"}, source="mined")
        db.add_words({"飲む"}, source="anki")

        removed = db.remove_words({"食べる", "飲む"})

        assert removed == 2
        assert db.get_known_words() == set()

    def test_remove_empty_set_returns_zero(self, db):
        """Removing an empty set is a no-op."""
        db.add_words({"食べる"}, source="mined")
        assert db.remove_words(set(), source="mined") == 0
        assert db.word_count() == 1


class TestUnicodeNormalization:
    """Canonically equivalent lemmas must share one row (NFC)."""

    #: が written as か + U+3099 (combining voiced sound mark).
    NFD = "がくせい"
    NFC = "がくせい"

    @pytest.fixture
    def db(self, tmp_path):
        db = KnownWordDB(tmp_path / "known_words.db")
        db.initialize()
        return db

    def test_add_stores_the_composed_form(self, db):
        """An NFD import is stored NFC so tokenizer output matches it."""
        db.add_words({self.NFD})
        assert db.get_known_words() == {self.NFC}

    def test_canonical_duplicates_collapse_to_one_row(self, db):
        """The same word in both forms is one lemma, not two."""
        db.add_words({self.NFD, self.NFC})
        assert db.word_count() == 1

    def test_remove_accepts_either_form(self, db):
        """Removal normalizes too, so the decomposed spelling still matches."""
        db.add_words({self.NFC})
        assert db.remove_words({self.NFD}) == 1
        assert db.get_known_words() == set()

    def test_receipt_reports_the_stored_form(self, db):
        """The undo receipt must name the lemma that was actually written."""
        assert db.add_words_with_receipt({self.NFD}, source="mined") == {self.NFC}

    def test_sync_does_not_re_add_a_decomposed_twin(self, db):
        """An NFD spelling from Anki is not a new word when the NFC row exists."""
        db.add_words({self.NFC}, source="anki")
        added, total = db.sync_with_anki({self.NFD})
        assert added == 0
        assert total == 1


class TestNfcMigration:
    """Rows written before normalization are rewritten once."""

    NFD = "がくせい"
    NFC = "がくせい"

    def _legacy_db(self, tmp_path, rows):
        """Create a pre-migration database holding raw, unnormalized rows."""
        db_path = tmp_path / "known_words.db"
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "CREATE TABLE known_words ("
                "lemma TEXT PRIMARY KEY, "
                "source TEXT DEFAULT 'anki', "
                "added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
            )
            conn.executemany(
                "INSERT INTO known_words (lemma, source, added_at) VALUES (?, ?, ?)",
                rows,
            )
            conn.commit()
        return db_path

    def test_rewrites_decomposed_rows(self, tmp_path):
        """A legacy NFD row becomes reachable by its NFC spelling."""
        db_path = self._legacy_db(tmp_path, [(self.NFD, "user", "2026-01-01")])
        db = KnownWordDB(db_path)
        db.initialize()
        assert db.get_known_words() == {self.NFC}
        assert db.get_words_by_source("user") == {self.NFC}

    def test_reads_normalize_without_the_migration(self, tmp_path):
        """The user ignore list is read on every run; initialize() is not called.

        service_factory only initializes the DB when use_known_words_db is on,
        but episode_processor reads source='user' regardless (Issue #42) — so a
        migration-gated fold alone would leave exactly that population still
        re-carding words they marked known.
        """
        db_path = self._legacy_db(tmp_path, [(self.NFD, "user", "2026-01-01")])
        db = KnownWordDB(db_path)  # deliberately NOT initialized

        assert db.get_words_by_source("user") == {self.NFC}
        assert db.get_known_words() == {self.NFC}

    def test_merges_collisions_keeping_user_source(self, tmp_path):
        """Two spellings of one word merge, and a curated mark is never lost."""
        db_path = self._legacy_db(
            tmp_path,
            [(self.NFD, "user", "2026-01-02"), (self.NFC, "anki", "2026-01-01")],
        )
        db = KnownWordDB(db_path)
        db.initialize()
        assert db.word_count() == 1
        assert db.get_words_by_source("user") == {self.NFC}

    def test_merge_keeps_the_earliest_added_at(self, tmp_path):
        """The surviving row keeps the date the word was first known."""
        db_path = self._legacy_db(
            tmp_path,
            [(self.NFD, "anki", "2026-05-05"), (self.NFC, "anki", "2026-01-01")],
        )
        db = KnownWordDB(db_path)
        db.initialize()
        with sqlite3.connect(db_path) as conn:
            assert conn.execute("SELECT added_at FROM known_words").fetchone()[0] == "2026-01-01"

    def test_runs_once(self, tmp_path):
        """A migrated database is not rescanned on the next initialize."""
        db_path = self._legacy_db(tmp_path, [(self.NFD, "anki", "2026-01-01")])
        db = KnownWordDB(db_path)
        db.initialize()
        with sqlite3.connect(db_path) as conn:
            assert int(conn.execute("PRAGMA user_version").fetchone()[0]) == 1
        db.initialize()
        assert db.get_known_words() == {self.NFC}

    def test_already_normalized_database_is_untouched(self, tmp_path):
        """Nothing is rewritten when every row is already canonical."""
        db_path = self._legacy_db(tmp_path, [(self.NFC, "user", "2026-01-01")])
        KnownWordDB(db_path).initialize()
        with sqlite3.connect(db_path) as conn:
            row = conn.execute("SELECT lemma, source, added_at FROM known_words").fetchone()
        assert row == (self.NFC, "user", "2026-01-01")

    def test_concurrent_writer_waits_for_migration_snapshot(self, tmp_path, monkeypatch):
        """The migration lock must cover its version check, read, and rewrite."""
        import anki_miner.services.known_word_db as known_word_db_mod

        db_path = self._legacy_db(tmp_path, [(self.NFD, "anki", "2026-01-01")])
        migration_reading = threading.Event()
        release_migration = threading.Event()
        writer_committed = threading.Event()
        errors: list[BaseException] = []
        real_normalize = known_word_db_mod.normalize_lemma

        def pause_while_normalizing(word):
            migration_reading.set()
            assert release_migration.wait(5)
            return real_normalize(word)

        def initialize():
            try:
                KnownWordDB(db_path).initialize()
            except BaseException as exc:  # pragma: no cover - thread receipt
                errors.append(exc)

        def write_new_word():
            try:
                with sqlite3.connect(db_path) as conn:
                    conn.execute(
                        "INSERT INTO known_words (lemma, source) VALUES (?, ?)",
                        ("新規", "user"),
                    )
                    conn.commit()
                writer_committed.set()
            except BaseException as exc:  # pragma: no cover - thread receipt
                errors.append(exc)

        monkeypatch.setattr(known_word_db_mod, "normalize_lemma", pause_while_normalizing)
        migration = threading.Thread(target=initialize)
        writer = threading.Thread(target=write_new_word)
        migration.start()
        assert migration_reading.wait(5)
        writer.start()
        try:
            assert not writer_committed.wait(0.2), "writer committed inside the migration snapshot"
        finally:
            release_migration.set()
            migration.join(5)
            writer.join(5)

        assert not migration.is_alive()
        assert not writer.is_alive()
        assert errors == []
        assert KnownWordDB(db_path).get_known_words() == {self.NFC, "新規"}


class TestRunCache:
    """The known/source sets and the Anki-vocabulary normalization are
    memoized for the object's lifetime (T24).

    The batch worker keeps one KnownWordDB alive for the whole run (T20), so a
    full-table scan + NFC-normalize on every ``get_known_words()`` /
    ``get_words_by_source()`` call was pure repeat work within a run. Every
    writer invalidates the memo so a run never serves cross-write-stale data.
    """

    @staticmethod
    def _counting_connect(db, monkeypatch):
        """Count how many connections a DB opens after this call."""
        calls: list[int] = []
        real_connect = db._connect

        def counting():
            calls.append(1)
            return real_connect()

        monkeypatch.setattr(db, "_connect", counting)
        return calls

    def test_get_known_words_scans_once_across_calls(self, tmp_path, monkeypatch):
        db = KnownWordDB(tmp_path / "known_words.db")
        db.initialize()
        db.add_words({"食べる", "飲む"})

        calls = self._counting_connect(db, monkeypatch)
        first = db.get_known_words()
        second = db.get_known_words()

        assert len(calls) == 1  # one connection opened == one SELECT
        assert first == second == {"食べる", "飲む"}
        assert second is first  # same cached object, not a fresh scan

    def test_get_words_by_source_scans_once_across_calls(self, tmp_path, monkeypatch):
        db = KnownWordDB(tmp_path / "known_words.db")
        db.initialize()
        db.add_words({"ラーメン"}, source="user")

        calls = self._counting_connect(db, monkeypatch)
        first = db.get_words_by_source("user")
        second = db.get_words_by_source("user")

        assert len(calls) == 1
        assert first == second == {"ラーメン"}
        assert second is first

    @pytest.mark.parametrize(
        "write",
        [
            lambda db: db.add_words({"新規"}, source="anki"),
            lambda db: db.add_words_with_receipt({"新規2"}, source="anki"),
            lambda db: db.remove_words({"食べる"}),
            lambda db: db.clear(),
            lambda db: db.clear(preserve_user=True),
            lambda db: db.clear_user(),
        ],
        ids=[
            "add_words",
            "add_words_with_receipt",
            "remove_words",
            "clear",
            "clear_preserve_user",
            "clear_user",
        ],
    )
    def test_every_writer_invalidates_the_memo(self, tmp_path, write):
        db = KnownWordDB(tmp_path / "known_words.db")
        db.initialize()
        db.add_words({"食べる"}, source="user")

        known_before = db.get_known_words()
        user_before = db.get_words_by_source("user")

        write(db)

        known_after = db.get_known_words()
        user_after = db.get_words_by_source("user")

        assert known_after is not known_before
        assert user_after is not user_before

    def test_sync_with_anki_normalizes_same_vocab_object_once(self, tmp_path, monkeypatch):
        """Passing the identical set object twice normalizes it only once.

        The batch worker's AnkiService caches ``get_existing_vocabulary()``
        for the run, so ``sync_with_anki`` sees the SAME set object on every
        queue item — that identity is the memo key.
        """
        import anki_miner.services.known_word_db as known_word_db_mod

        db = KnownWordDB(tmp_path / "known_words.db")
        db.initialize()

        anki_vocab = {"食べる", "飲む"}
        hits_on_anki_vocab = []
        real_normalize_all = known_word_db_mod._normalize_all

        def spy(words):
            if words is anki_vocab:
                hits_on_anki_vocab.append(1)
            return real_normalize_all(words)

        monkeypatch.setattr(known_word_db_mod, "_normalize_all", spy)

        db.sync_with_anki(anki_vocab)
        db.sync_with_anki(anki_vocab)  # same object again

        assert hits_on_anki_vocab == [1]

    def test_normalize_anki_vocabulary_different_object_equal_content(self, tmp_path, monkeypatch):
        """A NEW object with the SAME content still gives the correct result.

        The memo is single-slot, keyed by strict identity: an equal-content
        object that is not the retained ref is a cache MISS, so it
        renormalizes rather than risk serving stale data — accepted trade for
        a batch loop that always passes the one AnkiService-cached object.
        """
        import anki_miner.services.known_word_db as known_word_db_mod

        db = KnownWordDB(tmp_path / "known_words.db")
        db.initialize()

        first_vocab = {"食べる", "飲む"}
        second_vocab = set(first_vocab)  # new object, equal content
        assert second_vocab is not first_vocab

        hits_on_second_vocab = []
        real_normalize_all = known_word_db_mod._normalize_all

        def spy(words):
            if words is second_vocab:
                hits_on_second_vocab.append(1)
            return real_normalize_all(words)

        monkeypatch.setattr(known_word_db_mod, "_normalize_all", spy)

        db.sync_with_anki(first_vocab)
        added, total = db.sync_with_anki(second_vocab)

        assert added == 0  # correct result despite the identity miss
        assert total == 2
        assert hits_on_second_vocab == [1]  # identity miss re-normalizes

    def test_normalize_anki_vocabulary_different_object_different_content(self, tmp_path):
        """A genuinely different vocab object is never served the stale set.

        Regression guard for keying the memo on a retained strong reference
        rather than a bare ``id()`` — the failure mode a recycled id would
        cause is exactly a different object silently getting the old result.
        """
        db = KnownWordDB(tmp_path / "known_words.db")

        first = db._normalize_anki_vocabulary({"食べる"})
        second = db._normalize_anki_vocabulary({"飲む"})

        assert first == {"食べる"}
        assert second == {"飲む"}

    def test_sync_with_anki_normalizes_explicit_existing_argument(self, tmp_path):
        """The ``existing`` fast path only skips normalizing this object's OWN
        memo; an external caller-supplied set is still normalized correctly.
        """
        #: が written as か + U+3099 (combining voiced sound mark).
        nfd = "がくせい"
        db = KnownWordDB(tmp_path / "known_words.db")
        db.initialize()

        existing = {nfd}  # not this object's known-set memo
        added, total = db.sync_with_anki({nfd}, existing=existing)

        assert added == 0  # both sides fold to the same NFC lemma
        assert total == 1
