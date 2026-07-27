"""The status bar names the work that is actually running (D14).

The defect: one anonymous, last-writer-wins line. Whoever called
``set_operation`` most recently owned the whole status bar, so a background
download became invisible the moment anything else wrote a message — and
completely invisible once the user navigated away from the screen that started
it. Nothing told the user how many jobs were in flight, which one the text
referred to, or where to find it.

The strip renders ``TaskRegistry`` state and nothing else. It keeps no progress
state of its own, so it cannot drift from the screen that owns the run, and it
never invents a percentage the registry could not supply.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import QThread

from anki_miner.gui.capabilities import CapabilityTarget
from anki_miner.gui.controllers.task_registry import (
    TaskOutcome,
    TaskRegistry,
    TaskSpec,
)
from anki_miner.gui.widgets.status_bar_widget import StatusBarWidget


@pytest.fixture
def registry(qtbot):
    reg = TaskRegistry()
    yield reg
    reg.shutdown()


@pytest.fixture
def bar(qtbot, registry):
    widget = StatusBarWidget()
    qtbot.addWidget(widget)
    widget.bind_task_registry(registry)
    return widget


_OWNER = CapabilityTarget("settings", "dictionaries")


def _spec(task_id="jmdict", title="Downloading JMdict", target=_OWNER):
    return TaskSpec(task_id=task_id, title=title, owner=target)


class TestTheStripNamesRunningWork:
    def test_nothing_running_shows_no_strip(self, bar):
        assert bar.task_button.isHidden()

    def test_a_running_task_names_itself(self, bar, registry):
        registry.start(_spec(), now=0.0)

        assert not bar.task_button.isHidden()
        assert "Downloading JMdict" in bar.task_button.text()

    def test_it_states_how_many_jobs_are_in_flight(self, bar, registry):
        registry.start(_spec("a", "Mining Samurai Champloo"), now=0.0)
        registry.start(_spec("b", "Downloading JMdict"), now=0.0)
        registry.start(_spec("c", "Indexing Jitendex"), now=0.0)

        assert "3 task" in bar.task_button.text()

    def test_it_shows_the_elapsed_clock(self, bar, registry):
        registry.start(_spec(), now=0.0)

        registry.tick(now=37.0)

        assert "00:37" in bar.task_button.text()

    def test_the_strip_goes_away_when_the_last_job_ends(self, bar, registry):
        handle = registry.start(_spec(), now=0.0)

        handle.finish(TaskOutcome.SUCCEEDED, now=1.0)

        assert bar.task_button.isHidden()


class TestItNeverInventsAPercentage:
    def test_a_real_denominator_becomes_a_percentage(self, bar, registry):
        handle = registry.start(_spec(), now=0.0)

        handle.count(current=17, total=100, detail="", now=1.0)

        assert "17%" in bar.task_button.text()

    def test_no_denominator_means_no_percentage(self, bar, registry):
        """fraction is None: the strip must say what it knows, not guess."""
        handle = registry.start(_spec(), now=0.0)

        handle.count(current=7, total=None, detail="lines parsed", now=1.0)

        assert "%" not in bar.task_button.text()
        assert "lines parsed" in bar.task_button.text()

    def test_a_phase_is_rendered_when_there_is_no_fraction(self, bar, registry):
        handle = registry.start(_spec(), now=0.0)

        handle.stage(index=3, total=5, name="Extracting media", now=1.0)

        assert "Extracting media" in bar.task_button.text()
        assert "%" not in bar.task_button.text()

    def test_a_cancelling_run_says_so_instead_of_a_percentage(self, bar, registry):
        """The percentage stops being true the moment the run is being abandoned."""
        handle = registry.start(_spec(), now=0.0)
        handle.count(current=17, total=100, detail="", now=1.0)

        handle.cancelling(now=2.0)

        text = bar.task_button.text()
        assert "Cancelling…" in text
        assert "17%" not in text

    def test_a_bare_task_is_still_named(self, bar, registry):
        registry.start(_spec(), now=0.0)

        assert "Downloading JMdict" in bar.task_button.text()
        assert "%" not in bar.task_button.text()


class TestTheNamedJobIsStableNotLastWriterWins:
    def test_a_second_job_does_not_steal_the_line(self, bar, registry):
        """The exact defect D14 names: whoever wrote last owned the bar."""
        registry.start(_spec("a", "Mining Samurai Champloo"), now=0.0)

        registry.start(_spec("b", "Downloading JMdict"), now=1.0)

        assert "Mining Samurai Champloo" in bar.task_button.text()
        assert "2 task" in bar.task_button.text()

    def test_an_update_to_another_job_does_not_steal_the_line(self, bar, registry):
        registry.start(_spec("a", "Mining Samurai Champloo"), now=0.0)
        other = registry.start(_spec("b", "Downloading JMdict"), now=1.0)

        other.count(current=17, total=100, detail="", now=2.0)

        assert "Mining Samurai Champloo" in bar.task_button.text()

    def test_the_line_moves_on_when_the_named_job_ends(self, bar, registry):
        named = registry.start(_spec("a", "Mining Samurai Champloo"), now=0.0)
        registry.start(_spec("b", "Downloading JMdict"), now=1.0)

        named.finish(TaskOutcome.SUCCEEDED, now=2.0)

        assert "Downloading JMdict" in bar.task_button.text()


class TestStaleRunsAreIgnored:
    def test_the_strip_tracks_the_run_it_is_displaying(self, bar, registry):
        handle = registry.start(_spec(), now=0.0)

        assert bar.displayed_run == ("jmdict", handle.run_token)

    def test_nothing_running_displays_no_run(self, bar):
        assert bar.displayed_run is None

    def test_a_restarted_task_is_a_new_run_not_a_continuation(self, bar, registry):
        """Same task_id, different run: the strip must not carry the old run's
        clock into the new one."""
        first = registry.start(_spec(), now=0.0)
        registry.tick(now=30.0)
        first.finish(TaskOutcome.FAILED, now=31.0)

        second = registry.start(_spec(), now=100.0)

        assert second.run_token != first.run_token
        assert bar.displayed_run == ("jmdict", second.run_token)
        assert "00:00" in bar.task_button.text()

    def test_a_write_from_a_superseded_run_changes_nothing(self, bar, registry):
        stale = registry.start(_spec(), now=0.0)
        stale.finish(TaskOutcome.SUCCEEDED, now=1.0)
        registry.start(_spec(), now=2.0)
        before = bar.task_button.text()

        stale.count(current=999, total=999, detail="from the old run", now=3.0)

        assert bar.task_button.text() == before
        assert "999" not in bar.task_button.text()


class TestTheMenuListsEveryRunningJob:
    def test_every_running_job_gets_an_entry(self, bar, registry):
        registry.start(_spec("a", "Mining Samurai Champloo"), now=0.0)
        registry.start(_spec("b", "Downloading JMdict"), now=0.0)

        bar.task_menu.aboutToShow.emit()

        labels = [a.text() for a in bar.task_menu.actions()]
        assert any("Mining Samurai Champloo" in label for label in labels)
        assert any("Downloading JMdict" in label for label in labels)

    def test_a_finished_job_leaves_the_menu(self, bar, registry):
        handle = registry.start(_spec("a", "Mining Samurai Champloo"), now=0.0)
        registry.start(_spec("b", "Downloading JMdict"), now=0.0)
        handle.finish(TaskOutcome.SUCCEEDED, now=1.0)

        bar.task_menu.aboutToShow.emit()

        labels = [a.text() for a in bar.task_menu.actions()]
        assert not any("Mining Samurai Champloo" in label for label in labels)

    def test_an_entry_carries_its_progress(self, bar, registry):
        handle = registry.start(_spec(), now=0.0)
        handle.count(current=17, total=100, detail="", now=1.0)

        bar.task_menu.aboutToShow.emit()

        assert any("17%" in a.text() for a in bar.task_menu.actions())


class TestTheMenuOffersTheMiniMonitor:
    def test_the_monitor_entry_sits_below_the_runs(self, bar, registry):
        registry.start(_spec("a", "Mining Samurai Champloo"), now=0.0)

        bar.task_menu.aboutToShow.emit()

        labels = [a.text() for a in bar.task_menu.actions()]
        assert labels[-1] == "Open mini monitor"

    def test_choosing_it_asks_for_the_monitor(self, bar, registry, qtbot):
        registry.start(_spec("a", "Mining Samurai Champloo"), now=0.0)
        bar.task_menu.aboutToShow.emit()
        action = bar.task_menu.actions()[-1]

        with qtbot.waitSignal(bar.mini_monitor_requested, timeout=1000):
            action.trigger()

    def test_the_strip_does_not_own_the_monitor(self, bar):
        """It emits a request. Building and showing the window is the window's."""
        assert not hasattr(bar, "_mini_job_monitor")


