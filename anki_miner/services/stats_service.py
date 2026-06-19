"""Service for recording and querying mining statistics."""

import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from anki_miner.models.stats import (
    DifficultyEntry,
    Milestone,
    MiningSession,
    OverallStats,
)

logger = logging.getLogger(__name__)

CARD_MILESTONES = [
    (50, "First Steps", "Created 50 cards"),
    (100, "Getting Started", "Created 100 cards"),
    (250, "Building Momentum", "Created 250 cards"),
    (500, "Dedicated Learner", "Created 500 cards"),
    (1000, "Vocabulary Builder", "Created 1,000 cards"),
    (2500, "Word Collector", "Created 2,500 cards"),
    (5000, "Language Explorer", "Created 5,000 cards"),
    (10000, "Master Miner", "Created 10,000 cards"),
]

SESSION_MILESTONES = [
    (5, "Regular Miner", "Completed 5 mining sessions"),
    (10, "Consistent Learner", "Completed 10 mining sessions"),
    (25, "Mining Veteran", "Completed 25 mining sessions"),
    (50, "Mining Expert", "Completed 50 mining sessions"),
    (100, "Mining Master", "Completed 100 mining sessions"),
]

SERIES_MILESTONES = [
    (3, "Series Explorer", "Mined from 3 different series"),
    (5, "Series Fan", "Mined from 5 different series"),
    (10, "Series Enthusiast", "Mined from 10 different series"),
    (25, "Series Connoisseur", "Mined from 25 different series"),
]


