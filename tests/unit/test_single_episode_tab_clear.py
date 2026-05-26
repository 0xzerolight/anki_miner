"""Tests for SingleEpisodeTab file selector clear (Issue: paths stuck after run).

The recent-files combo was refreshed after processing but video/subtitle
selectors kept their old paths. Both must clear so the next run starts
fresh. Error path is intentionally NOT cleared (preserves retry-with-
same-files affordance).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QApplication

from anki_miner.gui.widgets.single_episode_tab import SingleEpisodeTab


@pytest.fixture
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def tab(qapp, test_config):
    widget = SingleEpisodeTab(
        config=test_config,
        presenter=MagicMock(name="Presenter"),
        progress_callback=MagicMock(name="ProgressCallback"),
    )
    yield widget
    widget.deleteLater()


def test_processing_finished_clears_both_file_selectors(tab):
    """Regression for bug 3: video + subtitle inputs must clear on success."""
    tab.video_selector.set_path("/tmp/video.mkv")
    tab.subtitle_selector.set_path("/tmp/subs.ass")
    assert tab.video_selector.get_path() == "/tmp/video.mkv"
    assert tab.subtitle_selector.get_path() == "/tmp/subs.ass"

    tab._on_processing_finished(result=MagicMock(cards_created=3))

    assert tab.video_selector.get_path() == ""
    assert tab.subtitle_selector.get_path() == ""


def test_processing_error_does_not_clear_file_selectors(tab):
    """Error path must preserve paths so user can retry without re-picking."""
    tab.video_selector.set_path("/tmp/video.mkv")
    tab.subtitle_selector.set_path("/tmp/subs.ass")

    tab._on_processing_error("boom")

    assert tab.video_selector.get_path() == "/tmp/video.mkv"
    assert tab.subtitle_selector.get_path() == "/tmp/subs.ass"
