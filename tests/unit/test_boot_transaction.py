"""Regression coverage for W7 boot commit and shortcut dispatch."""

from __future__ import annotations

import threading
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


def test_precommit_fault_joins_constructor_started_workers(qtbot):
    from PyQt6.QtWidgets import QWidget

    from anki_miner.gui import app as app_module
    from anki_miner.gui.utils.run_off_thread import run_off_thread, still_running

    started = threading.Event()
    cancelled = threading.Event()
    tabs = []

    class ConstructorWorkerTab(QWidget):
        def __init__(self) -> None:
            super().__init__()

            def scan(cancel_check):
                started.set()
                while not cancel_check():
                    cancelled.wait(0.01)
                cancelled.set()

            self.worker = run_off_thread(
                self,
                scan,
                lambda _result: None,
                pass_cancel_check=True,
            )

    rollback_on_fault = getattr(app_module, "_rollback_workers_on_startup_fault", lambda fn: fn)

    @rollback_on_fault
    def construct_tabs_then_fault() -> None:
        tab = ConstructorWorkerTab()
        qtbot.addWidget(tab)
        tabs.append(tab)
        assert started.wait(1)
        raise RuntimeError("fault before commit_boot")

    try:
        with pytest.raises(RuntimeError, match="fault before commit_boot"):
            construct_tabs_then_fault()

        assert getattr(app_module.main, "__wrapped__", None) is not None
        assert cancelled.wait(1)
        assert not still_running(tabs[0].worker)
    finally:
        if tabs and still_running(tabs[0].worker):
            tabs[0].worker.cancel()
            tabs[0].worker.wait(1000)


def test_frozen_windows_auto_shortcut_is_persisted_noop(qtbot, monkeypatch, test_config):
    from anki_miner.gui import main_window as main_window_module

    _patch_window_construction(monkeypatch)
    saved_configs = []
    monkeypatch.setattr(
        main_window_module.GUIConfigManager,
        "save_config",
        saved_configs.append,
    )
    config = replace(
        test_config,
        last_known_version=__version__,
        check_for_updates=False,
        first_run_shortcut_done=False,
        first_run_setup_done=True,
    )
    window = main_window_module.MainWindow(config)
    qtbot.addWidget(window)

    run_off_thread = MagicMock()
    shortcut_exists = MagicMock()
    create_shortcut = MagicMock()
    monkeypatch.setattr(main_window_module, "run_off_thread", run_off_thread)
    monkeypatch.setattr(main_window_module.ShortcutService, "shortcut_exists", shortcut_exists)
    monkeypatch.setattr(main_window_module.ShortcutService, "create_shortcut", create_shortcut)
    monkeypatch.setattr(main_window_module, "frozen_state", lambda: (True, r"C:\meipass"))
    monkeypatch.setattr(main_window_module.sys, "platform", "win32")

    window._maybe_create_shortcut_on_first_run()

    assert window.config.first_run_shortcut_done is True
    assert saved_configs[-1].first_run_shortcut_done is True
    assert window._shortcut_work_in_flight is False
    run_off_thread.assert_not_called()
    shortcut_exists.assert_not_called()
    create_shortcut.assert_not_called()
    window.deleteLater()


def test_shortcut_work_off_thread_and_attempt_state_persisted_on_error(qtbot, monkeypatch, test_config):
    from anki_miner.gui import main_window as main_window_module
    from anki_miner.services import ShortcutResult

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
    create_calls: list[tuple[bool, bool]] = []

    def create_shortcut(
        *,
        skip_if_exists: bool = False,
        include_start_menu: bool = True,
    ) -> ShortcutResult:
        create_calls.append((skip_if_exists, include_start_menu))
        return ShortcutResult(success=True)

    monkeypatch.setattr(main_window_module.ShortcutService, "create_shortcut", create_shortcut)
    dispatched: list[dict[str, object]] = []

    def fake_run_off_thread(parent, work, on_done, on_error=None, **kwargs):
        dispatched.append(
            {
                "work": work,
                "on_done": on_done,
                "on_error": on_error,
                "on_finished": kwargs.get("on_finished"),
            }
        )
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
    monkeypatch.setattr(main_window_module, "frozen_state", lambda: (False, None))
    monkeypatch.setattr(main_window_module.sys, "platform", "win32")

    window._maybe_create_shortcut_on_first_run()
    window._maybe_create_shortcut_on_first_run()

    assert len(dispatched) == 1
    assert window.config.first_run_shortcut_done is False
    work = dispatched[0]["work"]
    assert callable(work)
    work()
    assert create_calls == [(True, True)]

    on_error = dispatched[0]["on_error"]
    assert callable(on_error)
    on_error("disk full")

    assert window.config.first_run_shortcut_done is True
    assert window._shortcut_work_in_flight is False

    window._create_desktop_shortcut()
    assert len(dispatched) == 2
    manual_work = dispatched[1]["work"]
    assert callable(manual_work)
    manual_work()
    assert create_calls == [(True, True), (False, False)]
    on_finished = dispatched[1]["on_finished"]
    assert callable(on_finished)
    on_finished()
    assert window._shortcut_work_in_flight is False
    window.deleteLater()


def test_shortcut_cancel_finalizes_attempt_state(qtbot, monkeypatch, test_config):
    from anki_miner.gui import main_window as main_window_module
    from anki_miner.gui.utils.run_off_thread import still_running
    from anki_miner.services import ShortcutResult

    _patch_window_construction(monkeypatch)
    monkeypatch.setattr(main_window_module.GUIConfigManager, "save_config", lambda config: None)
    monkeypatch.setattr(main_window_module.ShortcutService, "shortcut_exists", lambda: False)
    started = threading.Event()
    release = threading.Event()
    calls = 0

    def create_shortcut() -> ShortcutResult:
        nonlocal calls
        calls += 1
        started.set()
        release.wait(1)
        return ShortcutResult(success=True, messages=())

    monkeypatch.setattr(main_window_module.ShortcutService, "create_shortcut", create_shortcut)
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
    assert started.wait(1)
    worker = next(iter(window._off_thread_workers))
    worker.cancel()
    release.set()
    assert worker.wait(1000)
    qtbot.waitUntil(lambda: not still_running(worker), timeout=1000)
    qtbot.wait(10)

    assert calls == 1
    assert window.config.first_run_shortcut_done is True
    assert window._shortcut_work_in_flight is False
    window.deleteLater()
