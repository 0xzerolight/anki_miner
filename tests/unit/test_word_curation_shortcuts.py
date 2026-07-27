"""Tests for WordCurationDialog keyboard shortcut logic and selection methods.

These tests validate the dialog's internal methods (_toggle_current_row,
_select_all, _deselect_all, get_selected_words) which are the underlying
logic invoked by keyboard shortcuts.
"""

import pytest
from PyQt6.QtCore import QItemSelection, QItemSelectionModel, Qt
from PyQt6.QtWidgets import QApplication, QTableWidget


@pytest.fixture
def dialog(qtbot, make_tokenized_words):
    """Create a WordCurationDialog with test words."""
    from anki_miner.gui.widgets.dialogs.word_curation_dialog import WordCurationDialog

    words = make_tokenized_words(3)
    dlg = WordCurationDialog(words)
    qtbot.addWidget(dlg)
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
        assert "3 included" in dialog.word_count_label.text()

        dialog._deselect_all()
        assert "0 included" in dialog.word_count_label.text()

        dialog._select_all()
        assert "3 included" in dialog.word_count_label.text()


def _apply_search(dialog, text: str) -> None:
    """Helper: set search text and run the filter synchronously (bypasses debounce)."""
    dialog.search_input.setText(text)
    dialog._apply_search()


class TestWordCurationDialogSearch:
    """Tests for search/filter functionality."""

    def test_search_filters_visible_rows(self, dialog):
        """Search should hide non-matching rows."""
        _apply_search(dialog, "食べる")
        visible_count = sum(1 for r in range(dialog.table.rowCount()) if not dialog.table.isRowHidden(r))
        assert visible_count == 1

    def test_clear_search_shows_all_rows(self, dialog):
        """Clearing search should show all rows."""
        _apply_search(dialog, "食べる")
        _apply_search(dialog, "")
        visible_count = sum(1 for r in range(dialog.table.rowCount()) if not dialog.table.isRowHidden(r))
        assert visible_count == 3

    def test_select_all_only_affects_visible_rows(self, dialog):
        """Select All should only affect visible (non-hidden) rows."""
        # First deselect all
        dialog._deselect_all()
        # Then filter to show only one word
        _apply_search(dialog, "食べる")
        # Select all (should only select the visible one)
        dialog._select_all()
        # Clear search to see all
        _apply_search(dialog, "")

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

    def test_include_highlighted_acts_only_on_the_highlight(self, dialog):
        """ "Include highlighted" checks exactly the highlighted rows."""
        dialog._deselect_all()
        _select_rows(dialog, [0, 2])
        dialog._include_highlighted()

        states = [dialog.table.item(row, 0).checkState() for row in range(dialog.table.rowCount())]
        assert states[0] == Qt.CheckState.Checked
        assert states[1] == Qt.CheckState.Unchecked
        assert states[2] == Qt.CheckState.Checked

    def test_include_highlighted_honours_a_single_row(self, dialog):
        """One highlighted row is a target like any other.

        The rule this replaces — "highlighted rows if 2+, else all visible" —
        turned "include this word" into "include all 84" with no indication
        (decision D32).
        """
        dialog._deselect_all()
        _select_rows(dialog, [1])
        dialog._include_highlighted()

        states = [dialog.table.item(row, 0).checkState() for row in range(dialog.table.rowCount())]
        assert states == [
            Qt.CheckState.Unchecked,
            Qt.CheckState.Checked,
            Qt.CheckState.Unchecked,
        ]

    def test_include_highlighted_is_disabled_without_a_highlight(self, dialog):
        _select_rows(dialog, [])
        assert not dialog.include_highlighted_button.isEnabled()

        _select_rows(dialog, [1])
        assert dialog.include_highlighted_button.isEnabled()

    def test_visible_verbs_ignore_the_highlight_entirely(self, dialog):
        """ "Include/Exclude visible" mean the same thing whatever is highlighted —
        that is the point of naming the target on the button."""
        _select_rows(dialog, [1])
        dialog._deselect_all()
        assert dialog.get_selected_words() == []

        dialog._select_all()
        assert len(dialog.get_selected_words()) == 3

    def test_select_all_covers_every_visible_row_with_no_selection(self, dialog):
        dialog._deselect_all()
        _select_rows(dialog, [])
        dialog._select_all()
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
        _apply_search(dialog, "食べる")
        # Highlight every row (incl. hidden) and include the highlight.
        _select_rows(dialog, [0, 1, 2])
        dialog._include_highlighted()
        _apply_search(dialog, "")

        states = [dialog.table.item(row, 0).checkState() for row in range(dialog.table.rowCount())]
        # Only the visible row (0 -> "食べる") should have been checked.
        assert states[0] == Qt.CheckState.Checked
        assert states[1] == Qt.CheckState.Unchecked
        assert states[2] == Qt.CheckState.Unchecked


