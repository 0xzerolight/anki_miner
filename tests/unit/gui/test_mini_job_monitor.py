"""The mini job monitor watches a run and owns none of it (D53).

A two-hour season run had nowhere to be read from except the page that started
it. The monitor is that reading, in a window that can sit in a corner — and the
whole reason it was safe to build is that ``TaskRegistry`` already made live
work a single fact. So the tests here are mostly about what it is *not*: not a
second account of the run, not a second route to a worker, and not something
whose closing can touch work in flight.
"""

from __future__ import annotations

import threading

import pytest
from PyQt6.QtCore import QObject, Qt, QThread
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QWidget

from anki_miner.gui.capabilities import CapabilityTarget
from anki_miner.gui.controllers.task_registry import TaskOutcome, TaskRegistry, TaskSpec
from anki_miner.gui.utils.task_lines import format_task_line
from anki_miner.gui.widgets.current_job_strip import CurrentJobStrip
from anki_miner.gui.widgets.mini_job_monitor import MiniJobMonitor
from anki_miner.gui.widgets.status_bar_widget import StatusBarWidget

_OWNER = CapabilityTarget("video", "youtube")


@pytest.fixture
def registry(qtbot):
    reg = TaskRegistry()
    yield reg
    reg.shutdown()


@pytest.fixture
def monitor(qtbot, registry):
    window = MiniJobMonitor(registry)
    qtbot.addWidget(window)
    return window


def _start(registry, task_id="queue.youtube", title="Samurai Champloo", *, cancellable=True, now=0.0):
    return registry.start(
        TaskSpec(task_id=task_id, title=title, owner=_OWNER, cancellable=cancellable),
        now=now,
    )


# ---------------------------------------------------------------------------
# It is a window that does not hold the application open
# ---------------------------------------------------------------------------


class TestWindowKind:
    def test_it_is_a_tool_window(self, monitor):
        assert monitor.windowFlags() & Qt.WindowType.Tool

    def test_it_never_keeps_the_application_alive(self, monitor):
        assert monitor.testAttribute(Qt.WidgetAttribute.WA_QuitOnClose) is False

    def test_keeping_it_above_other_windows_is_a_toggle(self, monitor):
        monitor.stay_on_top_checkbox.setChecked(True)
        assert monitor.windowFlags() & Qt.WindowType.WindowStaysOnTopHint

        monitor.stay_on_top_checkbox.setChecked(False)
        assert not (monitor.windowFlags() & Qt.WindowType.WindowStaysOnTopHint)

    def test_it_stays_a_tool_window_when_pinned_on_top(self, monitor):
        monitor.stay_on_top_checkbox.setChecked(True)
        assert monitor.windowFlags() & Qt.WindowType.Tool


# ---------------------------------------------------------------------------
# It renders exactly what the other surfaces render
# ---------------------------------------------------------------------------


class TestItDoesNotDriftFromTheOtherSurfaces:
    def test_it_renders_the_same_snapshot_as_the_queue_strip(self, qtbot, registry, monitor):
        strip = CurrentJobStrip()
        qtbot.addWidget(strip)
        handle = _start(registry)
        strip.bind(registry, handle.task_id, handle.run_token)
        handle.stage(index=3, total=5, name="Fetching definitions", now=1.0)
        handle.count(current=7, total=24, detail="18 cards", now=78.0)

        assert monitor.line_label.full_text == strip.line_label.full_text
        assert "Fetching definitions" in monitor.line_label.full_text

    def test_it_watches_the_run_the_status_strip_is_naming(self, qtbot, registry, monitor):
        bar = StatusBarWidget()
        qtbot.addWidget(bar)
        bar.bind_task_registry(registry)
        _start(registry, "a", "Mining Samurai Champloo")
        second = _start(registry, "b", "Downloading JMdict")

        monitor.watch(*bar.displayed_run)

        assert monitor.watched_run == bar.displayed_run
        assert monitor.watched_run != (second.task_id, second.run_token)

    def test_the_title_names_the_run(self, registry, monitor):
        _start(registry, title="Samurai Champloo")

        assert monitor.title_label.full_text == "Samurai Champloo"


# ---------------------------------------------------------------------------
# It never invents a number (D18)
# ---------------------------------------------------------------------------


