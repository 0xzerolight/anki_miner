"""Unit tests for the main-thread stall watchdog."""

from __future__ import annotations

import logging
import re
import time

import pytest
from PyQt6.QtCore import QObject
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QApplication

from anki_miner.gui.utils.stall_watchdog import (
    StallWatchdog,
    get_global_stall_count,
    install_stall_watchdog,
    paused_stall_detection,
    reset_global_stall_count,
)


@pytest.fixture(autouse=True)
def _reset_global_count():
    reset_global_stall_count()
    yield
    reset_global_stall_count()


@pytest.fixture(autouse=True)
def _restore_app_appearance():
    """Put the shared QApplication's stylesheet and palette back.

    ``test_apply_to_app_runs_within_pause`` calls ``Theme.apply_to_app`` on the
    real application, which installs a 38 KB themed stylesheet *and* a palette
    process-wide. Left behind, it repaints every widget a later test builds --
    ``test_status_badge_motion`` read a dark theme's surface where its own
    widget-scoped ``background: #ff0000`` should have been. Whether that lands
    depends on which files xdist happens to put on one worker, so it surfaces
    as an unrelated test failing on CI and passing locally.
    """
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        yield
        return
    stylesheet = app.styleSheet()
    palette = QPalette(app.palette())
    yield
    app.setStyleSheet(stylesheet)
    app.setPalette(palette)


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


def test_pause_suppresses_stall(qtbot, caplog):
    wd = StallWatchdog(threshold_ms=100, poll_ms=20)
    wd.start()
    try:
        _pump(qtbot, 100)
        with caplog.at_level(logging.WARNING), paused_stall_detection():
            # Block the GUI thread well past the threshold; inside the
            # pause this must NOT be recorded as a stall.
            time.sleep(100 * 4 / 1000)
            # Give the monitor several poll cycles to (not) fire.
            deadline = time.monotonic() + 0.2
            while time.monotonic() < deadline:
                time.sleep(0.02)

        assert wd.stall_count == 0
        assert get_global_stall_count() == 0
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert not any("stall" in r.getMessage().lower() for r in warnings)
    finally:
        wd.stop()


def test_pause_exit_refreshes_heartbeat_no_late_stall(qtbot):
    """The paused span itself must not register as a stall after resume."""
    wd = StallWatchdog(threshold_ms=100, poll_ms=20)
    wd.start()
    try:
        _pump(qtbot, 100)
        with paused_stall_detection():
            # Long block, longer than threshold, with no heartbeat ticks.
            time.sleep(100 * 4 / 1000)
        # On exit the heartbeat is refreshed. Pump the GUI loop so the monitor
        # gets several poll cycles with a fresh tick: the just-elapsed paused
        # span must not be reported as a stall now that detection resumed.
        _pump(qtbot, 150)
        assert wd.stall_count == 0
        assert get_global_stall_count() == 0
    finally:
        wd.stop()


def test_detection_resumes_after_pause(qtbot):
    wd = StallWatchdog(threshold_ms=100, poll_ms=20)
    wd.start()
    try:
        _pump(qtbot, 100)
        with paused_stall_detection():
            time.sleep(100 * 3 / 1000)
        # Pump so the heartbeat resumes and reporting re-arms.
        _pump(qtbot, 100)
        # Now a real (unpaused) block must be detected.
        time.sleep(100 * 3 / 1000)
        qtbot.waitUntil(lambda: wd.stall_count >= 1, timeout=3000)
        assert wd.stall_count >= 1
    finally:
        wd.stop()


def test_nested_pause(qtbot):
    """Inner pause exit must not re-enable detection while the outer is active."""
    wd = StallWatchdog(threshold_ms=100, poll_ms=20)
    wd.start()
    try:
        _pump(qtbot, 100)
        with paused_stall_detection():
            with paused_stall_detection():
                time.sleep(100 * 2 / 1000)
            # Outer still active: block more, still suppressed.
            time.sleep(100 * 2 / 1000)
            deadline = time.monotonic() + 0.15
            while time.monotonic() < deadline:
                time.sleep(0.02)
        assert wd.stall_count == 0
        assert get_global_stall_count() == 0
    finally:
        wd.stop()


def test_apply_to_app_runs_within_pause(qtbot, monkeypatch):
    """Theme.apply_to_app must wrap the repolish in paused_stall_detection()."""
    import anki_miner.gui.utils.stall_watchdog as sw
    from anki_miner.gui.resources.styles.theme import Theme

    paused_during_setstylesheet: list[bool] = []

    app = QApplication.instance()
    assert app is not None

    orig_set = app.setStyleSheet

    def _spy_set(sheet):
        paused_during_setstylesheet.append(sw._stall_detection_paused())
        orig_set(sheet)

    monkeypatch.setattr(app, "setStyleSheet", _spy_set)

    Theme.apply_to_app(app)

    assert paused_during_setstylesheet == [True]


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


