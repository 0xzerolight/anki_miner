"""Tests for the full-window ``AppDriver`` (real ``MainWindow``, offscreen Qt).

The headline acceptance is :func:`test_full_window_preview_drives_real_mainwindow`:
it builds the ACTUAL :class:`~anki_miner.gui.main_window.MainWindow` under an
isolated test home with the harness config injected, mounts the driver's real
``SingleEpisodeTab`` into the window's tab bar, drives a preview run through the
window's own ``_on_processing_result`` slot (which pops the patched
``ResultsDialog``), and asserts the window saw the result without blocking.
Preview mode parses + filters only, so it needs neither Anki nor card creation
and runs fully offscreen.

This is the HIGHEST-RISK harness path (Qt lifecycle / MainWindow startup), so
the tests pay particular attention to clean disposal. The ``AppDriver`` OWNS the
full widget lifecycle via :meth:`AppDriver.dispose` (close + deleteLater +
deferred-delete drain of BOTH the window and the mounted tab), so the tests must
NOT also register them with ``qtbot.addWidget`` — qtbot's own teardown
``close()`` on an already-deleted C++ object would crash. Each test takes the
``qtbot`` fixture (which guarantees a live QApplication) and calls
``driver.dispose()`` in a ``finally`` so both C++ objects are destroyed within
the test rather than leaking to a later ``processEvents`` — the documented
pytest-qt segfault hazard the conftest ``_drain_qt_deletes`` net backstops.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.e2e.app_config import build_app_config
from tests.e2e.app_driver import AppDriver
from tests.e2e.artifacts import RunDir
from tests.e2e.config import E2EConfig
from tests.e2e.fixtures_dictionary import seed_offline_dict
from tests.e2e.fixtures_media import get_test_video
from tests.e2e.fixtures_subtitle import EXPECTED_LEMMAS, get_test_srt

# fugashi/MeCab is required for the real tokenizer (preview run); skip cleanly
# if it is absent (mirrors test_driver.py).
pytest.importorskip("fugashi")


def test_full_window_builds_clean(tmp_path: Path, qtbot) -> None:
    """A real ``MainWindow`` builds offscreen under the test home without blocking.

    Asserts startup isolation worked: the loaded config carries the harness deck
    and the heavy/blocking startup paths (update check, first-run offer, startup
    validation worker) are disabled, so no background QThread is left running and
    the episode tab is mounted and locatable.
    """
    e2e = E2EConfig(test_home=tmp_path)
    cfg = build_app_config(e2e, tmp_path, bypass_known_words=True)
    seed_offline_dict(cfg.dicts_root)
    run_dir = RunDir(e2e.runs_root, label="full-window-build")

    driver = AppDriver(cfg, run_dir)
    try:
        # The injected config reached the live window (startup isolation worked).
        assert driver.window.get_config().anki_deck_name == e2e.deck_name
        assert driver.window.config.check_for_updates is False
        # yt-dlp auto-update is off too: it shells out + hits GitHub on startup
        # and would outlive teardown, tripping the worker-release race.
        assert driver.window.config.auto_update_ytdlp is False
        # No startup validation worker was spawned (it needs Anki + crashes at
        # teardown — see AppDriver for the patch).
        assert driver.window.background_tasks.validation_worker is None
        # The episode tab is mounted in the window's tab bar.
        assert driver.episode_tab_index >= 0
        assert driver.window.tabs.widget(driver.episode_tab_index) is driver.tab
    finally:
        driver.dispose()


def test_full_window_tab_switching(tmp_path: Path, qtbot) -> None:
    """Switching tabs by index drives the real ``QTabWidget`` current index."""
    e2e = E2EConfig(test_home=tmp_path)
    cfg = build_app_config(e2e, tmp_path, bypass_known_words=True)
    seed_offline_dict(cfg.dicts_root)
    run_dir = RunDir(e2e.runs_root, label="full-window-tabs")

    driver = AppDriver(cfg, run_dir)
    try:
        assert driver.window.tabs.count() >= 1
        driver.switch_to_tab(driver.episode_tab_index)
        assert driver.window.tabs.currentIndex() == driver.episode_tab_index
    finally:
        driver.dispose()


def test_full_window_menu_action_triggers(tmp_path: Path, qtbot) -> None:
    """A real menu ``QAction`` triggers offscreen without blocking the UI.

    Uses the Help → "Open Log Folder" action: its handler calls
    ``QDesktopServices.openUrl`` (an offscreen no-op) after ensuring the log
    folder exists, so triggering it is non-blocking and observable.
    """
    e2e = E2EConfig(test_home=tmp_path)
    cfg = build_app_config(e2e, tmp_path, bypass_known_words=True)
    seed_offline_dict(cfg.dicts_root)
    run_dir = RunDir(e2e.runs_root, label="full-window-menu")

    driver = AppDriver(cfg, run_dir)
    try:
        from unittest.mock import patch

        with patch("PyQt6.QtGui.QDesktopServices.openUrl", return_value=True) as open_url:
            driver.trigger_menu_action("Open Log Folder")
        assert open_url.called
        # The handler created the log folder as a side effect.
        assert driver.window.config.log_path.parent.exists()
    finally:
        driver.dispose()


def test_full_window_preview_drives_real_mainwindow(tmp_path: Path, qtbot) -> None:
    """Drive a preview run through the real ``MainWindow`` end-to-end, no Anki.

    Preview = parse + filter only, so it completes fully offscreen. The run's
    result flows through the window's own ``_on_processing_result`` slot, which
    pops the (patched) ``ResultsDialog`` — exercising the dialog wiring that the
    bare-tab driver never touches. Asserts the pipeline genuinely ran (words ==
    the fixture's ``EXPECTED_LEMMAS``), the window observed exactly one result,
    and the (patched) dialog flow did not block.
    """
    e2e = E2EConfig(test_home=tmp_path)
    cfg = build_app_config(e2e, tmp_path, bypass_known_words=True)
    seed_offline_dict(cfg.dicts_root)
    run_dir = RunDir(e2e.runs_root, label="full-window-preview")

    driver = AppDriver(cfg, run_dir)
    try:
        driver.switch_to_tab(driver.episode_tab_index)
        driver.select_video(get_test_video())
        driver.select_subtitle(get_test_srt())
        driver.click_preview()
        result = driver.wait_for_result(timeout_s=60)

        assert result.success, result.errors
        assert result.total_words_found == len(EXPECTED_LEMMAS)
        assert result.cards_created == 0  # preview creates nothing
        # The window's result slot fired exactly once (the patched ResultsDialog
        # was constructed + exec()'d non-blocking — no freeze).
        assert driver.window_results_seen == 1
        assert not driver.dialog_blocked

        shot = driver.screenshot("full-window-preview-done")
        assert shot.is_file() and shot.stat().st_size > 0
    finally:
        driver.dispose()


@pytest.mark.e2e
def test_full_window_process_creates_cards_live(tmp_path: Path, qtbot) -> None:
    """Real card creation through the full ``MainWindow`` (skips when Anki is down).

    The faithful full-window counterpart of ``test_process_creates_cards_live``:
    drives Process through the real window, the patched ``ResultsDialog`` fires
    (no real popup), and the deck card count matches ``cards_created``.
    """
    from tests.e2e.anki_gateway import AnkiGateway, AnkiUnreachableError

    e2e = E2EConfig(test_home=tmp_path)
    gateway = AnkiGateway(e2e)
    try:
        gateway.ping()
    except AnkiUnreachableError:
        pytest.skip("Anki not running (AnkiConnect unreachable)")
    gateway.ensure_test_deck()
    gateway.ensure_test_model()

    cfg = build_app_config(e2e, tmp_path, bypass_known_words=True)
    seed_offline_dict(cfg.dicts_root)
    run_dir = RunDir(e2e.runs_root, label="full-window-process")

    driver = AppDriver(cfg, run_dir, curation_policy="all")
    try:
        driver.select_video(get_test_video())
        driver.select_subtitle(get_test_srt())
        driver.click_process()
        result = driver.wait_for_result(timeout_s=e2e.result_timeout_s)
        assert result.success, result.errors
        assert result.cards_created > 0
        assert driver.window_results_seen == 1
        assert gateway.deck_card_count() == result.cards_created
    finally:
        driver.dispose()
        gateway.delete_test_deck()
