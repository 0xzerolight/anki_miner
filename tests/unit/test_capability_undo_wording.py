"""D20 item 5: the Usage Guide names the unit Undo removes the way the app does."""

from anki_miner.gui.capabilities import CAPABILITIES


def _undo():
    return next(c for c in CAPABILITIES if c.id == "undo-run")


def test_undo_entry_says_cards():
    assert "cards" in _undo().description
    assert "notes" not in _undo().description


def test_undo_entry_is_found_by_card_words():
    assert "delete cards" in _undo().keywords
