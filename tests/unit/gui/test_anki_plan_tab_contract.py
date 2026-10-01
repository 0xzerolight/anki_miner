"""The Scan → Preview → Apply contract Card Backfill and Deck Filter share.

Both screens run the same plumbing, so every case here runs against both. What
is meant to differ between them -- the wording, the Apply gate, the deck
control, the logger that names the screen -- is spelled out per screen in
``SCREENS`` and pinned, so moving shared code can never quietly make one screen
speak or behave like the other.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from unittest.mock import MagicMock, call, patch

import pytest
from PyQt6.QtCore import QEvent
from PyQt6.QtWidgets import QApplication, QComboBox, QMessageBox, QWidget

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.backfill_tab import CardBackfillTab
from anki_miner.gui.widgets.deck_filter_tab import DeckFilterTab
from anki_miner.gui.workers.base_worker import SingleCallWorker
from anki_miner.services.card_backfiller import BackfillOptions, BackfillPlan, FieldChange, NotePlan
from anki_miner.services.deck_filter import DeckFilterOptions, DeckFilterPlan, KeptNote


def _backfill_plan(config_version: int) -> BackfillPlan:
    return BackfillPlan(
        options=BackfillOptions(field_keys=frozenset({"frequency"})),
        notes=(NotePlan(1, "word", (FieldChange("frequency", "Frequency", "old", "new"),)),),
        scanned=1,
        skipped_no_identity=0,
        unavailable_fields=(),
        expression_field="Expression",
        config_version=config_version,
    )


def _deck_filter_plan(config_version: int) -> DeckFilterPlan:
    kept = KeptNote(
        note_id=1,
        model_name="Core",
        fields={"Expression": "語"},
        tags=(),
        expression="語",
        reading="",
        frequency_rank=None,
        forced=False,
    )
    return DeckFilterPlan(
        options=DeckFilterOptions(source_deck="Premade", target_deck="Premade (Filtered)"),
        kept=(kept,),
        drops=(),
        scanned=1,
        forced_count=0,
        config_version=config_version,
    )


@dataclass(frozen=True)
class Screen:
    """One screen's side of the contract."""

    build: Callable[[AnkiMinerConfig], QWidget]
    module: str
    deck_combo: str
    #: Bar actions that sit after the quiet verb whatever the stage.
    quiet_extras: tuple[str, ...]
    apply_worker: str
    plan: Callable[[int], object]
    applying: str
    fetch_failed: str
    settings_changed: str
    worker_failed_log: str
    fetch_failed_log: str
    worker_failed: str
    #: Whether Apply stays live when the scan's warnings are not on screen.
    #: Card Backfill refuses to apply past a warning the user cannot see.
    applies_past_unseen_warnings: bool
    #: Whether a failed Apply leaves the plan held so Apply can be retried as is.
    #: Deck Filter forgets it: chunks copied before the failure would be copied
    #: twice, so a retry must rescan first (BA-023).
    failed_apply_keeps_plan: bool


SCREENS = {
    "backfill": Screen(
        build=CardBackfillTab,
        module="anki_miner.gui.widgets.backfill_tab",
        deck_combo="deck_combo",
        quiet_extras=("restyle_button",),
        apply_worker="BackfillApplyWorker",
        plan=_backfill_plan,
        applying="Applying…",
        fetch_failed="Couldn't fetch deck names from Anki — scanning all decks.",
        settings_changed="Settings changed since this scan; re-scan before applying.",
        worker_failed_log="Card Backfill worker failed:",
        fetch_failed_log="Card Backfill deck fetch degraded:",
        worker_failed="Card Backfill could not finish.",
        applies_past_unseen_warnings=False,
        failed_apply_keeps_plan=True,
    ),
    "deckfilter": Screen(
        build=DeckFilterTab,
        module="anki_miner.gui.widgets.deck_filter_tab",
        deck_combo="source_combo",
        quiet_extras=(),
        apply_worker="DeckFilterApplyWorker",
        plan=_deck_filter_plan,
        applying="Copying…",
        fetch_failed="Couldn't fetch deck names from Anki — is Anki running?",
        settings_changed="Settings changed since this scan; re-scan before copying.",
        worker_failed_log="Deck Filter worker failed:",
        fetch_failed_log="Deck Filter deck fetch failed:",
        worker_failed="Deck Filter could not finish.",
        applies_past_unseen_warnings=True,
        failed_apply_keeps_plan=False,
    ),
}


@pytest.fixture(params=sorted(SCREENS))
def screen(request) -> Screen:
    return SCREENS[request.param]