class TestBulkButtonLabels:
    """D32 — each bulk verb names its own fixed target and counts it live."""

    def test_visible_buttons_count_the_visible_rows(self, dialog):
        assert dialog.select_all_button.text() == "Include visible (3)"
        assert dialog.deselect_all_button.text() == "Exclude visible (3)"

    def test_visible_counts_are_unaffected_by_the_highlight(self, dialog):
        _select_rows(dialog, [1])
        assert dialog.select_all_button.text() == "Include visible (3)"
        assert dialog.deselect_all_button.text() == "Exclude visible (3)"

    def test_highlighted_button_counts_the_highlight(self, dialog):
        _select_rows(dialog, [0, 2])
        assert dialog.include_highlighted_button.text() == "Include highlighted (2)"

    def test_highlighted_button_counts_a_single_row(self, dialog):
        _select_rows(dialog, [1])
        assert dialog.include_highlighted_button.text() == "Include highlighted (1)"

    def test_visible_counts_follow_the_search_filter(self, dialog):
        _apply_search(dialog, "食べる")
        assert dialog.select_all_button.text() == "Include visible (1)"
        assert dialog.deselect_all_button.text() == "Exclude visible (1)"

    def test_highlight_count_discounts_rows_hidden_by_search(self, dialog):
        _select_rows(dialog, [0, 1, 2])
        _apply_search(dialog, "食べる")
        assert dialog.include_highlighted_button.text() == "Include highlighted (1)"


class TestCurationCounter:
    """D32 — one counter line: position, included total, filtered total."""

    def test_counter_reports_position_included_and_shown(self, dialog):
        dialog.table.setCurrentCell(0, 0)
        assert dialog.word_count_label.text() == "Word 1 of 3 · 3 included · 3 shown of 3"

    def test_counter_position_follows_the_focused_row(self, dialog):
        dialog.table.setCurrentCell(2, 0)
        assert dialog.word_count_label.text() == "Word 3 of 3 · 3 included · 3 shown of 3"

    def test_counter_position_is_the_ordinal_among_visible_rows(self, dialog):
        # Hide rows 0 and 1; the surviving row is the FIRST visible one.
        _apply_search(dialog, "泳ぐ")
        dialog.table.setCurrentCell(2, 0)
        assert dialog.word_count_label.text() == "Word 1 of 1 · 3 included · 1 shown of 3"

    def test_counter_drops_the_position_without_a_focused_row(self, dialog):
        dialog.table.setCurrentCell(-1, -1)
        assert dialog.word_count_label.text() == "3 included · 3 shown of 3"

    def test_counter_position_is_recomputed_after_a_sort(self, dialog):
        """Sorting moves the focused word without touching the selection, so the
        counter has to follow the sort indicator, not only itemSelectionChanged."""
        dialog.table.setCurrentCell(0, 0)  # 食べるた — last in code-point order
        assert dialog.word_count_label.text().startswith("Word 1 of 3")

        dialog.table.sortItems(1, Qt.SortOrder.AscendingOrder)

        assert dialog.table.currentRow() == 2
        assert dialog.word_count_label.text().startswith("Word 3 of 3")

    def test_counter_included_total_tracks_checkboxes(self, dialog):
        dialog.table.setCurrentCell(0, 0)
        _select_rows(dialog, [1])
        dialog._toggle_selected_rows()  # S — exclude the highlighted row
        assert dialog.word_count_label.text() == "Word 1 of 3 · 2 included · 3 shown of 3"


class TestKeyHints:
    """D32 — the keyboard contract is stated on the screen, not only in docs."""

    def test_key_hint_line_is_present(self, dialog):
        text = dialog.key_hint_label.text()
        for key in ("S", "Ctrl+A", "Ctrl+D", "Ctrl+Enter"):
            assert key in text


