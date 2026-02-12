"""Orchestrator for processing a single episode."""

import time
from pathlib import Path

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import AnkiMinerException
from anki_miner.interfaces import PresenterProtocol, ProgressCallback
from anki_miner.models import MediaData, ProcessingResult, TokenizedWord
from anki_miner.services import (
    AnkiService,
    DefinitionService,
    MediaExtractorService,
    SubtitleParserService,
    WordFilterService,
)
from anki_miner.utils.file_utils import cleanup_temp_files


class EpisodeProcessor:
    """Orchestrate processing of a single episode."""

    def __init__(
        self,
        config: AnkiMinerConfig,
        subtitle_parser: SubtitleParserService,
        word_filter: WordFilterService,
        media_extractor: MediaExtractorService,
        definition_service: DefinitionService,
        anki_service: AnkiService,
        presenter: PresenterProtocol,
    ):
        """Initialize the episode processor.

        Args:
            config: Configuration
            subtitle_parser: Subtitle parsing service
            word_filter: Word filtering service
            media_extractor: Media extraction service
            definition_service: Definition lookup service
            anki_service: Anki integration service
            presenter: Output presenter
        """
        self.config = config
        self.subtitle_parser = subtitle_parser
        self.word_filter = word_filter
        self.media_extractor = media_extractor
        self.definition_service = definition_service
        self.anki_service = anki_service
        self.presenter = presenter

    def process_episode(
        self,
        video_file: Path,
        subtitle_file: Path,
        preview_mode: bool = False,
        progress_callback: ProgressCallback | None = None,
    ) -> ProcessingResult:
        """Process a single episode and create Anki cards.

        This orchestrates all services to:
        1. Parse subtitles
        2. Filter unknown words
        3. Extract media
        4. Fetch definitions
        5. Create Anki cards

        Args:
            video_file: Path to video file
            subtitle_file: Path to subtitle file
            preview_mode: If True, only show words without creating cards
            progress_callback: Optional progress callback

        Returns:
            ProcessingResult with statistics
        """
        start_time = time.time()
        errors: list[str] = []

        try:
            # Phase 1: Parse subtitles
            self.presenter.show_info(f"Step 1/5 \u2014 Parsing subtitles: {subtitle_file.name}")
            all_words = self.subtitle_parser.parse_subtitle_file(subtitle_file)
            self.presenter.show_success(f"Found {len(all_words)} unique words")

            if not all_words:
                self.presenter.show_warning("No words found in subtitles")
                return ProcessingResult(
                    total_words_found=0,
                    new_words_found=0,
                    cards_created=0,
                    errors=[],
                    elapsed_time=time.time() - start_time,
                )

            # Phase 2: Filter against existing vocabulary
            self.presenter.show_info("Step 2/5 \u2014 Filtering against known vocabulary")
            existing_words = self.anki_service.get_existing_vocabulary()
            unknown_words = self.word_filter.filter_unknown(all_words, existing_words)
            self.presenter.show_success(f"{len(unknown_words)} new words to mine")

            if not unknown_words:
                self.presenter.show_info("All words already in Anki!")
                return ProcessingResult(
                    total_words_found=len(all_words),
                    new_words_found=0,
                    cards_created=0,
                    errors=[],
                    elapsed_time=time.time() - start_time,
                )

            # Preview mode - show and return
            if preview_mode:
                self.presenter.show_word_preview(unknown_words)
                return ProcessingResult(
                    total_words_found=len(all_words),
                    new_words_found=len(unknown_words),
                    cards_created=0,
                    errors=[],
                    elapsed_time=time.time() - start_time,
                )

            # Phase 3: Extract media
            self.presenter.show_info("Step 3/5 \u2014 Extracting media from video")
            media_results = self.media_extractor.extract_media_batch(
                video_file, unknown_words, progress_callback
            )

            if not media_results:
                self.presenter.show_warning("No media extracted successfully")
                return ProcessingResult(
                    total_words_found=len(all_words),
                    new_words_found=len(unknown_words),
                    cards_created=0,
                    errors=["Media extraction failed for all words"],
                    elapsed_time=time.time() - start_time,
                )

            self.presenter.show_success(f"Extracted media for {len(media_results)} words")

            # Phase 4: Fetch definitions
            self.presenter.show_info("Step 4/5 \u2014 Fetching definitions")
            words_with_media = [word for word, _ in media_results]
            definitions = self.definition_service.get_definitions_batch(
                [w.lemma for w in words_with_media],
                progress_callback,
            )
            self.presenter.show_success(f"Found {sum(1 for d in definitions if d)} definitions")

            # Phase 5: Create cards
            self.presenter.show_info("Step 5/5 \u2014 Creating Anki cards")
            # Combine words, media, and definitions (skip words with no definition)
            card_data: list[tuple[TokenizedWord, MediaData, str | None]] = [
                (word, media, definition)
                for (word, media), definition in zip(media_results, definitions, strict=True)
                if definition is not None
            ]
            skipped = len(media_results) - len(card_data)
            if skipped:
                self.presenter.show_warning(f"Skipped {skipped} words with no definition found")

            cards_created = self.anki_service.create_cards_batch(
                card_data,
                progress_callback,
            )

            self.presenter.show_success(f"Successfully created {cards_created} cards")

            return ProcessingResult(
                total_words_found=len(all_words),
                new_words_found=len(unknown_words),
                cards_created=cards_created,
                errors=errors,
                elapsed_time=time.time() - start_time,
            )

        except AnkiMinerException as e:
            errors.append(str(e))
            self.presenter.show_error(f"Error: {e}")
            return ProcessingResult(
                total_words_found=0,
                new_words_found=0,
                cards_created=0,
                errors=errors,
                elapsed_time=time.time() - start_time,
            )
        except Exception as e:
            errors.append(f"Unexpected error: {e}")
            self.presenter.show_error(f"Unexpected error: {e}")
            return ProcessingResult(
                total_words_found=0,
                new_words_found=0,
                cards_created=0,
                errors=errors,
                elapsed_time=time.time() - start_time,
            )
        finally:
            # Clean up temporary media files
            if self.config.media_temp_folder.exists():
                cleanup_temp_files(self.config.media_temp_folder)
