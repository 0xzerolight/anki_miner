"""D20 item 5: the receipt counts cards, in the singular when there is one."""

from __future__ import annotations

from anki_miner.gui.utils import result_copy
from anki_miner.models.processing import TerminalOutcome


def _line(outcome=TerminalOutcome.SUCCESS, *, done=12, total=12, noun="episodes", cards=486):
    return result_copy.run_summary(
        outcome,
        items_completed=done,
        items_total=total,
        item_noun=noun,
        notes_added=cards,
        duration="40m 12s",
    )


def test_a_clean_multi_item_run_counts_cards():
    assert _line() == "Mining complete — 12 episodes, 486 cards added in 40m 12s"


def test_a_clean_single_item_run_counts_cards():
    assert _line(total=1, done=1, cards=7) == "Mining complete — 7 cards added in 40m 12s"


def test_one_card_is_singular():
    assert _line(total=1, done=1, cards=1) == "Mining complete — 1 card added in 40m 12s"
    assert _line(cards=1) == "Mining complete — 12 episodes, 1 card added in 40m 12s"


def test_a_stopped_run_counts_cards():
    line = _line(TerminalOutcome.CANCELLED, done=3, cards=84)
    assert line == "Cancelled — 3 of 12 episodes completed; 84 cards added in 40m 12s"
    single = _line(TerminalOutcome.FAILED, total=1, done=0, cards=1)
    assert single == "Mining failed — 1 card added in 40m 12s"


def test_nothing_says_notes_any_more():
    for outcome in TerminalOutcome:
        for cards in (0, 1, 5):
            assert "note" not in _line(outcome, cards=cards)
            assert "note" not in _line(outcome, total=1, done=1, cards=cards)
