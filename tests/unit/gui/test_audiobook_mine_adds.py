"""A15, A04, A21, A20 on Audiobooks."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QLabel


def _pair(tmp_path, stem: str = "book"):
    audio = tmp_path / f"{stem}.m4b"
    subtitle = tmp_path / f"{stem}.srt"
    audio.touch()
    subtitle.touch()
    return audio, subtitle


def _issue(tab):
    banner = tab.issue_banner()
    assert banner is not None
    return banner.current_issue()


def test_mine_is_offered_on_an_empty_screen(audiobook_tab):
    assert audiobook_tab.mine_button.isEnabled()


def test_mine_adds_the_picked_pair_then_runs(audiobook_tab, tmp_path):
    tab = audiobook_tab
    audio, subtitle = _pair(tmp_path)
    tab.audio_selector.set_path(str(audio))
    assert tab.subtitle_selector.get_path() == str(subtitle)  # auto-filled sibling

    tab._on_mine_clicked()

    items = tab._queue.all_items()
    assert [item.audio_file for item in items] == [audio]
    assert tab.worker_thread is not None


def test_mine_with_nothing_explains_itself(audiobook_tab):
    audiobook_tab._on_mine_clicked()

    assert _issue(audiobook_tab).summary == "Pick an audio file and its subtitle, then Mine."
    assert audiobook_tab.worker_thread is None


def test_a_missing_subtitle_is_a_banner(audiobook_tab, tmp_path):
    audio = tmp_path / "lonely.m4b"
    audio.touch()
    audiobook_tab.audio_selector.set_path(str(audio))

    audiobook_tab._on_add_clicked()

    assert _issue(audiobook_tab).summary == "Choose a subtitle file first."


def test_a_vanished_audio_file_is_a_banner_with_the_path_under_details(audiobook_tab, tmp_path):
    audiobook_tab.audio_selector.set_path(str(tmp_path / "gone.m4b"))
    subtitle = tmp_path / "gone.srt"
    subtitle.touch()
    audiobook_tab.subtitle_selector.set_path(str(subtitle))

    audiobook_tab._on_add_clicked()

    issue = _issue(audiobook_tab)
    assert issue.summary == "That audio file no longer exists."
    assert "gone.m4b" in issue.details


def test_the_run_is_called_audiobook_mining(audiobook_tab):
    assert audiobook_tab._run_strings.task_title == "Audiobook mining"


def test_no_heading_repeats_the_tab_name(audiobook_tab):
    texts = [label.text() for label in audiobook_tab.findChildren(QLabel)]
    assert "Audio queue" not in texts
    assert "Pick an audio file and its subtitle, then Mine. Use Add to queue several." in texts
