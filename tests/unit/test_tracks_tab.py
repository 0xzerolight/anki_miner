"""TracksTab (Utilities → Tracks).

Harness as in test_readability_tab.py: the ffmpeg probe, the track probe and the
worker class are patched at the tab's import site. The shared _ToolTabBase run
lifecycle is tested once in test_tool_tab_contract.py.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from PyQt6.QtCore import Qt

from anki_miner.exceptions import FfmpegNotFoundError
from anki_miner.gui.widgets.tracks_tab import TracksTab
from anki_miner.services.track_extractor import InputProbe, MediaTracks, TrackRef
from anki_miner.utils.audio_track_detector import AudioStream, SubtitleStream
from tests.unit._tool_tab_harness import OS_ACCESS, FakeToolWorker, capture_slots
from tests.unit._tool_tab_harness import make_config as _make_config

_MODULE = "anki_miner.gui.widgets.tracks_tab"
_PROBE = f"{_MODULE}.TracksTab._compute_ffmpeg_available"
_WORKER_CLS = f"{_MODULE}.TrackExtractWorker"

ASS_JPN = SubtitleStream(2, 0, "ass", "jpn", "Full", True, is_default=True)
PGS_JPN = SubtitleStream(3, 1, "hdmv_pgs_subtitle", "jpn", None, False)
AAC = AudioStream(1, 0, "jpn", None, "aac", 2, True)
ASS_REF = TrackRef("subtitle", 0)


def _make_tab(config, qtbot, *, available: bool = True) -> TracksTab:
    with patch(_PROBE, return_value=available):
        tab = TracksTab(config)
        qtbot.addWidget(tab)
        assert tab._availability_worker.wait(3000)
        if available:
            qtbot.waitUntil(lambda: tab._engine_is_available, timeout=3000)
        else:
            qtbot.waitUntil(lambda: not tab.engine_notice_label.isHidden(), timeout=3000)
    return tab


def _video(tmp_path: Path, name: str = "EP01.mkv") -> Path:
    path = tmp_path / name
    path.write_bytes(b"video")
    return path


def _probe(source: Path, videos: tuple[Path, ...] | None = None, *, preselected=(ASS_REF,)):
    return InputProbe(
        source=source,
        videos=videos if videos is not None else (source,),
        tracks=MediaTracks(subtitles=(ASS_JPN, PGS_JPN), audio=(AAC,)),
        preselected=preselected,
    )


def _loaded(tab: TracksTab, source: Path, probe: InputProbe | None = None) -> None:
    tab.input_selector.set_path(str(source))
    tab._apply_input_probe(probe or _probe(source))


def _cells(tab: TracksTab, row: int) -> list[str]:
    return [tab.tracks_table.item(row, c).text() for c in range(tab.tracks_table.columnCount())]


def test_identity(qtbot, tmp_path):
    tab = _make_tab(_make_config(tmp_path), qtbot)
    assert tab.TASK_ID == "tools.tracks"
    assert (tab.TASK_OWNER.main_tab, tab.TASK_OWNER.subtab) == ("subtitles", "tracks")
    assert tab.OUTPUT_HISTORY_KEY == "tools.tracks.output"
    assert tab.input_selector._history_key == "tools.tracks.inputs"
    assert tab.tracks_table.isHidden()
    assert not tab.overwrite_checkbox.isChecked()


def test_without_ffmpeg_extract_is_off_and_nothing_is_probed(qtbot, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(f"{_MODULE}.probe_input", lambda *a: calls.append(a))
    tab = _make_tab(_make_config(tmp_path), qtbot, available=False)
    tab.input_selector.set_path(str(_video(tmp_path)))
    qtbot.wait(500)
    assert not tab.extract_button.isEnabled()
    assert calls == []


def test_picking_a_video_lists_its_tracks(qtbot, tmp_path, monkeypatch):
    video = _video(tmp_path)
    seen = []

    def fake_probe(source, ffprobe_cmd, codes):
        seen.append((source, codes))
        return _probe(source)

    monkeypatch.setattr(f"{_MODULE}.probe_input", fake_probe)
    tab = _make_tab(_make_config(tmp_path), qtbot)
    tab.input_selector.set_path(str(video))
    qtbot.waitUntil(lambda: tab._probe is not None, timeout=3000)
    assert seen[0][0] == video and "jpn" in seen[0][1]
    assert not tab.tracks_table.isHidden()
    assert tab.tracks_table.rowCount() == 3
    assert _cells(tab, 0)[:3] == ["Subtitle 1", "jpn", "ASS"]
    assert _cells(tab, 2)[:3] == ["Audio 1", "jpn", "AAC stereo"]
    assert "Image" in _cells(tab, 1)[4]
    assert ".ass" in tab.tracks_table.item(0, 2).toolTip()
    assert "OCR" in tab.tracks_table.item(1, 4).toolTip()


def test_preselected_track_is_ticked_and_ticks_follow_the_checkboxes(qtbot, tmp_path):
    tab = _make_tab(_make_config(tmp_path), qtbot)
    _loaded(tab, _video(tmp_path))
    assert tab.ticked_refs() == (TrackRef("subtitle", 0),)
    tab.tracks_table.item(2, 0).setCheckState(Qt.CheckState.Checked)
    tab.tracks_table.item(0, 0).setCheckState(Qt.CheckState.Unchecked)
    assert tab.ticked_refs() == (TrackRef("audio", 0),)


def test_a_new_path_drops_the_listing(qtbot, tmp_path):
    tab = _make_tab(_make_config(tmp_path), qtbot)
    _loaded(tab, _video(tmp_path))
    other = tmp_path / "other.mkv"
    with patch(f"{_MODULE}.probe_input", return_value=_probe(other)):
        tab.input_selector.set_path(str(other))
        assert tab._probe is None and tab.tracks_table.isHidden()
        qtbot.waitUntil(lambda: tab._probe is not None, timeout=3000)


def test_a_stale_listing_is_dropped(qtbot, tmp_path, monkeypatch):
    captured = []
    monkeypatch.setattr(f"{_MODULE}.run_off_thread", lambda parent, work, done, failed: captured.append((done, failed)))
    tab = _make_tab(_make_config(tmp_path), qtbot)
    first = _video(tmp_path)
    tab.input_selector.set_path(str(first))
    tab._probe_tracks()
    tab.input_selector.set_path(str(_video(tmp_path, "EP02.mkv")))
    done, _failed = captured[-1]
    done(_probe(first))
    assert tab._probe is None


def test_a_failed_listing_raises_a_screen_issue(qtbot, tmp_path, monkeypatch):
    captured = []
    monkeypatch.setattr(f"{_MODULE}.run_off_thread", lambda parent, work, done, failed: captured.append((done, failed)))
    tab = _make_tab(_make_config(tmp_path), qtbot)
    tab.input_selector.set_path(str(_video(tmp_path)))
    tab._probe_tracks()
    captured[-1][1]("ffprobe exploded")
    issue = tab.issue_banner().current_issue()
    assert issue is not None and issue.summary == "Tracks could not be read." and issue.details == "ffprobe exploded"


def test_folder_caption_names_the_first_video_and_the_rule(qtbot, tmp_path):
    tab = _make_tab(_make_config(tmp_path), qtbot)
    videos = (_video(tmp_path, "EP01.mkv"), _video(tmp_path, "EP02.mkv"))
    _loaded(tab, tmp_path, _probe(tmp_path, videos))
    text = tab.tracks_status_label.text()
    assert "EP01.mkv" in text and "2" in text and "every video" in text


def test_folder_caption_names_the_listed_video(qtbot, tmp_path):
    """EP01 unreadable: the listing and its caption are EP02's (Tracks final review, minor 1)."""
    tab = _make_tab(_make_config(tmp_path), qtbot)
    videos = (_video(tmp_path, "EP01.mkv"), _video(tmp_path, "EP02.mkv"), _video(tmp_path, "EP03.mkv"))
    probe = _probe(tmp_path, videos)
    _loaded(tab, tmp_path, InputProbe(probe.source, probe.videos, probe.tracks, probe.preselected, listed_index=1))
    text = tab.tracks_status_label.text()
    assert "EP02.mkv" in text and "EP01.mkv" not in text and "3" in text and "every video" in text


