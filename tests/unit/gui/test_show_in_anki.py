"""D4: the receipt answers "where are my cards?" with Show in Anki."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.exceptions import AnkiConnectionError
from anki_miner.gui.controllers.run_receipt import RunReceipt
from anki_miner.gui.utils.progress_telemetry import ActiveDuration
from anki_miner.gui.widgets.inline_receipt import InlineReceipt
from anki_miner.models.processing import TerminalOutcome


def _receipt(notes: int) -> RunReceipt:
    return RunReceipt(
        outcome=TerminalOutcome.SUCCESS,
        items_total=1,
        items_completed=1,
        items_failed=0,
        notes_added=notes,
        note_ids=tuple(range(100, 100 + notes)),
        duration=ActiveDuration(active_s=5.0, suspended_s=0.0),
    )


def _widget(qtbot) -> InlineReceipt:
    widget = InlineReceipt()
    qtbot.addWidget(widget)
    return widget


def _with_details(notes: int) -> RunReceipt:
    from dataclasses import replace

    from anki_miner.models.processing import ProcessingResult

    result = ProcessingResult(total_words_found=4, new_words_found=notes, cards_created=notes)
    return replace(_receipt(notes), results=(result,))


def test_copy_summary_leaves_a_receipt_that_has_view_details(qtbot):
    widget = _widget(qtbot)
    widget.show_receipt(_with_details(2))
    assert widget.details_button.isVisibleTo(widget) is True
    assert widget.copy_button.isVisibleTo(widget) is False


def test_copy_summary_stays_where_there_is_no_view_details(qtbot):
    """Batch runs report counts only, so View details (and its Copy summary) never shows."""
    widget = _widget(qtbot)
    widget.show_receipt(_receipt(2))
    assert widget.details_button.isVisibleTo(widget) is False
    assert widget.copy_button.isVisibleTo(widget) is True


def test_show_in_anki_is_offered_when_the_run_added_cards(qtbot):
    widget = _widget(qtbot)
    widget.show_receipt(_receipt(2))
    assert widget.show_in_anki_button.isVisibleTo(widget) is True
    assert widget.show_in_anki_button.text() == "Show in Anki"


def test_show_in_anki_is_not_offered_for_an_empty_run(qtbot):
    widget = _widget(qtbot)
    widget.show_receipt(_receipt(0))
    assert widget.show_in_anki_button.isVisibleTo(widget) is False


def test_pressing_it_hands_over_the_note_ids(qtbot):
    widget = _widget(qtbot)
    widget.show_receipt(_receipt(2))
    seen: list[list] = []
    widget.show_in_anki_requested.connect(seen.append)

    widget.show_in_anki_button.click()

    assert seen == [[100, 101]]


def test_the_screen_opens_anki_off_the_gui_thread(single_tab, qtbot, monkeypatch):
    calls: list[list[int]] = []
    monkeypatch.setattr(
        "anki_miner.services.anki_service.AnkiService.gui_browse_notes",
        lambda self, ids: calls.append(list(ids)),
    )

    single_tab._show_run_in_anki([5, 6])

    qtbot.waitUntil(lambda: calls == [[5, 6]], timeout=3000)


def test_the_screen_reports_a_failure_in_its_banner(single_tab, qtbot, monkeypatch):
    def boom(self, ids):
        raise AnkiConnectionError("Cannot connect to AnkiConnect. Is Anki running?")

    monkeypatch.setattr("anki_miner.services.anki_service.AnkiService.gui_browse_notes", boom)

    single_tab._show_run_in_anki([5])

    banner = single_tab.issue_banner()
    assert banner is not None
    qtbot.waitUntil(lambda: banner.current_issue() is not None, timeout=3000)
    issue = banner.current_issue()
    assert issue.summary == "Anki Miner couldn't open these cards in Anki."
    assert "Cannot connect to AnkiConnect" in issue.details


def test_the_receipt_is_wired_to_the_screen(single_tab, qtbot, monkeypatch):
    calls: list[list[int]] = []
    monkeypatch.setattr(
        "anki_miner.services.anki_service.AnkiService.gui_browse_notes",
        lambda self, ids: calls.append(list(ids)),
    )
    receipt = single_tab._receipt_widget
    assert receipt is not None
    receipt.show_receipt(_receipt(1))

    receipt.show_in_anki_button.click()

    qtbot.waitUntil(lambda: calls == [[100]], timeout=3000)
