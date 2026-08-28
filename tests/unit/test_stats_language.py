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
