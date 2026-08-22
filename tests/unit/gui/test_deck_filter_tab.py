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
        assert tab.status_label.text() == "Pick the source deck first."

    def test_empty_target_name_shows_message(self, tab):
        _select_source(tab)
        tab.target_edit.setText("   ")
        with patch(f"{_TAB_MOD}.DeckFilterScanWorker") as worker_cls:
            tab._start_scan()

        worker_cls.assert_not_called()
        assert tab.status_label.text() == "Name the new deck first."

    def test_target_equal_to_source_is_refused(self, tab):
        _select_source(tab, "Premade")
        tab.target_edit.setText("Premade")
        with patch(f"{_TAB_MOD}.DeckFilterScanWorker") as worker_cls:
            tab._start_scan()

        worker_cls.assert_not_called()
        assert "different name" in tab.status_label.text()

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
        assert "3 note(s)" in tab.status_label.text()

    def test_stale_generation_is_ignored(self, tab):
        tab._inspect_generation = 8
        inspection = DeckInspection(1, ("Core",), ("Expression",), {"Core": "Expression"})

        tab._on_inspected(7, inspection)

        assert tab.expression_combo.count() == 1
        assert not tab.expression_combo.isEnabled()


class TestPlanLifecycle:
    def test_scan_result_enables_apply_and_flips_prominence(self, tab):
        tab._on_scan_finished(_plan())

        assert tab.apply_button.isEnabled()
        assert tab.apply_button.objectName() == "primary"
        assert tab.scan_button.objectName() == "secondary"
        assert "1 of 3 note(s) will be copied." in tab.summary_label.text()
        assert "already known or carded: 2" in tab.summary_label.text()

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

    def test_stale_config_version_blocks_apply(self, tab, test_config):
        tab._on_scan_finished(_plan(config_version=test_config.config_version + 1))

        with patch(f"{_TAB_MOD}.QMessageBox") as box:
            tab._start_apply()

        box.question.assert_not_called()
        assert tab._plan is None
        assert "re-scan" in tab.status_label.text()

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

    def test_worker_error_lands_on_the_status_line(self, tab):
        tab._on_worker_error("Deck filter scan failed: boom")

        assert tab.status_label.text() == "Deck filter scan failed: boom"


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
