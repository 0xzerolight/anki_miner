"""How a probed video resolves to a subtitle route, per requested source.

The picker's value is user *intent* (SubtitleSource); the classifier turns it
plus the probe facts into the route one video takes (SubMode). The hard gates —
live, duration, age restriction — outrank every source.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from anki_miner.config.config import AnkiMinerConfig
from anki_miner.gui.widgets.youtube_playlist_flow import _classify_probe_result
from anki_miner.models.youtube import VideoInfo

SOURCES = ("auto", "transcribe", "captions")


def _info(**overrides) -> VideoInfo:
    defaults = {
        "video_id": "abc123",
        "title": "Test Video",
        "duration_s": 600,
        "has_manual_ja_subs": False,
        "has_auto_ja_subs": False,
        "is_live": False,
        "is_age_restricted": False,
    }
    defaults.update(overrides)
    return VideoInfo(**defaults)


@pytest.mark.parametrize(
    ("source", "expected"),
    [("captions", "manual_only"), ("auto", "manual_only"), ("transcribe", "transcribe")],
)
def test_source_decides_the_route_for_a_captioned_video(
    source: str, expected: str, test_config: AnkiMinerConfig
) -> None:
    """Always transcribe ignores a real manual track; the other two prefer it."""
    mineable, error, mode = _classify_probe_result(_info(has_manual_ja_subs=True), test_config, source)
    assert (mineable, error, mode) == (True, None, expected)


@pytest.mark.parametrize("source", ["captions", "auto"])
def test_caption_priority_is_unchanged(source: str, test_config: AnkiMinerConfig) -> None:
    """Manual beats native auto beats dub, exactly as before the picker."""
    info = _info(has_auto_ja_subs=True, has_dub_ja_subs=True)
    assert _classify_probe_result(info, test_config, source)[2] == "auto_only"
    info = _info(has_dub_ja_subs=True)
    assert _classify_probe_result(info, test_config, source)[2] == "auto_dub"


def test_auto_falls_through_to_transcription(test_config: AnkiMinerConfig) -> None:
    """The refusal a caption-less video used to get becomes an ASR run."""
    mineable, error, mode = _classify_probe_result(_info(), test_config, "auto")
    assert (mineable, error, mode) == (True, None, "transcribe")


def test_captions_only_still_refuses(test_config: AnkiMinerConfig) -> None:
    """Captions only keeps today's behaviour, message included."""
    mineable, error, mode = _classify_probe_result(_info(), test_config, "captions")
    assert mineable is False
    assert error == "No Japanese subtitles available for this video."
    assert mode is None


def test_transcribe_needs_no_captions_at_all(test_config: AnkiMinerConfig) -> None:
    mineable, error, mode = _classify_probe_result(_info(), test_config, "transcribe")
    assert (mineable, error, mode) == (True, None, "transcribe")


@pytest.mark.parametrize("source", SOURCES)
def test_live_streams_are_refused_for_every_source(source: str, test_config: AnkiMinerConfig) -> None:
    mineable, error, mode = _classify_probe_result(_info(is_live=True), test_config, source)
    assert (mineable, mode) == (False, None)
    assert error == "Live streams are not supported."


@pytest.mark.parametrize("source", SOURCES)
def test_over_long_videos_are_refused_for_every_source(source: str, test_config: AnkiMinerConfig) -> None:
    """ASR cannot buy a video past the duration cap — that gate runs first."""
    info = _info(duration_s=test_config.youtube_max_duration_s + 1)
    mineable, error, mode = _classify_probe_result(info, test_config, source)
    assert (mineable, mode) == (False, None)
    assert "exceeds max duration" in (error or "")


@pytest.mark.parametrize("source", SOURCES)
def test_age_restricted_videos_are_refused_for_every_source(source: str, test_config: AnkiMinerConfig) -> None:
    """Without cookies the download itself fails, whatever the subtitle plan."""
    config = replace(test_config, youtube_cookies_from_browser=None, youtube_cookies_file=None)
    mineable, error, mode = _classify_probe_result(_info(is_age_restricted=True), config, source)
    assert (mineable, mode) == (False, None)
    assert "Age-restricted" in (error or "")
