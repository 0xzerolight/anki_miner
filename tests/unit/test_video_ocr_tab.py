"""VideoOcrTab: identity, region gating on click, setup-card states, persistence, worker build."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import replace
from unittest.mock import patch

import pytest
from PyQt6.QtGui import QPixmap

from anki_miner.gui.capabilities import CapabilityTarget
from anki_miner.gui.widgets import video_ocr_tab as mod
from anki_miner.gui.widgets.video_ocr_tab import VideoOcrTab, _EngineState
from anki_miner.services.video_ocr.errors import EngineLoadError
from anki_miner.services.video_ocr.region import Region
from tests.unit._tool_tab_harness import OS_ACCESS, FakeToolWorker


def test_importing_the_tab_does_not_load_numpy():
    # App startup does not load numpy today; the tab (built at startup) must keep it that way.
    code = "import sys, anki_miner.gui.widgets.video_ocr_tab; sys.exit(1 if 'numpy' in sys.modules else 0)"
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    assert subprocess.run([sys.executable, "-c", code], env=env, timeout=120).returncode == 0


_READY = _EngineState(True, True)


def _make(qtbot, config, state=_READY):
    """Build without the async probe, then land a verdict directly (the probe path is the contract test's job)."""
    tab = VideoOcrTab(config, suppress_optional_startup=True)
    qtbot.addWidget(tab)
    tab._apply_probe_result(state)
    return tab


def test_identity():
    assert VideoOcrTab.TASK_ID == "tools.videoocr"
    assert CapabilityTarget("subtitles", "videoocr") == VideoOcrTab.TASK_OWNER
    assert VideoOcrTab.OUTPUT_HISTORY_KEY == "tools.videoocr.output"


def test_start_without_a_region_asks_for_one(qtbot, test_config, tmp_path):
    tab = _make(qtbot, test_config)
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    tab.file_selector.set_path(str(video))
    with patch.object(mod, "VideoOcrWorker", FakeToolWorker):
        tab.ocr_button.click()
    issue = tab.issue_banner().current_issue()
    assert issue is not None and issue.action_text
    assert tab.worker_thread is None


def test_a_run_builds_the_worker_with_region_files_and_overwrite(qtbot, test_config, tmp_path):
    tab = _make(qtbot, test_config)
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    tab.file_selector.set_path(str(video))
    tab._set_region(Region(0.1, 0.8, 0.8, 0.15))
    tab.overwrite_checkbox.setChecked(True)
    with patch.object(mod, "VideoOcrWorker", FakeToolWorker), patch(OS_ACCESS, return_value=True):
        tab.ocr_button.click()
    worker = tab.worker_thread
    assert worker.args[1] == [video] and worker.args[2] == Region(0.1, 0.8, 0.8, 0.15)
    assert worker.kwargs["overwrite"] is True


def test_the_region_is_persisted_and_summarised(qtbot, test_config):
    tab = _make(qtbot, test_config)
    emitted = []
    tab.run_options_changed.connect(emitted.append)
    tab._set_region(Region(0.1, 0.8, 0.8, 0.15))
    assert emitted[-1].video_ocr_region == (0.1, 0.8, 0.8, 0.15)
    assert "80" in tab.region_label.text()


def test_a_config_region_is_adopted_when_idle(qtbot, test_config):
    tab = _make(qtbot, test_config)
    tab.update_config(replace(test_config, video_ocr_region=(0.2, 0.7, 0.6, 0.2)))
    assert tab._region == Region(0.2, 0.7, 0.6, 0.2)


def test_a_config_region_is_adopted_during_a_run(qtbot, test_config, tmp_path):
    # A profile switch mid-run: the worker scans its own copy, the next run must scan the new region.
    tab = _make(qtbot, test_config)
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    tab.file_selector.set_path(str(video))
    tab._set_region(Region(0.1, 0.8, 0.8, 0.15))
    with patch.object(mod, "VideoOcrWorker", FakeToolWorker), patch(OS_ACCESS, return_value=True):
        tab.ocr_button.click()
    assert tab.worker_thread.isRunning()
    tab.update_config(replace(tab.config, video_ocr_region=(0.2, 0.7, 0.6, 0.2)))
    assert tab._region == Region(0.2, 0.7, 0.6, 0.2)
    assert tab.worker_thread.args[2] == Region(0.1, 0.8, 0.8, 0.15)


def test_only_a_non_region_change_re_probes_the_engine(qtbot, test_config):
    tab = _make(qtbot, test_config)
    # MainWindow.update_config bumps config_version on every update, the region's own included.
    bumped = test_config.config_version + 1
    with patch.object(tab, "_refresh_engine_state") as probe:
        tab.update_config(replace(test_config, video_ocr_region=(0.2, 0.7, 0.6, 0.2), config_version=bumped))
        probe.assert_not_called()
        tab.update_config(replace(tab.config, video_ocr_models_root=test_config.video_ocr_models_root / "moved"))
        probe.assert_called_once()


def test_the_thumbnail_survives_the_config_round_trip(qtbot, test_config):
    tab = _make(qtbot, test_config)
    tab._set_region(Region(0.123456, 0.8, 0.75, 0.1), QPixmap(10, 10))
    tab.update_config(tab.config)  # what the app's config_refreshed echo does after persist_run_options
    assert not tab.region_thumbnail.isHidden()
    assert tab._region == Region(0.1235, 0.8, 0.75, 0.1)


def test_an_engine_load_error_gets_its_own_summary(qtbot, test_config):
    tab = _make(qtbot, test_config)
    assert tab._typed_problem_summary(EngineLoadError("no runtime"))
    assert tab._typed_problem_summary(ValueError("other")) is None


def test_setup_card_hidden_when_ready(qtbot, test_config):
    assert _make(qtbot, test_config).setup_card.isHidden()


def test_setup_card_offers_models_when_the_runtime_imports(qtbot, test_config):
    tab = _make(qtbot, test_config, _EngineState(False, True))
    assert not tab.setup_card.isHidden()
    assert "model" in tab.install_button.text().lower()


def test_setup_card_on_an_unsupported_platform_points_to_pip(qtbot, test_config):
    with patch.object(mod.onnx_pack_installer, "onnx_pack_supported", return_value=False):
        tab = _make(qtbot, test_config, _EngineState(False, False))
    assert tab.install_button.isHidden()
    assert "anki-miner[ocr]" in tab.install_status_label.text()


def test_install_click_requests_and_a_failure_keeps_its_message(qtbot, test_config):
    tab = _make(qtbot, test_config, _EngineState(False, True))
    with qtbot.waitSignal(tab.video_ocr_install_requested, timeout=1000):
        tab.install_button.click()
    assert not tab.install_button.isEnabled()
    tab.set_install_status("Download failed: boom")
    tab.notify_install_finished(False)
    assert tab.install_button.isEnabled()
    assert tab.install_status_label.text() == "Download failed: boom"


_BASE_RUN_OFF_THREAD = "anki_miner.gui.widgets._tool_tab_base.run_off_thread"


def _hold_region_listing(tab, folder):
    """Click Set region… in folder mode with the listing held; return run_off_thread's args."""
    tab.folder_mode_button.click()
    tab.folder_selector.set_path(str(folder))
    held: list[tuple] = []
    with patch(_BASE_RUN_OFF_THREAD, side_effect=lambda *args, **kwargs: held.append(args)):
        tab.set_region_button.click()
    assert len(held) == 1  # the listing is still in flight
    return held[0]


def test_a_probe_landing_mid_region_listing_leaves_read_subtitles_armed(qtbot, test_config, tmp_path):
    # Built with no verdict yet: the startup probe is still in flight.
    tab = VideoOcrTab(test_config, suppress_optional_startup=True)
    qtbot.addWidget(tab)
    (tmp_path / "part1.mp4").write_bytes(b"x")
    _, scan, apply, _on_error = _hold_region_listing(tab, tmp_path)

    tab._apply_probe_result(_EngineState(True, True))  # the probe lands mid-listing
    with patch.object(VideoOcrTab, "_open_region_dialog") as open_dialog:
        apply(scan())

    open_dialog.assert_called_once_with(tmp_path / "part1.mp4")
    assert tab.ocr_button.isEnabled()


@pytest.mark.parametrize("outcome", ["videos", "empty", "error"])
def test_set_region_is_held_for_the_listing_and_released_on_every_outcome(qtbot, test_config, tmp_path, outcome):
    tab = _make(qtbot, test_config)
    if outcome == "videos":
        (tmp_path / "part1.mp4").write_bytes(b"x")
    _, scan, apply, on_error = _hold_region_listing(tab, tmp_path)
    assert not tab.set_region_button.isEnabled()

    with patch.object(VideoOcrTab, "_open_region_dialog"):
        if outcome == "error":
            on_error("boom")
        else:
            apply(scan())

    assert tab.set_region_button.isEnabled()
