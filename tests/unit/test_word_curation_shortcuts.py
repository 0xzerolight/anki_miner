"""Tests for WordCurationDialog keyboard shortcut logic and selection methods.

These tests validate the dialog's internal methods (_toggle_current_row,
_select_all, _deselect_all, get_selected_words) which are the underlying
logic invoked by keyboard shortcuts.
"""

import pytest
from PyQt6.QtCore import QItemSelection, QItemSelectionModel, Qt
from PyQt6.QtWidgets import QApplication, QTableWidget

from anki_miner.models import TokenizedWord

# QApplication instance needed for any widget test
_app = QApplication.instance() or QApplication([])


def _make_words(count=3):
    """Create a list of test TokenizedWords."""
    names = ["食べる", "走る", "泳ぐ", "読む", "書く"]
    words = []
    for i in range(count):
        lemma = names[i % len(names)]
        words.append(
            TokenizedWord(
                surface=f"{lemma}た",
                lemma=lemma,
                reading="タベル",
                sentence=f"{lemma}のテスト",
                start_time=float(i),
                end_time=float(i + 2),
                duration=2.0,
                frequency_rank=i * 100 if i > 0 else None,
            )
        )
    return words


@pytest.fixture
def dialog():
    """Create a WordCurationDialog with test words."""
    from anki_miner.gui.widgets.dialogs.word_curation_dialog import WordCurationDialog

    words = _make_words(3)
    dlg = WordCurationDialog(words)
    return dlg


class TestWordCurationDialogSelection:
    """Tests for WordCurationDialog selection methods."""

    def test_all_words_checked_by_default(self, dialog):
        """All words should be checked on initialization."""
        selected = dialog.get_selected_words()
        assert len(selected) == 3

    def test_deselect_all(self, dialog):
        """Deselect All should uncheck every word."""
        dialog._deselect_all()
        selected = dialog.get_selected_words()
        assert len(selected) == 0

    def test_select_all_after_deselect(self, dialog):
        """Select All should re-check all words after deselecting."""
        dialog._deselect_all()
        dialog._select_all()
        selected = dialog.get_selected_words()
        assert len(selected) == 3

    def test_toggle_current_row_unchecks(self, dialog):
        """Toggle on a checked row should uncheck it."""
        dialog.table.setCurrentCell(0, 0)
        dialog._toggle_current_row()
        selected = dialog.get_selected_words()
        assert len(selected) == 2

    def test_toggle_current_row_rechecks(self, dialog):
        """Toggle twice should return to original state."""
        dialog.table.setCurrentCell(0, 0)
        dialog._toggle_current_row()
        dialog._toggle_current_row()
        selected = dialog.get_selected_words()
        assert len(selected) == 3

    def test_toggle_no_selection(self, dialog):
        """Toggle with no current row should be a no-op."""
        dialog.table.setCurrentCell(-1, -1)
        dialog._toggle_current_row()
        selected = dialog.get_selected_words()
        assert len(selected) == 3

    def test_get_selected_words_returns_correct_subset(self, dialog):
        """After unchecking specific rows, only checked words should be returned."""
        # Uncheck row 1 (second word)
        dialog.table.setCurrentCell(1, 0)
        dialog._toggle_current_row()

        selected = dialog.get_selected_words()
        assert len(selected) == 2
        lemmas = {w.lemma for w in selected}
        # Row 1 corresponds to the second word ("走る")
        assert "走る" not in lemmas

    def test_word_count_label_updates(self, dialog):
        """Word count label should reflect current selection."""
        assert "3 of 3" in dialog.word_count_label.text()

        dialog._deselect_all()
        assert "0 of 3" in dialog.word_count_label.text()

        dialog._select_all()
        assert "3 of 3" in dialog.word_count_label.text()


class TestWordCurationDialogSearch:
    """Tests for search/filter functionality."""

    def test_search_filters_visible_rows(self, dialog):
        """Search should hide non-matching rows."""
        dialog._on_search_changed("食べる")
        visible_count = sum(1 for r in range(dialog.table.rowCount()) if not dialog.table.isRowHidden(r))
        assert visible_count == 1

    def test_clear_search_shows_all_rows(self, dialog):
        """Clearing search should show all rows."""
        dialog._on_search_changed("食べる")
        dialog._on_search_changed("")
        visible_count = sum(1 for r in range(dialog.table.rowCount()) if not dialog.table.isRowHidden(r))
        assert visible_count == 3

    def test_select_all_only_affects_visible_rows(self, dialog):
        """Select All should only affect visible (non-hidden) rows."""
        # First deselect all
        dialog._deselect_all()
        # Then filter to show only one word
        dialog._on_search_changed("食べる")
        # Select all (should only select the visible one)
        dialog._select_all()
        # Clear search to see all
        dialog._on_search_changed("")

        selected = dialog.get_selected_words()
        assert len(selected) == 1
        assert selected[0].lemma == "食べる"