class StatsService:
    """Record and query mining statistics using SQLite.

    This service manages a SQLite database that stores:
    - Mining session records (Feature 1)
    - Series difficulty rankings (Feature 2)
    - Progress milestones (Feature 3)

    Thread Safety:
        Each method creates its own sqlite3.Connection, which is safe
        for use from both the main GUI thread and worker threads.
    """

    def __init__(self, db_path: Path):
        self._db_path = db_path
        self._initialized = False

    def load(self) -> bool:
        """Initialize the database, creating tables if needed."""
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as conn:
                self._create_tables(conn)
            self._initialized = True
            logger.info(f"Stats database initialized at {self._db_path}")
            return True
        except Exception:
            logger.exception("Failed to initialize stats database")
            return False

    def is_available(self) -> bool:
        """Check if the stats service has been initialized."""
        return self._initialized

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        # Wait up to 5 s before raising OperationalError on a busy DB so brief
        # reader/writer contention (Anki reading stats.db, parallel run) retries
        # instead of failing immediately. Keep the existing journal mode as-is.
        conn.execute("PRAGMA busy_timeout = 5000")
        try:
            with conn:  # commit on success, rollback on exception
                yield conn
        finally:
            conn.close()

    def _create_tables(self, conn: sqlite3.Connection) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS mining_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                series_name TEXT NOT NULL,
                episode_name TEXT NOT NULL,
                total_words INTEGER NOT NULL DEFAULT 0,
                unknown_words INTEGER NOT NULL DEFAULT 0,
                cards_created INTEGER NOT NULL DEFAULT 0,
                elapsed_time REAL NOT NULL DEFAULT 0.0,
                mined_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS series_difficulty (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                series_name TEXT NOT NULL,
                episode_name TEXT NOT NULL,
                total_words INTEGER NOT NULL DEFAULT 0,
                unknown_words INTEGER NOT NULL DEFAULT 0,
                unique_words INTEGER NOT NULL DEFAULT 0,
                difficulty_score REAL NOT NULL DEFAULT 0.0,
                recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_sessions_series
            ON mining_sessions(series_name)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_difficulty_series
            ON series_difficulty(series_name)
        """)

    # === Feature 1: Mining Session Recording ===

    def record_session(self, session: MiningSession) -> int:
        """Record a mining session. Returns the row ID, or -1 on failure."""
        if not self._initialized:
            return -1
        with self._connect() as conn:
            cursor = conn.execute(
                """INSERT INTO mining_sessions
                   (series_name, episode_name, total_words, unknown_words,
                    cards_created, elapsed_time, mined_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    session.series_name,
                    session.episode_name,
                    session.total_words,
                    session.unknown_words,
                    session.cards_created,
                    session.elapsed_time,
                    session.mined_at.isoformat(),
                ),
            )
            return cursor.lastrowid or -1

    def get_overall_stats(self) -> OverallStats:
        """Get aggregated statistics across all sessions."""
        if not self._initialized:
            return OverallStats()
        with self._connect() as conn:
            row = conn.execute("""
                SELECT
                    COUNT(*) as total_sessions,
                    COALESCE(SUM(cards_created), 0) as total_cards,
                    COALESCE(SUM(total_words), 0) as total_words,
                    COALESCE(SUM(unknown_words), 0) as total_unknown,
                    COALESCE(SUM(elapsed_time), 0.0) as total_time,
                    COUNT(DISTINCT series_name) as series_count
                FROM mining_sessions
            """).fetchone()
            return OverallStats(
                total_sessions=row["total_sessions"],
                total_cards_created=row["total_cards"],
                total_words_encountered=row["total_words"],
                total_unknown_words=row["total_unknown"],
                total_time_spent=row["total_time"],
                series_count=row["series_count"],
            )

    def get_recent_sessions(self, limit: int = 20) -> list[MiningSession]:
        """Get the most recent mining sessions, most recent first."""
        if not self._initialized:
            return []
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM mining_sessions
                   ORDER BY mined_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
            return [self._row_to_session(row) for row in rows]

    # === Feature 2: Difficulty Ranking ===

    def record_difficulty(
        self,
        series_name: str,
        episode_name: str,
        total_words: int,
        unknown_words: int,
        unique_words: int,
    ) -> None:
        """Record difficulty data for an episode.

        The difficulty_score is calculated as unknown_words / total_words.
        Skips recording if total_words is 0.
        """
        if not self._initialized or total_words == 0:
            return
        difficulty_score = unknown_words / total_words
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO series_difficulty
                   (series_name, episode_name, total_words, unknown_words,
                    unique_words, difficulty_score, recorded_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    series_name,
                    episode_name,
                    total_words,
                    unknown_words,
                    unique_words,
                    difficulty_score,
                    datetime.now().isoformat(),
                ),
            )

    def get_series_difficulty(self) -> list[DifficultyEntry]:
        """Get average difficulty ranking per series, sorted easiest first."""
        if not self._initialized:
            return []
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT
                    series_name,
                    CAST(AVG(total_words) AS INTEGER) as total_words,
                    CAST(AVG(unknown_words) AS INTEGER) as unknown_words,
                    CAST(AVG(unique_words) AS INTEGER) as unique_words,
                    AVG(difficulty_score) as difficulty_score,
                    MAX(recorded_at) as recorded_at
                FROM series_difficulty
                GROUP BY series_name
                ORDER BY difficulty_score ASC
            """).fetchall()
            return [
                DifficultyEntry(
                    series_name=row["series_name"],
                    total_words=row["total_words"] or 0,
                    unknown_words=row["unknown_words"] or 0,
                    unique_words=row["unique_words"] or 0,
                    difficulty_score=row["difficulty_score"] or 0.0,
                    recorded_at=datetime.fromisoformat(row["recorded_at"]),
                )
                for row in rows
            ]

    # === Feature 3: Progress Milestones ===

    def get_milestones(self, stats: OverallStats | None = None) -> list[Milestone]:
        """Get the next unachieved milestone for each category.

        Returns at most 3 milestones (one per category: cards, sessions, series).
        For each category, returns the first unachieved milestone. If all milestones
        in a category are achieved, the last (highest) milestone is returned.

        Args:
            stats: Pre-fetched overall stats to avoid a duplicate query.
                   If None, stats will be fetched automatically.
        """
        if not self._initialized:
            return []

        if stats is None:
            stats = self.get_overall_stats()
        milestones: list[Milestone] = []

        for milestone_list, current_value in [
            (CARD_MILESTONES, stats.total_cards_created),
            (SESSION_MILESTONES, stats.total_sessions),
            (SERIES_MILESTONES, stats.series_count),
        ]:
            selected = None
            for threshold, name, description in milestone_list:
                selected = Milestone(
                    name=name,
                    description=description,
                    threshold=threshold,
                    current_value=current_value,
                    achieved=current_value >= threshold,
                )
                if not selected.achieved:
                    break
            if selected:
                milestones.append(selected)

        return milestones

    @staticmethod
    def _row_to_session(row: sqlite3.Row) -> MiningSession:
        return MiningSession(
            id=row["id"],
            series_name=row["series_name"],
            episode_name=row["episode_name"],
            total_words=row["total_words"],
            unknown_words=row["unknown_words"],
            cards_created=row["cards_created"],
            elapsed_time=row["elapsed_time"],
            mined_at=datetime.fromisoformat(row["mined_at"]),
        )
