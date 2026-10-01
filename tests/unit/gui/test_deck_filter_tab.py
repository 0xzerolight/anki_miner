"""Tests for gui/widgets/deck_filter_tab.py (gating, staleness, receipts)."""

from __future__ import annotations

import threading
from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import QEvent
from PyQt6.QtWidgets import QApplication, QMessageBox

from anki_miner.gui.widgets.deck_filter_tab import DeckFilterTab
from anki_miner.gui.workers.base_worker import SingleCallWorker
from anki_miner.services.deck_filter import (
    DeckFilterOptions,
    DeckFilterPlan,
    DeckFilterResult,
    DeckInspection,
    KeptNote,
)

_TAB_MOD = "anki_miner.gui.widgets.deck_filter_tab"


@pytest.fixture
def tab(qtbot, test_config):
    widget = DeckFilterTab(test_config)
    qtbot.addWidget(widget)
    return widget


def _select_source(tab, deck="Premade"):
    """Put a deck in the combo and select it without touching the network."""
    with patch(f"{_TAB_MOD}.run_off_thread", MagicMock(return_value=None)):
        tab.source_combo.addItem(deck)
        tab.source_combo.setCurrentIndex(tab.source_combo.count() - 1)


def _plan(config_version=0, kept_count=1):
    kept = tuple(
        KeptNote(
            note_id=i,
            model_name="Core",
            fields={"Expression": f"語{i}"},
            tags=(),
            expression=f"語{i}",
            reading="",
            frequency_rank=None,
            forced=False,
        )
        for i in range(kept_count)
    )
    return DeckFilterPlan(
        options=DeckFilterOptions(source_deck="Premade", target_deck="Premade (Filtered)"),
        kept=kept,
        drops=(("known", 2),),
        scanned=kept_count + 2,
        forced_count=0,
        config_version=config_version,
    )


class TestScanGating:
    def test_no_source_deck_shows_message_and_starts_nothing(self, tab):
        with patch(f"{_TAB_MOD}.DeckFilterScanWorker") as worker_cls:
            tab._start_scan()

        worker_cls.assert_not_called()
        assert tab.issue_banner().current_issue().summary == "Pick the source deck first."

    def test_empty_target_name_shows_message(self, tab):
        _select_source(tab)
        tab.target_edit.setText("   ")
        with patch(f"{_TAB_MOD}.DeckFilterScanWorker") as worker_cls:
            tab._start_scan()

        worker_cls.assert_not_called()
        assert tab.issue_banner().current_issue().summary == "Name the new deck first."

    def test_target_equal_to_source_is_refused(self, tab):
        _select_source(tab, "Premade")
        tab.target_edit.setText("Premade")
        with patch(f"{_TAB_MOD}.DeckFilterScanWorker") as worker_cls:
            tab._start_scan()

        worker_cls.assert_not_called()
        assert "different name" in tab.issue_banner().current_issue().summary

    def test_target_naming_the_source_in_another_case_is_refused(self, tab):
        """BA-022: Anki deck names ignore case, so 'premade' resolves to the Premade deck
        and Apply would copy every kept note back into the source deck."""
        _select_source(tab, "Premade")
        tab.target_edit.setText("premade")
        with patch(f"{_TAB_MOD}.DeckFilterScanWorker") as worker_cls:
            tab._start_scan()

        worker_cls.assert_not_called()
        assert "different name" in tab.issue_banner().current_issue().summary

    @pytest.mark.parametrize(
        ("source", "target"),
        [("Mining::Ärger", "mining::ÄRGER"), ("日本語::Core 2K", "日本語::core 2k"), ("Écoute::Ça", "écoute::ça")],
    )
    def test_target_in_another_unicode_case_is_refused(self, tab, source, target):
        """BA-022: Anki's case-insensitive deck lookup covers nested and non-ASCII names."""
        _select_source(tab, source)
        tab.target_edit.setText(target)
        with patch(f"{_TAB_MOD}.DeckFilterScanWorker") as worker_cls:
            tab._start_scan()

        worker_cls.assert_not_called()
        assert "different name" in tab.issue_banner().current_issue().summary

    def test_valid_inputs_start_the_scan_worker(self, tab):
        _select_source(tab)
        worker = MagicMock()
        with patch(f"{_TAB_MOD}.DeckFilterScanWorker", MagicMock(return_value=worker)) as worker_cls:
            tab._start_scan()

        options = worker_cls.call_args.args[1]
        assert options.source_deck == "Premade"
        assert options.target_deck == "Premade (Filtered)"
        assert options.expression_field is None
        worker.start.assert_called_once_with()


