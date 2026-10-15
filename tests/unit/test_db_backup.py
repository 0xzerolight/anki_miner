"""WP-13: daily rotated backups of the user's own data, and a restore at the next launch."""

from __future__ import annotations

import json
import os
import sqlite3
import stat
from datetime import date
from pathlib import Path

import pytest

from anki_miner.services.db_backup import (
    MANIFEST_NAME,
    PENDING_RESTORE_NAME,
    apply_pending_restore,
    list_backups,
    pending_restore,
    run_daily_backup,
    schedule_restore,
)


def _sqlite(path: Path, value: str) -> Path:
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS t (v TEXT)")
        conn.execute("DELETE FROM t")
        conn.execute("INSERT INTO t VALUES (?)", (value,))
        conn.commit()
    finally:
        conn.close()
    return path


def _read(path: Path) -> str:
    conn = sqlite3.connect(path)
    try:
        return str(conn.execute("SELECT v FROM t").fetchone()[0])
    finally:
        conn.close()


@pytest.fixture
def data(tmp_path) -> Path:
    folder = tmp_path / "home"
    folder.mkdir()
    _sqlite(folder / "stats.db", "stats")
    (folder / "gui_config.json").write_text('{"a": 1}', encoding="utf-8")
    return folder


def test_a_backup_copies_each_source_and_writes_a_manifest(data):
    root = data / "backups"

    dest = run_daily_backup([data / "stats.db", data / "gui_config.json"], root, today=date(2026, 10, 15))

    assert dest == root / "2026-10-15"
    [day] = list_backups(root)
    assert day.day == date(2026, 10, 15)
    assert [(entry.name, entry.kind) for entry in day.entries] == [
        ("01-stats.db", "sqlite"),
        ("02-gui_config.json", "file"),
    ]
    assert day.entries[0].source == (data / "stats.db").resolve()
    assert _read(dest / "01-stats.db") == "stats"
    assert (dest / "02-gui_config.json").read_text(encoding="utf-8") == '{"a": 1}'


def test_the_second_start_of_a_day_backs_up_nothing(data):
    root = data / "backups"
    run_daily_backup([data / "stats.db"], root, today=date(2026, 10, 15))
    _sqlite(data / "stats.db", "changed")

    assert run_daily_backup([data / "stats.db"], root, today=date(2026, 10, 15)) is None
    assert _read(root / "2026-10-15" / "01-stats.db") == "stats"


def test_only_the_seven_newest_days_are_kept(data):
    root = data / "backups"
    for day in range(1, 10):
        run_daily_backup([data / "gui_config.json"], root, today=date(2026, 10, day))

    assert [backup.day.day for backup in list_backups(root)] == [9, 8, 7, 6, 5, 4, 3]
    assert not (root / "2026-10-02").exists()


def test_an_unreadable_source_is_skipped_not_fatal(data):
    root = data / "backups"

    dest = run_daily_backup([data / "missing.db", data / "gui_config.json"], root, today=date(2026, 10, 15))

    assert dest is not None
    [day] = list_backups(root)
    assert [entry.name for entry in day.entries] == ["02-gui_config.json"]


def test_a_corrupt_manifest_reads_as_no_backup(data):
    root = data / "backups"
    (root / "2026-10-14").mkdir(parents=True)
    (root / "2026-10-14" / MANIFEST_NAME).write_text("{not json", encoding="utf-8")

    assert list_backups(root) == []


def test_a_manifest_naming_a_file_outside_its_folder_is_rejected(data):
    root = data / "backups"
    run_daily_backup([data / "gui_config.json"], root, today=date(2026, 10, 15))
    manifest = root / "2026-10-15" / MANIFEST_NAME
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["files"][0]["name"] = "../../gui_config.json"
    manifest.write_text(json.dumps(payload), encoding="utf-8")

    assert list_backups(root) == []


