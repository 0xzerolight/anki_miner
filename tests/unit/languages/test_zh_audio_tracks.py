"""A bilingual file: every selection path picks the mining language's track."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PyQt6.QtWidgets")

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.widgets._mining_tab_base import MiningTabBase
from anki_miner.gui.widgets.single_episode_tab import SingleEpisodeTab
from anki_miner.languages.registry import get_profile
from anki_miner.services.audio_condenser import _pick_subtitle_stream
from anki_miner.services.media_extractor import MediaExtractorService
from anki_miner.services.retime_reference import _candidate_rank
from anki_miner.utils.audio_track_detector import (
    JAPANESE_LANGUAGE_CODES,
    SubtitleStream,
    find_japanese_audio_stream,
)

DETECTOR = "anki_miner.utils.audio_track_detector"
FIXTURE = Path(__file__).parents[2] / "fixtures" / "zh" / "dual_audio_ffprobe.json"
ZH_CODES = frozenset({"chi", "zho", "zh", "chinese", "cmn"})


@pytest.fixture
def dual_audio(tmp_path):
    """Patch ffprobe with the committed two-stream payload; yield a video path."""
    proc = MagicMock(returncode=0, stdout=FIXTURE.read_text(encoding="utf-8"), stderr="")
    with patch(f"{DETECTOR}.subprocess.run", return_value=proc):
        yield tmp_path / "bilingual.mkv"


def _subtitle_streams():
    return [
        SubtitleStream(3, 0, "subrip", "jpn", "Japanese", True, False, True),
        SubtitleStream(4, 1, "subrip", "chi", "Chinese", True, False, False),
    ]


def test_the_profile_codes_pick_the_zh_stream_over_the_default_ja_one(dual_audio):
    assert get_profile("zh").audio_track_codes == ZH_CODES
    assert find_japanese_audio_stream(dual_audio, codes=ZH_CODES).language_tag == "chi"
    assert find_japanese_audio_stream(dual_audio, codes=JAPANESE_LANGUAGE_CODES).language_tag == "jpn"


def test_media_extractor_phase3_maps_the_zh_stream(dual_audio, test_config):
    zh_config = dataclasses.replace(test_config, language="zh")
    assert MediaExtractorService(zh_config)._get_japanese_audio_stream(dual_audio) == 2
    assert MediaExtractorService(test_config)._get_japanese_audio_stream(dual_audio) == 1


def test_condense_picks_the_zh_subtitle_stream():
    streams = _subtitle_streams()
    assert _pick_subtitle_stream(streams, None, ZH_CODES).sub_index == 1
    assert _pick_subtitle_stream(streams, None, JAPANESE_LANGUAGE_CODES).sub_index == 0


def test_retime_ranks_the_zh_subtitle_stream_first():
    streams = _subtitle_streams()
    assert sorted(streams, key=lambda s: _candidate_rank(s, ZH_CODES))[0].language_tag == "chi"
    assert sorted(streams, key=lambda s: _candidate_rank(s, JAPANESE_LANGUAGE_CODES))[0].language_tag == "jpn"


def test_curation_media_context_carries_the_profile_codes(tmp_path, monkeypatch):
    video, subtitle = tmp_path / "a.mkv", tmp_path / "a.srt"
    video.touch()
    subtitle.touch()
    parser = MagicMock()
    parser.return_value.parse_raw_entries.return_value = [(0.0, 1.0, "我去银行。")]
    monkeypatch.setattr("anki_miner.gui.widgets._mining_tab_base.SubtitleParserService", parser)
    zh_config = dataclasses.replace(AnkiMinerConfig(), language="zh")
    context = MiningTabBase._make_curation_media_context(zh_config, video, subtitle, 0.0)
    assert context.audio_track_codes == ZH_CODES


def test_the_timing_preview_hands_the_viewer_the_profile_codes(qtbot, test_config, tmp_path):
    zh_config = dataclasses.replace(test_config, language="zh")
    tab = SingleEpisodeTab(
        config=zh_config,
        presenter=MagicMock(name="Presenter"),
        progress_callback=MagicMock(name="ProgressCallback"),
    )
    qtbot.addWidget(tab)
    video, subtitle = tmp_path / "ep01.mkv", tmp_path / "ep01.srt"
    video.touch()
    subtitle.touch()
    tab.video_selector.get_path = MagicMock(return_value=str(video))
    tab.video_selector.is_valid = MagicMock(return_value=True)
    tab.subtitle_selector.get_path = MagicMock(return_value=str(subtitle))
    tab.subtitle_selector.is_valid = MagicMock(return_value=True)

    parser = MagicMock()
    parser.return_value.parse_raw_entries.return_value = [(0.0, 2.5, "我去银行。")]
    viewer_instance = MagicMock()
    viewer_instance.exec.return_value = viewer_instance.DialogCode.Rejected
    with (
        patch("anki_miner.gui.widgets.subtitle_viewer.SubtitleViewer", return_value=viewer_instance) as viewer_cls,
        patch("anki_miner.gui.widgets.single_episode_tab.SubtitleParserService", parser),
    ):
        tab._on_timing_clicked()
        qtbot.waitUntil(lambda: viewer_cls.called, timeout=3000)
    assert viewer_cls.call_args.kwargs["audio_track_codes"] == ZH_CODES
    tab.deleteLater()
