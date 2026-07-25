"""Startup stats initialization stays off the GUI thread and wakes Analytics."""

from __future__ import annotations

import logging
import threading
from unittest.mock import MagicMock

import pytest
from PyQt6.QtWidgets import QWidget

from anki_miner.gui import app as app_module
from anki_miner.gui.widgets.analytics_tab import AnalyticsTab
from anki_miner.models.stats import OverallStats
from anki_miner.services.stats_service import StatsService


def _analytics_service() -> MagicMock:
    service = MagicMock(spec=StatsService)
    service.is_available.return_value = False
    service.get_overall_stats.return_value = OverallStats()
    service.get_recent_sessions.return_value = []
    service.get_series_difficulty.return_value = []
    service.get_milestones.return_value = []
    return service


def test_startup_load_runs_off_gui_thread_and_refreshes_open_analytics(qtbot):
    window = QWidget()
    qtbot.addWidget(window)
    # Production shows the window before scheduling the stats load; the ready
    # callback treats a hidden window as "closing" and skips the refresh.
    window.show()
    qtbot.waitUntil(window.isVisible, timeout=1000)
    service = _analytics_service()
    tab = AnalyticsTab(service)
    qtbot.addWidget(tab)
    tab.show()
    qtbot.waitUntil(tab.isVisible, timeout=1000)
    assert tab._last_refresh is None

    gui_thread_id = threading.get_ident()
    load_thread_ids: list[int] = []
    load_started = threading.Event()
    release_load = threading.Event()
    ready = False

    def load() -> bool:
        nonlocal ready
        load_thread_ids.append(threading.get_ident())
        load_started.set()
        assert release_load.wait(timeout=3)
        ready = True
        return True

    service.load.side_effect = load
    service.is_available.side_effect = lambda: ready
    workers_before = set(getattr(window, "_off_thread_workers", set()))

    app_module._start_stats_load(window, service, tab)

    startup_workers = set(getattr(window, "_off_thread_workers", set())) - workers_before
    assert len(startup_workers) == 1
    startup_worker = startup_workers.pop()
    try:
        assert load_started.wait(timeout=1)
        assert tab._last_refresh is None
    finally:
        release_load.set()
        assert startup_worker.wait(3000)

    qtbot.waitUntil(lambda: tab._last_refresh is not None, timeout=3000)
    assert load_thread_ids and load_thread_ids[0] != gui_thread_id
    assert service.get_overall_stats.call_count == 1
    for worker in list(getattr(tab, "_off_thread_workers", set())):
        assert worker.wait(3000)


@pytest.mark.parametrize("failure_mode", ["false", "error"])
def test_startup_load_failure_logs_one_warning_without_refresh(failure_mode, monkeypatch, qtbot, caplog):
    window = QWidget()
    qtbot.addWidget(window)
    service = _analytics_service()
    analytics = MagicMock(spec=AnalyticsTab)

    def fake_run_off_thread(parent, work, on_done, on_error):
        if failure_mode == "false":
            on_done(False)
        else:
            on_error("db unavailable")
        return MagicMock()

    monkeypatch.setattr(app_module, "run_off_thread", fake_run_off_thread, raising=False)
    with caplog.at_level(logging.WARNING, logger="anki_miner.gui.app"):
        app_module._start_stats_load(window, service, analytics)

    warnings = [
        record
        for record in caplog.records
        if record.name == "anki_miner.gui.app"
        and record.levelno == logging.WARNING
        and record.getMessage().startswith("Stats database initialization failed")
    ]
    assert len(warnings) == 1
    analytics.refresh_data.assert_not_called()


def test_ready_after_window_hidden_skips_refresh(monkeypatch, qtbot):
    """A load result delivered after closeEvent's sweep (window hidden) must not
    spawn a fresh Analytics worker — nothing would join it."""
    window = QWidget()
    qtbot.addWidget(window)
    service = _analytics_service()
    analytics = MagicMock(spec=AnalyticsTab)

    def fake_run_off_thread(parent, work, on_done, on_error):
        on_done(True)  # delivered while window is still hidden
        return MagicMock()

    monkeypatch.setattr(app_module, "run_off_thread", fake_run_off_thread, raising=False)
    app_module._start_stats_load(window, service, analytics)
    analytics.refresh_data.assert_not_called()
