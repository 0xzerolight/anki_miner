"""Every workflow screen keeps its run action out of the page scroll (D6-B).

One module rather than an assertion bolted onto each screen's own suite,
because the property being defended is the same sentence on all of them: the
button you press to start the job, the button you press to stop it, and the
activity log are siblings of the scroll area, not children of it.

The alternate launch actions are checked here too. Batch's *Process Folder*
and the Reading tabs' *Mine Folder* act on a specific card's inputs, so they
stay in that card; promoting them to the bar would put two run buttons on one
screen with no way to tell which folder each meant.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from PyQt6.QtWidgets import QScrollArea, QWidget

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets.audiobook_tab import AudiobookTab
from anki_miner.gui.widgets.base import WorkflowActionBar
from anki_miner.gui.widgets.batch_processing_tab import BatchProcessingTab
from anki_miner.gui.widgets.reading_manga_tab import ReadingMangaTab
from anki_miner.gui.widgets.reading_novels_tab import ReadingNovelsTab
from anki_miner.gui.widgets.reading_subtitles_tab import ReadingSubtitlesTab
from anki_miner.gui.widgets.reading_text_tab import ReadingTextTab
from anki_miner.gui.widgets.single_episode_tab import SingleEpisodeTab
from anki_miner.gui.widgets.youtube_tab import YouTubeTab


def _presenter() -> MagicMock:
    presenter = MagicMock(name="Presenter")
    for signal in ("info_signal", "success_signal", "warning_signal", "error_signal"):
        getattr(presenter, signal).connect = MagicMock()
    return presenter


def _progress_callback() -> MagicMock:
    callback = MagicMock(name="ProgressCallback")
    for signal in ("stage_signal", "start_signal", "progress_signal", "complete_signal", "error_signal"):
        getattr(callback, signal).connect = MagicMock()
    return callback


def _build(name: str, config: AnkiMinerConfig) -> QWidget:
    """Construct one workflow screen with its collaborators stubbed."""
    if name == "single":
        return SingleEpisodeTab(config, _presenter(), _progress_callback())
    if name == "batch":
        return BatchProcessingTab(config, _presenter(), _progress_callback())
    if name == "youtube":
        return YouTubeTab(config, MagicMock(name="Processor"), MagicMock(name="Fetcher"), MagicMock())
    if name == "audiobook":
        return AudiobookTab(config, MagicMock(name="Processor"), MagicMock())
    reading = {
        "manga": ReadingMangaTab,
        "novels": ReadingNovelsTab,
        "subtitles": ReadingSubtitlesTab,
        "text": ReadingTextTab,
    }[name]
    return reading(config, MagicMock(name="Processor"), MagicMock())


#: (screen, attribute of the pinned primary action, attribute of Cancel).
_SCREENS = [
    ("single", "process_button", "cancel_button"),
    ("batch", None, "cancel_button"),
    ("youtube", "mine_button", "stop_button"),
    ("audiobook", "mine_button", "stop_button"),
    ("manga", "mine_button", "cancel_button"),
    ("novels", "mine_button", "cancel_button"),
    ("subtitles", "mine_button", "cancel_button"),
    ("text", "mine_button", "cancel_button"),
]


@pytest.fixture(params=[s[0] for s in _SCREENS])
def screen(request, qtbot, test_config: AnkiMinerConfig):
    with patch("anki_miner.gui.utils.service_factory.create_episode_processor", MagicMock()):
        widget = _build(request.param, test_config)
    qtbot.addWidget(widget)
    return request.param, widget


def _primary(name: str, widget: QWidget):
    if name == "batch":
        # Batch's canonical run is the queue's own Process Queue button.
        return widget.queue_panel.process_queue_button
    return getattr(widget, {s[0]: s[1] for s in _SCREENS}[name])


def _cancel(name: str, widget: QWidget):
    return getattr(widget, {s[0]: s[2] for s in _SCREENS}[name])


def _bar(widget: QWidget) -> WorkflowActionBar:
    bars = widget.findChildren(WorkflowActionBar)
    assert len(bars) == 1, "a screen has exactly one action host"
    return bars[0]


def _ancestors(widget: QWidget) -> list[QWidget]:
    chain: list[QWidget] = []
    node = widget.parentWidget()
    while node is not None:
        chain.append(node)
        node = node.parentWidget()
    return chain


def _page_scroll(widget: QWidget) -> QScrollArea:
    scrolls = [s for s in widget.findChildren(QScrollArea) if s.objectName() == "page-scroll"]
    assert scrolls, "every workflow screen has a scrolled page column"
    return scrolls[0]


def test_primary_and_cancel_are_the_original_objects_in_the_bar(screen):
    name, widget = screen
    bar = _bar(widget)

    assert _primary(name, widget) in bar.findChildren(type(_primary(name, widget)))
    assert _ancestors(_primary(name, widget))[0] is bar
    assert _ancestors(_cancel(name, widget))[0] is bar


def test_the_bar_is_not_inside_the_page_scroll(screen):
    _name, widget = screen

    assert _page_scroll(widget) not in _ancestors(_bar(widget))


def test_the_activity_log_is_not_inside_the_page_scroll(screen):
    _name, widget = screen

    assert _page_scroll(widget) not in _ancestors(widget.log_widget)


def test_the_activity_log_starts_closed(screen):
    _name, widget = screen

    assert not _bar(widget).is_activity_open()
    # The whole drawer is hidden, so the log reserves no height at all -- the
    # 200px an empty console used to cost on every screen.
    assert widget.log_widget.parentWidget().isHidden()


def test_a_warning_does_not_open_activity_on_any_screen(screen):
    """No screen may hand its page to the drawer on its own initiative.

    A mining run warns routinely, so an auto-open on the first warning took 40%
    of the page on nearly every run. The receipt and the screen-issue banner are
    the failure channel (D24); the drawer waits to be asked.
    """
    _name, widget = screen
    bar = _bar(widget)

    widget.log_widget.append_warning("something needs attention")

    assert not bar.is_activity_open()
    assert widget.log_widget.parentWidget().isHidden()


def test_batch_keeps_process_folder_in_its_card(qtbot, test_config: AnkiMinerConfig):
    widget = BatchProcessingTab(test_config, _presenter(), _progress_callback())
    qtbot.addWidget(widget)

    assert _bar(widget) not in _ancestors(widget.process_pairs_button)


@pytest.mark.parametrize("name", ["manga", "novels"])
def test_reading_folder_runs_keep_mine_folder_in_their_card(qtbot, test_config: AnkiMinerConfig, name):
    widget = _build(name, test_config)
    qtbot.addWidget(widget)

    assert _bar(widget) not in _ancestors(widget.folder_mine_button)


@pytest.mark.parametrize("name", ["youtube", "audiobook"])
def test_queue_screens_keep_clear_with_their_list(qtbot, test_config: AnkiMinerConfig, name):
    widget = _build(name, test_config)
    qtbot.addWidget(widget)

    assert _bar(widget) not in _ancestors(widget.clear_button)


def test_single_keeps_timing_and_tracks_in_the_actions_card(qtbot, test_config: AnkiMinerConfig):
    widget = SingleEpisodeTab(test_config, _presenter(), _progress_callback())
    qtbot.addWidget(widget)

    bar = _bar(widget)
    assert bar not in _ancestors(widget.timing_button)
    assert bar not in _ancestors(widget.tracks_button)