class TestItShowsOnlyWhatTheSnapshotCanBack:
    def test_a_real_denominator_renders_as_a_percentage(self, registry, monitor):
        handle = _start(registry)
        handle.count(current=6, total=24, detail="", now=1.0)

        assert monitor.progress_bar.maximum() == 100
        assert monitor.progress_bar.value() == 25

    def test_no_denominator_renders_as_indeterminate(self, registry, monitor):
        handle = _start(registry)
        handle.count(current=18, total=None, detail="18 cards", now=1.0)

        assert monitor.progress_bar.maximum() == 0
        assert monitor.progress_bar.minimum() == 0

    def test_a_cancelling_run_stops_quoting_a_position(self, registry, monitor):
        handle = _start(registry)
        handle.count(current=6, total=24, detail="", now=1.0)
        handle.cancelling(now=2.0)

        assert monitor.progress_bar.maximum() == 0

    def test_a_cancelling_run_says_so_and_keeps_its_clock(self, registry, monitor):
        handle = _start(registry)
        handle.cancelling(now=2.0)
        registry.tick(now=80.0)

        line = monitor.line_label.full_text
        assert "Cancelling" in line
        assert "01:20" in line

    def test_a_long_cancel_names_what_it_is_waiting_on(self, registry, monitor):
        handle = _start(registry)
        handle.count(current=1, total=3, detail="Writing notes to Anki", now=1.0)
        handle.cancelling(now=2.0)
        registry.tick(now=10.0)

        assert "Writing notes to Anki" in monitor.line_label.full_text

    def test_with_nothing_running_it_says_so_rather_than_showing_a_bar(self, monitor):
        assert monitor.progress_bar.isHidden()
        assert monitor.title_label.full_text == "Nothing is running"
        assert monitor.cancel_button.isEnabled() is False


# ---------------------------------------------------------------------------
# Cancel is a request, never an action
# ---------------------------------------------------------------------------


class TestCancelIsARequest:
    def test_cancel_asks_the_registry_about_the_watched_run(self, qtbot, registry, monitor):
        handle = _start(registry)

        with qtbot.waitSignal(registry.cancel_requested, timeout=1000) as blocker:
            monitor.cancel_button.click()

        assert blocker.args == [handle.task_id]

    def test_cancel_asks_about_only_the_watched_run(self, registry, monitor):
        _start(registry, "a", "Mining Samurai Champloo")
        second = _start(registry, "b", "Downloading JMdict")
        asked: list[str] = []
        registry.cancel_requested.connect(asked.append)

        monitor.watch(second.task_id, second.run_token)
        monitor.cancel_button.click()

        assert asked == ["b"]

    def test_cancel_does_not_itself_mark_the_run_cancelling(self, registry, monitor):
        handle = _start(registry)

        monitor.cancel_button.click()

        # Only the screen holding the cancellation event can say the ask landed.
        assert registry.snapshot(handle.task_id).cancelling is False

    def test_cancel_is_offered_once_and_then_the_wait_is_the_answer(self, registry, monitor):
        handle = _start(registry)
        assert monitor.cancel_button.isEnabled() is True

        handle.cancelling(now=1.0)

        assert monitor.cancel_button.isEnabled() is False

    def test_a_run_that_cannot_be_cancelled_offers_no_button(self, registry, monitor):
        _start(registry, cancellable=False)

        assert monitor.cancel_button.isEnabled() is False

    def test_cancel_with_nothing_running_asks_nothing(self, registry, monitor):
        asked: list[str] = []
        registry.cancel_requested.connect(asked.append)

        monitor.cancel_button.click()

        assert asked == []


# ---------------------------------------------------------------------------
# Show main window
# ---------------------------------------------------------------------------


class TestShowMainWindow:
    def test_it_asks_rather_than_reaching_into_the_window(self, qtbot, registry, monitor):
        with qtbot.waitSignal(monitor.show_main_window_requested, timeout=1000):
            monitor.show_main_window_button.click()


# ---------------------------------------------------------------------------
# The picker
# ---------------------------------------------------------------------------


class TestThePicker:
    def test_one_job_needs_no_choice(self, registry, monitor):
        _start(registry)

        assert monitor.picker.isHidden()

    def test_several_jobs_offer_a_choice(self, registry, monitor):
        _start(registry, "a", "Mining Samurai Champloo")
        _start(registry, "b", "Downloading JMdict")

        assert not monitor.picker.isHidden()
        assert [monitor.picker.itemText(i) for i in range(monitor.picker.count())] == [
            "Mining Samurai Champloo",
            "Downloading JMdict",
        ]

    def test_choosing_a_job_watches_it(self, registry, monitor):
        _start(registry, "a", "Mining Samurai Champloo")
        second = _start(registry, "b", "Downloading JMdict")

        monitor.picker.setCurrentIndex(1)

        assert monitor.watched_run == (second.task_id, second.run_token)

    def test_a_second_job_starting_does_not_repoint_the_window(self, registry, monitor):
        first = _start(registry, "a", "Mining Samurai Champloo")
        _start(registry, "b", "Downloading JMdict")

        assert monitor.watched_run == (first.task_id, first.run_token)

    def test_the_clock_ticking_does_not_rebuild_the_picker(self, registry, monitor):
        _start(registry, "a", "Mining Samurai Champloo")
        _start(registry, "b", "Downloading JMdict")
        monitor.picker.setCurrentIndex(1)

        registry.tick(now=5.0)

        assert monitor.picker.currentIndex() == 1

    def test_the_watched_run_ending_falls_back_to_what_is_still_going(self, registry, monitor):
        first = _start(registry, "a", "Mining Samurai Champloo")
        second = _start(registry, "b", "Downloading JMdict")

        first.finish(TaskOutcome.SUCCEEDED, now=2.0)

        assert monitor.watched_run == (second.task_id, second.run_token)

    def test_a_later_run_of_the_same_id_does_not_inherit_the_pin(self, registry, monitor):
        first = _start(registry, "a", "Mining Samurai Champloo")
        monitor.watch(first.task_id, first.run_token)
        first.finish(TaskOutcome.CANCELLED, now=2.0)
        second = _start(registry, "a", "Mining Cowboy Bebop", now=3.0)

        assert monitor.watched_run == (second.task_id, second.run_token)


