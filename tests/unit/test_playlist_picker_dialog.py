"""Entry picker shown after a Download playlist probe."""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import Qt  # noqa: E402

from anki_miner.gui.widgets.dialogs.playlist_picker_dialog import PlaylistPickerDialog  # noqa: E402
from anki_miner.services.media_downloader import (  # noqa: E402
    DownloadPlaylist,
    DownloadPlaylistEntry,
)


def _playlist(n: int = 3, total: int | None = None) -> DownloadPlaylist:
    entries = tuple(DownloadPlaylistEntry(i, f"Video {i}", f"https://example.com/{i}", 60 + i) for i in range(1, n + 1))
    return DownloadPlaylist("My List", entries, total if total is not None else n)


def _make(qtbot: Any, playlist: DownloadPlaylist | None = None, truncated: bool = False) -> Any:
    dialog = PlaylistPickerDialog(playlist or _playlist(), truncated=truncated)
    qtbot.addWidget(dialog)
    return dialog


def test_every_entry_starts_checked(qtbot: Any) -> None:
    dialog = _make(qtbot)
    assert dialog.selected_urls() == [f"https://example.com/{i}" for i in (1, 2, 3)]


def test_rows_show_index_title_and_duration(qtbot: Any) -> None:
    dialog = _make(qtbot)
    text = dialog.entry_list.item(0).text()
    assert text.startswith("1.")
    assert "Video 1" in text
    assert "1:01" in text


def test_a_missing_duration_renders_without_a_time(qtbot: Any) -> None:
    playlist = DownloadPlaylist("L", (DownloadPlaylistEntry(1, "V", "https://example.com/1", None),), 1)
    dialog = _make(qtbot, playlist)
    assert dialog.entry_list.item(0).text().strip() == "1. V"


def test_a_long_entry_renders_hours(qtbot: Any) -> None:
    playlist = DownloadPlaylist("L", (DownloadPlaylistEntry(1, "V", "https://example.com/1", 3725),), 1)
    dialog = _make(qtbot, playlist)
    assert "1:02:05" in dialog.entry_list.item(0).text()


def test_unchecking_removes_the_url(qtbot: Any) -> None:
    dialog = _make(qtbot)
    dialog.entry_list.item(1).setCheckState(Qt.CheckState.Unchecked)
    assert dialog.selected_urls() == ["https://example.com/1", "https://example.com/3"]


def test_select_none_then_all(qtbot: Any) -> None:
    dialog = _make(qtbot)
    dialog.select_none_button.click()
    assert dialog.selected_urls() == []
    dialog.select_all_button.click()
    assert len(dialog.selected_urls()) == 3


def test_range_expression_replaces_the_selection(qtbot: Any) -> None:
    dialog = _make(qtbot, _playlist(5))
    dialog.range_edit.setText("2-3,5")
    dialog.apply_range_button.click()
    assert dialog.selected_urls() == [f"https://example.com/{i}" for i in (2, 3, 5)]


def test_a_malformed_range_is_reported_and_changes_nothing(qtbot: Any) -> None:
    dialog = _make(qtbot, _playlist(5))
    dialog.range_edit.setText("nope")
    dialog.apply_range_button.click()
    assert len(dialog.selected_urls()) == 5
    assert dialog.range_error_label.isHidden() is False


def test_a_valid_range_clears_a_previous_error(qtbot: Any) -> None:
    dialog = _make(qtbot, _playlist(5))
    dialog.range_edit.setText("nope")
    dialog.apply_range_button.click()
    dialog.range_edit.setText("1-2")
    dialog.apply_range_button.click()
    assert dialog.range_error_label.isHidden() is True


def test_accept_button_is_disabled_with_nothing_selected(qtbot: Any) -> None:
    dialog = _make(qtbot)
    dialog.select_none_button.click()
    assert dialog.add_button.isEnabled() is False
    dialog.select_all_button.click()
    assert dialog.add_button.isEnabled() is True


def test_accept_button_counts_the_selection(qtbot: Any) -> None:
    dialog = _make(qtbot)
    dialog.entry_list.item(0).setCheckState(Qt.CheckState.Unchecked)
    assert "2" in dialog.add_button.text()


def test_truncation_notice_is_shown_only_when_truncated(qtbot: Any) -> None:
    assert _make(qtbot, _playlist(3, total=900), truncated=True).truncation_label.isHidden() is False
    assert _make(qtbot).truncation_label.isHidden() is True


def test_the_header_names_the_playlist_and_its_size(qtbot: Any) -> None:
    dialog = _make(qtbot, _playlist(3, total=900))
    assert "My List" in dialog.header_label.text()
    assert "900" in dialog.header_label.text()


def test_search_hides_non_matching_rows_without_dropping_them(qtbot: Any) -> None:
    dialog = _make(qtbot, _playlist(5))
    dialog.search_edit.setText("Video 2")
    assert [r for r in range(5) if not dialog.entry_list.item(r).isHidden()] == [1]
    assert len(dialog.selected_urls()) == 5
