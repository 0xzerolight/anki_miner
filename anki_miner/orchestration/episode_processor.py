"""Orchestrator for processing a single episode."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import AnkiMinerException
from anki_miner.interfaces import PresenterProtocol, ProgressCallback
from anki_miner.models import ProcessingResult
from anki_miner.services import (
    AnkiService,
    DefinitionService,
    MediaExtractorService,
    SubtitleParserService,
    WordFilterService,
)
from anki_miner.utils.file_utils import cleanup_temp_files

if TYPE_CHECKING:
    from anki_miner.services.frequency_service import FrequencyService
    from anki_miner.services.known_word_db import KnownWordDB
    from anki_miner.services.pitch_accent_service import PitchAccentService
    from anki_miner.services.stats_service import StatsService
    from anki_miner.services.word_list_service import WordListService


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
        pitch_accent_service: PitchAccentService | None = None,
        frequency_service: FrequencyService | None = None,
        known_word_db: KnownWordDB | None = None,
        word_list_service: WordListService | None = None,
        stats_service: StatsService | None = None,
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
            pitch_accent_service: Optional pitch accent lookup service
            frequency_service: Optional word frequency lookup service
            known_word_db: Optional local known word database
            word_list_service: Optional word blacklist/whitelist service
            stats_service: Optional statistics recording service
        """
        self.config = config
        self.subtitle_parser = subtitle_parser
        self.word_filter = word_filter
        self.media_extractor = media_extractor
        self.definition_service = definition_service
        self.anki_service = anki_service
        self.presenter = presenter
        self.pitch_accent_service = pitch_accent_service
        self.frequency_service = frequency_service
        self.known_word_db = known_word_db
        self.word_list_service = word_list_service
        self.stats_service = stats_service
        self._cancelled = False

    def cancel(self) -> None:
        """Request cancellation of processing."""
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        """Check if cancellation has been requested."""
        return self._cancelled

    def _make_cancelled_result(
        self,
        start_time: float,
        total_words_found: int = 0,
        new_words_found: int = 0,
        cards_created: int = 0,
    ) -> ProcessingResult:
        """Create a ProcessingResult for a cancelled operation."""
        return ProcessingResult(
            total_words_found=total_words_found,
            new_words_found=new_words_found,
            cards_created=cards_created,
            errors=["Processing cancelled by user"],
            elapsed_time=time.time() - start_time,
        )

    def process_episode(
        self,
        video_file: Path,
        subtitle_file: Path,
        preview_mode: bool = False,
        progress_callback: ProgressCallback | None = None,
        curation_callback: Callable[[list], list] | None = None,
        cross_episode_counts: dict[str, int] | None = None,
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
            curation_callback: Optional callback for word curation. Receives
                filtered words, returns user-selected subset. Empty list cancels.
            cross_episode_counts: Optional cross-episode word frequency counts

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

            # Check cancellation after Phase 1
            if self._cancelled:
                return self._make_cancelled_result(start_time, total_words_found=len(all_words))

            # Attach frequency data if available
            if self.frequency_service and self.frequency_service.is_available():
                for word in all_words:
                    word.frequency_rank = self.frequency_service.lookup(word.lemma)
                ranked_count = sum(1 for w in all_words if w.frequency_rank is not None)
                self.presenter.show_info(
                    f"Frequency data: {ranked_count}/{len(all_words)} words ranked"
                )

            # Phase 2: Filter against existing vocabulary
            self.presenter.show_info("Step 2/5 \u2014 Filtering against known vocabulary")
            if self.known_word_db and self.known_word_db.is_available():
                known_words = self.known_word_db.get_known_words()
                unknown_words = self.word_filter.filter_unknown(all_words, known_words)
                # Sync with Anki to keep DB up to date
                anki_vocab = self.anki_service.get_existing_vocabulary()
                added, total = self.known_word_db.sync_with_anki(anki_vocab)
                if added > 0:
                    self.presenter.show_info(
                        f"Known word DB synced: {added} new words ({total} total)"
                    )
                    # Re-filter with updated known words
                    known_words = self.known_word_db.get_known_words()
                    unknown_words = self.word_filter.filter_unknown(all_words, known_words)
            else:
                existing_words = self.anki_service.get_existing_vocabulary()
                unknown_words = self.word_filter.filter_unknown(all_words, existing_words)
            self.presenter.show_success(f"{len(unknown_words)} new words to mine")

            # Calculate comprehension percentage
            comprehension = (
                ((len(all_words) - len(unknown_words)) / len(all_words)) * 100 if all_words else 0.0
            )
            self.presenter.show_info(f"Comprehension: {comprehension:.1f}% of words already known")

            # Apply frequency filter if configured
            if self.config.max_frequency_rank > 0:
                before = len(unknown_words)
                unknown_words = self.word_filter.filter_by_frequency(
                    unknown_words, self.config.max_frequency_rank
                )
                filtered_out = before - len(unknown_words)
                if filtered_out > 0:
                    self.presenter.show_info(
                        f"Frequency filter: removed {filtered_out} words "
                        f"outside top {self.config.max_frequency_rank}"
                    )

            # Apply word list filtering if available
            if self.word_list_service and self.word_list_service.is_available():
                before = len(unknown_words)
                unknown_words = self.word_filter.filter_by_word_lists(
                    unknown_words, self.word_list_service
                )
                filtered_out = before - len(unknown_words)
                if filtered_out > 0:
                    self.presenter.show_info(f"Word list filter: removed {filtered_out} words")

            # Apply sentence deduplication if configured
            if self.config.deduplicate_sentences:
                before = len(unknown_words)
                unknown_words = self.word_filter.deduplicate_by_sentence(unknown_words)
                deduped = before - len(unknown_words)
                if deduped > 0:
                    self.presenter.show_info(
                        f"Sentence deduplication: removed {deduped} duplicate-sentence words"
                    )

            # Apply cross-episode frequency filter if provided
            if cross_episode_counts is not None and self.config.min_episode_appearances > 1:
                before = len(unknown_words)
                unknown_words = self.word_filter.filter_by_episode_count(
                    unknown_words,
                    cross_episode_counts,
                    self.config.min_episode_appearances,
                )
                filtered_out = before - len(unknown_words)
                if filtered_out > 0:
                    self.presenter.show_info(
                        f"Cross-episode filter: removed {filtered_out} words "
                        f"appearing in fewer than {self.config.min_episode_appearances} episodes"
                    )

            # Record difficulty data if stats service available
            if self.stats_service and self.stats_service.is_available():
                self.stats_service.record_difficulty(
                    series_name=video_file.parent.name,
                    episode_name=video_file.stem,
                    total_words=len(all_words),
                    unknown_words=len(unknown_words),
                    unique_words=len(all_words),
                )

            if not unknown_words:
                self.presenter.show_info("All words already in Anki!")
                return ProcessingResult(
                    total_words_found=len(all_words),
                    new_words_found=0,
                    cards_created=0,
                    errors=[],
                    elapsed_time=time.time() - start_time,
                    comprehension_percentage=comprehension,
                )

            # Check cancellation after Phase 2
            if self._cancelled:
                return self._make_cancelled_result(
                    start_time,
                    total_words_found=len(all_words),
                    new_words_found=len(unknown_words),
                )

            # Word curation callback (if provided, not in preview mode)
            if curation_callback is not None and not preview_mode:
                unknown_words = curation_callback(unknown_words)
                if not unknown_words:
                    return self._make_cancelled_result(
                        start_time,
                        total_words_found=len(all_words),
                        new_words_found=0,
                    )
                self.presenter.show_info(
                    f"User selected {len(unknown_words)} words for card creation"
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
                    comprehension_percentage=comprehension,
                )

            # Phase 3: Extract media
            self.presenter.show_info("Step 3/5 \u2014 Extracting media from video")
            media_results = self.media_extractor.extract_media_batch(
                video_file,
                unknown_words,
                progress_callback,
                cancelled_check=lambda: self._cancelled,
            )

            if self._cancelled:
                return self._make_cancelled_result(
                    start_time,
                    total_words_found=len(all_words),
                    new_words_found=len(unknown_words),
                )

            if not media_results:
                self.presenter.show_warning("No media extracted successfully")
                return ProcessingResult(
                    total_words_found=len(all_words),
                    new_words_found=len(unknown_words),
                    cards_created=0,
                    errors=["Media extraction failed for all words"],
                    elapsed_time=time.time() - start_time,
                    comprehension_percentage=comprehension,
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

            # Check cancellation after Phase 4
            if self._cancelled:
                return self._make_cancelled_result(
                    start_time,
                    total_words_found=len(all_words),
                    new_words_found=len(unknown_words),
                )

            # Look up pitch accents if available
            pitch_accents: list[str | None] = [None] * len(words_with_media)
            if self.pitch_accent_service and self.pitch_accent_service.is_available():
                pitch_accents = self.pitch_accent_service.lookup_batch(
                    [(w.lemma, w.reading) for w in words_with_media]
                )
                found_count = sum(1 for p in pitch_accents if p)
                self.presenter.show_info(
                    f"Pitch accent data: {found_count}/{len(words_with_media)} words"
                )

            # Phase 5: Create cards
            self.presenter.show_info("Step 5/5 \u2014 Creating Anki cards")
            # Combine words, media, definitions, and extra data
            card_data: list[tuple] = []
            for (word, media), definition, pitch_accent in zip(
                media_results, definitions, pitch_accents, strict=True
            ):
                if definition is None:
                    continue

                extra_fields: dict[str, str] = {}
                if pitch_accent:
                    extra_fields["pitch_accent"] = pitch_accent
                if word.frequency_rank is not None:
                    extra_fields["frequency_rank"] = str(word.frequency_rank)

                card_data.append((word, media, definition, extra_fields if extra_fields else None))

            skipped = len(media_results) - len(card_data)
            if skipped:
                self.presenter.show_warning(f"Skipped {skipped} words with no definition found")

            cards_created = self.anki_service.create_cards_batch(
                card_data,
                progress_callback,
            )

            self.presenter.show_success(f"Successfully created {cards_created} cards")

            # Add newly mined words to known word DB
            if self.known_word_db and self.known_word_db.is_available() and card_data:
                mined_words = {word.lemma for word, _, _, _ in card_data}
                self.known_word_db.add_words(mined_words, source="mined")

            result = ProcessingResult(
                total_words_found=len(all_words),
                new_words_found=len(unknown_words),
                cards_created=cards_created,
                errors=errors,
                elapsed_time=time.time() - start_time,
                comprehension_percentage=comprehension,
            )

            # Record mining session if stats service available
            if self.stats_service and self.stats_service.is_available():
                from anki_miner.models.stats import MiningSession

                self.stats_service.record_session(
                    MiningSession(
                        series_name=video_file.parent.name,
                        episode_name=video_file.stem,
                        total_words=result.total_words_found,
                        unknown_words=result.new_words_found,
                        cards_created=result.cards_created,
                        elapsed_time=result.elapsed_time,
                    )
                )

            return result

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
