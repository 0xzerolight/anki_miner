"""Orchestrator for processing a folder of episodes."""

from collections import defaultdict
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

    def collect_cross_episode_frequencies(self, pairs: list[tuple[Path, Path]]) -> dict[str, int]:
        """Collect word frequencies across episodes (first pass).

        For each subtitle file, parses words and counts how many distinct
        episodes each lemma appears in.

        Args:
            pairs: List of (video_path, subtitle_path) tuples

        Returns:
            Mapping of lemma to number of episodes it appears in
        """
        # Track which episodes each lemma appears in
        lemma_episodes: dict[str, set[int]] = defaultdict(set)

        for i, (_, subtitle_file) in enumerate(pairs):
            try:
                words = self.episode_processor.subtitle_parser.parse_subtitle_file(subtitle_file)
                for word in words:
                    lemma_episodes[word.lemma].add(i)
            except Exception as e:
                self.presenter.show_warning(
                    f"Cross-episode scan: skipping {subtitle_file.name}: {e}"
                )

        return {lemma: len(episodes) for lemma, episodes in lemma_episodes.items()}

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

        # First pass: collect cross-episode frequencies if enabled
        cross_episode_counts: dict[str, int] | None = None
        config = self.episode_processor.config
        if config.use_cross_episode_priority:
            self.presenter.show_info("Cross-episode analysis: scanning all subtitles...")
            cross_episode_counts = self.collect_cross_episode_frequencies(pairs)
            multi_episode = sum(
                1 for c in cross_episode_counts.values() if c >= config.min_episode_appearances
            )
            self.presenter.show_success(
                f"Cross-episode analysis complete: {multi_episode} words appear "
                f"in {config.min_episode_appearances}+ episodes"
            )

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
                    cross_episode_counts=cross_episode_counts,
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
