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


# ---------------------------------------------------------------------------
# 5. The tab's own auto-fill follows the video (BA-003)
# ---------------------------------------------------------------------------


def test_video_change_replaces_the_tabs_own_autofilled_subtitle(tab, tmp_path):
    """BA-003: the subtitle this tab auto-filled follows the video to its new sibling."""
    for name in ("Show - 01.mkv", "Show - 01.srt", "Show - 02.mkv", "Show - 02.srt"):
        (tmp_path / name).touch()

    tab.video_selector.set_path(str(tmp_path / "Show - 01.mkv"))
    assert tab.subtitle_selector.get_path() == str(tmp_path / "Show - 01.srt")

    tab.video_selector.set_path(str(tmp_path / "Show - 02.mkv"))

    assert tab.subtitle_selector.get_path() == str(tmp_path / "Show - 02.srt")


def test_video_change_clears_autofill_when_new_video_has_no_sibling(tab, tmp_path):
    """BA-003: an auto-filled subtitle never outlives its video when the new one has no sibling."""
    for name in ("Show - 01.mkv", "Show - 01.srt", "Other.mkv"):
        (tmp_path / name).touch()

    tab.video_selector.set_path(str(tmp_path / "Show - 01.mkv"))
    tab.video_selector.set_path(str(tmp_path / "Other.mkv"))

    assert tab.subtitle_selector.get_path().strip() == ""


def test_video_change_keeps_a_subtitle_picked_after_the_autofill(tab, tmp_path):
    """BA-003: once the user replaces the auto-fill by hand, a video change keeps their choice."""
    for name in ("Show - 01.mkv", "Show - 01.srt", "Show - 02.mkv", "Show - 02.srt", "custom.ass"):
        (tmp_path / name).touch()

    tab.video_selector.set_path(str(tmp_path / "Show - 01.mkv"))
    tab.subtitle_selector.set_path(str(tmp_path / "custom.ass"))
    tab.video_selector.set_path(str(tmp_path / "Show - 02.mkv"))

    assert tab.subtitle_selector.get_path() == str(tmp_path / "custom.ass")
