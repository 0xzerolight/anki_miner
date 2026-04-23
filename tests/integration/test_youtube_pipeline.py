"""Integration tests for the YouTube mining pipeline.

These hit the real YouTube API via yt-dlp. They are SLOW (~30-90s each) and
require network. They are gated behind the `youtube` pytest marker and excluded
from default CI runs.

Run locally:  pytest -m youtube -v
"""

from __future__ import annotations

import pysubs2
import pytest

from anki_miner.config import AnkiMinerConfig
from anki_miner.models.youtube import SubMode
from anki_miner.services.youtube_fetcher import YouTubeFetcherService

# Short public Japanese-language video with native auto-captions. Used by the
# Wave 0 spike and verified to parse with pysubs2.
JA_VIDEO_URL = "https://www.youtube.com/watch?v=qzzweIQoIOU"
JA_VIDEO_ID = "qzzweIQoIOU"


@pytest.mark.youtube
class TestYouTubeIntegration:
    def test_probe_returns_video_info(self) -> None:
        config = AnkiMinerConfig()
        fetcher = YouTubeFetcherService(config)
        info = fetcher.probe_metadata(JA_VIDEO_URL)

        assert info.video_id == JA_VIDEO_ID
        assert info.title
        assert info.duration_s > 0
        assert (
            info.has_manual_ja_subs or info.has_auto_ja_subs
        ), f"Expected at least one form of Japanese subs for {JA_VIDEO_URL}, got none"
        assert not info.is_live
        assert not info.is_age_restricted

    def test_fetch_produces_parseable_subs(self, tmp_path) -> None:
        config = AnkiMinerConfig()
        fetcher = YouTubeFetcherService(config)
        info = fetcher.probe_metadata(JA_VIDEO_URL)

        sub_mode: SubMode = "manual_only" if info.has_manual_ja_subs else "auto_only"

        fetched = fetcher.fetch_video(
            url=JA_VIDEO_URL,
            video_id=info.video_id,
            workspace=tmp_path,
            sub_mode=sub_mode,
        )

        assert fetched.video_file.exists()
        assert fetched.video_file.stat().st_size > 0
        assert fetched.subtitle_file.exists()
        assert fetched.subtitle_file.stat().st_size > 0
        assert fetched.sub_source == ("manual" if info.has_manual_ja_subs else "auto")

        # The entire point of --convert-subs srt: pysubs2 must parse it.
        subs = pysubs2.load(str(fetched.subtitle_file))
        assert len(subs) > 0, "Subtitle file parsed but has zero events"
