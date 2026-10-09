"""A04 + D1 for Reading: one banner for all five sub-tabs, and progress in the pinned bar."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.exceptions import SetupError
from anki_miner.gui.controllers.task_registry import TaskRegistry
from anki_miner.gui.widgets.reading_deck_tab import ReadingDeckTab
from anki_miner.gui.widgets.reading_manga_tab import ReadingMangaTab
from anki_miner.gui.widgets.reading_novels_tab import ReadingNovelsTab
from anki_miner.gui.widgets.reading_subtitles_tab import ReadingSubtitlesTab
from anki_miner.gui.widgets.reading_text_tab import ReadingTextTab

_WORKER_TARGET = "anki_miner.gui.widgets._reading_mining_base.ReadingQueueWorker"


def _build(qtbot, cls, config):
    widget = cls(config=config, processor=MagicMock(name="EpisodeProcessor"), presenter=MagicMock(name="Presenter"))
    qtbot.addWidget(widget)
    return widget


@pytest.fixture
def tab(qtbot, test_config):
    with patch(_WORKER_TARGET) as worker_cls:
        worker_cls.side_effect = lambda *a, **kw: MagicMock(name="QueueWorker")
        yield _build(qtbot, ReadingTextTab, test_config)


@pytest.fixture
def registry(qtbot):
    reg = TaskRegistry()
    yield reg
    reg.shutdown()


@pytest.mark.parametrize(
    "cls", [ReadingMangaTab, ReadingNovelsTab, ReadingSubtitlesTab, ReadingTextTab, ReadingDeckTab]
)
def test_every_reading_tab_has_a_banner(qtbot, test_config, cls):
    widget = _build(qtbot, cls, test_config)
    assert widget.issue_banner() is not None


@pytest.mark.parametrize(
    "cls", [ReadingMangaTab, ReadingNovelsTab, ReadingSubtitlesTab, ReadingTextTab, ReadingDeckTab]
)
def test_a_run_that_fails_because_anki_is_closed_says_so_in_the_banner(qtbot, test_config, cls):
    """Review focus (Anki not running): a Reading run's fatal error reaches the new banner.

    The five Reading sub-tabs had no banner before A04, so a closed Anki was
    reported only in the Activity drawer. The queue base's _on_run_error now
    calls _show_run_failure (T1.09); this proves every Reading tab has the
    banner it lands on and keeps the translated summary for a closed Anki.
    """
    with patch(_WORKER_TARGET) as worker_cls:
        worker_cls.side_effect = lambda *a, **kw: MagicMock(name="QueueWorker")
        widget = _build(qtbot, cls, test_config)

        widget._on_run_error("Cannot connect to AnkiConnect. Is Anki running?")

        issue = widget.issue_banner().current_issue()
        assert issue is not None
        assert issue.summary == "Cannot connect to AnkiConnect. Is Anki running?"
        assert issue.details == "Cannot connect to AnkiConnect. Is Anki running?"
        assert "Cannot connect to AnkiConnect" in widget.log_widget.text_edit.toPlainText()


def test_a_refusal_reaches_the_banner_and_activity(tab):
    tab._report_refusal("Paste some text first.")

    issue = tab.issue_banner().current_issue()
    assert issue.summary == "Paste some text first."
    assert "Paste some text first." in tab.log_widget.text_edit.toPlainText()


def test_an_unrecognised_file_is_a_plain_sentence(tab, tmp_path):
    odd = tmp_path / "notes.docx"
    odd.touch()

    assert tab._detect_or_report(odd) is None

    issue = tab.issue_banner().current_issue()
    assert issue.summary == "Anki Miner can't mine this file."
    assert "not a recognized reading source" in issue.details


def test_an_unmineable_folder_is_called_a_folder(tab, tmp_path):
    folder = tmp_path / "books"
    folder.mkdir()

    assert tab._detect_or_report(folder, detect_fn=MagicMock(side_effect=SetupError("No books in it."))) is None

    issue = tab.issue_banner().current_issue()
    assert issue.summary == "Anki Miner can't mine this folder."
    assert issue.details == "No books in it."


def test_the_status_line_reaches_the_pinned_bar(tab, registry):
    tab.bind_task_registry(registry)
    tab._publish_task_start("Text mining", total=2)

    tab._publish_reading_status("Extracting media")
    tab._run_items = [object(), object()]
    tab._publish_reading_done(1)

    snapshot = registry.snapshot(tab.TASK_ID)
    assert snapshot is not None
    assert snapshot.detail == "Extracting media"
    assert (snapshot.current, snapshot.total) == (1, 2)
    assert tab.action_bar.stage_label.full_text == "Extracting media"
