"""GUI wiring for diagnostic export and deferred diagnostic snapshots."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import pytest

from anki_miner.diagnostics.bundle import BundleResult
from anki_miner.diagnostics.environment import EnvironmentSnapshot
from anki_miner.gui.widgets.dialogs.system_health_window import (
    HEALTH_KEYS,
    HEALTH_WARN,
    HealthReport,
    SystemHealthWindow,
)
from anki_miner.models import ValidationResult

CHECKED_AT = datetime(2026, 8, 4, 12, 30, 45)


@pytest.fixture
def main_window(qtbot, patch_heavy_init, test_config):
    """Build a real window with startup side effects disabled."""
    patch_heavy_init(test_config)
    from anki_miner.gui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    yield window
    window.deleteLater()


def _help_action(window, text: str):
    menu_bar = window.menuBar()
    assert menu_bar is not None
    for menu_action in menu_bar.actions():
        if menu_action.text().replace("&", "") != "Help":
            continue
        menu = menu_action.menu()
        assert menu is not None
        for action in menu.actions():
            if action.text() == text:
                return action
    raise AssertionError(f"Help action not found: {text}")


def _snapshot(tmp_path: Path) -> EnvironmentSnapshot:
    return EnvironmentSnapshot(
        app_version="2.9.0",
        python="3.11.9",
        qt="Qt 6.8.0 / PyQt 6.8.0",
        platform="TestOS-1",
        frozen=False,
        meipass=None,
        executable=str(tmp_path / "python"),
        home=str(tmp_path),
        log_path=str(tmp_path / "anki_miner.log"),
        log_ring="2097152 bytes x 5 backups",
        ffmpeg="ffmpeg",
        ffprobe="ffprobe",
        ytdlp="yt-dlp",
        alass="alass",
        dictionary_chain=(),
        frequency_chain=(),
        pitch_chain=(),
        audio_chain=(),
        ankiconnect_url="http://127.0.0.1:8765",
        deck="Test Deck",
        note_type="Test Note",
    )


def _healthy_report() -> HealthReport:
    result = ValidationResult(
        ankiconnect_ok=True,
        ffmpeg_ok=True,
        deck_exists=True,
        note_type_exists=True,
        field_mapping_ok=True,
        issues=[],
        tool_versions={},
    )
    return HealthReport.unknown().with_validation(result, CHECKED_AT)


def _install_immediate_runner(monkeypatch, main_window_module) -> None:
    def run_now(parent, work, on_done, on_error=None, *, on_finished=None, **_kwargs):
        try:
            result = work()
        except Exception as exc:  # noqa: BLE001 - emulate the terminal worker seam
            if on_error is not None:
                on_error(str(exc))
        else:
            on_done(result)
        finally:
            if on_finished is not None:
                on_finished()
        return object()

    monkeypatch.setattr(main_window_module, "run_off_thread", run_now)


def test_help_action_opens_zip_picker(main_window, monkeypatch):
    from anki_miner.gui import main_window as main_window_module

    picked: dict[str, object] = {}

    def pick_save_file(parent, caption, directory, file_filter, *, on_done):
        picked.update(
            parent=parent,
            caption=caption,
            directory=directory,
            file_filter=file_filter,
            on_done=on_done,
        )
        return object()

    monkeypatch.setattr(main_window_module.file_dialogs, "pick_save_file", pick_save_file)

    _help_action(main_window, "Export Diagnostics…").trigger()

    assert picked["parent"] is main_window
    assert Path(str(picked["directory"])).suffix == ".zip"
    assert picked["file_filter"] == "Zip Archives (*.zip);;All Files (*)"


def test_cancelled_picker_writes_nothing(main_window, monkeypatch):
    from anki_miner.gui import main_window as main_window_module

    writes: list[Path] = []
    monkeypatch.setattr(
        main_window_module.file_dialogs,
        "pick_save_file",
        lambda *_args, on_done, **_kwargs: on_done(""),
    )
    monkeypatch.setattr(
        main_window_module,
        "write_diagnostics_bundle",
        lambda target, **_kwargs: writes.append(target),
        raising=False,
    )

    _help_action(main_window, "Export Diagnostics…").trigger()

    assert writes == []


def test_success_sets_status_without_showing_a_banner(main_window, monkeypatch, tmp_path):
    from anki_miner.gui import main_window as main_window_module

    target = tmp_path / "report.zip"
    monkeypatch.setattr(
        main_window_module.file_dialogs,
        "pick_save_file",
        lambda *_args, on_done, **_kwargs: on_done(str(target)),
    )
    monkeypatch.setattr(
        main_window_module, "collect_environment", lambda _config, **_kwargs: _snapshot(tmp_path), raising=False
    )
    monkeypatch.setattr(
        main_window_module,
        "write_diagnostics_bundle",
        lambda path, **_kwargs: BundleResult(path=path, members=(), total_bytes=0, missing=()),
        raising=False,
    )
    _install_immediate_runner(monkeypatch, main_window_module)

    _help_action(main_window, "Export Diagnostics…").trigger()

    assert "report.zip" in main_window.status_bar.operation_label.text()
    assert main_window.issue_banner().current_issue() is None


def test_failure_shows_retry_issue_without_a_modal(main_window, monkeypatch, tmp_path):
    from anki_miner.gui import main_window as main_window_module

    pick_calls: list[Path] = []
    modal_calls: list[str] = []
    target = tmp_path / "report.zip"

    def pick_save_file(*_args, on_done, **_kwargs):
        pick_calls.append(target)
        on_done(str(target))
        return object()

    def fail_write(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(main_window_module.file_dialogs, "pick_save_file", pick_save_file)
    monkeypatch.setattr(
        main_window_module, "collect_environment", lambda _config, **_kwargs: _snapshot(tmp_path), raising=False
    )
    monkeypatch.setattr(main_window_module, "write_diagnostics_bundle", fail_write, raising=False)
    _install_immediate_runner(monkeypatch, main_window_module)
    for method in ("information", "warning", "critical", "question", "about"):
        monkeypatch.setattr(
            main_window_module.QMessageBox,
            method,
            lambda *_args, method=method, **_kwargs: modal_calls.append(method),
        )

    _help_action(main_window, "Export Diagnostics…").trigger()

    banner = main_window.issue_banner()
    issue = banner.current_issue()
    assert issue is not None
    assert issue.action_id == "diagnostics.export-retry"
    assert "disk full" in issue.details
    assert modal_calls == []

    banner.action_button.click()
    assert len(pick_calls) > 1


@pytest.mark.parametrize("failure", [False, True], ids=["success", "failure"])
def test_both_entry_points_are_disabled_during_flight_and_restored(
    main_window,
    qtbot,
    monkeypatch,
    tmp_path,
    failure,
):
    from anki_miner.gui import main_window as main_window_module

    target = tmp_path / "report.zip"
    pending: dict[str, object] = {}

    def hold_runner(parent, work, on_done, on_error=None, *, on_finished=None, **_kwargs):
        pending.update(work=work, on_done=on_done, on_error=on_error, on_finished=on_finished)
        return object()

    monkeypatch.setattr(
        main_window_module.file_dialogs,
        "pick_save_file",
        lambda *_args, on_done, **_kwargs: on_done(str(target)),
    )
    monkeypatch.setattr(main_window_module, "run_off_thread", hold_runner)
    monkeypatch.setattr(
        main_window_module,
        "write_diagnostics_bundle",
        lambda path, **_kwargs: BundleResult(path=path, members=(), total_bytes=0, missing=()),
        raising=False,
    )

    main_window.open_system_health()
    health_window = main_window._system_health_window
    assert health_window is not None
    qtbot.addWidget(health_window)
    menu_action = _help_action(main_window, "Export Diagnostics…")

    menu_action.trigger()

    assert not menu_action.isEnabled()
    assert not health_window.export_button.isEnabled()

    if failure:
        on_error = pending["on_error"]
        assert callable(on_error)
        on_error("disk full")
    else:
        on_done = pending["on_done"]
        assert callable(on_done)
        on_done(BundleResult(path=target, members=(), total_bytes=0, missing=()))
    on_finished = pending["on_finished"]
    assert callable(on_finished)
    on_finished()

    assert menu_action.isEnabled()
    assert health_window.export_button.isEnabled()


def test_system_health_export_button_reemits_request(qtbot):
    window = SystemHealthWindow()
    qtbot.addWidget(window)

    with qtbot.waitSignal(window.export_requested):
        window.export_button.click()


def test_environment_snapshot_is_dispatched_before_collection(main_window, monkeypatch, tmp_path):
    from anki_miner.gui import main_window as main_window_module

    captured: dict[str, object] = {}
    collections: list[object] = []
    snapshot = _snapshot(tmp_path)

    def hold_runner(parent, work, on_done, on_error=None, **_kwargs):
        captured.update(work=work, on_done=on_done)
        return object()

    def collect(config, **kwargs):
        collections.append(config)
        # platformName() is read on the GUI thread and handed to the worker;
        # the worker itself must never touch Qt.
        assert "platform_name" in kwargs
        return snapshot

    monkeypatch.setattr(main_window_module, "run_off_thread", hold_runner)
    monkeypatch.setattr(main_window_module, "collect_environment", collect, raising=False)

    main_window._start_environment_snapshot()

    assert collections == []
    work = captured["work"]
    assert callable(work)
    assert work() is snapshot
    assert collections == [main_window.config]


def test_environment_snapshot_is_an_optional_boot_step(main_window, monkeypatch):
    from anki_miner.gui import main_window as main_window_module

    steps: list[str] = []
    main_window._boot_committed = False
    monkeypatch.setattr(
        main_window_module.MainWindow,
        "_run_optional_boot_step",
        staticmethod(lambda name, _step: steps.append(name)),
    )
    monkeypatch.setattr(main_window_module.QTimer, "singleShot", staticmethod(lambda *_args: None))

    main_window.commit_boot()

    assert "environment snapshot" in steps


def test_environment_callback_logs_formatted_lines(main_window, caplog, tmp_path):
    with caplog.at_level(logging.INFO, logger="anki_miner.gui.main_window"):
        main_window._on_environment_snapshot(_snapshot(tmp_path))

    record = next(record for record in caplog.records if record.getMessage().startswith("env app_version:"))
    assert "app_version=2.9.0" not in record.getMessage()
    assert "2.9.0" in record.getMessage()
    assert record.levelno == logging.INFO
    assert record.name == "anki_miner.gui.main_window"


def test_health_snapshot_logs_first_sweep_and_only_changed_states(main_window, caplog):
    report = _healthy_report()

    with caplog.at_level(logging.INFO, logger="anki_miner.gui.main_window"):
        main_window._log_health_snapshot(report)

    records = [record for record in caplog.records if record.getMessage().startswith("health ")]
    seen_keys = {
        key for key in HEALTH_KEYS if any(record.getMessage().startswith(f"health {key}:") for record in records)
    }
    assert seen_keys == set(HEALTH_KEYS)
    anchor = next(record for record in records if record.getMessage().startswith("health anki.connect:"))
    assert "state=ok" in anchor.getMessage()
    assert anchor.levelno == logging.INFO
    assert anchor.name == "anki_miner.gui.main_window"

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="anki_miner.gui.main_window"):
        main_window._log_health_snapshot(report)
    assert not any(record.getMessage().startswith("health ") for record in caplog.records)

    changed = report.with_update_check(
        state=HEALTH_WARN,
        detail="Version 3.0 is available.",
        checked_at=CHECKED_AT,
    )
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="anki_miner.gui.main_window"):
        main_window._log_health_snapshot(changed)

    changed_record = next(record for record in caplog.records if record.getMessage().startswith("health app.updates:"))
    assert "state=warn" in changed_record.getMessage()
