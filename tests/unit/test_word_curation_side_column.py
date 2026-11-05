"""The curator's side column composes 1-3 panes; its sizing must compose too.

The column was sized by position: ``setSizes([480, 240, 280])`` against a list
that is built conditionally, so the manga composition (page image + dictionary)
handed the dictionary the sentence picker's share. Stretch and minimum height
now ride each pane's own tuple. These tests pin that, and pin the shares the
column opens at -- a stretch factor only governs a *resize*, so the opening
frame has to be stated separately.

The Sentences pane is in every side column, and it is the one pane that
collapses.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QSplitter

from anki_miner.gui.widgets.dialogs.word_curation_dialog import WordCurationDialog


def _lookup(term: str) -> list[tuple[str, str]]:
    return [("JMdict", f"a definition of {term}")]


def _unshown_curator(qtbot, words) -> WordCurationDialog:
    """A curator the test never shows, still leaving through done() as a real one does.

    pytest-qt's close() skips reject() on a hidden QDialog, so _stop_player
    would never run: the focus timer armed at construction fires in teardown's
    processEvents, and its lookup lands on widgets the delete drain removed.
    """
    dlg = WordCurationDialog(words, lookup_fn=_lookup)
    qtbot.addWidget(dlg, before_close_func=lambda d: d.reject())
    return dlg


def _side_splitter(dlg: WordCurationDialog) -> QSplitter:
    splitters = [s for s in dlg.findChildren(QSplitter) if s.orientation() == Qt.Orientation.Vertical]
    assert splitters, "a multi-pane side column should be a vertical splitter"
    return splitters[0]


@pytest.fixture()
def with_candidates(make_tokenized_word):
    """Words carrying a second sentence candidate, which shows the picker."""

    def _make(count: int = 3):
        words = []
        for index in range(count):
            word = make_tokenized_word(surface=f"語{index}", lemma=f"語{index}", sentence="一つ目の文")
            alternative = make_tokenized_word(surface=f"語{index}", lemma=f"語{index}", sentence="二つ目の文")
            word.sentence_candidates.extend([word, alternative])
            words.append(word)
        return words

    return _make


class TestPickerAndDictionary:
    @pytest.fixture()
    def dialog(self, qtbot, with_candidates):
        dlg = WordCurationDialog(with_candidates(), lookup_fn=_lookup)
        qtbot.addWidget(dlg)
        dlg.resize(1500, 800)
        dlg.show()
        return dlg

    def test_the_picker_keeps_its_rows_and_nothing_more(self, dialog):
        """Stretch 0: a picker showing its candidates is done growing."""
        picker, definition = _side_splitter(dialog).sizes()
        assert picker < definition

    def test_the_definition_takes_the_growth(self, dialog):
        splitter = _side_splitter(dialog)
        before = splitter.sizes()
        dialog.resize(1500, 1100)
        after = splitter.sizes()
        assert after[1] > before[1]
        assert after[0] == before[0]

    def test_no_pane_can_be_dragged_away(self, dialog):
        assert _side_splitter(dialog).childrenCollapsible() is False

    def test_only_the_sentences_pane_collapses(self, dialog):
        splitter = _side_splitter(dialog)
        assert splitter.widget(0) is dialog.sentence_pane
        assert [splitter.isCollapsible(i) for i in range(splitter.count())] == [True, False]
        total = sum(splitter.sizes())

        splitter.setSizes([0, total])
        assert splitter.sizes()[0] == 0

        splitter.setSizes([total, 0])
        assert splitter.sizes()[1] >= dialog.definition_view.minimumHeight() > 0


class TestTheSentencesPaneIsAlwaysThere:
    def test_a_run_without_candidates_gets_it(self, qtbot, make_tokenized_words):
        dlg = _unshown_curator(qtbot, make_tokenized_words(3))
        assert dlg._side_key == "sentences+dict"
        assert _side_splitter(dlg).widget(0) is dlg.sentence_pane

    def test_its_minimum_fits_the_title_and_two_lines(self, qtbot, make_tokenized_words):
        """The old four-row minimum was sized for the list, which a word with one
        sentence no longer shows.
        """
        dlg = _unshown_curator(qtbot, make_tokenized_words(3))
        dlg.ensurePolished()
        title = dlg.sentence_pane_label.sizeHint().height()
        two_lines = 2 * dlg.chosen_sentence_label.fontMetrics().lineSpacing()
        layout = dlg.sentence_pane.layout()
        assert layout is not None
        assert dlg.sentence_pane.minimumHeight() >= title + layout.spacing() + two_lines

    def test_without_the_list_the_sentence_sits_under_the_title(self, qtbot, make_tokenized_words):
        """The height a hidden list leaves must not be shared out between the two labels."""
        dlg = WordCurationDialog(make_tokenized_words(3), lookup_fn=_lookup)
        qtbot.addWidget(dlg)
        dlg.resize(1500, 800)
        dlg.show()
        splitter = _side_splitter(dlg)
        splitter.setSizes([400, sum(splitter.sizes()) - 400])
        qtbot.wait(10)
        assert dlg.sentence_list.isHidden()
        title, sentence = dlg.sentence_pane_label.geometry(), dlg.chosen_sentence_label.geometry()
        layout = dlg.sentence_pane.layout()
        assert layout is not None
        # Each label keeps its own height, so the text does not float to the middle of a share.
        assert title.height() == dlg.sentence_pane_label.sizeHint().height()
        assert sentence.top() == title.height() + layout.spacing()
        assert sentence.height() < dlg.sentence_pane.height() / 2

    def test_a_run_with_candidates_opens_it_taller(self, qtbot, make_tokenized_words, with_candidates):
        """Room for the list below the sentence; a run that never shows one does not pay for it."""
        heights = []
        for words in (make_tokenized_words(3), with_candidates()):
            dlg = WordCurationDialog(words, lookup_fn=_lookup)
            qtbot.addWidget(dlg)
            dlg.resize(1500, 800)
            dlg.show()
            heights.append(_side_splitter(dlg).sizes()[0])
            assert heights[-1] >= dlg.sentence_pane.minimumHeight()
            dlg.hide()
        plain, picker = heights
        assert picker > plain


class TestSinglePane:
    def test_one_pane_still_arrives_wrapped(self, qtbot, with_candidates):
        """The wrapper is what lets the dialog size its column without
        touching a widget the subtitle viewer also uses. Sentences alone is the
        one single-pane column left: a run with candidates and nothing else.
        """
        dlg = WordCurationDialog(with_candidates())
        qtbot.addWidget(dlg)
        assert dlg._side_key == "sentences"
        splitters = [s for s in dlg.findChildren(QSplitter) if s.orientation() == Qt.Orientation.Vertical]
        assert splitters == []
        side = [s for s in dlg.findChildren(QSplitter) if s.orientation() == Qt.Orientation.Horizontal][0].widget(1)
        assert side is not None
        assert side is not dlg.sentence_pane
        assert side.isAncestorOf(dlg.sentence_pane)
