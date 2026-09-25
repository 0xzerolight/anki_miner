"""S15: the known-words manager names the decks this language's scan skips."""

from __future__ import annotations

from anki_miner.gui.widgets.dialogs.known_words_dialog import KnownWordsManagerDialog
from anki_miner.services.known_word_db import KnownWordDB


def _dialog(qtbot, tmp_path, **kwargs):
    dialog = KnownWordsManagerDialog(KnownWordDB(tmp_path / "known_words.db"), **kwargs)
    qtbot.addWidget(dialog)
    return dialog


def test_the_excluded_decks_are_named(qtbot, tmp_path):
    dialog = _dialog(qtbot, tmp_path, excluded_decks=("Japanese Mining", "French"))
    text = dialog.exclusions_label.text()
    assert "Japanese Mining, French" in text and "Settings → Word Filters" in text


def test_no_exclusions_explains_the_risk(qtbot, tmp_path):
    text = _dialog(qtbot, tmp_path).exclusions_label.text()
    assert "Every deck is scanned" in text and "Settings → Word Filters" in text
