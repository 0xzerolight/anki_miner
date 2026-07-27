"""The task registry: one truth about what the app is currently doing.

Every progress surface renders a ``TaskSnapshot`` and none of them keeps its own
copy of the state, so the per-screen panel, the status strip and the mini monitor
cannot disagree about the same run.

Three properties the accepted decisions turn on:
  D17 -- the elapsed clock keeps moving during producer silence, and the app
         reports the *observed* silence rather than asserting the worker is alive.
  D18 -- there is no synthetic overall percentage; a fraction exists only when a
         real denominator does.
  D20/D22 -- a terminal outcome is durable, and cancelling never discards the
         counts a partial receipt needs.
"""

import pytest

from anki_miner.gui.capabilities import CapabilityTarget
from anki_miner.gui.controllers.task_registry import (
    TaskOutcome,
    TaskRegistry,
    TaskSpec,
)


@pytest.fixture
def registry(qtbot):
    reg = TaskRegistry()
    yield reg
    reg.shutdown()


def _spec(task_id="t1", title="Mining Samurai Champloo") -> TaskSpec:
    return TaskSpec(task_id=task_id, title=title, owner=CapabilityTarget("video", "single"))


class TestLifecycle:
    def test_starting_publishes_a_snapshot(self, registry):
        registry.start(_spec(), now=0.0)

        snap = registry.snapshot("t1")
        assert snap is not None
        assert snap.title == "Mining Samurai Champloo"
        assert snap.is_running

    def test_unknown_task_has_no_snapshot(self, registry):
        assert registry.snapshot("nope") is None

    def test_finishing_keeps_the_snapshot_for_the_receipt(self, registry):
        """D20: the counts must survive the run so a receipt can render them."""
        handle = registry.start(_spec(), now=0.0)
        handle.count(current=486, total=486, detail="notes added", now=5.0)

        handle.finish(TaskOutcome.SUCCEEDED, now=5.0)

        snap = registry.snapshot("t1")
        assert snap is not None
        assert not snap.is_running
        assert snap.outcome is TaskOutcome.SUCCEEDED
        assert snap.current == 486

    def test_cancelling_preserves_the_counts(self, registry):
        """D22: a partial receipt needs what actually happened before the stop."""
        handle = registry.start(_spec(), now=0.0)
        handle.count(current=84, total=486, detail="notes added", now=5.0)

        handle.finish(TaskOutcome.CANCELLED, now=6.0)

        snap = registry.snapshot("t1")
        assert snap is not None
        assert snap.outcome is TaskOutcome.CANCELLED
        assert snap.current == 84

    def test_running_tasks_are_listed_in_start_order(self, registry):
        registry.start(_spec("a", "First"), now=0.0)
        registry.start(_spec("b", "Second"), now=0.0)

        assert [s.task_id for s in registry.running()] == ["a", "b"]

    def test_finished_tasks_leave_the_running_list(self, registry):
        handle = registry.start(_spec(), now=0.0)
        handle.finish(TaskOutcome.SUCCEEDED, now=1.0)

        assert registry.running() == ()


class TestRunToken:
    def test_each_run_of_a_task_id_gets_a_new_token(self, registry):
        first = registry.start(_spec(), now=0.0)
        first.finish(TaskOutcome.SUCCEEDED, now=1.0)

        second = registry.start(_spec(), now=2.0)

        assert second.run_token != first.run_token

    def test_a_stale_handle_cannot_write_over_a_newer_run(self, registry):
        """The defect this prevents: a late signal from a finished run
        overwriting the run the user is actually watching."""
        stale = registry.start(_spec(), now=0.0)
        stale.finish(TaskOutcome.SUCCEEDED, now=1.0)
        registry.start(_spec(), now=2.0)

        stale.count(current=999, total=999, detail="from the old run", now=3.0)

        snap = registry.snapshot("t1")
        assert snap is not None
        assert snap.current == 0

    def test_a_view_can_detect_a_stale_snapshot(self, registry):
        handle = registry.start(_spec(), now=0.0)
        token = handle.run_token
        handle.finish(TaskOutcome.SUCCEEDED, now=1.0)
        registry.start(_spec(), now=2.0)

        snap = registry.snapshot("t1")
        assert snap is not None
        assert snap.run_token != token


class TestStagesAndCounts:
    def test_stage_is_reported_as_real_position(self, registry):
        """D18: 'Stage 3 of 5', never a weighted percentage."""
        handle = registry.start(_spec(), now=0.0)

        handle.stage(index=3, total=5, name="Extracting media", now=1.0)

        snap = registry.snapshot("t1")
        assert snap is not None
        assert (snap.stage_index, snap.stage_total, snap.stage_name) == (3, 5, "Extracting media")

    def test_no_fraction_without_a_denominator(self, registry):
        handle = registry.start(_spec(), now=0.0)
        handle.count(current=7, total=None, detail="lines parsed", now=1.0)

        snap = registry.snapshot("t1")
        assert snap is not None
        assert snap.fraction is None

    def test_fraction_exists_when_the_denominator_is_real(self, registry):
        handle = registry.start(_spec(), now=0.0)
        handle.count(current=7, total=24, detail="episodes", now=1.0)

        snap = registry.snapshot("t1")
        assert snap is not None
        assert snap.fraction == pytest.approx(7 / 24)


