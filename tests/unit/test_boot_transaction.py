"""Regression coverage for W7 boot commit and shortcut dispatch."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock

import pytest

from anki_miner import __version__


def _patch_window_construction(monkeypatch) -> None:
    from anki_miner.gui import main_window as main_window_module

    monkeypatch.setattr(main_window_module.ValidationService, "__init__", lambda self, *args, **kwargs: None)
    monkeypatch.setattr(main_window_module.MainWindow, "_maybe_repair_legacy_frequency_source_name", lambda self: None)
    monkeypatch.setattr(main_window_module.QTimer, "singleShot", lambda *args, **kwargs: None)


def test_workers_not_started_before_boot_commit(qtbot, monkeypatch, test_config):
    from anki_miner.gui import main_window as main_window_module

    _patch_window_construction(monkeypatch)
    events: list[str] = []
    monkeypatch.setattr(main_window_module.GUIConfigManager, "load_config", lambda: pytest.fail("decoded twice"))
    monkeypatch.setattr(main_window_module.GUIConfigManager, "save_config", lambda config: events.append("save"))
    monkeypatch.setattr(main_window_module.MainWindow, "_run_validation", lambda self: events.append("validation"))
    monkeypatch.setattr(main_window_module.MainWindow, "_check_for_updates", lambda self: events.append("update"))
    monkeypatch.setattr(main_window_module.MainWindow, "_maybe_migrate_jmdict", lambda self: events.append("migration"))
    monkeypatch.setattr(main_window_module.MainWindow, "_maybe_start_ytdlp_update", lambda self: events.append("ytdlp"))
    config = replace(
        test_config,
        last_known_version="",
        check_for_updates=True,
        first_run_shortcut_done=True,
        first_run_setup_done=True,
    )

    window = main_window_module.MainWindow(config)
    qtbot.addWidget(window)

    assert events == []

    window.commit_boot()

    assert events == ["save", "validation", "update", "migration", "ytdlp"]
    window.deleteLater()


def test_boot_fault_after_version_save_starts_no_workers(qtbot, monkeypatch, test_config):
    from anki_miner.gui import main_window as main_window_module

    _patch_window_construction(monkeypatch)
    events: list[str] = []
    monkeypatch.setattr(main_window_module.GUIConfigManager, "save_config", lambda config: events.append("save"))
    monkeypatch.setattr(main_window_module.MainWindow, "_run_validation", lambda self: events.append("validation"))
    monkeypatch.setattr(main_window_module.MainWindow, "_check_for_updates", lambda self: events.append("update"))
    monkeypatch.setattr(main_window_module.MainWindow, "_maybe_migrate_jmdict", lambda self: events.append("migration"))
    monkeypatch.setattr(main_window_module.MainWindow, "_maybe_start_ytdlp_update", lambda self: events.append("ytdlp"))
    monkeypatch.setattr(
        main_window_module.QMessageBox,
        "information",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("dialog failed")),
    )
    config = replace(
        test_config,
        last_known_version="0.0.0",
        check_for_updates=True,
        first_run_shortcut_done=True,
        first_run_setup_done=True,
    )

    window = main_window_module.MainWindow(config)
    qtbot.addWidget(window)

    with pytest.raises(RuntimeError, match="dialog failed"):
        window.commit_boot()

    assert events == ["save"]
    window.deleteLater()


def test_shortcut_work_off_thread_and_attempt_state_persisted_on_error(qtbot, monkeypatch, test_config):
    from anki_miner.gui import main_window as main_window_module

    _patch_window_construction(monkeypatch)
    monkeypatch.setattr(main_window_module.MainWindow, "_run_validation", lambda self: None)
    monkeypatch.setattr(main_window_module.MainWindow, "_check_for_updates", lambda self: None)
    monkeypatch.setattr(main_window_module.MainWindow, "_maybe_migrate_jmdict", lambda self: None)
    monkeypatch.setattr(main_window_module.MainWindow, "_maybe_start_ytdlp_update", lambda self: None)
    monkeypatch.setattr(main_window_module.GUIConfigManager, "save_config", lambda config: None)
    monkeypatch.setattr(
        main_window_module.ShortcutService,
        "shortcut_exists",
        lambda: pytest.fail("shortcut filesystem work ran on GUI thread"),
    )
    dispatched: list[dict[str, object]] = []

    def fake_run_off_thread(parent, work, on_done, on_error=None, **kwargs):
        dispatched.append({"work": work, "on_done": on_done, "on_error": on_error})
        return MagicMock()

    monkeypatch.setattr(main_window_module, "run_off_thread", fake_run_off_thread)
    config = replace(
        test_config,
        last_known_version=__version__,
        check_for_updates=False,
        first_run_shortcut_done=False,
        first_run_setup_done=True,
    )
    window = main_window_module.MainWindow(config)
    qtbot.addWidget(window)

    window._maybe_create_shortcut_on_first_run()
    window._maybe_create_shortcut_on_first_run()

    assert len(dispatched) == 1
    assert window.config.first_run_shortcut_done is False

    on_error = dispatched[0]["on_error"]
    assert callable(on_error)
    on_error("disk full")

    assert window.config.first_run_shortcut_done is True
    assert window._shortcut_work_in_flight is False
    window.deleteLater()
