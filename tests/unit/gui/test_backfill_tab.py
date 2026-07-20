"""Tests for gui/widgets/backfill_tab.py (Card Backfill tool tab)."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest

from anki_miner.gui.widgets.backfill_tab import _PREVIEW_ROW_CAP, CardBackfillTab
from anki_miner.services.card_backfiller import (
    BackfillOptions,
    BackfillPlan,
    BackfillResult,
    FieldChange,
    NotePlan,
)

_TAB_MOD = "anki_miner.gui.widgets.backfill_tab"


@pytest.fixture
def backfill_config(test_config):
    return replace(
        test_config,
        anki_fields={
            **test_config.anki_fields,
            "expression_reading": "ExpressionReading",
            "expression_furigana": "ExpressionFurigana",
            "pitch_graph": "PitchGraph",
            "pitch_text": "",
            "frequency": "Frequency",
            "frequency_sort": "",
            "definition": "definition",
            "glossary": "",
        },
    )


@pytest.fixture
def tab(qtbot, backfill_config):
    widget = CardBackfillTab(backfill_config)
    qtbot.addWidget(widget)
    return widget


def _plan(notes, field_keys=frozenset({"frequency"}), **kwargs):
    defaults = {
        "options": BackfillOptions(field_keys=field_keys),
        "notes": tuple(notes),
        "scanned": len(notes),
        "skipped_no_identity": 0,
        "unavailable_fields": (),
        "sentinel_only_sorts": 0,
        "expression_field": "Expression",
    }
    defaults.update(kwargs)
    return BackfillPlan(**defaults)


def _note_plan(note_id, n_changes=1, value="new"):
    changes = tuple(FieldChange("frequency", "Frequency", f"old{i}", f"{value}{i}") for i in range(n_changes))
    return NotePlan(note_id, f"word{note_id}", changes)


class TestCheckboxGating:
    def test_group_enabled_when_any_key_mapped(self, tab):
        # frequency mapped, frequency_sort unmapped -> still enabled (common config)
        assert tab.field_checkboxes["frequency"].isEnabled()
        # pitch_graph mapped, pitch_text unmapped -> enabled
        assert tab.field_checkboxes["pitch"].isEnabled()
        assert tab.field_checkboxes["definition"].isEnabled()

    def test_group_disabled_when_no_key_mapped(self, tab):
        assert not tab.field_checkboxes["glossary"].isEnabled()

    def test_reading_group_requires_both_keys(self, qtbot, backfill_config):
        one_mapped = replace(
            backfill_config,
            anki_fields={**backfill_config.anki_fields, "expression_reading": ""},
        )
        widget = CardBackfillTab(one_mapped)
        qtbot.addWidget(widget)
        assert not widget.field_checkboxes["reading"].isEnabled()

    def test_reading_group_enabled_with_both_keys(self, tab):
        assert tab.field_checkboxes["reading"].isEnabled()

    def test_overwrite_default_off(self, tab):
        assert not tab.overwrite_checkbox.isChecked()


class TestDeckDropdown:
    def test_all_decks_at_index_zero(self, tab):
        assert tab.deck_combo.itemText(0) == "All decks"

    def test_decks_populated_on_fetch(self, tab):
        tab._on_decks_fetched(["Mining", "Core"])
        items = [tab.deck_combo.itemText(i) for i in range(tab.deck_combo.count())]
        assert items == ["All decks", "Mining", "Core"]

    def test_empty_fetch_leaves_all_decks_with_status(self, tab):
        tab._on_decks_fetched([])
        assert tab.deck_combo.count() == 1
        assert tab.status_label.text() != ""

    def test_show_event_starts_fetch_once(self, tab, qtbot):
        from PyQt6.QtGui import QShowEvent

        fake_worker = MagicMock()
        with (
            patch(f"{_TAB_MOD}.AnkiService"),
            patch(f"{_TAB_MOD}.FetchDecksWorker", return_value=fake_worker) as factory,
        ):
            tab.showEvent(QShowEvent())
            tab.showEvent(QShowEvent())
        assert factory.call_count == 1
        fake_worker.start.assert_called_once()


class TestScanFlow:
    def test_scan_disabled_while_worker_runs(self, tab):
        running = MagicMock()
        running.isRunning.return_value = True
        tab.worker_thread = running
        tab._set_running(True)
        assert not tab.scan_button.isEnabled()
        assert not tab.apply_button.isEnabled()
        assert tab.cancel_button.isEnabled()

    def test_scan_builds_options_from_checked_groups(self, tab):
        tab.field_checkboxes["frequency"].setChecked(True)
        tab.field_checkboxes["pitch"].setChecked(True)
        tab.overwrite_checkbox.setChecked(True)
        fake_worker = MagicMock()
        with patch(f"{_TAB_MOD}.BackfillScanWorker", return_value=fake_worker) as factory:
            tab._start_scan()
        options = factory.call_args[0][1]
        # Only MAPPED keys inside checked groups (pitch_text/frequency_sort unmapped).
        assert options.field_keys == frozenset({"frequency", "pitch_graph"})
        assert options.overwrite is True
        assert options.deck is None
        fake_worker.start.assert_called_once()

    def test_scan_with_no_group_checked_sets_status(self, tab):
        with patch(f"{_TAB_MOD}.BackfillScanWorker") as factory:
            tab._start_scan()
        factory.assert_not_called()
        assert tab.status_label.text() != ""

    def test_deck_selection_passed(self, tab):
        tab._on_decks_fetched(["Mining"])
        tab.deck_combo.setCurrentIndex(1)
        tab.field_checkboxes["frequency"].setChecked(True)
        with patch(f"{_TAB_MOD}.BackfillScanWorker", return_value=MagicMock()) as factory:
            tab._start_scan()
        assert factory.call_args[0][1].deck == "Mining"


class TestPreviewTable:
    def test_plan_populates_table_and_summary(self, tab):
        plan = _plan([_note_plan(1, 2), _note_plan(2, 1)])
        tab._on_scan_finished(plan)
        assert tab.preview_table.rowCount() == 3
        assert tab.preview_table.item(0, 0).text() == "word1"
        assert tab.preview_table.item(0, 2).text() == "old0"
        assert tab.preview_table.item(0, 3).text() == "new0"
        assert "3" in tab.summary_label.text()  # field count
        assert "2" in tab.summary_label.text()  # note count
        assert tab.apply_button.isEnabled()

    def test_text_free_markup_shows_placeholder(self, tab):
        # A pitch-accent SVG strips to empty text; the New cell must not be blank.
        svg = "<svg viewBox='0 0 1 1'><path d='M0 0'/></svg>"
        plan = _plan([NotePlan(1, "w", (FieldChange("pitch_graph", "Pitch", "", svg),))])
        tab._on_scan_finished(plan)
        assert tab.preview_table.item(0, 3).text() == "(formatted content)"

    def test_row_cap(self, tab):
        plan = _plan([_note_plan(i) for i in range(1, _PREVIEW_ROW_CAP + 50)])
        tab._on_scan_finished(plan)
        assert tab.preview_table.rowCount() == _PREVIEW_ROW_CAP

    def test_long_values_elided_with_tooltip(self, tab):
        long_value = "x" * 500
        plan = _plan([NotePlan(1, "w", (FieldChange("frequency", "Frequency", long_value, long_value),))])
        tab._on_scan_finished(plan)
        cell = tab.preview_table.item(0, 2)
        assert len(cell.text()) < 200
        assert len(cell.toolTip()) >= 200

    def test_empty_plan_state(self, tab):
        tab._on_scan_finished(_plan([]))
        assert tab.preview_table.rowCount() == 0
        assert not tab.apply_button.isEnabled()
        assert tab.summary_label.text() != ""

    def test_unavailable_fields_reported(self, tab):
        plan = _plan([], unavailable_fields=("pitch_graph", "pitch_text"))
        tab._on_scan_finished(plan)
        assert "pitch" in tab.summary_label.text().lower()

    def test_sentinel_only_sorts_called_out(self, tab):
        plan = _plan([_note_plan(1)], sentinel_only_sorts=5)
        tab._on_scan_finished(plan)
        assert "5" in tab.summary_label.text()


class TestApplyFlow:
    def test_apply_confirm_declined_does_nothing(self, tab):
        from PyQt6.QtWidgets import QMessageBox

        tab._on_scan_finished(_plan([_note_plan(1)]))
        with (
            patch(f"{_TAB_MOD}.QMessageBox.question", return_value=QMessageBox.StandardButton.No),
            patch(f"{_TAB_MOD}.BackfillApplyWorker") as factory,
        ):
            tab._start_apply()
        factory.assert_not_called()

    def test_apply_starts_worker_with_plan(self, tab):
        from PyQt6.QtWidgets import QMessageBox

        plan = _plan([_note_plan(1)])
        tab._on_scan_finished(plan)
        fake_worker = MagicMock()
        with (
            patch(f"{_TAB_MOD}.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes),
            patch(f"{_TAB_MOD}.BackfillApplyWorker", return_value=fake_worker) as factory,
        ):
            tab._start_apply()
        assert factory.call_args[0][1] is plan
        fake_worker.start.assert_called_once()

    def test_apply_result_summary_and_reset(self, tab):
        tab._on_scan_finished(_plan([_note_plan(1)]))
        tab._on_apply_finished(BackfillResult(notes_updated=10, fields_filled=14, tagged=10, skipped_stale=2))
        assert "10" in tab.status_label.text()
        assert "14" in tab.status_label.text()
        assert not tab.apply_button.isEnabled()
        assert tab.preview_table.rowCount() == 0


class TestConfigAndLifecycle:
    def test_update_config_clears_plan_and_regates(self, tab, backfill_config):
        tab._on_scan_finished(_plan([_note_plan(1)]))
        assert tab.apply_button.isEnabled()
        new_config = replace(
            backfill_config,
            anki_fields={**backfill_config.anki_fields, "frequency": ""},
        )
        tab.update_config(new_config)
        assert not tab.apply_button.isEnabled()
        assert tab.preview_table.rowCount() == 0
        assert not tab.field_checkboxes["frequency"].isEnabled()

    def test_iter_close_workers_yields_running(self, tab):
        assert list(tab.iter_close_workers()) == []
        running = MagicMock()
        running.isRunning.return_value = True
        tab.worker_thread = running
        assert list(tab.iter_close_workers()) == [running]

    def test_iter_close_workers_yields_running_deck_worker(self, tab):
        # A deck fetch in flight at close must be joined, not abandoned to Qt.
        deck_worker = MagicMock()
        deck_worker.isRunning.return_value = True
        tab._deck_worker = deck_worker
        assert list(tab.iter_close_workers()) == [deck_worker]
        deck_worker.isRunning.return_value = False
        assert list(tab.iter_close_workers()) == []

    def test_error_sets_status(self, tab):
        tab._set_running(True)
        tab._on_worker_error("Backfill scan failed: down")
        assert "down" in tab.status_label.text()
        assert tab.scan_button.isEnabled()
