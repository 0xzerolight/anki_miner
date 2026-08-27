"""Tests for gui/widgets/backfill_tab.py (Card Backfill tool tab)."""

from __future__ import annotations

import logging
from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QFont, QShortcut
from PyQt6.QtWidgets import QApplication, QHeaderView, QScrollArea

from anki_miner.gui.controllers.task_registry import TaskOutcome, TaskRegistry
from anki_miner.gui.utils.qt_helpers import data_row_height
from anki_miner.gui.widgets.backfill_tab import (
    _PREVIEW_ROW_CAP,
    PREVIEW_MIN_VISIBLE_ROWS,
    CardBackfillTab,
)
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
        "expression_field": "Expression",
        "config_version": 0,
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

    def test_deck_fetch_error_reaches_the_log(self, tab, caplog):
        from anki_miner.gui.workers.base_worker import SingleCallWorker

        message = "Cannot connect to AnkiConnect. Is Anki running?"
        worker = SingleCallWorker(lambda: [], parent=tab)
        with (
            patch(f"{_TAB_MOD}.AnkiService"),
            patch(f"{_TAB_MOD}.FetchDecksWorker", return_value=worker),
            patch.object(worker, "start"),
            caplog.at_level(logging.WARNING, logger=_TAB_MOD),
        ):
            tab._load_decks()
            worker.error.emit(message)

        record = next(
            record for record in caplog.records if record.getMessage().startswith("Card Backfill deck fetch degraded:")
        )
        assert record.name == _TAB_MOD
        assert record.levelno == logging.WARNING
        assert message in record.getMessage()
        assert tab.deck_combo.count() == 1
        assert "all decks" in tab.status_label.text().lower()

    def test_incomplete_field_mapping_is_logged(self, tab, caplog):
        fields = dict(tab.config.anki_fields)
        fields.pop("word")
        tab.config = replace(tab.config, anki_fields=fields)

        with caplog.at_level(logging.WARNING, logger=_TAB_MOD):
            tab._load_decks()

        record = next(
            record for record in caplog.records if record.getMessage().startswith("Card Backfill deck fetch skipped:")
        )
        assert record.levelno == logging.WARNING
        assert "missing=field_mapping" in record.getMessage()


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

    def test_the_expression_column_uses_the_japanese_face(self, tab):
        """The Expression is the mined word; the other three are field data.

        Face only (decision D45-B): a cell font carrying no size resolves
        against the view's own, so the row height does not move.
        """
        from anki_miner.gui.utils.fonts import resolved_families

        tab._on_scan_finished(_plan([_note_plan(1, 1)]))
        expression = tab.preview_table.item(0, 0)
        assert expression.font().family() == resolved_families().japanese
        assert expression.font().pixelSize() == -1
        for column in (1, 2, 3):
            assert tab.preview_table.item(0, column).font().family() != resolved_families().japanese

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

    def test_the_preview_is_a_shared_data_surface(self, tab):
        """D42: no row-number column, no grid, one metric row height."""
        tab._on_scan_finished(_plan([_note_plan(1)]))

        header = tab.preview_table.verticalHeader()
        assert header.isHidden()
        assert tab.preview_table.showGrid() is False
        assert header.sectionResizeMode(0) == QHeaderView.ResizeMode.Fixed
        assert header.defaultSectionSize() == data_row_height(tab.preview_table)

    def test_a_copied_row_carries_the_whole_value_not_the_elided_cell(self, tab, qapp):
        long_value = "x" * 500
        plan = _plan([NotePlan(1, "w", (FieldChange("frequency", "Frequency", long_value, long_value),))])
        tab._on_scan_finished(plan)
        tab.preview_table.selectRow(0)

        shortcuts = [s for s in tab.preview_table.findChildren(QShortcut) if s.key().toString() == "Ctrl+C"]
        assert shortcuts, "no copy shortcut installed on the preview"
        shortcuts[0].activated.emit()

        assert len(tab.preview_table.item(0, 2).text()) < 200  # vacuity guard
        assert long_value in qapp.clipboard().text()

    def test_sorting_is_offered_but_the_preview_opens_in_plan_order(self, tab):
        """Enabling sorting must not silently reorder what is about to be written."""
        plan = _plan([_note_plan(2), _note_plan(1)])
        tab._on_scan_finished(plan)

        assert tab.preview_table.isSortingEnabled()
        assert [tab.preview_table.item(row, 0).text() for row in range(2)] == ["word2", "word1"]

    def test_sorting_by_a_column_orders_by_the_underlying_value(self, tab):
        plan = _plan([_note_plan(2), _note_plan(1)])
        tab._on_scan_finished(plan)

        tab.preview_table.sortItems(0, Qt.SortOrder.AscendingOrder)

        assert [tab.preview_table.item(row, 0).text() for row in range(2)] == ["word1", "word2"]

    def test_empty_plan_state(self, tab):
        tab._on_scan_finished(_plan([]))
        assert tab.preview_table.rowCount() == 0
        assert not tab.apply_button.isEnabled()
        assert tab.summary_label.text() != ""

    def test_empty_plan_fill_mode_is_neutral_about_existing_values(self, tab):
        # A zero-change fill scan can mean populated targets OR lookup misses.
        tab._on_scan_finished(_plan([], scanned=12))
        assert "No new values were found" in tab.summary_label.text()
        assert "already have values" not in tab.summary_label.text()

    def test_empty_plan_overwrite_with_identicals_says_identical(self, tab):
        plan = _plan(
            [],
            scanned=12,
            options=BackfillOptions(field_keys=frozenset({"frequency"}), overwrite=True),
            identical_skips=2,
        )
        tab._on_scan_finished(plan)
        text = tab.summary_label.text()
        assert "Nothing to overwrite" in text
        assert "identical" in text

    def test_empty_plan_overwrite_without_identicals_is_neutral(self, tab):
        # Empty overwrite plan with zero identical skips = lookups found nothing;
        # must NOT claim values are identical or already present.
        plan = _plan(
            [],
            scanned=12,
            options=BackfillOptions(field_keys=frozenset({"frequency"}), overwrite=True),
            identical_skips=0,
        )
        tab._on_scan_finished(plan)
        text = tab.summary_label.text()
        assert "No new values were found" in text
        assert "identical" not in text
        assert "already have values" not in text

    def test_identical_skips_suffix_on_nonempty_plan(self, tab):
        plan = _plan(
            [_note_plan(1)],
            options=BackfillOptions(field_keys=frozenset({"frequency"}), overwrite=True),
            identical_skips=3,
        )
        tab._on_scan_finished(plan)
        text = tab.summary_label.text()
        assert "3" in text
        assert "up to date" in text

    def test_zero_notes_matched_names_the_scope_not_the_fields(self, tab):
        # The bug this replaces: a query that matched nothing reported "all
        # selected fields already have values", sending the user to hunt
        # through a collection that was never examined.
        tab._on_scan_finished(_plan([], scanned=0))
        text = tab.summary_label.text()
        assert "No notes matched" in text
        assert "test_note_type" in text
        assert "already have values" not in text

    def test_zero_notes_matched_names_the_deck_when_one_was_chosen(self, tab):
        plan = _plan(
            [],
            scanned=0,
            options=BackfillOptions(field_keys=frozenset({"frequency"}), deck="Mining::JP"),
        )
        tab._on_scan_finished(plan)
        text = tab.summary_label.text()
        assert "Mining::JP" in text
        assert "test_note_type" in text

    def test_absent_fields_reported_as_a_stale_mapping(self, tab):
        # Distinct from unavailable_fields: the field name is not on the note
        # type at all, so installing resources cannot help.
        plan = _plan([], scanned=12, absent_fields=("PitchGraph",))
        tab._on_scan_finished(plan)
        text = tab.summary_label.text()
        assert "PitchGraph" in text
        assert "stale mapping" in text.lower()

    def test_absent_fields_reported_alongside_a_nonempty_plan(self, tab):
        plan = _plan([_note_plan(1)], absent_fields=("PitchGraph",))
        tab._on_scan_finished(plan)
        assert "PitchGraph" in tab.summary_label.text()

    def test_unavailable_fields_reported(self, tab):
        plan = _plan([], scanned=12, unavailable_fields=("pitch_graph", "pitch_text"))
        tab._on_scan_finished(plan)
        assert "pitch" in tab.summary_label.text().lower()


