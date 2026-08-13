"""Layout-floor regression tests for the word curation dialog.

The curator shipped with its filter/bulk toolbar inside the left splitter pane.
A ``QSplitter`` honours a child's ``minimumSizeHint`` absolutely, and that row
cannot shrink -- a pinned search field, four full-text verbs and, originally,
the counter -- so the pane carried a ~1254px floor and the media column beside
it was pinned at its own ~200px minimum at every window size. The floor is
font-sized rather than screen-sized, which is why maximising never helped.

Nothing in the suite measured a minimum, which is why the bug was invisible to
it. These tests measure. They are deliberately written against *floors and
proportions*, not against pixel counts, so a font change moves them together.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QSplitter, QWidget

from anki_miner.gui.widgets.dialogs.word_curation_dialog import WordCurationDialog


def _lookup(term: str) -> list[tuple[str, str]]:
    return [("JMdict", f"a definition of {term}")]


@pytest.fixture()
def dialog(qtbot, make_tokenized_words):
    """A curator with a second column: table left, definition browser right.

    Uses the dictionary pane rather than the player: the player tests swap
    ``player_widget`` for a ``MagicMock``, so a layout test that used one would
    be measuring the mock.
    """
    dlg = WordCurationDialog(make_tokenized_words(5), lookup_fn=_lookup)
    qtbot.addWidget(dlg)
    return dlg


def _main_splitter(dlg: WordCurationDialog) -> QSplitter:
    splitters = [s for s in dlg.findChildren(QSplitter) if s.orientation() == Qt.Orientation.Horizontal]
    assert splitters, "the curator should split the table from its side panes"
    return splitters[0]


def _table_pane(dlg: WordCurationDialog) -> QWidget:
    pane = _main_splitter(dlg).widget(0)
    assert pane is not None
    return pane


class TestToolbarIsNotInsideThePane:
    def test_the_search_field_is_not_a_descendant_of_the_table_pane(self, dialog):
        """The row that cannot shrink must not be the thing sizing the pane."""
        assert not _table_pane(dialog).isAncestorOf(dialog.search_input)

    def test_every_bulk_verb_is_out_of_the_pane_too(self, dialog):
        pane = _table_pane(dialog)
        for button in (
            dialog.select_all_button,
            dialog.deselect_all_button,
            dialog.include_highlighted_button,
            dialog.add_known_button,
        ):
            assert not pane.isAncestorOf(button)

    def test_the_counter_is_out_of_the_pane(self, dialog):
        assert not _table_pane(dialog).isAncestorOf(dialog.word_count_label)

    def test_the_table_pane_can_shrink_to_something_reasonable(self, dialog):
        """Was 1254px. The table itself asks for well under a fifth of that."""
        assert _table_pane(dialog).minimumSizeHint().width() < 250


class TestKeyHintsDoNotFloorThePane:
    def test_the_hint_line_wraps(self, dialog):
        """An unwrapped QLabel demands its whole text width as a minimum."""
        assert dialog.key_hint_label.wordWrap() is True

    def test_the_hint_line_asks_for_almost_nothing(self, dialog):
        assert dialog.key_hint_label.minimumSizeHint().width() < 200


class TestTheSplitIsAPolicy:
    @pytest.mark.parametrize("width", [1000, 1280, 1500, 1913])
    def test_the_side_column_keeps_a_real_share_at_every_width(self, dialog, width):
        """The defect in one assertion: the side column used to hold ~13%."""
        dialog.resize(width, 800)
        dialog.show()
        left, right = _main_splitter(dialog).sizes()
        assert 0.3 < right / (left + right) < 0.5

    def test_neither_column_can_be_dragged_away_entirely(self, dialog):
        assert _main_splitter(dialog).childrenCollapsible() is False

    def test_the_side_column_is_a_container_not_a_bare_pane(self, dialog):
        """A minimum set on the pane itself would follow it into the subtitle
        viewer, which shares ``SubtitlePlayerWidget``.
        """
        side = _main_splitter(dialog).widget(1)
        assert side is not None
        assert side is not dialog.definition_view
        assert side.isAncestorOf(dialog.definition_view)


class TestTheDialogFitsOnARealScreen:
    def test_it_never_opens_larger_than_the_screen(self, dialog, qapp):
        """Was a flat resize(1500, 760): on a 1366x768 laptop that put the
        Confirm button under the taskbar.

        A window still cannot go below its own layout minimum, so the width
        assertion allows for a screen narrower than that -- there is nothing
        honest to do in that case, and Qt would refuse anyway.
        """
        available = (dialog.screen() or qapp.primaryScreen()).availableGeometry()
        floor = max(dialog.minimumWidth(), dialog.minimumSizeHint().width())
        assert dialog.width() <= max(available.width(), floor)
        assert dialog.height() <= available.height()

    def test_it_can_be_narrower_than_a_1024_screen(self, dialog):
        """Was 1506px wide -- unfittable in the 1536 logical px of a 1080p
        screen at 125% scaling, which is the configuration that produced the
        original report.
        """
        assert dialog.minimumSizeHint().width() <= 1024

    def test_no_flat_pixel_minimum_dominates_the_floor(self, dialog):
        """The one part of the floor that answered to no font.

        The toolbar row *is* this dialog's width floor, and the rest of that
        row -- a label, four verbs -- is text, so the floor moves with the
        rendered face. The search field's flat 200px minimum was a quarter of
        the whole floor here and refused to move with it, which left nothing
        spare for a face 20% wider than the one the 1024px budget was set on:
        a CI runner resolving ``Sans Serif`` to such a face measured 1039px.
        """
        assert dialog.search_input.minimumWidth() < dialog.minimumSizeHint().width() / 6
