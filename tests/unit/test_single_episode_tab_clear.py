"""Tests for SingleEpisodeTab file selector clear behavior (Issue #51).

Selectors are cleared only on successful non-preview runs:
- Failed/cancelled results (result.success is False) keep paths so the user
  can retry without re-picking files.
- Preview runs (result.success is True but _last_run_was_preview is True)
  keep paths so the preview-then-process flow works without re-selecting.
- Only a successful Process run clears the selectors and adds to recents.
Failed runs (failed result OR error signal) keep paths AND the audio-track
override so the user can retry without re-picking files or re-selecting a
track.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.gui.widgets.single_episode_tab import SingleEpisodeTab
from anki_miner.models.processing import ProcessingResult


@pytest.fixture
def tab(qapp, qtbot, test_config):
    widget = SingleEpisodeTab(
        config=test_config,
        presenter=MagicMock(name="Presenter"),
        progress_callback=MagicMock(name="ProgressCallback"),
    )
    qtbot.addWidget(widget)
    yield widget
    widget.deleteLater()


def test_processing_finished_clears_both_file_selectors(tab):
    """Regression for bug 3: video + subtitle inputs must clear on success."""
    tab.video_selector.set_path("/tmp/video.mkv")
    tab.subtitle_selector.set_path("/tmp/subs.ass")
    assert tab.video_selector.get_path() == "/tmp/video.mkv"
    assert tab.subtitle_selector.get_path() == "/tmp/subs.ass"

    recent_manager = MagicMock(name="RecentManager")
    recent_manager.get_recent.return_value = []
    tab.recent_manager = recent_manager

    success_result = ProcessingResult(
        total_words_found=5,
        new_words_found=3,
        cards_created=3,
    )
    tab._on_processing_finished(result=success_result)

    assert tab.video_selector.get_path() == ""
    assert tab.subtitle_selector.get_path() == ""
    recent_manager.add_entry.assert_called_once()


def test_processing_error_does_not_clear_file_selectors(tab):
    """Error path must preserve paths so user can retry without re-picking."""
    tab.video_selector.set_path("/tmp/video.mkv")
    tab.subtitle_selector.set_path("/tmp/subs.ass")

    tab._on_processing_error("boom")

    assert tab.video_selector.get_path() == "/tmp/video.mkv"
    assert tab.subtitle_selector.get_path() == "/tmp/subs.ass"


def test_failed_result_does_not_clear_file_selectors(tab):
    """A failed ProcessingResult must not clear selectors (Issue #51)."""
    tab.video_selector.set_path("/tmp/video.mkv")
    tab.subtitle_selector.set_path("/tmp/subs.ass")
    tab._audio_track_override = 2
    tab.recent_manager = MagicMock(name="RecentManager")

    failed_result = ProcessingResult(
        total_words_found=0,
        new_words_found=0,
        cards_created=0,
        errors=["Error: deck missing"],
    )
    assert not failed_result.success

    tab._on_processing_finished(result=failed_result)

    assert tab.video_selector.get_path() == "/tmp/video.mkv"
    assert tab.subtitle_selector.get_path() == "/tmp/subs.ass"
    assert tab._audio_track_override == 2, "audio track override must survive a failed run for retry"
    tab.recent_manager.add_entry.assert_not_called()


def test_preview_success_does_not_clear_file_selectors(tab):
    """A successful preview run must not clear selectors (preview-then-process flow)."""
    tab.video_selector.set_path("/tmp/video.mkv")
    tab.subtitle_selector.set_path("/tmp/subs.ass")
    tab._last_run_was_preview = True

    recent_manager = MagicMock(name="RecentManager")
    recent_manager.get_recent.return_value = []
    tab.recent_manager = recent_manager

    success_result = ProcessingResult(
        total_words_found=10,
        new_words_found=5,
        cards_created=3,
    )
    assert success_result.success

    tab._on_processing_finished(result=success_result)

    assert tab.video_selector.get_path() == "/tmp/video.mkv"
    assert tab.subtitle_selector.get_path() == "/tmp/subs.ass"


def test_preview_success_preserves_audio_track_override(tab):
    """Preview keeps the audio track override; Process resets it."""
    recent_manager = MagicMock(name="RecentManager")
    recent_manager.get_recent.return_value = []
    tab.recent_manager = recent_manager

    # Preview run: override must survive
    tab._audio_track_override = 2
    tab._last_run_was_preview = True
    success_result = ProcessingResult(
        total_words_found=10,
        new_words_found=5,
        cards_created=3,
    )
    tab._on_processing_finished(result=success_result)
    assert tab._audio_track_override == 2

    # Non-preview run: override must be reset
    tab._last_run_was_preview = False
    tab._on_processing_finished(result=success_result)
    assert tab._audio_track_override is None
