"""Actions must name what they actually do to Anki (D46-B).

Two labels were describing something other than the thing they do:

* **Undo** counted *cards* while deleting *notes*. ``AnkiService`` returns note
  ids and ``ProcessingResult.card_ids`` holds them, so "Undo (42 cards)" on a
  note type with two card templates offered to remove 42 and removed 84.
* **Backfill** is jargon, and its **Scan** / **Apply** pair never said which of
  the two writes to the user's real collection — the read-only half and the
  destructive half were one word each and looked interchangeable.

Class names, worker names, the stable ``backfill`` sub-tab key and
``ProcessingResult.card_ids`` are deliberately unchanged; only the words the
user reads move.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.capabilities import CAPABILITIES, SUBTAB_KEYS
from anki_miner.gui.widgets.backfill_tab import CardBackfillTab
from anki_miner.gui.widgets.dialogs.results_dialog import ResultsDialog
from anki_miner.models import ProcessingResult


@pytest.fixture
def backfill(test_config: AnkiMinerConfig, qtbot) -> CardBackfillTab:
    tab = CardBackfillTab(test_config)
    qtbot.addWidget(tab)
    return tab


def _result_with_notes(count: int) -> ProcessingResult:
    return ProcessingResult(
        total_words_found=count,
        new_words_found=count,
        cards_created=count,
        card_ids=list(range(1, count + 1)),
    )


class TestUndoCountsNotes:
    """The unit shown must be the unit removed."""

    def test_the_button_counts_notes(self, qtbot):
        dialog = ResultsDialog(_result_with_notes(42), undo_callback=lambda ids: len(ids))
        qtbot.addWidget(dialog)
        assert dialog._undo_button.text() == "Undo (42 notes)"

    def test_the_undone_label_counts_notes(self, qtbot):
        dialog = ResultsDialog(_result_with_notes(3), undo_callback=lambda ids: len(ids))
        qtbot.addWidget(dialog)
        dialog._on_undo_done(3)
        assert dialog._undo_button.text() == "Undone (3 notes deleted)"

    def test_a_failed_undo_restores_the_note_wording(self, qtbot, monkeypatch):
        monkeypatch.setattr(
            "anki_miner.gui.widgets.dialogs.results_dialog.QMessageBox.critical",
            lambda *a, **k: None,
        )
        dialog = ResultsDialog(_result_with_notes(7), undo_callback=lambda ids: len(ids))
        qtbot.addWidget(dialog)
        dialog._on_undo_error("boom")
        assert dialog._undo_button.text() == "Undo (7 notes)"

    def test_undo_still_hands_the_callback_the_note_ids(self, qtbot, monkeypatch):
        seen: list[list[int]] = []
        monkeypatch.setattr(
            "anki_miner.gui.widgets.dialogs.results_dialog.QMessageBox.question",
            lambda *a, **k: __import__("PyQt6.QtWidgets", fromlist=["QMessageBox"]).QMessageBox.StandardButton.Yes,
        )
        dialog = ResultsDialog(_result_with_notes(4), undo_callback=lambda ids: seen.append(list(ids)) or 4)
        qtbot.addWidget(dialog)
        dialog._on_undo_clicked()
        qtbot.waitUntil(lambda: bool(seen), timeout=3000)
        assert seen == [[1, 2, 3, 4]]


class TestBackfillSaysWhatItTouches:
    """The screen is named for the tool; the BUTTONS say what they do to Anki.

    The screen name went Card Backfill → Backfill → Update Notes and back; what
    survived every round is that Scan reads and the write says it writes. Those
    are the assertions worth holding.
    """

    def test_the_screen_is_named_for_the_tool(self, backfill):
        headings = [w.text() for w in backfill.findChildren(type(backfill.status_label)) if w.text()]
        assert "Card Backfill" in headings

    def test_scan_declares_itself_read_only(self, backfill):
        assert backfill.scan_button.text() == "Scan Anki (read-only)"

    def test_apply_names_the_write(self, backfill):
        assert backfill.apply_button.text() == "Update Notes in Anki"

    def test_the_inner_tab_label_matches_the_screen(self, test_config, qtbot):
        from anki_miner.gui.widgets.subtitles_tab import SubtitlesTab

        tab = SubtitlesTab(test_config, suppress_optional_startup=True)
        qtbot.addWidget(tab)
        labels = [tab._inner_tabs.tabText(i) for i in range(tab._inner_tabs.count())]
        assert "Card Backfill" in labels

    def test_the_capability_entry_says_notes(self):
        entry = next(c for c in CAPABILITIES if c.id == "card-backfill")
        assert entry.title == "Fill missing fields on existing notes"
        assert "notes" in entry.description
        assert "cards" not in entry.description

    def test_the_stable_subtab_key_did_not_move(self):
        assert "backfill" in SUBTAB_KEYS["subtitles"]