class TestActivationRoutesToTheOwningScreen:
    def test_choosing_a_job_asks_for_its_task(self, bar, registry, qtbot):
        registry.start(_spec("a", "Mining Samurai Champloo"), now=0.0)
        bar.task_menu.aboutToShow.emit()
        action = bar.task_menu.actions()[0]

        with qtbot.waitSignal(bar.task_activated, timeout=1000) as blocker:
            action.trigger()

        assert blocker.args == ["a"]

    def test_a_superseded_entry_navigates_nowhere(self, bar, registry, qtbot):
        """A menu can sit open while a run is replaced. Activating the entry
        must not act on a run that no longer exists."""
        handle = registry.start(_spec("a", "Mining Samurai Champloo"), now=0.0)
        bar.task_menu.aboutToShow.emit()
        action = bar.task_menu.actions()[0]
        handle.finish(TaskOutcome.SUCCEEDED, now=1.0)
        registry.start(_spec("a", "Mining Samurai Champloo"), now=2.0)

        with qtbot.assertNotEmitted(bar.task_activated):
            action.trigger()


class TestItObservesAndOwnsNothing:
    def test_the_strip_holds_no_worker(self, bar, registry):
        registry.start(_spec(), now=0.0)

        assert not any(isinstance(value, QThread) for value in vars(bar).values())
        assert not bar.findChildren(QThread)

    def test_the_strip_cannot_cancel_or_remove_a_task(self, bar):
        """Worker ownership stays with the tab that started the run."""
        assert not hasattr(bar, "cancel_task")
        assert not hasattr(bar, "request_cancel")

    def test_the_strip_stores_no_progress_of_its_own(self, bar, registry):
        handle = registry.start(_spec(), now=0.0)
        handle.count(current=17, total=100, detail="", now=1.0)

        # Identity of the displayed run only -- never its numbers.
        assert bar.displayed_run == ("jmdict", handle.run_token)
        assert not hasattr(bar, "_fraction")
        assert not hasattr(bar, "_elapsed_s")


class TestExistingBehaviourSurvives:
    def test_health_badges_still_start_unknown(self, bar):
        assert bar.anki_status_badge.property("status") == "checking"
        assert bar.ffmpeg_status_badge.property("status") == "checking"

    def test_the_operation_message_still_works(self, bar, registry):
        registry.start(_spec(), now=0.0)

        bar.set_operation("Restyle complete", "success")

        assert bar.operation_label.text() == "Restyle complete"
        assert "Downloading JMdict" in bar.task_button.text()

    def test_an_unbound_strip_is_inert(self, qtbot):
        """MainWindow constructs the bar before it binds a registry."""
        widget = StatusBarWidget()
        qtbot.addWidget(widget)

        assert widget.task_button.isHidden()
        assert widget.displayed_run is None