def _select_rows(dialog, rows: list[int]) -> None:
    """Highlight the given rows in the dialog's table (issue #12 multi-select)."""
    selection_model = dialog.table.selectionModel()
    assert selection_model is not None
    selection_model.clearSelection()
    if not rows:
        return
    model = dialog.table.model()
    last_col = dialog.table.columnCount() - 1
    selection = QItemSelection()
    for row in rows:
        top_left = model.index(row, 0)
        bottom_right = model.index(row, last_col)
        selection.select(top_left, bottom_right)
    selection_model.select(selection, QItemSelectionModel.SelectionFlag.Select)


class TestMultiRowSelection:
    """Issue #12 — Ctrl+Click / Shift+Click multi-row selection in the curator."""

    def test_extended_selection_mode_enabled(self, dialog):
        """The table must allow Ctrl/Shift+Click multi-row selection."""
        assert dialog.table.selectionMode() == QTableWidget.SelectionMode.ExtendedSelection

    def test_select_all_acts_on_selection_when_2plus_selected(self, dialog):
        """With 2+ rows highlighted, Select All checks only those rows."""
        dialog._deselect_all()
        _select_rows(dialog, [0, 2])
        dialog._select_all()

        states = [dialog.table.item(row, 0).checkState() for row in range(dialog.table.rowCount())]
        assert states[0] == Qt.CheckState.Checked
        assert states[1] == Qt.CheckState.Unchecked
        assert states[2] == Qt.CheckState.Checked

    def test_deselect_all_acts_on_selection_when_2plus_selected(self, dialog):
        """With 2+ rows highlighted, Deselect All unchecks only those rows."""
        # Start with all checked (default), then highlight rows 0 and 1.
        _select_rows(dialog, [0, 1])
        dialog._deselect_all()

        states = [dialog.table.item(row, 0).checkState() for row in range(dialog.table.rowCount())]
        assert states[0] == Qt.CheckState.Unchecked
        assert states[1] == Qt.CheckState.Unchecked
        assert states[2] == Qt.CheckState.Checked

    def test_select_all_falls_back_to_visible_when_no_selection(self, dialog):
        """No highlighted rows -> Select All affects every visible row."""
        dialog._deselect_all()
        _select_rows(dialog, [])
        dialog._select_all()
        assert len(dialog.get_selected_words()) == 3

    def test_select_all_falls_back_to_visible_when_single_row_selected(self, dialog):
        """One highlighted row is below the 2+ threshold -> all visible rows."""
        dialog._deselect_all()
        _select_rows(dialog, [1])
        dialog._select_all()
        # All three visible rows should be checked, not just the highlighted one.
        assert len(dialog.get_selected_words()) == 3

    def test_toggle_selected_rows_flips_all_to_checked_when_any_unchecked(self, dialog):
        """Mixed states with 2+ selected rows flip together toward Checked first."""
        # Start: all checked. Uncheck row 1 individually so selection is mixed.
        dialog.table.item(1, 0).setCheckState(Qt.CheckState.Unchecked)
        _select_rows(dialog, [0, 1, 2])
        dialog._toggle_selected_rows()
        for row in (0, 1, 2):
            assert dialog.table.item(row, 0).checkState() == Qt.CheckState.Checked

    def test_toggle_selected_rows_flips_all_to_unchecked_when_all_checked(self, dialog):
        """All target rows already checked -> next toggle unchecks them."""
        _select_rows(dialog, [0, 1, 2])
        dialog._toggle_selected_rows()
        for row in (0, 1, 2):
            assert dialog.table.item(row, 0).checkState() == Qt.CheckState.Unchecked

    def test_selection_respects_search_filter(self, dialog):
        """Hidden rows in the selection must not be acted on by bulk handlers."""
        dialog._deselect_all()
        # Hide rows 1 and 2 by searching for the lemma in row 0.
        dialog._on_search_changed("食べる")
        # Highlight every row (incl. hidden) and run Select All.
        _select_rows(dialog, [0, 1, 2])
        dialog._select_all()
        dialog._on_search_changed("")

        states = [dialog.table.item(row, 0).checkState() for row in range(dialog.table.rowCount())]
        # Only the visible row (0 -> "食べる") should have been checked.
        assert states[0] == Qt.CheckState.Checked
        assert states[1] == Qt.CheckState.Unchecked
        assert states[2] == Qt.CheckState.Unchecked