def test_stall_record_carries_episode_and_total(qtbot, caplog):
    """A stall line reports which episode it is and the process-wide total."""
    wd = StallWatchdog(threshold_ms=100, poll_ms=20)
    wd.start()
    try:
        _pump(qtbot, 100)
        with caplog.at_level(logging.WARNING, logger="anki_miner.gui.utils.stall_watchdog"):
            time.sleep(100 * 3 / 1000)
            qtbot.waitUntil(lambda: wd.stall_count >= 1, timeout=3000)
        messages = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
        assert any("episode=1" in m and "total=" in m for m in messages), messages
    finally:
        wd.stop()


def test_pause_logs_resume_span_at_debug(caplog):
    """Leaving a labelled pause logs how long the deliberate block ran."""
    with (
        caplog.at_level(logging.DEBUG, logger="anki_miner.gui.utils.stall_watchdog"),
        paused_stall_detection("theme repolish"),
    ):
        time.sleep(0.05)
    records = [r for r in caplog.records if r.getMessage().startswith("stall detection resumed:")]
    assert len(records) == 1
    assert records[0].levelno == logging.DEBUG
    message = records[0].getMessage()
    assert "theme repolish" in message
    assert re.search(r"after \d+ ms", message), message


def test_long_pause_logs_resume_span_at_info(caplog, monkeypatch):
    """A pause past the info threshold is worth an INFO receipt."""
    import anki_miner.gui.utils.stall_watchdog as sw

    monkeypatch.setattr(sw, "_PAUSE_INFO_MS", 10)
    with (
        caplog.at_level(logging.DEBUG, logger="anki_miner.gui.utils.stall_watchdog"),
        paused_stall_detection("theme repolish"),
    ):
        time.sleep(0.05)
    records = [r for r in caplog.records if r.getMessage().startswith("stall detection resumed:")]
    assert len(records) == 1
    assert records[0].levelno == logging.INFO


def test_unlabelled_pause_still_logs(caplog):
    with caplog.at_level(logging.DEBUG, logger="anki_miner.gui.utils.stall_watchdog"), paused_stall_detection():
        time.sleep(0.01)
    assert any(r.getMessage().startswith("stall detection resumed:") for r in caplog.records)


def test_stop_logs_stall_count(qtbot, caplog):
    wd = StallWatchdog(threshold_ms=100, poll_ms=20)
    wd.start()
    _pump(qtbot, 40)
    with caplog.at_level(logging.DEBUG, logger="anki_miner.gui.utils.stall_watchdog"):
        wd.stop()
        # A second stop must stay silent: the watchdog is already down.
        wd.stop()
    records = [r for r in caplog.records if r.getMessage().startswith("stall watchdog stopped:")]
    assert len(records) == 1
    assert "stalls=0" in records[0].getMessage()


def test_stop_before_start_logs_nothing(caplog):
    wd = StallWatchdog(threshold_ms=100, poll_ms=20)
    with caplog.at_level(logging.DEBUG, logger="anki_miner.gui.utils.stall_watchdog"):
        wd.stop()
    assert not [r for r in caplog.records if r.getMessage().startswith("stall watchdog stopped:")]


def test_dump_stacks_later_is_cancellable(monkeypatch, tmp_path):
    """An armed shutdown dump writes nothing once cancelled."""
    import anki_miner.gui.app as app_module
    from anki_miner.gui.utils.stall_watchdog import cancel_stack_dump, dump_stacks_later

    sink_path = tmp_path / "crash.log"
    with open(sink_path, "w", encoding="utf-8") as sink:
        monkeypatch.setattr(app_module, "crash_stream", lambda: sink)
        try:
            assert dump_stacks_later(0.1) is True
            cancel_stack_dump()
            time.sleep(0.3)
            sink.flush()
        finally:
            cancel_stack_dump()
    assert sink_path.read_text(encoding="utf-8") == ""


def test_dump_stacks_later_accepts_a_long_delay(monkeypatch, tmp_path):
    import anki_miner.gui.app as app_module
    from anki_miner.gui.utils.stall_watchdog import cancel_stack_dump, dump_stacks_later

    sink_path = tmp_path / "crash.log"
    with open(sink_path, "w", encoding="utf-8") as sink:
        monkeypatch.setattr(app_module, "crash_stream", lambda: sink)
        try:
            assert dump_stacks_later(5) is True
            time.sleep(0.2)
            sink.flush()
            assert sink_path.read_text(encoding="utf-8") == ""
        finally:
            cancel_stack_dump()


def test_dump_stacks_later_reports_failure(monkeypatch):
    """A sink faulthandler cannot use is reported, not raised."""
    import anki_miner.gui.utils.stall_watchdog as sw

    def _boom(*args, **kwargs):
        raise RuntimeError("no fileno")

    monkeypatch.setattr(sw.faulthandler, "dump_traceback_later", _boom)
    assert sw.dump_stacks_later(5) is False
