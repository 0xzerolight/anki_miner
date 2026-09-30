"""A run parked on the Word Curator is waiting on a person, not stalled (A03)."""

from __future__ import annotations

import logging

import pytest

from anki_miner.gui.capabilities import CapabilityTarget
from anki_miner.gui.controllers.task_registry import TaskOutcome, TaskRegistry, TaskSpec

LOGGER_NAME = "anki_miner.gui.controllers.task_registry"


@pytest.fixture
def registry(qtbot):
    reg = TaskRegistry()
    yield reg
    reg.shutdown()


def _start(registry: TaskRegistry, *, now: float = 0.0):
    return registry.start(
        TaskSpec(task_id="run.single", title="Single episode", owner=CapabilityTarget("video", "single")),
        now=now,
    )


def _stall_lines(caplog) -> list[str]:
    return [
        r.getMessage() for r in caplog.records if r.name == LOGGER_NAME and r.getMessage().startswith("Task stalled")
    ]


def test_a_long_review_never_logs_a_stall(registry, caplog):
    handle = _start(registry)
    handle.set_awaiting_user(True, now=1.0)

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        registry.tick(now=500.0)

    assert _stall_lines(caplog) == []
    snapshot = registry.snapshot("run.single")
    assert snapshot is not None
    assert snapshot.no_update_age_s == 0.0


def test_the_silence_clock_restarts_when_the_review_ends(registry):
    handle = _start(registry)
    handle.set_awaiting_user(True, now=1.0)
    registry.tick(now=200.0)

    handle.set_awaiting_user(False, now=200.0)
    registry.tick(now=230.0)

    snapshot = registry.snapshot("run.single")
    assert snapshot is not None
    assert snapshot.no_update_age_s == pytest.approx(30.0)


def test_a_silent_run_without_a_review_still_stalls(registry, caplog):
    _start(registry)

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        registry.tick(now=61.0)

    assert len(_stall_lines(caplog)) == 1


def test_a_new_run_does_not_inherit_the_review_flag(registry, caplog):
    first = _start(registry)
    first.set_awaiting_user(True, now=1.0)
    first.finish(TaskOutcome.CANCELLED, now=2.0)

    _start(registry, now=3.0)
    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        registry.tick(now=70.0)

    assert len(_stall_lines(caplog)) == 1


def test_a_finished_handle_cannot_set_the_flag(registry):
    handle = _start(registry)
    handle.finish(TaskOutcome.SUCCEEDED, now=1.0)

    handle.set_awaiting_user(True, now=2.0)

    assert "run.single" not in registry._awaiting_user