class TestSilenceAndElapsed:
    def test_elapsed_advances_without_any_producer_update(self, registry):
        """D17: the clock is UI-owned, so silence does not freeze it."""
        registry.start(_spec(), now=0.0)

        registry.tick(now=12.0)

        snap = registry.snapshot("t1")
        assert snap is not None
        assert snap.elapsed_s == 12.0

    def test_reports_observed_silence(self, registry):
        handle = registry.start(_spec(), now=0.0)
        handle.count(current=1, total=10, detail="", now=2.0)

        registry.tick(now=20.0)

        snap = registry.snapshot("t1")
        assert snap is not None
        assert snap.no_update_age_s == 18.0

    def test_an_update_clears_the_silence(self, registry):
        handle = registry.start(_spec(), now=0.0)
        handle.count(current=1, total=10, detail="", now=2.0)
        registry.tick(now=20.0)

        handle.count(current=2, total=10, detail="", now=21.0)

        snap = registry.snapshot("t1")
        assert snap is not None
        assert snap.no_update_age_s == 0.0

    def test_a_finished_task_stops_accumulating_elapsed(self, registry):
        handle = registry.start(_spec(), now=0.0)
        handle.finish(TaskOutcome.SUCCEEDED, now=10.0)

        registry.tick(now=999.0)

        snap = registry.snapshot("t1")
        assert snap is not None
        assert snap.elapsed_s == 10.0


class TestCancelling:
    """D22: Cancel is one verb with an honest waiting state, and no prompt.

    Between the click and the worker actually stopping, the app must stop
    claiming progress it can no longer vouch for, keep the clock running so the
    wait is visibly a wait rather than a hang, and after a couple of seconds say
    what it is waiting for.
    """

    def test_cancelling_is_still_running(self, registry):
        handle = registry.start(_spec(), now=0.0)

        handle.cancelling(now=1.0)

        snap = registry.snapshot("t1")
        assert snap is not None
        assert snap.cancelling
        assert snap.is_running  # not terminal until the worker actually stops

    def test_the_bar_freezes_at_its_last_true_value(self, registry):
        handle = registry.start(_spec(), now=0.0)
        handle.count(current=3, total=12, detail="Episode 3", now=1.0)

        handle.cancelling(now=2.0)
        handle.count(current=9, total=12, detail="Episode 9", now=3.0)

        snap = registry.snapshot("t1")
        assert snap is not None
        assert (snap.current, snap.total) == (3, 12)

    def test_the_stage_position_freezes_too(self, registry):
        handle = registry.start(_spec(), now=0.0)
        handle.stage(index=3, total=5, name="Extracting media", now=1.0)

        handle.cancelling(now=2.0)
        handle.stage(index=5, total=5, name="Creating Anki cards", now=3.0)

        snap = registry.snapshot("t1")
        assert snap is not None
        assert (snap.stage_index, snap.stage_total) == (3, 5)

    def test_phase_text_still_gets_through(self, registry):
        """Words are not a claim about position, so they stay live."""
        handle = registry.start(_spec(), now=0.0)
        handle.cancelling(now=1.0)

        handle.count(current=99, total=99, detail="Waiting for Anki", now=2.0)

        snap = registry.snapshot("t1")
        assert snap is not None
        assert snap.detail == "Waiting for Anki"

    def test_the_clock_keeps_running(self, registry):
        handle = registry.start(_spec(), now=0.0)
        handle.cancelling(now=2.0)

        registry.tick(now=9.0)

        snap = registry.snapshot("t1")
        assert snap is not None
        assert snap.elapsed_s == 9.0

    def test_it_reports_how_long_the_cancel_has_been_waiting(self, registry):
        handle = registry.start(_spec(), now=0.0)
        handle.cancelling(now=2.0)

        registry.tick(now=5.0)

        snap = registry.snapshot("t1")
        assert snap is not None
        assert snap.cancelling_age_s == 3.0

    def test_a_run_that_was_never_cancelled_has_no_cancel_age(self, registry):
        registry.start(_spec(), now=0.0)

        registry.tick(now=5.0)

        snap = registry.snapshot("t1")
        assert snap is not None
        assert not snap.cancelling
        assert snap.cancelling_age_s == 0.0

    def test_cancelling_twice_does_not_restart_the_wait(self, registry):
        handle = registry.start(_spec(), now=0.0)
        handle.cancelling(now=2.0)

        handle.cancelling(now=6.0)

        snap = registry.snapshot("t1")
        assert snap is not None
        assert snap.cancelling_age_s == 4.0

    def test_a_stale_handle_cannot_start_a_cancel(self, registry):
        stale = registry.start(_spec(), now=0.0)
        stale.finish(TaskOutcome.CANCELLED, now=1.0)
        registry.start(_spec(), now=2.0)

        stale.cancelling(now=3.0)

        snap = registry.snapshot("t1")
        assert snap is not None
        assert not snap.cancelling

    def test_the_partial_counts_survive_into_the_terminal_state(self, registry):
        handle = registry.start(_spec(), now=0.0)
        handle.count(current=3, total=12, detail="Episode 3", now=1.0)
        handle.cancelling(now=2.0)

        handle.finish(TaskOutcome.CANCELLED, now=4.0)

        snap = registry.snapshot("t1")
        assert snap is not None
        assert (snap.current, snap.total) == (3, 12)
        assert snap.outcome is TaskOutcome.CANCELLED


