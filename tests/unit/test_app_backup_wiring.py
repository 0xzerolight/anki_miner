"""WP-13 at startup: the daily backup rides the lock-gated repair pass, a restore runs before the config load."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import cast

from PyQt6.QtCore import QLockFile

from anki_miner.cli.entry import RUN_LOCK_NAME
from anki_miner.gui import app
from anki_miner.gui.utils.config_manager import GUIConfigManager
from anki_miner.services.db_backup import (
    backups_root,
    list_backups,
    pending_restore,
    run_daily_backup,
    schedule_restore,
)

_HELD = cast(QLockFile, object())  # any non-None lock means "this window owns instance.lock"


def _config_file() -> Path:
    config_file = GUIConfigManager.CONFIG_FILE
    config_file.parent.mkdir(parents=True, exist_ok=True)
    return config_file


def test_the_startup_pass_takes_the_daily_backup(test_config, monkeypatch):
    monkeypatch.setattr(app, "run_startup_store_recovery", lambda config, **kw: None)
    _config_file().write_text("{}", encoding="utf-8")

    app._run_store_recovery_if_locked(test_config, _HELD, allow_collection=True)

    [day] = list_backups(backups_root(GUIConfigManager.CONFIG_FILE))
    assert day.day == date.today()
    assert GUIConfigManager.CONFIG_FILE.resolve() in {entry.source for entry in day.entries}


def test_no_instance_lock_means_no_backup(test_config, monkeypatch):
    calls: list = []
    monkeypatch.setattr(app, "run_daily_backup", lambda *a, **k: calls.append(a))

    app._run_store_recovery_if_locked(test_config, None, allow_collection=True)

    assert calls == []


def test_a_failed_repair_still_backs_up(test_config, monkeypatch):
    def _broken(config, **kw):
        raise RuntimeError("repair broke")

    calls: list = []
    monkeypatch.setattr(app, "run_startup_store_recovery", _broken)
    monkeypatch.setattr(app, "run_daily_backup", lambda *a, **k: calls.append(a))

    app._run_store_recovery_if_locked(test_config, _HELD, allow_collection=True)

    assert len(calls) == 1


def test_a_failed_backup_does_not_stop_startup(test_config, monkeypatch, caplog):
    def _full(*_a, **_k):
        raise OSError("No space left on device")

    monkeypatch.setattr(app, "run_startup_store_recovery", lambda config, **kw: None)
    monkeypatch.setattr(app, "run_daily_backup", _full)

    with caplog.at_level(logging.ERROR, logger="anki_miner.gui.app"):
        app._run_store_recovery_if_locked(test_config, _HELD, allow_collection=True)

    assert any(record.getMessage().startswith("Daily backup failed") for record in caplog.records)


def _scheduled() -> Path:
    """Back up the config, change it, schedule the restore; return the config path."""
    config_file = _config_file()
    config_file.write_text('{"v": 1}', encoding="utf-8")
    root = backups_root(config_file)
    run_daily_backup([config_file], root, today=date(2026, 10, 14))
    config_file.write_text('{"v": 2}', encoding="utf-8")
    schedule_restore(root, date(2026, 10, 14))
    return config_file


def test_a_scheduled_restore_runs_at_launch():
    config_file = _scheduled()

    app._apply_pending_backup_restore(app.ANKI_MINER_HOME)

    assert config_file.read_text(encoding="utf-8") == '{"v": 1}'
    assert pending_restore(backups_root(config_file)) is None


def test_a_restore_waits_while_another_window_holds_the_lock():
    config_file = _scheduled()
    other = QLockFile(str(app.ANKI_MINER_HOME / "instance.lock"))
    assert other.tryLock(0)
    try:
        app._apply_pending_backup_restore(app.ANKI_MINER_HOME)
    finally:
        other.unlock()

    assert config_file.read_text(encoding="utf-8") == '{"v": 2}'
    assert pending_restore(backups_root(config_file)) == date(2026, 10, 14)


def test_a_restore_waits_while_a_command_line_run_works():
    config_file = _scheduled()
    run = QLockFile(str(app.ANKI_MINER_HOME / RUN_LOCK_NAME))
    assert run.tryLock(0)
    try:
        app._apply_pending_backup_restore(app.ANKI_MINER_HOME)
    finally:
        run.unlock()

    assert config_file.read_text(encoding="utf-8") == '{"v": 2}'
    assert pending_restore(backups_root(config_file)) == date(2026, 10, 14)


def test_nothing_scheduled_touches_no_lock():
    _config_file()
    held = QLockFile(str(app.ANKI_MINER_HOME / "instance.lock"))
    assert held.tryLock(0)
    try:
        app._apply_pending_backup_restore(app.ANKI_MINER_HOME)  # returns before any lock probe
        assert held.isLocked()
    finally:
        held.unlock()


def test_the_startup_pass_prunes_the_transcript_cache(test_config, monkeypatch):
    """R16: the saved-transcript cache is trimmed under the same locks as the repair."""
    from anki_miner.services.asr import transcript_cache

    calls: list = []
    monkeypatch.setattr(app, "run_startup_store_recovery", lambda config, **kw: None)
    monkeypatch.setattr(app, "run_daily_backup", lambda *a, **k: None)
    monkeypatch.setattr(transcript_cache, "prune_transcript_cache", lambda *a, **k: calls.append(a) or 0)

    app._run_store_recovery_if_locked(test_config, _HELD, allow_collection=True)

    assert len(calls) == 1


def test_no_instance_lock_means_no_transcript_prune(test_config, monkeypatch):
    from anki_miner.services.asr import transcript_cache

    calls: list = []
    monkeypatch.setattr(transcript_cache, "prune_transcript_cache", lambda *a, **k: calls.append(a) or 0)

    app._run_store_recovery_if_locked(test_config, None, allow_collection=True)

    assert calls == []


def test_a_failed_prune_does_not_stop_startup(test_config, monkeypatch, caplog):
    from anki_miner.services.asr import transcript_cache

    def _broken(*_a, **_k):
        raise RuntimeError("prune broke")

    monkeypatch.setattr(app, "run_startup_store_recovery", lambda config, **kw: None)
    monkeypatch.setattr(app, "run_daily_backup", lambda *a, **k: None)
    monkeypatch.setattr(transcript_cache, "prune_transcript_cache", _broken)

    with caplog.at_level(logging.ERROR, logger="anki_miner.gui.app"):
        app._run_store_recovery_if_locked(test_config, _HELD, allow_collection=True)

    assert any(record.getMessage().startswith("Transcript cache prune failed") for record in caplog.records)