def test_a_folder_without_tracks_says_no_video_had_any(qtbot, tmp_path):
    tab = _make_tab(_make_config(tmp_path), qtbot)
    videos = (_video(tmp_path, "EP01.mkv"), _video(tmp_path, "EP02.mkv"))
    _loaded(tab, tmp_path, InputProbe(tmp_path, videos, MediaTracks(), ()))
    assert tab.tracks_table.isHidden()
    assert "EP01.mkv" not in tab.tracks_status_label.text()


@pytest.mark.parametrize(
    ("setup", "summary"),
    [
        ("none", "Choose a video or a folder first."),
        ("gone", "That file or folder no longer exists."),
        ("unloaded", "Wait for the track list to load, then tick the tracks to save."),
        ("unticked", "Tick at least one track to save."),
    ],
)
def test_refusals(qtbot, tmp_path, setup, summary):
    tab = _make_tab(_make_config(tmp_path), qtbot)
    video = _video(tmp_path)
    if setup == "gone":
        tab.input_selector.set_path(str(tmp_path / "gone.mkv"))
    elif setup == "unloaded":
        tab.input_selector.set_path(str(video))
        tab._probe_timer.stop()
    elif setup == "unticked":
        _loaded(tab, video, _probe(video, preselected=()))
    with patch(_WORKER_CLS) as worker_cls:
        tab._on_extract()
    worker_cls.assert_not_called()
    assert tab.issue_banner().current_issue().summary == summary


