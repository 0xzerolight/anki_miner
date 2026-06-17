"""Service for managing a local SQLite database of known words."""

import logging
import os
import sqlite3
from contextlib import closing
from pathlib import Path

logger = logging.getLogger(__name__)


class KnownWordDB:
    """Persistent local database of known words.

    Caches known vocabulary in a SQLite database so that AnkiConnect
    does not need to be queried for the full word list on every run.
    Supports differential sync: words are only added, never removed.
    """

    def __init__(self, db_path: Path):
        """Initialize the known word database.

        Args:
            db_path: Path to the SQLite database file.
        """
        self._db_path = db_path

    def initialize(self) -> None:
        """Create the database and schema if they don't exist.

        Creates the parent directories and the known_words table.
        """
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self._db_path)) as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS known_words ("
                "lemma TEXT PRIMARY KEY, "
                "source TEXT DEFAULT 'anki', "
                "added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
                ")"
            )
            conn.commit()

    def is_available(self) -> bool:
        """Check if the database file exists and is readable.

        Returns:
            True if the database is ready for use.
        """
        return self._db_path.exists() and os.access(self._db_path, os.R_OK)

    def get_known_words(self) -> set[str]:
        """Return all known word lemmas.

        Returns:
            Set of all lemma strings in the database.
        """
        with closing(sqlite3.connect(self._db_path)) as conn:
            cursor = conn.execute("SELECT lemma FROM known_words")
            return {row[0] for row in cursor.fetchall()}

    def get_words_by_source(self, source: str) -> set[str]:
        """Return all lemmas stored under a given source label.

        Used for the user-curated ignore list (Issue #42): ``source='user'``
        words are applied on every mining run regardless of
        ``config.use_known_words_db``.

        Args:
            source: Source label to filter on (e.g. 'anki', 'user').

        Returns:
            Set of lemma strings with the matching source.
        """
        with closing(sqlite3.connect(self._db_path)) as conn:
            cursor = conn.execute("SELECT lemma FROM known_words WHERE source = ?", (source,))
            return {row[0] for row in cursor.fetchall()}

    def add_words(self, words: set[str], source: str = "anki") -> int:
        """Bulk insert words into the database, ignoring duplicates.

        Args:
            words: Set of lemma strings to add.
            source: Source label (e.g. 'anki', 'mined', 'user').

        Returns:
            Number of newly inserted rows (an in-place source upgrade is not
            counted as new).
        """
        if not words:
            return 0

        # ``lemma`` is the PRIMARY KEY, so a plain INSERT OR IGNORE no-ops when
        # the row already exists. That silently dropped a user "mark known" when
        # the lemma was already cached as source='anki': the mark never took, and
        # clear(preserve_user=True) on Rebuild then deleted the anki row — losing
        # the user's entry and violating the Issue #42 "user list survives
        # rebuild" invariant (T-27).
        #
        # When marking as 'user' we therefore UPGRADE an existing row's source on
        # conflict. For every other source (anki/mined) we keep IGNORE so a later
        # sync can never DOWNGRADE a 'user' row back to 'anki'.
        with closing(sqlite3.connect(self._db_path)) as conn:
            before = self._count(conn)
            if source == "user":
                conn.executemany(
                    "INSERT INTO known_words (lemma, source) VALUES (?, ?) "
                    "ON CONFLICT(lemma) DO UPDATE SET source=excluded.source",
                    [(w, source) for w in words],
                )
            else:
                conn.executemany(
                    "INSERT OR IGNORE INTO known_words (lemma, source) VALUES (?, ?)",
                    [(w, source) for w in words],
                )
            conn.commit()
            after = self._count(conn)
            return after - before

    def sync_with_anki(
        self,
        anki_vocabulary: set[str],
        existing: set[str] | None = None,
    ) -> tuple[int, int]:
        """Differential sync: add words from Anki that are not yet in the DB.

        Words that are in the DB but no longer in Anki are NOT removed
        (the user may have deleted a card but still knows the word).

        Args:
            anki_vocabulary: Current set of vocabulary from AnkiConnect.
            existing: Pre-fetched current known set. When supplied, skips the
                internal full-table scan — callers that already hold the set
                (e.g. episode_processor filtering before sync) should pass it.

        Returns:
            Tuple of (newly_added_count, total_count).
        """
        if existing is None:
            existing = self.get_known_words()
        new_words = anki_vocabulary - existing
        added = self.add_words(new_words, source="anki")
        return (added, len(existing) + added)

    def remove_words(self, words: set[str], source: str | None = None) -> int:
        """Delete specific words from the database (Issue #42).

        Used by the Manage Known Words dialog to remove individual user-added
        entries, and by the Undo callback to revert ``source='mined'`` rows
        without touching ``source='user'`` or ``source='anki'`` rows (OVH-030).

        Args:
            words: Set of lemma strings to remove.
            source: When given, only rows whose ``source`` matches this value
                are removed.  When ``None`` (default), all rows matching the
                lemma are removed regardless of source.  Pass ``source='mined'``
                from the Undo path to scope removal to the session's newly mined
                rows and leave user-curated (Issue #42) and Anki-synced rows
                untouched.

        Returns:
            Number of rows actually removed.
        """
        if not words:
            return 0

        with closing(sqlite3.connect(self._db_path)) as conn:
            before = self._count(conn)
            if source is None:
                conn.executemany("DELETE FROM known_words WHERE lemma = ?", [(w,) for w in words])
            else:
                conn.executemany(
                    "DELETE FROM known_words WHERE lemma = ? AND source = ?",
                    [(w, source) for w in words],
                )
            conn.commit()
            after = self._count(conn)
            return before - after

    def clear(self, preserve_user: bool = False) -> int:
        """Delete known words from the database.

        Used by the "Rebuild Known Words DB" action (Issue #38) so that deck
        exclusions take effect for users of the local cache: the additive
        ``sync_with_anki`` never removes words, so a previously-synced excluded
        deck would otherwise stay cached forever.

        Args:
            preserve_user: When True, keep ``source='user'`` rows (the curated
                ignore list, Issue #42). Rebuild passes True so user-added words
                survive a cache rebuild; the default False keeps the full-wipe
                behaviour for any other caller.

        Returns:
            Number of rows removed.
        """
        with closing(sqlite3.connect(self._db_path)) as conn:
            before = self._count(conn)
            if preserve_user:
                conn.execute("DELETE FROM known_words WHERE source != 'user'")
            else:
                conn.execute("DELETE FROM known_words")
            conn.commit()
            after = self._count(conn)
            return before - after

    def clear_user(self) -> int:
        """Delete only the user-curated ignore list (Issue #42).

        Backs the "Reset User List" action in the Manage Known Words dialog.

        Returns:
            Number of ``source='user'`` rows removed.
        """
        with closing(sqlite3.connect(self._db_path)) as conn:
            before = self._count(conn)
            conn.execute("DELETE FROM known_words WHERE source = 'user'")
            conn.commit()
            after = self._count(conn)
            return before - after

    def word_count(self) -> int:
        """Return the total number of known words.

        Returns:
            Count of rows in the known_words table.
        """
        with closing(sqlite3.connect(self._db_path)) as conn:
            return self._count(conn)

    @staticmethod
    def _count(conn: sqlite3.Connection) -> int:
        """Count rows in the known_words table using an open connection."""
        cursor = conn.execute("SELECT COUNT(*) FROM known_words")
        return int(cursor.fetchone()[0])
