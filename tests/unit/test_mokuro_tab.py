"""Tests for MokuroTab — availability guard, folder scan → worker, options, cancel, close."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.mokuro_tab import MokuroTab
from anki_miner.services.mokuro_volumes import MokuroVolume

_AVAILABLE = "anki_miner.gui.widgets.mokuro_tab.MokuroTab._mokuro_ready"
_COMPUTE_AVAILABLE = "anki_miner.gui.widgets.mokuro_tab.MokuroTab._compute_mokuro_available"
_OS_ACCESS = "anki_miner.gui.widgets.mokuro_tab.os.access"
_WORKER_CLS = "anki_miner.gui.widgets.mokuro_tab.MokuroWorker"
_RUN_OFF_THREAD = "anki_miner.gui.widgets.mokuro_tab.run_off_thread"


class _FakeWorker:
    def __init__(self) -> None:
        for name in (
            "file_started",
            "file_progress",
            "file_finished",
            "file_skipped",
            "queue_finished",
            "error",
            "finished",
        ):
            setattr(self, name, MagicMock())
        self.deleteLater = MagicMock()
        self.started = False
        self.cancelled = False

    def start(self) -> None:
        self.started = True

    def cancel(self) -> None:
        self.cancelled = True

    def isRunning(self) -> bool:
        return self.started and not self.cancelled

    def wait(self, *_a) -> bool:
        return True


def _config(tmp_path) -> AnkiMinerConfig:
    return AnkiMinerConfig(media_temp_folder=tmp_path / "tmp", uv_root=tmp_path / "uv")


def _make_tab(config, qtbot) -> MokuroTab:
    with patch(_COMPUTE_AVAILABLE, return_value=True):
        tab = MokuroTab(config)
        qtbot.addWidget(tab)
        assert tab._availability_worker.wait(3000)
        qtbot.waitUntil(tab.run_button.isEnabled, timeout=3000)
    return tab


def _series(tmp_path) -> Path:
    for n in ("vol1", "vol2"):
        (tmp_path / "series" / n).mkdir(parents=True)
        (tmp_path / "series" / n / "p.jpg").write_bytes(b"x")
    return tmp_path / "series"


def _run(tab, qtbot, fake):
    with (
        patch(_AVAILABLE, return_value=True),
        patch(_OS_ACCESS, return_value=True),
        patch(_WORKER_CLS, return_value=fake) as cls,
    ):
        tab.run_button.click()
        qtbot.waitUntil(lambda: fake.started, timeout=3000)
    return cls


class TestConstruction:
    def test_ids(self, qtbot, tmp_path):
        tab = _make_tab(_config(tmp_path), qtbot)
        assert tab.TASK_ID == "tools.mokuro"
        assert tab.TASK_OWNER.main_tab == "subtitles" and tab.TASK_OWNER.subtab == "mokuro"
        assert tab.OUTPUT_HISTORY_KEY == ""

    def test_unavailable_disables_run_and_shows_notice(self, qtbot, tmp_path):
        with patch(_COMPUTE_AVAILABLE, return_value=False):
            tab = MokuroTab(_config(tmp_path))
            qtbot.addWidget(tab)
            assert tab._availability_worker.wait(3000)
            qtbot.waitUntil(lambda: not tab.engine_notice_label.isHidden(), timeout=3000)
        assert not tab.run_button.isEnabled()

    def test_suppressed_startup_skips_probe(self, qtbot, tmp_path):
        tab = MokuroTab(_config(tmp_path), suppress_optional_startup=True)
        qtbot.addWidget(tab)
        assert tab._availability_worker is None

    def test_options_seed_from_config(self, qtbot, tmp_path):
        import dataclasses

        tab = _make_tab(dataclasses.replace(_config(tmp_path), mokuro_use_gpu=False), qtbot)
        assert tab.gpu_checkbox.isChecked() is False
        assert tab.redo_checkbox.isChecked() is False


class TestRun:
    def test_scan_then_worker_with_volumes_and_options(self, qtbot, tmp_path):
        tab = _make_tab(_config(tmp_path), qtbot)
        tab.folder_selector.set_path(str(_series(tmp_path)))
        tab.redo_checkbox.setChecked(True)
        tab.gpu_checkbox.setChecked(False)
        fake = _FakeWorker()
        cls = _run(tab, qtbot, fake)
        args, kwargs = cls.call_args
        assert [v.source.name for v in args[1]] == ["vol1", "vol2"]
        assert all(isinstance(v, MokuroVolume) for v in args[1])
        assert kwargs["options"].force_cpu is True and kwargs["options"].no_cache is True
        assert kwargs["skip_processed"] is False
        assert tab._item_total() == 2
        assert not tab.run_button.isEnabled() and not tab.cancel_button.isHidden()

    def test_no_folder_raises_screen_issue(self, qtbot, tmp_path):
        tab = _make_tab(_config(tmp_path), qtbot)
        issues: list = []
        with patch(_AVAILABLE, return_value=True), patch.object(tab, "show_screen_issue", side_effect=issues.append):
            tab._on_run()
        assert issues and "folder" in issues[0].summary.lower()

    def test_empty_folder_raises_screen_issue(self, qtbot, tmp_path):
        tab = _make_tab(_config(tmp_path), qtbot)
        (tmp_path / "empty").mkdir()
        tab.folder_selector.set_path(str(tmp_path / "empty"))
        issues: list = []
        with (
            patch(_AVAILABLE, return_value=True),
            patch.object(tab, "show_screen_issue", side_effect=issues.append),
            patch(_WORKER_CLS) as cls,
        ):
            tab._on_run()
            qtbot.waitUntil(lambda: bool(issues), timeout=3000)
        cls.assert_not_called()
        assert "no manga volumes" in issues[0].summary.lower()

    def test_unwritable_folder_raises_screen_issue(self, qtbot, tmp_path):
        tab = _make_tab(_config(tmp_path), qtbot)
        tab.folder_selector.set_path(str(_series(tmp_path)))
        issues: list = []
        with (
            patch(_AVAILABLE, return_value=True),
            patch(_OS_ACCESS, return_value=False),
            patch.object(tab, "show_screen_issue", side_effect=issues.append),
            patch(_WORKER_CLS) as cls,
        ):
            tab._on_run()
            qtbot.waitUntil(lambda: bool(issues), timeout=3000)
        cls.assert_not_called()
        assert "not writable" in issues[0].summary.lower()

    def test_probe_landing_during_run_scan_keeps_run_disabled(self, qtbot, tmp_path):
        tab = _make_tab(_config(tmp_path), qtbot)
        tab._scan_pending_run = True
        tab._apply_probe_result(True)
        assert not tab.run_button.isEnabled()

    def test_pending_run_scan_blocks_a_second_scan(self, qtbot, tmp_path):
        tab = _make_tab(_config(tmp_path), qtbot)
        tab.folder_selector.set_path(str(_series(tmp_path)))
        # The scan thread has stopped but its result_ready has not run yet: the
        # flag alone must hold the gate.
        tab._scan_worker = None
        tab._scan_pending_run = True
        with patch(_AVAILABLE, return_value=True), patch(_RUN_OFF_THREAD) as dispatch:
            tab._on_run()
        dispatch.assert_not_called()

    def test_reentrancy_guard(self, qtbot, tmp_path):
        tab = _make_tab(_config(tmp_path), qtbot)
        tab.folder_selector.set_path(str(_series(tmp_path)))
        fake = _FakeWorker()
        _run(tab, qtbot, fake)
        with patch(_AVAILABLE, return_value=True), patch(_WORKER_CLS) as cls:
            tab._on_run()
        cls.assert_not_called()


class TestPreviewAndPersistence:
    def test_folder_change_updates_volumes_label(self, qtbot, tmp_path):
        tab = _make_tab(_config(tmp_path), qtbot)
        series = _series(tmp_path)
        (tmp_path / "series" / "vol1.mokuro").write_text("{}")
        # Drive the slot path_changed is wired to directly, so the test stays
        # signal-agnostic (set_path would reach it via textChanged).
        tab._on_folder_changed(str(series))
        qtbot.waitUntil(lambda: "2" in tab.volumes_label.text(), timeout=3000)
        assert "1" in tab.volumes_label.text()  # already processed count

    def test_stale_preview_scan_is_dropped(self, qtbot, tmp_path):
        tab = _make_tab(_config(tmp_path), qtbot)
        series = _series(tmp_path)
        tab._on_folder_changed(str(series))
        tab._on_folder_changed("")  # user cleared the field before the scan landed
        assert tab._scan_worker is not None and tab._scan_worker.wait(3000)
        qtbot.wait(50)
        assert tab.volumes_label.text() == ""

    def test_gpu_toggle_persists_via_config_changed(self, qtbot, tmp_path):
        tab = _make_tab(_config(tmp_path), qtbot)
        seen: list = []
        tab.config_changed.connect(seen.append)
        tab.gpu_checkbox.setChecked(False)
        assert seen and seen[-1].mokuro_use_gpu is False

    def test_redo_toggle_is_transient(self, qtbot, tmp_path):
        tab = _make_tab(_config(tmp_path), qtbot)
        seen: list = []
        tab.config_changed.connect(seen.append)
        tab.redo_checkbox.setChecked(True)
        assert seen == []

    def test_update_config_with_only_gpu_change_skips_probe(self, qtbot, tmp_path):
        import dataclasses

        tab = _make_tab(_config(tmp_path), qtbot)
        with patch(_COMPUTE_AVAILABLE, return_value=True) as compute:
            tab.update_config(dataclasses.replace(tab.config, mokuro_use_gpu=False))
            assert compute.call_count == 0
            tab.update_config(dataclasses.replace(tab.config, mokuro_location=tmp_path / "m"))
            assert tab._availability_worker.wait(3000)
            assert compute.call_count == 1

    def test_notify_install_finished_reprobes_and_enables_run(self, qtbot, tmp_path):
        """An install changes no config value, so update_config's mask would skip
        the probe; the install wiring tells the tab directly."""
        with patch(_COMPUTE_AVAILABLE, return_value=False):
            tab = MokuroTab(_config(tmp_path))
            qtbot.addWidget(tab)
            assert tab._availability_worker.wait(3000)
            qtbot.waitUntil(lambda: not tab.engine_notice_label.isHidden(), timeout=3000)
        assert not tab.run_button.isEnabled()

        with patch(_COMPUTE_AVAILABLE, return_value=True) as compute:
            tab.notify_install_finished()
            assert tab._availability_worker.wait(3000)
            qtbot.waitUntil(tab.run_button.isEnabled, timeout=3000)
        assert compute.call_count == 1
        assert tab.engine_notice_label.isHidden()


class TestCancelAndClose:
    def test_cancel_forwards_to_worker(self, qtbot, tmp_path):
        tab = _make_tab(_config(tmp_path), qtbot)
        tab.folder_selector.set_path(str(_series(tmp_path)))
        fake = _FakeWorker()
        _run(tab, qtbot, fake)
        tab.cancel_button.click()
        assert fake.cancelled and not tab.cancel_button.isEnabled()

    def test_iter_close_workers_yields_run_worker(self, qtbot, tmp_path):
        tab = _make_tab(_config(tmp_path), qtbot)
        tab.folder_selector.set_path(str(_series(tmp_path)))
        fake = _FakeWorker()
        _run(tab, qtbot, fake)
        assert fake in list(tab.iter_close_workers())
