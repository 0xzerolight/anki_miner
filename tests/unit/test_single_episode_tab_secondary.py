"""Tests for the secondary-language subtitle inputs on Video -> Single (F7)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.gui.widgets.single_episode_tab import SingleEpisodeTab

_WORKER = "anki_miner.gui.widgets.single_episode_tab.EpisodeWorkerThread"
_FACTORY = "anki_miner.gui.widgets.single_episode_tab.create_episode_processor"


def _tab(qtbot, config) -> SingleEpisodeTab:
    widget = SingleEpisodeTab(
        config=config, presenter=MagicMock(name="Presenter"), progress_callback=MagicMock(name="ProgressCallback")
    )
    qtbot.addWidget(widget)
    return widget


def _point(selector, path: Path, *, valid: bool = True) -> None:
    selector.get_path = MagicMock(return_value=str(path))
    selector.path_or_none = MagicMock(return_value=str(path))
    selector.is_valid = MagicMock(return_value=valid)


def _start(tab) -> MagicMock:
    with patch(_WORKER, return_value=MagicMock()) as worker_cls, patch(_FACTORY, return_value=MagicMock()):
        tab._start_processing()
    return worker_cls


def test_rows_are_hidden_until_the_setting_is_on(qtbot, test_config):
    tab = _tab(qtbot, test_config)
    assert not tab.secondary_selector.isVisibleTo(tab)
    assert not tab.secondary_offset_row.isVisibleTo(tab)
    tab.update_config(replace(test_config, secondary_subtitle_enabled=True))
    assert tab.secondary_selector.isVisibleTo(tab)
    assert tab.secondary_offset_row.isVisibleTo(tab)
    tab.update_config(test_config)
    assert not tab.secondary_selector.isVisibleTo(tab)


def test_start_forwards_the_second_track_and_its_offset(qtbot, test_config, tmp_path):
    tab = _tab(qtbot, replace(test_config, secondary_subtitle_enabled=True))
    _point(tab.video_selector, tmp_path / "ep01.mkv")
    _point(tab.subtitle_selector, tmp_path / "ep01.ass")
    _point(tab.secondary_selector, tmp_path / "ep01.en.srt")
    tab.secondary_offset_spinbox.setValue(-1.5)

    worker_cls = _start(tab)

    kwargs = worker_cls.call_args.kwargs
    assert kwargs["secondary_subtitle_file"] == tmp_path / "ep01.en.srt"
    assert kwargs["secondary_subtitle_offset"] == -1.5
    assert tab._curation_secondary == tmp_path / "ep01.en.srt"
    assert tab._curation_secondary_offset == -1.5


def test_a_path_left_in_the_hidden_picker_is_ignored(qtbot, test_config, tmp_path):
    tab = _tab(qtbot, test_config)  # feature off
    _point(tab.video_selector, tmp_path / "ep01.mkv")
    _point(tab.subtitle_selector, tmp_path / "ep01.ass")
    _point(tab.secondary_selector, tmp_path / "ep01.en.srt")

    worker_cls = _start(tab)

    assert worker_cls.call_args.kwargs["secondary_subtitle_file"] is None
    assert tab._curation_secondary is None


def test_a_missing_second_file_refuses_the_run(qtbot, test_config, tmp_path):
    tab = _tab(qtbot, replace(test_config, secondary_subtitle_enabled=True))
    _point(tab.video_selector, tmp_path / "ep01.mkv")
    _point(tab.subtitle_selector, tmp_path / "ep01.ass")
    _point(tab.secondary_selector, tmp_path / "gone.srt", valid=False)
    shown: list = []
    tab.show_screen_issue = lambda issue, **_k: shown.append(issue)  # type: ignore[method-assign]

    worker_cls = _start(tab)

    worker_cls.assert_not_called()
    assert shown and "translation subtitle" in shown[0].summary.lower()


def test_curation_context_carries_the_second_track(qtbot, test_config, tmp_path, monkeypatch):
    tab = _tab(qtbot, replace(test_config, secondary_subtitle_enabled=True))
    captured: dict = {}

    def _fake_make(config, video, subtitle, offset, audio_track_override=None, **kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr(tab, "_make_curation_media_context", _fake_make)
    tab._curation_video = tmp_path / "ep01.mkv"
    tab._curation_subtitle = tmp_path / "ep01.ass"
    tab._curation_secondary = tmp_path / "ep01.en.srt"
    tab._curation_secondary_offset = 0.5

    tab._build_curation_context()

    assert captured == {"secondary_subtitle": tmp_path / "ep01.en.srt", "secondary_offset": 0.5}


def test_recent_pair_restores_the_second_track(qtbot, test_config, tmp_path):
    tab = _tab(qtbot, replace(test_config, secondary_subtitle_enabled=True))
    tab.recent_manager._file_path = tmp_path / "recent.json"
    tab.recent_manager.add_entry(
        tmp_path / "v.mkv", tmp_path / "s.ass", 1.0, secondary_subtitle=tmp_path / "s.en.srt", secondary_offset=-2.0
    )
    tab._refresh_recent_combo()

    tab.recent_combo.setCurrentIndex(1)

    assert tab.secondary_selector.get_path() == str(tmp_path / "s.en.srt")
    assert tab.secondary_offset_spinbox.value() == -2.0


def test_a_finished_run_records_the_second_track_and_clears_its_picker(qtbot, test_config, tmp_path):
    """Modelled on test_processing_finished_uses_run_snapshot_and_preserves_new_selection."""
    tab = _tab(qtbot, replace(test_config, secondary_subtitle_enabled=True))
    video, sub, second = tmp_path / "a.mkv", tmp_path / "a.srt", tmp_path / "a.en.srt"
    for path in (video, sub, second):
        path.touch()
    tab.video_selector.set_path(str(video))
    tab.subtitle_selector.set_path(str(sub))
    tab.secondary_selector.set_path(str(second))
    tab.secondary_offset_spinbox.setValue(-2.0)
    tab.recent_manager = MagicMock(name="RecentFilesManager")
    tab.recent_manager.get_recent.return_value = []
    _start(tab)
    result = MagicMock(name="ProcessingResult")
    result.success = True
    result.cards_created = 1

    tab._on_processing_finished(result)

    tab.recent_manager.add_entry.assert_called_once_with(
        video, sub, 0.0, secondary_subtitle=second, secondary_offset=-2.0
    )
    assert tab.secondary_selector.get_path() == ""  # the run owned it, so it was cleared