@pytest.fixture
def tab(qtbot, test_config, screen):
    widget = screen.build(test_config)
    qtbot.addWidget(widget)
    return widget


def _combo(tab, screen: Screen) -> QComboBox:
    return getattr(tab, screen.deck_combo)


def _issue(tab):
    banner = tab.issue_banner()
    return banner.current_issue() if banner is not None else None


def _running() -> MagicMock:
    worker = MagicMock()
    worker.isRunning.return_value = True
    return worker


def _dead_worker(tab) -> SingleCallWorker:
    """A handle whose C++ QThread is already gone, as after a deleteLater()."""
    worker = SingleCallWorker(lambda: None, parent=tab)
    worker.deleteLater()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    with pytest.raises(RuntimeError):  # precondition: the wrapper really is dead
        worker.isRunning()
    return worker


class TestPinnedBar:
    def test_the_bar_swaps_the_two_verbs_and_keeps_the_screens_extras(self, tab, test_config, screen):
        extras = [getattr(tab, name) for name in screen.quiet_extras]
        bar = tab.action_bar

        assert bar.current_primary() is tab.scan_button
        assert bar._secondary == [tab.cancel_button, tab.apply_button, *extras]

        tab._on_scan_finished(screen.plan(test_config.config_version))

        assert bar.current_primary() is tab.apply_button
        assert bar._secondary == [tab.cancel_button, tab.scan_button, *extras]