def test_a_leftover_day_folder_without_manifest_is_replaced(data):
    root = data / "backups"
    (root / "2026-10-15").mkdir(parents=True)
    (root / "2026-10-15" / "junk").write_text("x", encoding="utf-8")

    dest = run_daily_backup([data / "gui_config.json"], root, today=date(2026, 10, 15))

    assert dest is not None
    assert not (dest / "junk").exists()
    assert (dest / MANIFEST_NAME).is_file()


def test_a_crashed_staging_folder_is_cleaned_up(data):
    root = data / "backups"
    (root / ".staging-old").mkdir(parents=True)

    run_daily_backup([data / "gui_config.json"], root, today=date(2026, 10, 15))

    assert not (root / ".staging-old").exists()


def test_an_open_database_still_backs_up(data):
    root = data / "backups"
    held = sqlite3.connect(data / "stats.db")
    try:
        held.execute("SELECT * FROM t").fetchall()
        dest = run_daily_backup([data / "stats.db"], root, today=date(2026, 10, 15))
    finally:
        held.close()

    assert dest is not None
    assert _read(dest / "01-stats.db") == "stats"


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
def test_copies_are_owner_only(data):
    dest = run_daily_backup([data / "gui_config.json"], data / "backups", today=date(2026, 10, 15))

    assert dest is not None
    assert stat.S_IMODE((dest / "01-gui_config.json").stat().st_mode) == 0o600
    assert stat.S_IMODE(dest.stat().st_mode) == 0o700


def test_a_scheduled_restore_puts_every_file_back(data):
    root = data / "backups"
    sources = [data / "stats.db", data / "gui_config.json"]
    run_daily_backup(sources, root, today=date(2026, 10, 14))
    _sqlite(data / "stats.db", "later")
    (data / "gui_config.json").write_text('{"a": 2}', encoding="utf-8")

    schedule_restore(root, date(2026, 10, 14))
    assert pending_restore(root) == date(2026, 10, 14)
    restored = apply_pending_restore(root)

    assert restored == 2
    assert _read(data / "stats.db") == "stats"
    assert (data / "gui_config.json").read_text(encoding="utf-8") == '{"a": 1}'
    assert pending_restore(root) is None
    assert not (root / PENDING_RESTORE_NAME).exists()


def test_nothing_pending_does_nothing(data):
    assert apply_pending_restore(data / "backups") is None


def test_scheduling_a_day_without_a_backup_is_refused(data):
    with pytest.raises(ValueError):
        schedule_restore(data / "backups", date(2026, 1, 1))


def test_a_pending_day_that_vanished_is_dropped(data):
    root = data / "backups"
    root.mkdir()
    (root / PENDING_RESTORE_NAME).write_text(json.dumps({"day": "2026-01-01"}), encoding="utf-8")

    assert apply_pending_restore(root) == 0
    assert not (root / PENDING_RESTORE_NAME).exists()


def test_a_corrupt_pending_file_is_cleared(data):
    root = data / "backups"
    root.mkdir()
    (root / PENDING_RESTORE_NAME).write_text("nope", encoding="utf-8")

    assert pending_restore(root) is None
    assert apply_pending_restore(root) is None
    assert not (root / PENDING_RESTORE_NAME).exists()


def test_a_stale_journal_goes_with_the_restore(data):
    root = data / "backups"
    run_daily_backup([data / "stats.db"], root, today=date(2026, 10, 14))
    journal = data / "stats.db-journal"
    journal.write_bytes(b"stale")

    schedule_restore(root, date(2026, 10, 14))
    apply_pending_restore(root)

    assert not journal.exists()
    assert _read(data / "stats.db") == "stats"


def test_a_restore_recreates_a_deleted_folder(data, tmp_path):
    root = data / "backups"
    nested = tmp_path / "elsewhere" / "known_words.db"
    nested.parent.mkdir()
    _sqlite(nested, "words")
    run_daily_backup([nested], root, today=date(2026, 10, 14))
    nested.unlink()
    nested.parent.rmdir()

    schedule_restore(root, date(2026, 10, 14))

    assert apply_pending_restore(root) == 1
    assert _read(nested) == "words"