class TestLayoutSizing:
    """Issue #102: fixed chrome must never crush the preview table.

    The floor is stated in rows rather than pixels (D40/D42): 240px held eight
    rows at the default text size and four at 150%, so a pixel floor re-creates
    the crushing it was added to prevent.
    """

    def _rows_that_fit(self, tab) -> float:
        header = tab.preview_table.horizontalHeader()
        header_h = header.sizeHint().height() if header is not None else 0
        usable = tab.preview_table.minimumHeight() - header_h - 2 * tab.preview_table.frameWidth()
        return usable / data_row_height(tab.preview_table)

    def test_preview_table_has_height_floor(self, tab):
        assert self._rows_that_fit(tab) >= PREVIEW_MIN_VISIBLE_ROWS

    def test_the_floor_still_holds_its_rows_at_a_larger_text_size(self, tab):
        grown = QFont(tab.preview_table.font())
        grown.setPointSizeF(grown.pointSizeF() * 1.5)
        tab.setFont(grown)
        QApplication.sendEvent(tab, QEvent(QEvent.Type.FontChange))

        assert self._rows_that_fit(tab) >= PREVIEW_MIN_VISIBLE_ROWS

    def test_content_wrapped_in_resizable_scroll_area(self, tab):
        scroll = tab.findChild(QScrollArea)
        assert scroll is not None
        assert scroll.widgetResizable()

    def test_table_keeps_floor_at_short_window_height(self, qtbot, tab):
        tab._decks_requested = True  # suppress showEvent deck fetch (network tripwire)
        tab.resize(900, 620)
        tab.show()
        qtbot.waitExposed(tab)
        QApplication.processEvents()
        assert tab.preview_table.height() >= tab.preview_table.minimumHeight()


