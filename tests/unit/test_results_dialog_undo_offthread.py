"""FIX G4: ResultsDialog dispatches the Undo delete off the GUI thread.

The undo callback (AnkiConnect ``delete_notes`` + known-words revert) can block
on a slow AnkiConnect call; running it synchronously inside the modal dialog
froze the GUI. It must now run via ``run_off_thread`` — button disabled in
flight, updated from the done/error callbacks.
"""

from __future__ import annotations

import threading

import pytest

from anki_miner.models import ProcessingResult


@pytest.fixture
def result():
    return ProcessingResult(
        total_words_found=2,
        new_words_found=2,
        cards_created=2,
        card_ids=[1, 2],
    )


def _make_dialog(qtbot, result, undo_callback, on_undo_committed=None):
    from anki_miner.gui.widgets.dialogs.results_dialog import ResultsDialog

    dialog = ResultsDialog(result, None, undo_callback=undo_callback, on_undo_committed=on_undo_committed)
    qtbot.addWidget(dialog)
    return dialog


def test_undo_runs_off_gui_thread(qtbot, monkeypatch, result):
    """The blocking undo callback runs on a worker thread, not the GUI thread."""
    from PyQt6.QtWidgets import QMessageBox

    from anki_miner.gui.widgets.dialogs import results_dialog as rd_module

    monkeypatch.setattr(rd_module.QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)

    gui_ident = threading.get_ident()
    captured: dict = {}

    def undo_callback(note_ids):
        captured["thread"] = threading.get_ident()
        captured["ids"] = list(note_ids)
        return len(note_ids)

    committed: list[int] = []
    dialog = _make_dialog(qtbot, result, undo_callback, on_undo_committed=committed.append)

    dialog._on_undo_clicked()

    # Disabled the instant work is dispatched (before it can finish).
    assert not dialog._undo_button.isEnabled()

    qtbot.waitUntil(lambda: dialog.undo_completed, timeout=3000)

    assert captured["thread"] != gui_ident, "delete must run off the GUI thread"
    assert captured["ids"] == [1, 2]
    assert committed == [2], "session card count decremented on the GUI thread"
    assert dialog.undo_completed is True


def test_undo_error_reenables_button_and_surfaces(qtbot, monkeypatch, result):
    """A delete failure re-enables the button and surfaces the existing error dialog."""
    from PyQt6.QtWidgets import QMessageBox

    from anki_miner.gui.widgets.dialogs import results_dialog as rd_module

    monkeypatch.setattr(rd_module.QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    crit: list = []
    monkeypatch.setattr(rd_module.QMessageBox, "critical", lambda *a, **k: crit.append(a))

    def undo_callback(note_ids):
        raise RuntimeError("anki down")

    dialog = _make_dialog(qtbot, result, undo_callback)
    dialog._on_undo_clicked()

    qtbot.waitUntil(lambda: dialog._undo_button.isEnabled(), timeout=3000)
    assert dialog.undo_completed is False
    assert crit, "error surfaced via QMessageBox.critical"