class TestApplyStart:
    def test_a_confirmed_apply_shows_the_screens_live_verb(self, tab, test_config, screen):
        tab._on_scan_finished(screen.plan(test_config.config_version))

        with (
            patch(f"{screen.module}.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes),
            patch(f"{screen.module}.{screen.apply_worker}") as worker_cls,
        ):
            tab._start_apply()

        worker_cls.return_value.start.assert_called_once_with()
        assert tab.status_label.text() == screen.applying

    def test_a_retried_apply_clears_the_failed_applys_banner(self, tab, test_config, screen):
        tab._on_scan_finished(screen.plan(test_config.config_version))
        apply_worker_cls = getattr(importlib.import_module(screen.module), screen.apply_worker)

        def confirmed_apply():
            with (
                patch(f"{screen.module}.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes),
                patch(f"{screen.module}.{screen.apply_worker}"),
            ):
                tab._start_apply()

        confirmed_apply()
        # The failing worker is a real Apply worker; the screen tells Apply and Scan apart by type.
        tab.worker_thread = MagicMock(spec=apply_worker_cls)
        tab._on_worker_error("Apply failed: boom")
        assert _issue(tab).summary == screen.worker_failed
        if screen.failed_apply_keeps_plan:
            assert tab._plan is not None  # the plan survives, so Apply is live again
        else:
            assert tab._plan is None  # the retry rescans first (BA-023)
            tab._on_scan_finished(screen.plan(test_config.config_version))
        tab.worker_thread = None

        confirmed_apply()

        assert _issue(tab) is None

    def test_a_declined_apply_keeps_the_banner(self, tab, test_config, screen):
        tab._on_scan_finished(screen.plan(test_config.config_version))
        tab._on_worker_error("Apply failed: boom")

        with (
            patch(f"{screen.module}.QMessageBox.question", return_value=QMessageBox.StandardButton.No),
            patch(f"{screen.module}.{screen.apply_worker}") as worker_cls,
        ):
            tab._start_apply()

        worker_cls.assert_not_called()
        assert _issue(tab).summary == screen.worker_failed


class TestProgress:
    def test_counts_reach_the_bar_and_the_task_registry(self, tab):
        with patch.object(tab, "_publish_task_count") as publish:
            tab._on_progress(0, 0)
            tab._on_progress(3, 5)

        assert publish.call_args_list == [
            call(current=0, total=None, detail=""),
            call(current=3, total=5, detail=""),
        ]
        assert (tab.progress_bar.maximum(), tab.progress_bar.value()) == (5, 3)


class TestCancel:
    """The scan workers emit no ``cancelled`` signal, so ``finished`` closes out.

    Without that, a cancelled scan left "Cancelling…" on screen for good — a
    status asserting live work that had already ended (D17).
    """

    def test_cancel_stops_the_live_worker_and_says_it_is_waiting(self, tab):
        worker = _running()
        tab.worker_thread = worker
        tab.cancel_button.setEnabled(True)

        tab._cancel()

        worker.cancel.assert_called_once_with()
        assert not tab.cancel_button.isEnabled()
        assert tab.status_label.text() == "Cancelling…"

    def test_the_registry_hears_of_the_cancel_before_the_worker_does(self, tab):
        order = MagicMock()
        order.worker.isRunning.return_value = True
        tab.worker_thread = order.worker

        with patch.object(tab, "_publish_task_cancelling", order.publish_cancelling):
            tab._cancel()

        assert [name for name, _args, _kwargs in order.mock_calls] == [
            "worker.isRunning",
            "publish_cancelling",
            "worker.cancel",
        ]

    def test_cancelled_scan_reports_cancelled(self, tab):
        worker = MagicMock()
        worker.is_cancelled = True
        tab.worker_thread = worker
        tab.status_label.setText("Cancelling…")

        tab._on_worker_finished()

        assert tab.status_label.text() == "Cancelled."
        assert tab.worker_thread is None

    def test_finished_scan_keeps_its_own_receipt(self, tab):
        worker = MagicMock()
        worker.is_cancelled = False
        tab.worker_thread = worker
        tab.status_label.setText("Cancelling…")

        tab._on_worker_finished()

        assert tab.status_label.text() == "Cancelling…"

    def test_cancelled_apply_receipt_survives_the_finish(self, tab):
        # _on_apply_cancelled runs before finished; the finish must not
        # overwrite the partial receipt it composed.
        worker = MagicMock()
        worker.is_cancelled = True
        tab.worker_thread = worker
        tab.status_label.setText("Cancelled. 1 done.")

        tab._on_worker_finished()

        assert tab.status_label.text() == "Cancelled. 1 done."

    def test_a_cancelled_apply_replaces_the_screens_live_verb(self, tab, test_config, screen):
        tab._on_scan_finished(screen.plan(test_config.config_version))
        tab.status_label.setText(screen.applying)

        tab._on_apply_cancelled()

        assert tab.status_label.text() == "Cancelled."
        assert tab._plan is None
        assert tab.preview_table.rowCount() == 0
        assert tab.summary_label.text() == ""
        assert not tab.apply_button.isEnabled()

    def test_a_cancelled_apply_keeps_the_partial_receipt_behind_the_verdict(self, tab):
        tab.status_label.setText("1 done.")

        tab._on_apply_cancelled()

        assert tab.status_label.text() == "Cancelled. 1 done."

    def test_cancel_shows_only_while_a_run_is_going(self, tab):
        """E07: a disabled Cancel at idle is still a button to read."""
        assert tab.cancel_button.isHidden()

        tab._set_running(True)
        assert not tab.cancel_button.isHidden()

        tab._set_running(False)
        assert tab.cancel_button.isHidden()


class TestStalePlan:
    def test_a_plan_from_older_settings_is_dropped_without_a_modal(self, tab, test_config, screen):
        tab._on_scan_finished(screen.plan(test_config.config_version + 1))
        assert tab.apply_button.objectName() == "primary"

        with patch(f"{screen.module}.QMessageBox") as box:
            tab._start_apply()

        box.question.assert_not_called()
        assert tab._plan is None
        assert tab.preview_table.rowCount() == 0
        assert not tab.apply_button.isEnabled()
        assert tab.scan_button.objectName() == "primary"
        assert tab.status_label.text() == screen.settings_changed


class TestApplyGate:
    def test_each_screen_keeps_its_own_rule_for_warnings_off_screen(self, tab, test_config, screen):
        tab._on_scan_finished(screen.plan(test_config.config_version), ("Dictionary missing.",))
        assert tab.apply_button.isEnabled()  # the warning is in the visible summary
        tab.summary_label.hide()

        tab._set_running(False)

        assert tab.apply_button.isEnabled() is screen.applies_past_unseen_warnings


class TestDeckList:
    """A deck fetch that failed because Anki was closed must be retried.

    The regression: the latch was set on the ATTEMPT, and ``get_deck_names``
    answers an unreachable Anki with an empty list, so the failure was
    remembered as "done". Neither screen has a Refresh button, so the line
    stayed on screen for the life of the process.
    """

    def test_empty_fetch_leaves_the_tab_asking(self, tab):
        with patch.object(tab, "_load_decks") as load:
            tab.ensure_decks()
            assert load.call_count == 1

            tab._on_decks_fetched([])
            assert _issue(tab) is not None

            tab.ensure_decks()
            assert load.call_count == 2

    def test_a_real_deck_list_stops_the_asking_and_clears_the_line(self, tab, screen):
        tab._on_decks_fetched([])
        assert _issue(tab) is not None

        tab._on_decks_fetched(["Default", "Premade"])

        assert _issue(tab) is None
        assert _combo(tab, screen).count() == 3  # the fixed first item + two decks
        with patch.object(tab, "_load_decks") as load:
            tab.ensure_decks()
            load.assert_not_called()

    def test_a_scan_message_survives_a_deck_fetch(self, tab):
        """Only the fetch failure is cleared, never whatever else wrote there."""
        tab.status_label.setText("Scanned 40 notes.")

        tab._on_decks_fetched(["Default"])

        assert tab.status_label.text() == "Scanned 40 notes."

    def test_a_failed_fetch_says_what_the_screen_falls_back_to(self, tab, screen):
        tab._on_decks_fetched([])

        assert _issue(tab).summary == screen.fetch_failed
        assert _combo(tab, screen).count() == 1

    def test_the_fetch_error_is_logged_under_the_screens_own_module(self, tab, screen, caplog):
        with caplog.at_level(logging.WARNING, logger=screen.module):
            tab._on_deck_fetch_error("boom")

        record = next(r for r in caplog.records if r.getMessage().startswith(screen.fetch_failed_log))
        assert record.name == screen.module
        assert _issue(tab).summary == screen.fetch_failed

    def test_a_later_success_clears_only_the_fetch_banner(self, tab):
        """A validation banner shown after the failed fetch is not the fetch's to clear."""
        from anki_miner.gui.widgets.base import ScreenIssue

        tab._on_decks_fetched([])
        tab.show_screen_issue(ScreenIssue(summary="Something else."))

        tab._on_decks_fetched(["Default"])

        assert _issue(tab).summary == "Something else."


class TestWorkerError:
    def test_the_failure_is_a_banner_and_logs_under_the_screen(self, tab, screen, caplog):
        tab._set_running(True)

        with caplog.at_level(logging.WARNING, logger=screen.module):
            tab._on_worker_error("Scan failed: boom")

        record = next(r for r in caplog.records if r.getMessage().startswith(screen.worker_failed_log))
        assert record.name == screen.module
        issue = _issue(tab)
        assert issue.summary == screen.worker_failed
        assert issue.details == "Scan failed: boom"
        assert tab._run_failed is True
        assert tab.scan_button.isEnabled()


class TestCloseWorkers:
    """``iter_close_workers`` runs inside MainWindow.closeEvent -- it may not raise.

    A worker whose native ``finished`` already deleteLater()'d it leaves the
    attribute pointing at a dead C++ wrapper. A raw ``isRunning()`` on that
    wrapper raises RuntimeError straight out of closeEvent, past the config save
    at the end of it.
    """

    def test_nothing_running_yields_nothing(self, tab):
        assert list(tab.iter_close_workers()) == []

    def test_the_run_is_yielded_before_the_deck_fetch(self, tab):
        run, fetch = _running(), _running()
        tab.worker_thread = run
        tab._deck_worker = fetch

        assert list(tab.iter_close_workers()) == [run, fetch]

    def test_a_deck_fetch_is_yielded_only_while_it_runs(self, tab):
        # A deck fetch in flight at close must be joined, not abandoned to Qt.
        fetch = _running()
        tab._deck_worker = fetch
        assert list(tab.iter_close_workers()) == [fetch]

        fetch.isRunning.return_value = False
        assert list(tab.iter_close_workers()) == []

    def test_a_dead_run_handle_is_skipped_not_raised_on(self, tab):
        tab.worker_thread = _dead_worker(tab)

        assert list(tab.iter_close_workers()) == []

    def test_a_dead_deck_fetch_handle_is_skipped_not_raised_on(self, tab):
        tab._deck_worker = _dead_worker(tab)

        assert list(tab.iter_close_workers()) == []


class TestRunLineInThePinnedBar:
    """D1 point 6: the run strip under the page folds into the pinned bar."""

    def test_the_run_strip_is_not_on_screen(self, tab):
        assert not tab.status_label.isVisibleTo(tab)
        assert not tab.progress_bar.isVisibleTo(tab)

    def test_a_run_line_is_the_bars_kept_line(self, tab):
        tab._set_run_line("Cancelled. 1 done.")

        assert tab.status_label.text() == "Cancelled. 1 done."
        assert tab.action_bar.stage_label.full_text == "Cancelled. 1 done."

    def test_a_cancelled_apply_keeps_its_verdict_in_the_bar(self, tab):
        tab._set_run_line("1 done.")

        tab._on_apply_cancelled()

        assert tab.action_bar.stage_label.full_text == "Cancelled. 1 done."