# ---------------------------------------------------------------------------
# Lifetime: it owns nothing, and closing it means nothing to the run
# ---------------------------------------------------------------------------


class TestItOwnsNothing:
    def test_it_holds_no_worker_shaped_attribute(self, registry, monitor):
        forbidden = (QThread, threading.Thread, threading.Event)
        held = [
            name
            for name, value in vars(monitor).items()
            if isinstance(value, forbidden) or hasattr(value, "cancel") and not isinstance(value, TaskRegistry)
        ]
        assert held == []

    def test_no_child_object_is_a_thread(self, registry, monitor):
        assert [c for c in monitor.findChildren(QObject) if isinstance(c, QThread)] == []

    def test_it_holds_no_registry_handle_it_could_write_through(self, monitor):
        # A TaskHandle is write access to a run. The monitor renders snapshots.
        assert [name for name, value in vars(monitor).items() if hasattr(value, "run_token")] == []

    def test_closing_it_asks_for_no_cancellation(self, registry, monitor):
        _start(registry)
        asked: list[str] = []
        registry.cancel_requested.connect(asked.append)

        monitor.close()

        assert asked == []

    def test_closing_it_leaves_the_run_running_and_untouched(self, registry, monitor):
        handle = _start(registry)
        handle.count(current=6, total=24, detail="18 cards", now=1.0)
        before = registry.snapshot(handle.task_id)

        monitor.close()

        after = registry.snapshot(handle.task_id)
        assert after == before
        assert after.is_running is True

    def test_closing_it_removes_nothing_from_the_registry(self, registry, monitor):
        handle = _start(registry)

        monitor.close()

        assert [s.task_id for s in registry.running()] == [handle.task_id]

    def test_a_closed_monitor_still_agrees_when_reopened(self, registry, monitor):
        handle = _start(registry)
        monitor.close()
        handle.stage(index=2, total=4, name="Extracting media", now=1.0)

        monitor.show()

        assert monitor.line_label.full_text == format_task_line(registry.snapshot(handle.task_id))


# ---------------------------------------------------------------------------
# Themes: it needs no colour of its own
# ---------------------------------------------------------------------------


class TestItNeedsNoThemeKeys:
    """The window is a plain top-level widget, so it paints the palette's own
    Window/WindowText. That is what lets it ship without a theme key or a QSS
    colour rule and still be correct in all 29 shipped themes."""

    @pytest.fixture(autouse=True)
    def _restore_app_theme(self, qapp):
        """``Theme.apply_to_app`` writes a stylesheet *and* a palette onto the
        shared QApplication. Hand both back exactly as found, or the sweep
        leaks a theme into every later test that reads a rendered colour."""
        stylesheet = qapp.styleSheet()
        palette = QPalette(qapp.palette())
        yield
        qapp.setStyleSheet(stylesheet)
        qapp.setPalette(palette)

    def test_every_shipped_theme_paints_it(self, qapp, qtbot, registry):
        from anki_miner.gui.resources.styles.theme import Theme

        Theme.initialize(active="light")
        keys = list(Theme.get_available_themes())
        assert len(keys) >= 29, f"expected the shipped gallery, got {len(keys)}"

        _start(registry)

        for key in keys:
            Theme.apply_to_app(qapp, key)
            # Built after the apply: an application stylesheet freezes an
            # already-polished widget's palette, so a window constructed first
            # would be answering about the theme it was born under.
            window = MiniJobMonitor(registry)
            qtbot.addWidget(window)
            colors = Theme.get_colors(key)
            palette = window.palette()
            assert palette.color(QPalette.ColorRole.Window).name() == QColor(colors["background"]).name(), key
            assert palette.color(QPalette.ColorRole.WindowText).name() == QColor(colors["text"]).name(), key
            assert window.line_label.full_text != ""
            window.deleteLater()

    def test_it_declares_no_colour_of_its_own(self, monitor):
        assert monitor.styleSheet() == ""
        assert all(child.styleSheet() == "" for child in monitor.findChildren(QWidget))
