"""Starting Anki at launch when the startup check finds it closed."""

from __future__ import annotations

import threading
import time

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import QObject

from anki_miner.gui.controllers import anki_auto_open as mod


@pytest.fixture
def launches(monkeypatch):
    calls: list[list[str]] = []
    monkeypatch.setattr(mod, "anki_launch_command", lambda: ["anki"])
    monkeypatch.setattr(mod, "launch_anki", lambda command: calls.append(command) or True)
    return calls


def _opener(qtbot, answers, *, attempts=5, check=None):
    parent = QObject()
    checks: list[int] = []
    ready: list[bool] = []

    def default_check():
        checks.append(1)
        return answers[min(len(checks), len(answers)) - 1]

    opener = mod.AnkiAutoOpener(
        parent, check or default_check, lambda: ready.append(True), interval_ms=1, attempts=attempts
    )
    return parent, opener, checks, ready


def _drain(qtbot, opener):
    """Let every off-thread check finish before the test ends (testing.md: join workers)."""
    qtbot.waitUntil(lambda: not getattr(opener, "_off_thread_workers", None), timeout=3000)


def test_starts_anki_and_hands_over_once_ankiconnect_answers(qtbot, launches):
    parent, opener, checks, ready = _opener(qtbot, [(False, "down"), (True, "ok")])

    opener.open()

    qtbot.waitUntil(lambda: ready == [True], timeout=3000)
    _drain(qtbot, opener)
    assert launches == [["anki"]]
    assert len(checks) == 2


def test_opens_once_per_launch(qtbot, launches):
    parent, opener, checks, ready = _opener(qtbot, [(True, "ok")])

    opener.open()
    opener.open()

    qtbot.waitUntil(lambda: ready == [True], timeout=3000)
    _drain(qtbot, opener)
    assert launches == [["anki"]]


def test_no_install_found_starts_nothing(qtbot, monkeypatch):
    started: list[list[str]] = []
    monkeypatch.setattr(mod, "anki_launch_command", lambda: None)
    monkeypatch.setattr(mod, "launch_anki", lambda command: started.append(command) or True)
    parent, opener, checks, ready = _opener(qtbot, [(True, "ok")])

    opener.open()
    qtbot.wait(50)

    assert started == [] and checks == [] and ready == []


def test_refused_start_does_not_poll(qtbot, monkeypatch):
    monkeypatch.setattr(mod, "anki_launch_command", lambda: ["anki"])
    monkeypatch.setattr(mod, "launch_anki", lambda command: False)
    parent, opener, checks, ready = _opener(qtbot, [(True, "ok")])

    opener.open()
    qtbot.wait(50)

    assert checks == [] and ready == []


def test_gives_up_after_the_last_attempt(qtbot, launches):
    parent, opener, checks, ready = _opener(qtbot, [(False, "down")], attempts=3)

    opener.open()

    qtbot.waitUntil(lambda: len(checks) == 3, timeout=3000)
    qtbot.wait(100)
    _drain(qtbot, opener)
    assert len(checks) == 3
    assert ready == []
    assert launches == [["anki"]]


def test_checks_never_overlap(qtbot, launches):
    lock = threading.Lock()
    running = 0
    peak = 0
    done: list[int] = []

    def slow_check():
        nonlocal running, peak
        with lock:
            running += 1
            peak = max(peak, running)
        time.sleep(0.02)
        with lock:
            running -= 1
        done.append(1)
        return (False, "down")

    parent, opener, _checks, ready = _opener(qtbot, [], attempts=4, check=slow_check)

    opener.open()

    qtbot.waitUntil(lambda: len(done) == 4, timeout=3000)
    _drain(qtbot, opener)
    assert peak == 1
