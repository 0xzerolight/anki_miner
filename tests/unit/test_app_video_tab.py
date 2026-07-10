"""Smoke test: VideoTab is registered in the main() wiring.

Reuses the ``_build_tabs`` helper from ``test_app_deck_builder_tab`` (which
mirrors ``anki_miner.gui.app.main``'s tab-construction block) and asserts the
"Video" tab is present at index 0, correctly typed, and that it nests the
Single/Batch/YouTube sub-tabs with per-child presenters.
"""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.gui.widgets.batch_processing_tab import BatchProcessingTab
from anki_miner.gui.widgets.single_episode_tab import SingleEpisodeTab
from anki_miner.gui.widgets.video_tab import VideoTab
from anki_miner.gui.widgets.youtube_tab import YouTubeTab
from tests.unit.test_app_deck_builder_tab import _build_tabs


@pytest.fixture
def wired_window(monkeypatch, test_config, qtbot):
    window, titles, tabs = _build_tabs(monkeypatch, test_config)
    qtbot.addWidget(window)
    yield window, titles, tabs
    window.deleteLater()


def test_video_tab_present(wired_window):
    _window, titles, _tabs = wired_window
    assert "Video" in titles


def test_video_tab_is_first(wired_window):
    _window, titles, _tabs = wired_window
    assert titles.index("Video") == 0


def test_video_tab_is_correct_type(wired_window):
    _window, _titles, tabs = wired_window
    assert isinstance(tabs["Video"], VideoTab)


def test_video_tab_nests_three_inner_tabs(wired_window):
    """The container holds exactly three inner tabs: Single / Batch / YouTube."""
    _window, _titles, tabs = wired_window
    video = tabs["Video"]
    assert video._inner_tabs.count() == 3
    labels = [video._inner_tabs.tabText(i) for i in range(video._inner_tabs.count())]
    assert labels == ["Single", "Batch", "YouTube"]


def test_video_tab_inner_child_types(wired_window):
    """Inner tabs are the Single, Batch, and YouTube sub-tabs, in order."""
    _window, _titles, tabs = wired_window
    video = tabs["Video"]
    assert isinstance(video._inner_tabs.widget(0), SingleEpisodeTab)
    assert isinstance(video._inner_tabs.widget(1), BatchProcessingTab)
    assert isinstance(video._inner_tabs.widget(2), YouTubeTab)
    assert video.single_tab is video._inner_tabs.widget(0)
    assert video.batch_tab is video._inner_tabs.widget(1)
    assert video.youtube_tab is video._inner_tabs.widget(2)


def test_video_tab_children_have_distinct_presenters(wired_window):
    """Per-child presenters: Single/Batch wire presenter signals into their own
    log widgets, so sharing one would cross-post between sub-tabs."""
    _window, _titles, tabs = wired_window
    video = tabs["Video"]
    presenters = (
        video.single_tab.presenter,
        video.batch_tab.presenter,
        video.youtube_tab._presenter,
    )
    assert all(p is not None for p in presenters)
    assert len({id(p) for p in presenters}) == 3