def test_single_run_hands_the_worker_its_inputs(qtbot, tmp_path):
    tab = _make_tab(_make_config(tmp_path), qtbot)
    video = _video(tmp_path)
    _loaded(tab, video)
    out = tmp_path / "out"
    out.mkdir()
    tab._custom_output_dir = out
    tab.overwrite_checkbox.setChecked(True)
    worker = FakeToolWorker()
    with patch(_WORKER_CLS, return_value=worker) as worker_cls:
        tab.extract_button.click()
    args, kwargs = worker_cls.call_args
    assert args[1] == [video] and args[2] == (TrackRef("subtitle", 0),)
    assert kwargs["output_dir"] == out and kwargs["overwrite"] is True
    assert worker._started


def test_folder_run_lists_videos_in_natural_order(qtbot, tmp_path):
    tab = _make_tab(_make_config(tmp_path), qtbot)
    videos = tuple(_video(tmp_path, n) for n in ("EP2.mkv", "EP10.mkv"))
    (tmp_path / "EP2.ass").write_text("x", encoding="utf-8")
    _loaded(tab, tmp_path, _probe(tmp_path, videos))
    with patch(_WORKER_CLS, return_value=FakeToolWorker()) as worker_cls:
        tab.extract_button.click()
        qtbot.waitUntil(lambda: worker_cls.called, timeout=3000)
    assert worker_cls.call_args.args[1] == list(videos)


def test_unwritable_output_refuses_and_hands_back_extract(qtbot, tmp_path):
    tab = _make_tab(_make_config(tmp_path), qtbot)
    _loaded(tab, _video(tmp_path))
    with patch(OS_ACCESS, return_value=False), patch(_WORKER_CLS) as worker_cls:
        tab.extract_button.click()
    worker_cls.assert_not_called()
    assert tab.issue_banner().current_issue().summary == "Output folder is not writable."
    assert tab.extract_button.isEnabled()


def test_track_notes_reach_the_log(qtbot, tmp_path):
    tab = _make_tab(_make_config(tmp_path), qtbot)
    _loaded(tab, _video(tmp_path))
    worker = FakeToolWorker()
    notes = capture_slots(worker.file_note)
    with patch(_WORKER_CLS, return_value=worker):
        tab.extract_button.click()
    notes[0](0, "Saved EP01.ass")
    assert "Saved EP01.ass" in tab.log_widget.full_text()


def test_ffmpeg_missing_has_its_own_sentence(qtbot, tmp_path):
    tab = _make_tab(_make_config(tmp_path), qtbot)
    assert "ffmpeg" in tab._typed_problem_summary(FfmpegNotFoundError("x"))


def test_the_run_hands_the_worker_the_listed_languages(qtbot, tmp_path):
    tab = _make_tab(_make_config(tmp_path), qtbot)
    _loaded(tab, _video(tmp_path))
    tab.tracks_table.item(2, 0).setCheckState(Qt.CheckState.Checked)
    with patch(_WORKER_CLS, return_value=FakeToolWorker()) as worker_cls:
        tab.extract_button.click()
    assert worker_cls.call_args.kwargs["expected_languages"] == {ASS_REF: "jpn", TrackRef("audio", 0): "jpn"}
