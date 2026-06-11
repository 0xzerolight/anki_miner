"""SQLite-backed processing history service."""

import json
import logging
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from anki_miner.config.paths import ANKI_MINER_HOME
from anki_miner.models.processing import ProcessingResult

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = ANKI_MINER_HOME / "history.db"


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
            conn.execute("CREATE INDEX IF NOT EXISTS idx_history_timestamp " "ON mining_history(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_history_series " "ON mining_history(series_name)")

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
        timestamp = datetime.now(UTC).isoformat()
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
