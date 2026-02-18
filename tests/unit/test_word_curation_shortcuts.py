"""Tests for WordCurationDialog keyboard shortcut logic and selection methods.

These tests validate the dialog's internal methods (_toggle_current_row,
_select_all, _deselect_all, get_selected_words) which are the underlying
logic invoked by keyboard shortcuts.

Requires PyQt6 to be importable. Tests are skipped if PyQt6 or a display is unavailable.
"""

import os

import pytest

from anki_miner.models import TokenizedWord

# Skip all tests in this module if PyQt6 is not available or no display
try:
    from PyQt6.QtWidgets import QApplication

    # Create QApplication if not already running (needed for any widget)
    _app = QApplication.instance() or QApplication([])
    _HAS_QT = True
except (ImportError, RuntimeError):
    _HAS_QT = False

# Also skip if no display available (headless CI without virtual display)
_HAS_DISPLAY = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY") or _HAS_QT)

pytestmark = pytest.mark.skipif(
    not (_HAS_QT and _HAS_DISPLAY),
    reason="PyQt6 or display not available",
)


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
        visible_count = sum(
            1 for r in range(dialog.table.rowCount()) if not dialog.table.isRowHidden(r)
        )
        assert visible_count == 1

    def test_clear_search_shows_all_rows(self, dialog):
        """Clearing search should show all rows."""
        dialog._on_search_changed("食べる")
        dialog._on_search_changed("")
        visible_count = sum(
            1 for r in range(dialog.table.rowCount()) if not dialog.table.isRowHidden(r)
        )
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
