"""Tests for the WordCurationDialog detail panel (decision D32, D45-B).

The curator is a keyboard-driven spreadsheet: the row under the cursor has to be
readable without a tooltip or a second click. The panel restates the focused
word's expression, its kana reading, and the sentence, as PLAIN text — ruby was
explicitly not selected (D45-B) — in a strip whose height never moves.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt

from anki_miner.gui.widgets.dialogs.word_curation_dialog import WordCurationDialog


@pytest.fixture(autouse=True)
def _no_app_stylesheet(qapp):
    """Measure the sentence strip against the label's own font.

    A QSS ``font-size`` overrides ``setFont``, so a theme sheet left installed by
    an earlier file on this xdist worker would make the two-line reservation
    assertion measure something other than what it names.
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


class TestDetailPanelStructure:
    def test_panel_exists_on_a_plain_table_dialog(self, dialog):
        """No player, no dictionary, no candidates — the panel is still there."""
        assert dialog.detail_panel is not None
        assert dialog.detail_panel.objectName() == "curator-detail"

    def test_labels_carry_stable_object_names_for_theming(self, dialog):
        assert dialog.detail_expression.objectName() == "curator-detail-expression"
        assert dialog.detail_reading.objectName() == "curator-detail-reading"
        assert dialog.detail_sentence.objectName() == "curator-detail-sentence"

    def test_every_label_is_plain_text_not_rich_text(self, dialog):
        """D45-B: no ruby, and no chance of markup in a sentence rendering as HTML."""
        for label in (dialog.detail_expression, dialog.detail_reading, dialog.detail_sentence):
            assert label.textFormat() == Qt.TextFormat.PlainText

    def test_sentence_strip_reserves_two_lines(self, dialog):
        expected = 2 * dialog.detail_sentence.fontMetrics().lineSpacing()
        assert dialog.detail_sentence.minimumHeight() == expected
        assert dialog.detail_sentence.maximumHeight() == expected


class TestDetailPanelContent:
    def test_focus_fills_expression_reading_and_sentence(self, dialog, make_tokenized_words):
        word = make_tokenized_words(3)[1]
        dialog.table.setCurrentCell(1, 0)

        assert dialog.detail_expression.text() == word.mined_form
        assert dialog.detail_reading.text() == word.reading
        assert dialog.detail_sentence.text() == word.sentence

    def test_full_sentence_is_available_as_a_tooltip(self, dialog):
        dialog.table.setCurrentCell(0, 0)
        assert dialog.detail_sentence.toolTip() == dialog.detail_sentence.text()

    def test_panel_shows_the_untruncated_sentence(self, qtbot, make_tokenized_word):
        long_sentence = "あ" * 120
        dlg = WordCurationDialog(
            [
                make_tokenized_word(
                    surface="猫",
                    lemma="猫",
                    reading="ネコ",
                    sentence=long_sentence,
                )
            ]
        )
        qtbot.addWidget(dlg)
        dlg.table.setCurrentCell(0, 0)

        assert dlg.detail_sentence.text() == long_sentence
        # The table cell stays truncated; only the panel carries the whole line.
        assert dlg.table.item(0, 4).text() != long_sentence

    def test_panel_follows_the_focused_row(self, dialog):
        dialog.table.setCurrentCell(0, 0)
        first = dialog.detail_expression.text()
        dialog.table.setCurrentCell(2, 0)

        assert dialog.detail_expression.text() != first

    def test_panel_follows_the_word_through_a_sort(self, dialog):
        dialog.table.setCurrentCell(0, 0)
        focused = dialog.detail_expression.text()

        dialog.table.sortItems(1, Qt.SortOrder.AscendingOrder)

        assert dialog.detail_expression.text() == focused

    def test_panel_clears_when_no_row_is_focused(self, dialog):
        dialog.table.setCurrentCell(0, 0)
        assert dialog.detail_expression.text() != ""

        dialog.table.setCurrentCell(-1, -1)

        assert dialog.detail_expression.text() == ""
        assert dialog.detail_reading.text() == ""
        assert dialog.detail_sentence.text() == ""

    def test_panel_follows_the_cursor_while_a_modifier_is_held(self, dialog):
        """The panel tracks the cursor, not the selection.

        Qt derives an item view's selection command from the *global*
        ``QGuiApplication::keyboardModifiers()``, so with Ctrl down the cursor
        can move without ``itemSelectionChanged`` ever firing. Driving the panel
        off that signal alone left it showing a row the user had left — and made
        it depend on whichever test last pressed a modifier.
        """
        from PyQt6.QtCore import Qt as QtCore_Qt
        from PyQt6.QtTest import QTest

        dialog.table.setCurrentCell(0, 0)
        assert dialog.detail_expression.text() != ""

        QTest.keyPress(dialog.table, QtCore_Qt.Key.Key_Control, QtCore_Qt.KeyboardModifier.ControlModifier)
        try:
            dialog.table.setCurrentCell(-1, -1)
            assert dialog.table.currentRow() == -1
            assert dialog.detail_expression.text() == ""
        finally:
            QTest.keyRelease(dialog.table, QtCore_Qt.Key.Key_Control, QtCore_Qt.KeyboardModifier.NoModifier)


class TestDetailPanelTypography:
    """Japanese is content here, not chrome (decision D45-B).

    Option B was chosen and option C — real furigana above the kanji — was
    declined, so the reading sits *beneath* the expression and nothing on this
    screen generates ruby.
    """

    def test_the_reading_sits_beneath_the_expression(self, dialog):
        layout = dialog.detail_panel.layout()
        order = [layout.itemAt(i).widget() for i in range(layout.count())]
        assert order == [dialog.detail_expression, dialog.detail_reading, dialog.detail_sentence]

    def test_all_three_lines_use_the_japanese_face(self, dialog):
        from anki_miner.gui.utils.fonts import resolved_families

        japanese = resolved_families().japanese
        assert dialog.detail_expression.font().family() == japanese
        assert dialog.detail_reading.font().family() == japanese
        assert dialog.detail_sentence.font().family() == japanese

    def test_the_expression_is_the_one_feature_line(self, dialog):
        assert dialog.detail_expression.font().pixelSize() > dialog.detail_reading.font().pixelSize()
        assert dialog.detail_reading.font().pixelSize() == dialog.detail_sentence.font().pixelSize()

    def test_content_is_larger_than_the_table_it_summarises(self, dialog):
        assert dialog.detail_reading.font().pixelSize() > dialog.table.font().pixelSize()

    def test_the_expression_carries_the_weight_through_the_stylesheet(self, qapp, dialog):
        """A QSS ``font-weight`` on QWidget overrides ``setFont``.

        The Python bold this panel used to set never rendered; the weight has to
        come from a rule that outranks the base one.
        """
        from anki_miner.gui.resources.styles.theme import Theme

        stylesheet = qapp.styleSheet()
        Theme.apply_to_app(qapp)
        dialog.show()
        qapp.processEvents()
        try:
            assert dialog.detail_expression.font().weight() > dialog.detail_reading.font().weight()
        finally:
            dialog.hide()
            qapp.setStyleSheet(stylesheet)


class TestWordTableDensity:
    """The larger Japanese content sizes must not reach the rows.

    The table's density is what makes it scannable; W2-T3 tightened the rows and
    D45-B's reading size would undo that. The cells get the Japanese *face* and
    nothing else.
    """

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
