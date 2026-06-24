"""Utility for pairing video and subtitle files across folders."""

from dataclasses import dataclass
from pathlib import Path

DEFAULT_SUBTITLE_PRIORITY: tuple[str, ...] = (".ass", ".ssa", ".srt")


def find_sibling_subtitle(video_path: Path) -> Path | None:
    """Return the highest-priority sibling subtitle for *video_path*, or None.

    Looks in the same folder for a file whose stem matches *video_path*'s stem
    and whose extension is one of DEFAULT_SUBTITLE_PRIORITY.  Returns the first
    hit in priority order (.ass > .ssa > .srt), or None when no sibling exists.
    """
    folder = video_path.parent
    stem = video_path.stem
    for ext in DEFAULT_SUBTITLE_PRIORITY:
        candidate = folder / f"{stem}{ext}"
        if candidate.is_file():
            return candidate
    return None


@dataclass
class FilePair:
    """Represents a video/subtitle file pair."""

    video: Path
    subtitle: Path


class FilePairMatcher:
    """Matches video and subtitle files by base name, with deterministic
    format priority when multiple subtitle variants exist for one video.
    """

    VIDEO_EXTENSIONS: frozenset[str] = frozenset({".mp4", ".mkv", ".avi", ".m4v", ".mov"})
    SUBTITLE_EXTENSIONS: frozenset[str] = frozenset(DEFAULT_SUBTITLE_PRIORITY)

    @staticmethod
    def find_pairs_by_episode_number(video_folder: Path, subtitle_folder: Path) -> list[FilePair]:
        """Find matching pairs by episode number instead of exact name.

        Matches files like:
        - Jujutsu_Kaisen_01.mp4 ↔ jjk_ep01.ass (both episode 1)
        - S01E05.mkv ↔ 05.srt (both episode 5)
        - video_1.mp4 ↔ episode_01.ass (both episode 1, different padding)

        Args:
            video_folder: Folder containing video files
            subtitle_folder: Folder containing subtitle files

        Returns:
            List of FilePair objects matched by episode number
        """
        from anki_miner.utils.episode_matcher import EpisodeMatcher

        # Get all videos and subtitles
        videos = [
            f for f in video_folder.iterdir() if f.is_file() and f.suffix.lower() in FilePairMatcher.VIDEO_EXTENSIONS
        ]

        subtitles = [
            f
            for f in subtitle_folder.iterdir()
            if f.is_file() and f.suffix.lower() in FilePairMatcher.SUBTITLE_EXTENSIONS
        ]

        # Match by episode number
        matched_pairs = EpisodeMatcher.match_by_episode_number(videos, subtitles)

        # Convert to FilePair objects
        return [FilePair(video, subtitle) for video, subtitle in matched_pairs]