class TestFrequencyColumnSort:
    """Issue #6 regression — frequency column must sort numerically, not lexically."""

    def test_frequency_sorts_numerically(self):
        from anki_miner.gui.widgets.dialogs.word_curation_dialog import WordCurationDialog

        # Ranks chosen to expose the bug: lexical sort gives 1,10,100,2,20,3.
        ranks = [3, 100, 1, 20, 10, 2]
        words = []
        for i, rank in enumerate(ranks):
            words.append(
                TokenizedWord(
                    surface=f"w{i}",
                    lemma=f"w{i}",
                    reading="タベル",
                    sentence="x",
                    start_time=float(i),
                    end_time=float(i + 1),
                    duration=1.0,
                    frequency_rank=rank,
                )
            )
        dlg = WordCurationDialog(words)
        dlg.table.sortItems(5, Qt.SortOrder.AscendingOrder)
        sorted_ranks = [int(dlg.table.item(r, 5).text()) for r in range(dlg.table.rowCount())]
        assert sorted_ranks == sorted(ranks)

    def test_frequency_none_sorts_last_ascending(self):
        from anki_miner.gui.widgets.dialogs.word_curation_dialog import WordCurationDialog

        words = [
            TokenizedWord(
                surface="a",
                lemma="a",
                reading="ア",
                sentence="x",
                start_time=0.0,
                end_time=1.0,
                duration=1.0,
                frequency_rank=50,
            ),
            TokenizedWord(
                surface="b",
                lemma="b",
                reading="イ",
                sentence="x",
                start_time=1.0,
                end_time=2.0,
                duration=1.0,
                frequency_rank=None,
            ),
            TokenizedWord(
                surface="c",
                lemma="c",
                reading="ウ",
                sentence="x",
                start_time=2.0,
                end_time=3.0,
                duration=1.0,
                frequency_rank=5,
            ),
        ]
        dlg = WordCurationDialog(words)
        dlg.table.sortItems(5, Qt.SortOrder.AscendingOrder)
        texts = [dlg.table.item(r, 5).text() for r in range(dlg.table.rowCount())]
        assert texts == ["5", "50", "-"]


class TestAddToKnownWords:
    """Issue #42 — 'Add to Known Words' from the curator."""

    def _dialog_with_callback(self):
        from anki_miner.gui.widgets.dialogs.word_curation_dialog import WordCurationDialog

        captured: list[set[str]] = []
        dlg = WordCurationDialog(_make_words(3), mark_known_callback=lambda forms: captured.append(forms) or len(forms))
        return dlg, captured

    def test_calls_callback_with_mined_forms_of_selected_rows(self):
        dlg, captured = self._dialog_with_callback()
        mined = dlg.table.item(0, 1).text()
        _select_rows(dlg, [0])
        dlg._on_add_to_known()
        assert captured == [{mined}]
        assert dlg._marked_known == {mined}

    def test_marked_rows_are_unchecked_and_excluded(self):
        dlg, _ = self._dialog_with_callback()
        mined = dlg.table.item(0, 1).text()
        _select_rows(dlg, [0])
        dlg._on_add_to_known()
        assert dlg.table.item(0, 0).checkState() == Qt.CheckState.Unchecked
        # Excluded from the run's selection.
        assert mined not in {w.mined_form for w in dlg.get_selected_words()}

    def test_marked_row_struck_through(self):
        dlg, _ = self._dialog_with_callback()
        _select_rows(dlg, [0])
        dlg._on_add_to_known()
        assert dlg.table.item(0, 1).font().strikeOut() is True

    def test_marked_row_cannot_be_rechecked_by_select_all(self):
        dlg, _ = self._dialog_with_callback()
        _select_rows(dlg, [0])
        dlg._on_add_to_known()
        dlg._select_all()  # acts on all visible rows
        assert dlg.table.item(0, 0).checkState() == Qt.CheckState.Unchecked

    def test_falls_back_to_current_row_when_no_selection(self):
        dlg, captured = self._dialog_with_callback()
        _select_rows(dlg, [])
        dlg.table.setCurrentCell(1, 0)
        mined = dlg.table.item(1, 1).text()
        dlg._on_add_to_known()
        assert captured == [{mined}]

    def test_noop_without_target(self):
        dlg, captured = self._dialog_with_callback()
        _select_rows(dlg, [])
        dlg.table.setCurrentCell(-1, -1)
        dlg._on_add_to_known()
        assert captured == []

    def test_works_without_callback(self):
        from anki_miner.gui.widgets.dialogs.word_curation_dialog import WordCurationDialog

        dlg = WordCurationDialog(_make_words(3))
        _select_rows(dlg, [0])
        dlg._on_add_to_known()  # must not raise
        assert dlg.table.item(0, 0).checkState() == Qt.CheckState.Unchecked
