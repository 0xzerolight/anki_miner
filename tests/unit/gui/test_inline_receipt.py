"""The durable inline receipt that replaced the terminal dialog storm (D20).

It is a widget on the screen that owned the run, not a dialog: it never steals
focus, it survives navigating away, and it stays until the user dismisses it or
the next run replaces it.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication, QLineEdit, QVBoxLayout, QWidget

from anki_miner.gui.controllers.run_receipt import RunReceipt
from anki_miner.gui.utils.progress_telemetry import ActiveDuration
from anki_miner.gui.widgets.inline_receipt import InlineReceipt
from anki_miner.models.processing import ProcessingResult, TerminalOutcome


def _receipt(
    outcome: TerminalOutcome = TerminalOutcome.SUCCESS,
    *,
    total: int = 12,
    completed: int = 12,
    failed: int = 0,
    notes: int = 486,
    seconds: float = 40 * 60 + 12,
    slept: float = 0.0,
    results: tuple[ProcessingResult, ...] = (),
) -> RunReceipt:
    return RunReceipt(
        outcome=outcome,
        items_total=total,
        items_completed=completed,
        items_failed=failed,
        notes_added=notes,
        note_ids=tuple(range(notes)),
        duration=ActiveDuration(active_s=seconds, suspended_s=slept),
        results=results,
    )


def _widget(qtbot) -> InlineReceipt:
    receipt = InlineReceipt()
    qtbot.addWidget(receipt)
    return receipt


class TestSummaryText:
    def test_a_complete_run_reads_as_the_brief_specified(self, qtbot):
        widget = _widget(qtbot)

        widget.show_receipt(_receipt(), item_noun="episodes")

        assert widget.summary_text == "Mining complete — 12 episodes, 486 notes added in 40m 12s"

    def test_a_cancelled_run_still_reports_what_it_did(self, qtbot):
        widget = _widget(qtbot)

        widget.show_receipt(
            _receipt(
                TerminalOutcome.CANCELLED,
                completed=3,
                notes=84,
                seconds=8 * 60 + 17,
            ),
            item_noun="episodes",
        )

        assert widget.summary_text == "Cancelled — 3 of 12 episodes completed; 84 notes added in 08m 17s"

    def test_a_partly_failed_run_says_so(self, qtbot):
        widget = _widget(qtbot)

        widget.show_receipt(
            _receipt(TerminalOutcome.PARTIAL, completed=10, failed=2, notes=400, seconds=90),
            item_noun="videos",
        )

        assert widget.summary_text == "Finished with errors — 10 of 12 videos completed; 400 notes added in 01m 30s"

    def test_a_failed_run_says_so(self, qtbot):
        widget = _widget(qtbot)

        widget.show_receipt(
            _receipt(TerminalOutcome.FAILED, completed=0, failed=12, notes=0, seconds=5),
            item_noun="books",
        )

        assert widget.summary_text == "Mining failed — 0 of 12 books completed; 0 notes added in 00m 05s"

    def test_a_single_item_run_never_says_one_episodes(self, qtbot):
        """One-item screens have no count worth printing, so the noun is dropped."""
        widget = _widget(qtbot)

        widget.show_receipt(_receipt(total=1, completed=1, notes=7, seconds=63), item_noun="episodes")

        assert widget.summary_text == "Mining complete — 7 notes added in 01m 03s"

    def test_a_cancelled_single_item_run_reports_its_notes(self, qtbot):
        widget = _widget(qtbot)

        widget.show_receipt(
            _receipt(TerminalOutcome.CANCELLED, total=1, completed=0, notes=0, seconds=12),
            item_noun="episodes",
        )

        assert widget.summary_text == "Cancelled — 0 notes added in 00m 12s"

    def test_a_slept_run_says_the_time_is_active_time(self, qtbot):
        widget = _widget(qtbot)

        widget.show_receipt(_receipt(total=1, notes=3, seconds=120, slept=3600), item_noun="")

        assert widget.summary_text.endswith(" (asleep time excluded)")

    def test_the_label_shows_the_summary(self, qtbot):
        widget = _widget(qtbot)

        widget.show_receipt(_receipt(), item_noun="episodes")

        assert widget.summary_label.full_text == widget.summary_text


class TestLifetime:
    def test_it_starts_hidden(self, qtbot):
        widget = _widget(qtbot)

        assert widget.isVisibleTo(widget.parentWidget()) is False

    def test_showing_a_receipt_reveals_it(self, qtbot):
        widget = _widget(qtbot)

        widget.show_receipt(_receipt(), item_noun="episodes")

        assert widget.isVisibleTo(widget.parentWidget()) is True

    def test_dismiss_hides_it_and_says_so(self, qtbot):
        widget = _widget(qtbot)
        widget.show_receipt(_receipt(), item_noun="episodes")
        dismissed: list[int] = []
        widget.dismissed.connect(lambda: dismissed.append(1))

        widget.dismiss_button.click()

        assert widget.isVisibleTo(widget.parentWidget()) is False
        assert widget.summary_text == ""
        assert dismissed == [1]

    def test_clear_hides_it_without_announcing_a_dismissal(self, qtbot):
        """The next run clears the last one's receipt; the user did not dismiss it."""
        widget = _widget(qtbot)
        widget.show_receipt(_receipt(), item_noun="episodes")
        dismissed: list[int] = []
        widget.dismissed.connect(lambda: dismissed.append(1))

        widget.clear()

        assert widget.isVisibleTo(widget.parentWidget()) is False
        assert dismissed == []

    def test_it_survives_the_screen_being_navigated_away_from(self, qtbot):
        """Hiding the page it lives on must not throw the receipt away."""
        page = QWidget()
        qtbot.addWidget(page)
        layout = QVBoxLayout()
        widget = InlineReceipt()
        layout.addWidget(widget)
        page.setLayout(layout)
        widget.show_receipt(_receipt(), item_noun="episodes")

        page.hide()
        page.show()

        assert widget.isVisibleTo(page) is True
        assert widget.summary_text == "Mining complete — 12 episodes, 486 notes added in 40m 12s"

    def test_it_never_takes_focus(self, qtbot):
        """A receipt appearing mid-typing must not swallow the next keystroke."""
        page = QWidget()
        qtbot.addWidget(page)
        layout = QVBoxLayout()
        edit = QLineEdit()
        widget = InlineReceipt()
        layout.addWidget(edit)
        layout.addWidget(widget)
        page.setLayout(layout)
        page.show()
        qtbot.waitExposed(page)
        edit.setFocus()

        widget.show_receipt(_receipt(), item_noun="episodes")

        assert page.focusWidget() is edit


