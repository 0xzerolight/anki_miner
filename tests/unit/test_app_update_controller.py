"""AppUpdateController: Update now → download → Restart now (gui/controllers/app_update_controller.py)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from PyQt6.QtWidgets import QWidget

from anki_miner.gui import restart
from anki_miner.gui.capabilities import CapabilityTarget
from anki_miner.gui.controllers.app_update_controller import AppUpdateController
from anki_miner.gui.controllers.task_registry import TaskOutcome, TaskRegistry, TaskSpec
from anki_miner.gui.widgets.update_banner import UpdateBanner
from anki_miner.services.app_updater import StagedUpdate
from anki_miner.services.update_checker import UpdateInfo

TASK_ID = AppUpdateController.TASK_ID


class _FakeWorker:
    def __init__(self) -> None:
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class _FakeBackgroundTasks:
    """Keeps the callbacks start_app_update received, so a test can play the worker."""

    def __init__(self) -> None:
        self.app_update_worker: _FakeWorker | None = None
        self.callbacks = None

    def start_app_update(self, info, *, on_progress, on_result, on_finished):
        if self.app_update_worker is not None:
            return None
        self.app_update_worker = _FakeWorker()
        self.callbacks = (on_progress, on_result, on_finished)
        return self.app_update_worker


class _FakeWindow(QWidget):
    """The four things the controller reads off MainWindow."""

    def __init__(self, registry: TaskRegistry) -> None:
        super().__init__()
        self.task_registry = registry
        self.background_tasks = _FakeBackgroundTasks()
        self.status_bar = MagicMock()
        self.close_result = True
        self.shutting_down = False
        self.closed = 0

    def close(self) -> bool:  # type: ignore[override]
        self.closed += 1
        return self.close_result

    def is_shutting_down(self) -> bool:
        return self.shutting_down


def _info(target: str = "windows-frozen") -> UpdateInfo:
    return UpdateInfo(
        version="9.9.9",
        release_page_url="https://github.com/0xzerolight/anki_miner/releases/latest",
        asset_url="https://github.com/0xzerolight/anki_miner/releases/download/v9.9.9/AnkiMiner-9.9.9-Windows-x86_64-Setup.exe",
        release_notes="",
        asset_sha256="a" * 64,
        target=target,
    )


@pytest.fixture(autouse=True)
def _clean_restart_intent():
    restart.clear_restart_request()
    yield
    restart.clear_restart_request()


@pytest.fixture
def rig(qtbot, monkeypatch):
    monkeypatch.setattr("anki_miner.gui.widgets.update_banner.in_place_supported", lambda info: True)
    registry = TaskRegistry()
    window = _FakeWindow(registry)
    qtbot.addWidget(window)
    banner = UpdateBanner(_info(), window)
    controller = AppUpdateController(window, banner)
    yield window, banner, controller, registry
    registry.shutdown()


def _stage(rig, staged: StagedUpdate) -> None:
    """Click Update now and play a worker that finished with *staged*."""
    window, banner, _, _ = rig
    banner._download_btn.click()
    _, on_result, on_finished = window.background_tasks.callbacks
    on_result(staged)
    on_finished()
    window.background_tasks.app_update_worker = None


def test_update_now_publishes_a_task_with_byte_progress(rig):
    window, banner, _, registry = rig

    banner._download_btn.click()
    on_progress, _, _ = window.background_tasks.callbacks
    on_progress(50, 200)

    snapshot = registry.snapshot(TASK_ID)
    assert snapshot is not None and snapshot.is_running
    assert (snapshot.current, snapshot.total) == (50, 200)
    assert banner._download_btn.text() == "Cancel"


def test_a_staged_update_finishes_the_task_and_offers_restart(rig, tmp_path):
    _, banner, _, registry = rig

    _stage(rig, StagedUpdate(version="9.9.9", target="windows-frozen", path=tmp_path / "Setup.exe"))

    assert registry.snapshot(TASK_ID).outcome is TaskOutcome.SUCCEEDED
    assert banner._download_btn.text() == "Restart now"


def test_a_failure_finishes_failed_and_falls_back_to_the_browser(rig):
    window, banner, _, registry = rig

    banner._download_btn.click()
    _, on_result, on_finished = window.background_tasks.callbacks
    on_result(OSError("disk full"))
    on_finished()

    assert registry.snapshot(TASK_ID).outcome is TaskOutcome.FAILED
    assert banner._download_btn.text() == "Download installer"


def test_cancel_stops_the_worker_and_returns_to_the_offer(rig):
    window, banner, _, registry = rig

    banner._download_btn.click()
    banner._download_btn.click()  # now "Cancel"
    assert window.background_tasks.app_update_worker.cancelled
    _, _, on_finished = window.background_tasks.callbacks
    on_finished()  # a cancelled worker emits no result

    assert registry.snapshot(TASK_ID).outcome is TaskOutcome.CANCELLED
    assert banner._download_btn.text() == "Update now"


def test_the_job_monitor_can_cancel_it(rig):
    window, banner, _, registry = rig

    banner._download_btn.click()
    registry.request_cancel(TASK_ID)

    assert window.background_tasks.app_update_worker.cancelled


def test_restart_refused_while_another_task_runs(rig, tmp_path):
    window, banner, _, registry = rig
    _stage(rig, StagedUpdate(version="9.9.9", target="windows-frozen", path=tmp_path / "Setup.exe"))
    registry.start(TaskSpec(task_id="run.other", title="Mining", owner=CapabilityTarget("settings", "ui")))

    banner._download_btn.click()  # "Restart now"

    assert window.closed == 0
    assert not restart.restart_requested()
    window.status_bar.set_operation.assert_called_once()


def _setup_exe(tmp_path):
    """A staged installer that is still on disk when Restart now is clicked."""
    setup = tmp_path / "Setup.exe"
    setup.write_bytes(b"MZ")
    return setup


def test_windows_restart_runs_the_installer_in_update_mode(rig, tmp_path):
    window, banner, _, _ = rig
    setup = _setup_exe(tmp_path)
    _stage(rig, StagedUpdate(version="9.9.9", target="windows-frozen", path=setup))

    banner._download_btn.click()

    assert window.closed == 1
    assert restart.relaunch_command() == (setup, ("/SILENT", "/SP-", "/NOCANCEL", "/NORESTART", "/UPDATE=1"))


def test_appimage_restart_relaunches_the_replaced_appimage(rig, tmp_path, monkeypatch):
    window, banner, _, _ = rig
    appimage = tmp_path / "AnkiMiner.AppImage"
    monkeypatch.setattr(restart, "resolve_relaunch_target", lambda: appimage)
    _stage(rig, StagedUpdate(version="9.9.9", target="appimage", path=appimage))

    banner._download_btn.click()

    assert window.closed == 1
    assert restart.relaunch_command() == (appimage, ())


def test_a_refused_close_clears_the_intent(rig, tmp_path):
    window, banner, _, _ = rig
    window.close_result = False
    _stage(rig, StagedUpdate(version="9.9.9", target="windows-frozen", path=_setup_exe(tmp_path)))

    banner._download_btn.click()

    assert not restart.restart_requested()


def test_a_deferred_close_keeps_the_intent(rig, tmp_path):
    window, banner, _, _ = rig
    window.close_result = False
    window.shutting_down = True
    _stage(rig, StagedUpdate(version="9.9.9", target="windows-frozen", path=_setup_exe(tmp_path)))

    banner._download_btn.click()

    assert restart.restart_requested()


def test_a_vanished_installer_keeps_the_app_open(rig, tmp_path):
    """TEMP cleanup or an antivirus can remove the staged Setup.exe while the
    banner waits; closing then would leave the user with no app at all."""
    window, banner, _, _ = rig
    _stage(rig, StagedUpdate(version="9.9.9", target="windows-frozen", path=tmp_path / "Setup.exe"))

    banner._download_btn.click()  # "Restart now", but the installer is gone

    assert window.closed == 0
    assert not restart.restart_requested()
    assert banner._download_btn.text() == "Download installer"
