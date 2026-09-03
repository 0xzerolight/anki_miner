"""The task registry narrates its own lifecycle, stalls and rejected writes.

These lines are the progress-ownership half of a diagnosis. ``Task`` lines say
what the registry believed about a run; the ``Run`` lines a mining tab emits say
what the mining pipeline believed. A log that carries ``Task stalled`` and never
``Task end`` is the zombie run; one where ``Task cancel ignored`` follows every
click is the "Cancel does nothing" report.
"""

from __future__ import annotations

import logging

import pytest

from anki_miner.gui.capabilities import CapabilityTarget
from anki_miner.gui.controllers.task_registry import (
    STALL_WARN_S,
    TaskOutcome,
    TaskRegistry,
    TaskSpec,
)

LOGGER_NAME = "anki_miner.gui.controllers.task_registry"


@pytest.fixture
def registry(qtbot):
    reg = TaskRegistry()
    yield reg
    reg.shutdown()


def _spec(task_id: str = "t1", *, cancellable: bool = True) -> TaskSpec:
    return TaskSpec(
        task_id=task_id,
        title="Mining Samurai Champloo",
        owner=CapabilityTarget("video", "single"),
        cancellable=cancellable,
    )


def _messages(caplog, prefix: str) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == LOGGER_NAME and r.getMessage().startswith(prefix)]


class TestLifecycleLines:
    def test_start_records_the_run_identity(self, registry, caplog):
        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            registry.start(_spec(), now=0.0)

        lines = _messages(caplog, "Task start:")
        assert len(lines) == 1
        assert "id=t1" in lines[0]
        assert 'title="Mining Samurai Champloo"' in lines[0]
        assert "owner=video/single" in lines[0]
        assert "token=1" in lines[0]

    def test_end_carries_the_outcome_and_the_counts_it_reached(self, registry, caplog):
        handle = registry.start(_spec(), now=0.0)
        handle.stage(index=3, total=5, name="Extracting media", now=1.0)
        handle.count(current=12, total=40, detail="notes", now=2.0)

        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            handle.finish(TaskOutcome.FAILED, now=7.5)

        lines = _messages(caplog, "Task end:")
        assert len(lines) == 1
        assert "id=t1" in lines[0]
        assert "token=1" in lines[0]
        assert "outcome=failed" in lines[0]
        assert "elapsed_s=7.5" in lines[0]
        assert "current=12" in lines[0]
        assert "total=40" in lines[0]
        assert 'stage="3/5 Extracting media"' in lines[0]

    def test_cancelling_is_recorded_when_the_owner_reports_back(self, registry, caplog):
        handle = registry.start(_spec(), now=0.0)

        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            handle.cancelling(now=4.0)

        lines = _messages(caplog, "Task cancelling:")
        assert len(lines) == 1
        assert "id=t1" in lines[0]
        assert "elapsed_s=4" in lines[0]

    def test_shutdown_names_what_was_still_running(self, registry, caplog):
        registry.start(_spec("t1"), now=0.0)
        registry.start(_spec("t2"), now=0.0)

        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            registry.shutdown()

        lines = _messages(caplog, "Task registry shutdown:")
        assert len(lines) == 1
        assert "running=t1,t2" in lines[0]


