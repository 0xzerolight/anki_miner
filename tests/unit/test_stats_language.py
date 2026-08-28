"""stats.db is partitioned by mining language, re-read at call time."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.app import _bind_stats_language
from anki_miner.models.stats import MiningSession
from anki_miner.services.stats_service import StatsService
from anki_miner.services.word_pool import MinePassStats


class _FakeWindow(QObject):
    config_refreshed = pyqtSignal(object)


def test_writes_are_stamped_and_reads_are_filtered(tmp_path: Path):
    svc = StatsService(tmp_path / "stats.db", language="ja")
    assert svc.load()
    svc.record_session(MiningSession(series_name="JA Show", cards_created=3))
    svc.record_difficulty("JA Show", "ep01", 100, 20)

    svc.language = "zh"
    assert svc.get_overall_stats().total_sessions == 0
    assert svc.get_recent_sessions() == []
    assert svc.get_series_difficulty() == []

    svc.record_session(MiningSession(series_name="ZH Show", cards_created=7))
    assert svc.get_overall_stats().total_cards_created == 7

    svc.language = "ja"
    assert svc.get_overall_stats().total_cards_created == 3
    assert len(svc.get_series_difficulty()) == 1


def test_existing_rows_migrate_to_ja(tmp_path: Path):
    db_path = tmp_path / "legacy.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute("""CREATE TABLE mining_sessions (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   series_name TEXT NOT NULL, episode_name TEXT NOT NULL,
                   total_words INTEGER NOT NULL DEFAULT 0,
                   unknown_words INTEGER NOT NULL DEFAULT 0,
                   cards_created INTEGER NOT NULL DEFAULT 0,
                   elapsed_time REAL NOT NULL DEFAULT 0.0,
                   mined_at TEXT NOT NULL DEFAULT (datetime('now')))""")
        conn.execute("INSERT INTO mining_sessions (series_name, episode_name, cards_created) VALUES ('Old', 'ep01', 4)")

    svc = StatsService(db_path)
    assert svc.load()
    assert svc.get_overall_stats().total_cards_created == 4
    with sqlite3.connect(db_path) as conn:
        assert int(conn.execute("PRAGMA user_version").fetchone()[0]) == 1
        assert conn.execute("SELECT language FROM mining_sessions").fetchone()[0] == "ja"


class _AlterPausingConnection:
    """A connection proxy that lets a *rival* migration commit first.

    ``_migrate_language_column`` materializes its column probe into a Python
    set, so a rival connection that ALTERs and commits in between leaves this
    one about to add a column that already exists — the production race between
    a StatsService and the MinePassStats instance wrapping it (or a second app
    instance). Firing the rival from inside the proxy replays that interleave
    deterministically, with no threads.
    """

    def __init__(self, conn: sqlite3.Connection, before_first_alter):
        self._conn = conn
        self._before_first_alter = before_first_alter

    def execute(self, sql: str, *args):
        if sql.startswith("ALTER TABLE") and self._before_first_alter is not None:
            run, self._before_first_alter = self._before_first_alter, None
            run()
        return self._conn.execute(sql, *args)


def _write_legacy_stats_db(db_path: Path) -> None:
    """Both tables at user_version 0, neither carrying ``language``."""
    with sqlite3.connect(db_path) as conn:
        conn.execute("""CREATE TABLE mining_sessions (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   series_name TEXT NOT NULL, episode_name TEXT NOT NULL,
                   cards_created INTEGER NOT NULL DEFAULT 0,
                   mined_at TEXT NOT NULL DEFAULT (datetime('now')))""")
        conn.execute("""CREATE TABLE series_difficulty (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   series_name TEXT NOT NULL, episode_name TEXT NOT NULL,
                   difficulty_score REAL NOT NULL DEFAULT 0.0,
                   recorded_at TEXT NOT NULL DEFAULT (datetime('now')))""")


def test_a_migration_that_loses_the_alter_race_still_succeeds(tmp_path: Path):
    db_path = tmp_path / "legacy.db"
    _write_legacy_stats_db(db_path)
    winner = sqlite3.connect(db_path)
    loser = sqlite3.connect(db_path)
    loser.execute("PRAGMA busy_timeout = 5000")

    def rival_migration() -> None:
        StatsService._migrate_language_column(winner)
        winner.commit()

    try:
        StatsService._migrate_language_column(_AlterPausingConnection(loser, rival_migration))
        loser.commit()
    finally:
        winner.close()
        loser.close()

    assert StatsService(db_path).load()
    with sqlite3.connect(db_path) as conn:
        assert int(conn.execute("PRAGMA user_version").fetchone()[0]) == 1
        for table in ("mining_sessions", "series_difficulty"):
            columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
            assert columns.count("language") == 1, table


def test_mine_pass_stats_forwards_the_language(tmp_path: Path):
    inner = StatsService(tmp_path / "stats.db", language="zh")
    assert inner.load()
    MinePassStats(inner).record_session(MiningSession(series_name="ZH", cards_created=1))
    assert inner.get_overall_stats().total_sessions == 1
    inner.language = "ja"
    assert inner.get_overall_stats().total_sessions == 0


def test_config_refresh_repartitions_subsequent_calls(tmp_path: Path):
    window = _FakeWindow()
    svc = StatsService(tmp_path / "stats.db")
    _bind_stats_language(window, svc)

    window.config_refreshed.emit(AnkiMinerConfig(language="zh"))
    assert svc.language == "zh"
    window.config_refreshed.emit(AnkiMinerConfig(language="ja"))
    assert svc.language == "ja"
