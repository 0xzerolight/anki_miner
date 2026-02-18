"""SQLite-backed processing history service."""

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from anki_miner.models.processing import ProcessingResult

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path.home() / ".anki_miner" / "history.db"


class HistoryService:
    """Service for recording and querying mining session history.

    Uses SQLite for persistent storage across sessions.
    """

    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        """Initialize the history service.

        Args:
            db_path: Path to the SQLite database file
        """
        self.db_path = db_path

    def initialize(self) -> None:
        """Create the database and table if they don't exist."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS mining_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    video_file TEXT NOT NULL DEFAULT '',
                    subtitle_file TEXT NOT NULL DEFAULT '',
                    series_name TEXT NOT NULL DEFAULT '',
                    cards_created INTEGER NOT NULL DEFAULT 0,
                    card_ids TEXT NOT NULL DEFAULT '[]',
                    words_mined TEXT NOT NULL DEFAULT '[]',
                    elapsed_time REAL NOT NULL DEFAULT 0.0
                )
                """)

    def record_session(
        self,
        video_file: Path,
        subtitle_file: Path,
        result: ProcessingResult,
        card_ids: list[int] | None = None,
        words_mined: list[str] | None = None,
    ) -> int:
        """Record a mining session to the history database.

        Args:
            video_file: Path to the video file that was processed
            subtitle_file: Path to the subtitle file that was processed
            result: Processing result with statistics
            card_ids: Optional list of Anki note IDs that were created
            words_mined: Optional list of word lemmas that were mined

        Returns:
            The row ID of the inserted record
        """
        timestamp = datetime.now(timezone.utc).isoformat()
        series_name = video_file.parent.name if video_file.parent != video_file else ""
        ids_json = json.dumps(card_ids or [])
        words_json = json.dumps(words_mined or [])

        with sqlite3.connect(str(self.db_path)) as conn:
            cursor = conn.execute(
                """
                INSERT INTO mining_history
                    (timestamp, video_file, subtitle_file, series_name,
                     cards_created, card_ids, words_mined, elapsed_time)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    timestamp,
                    str(video_file),
                    str(subtitle_file),
                    series_name,
                    result.cards_created,
                    ids_json,
                    words_json,
                    result.elapsed_time,
                ),
            )
            return cursor.lastrowid  # type: ignore[return-value]

    def get_history(self, limit: int = 50) -> list[dict]:
        """Get the most recent history entries.

        Args:
            limit: Maximum number of entries to return

        Returns:
            List of history entry dicts, newest first
        """
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM mining_history ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_dict(row) for row in rows]

    def get_session(self, session_id: int) -> dict | None:
        """Get a specific history entry by ID.

        Args:
            session_id: The row ID of the session

        Returns:
            History entry dict, or None if not found
        """
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM mining_history WHERE id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_dict(row)

    def get_card_ids_for_session(self, session_id: int) -> list[int]:
        """Get the card IDs for a specific session.

        Args:
            session_id: The row ID of the session

        Returns:
            List of Anki note IDs, or empty list if not found
        """
        with sqlite3.connect(str(self.db_path)) as conn:
            row = conn.execute(
                "SELECT card_ids FROM mining_history WHERE id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return []
        try:
            return json.loads(row[0])
        except (json.JSONDecodeError, TypeError):
            return []

    def delete_session(self, session_id: int) -> bool:
        """Delete a history entry.

        Args:
            session_id: The row ID of the session to delete

        Returns:
            True if a row was deleted, False if not found
        """
        with sqlite3.connect(str(self.db_path)) as conn:
            cursor = conn.execute(
                "DELETE FROM mining_history WHERE id = ?",
                (session_id,),
            )
            return cursor.rowcount > 0

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict:
        """Convert a database row to a dictionary with parsed JSON fields.

        Args:
            row: SQLite row object

        Returns:
            Dictionary with parsed card_ids and words_mined
        """
        d = dict(row)
        try:
            d["card_ids"] = json.loads(d.get("card_ids", "[]"))
        except (json.JSONDecodeError, TypeError):
            d["card_ids"] = []
        try:
            d["words_mined"] = json.loads(d.get("words_mined", "[]"))
        except (json.JSONDecodeError, TypeError):
            d["words_mined"] = []
        return d