class TestActions:
    def test_view_details_is_offered_only_when_there_are_details(self, qtbot):
        widget = _widget(qtbot)

        widget.show_receipt(_receipt(), item_noun="episodes")

        assert widget.details_button.isVisibleTo(widget) is False

    def test_view_details_appears_when_the_run_produced_results(self, qtbot):
        widget = _widget(qtbot)
        result = ProcessingResult(total_words_found=4, new_words_found=2, cards_created=2)

        widget.show_receipt(_receipt(results=(result,)), item_noun="episodes")

        assert widget.details_button.isVisibleTo(widget) is True

    def test_view_details_asks_for_the_details_surface(self, qtbot):
        widget = _widget(qtbot)
        result = ProcessingResult(total_words_found=4, new_words_found=2, cards_created=2)
        widget.show_receipt(_receipt(results=(result,)), item_noun="episodes")
        asked: list[int] = []
        widget.details_requested.connect(lambda: asked.append(1))

        widget.details_button.click()

        assert asked == [1]

    def test_copy_summary_puts_the_exact_line_on_the_clipboard(self, qtbot):
        widget = _widget(qtbot)
        widget.show_receipt(_receipt(), item_noun="episodes")

        widget.copy_button.click()

        clipboard = QApplication.clipboard()
        assert clipboard is not None
        assert clipboard.text() == "Mining complete — 12 episodes, 486 notes added in 40m 12s"
