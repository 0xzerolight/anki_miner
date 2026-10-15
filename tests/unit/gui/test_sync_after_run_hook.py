"""AI-03: a finished run that added cards hands one sync to a worker thread, never the GUI thread."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.models.processing import ProcessingResult
from anki_miner.services.anki_service import sync_after_run
from tests.unit.gui._screens import ready_youtube_item


def _result(cards: int) -> ProcessingResult:
    return ProcessingResult(
        total_words_found=cards * 3, new_words_found=cards, cards_created=cards, card_ids=list(range(cards))
    )


@pytest.fixture
def sync_calls(monkeypatch):
    """Capture run_off_thread calls whose work is sync_after_run; any other work runs as before."""
    from anki_miner.gui.widgets import _mining_tab_base

    real = _mining_tab_base.run_off_thread
    calls: list[tuple[object, object]] = []

    def fake(parent, work, on_done, on_error=None, **kwargs):
        if getattr(work, "func", None) is sync_after_run:
            calls.append((work, on_done))
            return MagicMock()
        return real(parent, work, on_done, on_error, **kwargs)

    monkeypatch.setattr(_mining_tab_base, "run_off_thread", fake)
    return calls


def _finish_run(tab, cards: int) -> None:
    ready_youtube_item(tab, "aaa")
    tab._on_mine_clicked()
    tab._on_item_finished(0, _result(cards), None, 1)
    tab._after_run_cleanup()


def test_a_run_that_added_cards_syncs_once(youtube_tab, clock, sync_calls):
    youtube_tab.config = replace(youtube_tab.config, sync_anki_after_run=True)

    _finish_run(youtube_tab, 3)
    youtube_tab._finish_receipt()  # a second terminal signal changes nothing

    assert len(sync_calls) == 1
    work, _on_done = sync_calls[0]
    assert work.args[1] == 3


def test_off_never_syncs(youtube_tab, clock, sync_calls):
    _finish_run(youtube_tab, 3)

    assert sync_calls == []


def test_a_run_without_cards_never_syncs(youtube_tab, clock, sync_calls):
    youtube_tab.config = replace(youtube_tab.config, sync_anki_after_run=True)

    _finish_run(youtube_tab, 0)

    assert sync_calls == []


def test_a_failed_sync_is_a_warning_line_in_the_activity_log(youtube_tab, clock, sync_calls):
    youtube_tab.config = replace(youtube_tab.config, sync_anki_after_run=True)
    _finish_run(youtube_tab, 2)
    _work, on_done = sync_calls[0]

    on_done(False)

    assert "Anki didn't sync after this run" in youtube_tab.log_widget.full_text()


def test_a_good_sync_writes_nothing(youtube_tab, clock, sync_calls):
    youtube_tab.config = replace(youtube_tab.config, sync_anki_after_run=True)
    _finish_run(youtube_tab, 2)
    before = youtube_tab.log_widget.full_text()
    _work, on_done = sync_calls[0]

    on_done(True)

    assert youtube_tab.log_widget.full_text() == before
