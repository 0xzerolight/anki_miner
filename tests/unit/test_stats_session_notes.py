"""AI-16: every run keeps the note ids it created and the forms it marked mined."""

from __future__ import annotations

import sqlite3
from datetime import datetime
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


def test_history_rows_count_their_notes(tmp_path):
    service = _service(tmp_path)
    service.record_session(MiningSession(series_name="Old", mined_at=datetime(2026, 1, 1)))
    service.record_session(MiningSession(series_name="New", note_ids=[5, 6, 7], mined_at=datetime(2026, 2, 1)))

    new, old = service.get_recent_sessions()

    assert (new.series_name, new.note_count, new.deleted_count) == ("New", 3, 0)
    assert (old.series_name, old.note_count, old.deleted_count) == ("Old", 0, 0)


def test_no_limit_returns_every_run(tmp_path):
    service = _service(tmp_path)
    for day in range(1, 26):
        service.record_session(
            MiningSession(series_name="S", episode_name=f"ep{day:02d}", mined_at=datetime(2026, 1, day))
        )

    assert len(service.get_recent_sessions()) == 20
    assert len(service.get_recent_sessions(limit=None)) == 25


def test_forgetting_a_run_clears_its_ids_and_forms(tmp_path):
    service = _service(tmp_path)
    row_id = service.record_session(MiningSession(series_name="S", note_ids=[1, 2], mined_forms=["猫"]))

    service.forget_run_notes(row_id)

    assert service.get_run_receipt(row_id) == RunReceipt(session_id=row_id, language="ja", note_ids=(), mined_forms=())
    [row] = service.get_recent_sessions()
    assert row.note_count == 0


def test_a_forgotten_run_stays_marked_deleted_after_a_restart(tmp_path):
    """Ruling C5: the row keeps its totals and says, persistently, how many cards went."""
    service = _service(tmp_path)
    row_id = service.record_session(MiningSession(series_name="S", cards_created=2, note_ids=[1, 2]))
    service.forget_run_notes(row_id)

    [row] = _service(tmp_path).get_recent_sessions()

    assert (row.cards_created, row.note_count, row.deleted_count) == (2, 0, 2)


def test_forgetting_a_run_without_ids_leaves_no_mark(tmp_path):
    service = _service(tmp_path)
    row_id = service.record_session(MiningSession(series_name="S"))

    service.forget_run_notes(row_id)

    [row] = service.get_recent_sessions()
    assert row.deleted_count == 0


def test_reset_drops_only_the_active_languages_lists(tmp_path):
    service = _service(tmp_path, "ja")
    service.record_session(MiningSession(series_name="JA", note_ids=[1], mined_forms=["猫"]))
    ja_gone = service.record_session(MiningSession(series_name="JA2", note_ids=[3]))
    service.forget_run_notes(ja_gone)
    service.language = "es"
    es_id = service.record_session(MiningSession(series_name="ES", note_ids=[2], mined_forms=["gato"]))
    es_gone = service.record_session(MiningSession(series_name="ES2", note_ids=[4]))
    service.forget_run_notes(es_gone)
    service.language = "ja"

    service.reset()

    conn = sqlite3.connect(tmp_path / "stats.db")
    try:
        notes = conn.execute("SELECT session_id FROM session_notes").fetchall()
        forms = conn.execute("SELECT session_id FROM session_forms").fetchall()
        marks = conn.execute("SELECT session_id, cards FROM session_deleted").fetchall()
    finally:
        conn.close()
    assert notes == [(es_id,)]
    assert forms == [(es_id,)]
    assert marks == [(es_gone, 1)]
