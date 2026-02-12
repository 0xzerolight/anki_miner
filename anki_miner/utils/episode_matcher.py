"""Episode number extraction and matching for video/subtitle pairs."""

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass
class EpisodeInfo:
    """Information extracted from episode filename."""

    file_path: Path
    episode_number: int
    season_number: int | None = None

    @property
    def filename(self) -> str:
        """Get filename."""
        return self.file_path.name


class EpisodeNumberExtractor:
    """Extract episode numbers from filenames using regex patterns."""

    # Regex patterns for common episode naming conventions (in priority order)
    PATTERNS = [
        # S01E01, s1e1, S01 E01 (season + episode)
        (r"[Ss](\d+)[Ee](\d+)", lambda m: (int(m.group(1)), int(m.group(2)))),
        # 1x01, 1X01 (season x episode)
        (r"(\d+)[xX](\d+)", lambda m: (int(m.group(1)), int(m.group(2)))),
        # Episode 01, Ep01, ep.01, episode_01 (no season)
        (r"[Ee][Pp](?:isode)?[\s._-]*(\d+)", lambda m: (None, int(m.group(1)))),
        # Just numbers: 01, 001, 1 (at boundaries or after non-digits)
        (r"(?:^|[^\d])(\d{1,3})(?:[^\d]|$)", lambda m: (None, int(m.group(1)))),
    ]

    @classmethod
    def extract_episode_info(cls, file_path: Path) -> EpisodeInfo | None:
        """Extract episode number from filename.

        Args:
            file_path: Path to video or subtitle file

        Returns:
            EpisodeInfo if episode number found, None otherwise
        """
        filename = file_path.stem  # Remove extension

        for pattern, extractor in cls.PATTERNS:
            match = re.search(pattern, filename)
            if match:
                season, episode = extractor(match)
                return EpisodeInfo(file_path, episode, season)

        return None


class EpisodeMatcher:
    """Match video/subtitle files by episode number."""

    @staticmethod
    def match_by_episode_number(
        video_files: list[Path], subtitle_files: list[Path]
    ) -> list[tuple[Path, Path]]:
        """Match video and subtitle files by episode number.

        Matches:
        - Same episode number (1 ↔ 01)
        - If both have season, seasons must match too

        Args:
            video_files: List of video file paths
            subtitle_files: List of subtitle file paths

        Returns:
            List of (video, subtitle) tuples sorted by episode number
        """
        # Extract episode info for all files
        video_episodes = []
        for video in video_files:
            info = EpisodeNumberExtractor.extract_episode_info(video)
            if info:
                video_episodes.append(info)

        subtitle_episodes = []
        for subtitle in subtitle_files:
            info = EpisodeNumberExtractor.extract_episode_info(subtitle)
            if info:
                subtitle_episodes.append(info)

        # Match by episode number
        pairs = []
        for video_info in video_episodes:
            for subtitle_info in subtitle_episodes:
                # Match if episode numbers are the same
                if video_info.episode_number == subtitle_info.episode_number:
                    # If both have season numbers, they must match
                    if (
                        video_info.season_number is not None
                        and subtitle_info.season_number is not None
                        and video_info.season_number != subtitle_info.season_number
                    ):
                        continue  # Seasons don't match, skip

                    pairs.append(
                        (video_info.file_path, subtitle_info.file_path, video_info.episode_number)
                    )
                    break  # Found match, move to next video

        # Sort by episode number using cached value
        pairs.sort(key=lambda p: p[2])
        # Return without the cached episode number
        return [(p[0], p[1]) for p in pairs]