class TestTargetSuggestion:
    def test_selecting_a_source_suggests_a_filtered_name(self, tab):
        _select_source(tab, "Core 2k")
        assert tab.target_edit.text() == "Core 2k (Filtered)"

    def test_a_user_typed_name_is_never_overwritten(self, tab):
        tab.target_edit.setText("My deck")
        _select_source(tab, "Core 2k")
        assert tab.target_edit.text() == "My deck"

    def test_a_previous_suggestion_is_replaced_by_the_next(self, tab):
        _select_source(tab, "Core 2k")
        _select_source(tab, "Tango N1")
        assert tab.target_edit.text() == "Tango N1 (Filtered)"


class TestInspection:
    def test_inspection_populates_field_combos(self, tab):
        tab._inspect_generation = 7
        inspection = DeckInspection(
            note_count=3,
            models=("Core",),
            field_names=("Expression", "Meaning"),
            first_field_by_model={"Core": "Expression"},
        )

        tab._on_inspected(7, inspection)

        assert [tab.expression_combo.itemText(i) for i in range(tab.expression_combo.count())] == [
            "(first field)",
            "Expression",
            "Meaning",
        ]
        assert tab.expression_combo.isEnabled()
        assert tab.deck_info_label.text() == "3 note(s) in the deck."
        assert not tab.field_row.isHidden()

    def test_stale_generation_is_ignored(self, tab):
        tab._inspect_generation = 8
        inspection = DeckInspection(1, ("Core",), ("Expression",), {"Core": "Expression"})

        tab._on_inspected(7, inspection)

        assert tab.expression_combo.count() == 1
        assert not tab.expression_combo.isEnabled()
        assert tab.field_row.isHidden()

    def test_the_field_pickers_wait_for_the_deck(self, tab):
        """E07: Word and Reading field appear once a deck is read."""
        assert tab.field_row.isHidden()

    def test_a_failed_read_is_a_banner(self, tab):
        tab._inspect_generation = 3

        tab._on_inspect_error(3, "Couldn't read the deck: boom")

        issue = tab.issue_banner().current_issue()
        assert issue.summary == "The deck could not be read."
        assert issue.details == "Couldn't read the deck: boom"

    def test_picking_another_deck_clears_the_read_banner(self, tab):
        _select_source(tab, "Broken")
        tab._on_inspect_error(tab._inspect_generation, "Couldn't read the deck: boom")
        assert tab.issue_banner().current_issue() is not None

        _select_source(tab, "Core 2k")
        tab._on_inspected(
            tab._inspect_generation,
            DeckInspection(3, ("Core",), ("Expression",), {"Core": "Expression"}),
        )

        assert tab.issue_banner().current_issue() is None
        assert tab.deck_info_label.text() == "3 note(s) in the deck."

    def test_picking_a_deck_clears_the_pick_a_deck_refusal(self, tab):
        tab._start_scan()
        assert tab.issue_banner().current_issue().summary == "Pick the source deck first."

        _select_source(tab)

        assert tab.issue_banner().current_issue() is None


class TestCardLayout:
    def test_the_page_is_three_titled_cards(self, tab):
        from anki_miner.gui.widgets.enhanced import SectionHeader

        titles = [header.title_label.text() for header in tab.findChildren(SectionHeader)]
        assert titles == ["Deck", "Filters", "Preview"]
        for card in (tab.deck_card, tab.filters_card, tab.preview_card):
            assert card.objectName() == "card"

    def test_the_preview_waits_for_a_scan(self, tab):
        assert tab.preview_card.isHidden()

        tab._on_scan_finished(_plan())

        assert not tab.preview_card.isHidden()
        assert not tab.preview_table.isHidden()
        assert tab.page_filler.isHidden()

    def test_a_dropped_plan_takes_the_preview_down(self, tab):
        tab._on_scan_finished(_plan())

        tab._drop_plan()

        assert tab.preview_card.isHidden()
        assert tab.preview_table.isHidden()
        assert not tab.page_filler.isHidden()


