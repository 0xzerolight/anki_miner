"""The run's details window after D4 and D20 item 5."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication, QMessageBox

from anki_miner.gui.controllers.run_receipt import RunReceipt
from anki_miner.gui.utils.progress_telemetry import ActiveDuration
from anki_miner.gui.widgets.dialogs.results_dialog import ResultsDialog
from anki_miner.gui.widgets.enhanced import StatCard
from anki_miner.gui.widgets.inline_receipt import InlineReceipt
from anki_miner.models import ProcessingResult
from anki_miner.models.processing import TerminalOutcome


def _result(count: int = 3) -> ProcessingResult:
    return ProcessingResult(
        total_words_found=count,
        new_words_found=count,
        cards_created=count,
        elapsed_time=12.0,
        card_ids=list(range(1, count + 1)),
    )


def test_the_rate_tile_is_gone(qtbot):
    dialog = ResultsDialog(_result())
    qtbot.addWidget(dialog)

    labels = [card._label for card in dialog.findChildren(StatCard)]
    assert "Processing Rate" not in labels
    assert "Cards Created" in labels


def test_undo_counts_cards(qtbot):
    dialog = ResultsDialog(_result(42), undo_callback=lambda ids: len(ids))
    qtbot.addWidget(dialog)
    assert dialog._undo_button.text() == "Undo (42 cards)"
    dialog._on_undo_done(1)
    assert dialog._undo_button.text() == "Undone (1 card deleted)"


def test_the_confirmation_counts_cards(qtbot, monkeypatch):
    asked: list[str] = []

    def fake_question(parent, title, text, *args, **kwargs):
        asked.append(text)
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", fake_question)
    dialog = ResultsDialog(_result(5), undo_callback=lambda ids: len(ids))
    qtbot.addWidget(dialog)

    dialog._on_undo_clicked()

    assert asked == ["Delete 5 cards from Anki? This cannot be undone; those words become mineable again."]


def test_copy_summary_is_offered_when_opened_from_a_receipt(qtbot):
    receipt = InlineReceipt()
    qtbot.addWidget(receipt)
    receipt.show_receipt(
        RunReceipt(
            outcome=TerminalOutcome.SUCCESS,
            items_total=1,
            items_completed=1,
            items_failed=0,
            notes_added=3,
            note_ids=(1, 2, 3),
            duration=ActiveDuration(active_s=5.0, suspended_s=0.0),
            results=(_result(),),
        )
    )
    opened: list[ResultsDialog] = []

    def open_details() -> None:
        dialog = ResultsDialog(_result())
        qtbot.addWidget(dialog)
        opened.append(dialog)

    receipt.details_requested.connect(open_details)
    receipt.details_button.click()

    dialog = opened[0]
    assert dialog._copy_button is not None
    dialog._copy_button.click()
    clipboard = QApplication.clipboard()
    assert clipboard is not None
    assert clipboard.text() == receipt.summary_text


def test_no_copy_summary_without_a_receipt(qtbot):
    dialog = ResultsDialog(_result())
    qtbot.addWidget(dialog)
    assert dialog._copy_button is None
