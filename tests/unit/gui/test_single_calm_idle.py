"""A05: a new user's Single screen asks for one thing, a video."""

from __future__ import annotations

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtWidgets import QLabel


def _row_layout_of(widget):
    """The box layout that holds ``widget`` directly."""
    parent = widget.parentWidget()
    pending = [parent.layout()]
    while pending:
        layout = pending.pop()
        if layout.indexOf(widget) >= 0:
            return layout
        for index in range(layout.count()):
            child = layout.itemAt(index).layout()
            if child is not None:
                pending.append(child)
    raise AssertionError("widget is in no layout")


def test_recent_files_hidden_while_there_is_no_history(single_tab, monkeypatch):
    # Pinned to an empty history: another test in the same worker may have
    # recorded a finished run in the isolated home.
    monkeypatch.setattr(single_tab.recent_manager, "get_recent", lambda: [])

    single_tab._refresh_recent_combo()

    assert single_tab.recent_combo.count() == 1
    assert single_tab.recent_row.isHidden()


def test_recent_files_shown_once_there_is_history(single_tab, monkeypatch, tmp_path):
    entry = {"video": str(tmp_path / "a.mkv"), "subtitle": str(tmp_path / "a.srt")}
    monkeypatch.setattr(single_tab.recent_manager, "get_recent", lambda: [entry])

    single_tab._refresh_recent_combo()

    assert not single_tab.recent_row.isHidden()


def test_one_helper_line_while_both_pickers_are_empty(single_tab, tmp_path):
    assert not single_tab.empty_hint.isHidden()
    assert (
        single_tab.empty_hint.text() == "Choose a video. A subtitle file with the same name is picked up automatically."
    )

    video = tmp_path / "ep01.mkv"
    video.touch()
    single_tab.video_selector.set_path(str(video))

    assert single_tab.empty_hint.isHidden()


def test_card_source_and_audio_track_wait_for_a_video(single_tab, tmp_path):
    assert single_tab.card_source_row.isHidden()
    assert single_tab.tracks_button.isHidden()

    video = tmp_path / "ep01.mkv"
    video.touch()
    single_tab.video_selector.set_path(str(video))

    assert not single_tab.card_source_row.isHidden()
    assert not single_tab.tracks_button.isHidden()
    assert single_tab.tracks_button.text() == "Audio track…"


def test_timing_and_audio_track_sit_on_the_offset_row(single_tab):
    row = _row_layout_of(single_tab.offset_spinbox)
    spin = row.indexOf(single_tab.offset_spinbox)
    assert row.indexOf(single_tab.timing_button) > spin
    assert row.indexOf(single_tab.tracks_button) > spin


def test_no_orphan_headings(single_tab):
    texts = [label.text() for label in single_tab.findChildren(QLabel)]
    assert "Actions" not in texts
    assert "File Selection" not in texts


def test_a_run_hides_both_buttons_and_restore_brings_back_what_applies(single_tab):
    single_tab._is_processing = True
    single_tab._refresh_input_rows()
    assert single_tab.timing_button.isHidden()

    single_tab._restore_buttons()

    assert not single_tab.timing_button.isHidden()
    assert single_tab.tracks_button.isHidden()  # still no video chosen
