"""A04: a failed run explains itself in the screen banner, raw text under Details."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.models import ProcessingResult

ANKI_DOWN = "Cannot connect to AnkiConnect. Is Anki running?"


def _issue(tab):
    banner = tab.issue_banner()
    assert banner is not None
    return banner.current_issue()


def test_single_worker_error_for_a_closed_anki(single_tab):
    single_tab._on_processing_error(ANKI_DOWN)

    issue = _issue(single_tab)
    assert issue.summary == ANKI_DOWN
    assert issue.details == ANKI_DOWN


def test_single_worker_error_for_anything_else(single_tab):
    single_tab._on_processing_error("RuntimeError: disk full")

    issue = _issue(single_tab)
    assert issue.summary == "Mining failed."
    assert issue.details == "RuntimeError: disk full"


def test_single_failed_result_raises_the_banner(single_tab):
    result = ProcessingResult(total_words_found=0, new_words_found=0, cards_created=0, errors=[ANKI_DOWN])

    single_tab._on_processing_finished(result)

    assert _issue(single_tab).summary == ANKI_DOWN


def test_single_cancelled_run_raises_no_banner(single_tab):
    single_tab._cancel_requested = True
    result = ProcessingResult(total_words_found=0, new_words_found=0, cards_created=0, errors=["cancelled"])

    single_tab._on_processing_finished(result)

    assert _issue(single_tab) is None


def test_single_failure_still_says_failed_see_log(single_tab):
    single_tab._on_processing_error("boom")
    assert single_tab.progress_widget.status_label.text() == "Failed — see log"


def test_batch_run_level_failure(batch_tab):
    batch_tab._on_queue_worker_error("stale dictionary")

    issue = _issue(batch_tab)
    assert issue.summary == "Mining failed."
    assert issue.details == "stale dictionary"


def test_deck_builder_keeps_its_own_summary_but_names_a_closed_anki(qtbot, test_config):
    from anki_miner.gui.widgets.deck_builder_tab import DeckBuilderTab

    tab = DeckBuilderTab(config=test_config, presenter=MagicMock(), progress_callback=MagicMock())
    qtbot.addWidget(tab)

    tab._on_worker_error("could not create deck")
    assert _issue(tab).summary == "The deck could not be built."

    tab._on_worker_error(ANKI_DOWN)
    assert _issue(tab).summary == ANKI_DOWN


def test_queue_run_level_failure(queue_youtube_tab):
    queue_youtube_tab._on_run_error(ANKI_DOWN)

    issue = _issue(queue_youtube_tab)
    assert issue.summary == ANKI_DOWN
    assert issue.details == ANKI_DOWN
