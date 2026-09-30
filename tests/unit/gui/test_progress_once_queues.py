"""D1 on the list queues: no Progress card, bar = phase, strip = run line."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QFrame, QLabel

from anki_miner.gui.capabilities import CapabilityTarget
from anki_miner.gui.controllers.task_registry import TaskRegistry, TaskSpec
from anki_miner.gui.widgets.current_job_strip import CurrentJobStrip
from tests.unit.gui._screens import add_audiobook


@pytest.fixture
def registry(qtbot):
    reg = TaskRegistry()
    yield reg
    reg.shutdown()


@pytest.mark.parametrize("fixture", ["audiobook_tab", "queue_youtube_tab"])
def test_no_progress_card(request, fixture):
    tab = request.getfixturevalue(fixture)
    assert tab.progress_widget.isHidden()
    assert not isinstance(tab.progress_widget.parentWidget(), QFrame) or (
        tab.progress_widget.parentWidget().objectName() != "card"
    )
    assert "Progress" not in [label.text() for label in tab.findChildren(QLabel)]


def test_the_pinned_bar_shows_the_phase_only(audiobook_tab, registry, tmp_path):
    tab = audiobook_tab
    tab.bind_task_registry(registry)
    add_audiobook(tab, tmp_path, "book1")
    add_audiobook(tab, tmp_path, "book2")
    tab._start_run()
    tab._on_item_started(0)

    tab._on_item_progress(0, "Extracting media (3 of 5)")

    snapshot = registry.snapshot(tab.TASK_ID)
    assert snapshot is not None
    assert snapshot.detail == "Extracting media (3 of 5)"
    assert tab.action_bar.stage_label.full_text == "Extracting media (3 of 5)"


def test_the_strip_keeps_the_run_line(qtbot, registry):
    strip = CurrentJobStrip()
    qtbot.addWidget(strip)
    handle = registry.start(
        TaskSpec(task_id="queue.audiobook", title="Audiobook mining", owner=CapabilityTarget("audiobook")),
        now=0.0,
    )
    strip.bind(registry, handle.task_id, handle.run_token)

    handle.count(current=2, total=12, detail="Extracting media (3 of 5)", now=65.0)

    assert strip.line_label.full_text == "3 of 12 · 2 done · Elapsed 01:05"


def test_the_strip_never_counts_past_the_end(qtbot, registry):
    strip = CurrentJobStrip()
    qtbot.addWidget(strip)
    handle = registry.start(
        TaskSpec(task_id="queue.audiobook", title="Audiobook mining", owner=CapabilityTarget("audiobook")),
        now=0.0,
    )
    strip.bind(registry, handle.task_id, handle.run_token)

    handle.count(current=12, total=12, detail="", now=5.0)

    assert strip.line_label.full_text.startswith("12 of 12 · 12 done")
