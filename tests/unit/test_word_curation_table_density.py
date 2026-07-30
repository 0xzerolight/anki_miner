"""The word curator's table must stay dense.

Its density is what makes it scannable, and it is now the only place the mined
form, reading and sentence are shown -- the detail strip that used to restate the
focused row below it (D45-B) was removed as a restatement of columns 1, 3 and 4.
Nothing may pin a larger Japanese content size into a cell and undo W2-T3's row
tightening, and no cell may carry generated ruby (D45-C was declined).
"""

from __future__ import annotations

import pytest

from anki_miner.gui.widgets.dialogs.word_curation_dialog import WordCurationDialog


@pytest.fixture(autouse=True)
def _no_app_stylesheet(qapp):
    """Measure the rows against the widgets' own fonts.

    A QSS ``font-size`` overrides ``setFont``, so a theme sheet left installed by
    an earlier file on this xdist worker would make the row-height assertion
    measure something other than what it names.
    """
    previous = qapp.styleSheet()
    qapp.setStyleSheet("")
    yield
    qapp.setStyleSheet(previous)


@pytest.fixture()
def dialog(qtbot, make_tokenized_words):
    dlg = WordCurationDialog(make_tokenized_words(3))
    qtbot.addWidget(dlg)
    return dlg


@pytest.fixture()
def long_sentence_cell(qtbot, make_tokenized_word):
    """The Sentence cell of a row whose sentence is far past the cell's cap."""
    sentence = "あ" * 120
    dlg = WordCurationDialog([make_tokenized_word(surface="猫", lemma="猫", reading="ネコ", sentence=sentence)])
    qtbot.addWidget(dlg)
    cell = dlg.table.item(0, 4)
    assert cell is not None
    return cell, sentence


class TestWordTableDensity:
    def test_japanese_cells_carry_the_face_but_no_size(self, dialog):
        from anki_miner.gui.utils.fonts import resolved_families

        for column in (1, 2, 3, 4):  # mined form, surface, reading, sentence
            item = dialog.table.item(0, column)
            assert item is not None
            assert item.font().family() == resolved_families().japanese
            assert item.font().pixelSize() == -1, f"column {column} pins a size into the row"

    def test_the_row_height_is_still_the_shared_rule(self, dialog):
        from anki_miner.gui.utils.qt_helpers import data_row_height

        header = dialog.table.verticalHeader()
        assert header is not None
        assert header.defaultSectionSize() == data_row_height(dialog.table)

    def test_no_row_carries_ruby_or_generated_furigana(self, dialog):
        for row in range(dialog.table.rowCount()):
            for column in range(dialog.table.columnCount()):
                item = dialog.table.item(row, column)
                if item is not None:
                    assert "<ruby>" not in item.text()
                    assert "<rt>" not in item.text()


class TestTheFullSentenceIsStillReachable:
    """The strip was the only untruncated sentence on screen; the cell keeps the
    rest of the routes to it.
    """

    def test_the_cell_hides_nothing_it_does_not_offer_on_hover(self, long_sentence_cell):
        cell, sentence = long_sentence_cell
        assert len(cell.text()) < len(sentence), "the cell is expected to truncate"
        assert cell.toolTip() == sentence

    def test_the_row_copies_the_untruncated_sentence(self, long_sentence_cell):
        from anki_miner.gui.utils.qt_helpers import COPY_ROLE

        cell, sentence = long_sentence_cell
        assert cell.data(COPY_ROLE) == sentence