class TestStallNarration:
    def test_one_warning_per_crossing_not_per_tick(self, registry, caplog):
        """The 10 h zombie warned on every tick would bury the log it lives in."""
        registry.start(_spec(), now=0.0)

        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            registry.tick(now=30.0)
            registry.tick(now=61.0)
            registry.tick(now=90.0)

        lines = _messages(caplog, "Task stalled:")
        assert len(lines) == 1
        assert "id=t1" in lines[0]
        assert "no_update_s=61" in lines[0]
        stalls = [r for r in caplog.records if r.getMessage().startswith("Task stalled:")]
        assert [r.levelno for r in stalls] == [logging.WARNING]

    def test_progress_unstalls_and_a_later_silence_stalls_again(self, registry, caplog):
        handle = registry.start(_spec(), now=0.0)
        registry.tick(now=61.0)

        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            handle.count(current=1, total=None, detail="moving", now=95.0)
            registry.tick(now=170.0)

        unstalled = _messages(caplog, "Task unstalled:")
        assert len(unstalled) == 1
        assert "id=t1" in unstalled[0]
        assert "after_s=95" in unstalled[0]

        # The first crossing, then the second: silence is measured from the
        # update at 95, so the re-stall reports 75s rather than the 170s clock.
        stalls = _messages(caplog, "Task stalled:")
        assert len(stalls) == 2
        assert "no_update_s=75" in stalls[1]
        assert "elapsed_s=170" in stalls[1]

    def test_finishing_a_stalled_run_clears_the_stall(self, registry, caplog):
        handle = registry.start(_spec(), now=0.0)
        registry.tick(now=61.0)

        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            handle.finish(TaskOutcome.SUCCEEDED, now=70.0)

        assert len(_messages(caplog, "Task unstalled:")) == 1
        assert len(_messages(caplog, "Task end:")) == 1

    def test_the_threshold_is_the_published_constant(self, registry, caplog):
        registry.start(_spec(), now=0.0)

        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            registry.tick(now=STALL_WARN_S - 0.5)
            assert _messages(caplog, "Task stalled:") == []
            registry.tick(now=STALL_WARN_S)

        assert len(_messages(caplog, "Task stalled:")) == 1


class TestRefusals:
    def test_cancelling_an_unknown_task_says_so(self, registry, caplog):
        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            registry.request_cancel("nope")

        lines = _messages(caplog, "Task cancel ignored:")
        assert len(lines) == 1
        assert "id=nope" in lines[0]
        assert "reason=unknown" in lines[0]

    def test_cancelling_a_finished_task_says_so(self, registry, caplog):
        handle = registry.start(_spec(), now=0.0)
        handle.finish(TaskOutcome.SUCCEEDED, now=1.0)

        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            registry.request_cancel("t1")

        assert "reason=not_running" in _messages(caplog, "Task cancel ignored:")[0]

    def test_cancelling_a_non_cancellable_task_says_so(self, registry, caplog):
        registry.start(_spec(cancellable=False), now=0.0)

        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            registry.request_cancel("t1")

        assert "reason=not_cancellable" in _messages(caplog, "Task cancel ignored:")[0]

    def test_a_relayed_cancel_is_recorded(self, registry, caplog):
        registry.start(_spec(), now=0.0)

        with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
            registry.request_cancel("t1")

        assert _messages(caplog, "Task cancel ignored:") == []
        assert len(_messages(caplog, "Task cancel requested:")) == 1


class TestDroppedWrites:
    def test_a_superseded_handle_is_reported_once(self, registry, caplog):
        stale = registry.start(_spec(), now=0.0)
        registry.start(_spec(), now=1.0)

        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            stale.count(current=1, total=2, detail="late", now=2.0)
            stale.count(current=2, total=2, detail="later", now=3.0)

        lines = _messages(caplog, "Task write dropped:")
        assert len(lines) == 1
        assert "id=t1" in lines[0]
        assert "token=1" in lines[0]
        assert "live_token=2" in lines[0]
        assert "reason=superseded" in lines[0]
        assert "fields=current,total,detail" in lines[0]

    def test_each_stale_token_gets_its_own_line(self, registry, caplog):
        first = registry.start(_spec(), now=0.0)
        second = registry.start(_spec(), now=1.0)
        registry.start(_spec(), now=2.0)

        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            first.count(current=1, total=2, detail="late", now=3.0)
            second.count(current=1, total=2, detail="late", now=3.0)

        assert len(_messages(caplog, "Task write dropped:")) == 2

    def test_writing_to_a_finished_run_is_reported(self, registry, caplog):
        handle = registry.start(_spec(), now=0.0)
        handle.finish(TaskOutcome.SUCCEEDED, now=1.0)

        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            handle.count(current=1, total=2, detail="late", now=2.0)

        assert "reason=finished" in _messages(caplog, "Task write dropped:")[0]

    def test_drops_are_debug_not_warnings(self, registry, caplog):
        stale = registry.start(_spec(), now=0.0)
        registry.start(_spec(), now=1.0)

        with caplog.at_level(logging.DEBUG, logger=LOGGER_NAME):
            stale.count(current=1, total=2, detail="late", now=2.0)

        dropped = [r for r in caplog.records if r.getMessage().startswith("Task write dropped:")]
        assert [r.levelno for r in dropped] == [logging.DEBUG]
