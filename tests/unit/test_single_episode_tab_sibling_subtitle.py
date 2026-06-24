"""Tests for SingleEpisodeTab sibling-subtitle auto-fill (Task 7)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.gui.widgets.single_episode_tab import SingleEpisodeTab


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


# ---------------------------------------------------------------------------
# 1. Auto-fills subtitle selector when empty and sibling exists
# ---------------------------------------------------------------------------


def test_video_path_change_autofills_subtitle_when_empty(tab, tmp_path):
    """Picking a video auto-fills the subtitle selector from a sibling .srt."""
    video = tmp_path / "ep01.mkv"
    video.touch()
    srt = tmp_path / "ep01.srt"
    srt.touch()

    # Subtitle selector starts empty.
    assert tab.subtitle_selector.get_path().strip() == ""

    tab.video_selector.set_path(str(video))

    assert tab.subtitle_selector.get_path() == str(srt)


def test_video_path_change_autofills_prefers_ass_over_srt(tab, tmp_path):
    """.ass is preferred over .srt when both siblings exist."""
    video = tmp_path / "ep01.mkv"
    video.touch()
    (tmp_path / "ep01.ass").touch()
    (tmp_path / "ep01.srt").touch()

    tab.video_selector.set_path(str(video))

    assert tab.subtitle_selector.get_path() == str(tmp_path / "ep01.ass")


# ---------------------------------------------------------------------------
# 2. Does NOT overwrite a subtitle the user already chose
# ---------------------------------------------------------------------------


def test_video_path_change_does_not_overwrite_existing_subtitle(tab, tmp_path):
    """If the subtitle selector already has a value, auto-fill must not touch it."""
    video = tmp_path / "ep01.mkv"
    video.touch()
    srt = tmp_path / "ep01.srt"
    srt.touch()

    # User has already picked a different subtitle.
    existing = tmp_path / "ep02.ass"
    existing.touch()
    tab.subtitle_selector.set_path(str(existing))

    tab.video_selector.set_path(str(video))

    # Must still point at the user's choice.
    assert tab.subtitle_selector.get_path() == str(existing)


# ---------------------------------------------------------------------------
# 3. No sibling → subtitle selector stays empty
# ---------------------------------------------------------------------------


def test_video_path_change_no_sibling_leaves_subtitle_empty(tab, tmp_path):
    """When no sibling subtitle exists, the selector remains empty."""
    video = tmp_path / "ep01.mkv"
    video.touch()

    tab.video_selector.set_path(str(video))

    assert tab.subtitle_selector.get_path().strip() == ""


# ---------------------------------------------------------------------------
# 4. Empty video path does not raise
# ---------------------------------------------------------------------------


def test_video_path_change_empty_string_is_safe(tab):
    """Emitting an empty path via path_changed must not raise."""
    tab.video_selector.path_changed.emit("")
    # No exception → pass
