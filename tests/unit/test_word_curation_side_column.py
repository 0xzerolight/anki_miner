"""The curator's side column composes 1-3 panes; its sizing must compose too.

The column was sized by position: ``setSizes([480, 240, 280])`` against a list
that is built conditionally, so the manga composition (page image + dictionary)
handed the dictionary the sentence picker's share. Stretch and minimum height
now ride each pane's own tuple. These tests pin that, and pin the shares the
column opens at -- a stretch factor only governs a *resize*, so the opening
frame has to be stated separately.
"""

from __future__ import annotations

import pytest
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QSplitter

from anki_miner.gui.widgets.dialogs.word_curation_dialog import WordCurationDialog


def _lookup(term: str) -> list[tuple[str, str]]:
    return [("JMdict", f"a definition of {term}")]


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


class TestSinglePane:
    def test_one_pane_still_arrives_wrapped(self, qtbot, make_tokenized_words):
        """The wrapper is what lets the dialog size its column without
        touching a widget the subtitle viewer also uses.
        """
        dlg = WordCurationDialog(make_tokenized_words(3), lookup_fn=_lookup)
        qtbot.addWidget(dlg)
        splitters = [s for s in dlg.findChildren(QSplitter) if s.orientation() == Qt.Orientation.Vertical]
        assert splitters == []
        side = [s for s in dlg.findChildren(QSplitter) if s.orientation() == Qt.Orientation.Horizontal][0].widget(1)
        assert side is not None
        assert side is not dlg.definition_view
        assert side.isAncestorOf(dlg.definition_view)