class TestApplyFlow:
    def test_stale_backfill_plan_aborts_on_config_change(self, tab, backfill_config):
        from PyQt6.QtWidgets import QMessageBox

        stale_plan = _plan([_note_plan(1)], config_version=0)
        changed_config = replace(backfill_config, theme="dark", config_version=1)
        tab.update_config(changed_config)
        tab._on_scan_finished(stale_plan)

        with (
            patch(f"{_TAB_MOD}.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes),
            patch(f"{_TAB_MOD}.BackfillApplyWorker") as factory,
        ):
            tab._start_apply()

        factory.assert_not_called()
        assert "settings changed" in tab.status_label.text().lower()
        assert "re-scan" in tab.status_label.text().lower()
        assert tab._plan is None

        matching_plan = _plan([_note_plan(1)], config_version=1)
        tab._on_scan_finished(matching_plan)
        fake_worker = MagicMock()
        with (
            patch(f"{_TAB_MOD}.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes),
            patch(f"{_TAB_MOD}.BackfillApplyWorker", return_value=fake_worker) as factory,
        ):
            tab._start_apply()

        factory.assert_called_once()
        fake_worker.start.assert_called_once()

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

    def test_unconfirmed_updates_are_visible_and_mark_run_failed(self, tab):
        registry = TaskRegistry(tab)
        tab.bind_task_registry(registry)
        tab._publish_task_start("Card backfill", total=1)
        worker = MagicMock()
        worker.is_cancelled = False
        tab.worker_thread = worker
        tab._on_scan_finished(_plan([_note_plan(1)]))

        tab._on_apply_finished(
            BackfillResult(
                notes_updated=1,
                fields_filled=1,
                tagged=1,
                skipped_stale=0,
                failed=2,
            )
        )

        assert "2 note update(s) were not confirmed" in tab.status_label.text()
        assert "scan again to retry" in tab.status_label.text()
        assert tab._run_failed is True
        tab._on_worker_finished()
        snapshot = registry.snapshot(tab.TASK_ID)
        assert snapshot is not None
        assert snapshot.outcome is TaskOutcome.FAILED

    def test_cancelled_apply_keeps_confirmed_partial_counts_and_cancel_verdict(self, tab):
        registry = TaskRegistry(tab)
        tab.bind_task_registry(registry)
        tab._publish_task_start("Card backfill", total=1)
        worker = MagicMock()
        worker.is_cancelled = True
        tab.worker_thread = worker
        tab._on_scan_finished(_plan([_note_plan(1)]))
        tab.status_label.setText("Cancelling…")
        tab._on_apply_finished(
            BackfillResult(
                notes_updated=1,
                fields_filled=2,
                tagged=1,
                skipped_stale=0,
            )
        )

        tab._on_apply_cancelled()

        assert tab.status_label.text().startswith("Cancelled.")
        assert "2 field(s)" in tab.status_label.text()
        assert "1 note(s)" in tab.status_label.text()
        tab._on_worker_finished()
        snapshot = registry.snapshot(tab.TASK_ID)
        assert snapshot is not None
        assert snapshot.outcome is TaskOutcome.CANCELLED


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


class TestCloseWorkerHandles:
    """``iter_close_workers`` runs inside MainWindow.closeEvent -- it may not raise.

    Mirrors deck_filter_tab's close-crash fix (2aa584): a worker whose native
    ``finished`` already deleteLater()'d it leaves the attribute pointing at a
    dead C++ wrapper. A raw ``isRunning()`` on that wrapper raises RuntimeError
    straight out of closeEvent, past the config save at the end of it.
    """

    def test_a_finished_worker_thread_is_skipped_not_raised_on(self, tab):
        from anki_miner.gui.workers.base_worker import SingleCallWorker

        worker = SingleCallWorker(lambda: None, parent=tab)
        tab.worker_thread = worker
        worker.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
        with pytest.raises(RuntimeError):  # precondition: the wrapper really is dead
            worker.isRunning()

        assert list(tab.iter_close_workers()) == []

    def test_a_finished_deck_worker_is_skipped_not_raised_on(self, tab):
        from anki_miner.gui.workers.base_worker import SingleCallWorker

        worker = SingleCallWorker(lambda: None, parent=tab)
        tab._deck_worker = worker
        worker.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
        with pytest.raises(RuntimeError):  # precondition: the wrapper really is dead
            worker.isRunning()

        assert list(tab.iter_close_workers()) == []


class TestWordAudioGroup:
    def test_checkbox_exists_and_is_disabled_while_unmapped(self, tab):
        # The shared backfill_config leaves expression_audio unmapped.
        assert "word_audio" in tab.field_checkboxes
        checkbox = tab.field_checkboxes["word_audio"]
        assert checkbox.isEnabled() is False
        assert checkbox.isChecked() is False

    def test_checkbox_enables_once_the_field_is_mapped(self, qtbot, backfill_config):
        config = replace(
            backfill_config,
            anki_fields={**backfill_config.anki_fields, "expression_audio": "WordAudio"},
        )
        widget = CardBackfillTab(config)
        qtbot.addWidget(widget)
        checkbox = widget.field_checkboxes["word_audio"]
        assert checkbox.isEnabled() is True
        checkbox.setChecked(True)
        assert "expression_audio" in widget._selected_field_keys()

    def test_it_carries_a_tooltip_naming_the_scan_cost(self, qtbot, backfill_config):
        # The only group that goes to the network per note; the user should be
        # told before starting a scan over a large deck.
        config = replace(
            backfill_config,
            anki_fields={**backfill_config.anki_fields, "expression_audio": "WordAudio"},
        )
        widget = CardBackfillTab(config)
        qtbot.addWidget(widget)
        assert "take a while" in widget.field_checkboxes["word_audio"].toolTip()

    def test_the_group_tooltip_survives_a_trip_through_unmapped(self, qtbot, backfill_config):
        # The gate replaces the tooltip with "Map this field…" while disabled;
        # re-enabling has to put the group's own explanation back, or one pass
        # through an unmapped state destroys it for the rest of the session.
        mapped = replace(
            backfill_config,
            anki_fields={**backfill_config.anki_fields, "expression_audio": "WordAudio"},
        )
        widget = CardBackfillTab(mapped)
        qtbot.addWidget(widget)
        widget.update_config(replace(mapped, anki_fields={**mapped.anki_fields, "expression_audio": ""}))
        assert "Map this field" in widget.field_checkboxes["word_audio"].toolTip()
        widget.update_config(mapped)
        assert "take a while" in widget.field_checkboxes["word_audio"].toolTip()

    def test_the_reading_group_tooltip_survives_too(self, qtbot, backfill_config):
        # Same bug, pre-existing: the reading group lost its explanation the
        # first time either of its two fields went unmapped.
        widget = CardBackfillTab(backfill_config)
        qtbot.addWidget(widget)
        unmapped = replace(
            backfill_config,
            anki_fields={**backfill_config.anki_fields, "expression_reading": ""},
        )
        widget.update_config(unmapped)
        widget.update_config(backfill_config)
        assert "does not generate new readings" in widget.field_checkboxes["reading"].toolTip()

    def test_unmapping_the_field_re_disables_and_unchecks_it(self, qtbot, backfill_config):
        config = replace(
            backfill_config,
            anki_fields={**backfill_config.anki_fields, "expression_audio": "WordAudio"},
        )
        widget = CardBackfillTab(config)
        qtbot.addWidget(widget)
        widget.field_checkboxes["word_audio"].setChecked(True)
        widget.update_config(replace(config, anki_fields={**config.anki_fields, "expression_audio": ""}))
        checkbox = widget.field_checkboxes["word_audio"]
        assert checkbox.isEnabled() is False
        assert checkbox.isChecked() is False


class TestMediaFailureReporting:
    def test_media_failures_are_named_and_degrade_the_run(self, tab):
        tab._on_apply_finished(
            BackfillResult(notes_updated=1, fields_filled=1, tagged=1, skipped_stale=0, media_failed=2)
        )
        assert "2" in tab.status_label.text()
        assert tab._run_failed is True

    def test_a_clean_run_reports_no_media_failure(self, tab):
        tab._on_apply_finished(BackfillResult(notes_updated=1, fields_filled=1, tagged=1, skipped_stale=0))
        assert tab._run_failed is False
