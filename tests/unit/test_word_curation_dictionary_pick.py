"""F-10: the curator's dictionary pane lets the user choose which dictionary the card's Definition uses."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from PyQt6.QtCore import QUrl

from anki_miner.gui.widgets.dialogs import word_curation_dialog as wcd
from anki_miner.gui.widgets.dialogs.word_curation_dialog import WordCurationDialog
from anki_miner.models import TokenizedWord

ENTRIES = [("JMdict", "<div>cat (from JMdict)</div>"), ("Jitendex", "<div>cat (from Jitendex)</div>")]


@pytest.fixture()
def sync_off_thread(monkeypatch):
    """Run every dispatched lookup inline, on the calling thread."""

    def fake_run_off_thread(parent, work, on_done, on_error=None, **kwargs):
        on_done(work())
        return MagicMock()

    monkeypatch.setattr(wcd, "run_off_thread", fake_run_off_thread)


def _word(lemma: str = "猫", start: float = 1.0) -> TokenizedWord:
    return TokenizedWord(
        surface=lemma,
        lemma=lemma,
        reading="ネコ",
        sentence=f"{lemma}だ",
        start_time=start,
        end_time=start + 2.0,
        duration=2.0,
        pos="名詞",
    )


def _dialog(qtbot, words, entries=ENTRIES) -> WordCurationDialog:
    """A curator focused on word 0, its dictionary pane painted."""
    dlg = WordCurationDialog(words, lookup_fn=lambda term, *args, **kwargs: list(entries))
    qtbot.addWidget(dlg)
    dlg.table.setCurrentCell(dlg._visual_row_for_index(0), 0)
    dlg._on_row_focus_changed()
    dlg._focus_timer.stop()
    dlg._on_focus_timer_fired()
    return dlg


def _picks(dlg: WordCurationDialog) -> dict[str, str]:
    return {w.mined_form: w.dictionary_pick for w in dlg.get_selected_words()}


@pytest.mark.usefixtures("sync_off_thread")
class TestDictionaryPick:
    def test_each_dictionary_gets_a_choice_when_there_are_two(self, qtbot):
        dlg = _dialog(qtbot, [_word()])
        assert dlg.definition_view.toHtml().count("am-dict:") == 2
        assert "Use this dictionary on the card" in dlg.definition_view.toPlainText()
        assert "cat (from Jitendex)" in dlg.definition_view.toPlainText()

    def test_one_dictionary_offers_no_choice(self, qtbot):
        dlg = _dialog(qtbot, [_word()], entries=ENTRIES[:1])
        assert "am-dict:" not in dlg.definition_view.toHtml()

    def test_a_pick_is_stamped_on_its_word_only(self, qtbot):
        dlg = _dialog(qtbot, [_word(), _word("犬", 5.0)])
        dlg.definition_view.anchorClicked.emit(QUrl("am-dict:1"))
        assert _picks(dlg) == {"猫": "Jitendex", "犬": ""}
        assert "Used on the card" in dlg.definition_view.toPlainText()

    def test_back_to_the_first_match_clears_the_pick(self, qtbot):
        dlg = _dialog(qtbot, [_word()])
        dlg.definition_view.anchorClicked.emit(QUrl("am-dict:1"))
        dlg.definition_view.anchorClicked.emit(QUrl("am-dict:clear"))
        assert _picks(dlg) == {"猫": ""}
        assert "Used on the card" not in dlg.definition_view.toPlainText()

    def test_a_pick_made_for_another_card_front_is_not_stamped(self, qtbot):
        # A sentence pick or edit that changes the card front makes the stored pick lapse (recipe point 4).
        dlg = _dialog(qtbot, [_word()])
        dlg.definition_view.anchorClicked.emit(QUrl("am-dict:1"))
        dlg._dictionary_picks[0] = ("別の語", "Jitendex")
        assert _picks(dlg) == {"猫": ""}

    def test_an_unknown_link_changes_nothing(self, qtbot):
        dlg = _dialog(qtbot, [_word()])
        dlg.definition_view.anchorClicked.emit(QUrl("am-dict:7"))
        dlg.definition_view.anchorClicked.emit(QUrl("https://example.org/"))
        assert _picks(dlg) == {"猫": ""}

    def test_nothing_picked_leaves_every_word_untouched(self, qtbot):
        dlg = _dialog(qtbot, [_word(), _word("犬", 5.0)])
        assert _picks(dlg) == {"猫": "", "犬": ""}

    def test_links_never_navigate_the_pane(self, qtbot):
        dlg = _dialog(qtbot, [_word()])
        assert dlg.definition_view.openLinks() is False
