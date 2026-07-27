"""Tests for the queue-local current-job strip (D31).

The rows went calm, so the one item actually being mined needs somewhere to
say what it is doing. The strip is that place. It renders ``TaskSnapshot``s
from the ``TaskRegistry`` and owns no worker, no timer and no numbers of its
own -- and it is bound to one exact run, so a second job elsewhere in the app
cannot rename the queue's line.
"""

from __future__ import annotations

import pytest

from anki_miner.gui.capabilities import CapabilityTarget
from anki_miner.gui.controllers.task_registry import TaskOutcome, TaskRegistry, TaskSpec
from anki_miner.gui.widgets.current_job_strip import CurrentJobStrip


@pytest.fixture
def registry(qtbot):
    reg = TaskRegistry()
    yield reg
    reg.shutdown()


def _strip(qtbot) -> CurrentJobStrip:
    strip = CurrentJobStrip()
    qtbot.addWidget(strip)
    return strip


def _start(registry: TaskRegistry, task_id: str = "queue.youtube", *, now: float = 0.0):
    return registry.start(
        TaskSpec(
            task_id=task_id,
            title="YouTube queue",
            owner=CapabilityTarget("video", "youtube"),
        ),
        now=now,
    )


# ---------------------------------------------------------------------------
# No job
# ---------------------------------------------------------------------------


def test_strip_is_hidden_before_any_run(qtbot) -> None:
    strip = _strip(qtbot)
    assert strip.isVisibleTo(strip.parentWidget()) is False


def test_strip_collapses_when_the_run_is_released(qtbot, registry) -> None:
    strip = _strip(qtbot)
    handle = _start(registry)
    strip.bind(registry, handle.task_id, handle.run_token)
    handle.stage(index=1, total=3, name="Downloading", now=1.0)

    strip.unbind()

    assert strip.line_label.full_text == ""
    assert strip.isVisibleTo(strip.parentWidget()) is False


# ---------------------------------------------------------------------------
# Rendering the bound run
# ---------------------------------------------------------------------------


def test_binding_renders_the_running_task(qtbot, registry) -> None:
    """Before the run says anything specific, its name is what there is to say."""
    strip = _strip(qtbot)
    handle = _start(registry)

    strip.bind(registry, handle.task_id, handle.run_token)

    assert "YouTube queue" in strip.line_label.full_text


def test_the_title_gives_way_to_what_the_run_is_doing(qtbot, registry) -> None:
    """The card above already names the queue; the strip spends its width on the item."""
    strip = _strip(qtbot)
    handle = _start(registry)
    strip.bind(registry, handle.task_id, handle.run_token)

    handle.count(current=1, total=3, detail="Episode 2", now=1.0)

    assert "Episode 2" in strip.line_label.full_text
    assert "YouTube queue" not in strip.line_label.full_text


def test_stage_reaches_the_strip(qtbot, registry) -> None:
    strip = _strip(qtbot)
    handle = _start(registry)
    strip.bind(registry, handle.task_id, handle.run_token)

    handle.stage(index=2, total=5, name="Extracting media", now=4.0)

    text = strip.line_label.full_text
    assert "Extracting media" in text
    assert "2 of 5" in text


def test_counts_render_as_a_real_fraction(qtbot, registry) -> None:
    strip = _strip(qtbot)
    handle = _start(registry)
    strip.bind(registry, handle.task_id, handle.run_token)

    handle.count(current=3, total=10, detail="Episode 4", now=2.0)

    text = strip.line_label.full_text
    assert "Episode 4" in text
    assert "3 / 10" in text


def test_elapsed_clock_is_shown(qtbot, registry) -> None:
    strip = _strip(qtbot)
    handle = _start(registry, now=0.0)
    strip.bind(registry, handle.task_id, handle.run_token)

    handle.count(current=1, total=2, detail="Episode 1", now=95.0)

    assert "01:35" in strip.line_label.full_text


def test_a_finished_run_collapses_the_strip(qtbot, registry) -> None:
    strip = _strip(qtbot)
    handle = _start(registry)
    strip.bind(registry, handle.task_id, handle.run_token)

    handle.finish(TaskOutcome.SUCCEEDED, now=3.0)

    assert strip.isVisibleTo(strip.parentWidget()) is False


# ---------------------------------------------------------------------------
# Run-token discipline
# ---------------------------------------------------------------------------


def test_a_newer_run_of_the_same_task_is_ignored(qtbot, registry) -> None:
    """A later run of the same id is a different run; the strip does not adopt it."""
    strip = _strip(qtbot)
    first = _start(registry)
    strip.bind(registry, first.task_id, first.run_token)
    first.count(current=1, total=2, detail="Episode 1", now=1.0)

    second = _start(registry)
    second.count(current=1, total=9, detail="Something else", now=2.0)

    assert "Something else" not in strip.line_label.full_text
    assert strip.isVisibleTo(strip.parentWidget()) is False


def test_another_task_cannot_replace_the_queue_line(qtbot, registry) -> None:
    """A background download running at the same time is a different task."""
    strip = _strip(qtbot)
    handle = _start(registry)
    strip.bind(registry, handle.task_id, handle.run_token)
    handle.count(current=1, total=2, detail="Episode 1", now=1.0)
    rendered = strip.line_label.full_text

    other = registry.start(
        TaskSpec(task_id="download.dict", title="Dictionary", owner=CapabilityTarget("settings", "dictionaries")),
        now=1.0,
    )
    other.count(current=5, total=5, detail="Indexing", now=2.0)

    assert strip.line_label.full_text == rendered


def test_rebinding_to_a_second_run_switches_the_line(qtbot, registry) -> None:
    strip = _strip(qtbot)
    first = _start(registry)
    strip.bind(registry, first.task_id, first.run_token)

    second = _start(registry)
    strip.bind(registry, second.task_id, second.run_token)
    second.count(current=1, total=9, detail="Something else", now=2.0)

    assert "Something else" in strip.line_label.full_text


def test_strip_owns_no_worker(qtbot) -> None:
    """The strip observes. It must expose no way to stop or start anything."""
    strip = _strip(qtbot)

    assert not hasattr(strip, "worker_thread")
    assert not hasattr(strip, "cancel")