class TestPlanLifecycle:
    def test_scan_result_enables_apply_and_flips_prominence(self, tab):
        tab._on_scan_finished(_plan())

        assert tab.apply_button.isEnabled()
        assert tab.apply_button.objectName() == "primary"
        assert tab.scan_button.objectName() == "secondary"
        assert "1 of 3 note(s) will be copied." in tab.summary_label.text()
        assert "already known or in Anki: 2" in tab.summary_label.text()

    def test_empty_plan_keeps_apply_disabled(self, tab):
        tab._on_scan_finished(_plan(kept_count=0))

        assert not tab.apply_button.isEnabled()
        assert tab.scan_button.objectName() == "primary"

    def test_update_config_drops_the_held_plan(self, tab, test_config):
        tab._on_scan_finished(_plan())

        tab.update_config(replace(test_config, config_version=5))

        assert tab._plan is None
        assert not tab.apply_button.isEnabled()
        assert tab.preview_table.rowCount() == 0

    def test_confirm_no_aborts_the_apply(self, tab):
        tab._on_scan_finished(_plan())

        with (
            patch(
                f"{_TAB_MOD}.QMessageBox.question",
                return_value=QMessageBox.StandardButton.No,
            ),
            patch(f"{_TAB_MOD}.DeckFilterApplyWorker") as worker_cls,
        ):
            tab._start_apply()

        worker_cls.assert_not_called()
        assert tab._plan is not None

    def test_confirm_yes_starts_the_apply_worker(self, tab):
        tab._on_scan_finished(_plan())
        worker = MagicMock()

        with (
            patch(
                f"{_TAB_MOD}.QMessageBox.question",
                return_value=QMessageBox.StandardButton.Yes,
            ),
            patch(f"{_TAB_MOD}.DeckFilterApplyWorker", MagicMock(return_value=worker)) as worker_cls,
        ):
            tab._start_apply()

        assert worker_cls.call_args.args[1] is not None
        worker.start.assert_called_once_with()


class TestReceipts:
    def test_apply_receipt_names_the_deck_and_count(self, tab):
        tab._on_scan_finished(_plan())

        tab._on_apply_finished(DeckFilterResult(created=1, not_created=0))

        assert tab.status_label.text() == 'Copied 1 note(s) into "Premade (Filtered)".'
        assert tab._plan is None

    def test_rejected_notes_are_reported(self, tab):
        tab._on_scan_finished(_plan())

        tab._on_apply_finished(DeckFilterResult(created=0, not_created=1))

        assert "not accepted by Anki" in tab.status_label.text()


class TestCloseWorkerHandles:
    """``iter_close_workers`` runs inside MainWindow.closeEvent -- it may not raise."""

    def test_a_finished_inspect_worker_is_skipped_not_raised_on(self, tab):
        # run_off_thread deleteLater()s the worker it returns once the work
        # finishes, but _inspect_worker keeps pointing at the wrapper. A raw
        # isRunning() on that dead wrapper escaped closeEvent and reached the
        # excepthook dialog, skipping the config save at the end of it.
        worker = SingleCallWorker(lambda: None, parent=tab)
        tab._inspect_worker = worker
        worker.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
        with pytest.raises(RuntimeError):  # precondition: the wrapper really is dead
            worker.isRunning()

        assert list(tab.iter_close_workers()) == []

    def test_a_running_inspect_worker_is_still_yielded(self, qtbot, tab):
        gate = threading.Event()
        worker = SingleCallWorker(gate.wait, parent=tab)
        tab._inspect_worker = worker
        worker.start()
        try:
            qtbot.waitUntil(worker.isRunning)

            assert list(tab.iter_close_workers()) == [worker]
        finally:
            gate.set()
            assert worker.wait(2000)

    def test_the_inspection_is_joined_after_the_run_and_the_deck_fetch(self, tab):
        run, fetch, inspect = MagicMock(), MagicMock(), MagicMock()
        for worker in (run, fetch, inspect):
            worker.isRunning.return_value = True
        tab.worker_thread, tab._deck_worker, tab._inspect_worker = run, fetch, inspect

        assert list(tab.iter_close_workers()) == [run, fetch, inspect]


class TestFailedApply:
    def test_a_failed_apply_drops_the_plan_so_a_retry_cannot_copy_twice(self, tab):
        """BA-023: notes copied before the failure are already in the target deck and Apply
        sends allowDuplicate=True, so re-applying the held plan would copy them again. A fresh
        scan counts them as already in Anki, as Cancel already forces."""
        from anki_miner.gui.workers.deck_filter_worker import DeckFilterApplyWorker

        tab._on_scan_finished(_plan())
        tab.worker_thread = MagicMock(spec=DeckFilterApplyWorker)

        tab._on_worker_error("AnkiConnect timed out")

        assert tab._plan is None
        assert not tab.apply_button.isEnabled()
        tab.worker_thread = None

    def test_a_failed_scan_keeps_the_held_plan(self, tab):
        """BA-023: only a failed Apply forgets the plan; a failed rescan changes nothing."""
        from anki_miner.gui.workers.deck_filter_worker import DeckFilterScanWorker

        tab._on_scan_finished(_plan())
        tab.worker_thread = MagicMock(spec=DeckFilterScanWorker)

        tab._on_worker_error("AnkiConnect timed out")

        assert tab._plan is not None
        tab.worker_thread = None
