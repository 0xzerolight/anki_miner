"""Orchestrator for processing a single episode."""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import AnkiMinerException
from anki_miner.interfaces import PresenterProtocol, ProgressCallback
from anki_miner.models import CardPayload, MediaData, ProcessingResult, TokenizedWord
from anki_miner.models.youtube import SubMode
from anki_miner.services import (
    AnkiService,
    DefinitionService,
    MediaExtractorService,
    SubtitleParserService,
    WordFilterService,
)
from anki_miner.utils import ensure_directory

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from anki_miner.models import LineLemmas
    from anki_miner.services.frequency_service import FrequencyService
    from anki_miner.services.known_word_db import KnownWordDB
    from anki_miner.services.pitch_accent_service import PitchAccentService
    from anki_miner.services.stats_service import StatsService
    from anki_miner.services.word_list_service import WordListService
    from anki_miner.services.youtube_fetcher import YouTubeFetcherService


def _resolve_identity(override: str | None, default: str) -> str:
    """Return ``override`` when supplied (non-None), else ``default``.

    Preserves the historical ``is not None`` semantics so an explicit empty
    string is honored as-is.
    """
    return override if override is not None else default


@dataclass
class _EpisodeContext:
    """Mutable accumulator carried through the five phase helpers.

    Stores the immutable inputs every phase needs (timing, identity, file
    strings) plus a small set of accumulator fields that ``build_result``
    reads when constructing the final ``ProcessingResult``. Each phase
    helper returns its own outputs explicitly; ``ctx`` is intentionally a
    thin state holder, not a god-object.
    """

    start_time: float
    video_file_str: str
    subtitle_file_str: str
    episode_name: str
    series_name: str

    # Accumulator fields populated as phases progress.
    errors: list[str] = field(default_factory=list)
    total_words_found: int = 0
    new_words_found: int = 0
    comprehension_percentage: float = 0.0

    def build_result(self, **overrides: Any) -> ProcessingResult:
        """Construct a ProcessingResult from accumulated state.

        ``overrides`` lets the caller stamp values that aren't part of the
        default accumulator (e.g. ``cards_created``, ``card_ids``) or
        override the accumulated defaults (e.g. force ``errors``).
        """
        defaults: dict[str, Any] = {
            "total_words_found": self.total_words_found,
            "new_words_found": self.new_words_found,
            "cards_created": 0,
            "errors": list(self.errors),
            "elapsed_time": time.time() - self.start_time,
            "comprehension_percentage": self.comprehension_percentage,
            "video_file": self.video_file_str,
            "subtitle_file": self.subtitle_file_str,
        }
        defaults.update(overrides)
        return ProcessingResult(**defaults)


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
        youtube_fetcher: YouTubeFetcherService | None = None,
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
            youtube_fetcher: Optional YouTube fetcher service. Required for
                ``process_youtube_url``; unused by ``process_episode``.
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
        self._youtube_fetcher = youtube_fetcher
        self._cancelled = False

    def cancel(self) -> None:
        """Request cancellation of processing."""
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        """Check if cancellation has been requested."""
        return self._cancelled

    def _allocate_run_temp_folder(self) -> Path:
        """Create an isolated temp directory for a single episode run.

        Each call returns a fresh, uniquely-named directory under the
        system temp root. If ANKI_MINER_KEEP_TEMP is set in the
        environment, the directory is created under
        self.config.media_temp_folder instead so the user can inspect
        intermediate files; in that case cleanup is also skipped by
        process_episode's finally block.
        """
        if os.environ.get("ANKI_MINER_KEEP_TEMP"):
            base = self.config.media_temp_folder
            ensure_directory(base)
            run_dir = base / f"run_{uuid.uuid4().hex[:8]}"
            run_dir.mkdir(parents=True, exist_ok=True)
            return run_dir

        return Path(tempfile.mkdtemp(prefix="anki_miner_"))

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

    def _cancelled_result_from_ctx(self, ctx: _EpisodeContext) -> ProcessingResult:
        """Cancellation result populated from the accumulator ctx."""
        return self._make_cancelled_result(
            ctx.start_time,
            total_words_found=ctx.total_words_found,
            new_words_found=ctx.new_words_found,
        )

    def _phase1_parse(
        self,
        ctx: _EpisodeContext,
        subtitle_file: Path,
    ) -> tuple[list[TokenizedWord], list[LineLemmas] | None]:
        """Phase 1: parse subtitles into tokenized words (and optionally a line index).

        Returns the raw parse output; mutates ``ctx.total_words_found``.
        """
        self.presenter.show_info(f"Step 1/5 — Parsing subtitles: {subtitle_file.name}")
        line_index: list[LineLemmas] | None = None
        if self.config.use_i_plus_one_filter:
            all_words, line_index = self.subtitle_parser.parse_subtitle_file_with_index(subtitle_file)
        else:
            all_words = self.subtitle_parser.parse_subtitle_file(subtitle_file)
        self.presenter.show_success(f"Found {len(all_words)} unique words")
        ctx.total_words_found = len(all_words)
        return all_words, line_index

    def _phase2_filter(
        self,
        ctx: _EpisodeContext,
        all_words: list[TokenizedWord],
        line_index: list[LineLemmas] | None,
        cross_episode_counts: dict[str, int] | None,
    ) -> list[TokenizedWord]:
        """Phase 2: attach frequency data, filter against known vocab, apply optional filters.

        Mutates ``ctx.new_words_found`` and ``ctx.comprehension_percentage``.
        Records difficulty stats if a stats service is available.
        """
        # Attach frequency data if available (mutates words in-place).
        if self.frequency_service and self.frequency_service.is_available():
            for word in all_words:
                word.frequency_rank = self.frequency_service.lookup(word.lemma)
            ranked_count = sum(1 for w in all_words if w.frequency_rank is not None)
            self.presenter.show_info(f"Frequency data: {ranked_count}/{len(all_words)} words ranked")

        # Filter against existing vocabulary.
        self.presenter.show_info("Step 2/5 — Filtering against known vocabulary")
        if self.known_word_db and self.known_word_db.is_available():
            known_words = self.known_word_db.get_known_words()
            # Sync with Anki to keep DB up to date. Pass the pre-fetched
            # ``known_words`` so the DB skips its internal scan; merge the
            # diff in-memory below to avoid a post-sync re-read.
            anki_vocab = self.anki_service.get_existing_vocabulary()
            added, total = self.known_word_db.sync_with_anki(anki_vocab, existing=known_words)
            if added > 0:
                self.presenter.show_info(f"Known word DB synced: {added} new words ({total} total)")
                known_words = known_words | (anki_vocab - known_words)
            unknown_words = self.word_filter.filter_unknown(all_words, known_words)
        else:
            existing_words = self.anki_service.get_existing_vocabulary()
            unknown_words = self.word_filter.filter_unknown(all_words, existing_words)
        self.presenter.show_success(f"{len(unknown_words)} new words to mine")

        # Comprehension percentage.
        comprehension = ((len(all_words) - len(unknown_words)) / len(all_words)) * 100 if all_words else 0.0
        self.presenter.show_info(f"Comprehension: {comprehension:.1f}% of words already known")
        ctx.comprehension_percentage = comprehension

        # Surface the "everything was already known" case explicitly. Without
        # this, users who enable a card-format option (bold target word, etc.)
        # and re-mine the same episode see no visible change because every
        # word was filtered out before card creation. The pipeline silently
        # produces zero cards. Issue #20 (reopened): user mistook silent
        # no-op for "bold isn't working".
        if all_words and not unknown_words:
            self.presenter.show_warning(
                f"All {len(all_words)} words from this subtitle are already in your "
                "Anki collection — no new cards will be created. Card-format "
                "options (bold target word, etc.) only apply to newly mined cards."
            )

        # Frequency rank cutoff.
        if self.config.max_frequency_rank > 0:
            before = len(unknown_words)
            unknown_words = self.word_filter.filter_by_frequency(unknown_words, self.config.max_frequency_rank)
            filtered_out = before - len(unknown_words)
            if filtered_out > 0:
                self.presenter.show_info(
                    f"Frequency filter: removed {filtered_out} words " f"outside top {self.config.max_frequency_rank}"
                )

        # Word list (blacklist/whitelist) filter.
        if self.word_list_service and self.word_list_service.is_available():
            before = len(unknown_words)
            unknown_words = self.word_filter.filter_by_word_lists(unknown_words, self.word_list_service)
            filtered_out = before - len(unknown_words)
            if filtered_out > 0:
                self.presenter.show_info(f"Word list filter: removed {filtered_out} words")

        # Sentence deduplication. i+1 filter does its own sentence picking;
        # dedup would be a no-op (post-i+1 sentences are unique by construction).
        if self.config.deduplicate_sentences and not self.config.use_i_plus_one_filter:
            before = len(unknown_words)
            unknown_words = self.word_filter.deduplicate_by_sentence(unknown_words)
            deduped = before - len(unknown_words)
            if deduped > 0:
                self.presenter.show_info(f"Sentence deduplication: removed {deduped} duplicate-sentence words")

        # Cross-episode frequency filter.
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

        # i+1 sentence filtering. Restricts mining to words with an i+1 example
        # sentence (exactly one mineable unknown). Rescans lines and may swap
        # the chosen sentence per word. Drops words with no i+1 coverage.
        if self.config.use_i_plus_one_filter:
            before = len(unknown_words)
            unknown_words = self.word_filter.filter_i_plus_one(unknown_words, line_index or [])
            kept = len(unknown_words)
            pct = (kept / before * 100.0) if before else 0.0
            self.presenter.show_info(f"i+1 filter: kept {kept}/{before} words ({pct:.0f}%)")

        # Sentence length filter (Issue #33). Drops words whose FINAL example
        # sentence exceeds the configured audio-duration and/or character caps.
        # Runs AFTER i+1 because filter_i_plus_one swaps each word's sentence
        # (and duration) to its chosen i+1 line — applying the cap before that
        # swap would be silently bypassed by the swap target.
        if self.config.use_sentence_length_filter and (
            self.config.max_sentence_duration_seconds > 0.0 or self.config.max_sentence_chars > 0
        ):
            before = len(unknown_words)
            unknown_words = self.word_filter.filter_by_sentence_length(
                unknown_words,
                max_duration=self.config.max_sentence_duration_seconds,
                max_chars=self.config.max_sentence_chars,
            )
            filtered_out = before - len(unknown_words)
            if filtered_out > 0:
                caps = []
                if self.config.max_sentence_duration_seconds > 0.0:
                    caps.append(f"{self.config.max_sentence_duration_seconds:g}s")
                if self.config.max_sentence_chars > 0:
                    caps.append(f"{self.config.max_sentence_chars} chars")
                self.presenter.show_info(
                    f"Sentence length filter: removed {filtered_out} words " f"(cap: {', '.join(caps)})"
                )

        # Record difficulty data if stats service available.
        if self.stats_service and self.stats_service.is_available():
            self.stats_service.record_difficulty(
                series_name=ctx.series_name,
                episode_name=ctx.episode_name,
                total_words=len(all_words),
                unknown_words=len(unknown_words),
                unique_words=len(all_words),
            )

        ctx.new_words_found = len(unknown_words)
        return unknown_words

    def _phase3_extract(
        self,
        ctx: _EpisodeContext,
        video_file: Path,
        unknown_words: list[TokenizedWord],
        progress_callback: ProgressCallback | None,
        run_temp_folder: Path,
        audio_track_override: int | None = None,
    ) -> list[tuple[TokenizedWord, MediaData]]:
        """Phase 3: extract media (screenshots + audio) for each unknown word."""
        self.presenter.show_info("Step 3/5 — Extracting media from video")
        media_results: list[tuple[TokenizedWord, MediaData]] = self.media_extractor.extract_media_batch(
            video_file,
            unknown_words,
            progress_callback,
            cancelled_check=lambda: self._cancelled,
            temp_folder=run_temp_folder,
            audio_track_override=audio_track_override,
        )
        return media_results

    def _phase4_lookup(
        self,
        ctx: _EpisodeContext,
        media_results: list[tuple[TokenizedWord, MediaData]],
        progress_callback: ProgressCallback | None,
    ) -> tuple[
        list[str | None],
        list[str | None],
        list[tuple[str | None, str | None]],
    ]:
        """Phase 4: look up definitions, optional glossaries, and pitch accents."""
        self.presenter.show_info("Step 4/5 — Fetching definitions")
        words_with_media = [word for word, _ in media_results]
        definitions = self.definition_service.get_definitions_batch(
            [w.lemma for w in words_with_media],
            progress_callback,
        )
        self.presenter.show_success(f"Found {sum(1 for d in definitions if d)} definitions")

        # Optional: fetch concatenated multi-dict glossary if the user mapped
        # the Glossary field. Skipped otherwise to avoid the extra chain walk
        # per word.
        glossaries: list[str | None] = [None] * len(words_with_media)
        if self.config.anki_fields.get("glossary"):
            glossaries = self.definition_service.get_glossaries_batch(
                [w.lemma for w in words_with_media],
                progress_callback,
            )

        # Pitch accents if available.
        pitch_data: list[tuple[str | None, str | None]] = [(None, None)] * len(words_with_media)
        if self.pitch_accent_service and self.pitch_accent_service.is_available():
            pitch_data = self.pitch_accent_service.lookup_batch_detailed(
                [(w.lemma, w.reading, w.pos) for w in words_with_media],
                fmt=self.config.pitch_category_format,
            )
            found_count = sum(1 for pos, _ in pitch_data if pos)
            self.presenter.show_info(f"Pitch accent data: {found_count}/{len(words_with_media)} words")

        return definitions, glossaries, pitch_data

    def _phase5_create(
        self,
        ctx: _EpisodeContext,
        media_results: list[tuple[TokenizedWord, MediaData]],
        definitions: list[str | None],
        glossaries: list[str | None],
        pitch_data: list[tuple[str | None, str | None]],
        progress_callback: ProgressCallback | None,
    ) -> tuple[int, list[int]]:
        """Phase 5: build CardPayloads and submit them to Anki.

        Returns ``(cards_created, created_note_ids)``.
        """
        self.presenter.show_info("Step 5/5 — Creating Anki cards")
        card_data: list[CardPayload] = []
        for (word, media), definition, glossary, (pitch_position, pitch_category) in zip(
            media_results, definitions, glossaries, pitch_data, strict=True
        ):
            if not definition:
                continue

            extra_fields: dict[str, str] = {}
            if pitch_position:
                extra_fields["pitch_position"] = pitch_position
            if pitch_category:
                extra_fields["pitch_category"] = pitch_category
            if word.frequency_rank is not None:
                extra_fields["frequency"] = str(word.frequency_rank)
            if glossary:
                extra_fields["glossary"] = glossary

            card_data.append(
                CardPayload(
                    word=word,
                    media=media,
                    definition=definition,
                    extra_fields=extra_fields if extra_fields else None,
                )
            )

        skipped_words = [
            word.lemma for (word, _), definition in zip(media_results, definitions, strict=True) if not definition
        ]
        if skipped_words:
            preview = ", ".join(skipped_words[:10])
            more = f" (+{len(skipped_words) - 10} more)" if len(skipped_words) > 10 else ""
            self.presenter.show_warning(f"Skipped {len(skipped_words)} words with no definition found: {preview}{more}")

        cards_created = self.anki_service.create_cards_batch(card_data, progress_callback)
        created_note_ids = list(self.anki_service.last_created_note_ids)

        self.presenter.show_success(f"Successfully created {cards_created} cards")

        # Add newly mined words to known word DB.
        # Store mined_form so the local DB matches what Anki stores in the
        # Expression first field (POS-aware via mined_form); Issue #5.
        if self.known_word_db and self.known_word_db.is_available() and card_data:
            mined_words = {payload.word.mined_form for payload in card_data}
            self.known_word_db.add_words(mined_words, source="mined")

        return cards_created, created_note_ids

    def process_episode(
        self,
        video_file: Path,
        subtitle_file: Path,
        preview_mode: bool = False,
        progress_callback: ProgressCallback | None = None,
        curation_callback: Callable[[list], list] | None = None,
        cross_episode_counts: dict[str, int] | None = None,
        episode_name_override: str | None = None,
        series_name_override: str | None = None,
        audio_track_override: int | None = None,
    ) -> ProcessingResult:
        """Process a single episode and create Anki cards.

        Orchestrates the five phase helpers: parse → filter → extract media →
        lookup definitions/pitch → create cards. Each phase is a small method
        on this class; this entrypoint owns only cancellation checkpoints,
        early-return paths, and temp folder cleanup.

        Args:
            video_file: Path to video file.
            subtitle_file: Path to subtitle file.
            preview_mode: If True, only show words without creating cards.
            progress_callback: Optional progress callback.
            curation_callback: Optional callback for word curation. Receives
                filtered words, returns user-selected subset. Empty list cancels.
            cross_episode_counts: Optional cross-episode word frequency counts.
            episode_name_override: Optional override for the episode identity
                passed to stats_service. When ``None`` (default) the identity
                is derived from ``video_file.stem`` (preserves current anime
                flow). Used by ``process_youtube_url`` to record
                ``YT:<video_id>``.
            series_name_override: Optional override for the series identity
                passed to stats_service. When ``None`` the identity is derived
                from ``video_file.parent.name``.
            audio_track_override: Optional 0-indexed audio track to extract instead of
                auto-detecting Japanese. None (default) preserves existing JP auto-detect behavior.

        Returns:
            ProcessingResult with statistics.
        """
        ctx = _EpisodeContext(
            start_time=time.time(),
            video_file_str=str(video_file),
            subtitle_file_str=str(subtitle_file),
            episode_name=_resolve_identity(episode_name_override, video_file.stem),
            series_name=_resolve_identity(series_name_override, video_file.parent.name),
        )
        run_temp_folder = self._allocate_run_temp_folder()
        keep_temp = bool(os.environ.get("ANKI_MINER_KEEP_TEMP"))

        try:
            all_words, line_index = self._phase1_parse(ctx, subtitle_file)
            if not all_words:
                self.presenter.show_warning("No words found in subtitles")
                return ctx.build_result()
            if self._cancelled:
                return self._cancelled_result_from_ctx(ctx)

            unknown_words = self._phase2_filter(ctx, all_words, line_index, cross_episode_counts)
            if not unknown_words:
                self.presenter.show_info("All words already in Anki!")
                return ctx.build_result(new_words_found=0)
            if self._cancelled:
                return self._cancelled_result_from_ctx(ctx)

            if curation_callback is not None and not preview_mode:
                unknown_words = curation_callback(unknown_words)
                ctx.new_words_found = len(unknown_words)
                if not unknown_words:
                    return self._cancelled_result_from_ctx(ctx)
                self.presenter.show_info(f"User selected {len(unknown_words)} words for card creation")

            if preview_mode:
                self.presenter.show_word_preview(unknown_words)
                return ctx.build_result()

            media_results = self._phase3_extract(
                ctx, video_file, unknown_words, progress_callback, run_temp_folder, audio_track_override
            )
            if self._cancelled:
                return self._cancelled_result_from_ctx(ctx)
            if not media_results:
                self.presenter.show_warning("No media extracted successfully")
                return ctx.build_result(errors=["Media extraction failed for all words"])
            self.presenter.show_success(f"Extracted media for {len(media_results)} words")

            definitions, glossaries, pitch_data = self._phase4_lookup(ctx, media_results, progress_callback)
            if self._cancelled:
                return self._cancelled_result_from_ctx(ctx)

            cards_created, created_note_ids = self._phase5_create(
                ctx, media_results, definitions, glossaries, pitch_data, progress_callback
            )
            result = ctx.build_result(cards_created=cards_created, card_ids=created_note_ids)
            self._record_session(ctx, result)
            return result

        except AnkiMinerException as e:
            ctx.errors.append(str(e))
            self.presenter.show_error(f"Error: {e}")
            return ctx.build_result(total_words_found=0, new_words_found=0)
        except Exception as e:
            ctx.errors.append(f"Unexpected error: {e}")
            self.presenter.show_error(f"Unexpected error: {e}")
            return ctx.build_result(total_words_found=0, new_words_found=0)
        finally:
            if keep_temp:
                logger.info(
                    "ANKI_MINER_KEEP_TEMP set; leaving run temp folder at %s",
                    run_temp_folder,
                )
            else:
                shutil.rmtree(run_temp_folder, ignore_errors=True)

    def _record_session(self, ctx: _EpisodeContext, result: ProcessingResult) -> None:
        """Record a mining session in the stats service if one is configured."""
        if not (self.stats_service and self.stats_service.is_available()):
            return
        from anki_miner.models.stats import MiningSession

        self.stats_service.record_session(
            MiningSession(
                series_name=ctx.series_name,
                episode_name=ctx.episode_name,
                total_words=result.total_words_found,
                unknown_words=result.new_words_found,
                cards_created=result.cards_created,
                elapsed_time=result.elapsed_time,
            )
        )

    def process_youtube_url(
        self,
        url: str,
        video_id: str,
        workspace: Path,
        sub_mode: SubMode,
        *,
        cancel_event: threading.Event,
        progress_callback: ProgressCallback | None = None,
        fetch_progress_cb: Callable[[str, float | None], None] | None = None,
        curation_callback: Callable[[list], list] | None = None,
        preview_mode: bool = False,
    ) -> ProcessingResult:
        """Fetch a YouTube video + subs then run the standard mining pipeline.

        The ``workspace`` directory is owned by the caller (the worker) — this
        method only writes into it via the fetcher; cleanup (``rmtree``) is the
        caller's responsibility, typically in a ``try/finally``.

        Episode identity recorded to stats_service is ``YT:<video_id>`` with
        series ``YouTube`` so that YouTube mining rows never collide with
        anime folders that happen to share a stem.

        Args:
            url: YouTube video URL (or anything yt-dlp accepts).
            video_id: Pre-extracted video_id; must match the ID yt-dlp will
                write file names with (the worker takes it from probe_metadata).
            workspace: Pre-created, caller-owned directory that yt-dlp writes
                the video and subtitle files into.
            sub_mode: "manual_only" or "auto_only" — chosen by the user based
                on what probe_metadata reported as available.
            cancel_event: Threading event set by the worker on cancellation;
                forwarded to the fetcher so in-flight yt-dlp can be killed.
            progress_callback: Optional ``ProgressCallback`` forwarded to
                ``process_episode`` for mining-phase reporting (media extract,
                definitions, card creation).
            fetch_progress_cb: Optional ``(label, frac)`` callable forwarded
                to ``YouTubeFetcherService.fetch_video`` for download-phase
                reporting. ``frac`` is in [0.0, 1.0] or ``None`` for
                indeterminate stages (merging, post-processing).
            curation_callback: Optional callback for word curation. Forwarded
                unchanged to ``process_episode``; see its docstring for semantics.
            preview_mode: If True, skip card creation and show previews only.
                Forwarded unchanged to ``process_episode``.

        Returns:
            ProcessingResult from the mining pipeline, with episode identity
            overridden to ``YT:<video_id>``.

        Raises:
            RuntimeError: if no YouTubeFetcherService was injected.
            Any fetcher exception propagates unchanged (no workspace cleanup
            happens here — the worker handles it).
        """
        if self._youtube_fetcher is None:
            raise RuntimeError("YouTubeFetcherService not injected — check service_factory")

        if cancel_event.is_set():
            return self._make_cancelled_result(time.time())

        fetched = self._youtube_fetcher.fetch_video(
            url,
            video_id,
            workspace,
            sub_mode,
            progress_cb=fetch_progress_cb,
            cancel_event=cancel_event,
        )

        return self.process_episode(
            fetched.video_file,
            fetched.subtitle_file,
            preview_mode=preview_mode,
            progress_callback=progress_callback,
            curation_callback=curation_callback,
            episode_name_override=f"YT:{video_id}",
            series_name_override="YouTube",
        )
