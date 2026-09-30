"""The pinned bar can keep a tool's last result line (D1, tool-screen half is WS4)."""

from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QPushButton

from anki_miner.gui.capabilities import CapabilityTarget
from anki_miner.gui.controllers.task_registry import TaskOutcome, TaskRegistry, TaskSpec
from anki_miner.gui.widgets.base import WorkflowActionBar


@pytest.fixture
def registry(qtbot):
    reg = TaskRegistry()
    yield reg
    reg.shutdown()


def _bar(qtbot) -> WorkflowActionBar:
    bar = WorkflowActionBar()
    qtbot.addWidget(bar)
    return bar


def _start(registry: TaskRegistry, *, now: float = 0.0):
    return registry.start(
        TaskSpec(task_id="tool.demo", title="Demo tool", owner=CapabilityTarget("subtitles", "condense")),
        now=now,
    )


def test_current_secondary_returns_the_buttons_in_order(qtbot):
    bar = _bar(qtbot)
    primary, first, second = QPushButton("Run"), QPushButton("A"), QPushButton("B")
    bar.set_actions(primary, (first, second))

    assert bar.current_secondary() == (first, second)
    assert bar.current_primary() is primary


def test_without_the_flag_an_idle_bar_stays_empty(qtbot, registry):
    bar = _bar(qtbot)
    bar.bind_task(registry, "tool.demo")
    handle = _start(registry)
    handle.finish(TaskOutcome.FAILED, now=1.0)

    bar.set_last_result("Failed — see log")

    assert bar.stage_label.full_text == ""


def test_with_the_flag_the_last_result_stays_after_the_run(qtbot, registry):
    bar = _bar(qtbot)
    bar.set_keeps_last_result(True)
    bar.bind_task(registry, "tool.demo")
    handle = _start(registry)
    handle.finish(TaskOutcome.FAILED, now=1.0)

    bar.set_last_result("Failed — see log")

    assert bar.stage_label.full_text == "Failed — see log"


def test_a_new_run_replaces_the_old_result(qtbot, registry):
    bar = _bar(qtbot)
    bar.set_keeps_last_result(True)
    bar.bind_task(registry, "tool.demo")
    first = _start(registry)
    first.finish(TaskOutcome.FAILED, now=1.0)
    bar.set_last_result("Failed — see log")

    second = _start(registry, now=2.0)
    assert bar.stage_label.full_text == "Demo tool"

    second.finish(TaskOutcome.SUCCEEDED, now=3.0)
    assert bar.stage_label.full_text == ""


def test_turning_the_flag_off_clears_the_line(qtbot, registry):
    bar = _bar(qtbot)
    bar.set_keeps_last_result(True)
    bar.set_last_result("Finished with errors — see log")
    assert bar.stage_label.full_text == "Finished with errors — see log"

    bar.set_keeps_last_result(False)

    assert bar.stage_label.full_text == ""