class TestConfirmShortcut:
    """Only Return was window-scoped (it fired from the Search box); it becomes
    Ctrl+Return via the shared primary-action helper."""

    def test_no_bare_return_shortcut_anywhere_in_the_dialog(self, dialog):
        from PyQt6.QtGui import QKeySequence, QShortcut

        keys = {sc.key().toString() for sc in dialog.findChildren(QShortcut)}
        assert QKeySequence(Qt.Key.Key_Return).toString() not in keys

    def test_ctrl_return_confirms(self, dialog):
        from PyQt6.QtGui import QKeySequence, QShortcut

        matches = [sc for sc in dialog.findChildren(QShortcut) if sc.key() == QKeySequence("Ctrl+Return")]
        assert matches, "no Ctrl+Return confirm shortcut"
        matches[0].activated.emit()
        assert dialog.result() == dialog.DialogCode.Accepted

    def test_no_footer_button_is_the_dialog_default(self, dialog):
        """A default button would re-create the bare-Enter commit on kana input."""
        from PyQt6.QtWidgets import QPushButton

        buttons = dialog.findChildren(QPushButton)
        assert buttons
        assert not any(b.isDefault() or b.autoDefault() for b in buttons)


def _find_table_shortcut(dialog, key_str):
    """Find a QShortcut registered on the table by its key sequence (Issue #55)."""
    from PyQt6.QtGui import QKeySequence, QShortcut

    for sc in dialog.table.findChildren(QShortcut):
        if sc.key() == QKeySequence(key_str):
            return sc
    return None


class TestPlayPauseAndToggleKeys:
    """Issue #55 — S becomes the checkbox-toggle key; Space is repurposed."""

    def test_s_shortcut_registered(self, dialog):
        assert _find_table_shortcut(dialog, "S") is not None

    def test_s_key_toggles_current_row_checkbox(self, dialog):
        dialog.table.setCurrentCell(0, 0)
        shortcut = _find_table_shortcut(dialog, "S")
        assert shortcut is not None
        shortcut.activated.emit()
        assert len(dialog.get_selected_words()) == 2

    def test_space_does_not_toggle_checkbox(self, dialog):
        """A real Space keypress must not toggle the checkbox (it's play/pause now).

        Uses QTest.keyClick to drive the actual Qt key-dispatch + shortcut
        interception path, not shortcut.activated.emit() — only a real keypress
        can catch a regression where the Space shortcut is removed and Qt's
        built-in QTableWidget Space-to-toggle fires on the checkable cell.
        """
        from PyQt6.QtCore import Qt as QtCore_Qt
        from PyQt6.QtTest import QTest

        dialog.show()
        QApplication.setActiveWindow(dialog)
        dialog.table.setFocus()
        dialog.table.setCurrentCell(0, 0)
        assert dialog.table.item(0, 0).checkState() == QtCore_Qt.CheckState.Checked

        QTest.keyClick(dialog.table, QtCore_Qt.Key.Key_Space)

        assert dialog.table.item(0, 0).checkState() == QtCore_Qt.CheckState.Checked
        assert len(dialog.get_selected_words()) == 3
        dialog.hide()

    def test_space_shortcut_wired_to_play_pause(self, dialog):
        """The Space shortcut exists and triggers play/pause without error.

        Guards the wiring deterministically even in headless environments where
        synthetic keyClick shortcut delivery can be unreliable.
        """
        shortcut = _find_table_shortcut(dialog, "Space")
        assert shortcut is not None
        # _toggle_play_pause is a no-op when the player pane is hidden; emitting
        # must not toggle any checkbox.
        shortcut.activated.emit()
        assert len(dialog.get_selected_words()) == 3


