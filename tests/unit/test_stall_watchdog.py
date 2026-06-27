"""Unit tests for the main-thread stall watchdog."""

from __future__ import annotations

import logging
import time

import pytest
from PyQt6.QtCore import QObject

from anki_miner.gui.utils.stall_watchdog import (
    StallWatchdog,
    get_global_stall_count,
    install_stall_watchdog,
    reset_global_stall_count,
)


@pytest.fixture(autouse=True)
def _reset_global_count():
    reset_global_stall_count()
    yield
    reset_global_stall_count()


def _pump(qtbot, duration_ms: int) -> None:
    """Keep the GUI event loop responsive for ``duration_ms`` ms.

    Loops short ``qtbot.wait`` slices so the heartbeat QTimer keeps firing.
    """
    deadline = time.monotonic() + duration_ms / 1000
    while time.monotonic() < deadline:
        qtbot.wait(20)


def test_responsive_loop_no_stall(qtbot):
    wd = StallWatchdog(threshold_ms=100, poll_ms=20)
    wd.start()
    try:
        # Keep the event loop ticking well past the threshold; no stall.
        _pump(qtbot, 600)
        assert wd.stall_count == 0
        assert wd.last_stall_ms is None
        assert get_global_stall_count() == 0
    finally:
        wd.stop()


def test_blocking_gui_thread_triggers_stall(qtbot, caplog):
    wd = StallWatchdog(threshold_ms=100, poll_ms=20)
    wd.start()
    try:
        # Let one heartbeat land first.
        _pump(qtbot, 100)
        with caplog.at_level(logging.WARNING):
            # Block the GUI thread WITHOUT processing events; the monitor
            # thread observes the missing heartbeat.
            time.sleep(100 * 3 / 1000)
            # Now let the event loop catch up so the heartbeat resumes.
            qtbot.waitUntil(lambda: wd.stall_count >= 1, timeout=3000)

        assert wd.stall_count >= 1
        assert wd.last_stall_ms is not None
        # Roughly the blocked duration (300ms); allow generous slack.
        assert wd.last_stall_ms >= 100
        assert get_global_stall_count() >= 1

        # A WARNING with a stack/traceback was logged.
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert warnings
        assert any("stall" in r.getMessage().lower() for r in warnings)
        assert any(("File " in r.getMessage()) or ("line " in r.getMessage()) for r in warnings)
    finally:
        wd.stop()


def test_global_count_reset(qtbot):
    wd = StallWatchdog(threshold_ms=100, poll_ms=20)
    wd.start()
    try:
        _pump(qtbot, 100)
        time.sleep(100 * 3 / 1000)
        qtbot.waitUntil(lambda: get_global_stall_count() >= 1, timeout=3000)
        assert get_global_stall_count() >= 1
        reset_global_stall_count()
        assert get_global_stall_count() == 0
    finally:
        wd.stop()


def test_stop_halts_monitor_thread(qtbot):
    wd = StallWatchdog(threshold_ms=100, poll_ms=20)
    wd.start()
    _pump(qtbot, 60)
    monitor = wd._monitor_thread
    assert monitor is not None
    assert monitor.is_alive()
    wd.stop()
    qtbot.waitUntil(lambda: not monitor.is_alive(), timeout=3000)
    assert not monitor.is_alive()


def test_stop_safe_before_start_and_twice(qtbot):
    wd = StallWatchdog(threshold_ms=100, poll_ms=20)
    # Safe to stop before ever starting.
    wd.stop()
    wd.start()
    _pump(qtbot, 40)
    wd.stop()
    # Safe to stop twice.
    wd.stop()


def test_start_is_idempotent(qtbot):
    wd = StallWatchdog(threshold_ms=100, poll_ms=20)
    wd.start()
    first = wd._monitor_thread
    wd.start()
    try:
        assert wd._monitor_thread is first
    finally:
        wd.stop()


def test_install_helper(qtbot):
    window = QObject()
    wd = install_stall_watchdog(window)
    try:
        assert window._stall_watchdog is wd
        assert isinstance(wd, StallWatchdog)
        assert wd._monitor_thread is not None
        assert wd._monitor_thread.is_alive()
    finally:
        wd.stop()
