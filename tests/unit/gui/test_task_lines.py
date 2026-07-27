"""One snapshot, one sentence, wherever it is shown.

Three surfaces now quote the same run: the strip above a queue, the status bar,
and the mini job monitor. A third independent renderer is exactly the drift
D14-B was chosen to prevent, so the sentences live in one module and the widgets
only place them. These tests pin the two shapes and pin the widgets to them.
"""

from __future__ import annotations

import pytest

from anki_miner.gui.capabilities import CapabilityTarget
from anki_miner.gui.controllers.task_registry import TaskRegistry, TaskSpec
from anki_miner.gui.utils.task_lines import format_task_line, format_task_summary
from anki_miner.gui.widgets.current_job_strip import CurrentJobStrip
from anki_miner.gui.widgets.mini_job_monitor import MiniJobMonitor
from anki_miner.gui.widgets.status_bar_widget import StatusBarWidget

_OWNER = CapabilityTarget("video", "youtube")


@pytest.fixture
def registry(qtbot):
    reg = TaskRegistry()
    yield reg
    reg.shutdown()


def _running(registry):
    handle = registry.start(TaskSpec("queue.youtube", "Samurai Champloo", _OWNER), now=0.0)
    handle.stage(index=3, total=5, name="Fetching definitions", now=1.0)
    handle.count(current=7, total=24, detail="18 cards", now=78.0)
    return handle


class TestTheDetailedLine:
    def test_it_names_stage_detail_position_and_clock(self, registry):
        _running(registry)

        line = format_task_line(registry.snapshot("queue.youtube"))

        assert line == "Fetching definitions (3 of 5) · 18 cards · 7 / 24 · 01:18"

    def test_it_falls_back_to_the_title_before_anything_is_reported(self, registry):
        registry.start(TaskSpec("queue.youtube", "Samurai Champloo", _OWNER), now=0.0)

        assert format_task_line(registry.snapshot("queue.youtube")) == "Samurai Champloo · 00:00"

    def test_no_denominator_prints_no_position(self, registry):
        handle = registry.start(TaskSpec("queue.youtube", "Samurai Champloo", _OWNER), now=0.0)
        handle.count(current=18, total=None, detail="18 cards", now=5.0)

        assert format_task_line(registry.snapshot("queue.youtube")) == "18 cards · 00:05"


class TestTheCompactLine:
    def test_a_real_denominator_becomes_a_percentage(self, registry):
        _running(registry)

        assert format_task_summary(registry.snapshot("queue.youtube")) == "Samurai Champloo 29%"

    def test_no_denominator_names_the_phase_instead(self, registry):
        handle = registry.start(TaskSpec("queue.youtube", "Samurai Champloo", _OWNER), now=0.0)
        handle.stage(index=1, total=3, name="Downloading", now=1.0)

        summary = format_task_summary(registry.snapshot("queue.youtube"))
        assert summary == "Samurai Champloo · Downloading"

    def test_a_cancelling_run_drops_the_percentage(self, registry):
        handle = _running(registry)
        handle.cancelling(now=80.0)

        summary = format_task_summary(registry.snapshot("queue.youtube"))
        assert "%" not in summary
        assert "Cancelling" in summary


class TestEverySurfaceUsesThem:
    def test_the_queue_strip_places_the_detailed_line(self, qtbot, registry):
        strip = CurrentJobStrip()
        qtbot.addWidget(strip)
        handle = _running(registry)
        strip.bind(registry, handle.task_id, handle.run_token)

        assert strip.line_label.full_text == format_task_line(registry.snapshot("queue.youtube"))

    def test_the_monitor_places_the_detailed_line(self, qtbot, registry):
        monitor = MiniJobMonitor(registry)
        qtbot.addWidget(monitor)
        _running(registry)

        assert monitor.line_label.full_text == format_task_line(registry.snapshot("queue.youtube"))

    def test_the_status_strip_places_the_compact_line(self, qtbot, registry):
        bar = StatusBarWidget()
        qtbot.addWidget(bar)
        bar.bind_task_registry(registry)
        _running(registry)

        assert format_task_summary(registry.snapshot("queue.youtube")) in bar.task_button.text()