class TestFrequencyColumnSort:
    """Issue #6 regression — frequency column must sort numerically, not lexically."""

    def test_frequency_sorts_numerically(self, qtbot, make_tokenized_word):
        from anki_miner.gui.widgets.dialogs.word_curation_dialog import WordCurationDialog

        # Ranks chosen to expose the bug: lexical sort gives 1,10,100,2,20,3.
        ranks = [3, 100, 1, 20, 10, 2]
        words = [
            make_tokenized_word(
                surface=f"w{i}",
                lemma=f"w{i}",
                reading="タベル",
                sentence="x",
                start_time=float(i),
                end_time=float(i + 1),
                duration=1.0,
                frequency_rank=rank,
            )
            for i, rank in enumerate(ranks)
        ]
        dlg = WordCurationDialog(words)
        qtbot.addWidget(dlg)
        dlg.table.sortItems(5, Qt.SortOrder.AscendingOrder)
        sorted_ranks = [int(dlg.table.item(r, 5).text()) for r in range(dlg.table.rowCount())]
        assert sorted_ranks == sorted(ranks)

    def test_frequency_none_sorts_last_ascending(self, qtbot, make_tokenized_word):
        from anki_miner.gui.widgets.dialogs.word_curation_dialog import WordCurationDialog

        words = [
            make_tokenized_word(
                surface="a",
                lemma="a",
                reading="ア",
                sentence="x",
                start_time=0.0,
                end_time=1.0,
                duration=1.0,
                frequency_rank=50,
            ),
            make_tokenized_word(
                surface="b",
                lemma="b",
                reading="イ",
                sentence="x",
                start_time=1.0,
                end_time=2.0,
                duration=1.0,
                frequency_rank=None,
            ),
            make_tokenized_word(
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
        qtbot.addWidget(dlg)
        dlg.table.sortItems(5, Qt.SortOrder.AscendingOrder)
        texts = [dlg.table.item(r, 5).text() for r in range(dlg.table.rowCount())]
        assert texts == ["5", "50", "-"]


class TestAddToKnownWords:
    """Issue #42 — 'Add to Known Words' from the curator."""

    def _dialog_with_callback(self, qtbot, make_tokenized_words):
        from anki_miner.gui.widgets.dialogs.word_curation_dialog import WordCurationDialog

        captured: list[set[str]] = []
        dlg = WordCurationDialog(
            make_tokenized_words(3), mark_known_callback=lambda forms: captured.append(forms) or len(forms)
        )
        qtbot.addWidget(dlg)
        return dlg, captured

    def test_calls_callback_with_mined_forms_of_selected_rows(self, qtbot, make_tokenized_words):
        dlg, captured = self._dialog_with_callback(qtbot, make_tokenized_words)
        mined = dlg.table.item(0, 1).text()
        _select_rows(dlg, [0])
        dlg._on_add_to_known()
        assert captured == [{mined}]

    def test_marked_rows_are_unchecked_and_excluded(self, qtbot, make_tokenized_words):
        dlg, _ = self._dialog_with_callback(qtbot, make_tokenized_words)
        mined = dlg.table.item(0, 1).text()
        _select_rows(dlg, [0])
        dlg._on_add_to_known()
        assert dlg.table.item(0, 0).checkState() == Qt.CheckState.Unchecked
        # Excluded from the run's selection.
        assert mined not in {w.mined_form for w in dlg.get_selected_words()}

    def test_marked_row_struck_through(self, qtbot, make_tokenized_words):
        dlg, _ = self._dialog_with_callback(qtbot, make_tokenized_words)
        _select_rows(dlg, [0])
        dlg._on_add_to_known()
        assert dlg.table.item(0, 1).font().strikeOut() is True

    def test_marked_row_cannot_be_rechecked_by_select_all(self, qtbot, make_tokenized_words):
        dlg, _ = self._dialog_with_callback(qtbot, make_tokenized_words)
        _select_rows(dlg, [0])
        dlg._on_add_to_known()
        dlg._select_all()  # acts on the highlighted row — the marked one
        assert dlg.table.item(0, 0).checkState() == Qt.CheckState.Unchecked

    def test_falls_back_to_current_row_when_no_selection(self, qtbot, make_tokenized_words):
        dlg, captured = self._dialog_with_callback(qtbot, make_tokenized_words)
        _select_rows(dlg, [])
        dlg.table.setCurrentCell(1, 0)
        mined = dlg.table.item(1, 1).text()
        dlg._on_add_to_known()
        assert captured == [{mined}]

    def test_noop_without_target(self, qtbot, make_tokenized_words):
        dlg, captured = self._dialog_with_callback(qtbot, make_tokenized_words)
        _select_rows(dlg, [])
        dlg.table.setCurrentCell(-1, -1)
        dlg._on_add_to_known()
        assert captured == []

    def test_works_without_callback(self, qtbot, make_tokenized_words):
        from anki_miner.gui.widgets.dialogs.word_curation_dialog import WordCurationDialog

        dlg = WordCurationDialog(make_tokenized_words(3))
        qtbot.addWidget(dlg)
        _select_rows(dlg, [0])
        dlg._on_add_to_known()  # must not raise
        assert dlg.table.item(0, 0).checkState() == Qt.CheckState.Unchecked
