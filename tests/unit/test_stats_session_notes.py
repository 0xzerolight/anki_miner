"""AI-16: every run keeps the note ids it created and the forms it marked mined."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from anki_miner.models.stats import MiningSession, RunReceipt
from anki_miner.services.stats_service import StatsService


def _service(tmp_path: Path, language: str = "ja") -> StatsService:
    service = StatsService(tmp_path / "stats.db", language=language)
    assert service.load()
    return service


def test_a_run_keeps_its_note_ids_and_mined_forms(tmp_path):
    service = _service(tmp_path)

    row_id = service.record_session(
        MiningSession(
            series_name="Show",
            episode_name="ep01",
            cards_created=2,
            note_ids=[102, 101],
            mined_forms=["猫", "犬"],
        )
    )

    assert service.get_run_receipt(row_id) == RunReceipt(
        session_id=row_id, language="ja", note_ids=(101, 102), mined_forms=("犬", "猫")
    )


def test_a_run_without_ids_has_an_empty_receipt(tmp_path):
    service = _service(tmp_path)

    row_id = service.record_session(MiningSession(series_name="Show", cards_created=0))

    assert service.get_run_receipt(row_id) == RunReceipt(session_id=row_id, language="ja", note_ids=(), mined_forms=())


def test_an_unknown_session_has_no_receipt(tmp_path):
    assert _service(tmp_path).get_run_receipt(999) is None


def test_the_receipt_carries_the_runs_own_language(tmp_path):
    """Review Focus 5: read after a language switch, a receipt still names its run's language."""
    service = _service(tmp_path, "ja")
    ja_id = service.record_session(MiningSession(series_name="JA", note_ids=[1], mined_forms=["猫"]))
    service.language = "es"
    es_id = service.record_session(MiningSession(series_name="ES", note_ids=[2], mined_forms=["gato"]))

    ja_receipt = service.get_run_receipt(ja_id)
    es_receipt = service.get_run_receipt(es_id)

    assert ja_receipt is not None and ja_receipt.language == "ja"
    assert es_receipt is not None and es_receipt.language == "es"


def test_a_database_from_before_the_update_gains_the_tables(tmp_path):
    db = tmp_path / "stats.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE mining_sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, series_name TEXT NOT NULL, "
        "episode_name TEXT NOT NULL, total_words INTEGER NOT NULL DEFAULT 0, unknown_words INTEGER NOT NULL "
        "DEFAULT 0, cards_created INTEGER NOT NULL DEFAULT 0, elapsed_time REAL NOT NULL DEFAULT 0.0, "
        "mined_at TEXT NOT NULL DEFAULT (datetime('now')), language TEXT NOT NULL DEFAULT 'ja')"
    )
    conn.execute(
        "INSERT INTO mining_sessions (series_name, episode_name, cards_created, mined_at) "
        "VALUES ('Old', 'ep01', 4, '2026-01-02T03:04:05')"
    )
    conn.execute("PRAGMA user_version = 1")
    conn.commit()
    conn.close()
    service = StatsService(db)
    assert service.load()

    [old] = service.get_recent_sessions()

    assert old.id is not None
    assert service.get_run_receipt(old.id) == RunReceipt(session_id=old.id, language="ja", note_ids=(), mined_forms=())
