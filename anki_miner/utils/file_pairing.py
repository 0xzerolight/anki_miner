"""Utility for pairing video and subtitle files across folders."""

from dataclasses import dataclass
from pathlib import Path


@dataclass
class FilePair:
    """Represents a video/subtitle file pair."""

    video: Path
    subtitle: Path

    @property
    def video_name(self) -> str:
        """Get video filename."""
        return self.video.name

    @property
    def subtitle_name(self) -> str:
        """Get subtitle filename."""
        return self.subtitle.name


class FilePairMatcher:
    """Matches video and subtitle files across folders by base name."""

    VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".m4v", ".mov"}
    SUBTITLE_EXTENSIONS = {".ass", ".srt", ".ssa"}

    @staticmethod
    def find_pairs_across_folders(anime_folder: Path, subtitle_folder: Path) -> list[FilePair]:
        """Find matching video/subtitle pairs across two folders.

        Matches by base filename:
        - anime_folder/episode_01.mp4 <-> subtitle_folder/episode_01.ass
        - anime_folder/ep02.mkv <-> subtitle_folder/ep02.srt

        Args:
            anime_folder: Folder containing video files
            subtitle_folder: Folder containing subtitle files

        Returns:
            List of FilePair objects, naturally sorted by video filename
        """
        # Get all videos from anime folder
        videos = [
            f
            for f in anime_folder.iterdir()
            if f.is_file() and f.suffix.lower() in FilePairMatcher.VIDEO_EXTENSIONS
        ]

        pairs = []
        for video in videos:
            base_name = video.stem  # "episode_01.mp4" -> "episode_01"

            # Look for subtitle with same base name in subtitle folder
            for sub_ext in FilePairMatcher.SUBTITLE_EXTENSIONS:
                subtitle = subtitle_folder / f"{base_name}{sub_ext}"
                if subtitle.exists():
                    pairs.append(FilePair(video, subtitle))
                    break

        # Natural sort by video filename
        from anki_miner.utils.sort_utils import natural_sort_key

        pairs.sort(key=lambda p: natural_sort_key(p.video.name))

        return pairs

    @staticmethod
    def find_unpaired_files(
        anime_folder: Path, subtitle_folder: Path
    ) -> tuple[list[Path], list[Path]]:
        """Find unpaired videos and subtitles for diagnostics.

        Args:
            anime_folder: Folder containing video files
            subtitle_folder: Folder containing subtitle files

        Returns:
            Tuple of (unpaired_videos, unpaired_subtitles)
        """
        pairs = FilePairMatcher.find_pairs_across_folders(anime_folder, subtitle_folder)
        paired_videos = {p.video for p in pairs}
        paired_subtitles = {p.subtitle for p in pairs}

        all_videos = [
            f
            for f in anime_folder.iterdir()
            if f.is_file() and f.suffix.lower() in FilePairMatcher.VIDEO_EXTENSIONS
        ]
        all_subtitles = [
            f
            for f in subtitle_folder.iterdir()
            if f.is_file() and f.suffix.lower() in FilePairMatcher.SUBTITLE_EXTENSIONS
        ]

        unpaired_videos = [v for v in all_videos if v not in paired_videos]
        unpaired_subtitles = [s for s in all_subtitles if s not in paired_subtitles]

        return unpaired_videos, unpaired_subtitles

    @staticmethod
    def find_pairs_by_episode_number(anime_folder: Path, subtitle_folder: Path) -> list[FilePair]:
        """Find matching pairs by episode number instead of exact name.

        Matches files like:
        - Jujutsu_Kaisen_01.mp4 ↔ jjk_ep01.ass (both episode 1)
        - S01E05.mkv ↔ 05.srt (both episode 5)
        - anime_1.mp4 ↔ episode_01.ass (both episode 1, different padding)

        Args:
            anime_folder: Folder containing video files
            subtitle_folder: Folder containing subtitle files

        Returns:
            List of FilePair objects matched by episode number
        """
        from anki_miner.utils.episode_matcher import EpisodeMatcher

        # Get all videos and subtitles
        videos = [
            f
            for f in anime_folder.iterdir()
            if f.is_file() and f.suffix.lower() in FilePairMatcher.VIDEO_EXTENSIONS
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
