"""F-18 on Utilities → Video OCR: image subtitle files and a video's image tracks."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from anki_miner.gui.widgets import video_ocr_tab as mod
from anki_miner.gui.widgets.video_ocr_tab import VideoOcrTab, _EngineState
from anki_miner.services.video_ocr.region import FULL_FRAME, Region
from anki_miner.utils.audio_track_detector import SubtitleStream
from tests.unit._tool_tab_harness import OS_ACCESS, FakeToolWorker


def _make(qtbot, config) -> VideoOcrTab:
    tab = VideoOcrTab(config, suppress_optional_startup=True)
    qtbot.addWidget(tab)
    tab._apply_probe_result(_EngineState(True, True))
    return tab


@pytest.fixture
def sync_probe(monkeypatch):
    """Run the track listing inline instead of on a worker thread."""

    def fake_run_off_thread(parent, work, on_done, on_error=None, **kwargs):
        on_done(work())
        return MagicMock()

    monkeypatch.setattr(mod, "run_off_thread", fake_run_off_thread)


def _image(sub_index: int, lang: str = "jpn", title: str | None = None) -> SubtitleStream:
    return SubtitleStream(
        index=sub_index + 1,
        sub_index=sub_index,
        codec_name="hdmv_pgs_subtitle",
        language_tag=lang,
        title=title,
        is_text=False,
    )


def _text(sub_index: int) -> SubtitleStream:
    return SubtitleStream(
        index=sub_index + 1, sub_index=sub_index, codec_name="ass", language_tag="jpn", title=None, is_text=True
    )


def _choose(tab: VideoOcrTab, path, streams) -> None:
    """Point Single File at ``path`` and land the (debounced) track listing now."""
    with patch.object(mod, "list_subtitle_streams", return_value=streams):
        tab.file_selector.set_path(str(path))
        tab._image_track_timer.stop()
        tab._probe_image_tracks()


def _run(tab: VideoOcrTab) -> FakeToolWorker | None:
    with patch.object(mod, "VideoOcrWorker", FakeToolWorker), patch(OS_ACCESS, return_value=True):
        tab.ocr_button.click()
    return tab.worker_thread


def test_a_sup_needs_no_region_and_reads_the_full_frame(qtbot, test_config, tmp_path):
    tab = _make(qtbot, test_config)
    sup = tmp_path / "movie.sup"
    sup.write_bytes(b"x")
    tab.file_selector.set_path(str(sup))
    worker = _run(tab)
    assert worker is not None and worker.args[1] == [sup] and worker.args[2] == FULL_FRAME
    assert worker.kwargs["bitmap_track"] is None


def test_a_saved_region_is_not_applied_to_image_subtitles(qtbot, test_config, tmp_path):
    tab = _make(qtbot, test_config)
    tab._set_region(Region(0.1, 0.8, 0.8, 0.15))
    (tmp_path / "movie.idx").write_text("x", encoding="utf-8")
    (tmp_path / "movie.sub").write_bytes(b"x")
    tab.file_selector.set_path(str(tmp_path / "movie.idx"))
    worker = _run(tab)
    assert worker is not None and worker.args[2] == FULL_FRAME


def test_half_a_vobsub_pair_is_refused_on_screen(qtbot, test_config, tmp_path):
    tab = _make(qtbot, test_config)
    sub = tmp_path / "movie.sub"
    sub.write_bytes(b"x")
    tab.file_selector.set_path(str(sub))
    assert _run(tab) is None
    issue = tab.issue_banner().current_issue()
    assert issue is not None and ".idx" in issue.summary


@pytest.mark.usefixtures("sync_probe")
def test_a_video_with_image_tracks_offers_them_and_defaults_to_the_picture(qtbot, test_config, tmp_path):
    tab = _make(qtbot, test_config)
    video = tmp_path / "ep01.mkv"
    video.write_bytes(b"x")
    _choose(tab, video, [_text(0), _image(1, "jpn", "Full"), _image(2, "eng")])
    assert not tab.source_row.isHidden()
    assert [tab.source_combo.itemData(i) for i in range(tab.source_combo.count())] == [None, 1, 2]
    assert tab.source_combo.currentIndex() == 0
    assert "jpn" in tab.source_combo.itemText(1) and "Full" in tab.source_combo.itemText(1)


@pytest.mark.usefixtures("sync_probe")
def test_a_video_without_image_tracks_shows_no_choice(qtbot, test_config, tmp_path):
    tab = _make(qtbot, test_config)
    video = tmp_path / "ep01.mkv"
    video.write_bytes(b"x")
    _choose(tab, video, [_text(0)])
    assert tab.source_row.isHidden()


@pytest.mark.usefixtures("sync_probe")
def test_a_chosen_image_track_runs_without_a_region(qtbot, test_config, tmp_path):
    tab = _make(qtbot, test_config)
    video = tmp_path / "ep01.mkv"
    video.write_bytes(b"x")
    _choose(tab, video, [_image(0), _image(1, "eng")])
    tab.source_combo.setCurrentIndex(2)
    worker = _run(tab)
    assert worker is not None and worker.kwargs["bitmap_track"] == 1 and worker.args[2] == FULL_FRAME


@pytest.mark.usefixtures("sync_probe")
def test_the_picture_still_needs_a_region(qtbot, test_config, tmp_path):
    tab = _make(qtbot, test_config)
    video = tmp_path / "ep01.mkv"
    video.write_bytes(b"x")
    _choose(tab, video, [_image(0)])
    assert _run(tab) is None
    issue = tab.issue_banner().current_issue()
    assert issue is not None and issue.action_text


@pytest.mark.usefixtures("sync_probe")
def test_folder_mode_hides_the_track_choice(qtbot, test_config, tmp_path):
    tab = _make(qtbot, test_config)
    video = tmp_path / "ep01.mkv"
    video.write_bytes(b"x")
    _choose(tab, video, [_image(0)])
    tab._set_mode(False)
    assert tab.source_row.isHidden()
    tab._set_mode(True)
    assert not tab.source_row.isHidden()


@pytest.mark.usefixtures("sync_probe")
def test_a_stale_listing_is_dropped(qtbot, test_config, tmp_path):
    tab = _make(qtbot, test_config)
    first, second = tmp_path / "a.mkv", tmp_path / "b.mkv"
    first.write_bytes(b"x")
    second.write_bytes(b"x")
    pending = []
    with patch.object(mod, "run_off_thread", lambda parent, work, on_done, on_error=None, **k: pending.append(on_done)):
        tab.file_selector.set_path(str(first))
        tab._image_track_timer.stop()
        tab._probe_image_tracks()
    _choose(tab, second, [])
    pending[0]([_image(0)])  # a's listing lands after b was chosen
    assert tab.source_row.isHidden()


def test_set_region_on_an_image_file_explains_instead_of_opening(qtbot, test_config, tmp_path):
    tab = _make(qtbot, test_config)
    sup = tmp_path / "movie.sup"
    sup.write_bytes(b"x")
    tab.file_selector.set_path(str(sup))
    with patch.object(tab, "_open_region_dialog") as opened:
        tab.set_region_button.click()
    opened.assert_not_called()
    issue = tab.issue_banner().current_issue()
    assert issue is not None and "no region" in issue.summary
