"""Orchestrator for processing a folder of episodes."""

from pathlib import Path

from anki_miner.interfaces import PresenterProtocol, ProgressCallback
from anki_miner.models import ProcessingResult
from anki_miner.orchestration.episode_processor import EpisodeProcessor


class FolderProcessor:
    """Orchestrate processing of a folder of episodes."""

    def __init__(self, episode_processor: EpisodeProcessor, presenter: PresenterProtocol):
        """Initialize the folder processor.

        Args:
            episode_processor: Episode processor for individual episodes
            presenter: Output presenter
        """
        self.episode_processor = episode_processor
        self.presenter = presenter

    def find_video_subtitle_pairs(self, folder: Path) -> list[tuple[Path, Path]]:
        """Find matching video and subtitle file pairs in a folder.

        Args:
            folder: Folder to search

        Returns:
            List of (video_path, subtitle_path) tuples
        """
        # Video extensions
        video_extensions = {".mp4", ".mkv", ".avi", ".m4v", ".mov"}
        # Subtitle extensions
        subtitle_extensions = {".ass", ".srt", ".ssa"}

        # Get all videos
        videos = [
            f for f in folder.iterdir() if f.is_file() and f.suffix.lower() in video_extensions
        ]

        # Match with subtitles
        pairs = []
        for video in videos:
            # Look for subtitle with same name
            for sub_ext in subtitle_extensions:
                subtitle = video.with_suffix(sub_ext)
                if subtitle.exists():
                    pairs.append((video, subtitle))
                    break  # Found a match, stop looking

        # Sort pairs by video name (natural sort)
        from anki_miner.utils import natural_sort_key

        pairs.sort(key=lambda pair: natural_sort_key(pair[0].name))

        return pairs

    def process_folder(
        self,
        folder: Path,
        preview_mode: bool = False,
        progress_callback: ProgressCallback | None = None,
    ) -> list[ProcessingResult]:
        """Process all episodes in a folder.

        Args:
            folder: Folder containing video/subtitle pairs
            preview_mode: If True, only show words without creating cards
            progress_callback: Optional progress callback

        Returns:
            List of ProcessingResult for each episode
        """
        # Find video/subtitle pairs
        self.presenter.show_info(f"Scanning folder: {folder}")
        pairs = self.find_video_subtitle_pairs(folder)

        if not pairs:
            self.presenter.show_warning("No video/subtitle pairs found")
            return []

        self.presenter.show_success(f"Found {len(pairs)} video/subtitle pairs")

        # Process each pair
        results = []
        total_cards = 0

        if progress_callback:
            progress_callback.on_start(len(pairs), "Processing episodes")

        for i, (video_file, subtitle_file) in enumerate(pairs, 1):
            self.presenter.show_info(f"\n[{i}/{len(pairs)}] Processing: {video_file.name}")

            try:
                result = self.episode_processor.process_episode(
                    video_file,
                    subtitle_file,
                    preview_mode=preview_mode,
                    progress_callback=None,  # Don't pass nested progress
                )

                results.append(result)
                total_cards += result.cards_created

                if progress_callback:
                    progress_callback.on_progress(
                        i, f"{video_file.name}: {result.cards_created} cards"
                    )

            except Exception as e:
                self.presenter.show_error(f"Error processing {video_file.name}: {e}")
                results.append(
                    ProcessingResult(
                        total_words_found=0,
                        new_words_found=0,
                        cards_created=0,
                        errors=[str(e)],
                        elapsed_time=0.0,
                    )
                )

                if progress_callback:
                    progress_callback.on_error(video_file.name, str(e))

        if progress_callback:
            progress_callback.on_complete()

        # Show summary
        self.presenter.show_success(
            f"\nFolder processing complete: {total_cards} total cards created"
        )

        return results
