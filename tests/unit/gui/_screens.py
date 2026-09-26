"""Plain helpers for the mining-screen fixtures in this package's conftest."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from anki_miner.models.youtube import VideoInfo


def ready_youtube_item(tab, video_id: str):
    """Queue one link through the add flow and resolve its (patched) probe as READY."""
    tab._add_flow.add_urls([f"https://www.youtube.com/watch?v={video_id}"])
    item = tab._queue.all_items()[-1]
    tab._add_flow._on_probe_done(
        item,
        VideoInfo(
            video_id=video_id,
            title=f"Video {video_id}",
            duration_s=600,
            has_manual_ja_subs=True,
            has_auto_ja_subs=False,
            is_live=False,
            is_age_restricted=False,
        ),
    )
    return item


def start_single_run(tab, tmp_path: Path) -> tuple[Path, Path]:
    """Start a Single Episode run on two empty files; the worker and processor are mocks."""
    video = tmp_path / "ep01.mkv"
    video.touch()
    subs = tmp_path / "ep01.ass"
    subs.touch()
    tab.video_selector.get_path = MagicMock(return_value=str(video))
    tab.video_selector.is_valid = MagicMock(return_value=True)
    tab.subtitle_selector.get_path = MagicMock(return_value=str(subs))
    tab.subtitle_selector.is_valid = MagicMock(return_value=True)
    with (
        patch("anki_miner.gui.widgets.single_episode_tab.EpisodeWorkerThread", return_value=MagicMock()),
        patch("anki_miner.gui.widgets.single_episode_tab.create_episode_processor", return_value=MagicMock()),
    ):
        tab._start_processing()
    return video, subs


def add_audiobook(tab, tmp_path: Path, stem: str):
    """Add one audio + subtitle pair to an AudiobookTab's queue and render its row."""
    audio = tmp_path / f"{stem}.m4b"
    sub = tmp_path / f"{stem}.srt"
    audio.touch()
    sub.touch()
    item = tab._queue.add(audio, sub)
    tab._render_new_item(item)
    tab._recompute_buttons()
    return item
