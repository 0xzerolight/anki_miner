"""Service for managing a local SQLite database of known words."""

import logging
import os
import sqlite3
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
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS known_words ("
                "lemma TEXT PRIMARY KEY, "
                "source TEXT DEFAULT 'anki', "
                "added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP"
                ")"
            )
            conn.commit()
        finally:
            conn.close()

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
        conn = sqlite3.connect(self._db_path)
        try:
            cursor = conn.execute("SELECT lemma FROM known_words")
            return {row[0] for row in cursor.fetchall()}
        finally:
            conn.close()

    def add_words(self, words: set[str], source: str = "anki") -> int:
        """Bulk insert words into the database, ignoring duplicates.

        Args:
            words: Set of lemma strings to add.
            source: Source label (e.g. 'anki', 'mined').

        Returns:
            Number of newly inserted rows.
        """
        if not words:
            return 0

        conn = sqlite3.connect(self._db_path)
        try:
            before = self._count(conn)
            conn.executemany(
                "INSERT OR IGNORE INTO known_words (lemma, source) VALUES (?, ?)",
                [(w, source) for w in words],
            )
            conn.commit()
            after = self._count(conn)
            return after - before
        finally:
            conn.close()

    def sync_with_anki(self, anki_vocabulary: set[str]) -> tuple[int, int]:
        """Differential sync: add words from Anki that are not yet in the DB.

        Words that are in the DB but no longer in Anki are NOT removed
        (the user may have deleted a card but still knows the word).

        Args:
            anki_vocabulary: Current set of vocabulary from AnkiConnect.

        Returns:
            Tuple of (newly_added_count, total_count).
        """
        existing = self.get_known_words()
        new_words = anki_vocabulary - existing
        added = self.add_words(new_words, source="anki")
        return (added, self.word_count())

    def word_count(self) -> int:
        """Return the total number of known words.

        Returns:
            Count of rows in the known_words table.
        """
        conn = sqlite3.connect(self._db_path)
        try:
            return self._count(conn)
        finally:
            conn.close()

    @staticmethod
    def _count(conn: sqlite3.Connection) -> int:
        """Count rows in the known_words table using an open connection."""
        cursor = conn.execute("SELECT COUNT(*) FROM known_words")
        return cursor.fetchone()[0]