class TestChangeSignal:
    def test_emits_on_start(self, registry, qtbot):
        with qtbot.waitSignal(registry.snapshot_changed, timeout=1000) as blocker:
            registry.start(_spec(), now=0.0)

        assert blocker.args == ["t1"]

    def test_emits_on_update(self, registry, qtbot):
        handle = registry.start(_spec(), now=0.0)

        with qtbot.waitSignal(registry.snapshot_changed, timeout=1000) as blocker:
            handle.count(current=1, total=10, detail="", now=1.0)

        assert blocker.args == ["t1"]

    def test_emits_on_finish(self, registry, qtbot):
        handle = registry.start(_spec(), now=0.0)

        with qtbot.waitSignal(registry.snapshot_changed, timeout=1000) as blocker:
            handle.finish(TaskOutcome.SUCCEEDED, now=1.0)

        assert blocker.args == ["t1"]

    def test_a_stale_handle_emits_nothing(self, registry, qtbot):
        stale = registry.start(_spec(), now=0.0)
        stale.finish(TaskOutcome.SUCCEEDED, now=1.0)
        registry.start(_spec(), now=2.0)

        with qtbot.assertNotEmitted(registry.snapshot_changed):
            stale.count(current=999, total=999, detail="", now=3.0)


class TestOwnership:
    def test_the_registry_owns_no_worker(self, registry):
        """It is a state store. Worker lifetime stays with the tab that spawned it."""
        handle = registry.start(_spec(), now=0.0)

        assert not hasattr(handle, "worker")
        assert not hasattr(handle, "thread")

    def test_a_task_carries_where_it_came_from(self, registry):
        """So the status strip can navigate to the screen that owns the run."""
        registry.start(_spec(), now=0.0)

        snap = registry.snapshot("t1")
        assert snap is not None
        assert snap.owner == CapabilityTarget("video", "single")


class TestCancelRequests:
    """``request_cancel`` relays. It never touches the run itself."""

    def test_a_request_reaches_whoever_owns_the_run(self, registry, qtbot):
        registry.start(_spec(), now=0.0)

        with qtbot.waitSignal(registry.cancel_requested, timeout=1000) as blocker:
            registry.request_cancel("t1")

        assert blocker.args == ["t1"]

    def test_a_request_does_not_mark_the_run_cancelling(self, registry):
        """Only the screen holding the cancellation event can say the ask landed.

        A registry that set the flag here would paint every surface as
        cancelling even when nothing was listening.
        """
        registry.start(_spec(), now=0.0)

        registry.request_cancel("t1")

        snap = registry.snapshot("t1")
        assert snap is not None
        assert snap.cancelling is False
        assert snap.is_running is True

    def test_an_unknown_run_is_ignored(self, registry, qtbot):
        with qtbot.assertNotEmitted(registry.cancel_requested):
            registry.request_cancel("nope")

    def test_a_finished_run_is_ignored(self, registry, qtbot):
        handle = registry.start(_spec(), now=0.0)
        handle.finish(TaskOutcome.SUCCEEDED, now=1.0)

        with qtbot.assertNotEmitted(registry.cancel_requested):
            registry.request_cancel("t1")

    def test_a_run_declared_uncancellable_is_ignored(self, registry, qtbot):
        registry.start(
            TaskSpec(
                task_id="t1",
                title="Activating",
                owner=CapabilityTarget("video", "single"),
                cancellable=False,
            ),
            now=0.0,
        )

        with qtbot.assertNotEmitted(registry.cancel_requested):
            registry.request_cancel("t1")

    def test_the_registry_still_owns_no_cancellation(self, registry):
        registry.start(_spec(), now=0.0)

        assert not hasattr(registry, "cancel")
        assert not hasattr(registry, "_cancel_event")
