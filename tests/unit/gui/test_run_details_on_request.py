"""The results dialog opens because a user asked, never because a run finished.

Before this, every result the presenter forwarded executed a modal dialog — one
per successful item, so a twenty-item queue produced twenty of them. The window
still counts the session's notes from those results; it just stops interrupting.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.models import ProcessingResult


@pytest.fixture
def main_window(qtbot, patch_heavy_init, test_config):
    patch_heavy_init(test_config)
    from anki_miner.gui.main_window import MainWindow

    window = MainWindow()
    qtbot.addWidget(window)
    yield window
    window.deleteLater()


@pytest.fixture
def opened(monkeypatch) -> list:
    """Record every ResultsDialog construction without running one."""
    from anki_miner.gui import main_window as mw_module

    built: list = []

    class _FakeDialog:
        undo_completed = False

        def __init__(self, result, parent, undo_callback=None, on_undo_committed=None):
            built.append(result)
            self.undo_callback = undo_callback

        def exec(self):
            return 0

    monkeypatch.setattr(mw_module, "ResultsDialog", _FakeDialog)
    return built


def _result(cards: int = 3) -> ProcessingResult:
    return ProcessingResult(
        total_words_found=10,
        new_words_found=cards,
        cards_created=cards,
        card_ids=list(range(cards)),
    )


def test_a_finished_item_opens_no_dialog(main_window, opened):
    main_window._on_processing_result(_result())

    assert opened == []


def test_a_twenty_item_queue_opens_no_dialogs(main_window, opened):
    for _ in range(20):
        main_window._on_processing_result(_result(1))

    assert opened == []


def test_the_session_note_count_still_follows_every_result(main_window, opened):
    before = main_window.status_bar._cards_created_session

    main_window._on_processing_result(_result(4))
    main_window._on_processing_result(_result(2))

    assert main_window.status_bar._cards_created_session == before + 6


def test_view_details_opens_the_dialog_for_that_run(main_window, opened):
    run = _result(5)

    main_window._on_run_details(run)

    assert opened == [run]


def test_the_presenter_routes_a_details_request_to_the_window(main_window, opened):
    run = _result(2)

    main_window.presenter.show_run_details(run)

    assert opened == [run]
