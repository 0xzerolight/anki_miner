"""Orchestrator for processing a single episode."""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
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
from anki_miner.models.youtube import FetchedMedia, SubMode
from anki_miner.orchestration.stage_weighted_progress import StageWeightedProgress
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
    from anki_miner.services.wordset_service import WordsetService
    from anki_miner.services.youtube_fetcher import YouTubeFetcherService


def _resolve_identity(override: str | None, default: str) -> str:
    """Return ``override`` when supplied (non-None), else ``default``.

    Preserves the historical ``is not None`` semantics so an explicit empty
    string is honored as-is.
    """
    return override if override is not None else default


def _format_timestamp(seconds: float) -> str:
    """Format a float-second offset as ``HH:MM:SS`` (negative clamps to zero)."""
    total = max(0, int(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


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
    source_label: str

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
        wordset_service: WordsetService | None = None,
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
            wordset_service: Optional bundled name wordset filter service (Issue #59)
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
        self.wordset_service = wordset_service
        self.stats_service = stats_service
        self._youtube_fetcher = youtube_fetcher
        self._cancelled = False
        # Per-run external cancel source (e.g. a worker's threading.Event
        # ``is_set``), installed/removed by process_youtube_url around each
        # run. The YouTube path must NOT set the sticky ``_cancelled`` flag:
        # this processor instance is reused across runs (YouTubeTab builds it
        # once) and ``_cancelled`` is only reset in __init__, so a sticky flag
        # set on run N would poison run N+1. Dropping the reference in a
        # ``finally`` makes the bridge per-run by construction.
        self._external_cancel: Callable[[], bool] | None = None

    def cancel(self) -> None:
        """Request cancellation of processing."""
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        """Check if cancellation has been requested.

        True when :meth:`cancel` was called (sticky; file-based worker path)
        or when the active run's external cancel source — installed by
        :meth:`process_youtube_url` from the worker's ``cancel_event`` —
        reports set.
        """
        if self._cancelled:
            return True
        external = self._external_cancel
        return external is not None and external()

    # ------------------------------------------------------------------
    # Dictionary-resource facade
    #
    # GUI callers (mining tabs, Settings → Remove dictionary) need exactly
    # two things from the dictionary stack: the offline lookup the curation
    # dialog calls, and a way to drop sqlite handles (Issue #30 file locks).
    # These wrappers keep that contract on the processor so tabs never reach
    # two levels deep into ``definition_service`` internals.
    # ------------------------------------------------------------------

    @property
    def offline_lookup_fn(self) -> Callable[[str], list[tuple[str, str]]]:
        """Offline-dictionary lookup for interactive UI (curation dialog).

        Bound form of :meth:`DefinitionService.lookup_all_offline`: takes a
        word, returns ``(provider_name, html)`` per offline provider hit.
        """
        return self.definition_service.lookup_all_offline

    def release_dictionary_resources(self) -> None:
        """Close dictionary provider handles held by the definition service.

        Drops per-dict ``index.sqlite`` connections so Settings → Remove /
        Re-import can delete the folder (Issue #30, Win11 file-lock). The
        service re-opens the chain lazily on the next lookup, so calling
        this on an idle processor is always safe; callers are responsible
        for not invoking it mid-run.
        """
        self.definition_service.close()

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
        if self.config.include_known_words:
            # Deck Builder "include everything" mode: skip known-words subtraction
            # entirely — including the Issue #42 user ignore list — and mine all
            # words that passed POS/subtype filtering. Coverage-deck builds
            # intentionally re-card words the user already knows.
            self.presenter.show_info("Step 2/5 — Known-words filter bypassed (include everything mode)")
            unknown_words = all_words
        else:
            self.presenter.show_info("Step 2/5 — Filtering against known vocabulary")
            # User-curated ignore list (Issue #42): always applied on the normal
            # mining path, regardless of the use_known_words_db toggle. The DB
            # object is always present now, but the file may not exist for users
            # who never added a word — is_available guards.
            user_words: set[str] = set()
            if self.known_word_db and self.known_word_db.is_available():
                user_words = self.known_word_db.get_words_by_source("user")

            if self.config.use_known_words_db and self.known_word_db and self.known_word_db.is_available():
                known_words = self.known_word_db.get_known_words()
                # Sync with Anki to keep DB up to date. Pass the pre-fetched
                # ``known_words`` so the DB skips its internal scan; merge the
                # diff in-memory below to avoid a post-sync re-read.
                anki_vocab = self.anki_service.get_existing_vocabulary()
                added, total = self.known_word_db.sync_with_anki(anki_vocab, existing=known_words)
                if added > 0:
                    self.presenter.show_info(f"Known word DB synced: {added} new words ({total} total)")
                    known_words = known_words | (anki_vocab - known_words)
            else:
                known_words = self.anki_service.get_existing_vocabulary()

            unknown_words = self.word_filter.filter_unknown(all_words, known_words | user_words)
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
        if self.config.max_frequency_rank > 0 and not self.config.bypass_optional_filters:
            before = len(unknown_words)
            unknown_words = self.word_filter.filter_by_frequency(unknown_words, self.config.max_frequency_rank)
            filtered_out = before - len(unknown_words)
            if filtered_out > 0:
                self.presenter.show_info(
                    f"Frequency filter: removed {filtered_out} words " f"outside top {self.config.max_frequency_rank}"
                )

        # Word list (blacklist/whitelist) filter.
        if self.word_list_service and self.word_list_service.is_available() and not self.config.bypass_optional_filters:
            before = len(unknown_words)
            unknown_words = self.word_filter.filter_by_word_lists(unknown_words, self.word_list_service)
            filtered_out = before - len(unknown_words)
            if filtered_out > 0:
                self.presenter.show_info(f"Word list filter: removed {filtered_out} words")

        # Script-type filter (hiragana-only / katakana-only). Issue #57.
        if (
            self.config.exclude_hiragana_only_words or self.config.exclude_katakana_only_words
        ) and not self.config.bypass_optional_filters:
            before = len(unknown_words)
            unknown_words = self.word_filter.filter_by_script_type(
                unknown_words,
                exclude_hiragana_only=self.config.exclude_hiragana_only_words,
                exclude_katakana_only=self.config.exclude_katakana_only_words,
            )
            removed = before - len(unknown_words)
            if removed > 0:
                kinds = []
                if self.config.exclude_hiragana_only_words:
                    kinds.append("hiragana-only")
                if self.config.exclude_katakana_only_words:
                    kinds.append("katakana-only")
                self.presenter.show_info(f"Script-type filter: removed {removed} {'/'.join(kinds)} words")
        # Name wordset filter (Issue #59). Drops proper nouns (people/place
        # names) that slipped past the 固有名詞 POS filter because unidic-lite
        # mistagged them. Whitelist still rescues. Gated like neighbors so the
        # Deck Builder corpus preview (bypass_optional_filters) stays in parity.
        if self.wordset_service and self.wordset_service.is_available() and not self.config.bypass_optional_filters:
            before = len(unknown_words)
            unknown_words = self.word_filter.filter_by_wordsets(
                unknown_words, self.wordset_service, self.word_list_service
            )
            filtered_out = before - len(unknown_words)
            if filtered_out > 0:
                self.presenter.show_info(f"Name wordset filter: removed {filtered_out} words")

        # Sentence deduplication. i+1 filter does its own sentence picking;
        # dedup would be a no-op (post-i+1 sentences are unique by construction).
        if (
            self.config.deduplicate_sentences
            and not self.config.use_i_plus_one_filter
            and not self.config.bypass_optional_filters
        ):
            before = len(unknown_words)
            unknown_words = self.word_filter.deduplicate_by_sentence(unknown_words)
            deduped = before - len(unknown_words)
            if deduped > 0:
                self.presenter.show_info(f"Sentence deduplication: removed {deduped} duplicate-sentence words")

        # Cross-episode frequency filter.
        if (
            cross_episode_counts is not None
            and self.config.min_episode_appearances > 1
            and not self.config.bypass_optional_filters
        ):
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
        if self.config.use_i_plus_one_filter and not self.config.bypass_optional_filters:
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
        if (
            self.config.use_sentence_length_filter
            and not self.config.bypass_optional_filters
            and (self.config.max_sentence_duration_seconds > 0.0 or self.config.max_sentence_chars > 0)
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
        audio_only: bool = False,
    ) -> list[tuple[TokenizedWord, MediaData]]:
        """Phase 3: extract media (screenshots + audio) for each unknown word."""
        self.presenter.show_info("Step 3/5 — Extracting media from video")
        media_results: list[tuple[TokenizedWord, MediaData]] = self.media_extractor.extract_media_batch(
            video_file,
            unknown_words,
            progress_callback,
            cancelled_check=lambda: self.cancelled,
            temp_folder=run_temp_folder,
            audio_track_override=audio_track_override,
            audio_only=audio_only,
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
            # Stamp the source unconditionally; AnkiService gates the write on a
            # non-empty configured field name (anki_fields["source"]).
            extra_fields["source"] = f"{ctx.source_label} @ {_format_timestamp(word.start_time)}"

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
        media_failures = self.anki_service.last_media_store_failures
        if isinstance(media_failures, int) and media_failures > 0:
            self.presenter.show_warning(
                f"{media_failures} media file(s) could not be stored in Anki; those cards "
                f"will have no audio or screenshot. Check that Anki/AnkiConnect is running and "
                f"see the log for details."
            )
        skipped_duplicates = self.anki_service.last_skipped_duplicates
        if isinstance(skipped_duplicates, int) and skipped_duplicates > 0:
            self.presenter.show_warning(
                f"Skipped {skipped_duplicates} word(s) Anki flagged as duplicates "
                f"(same Expression as an existing card or another word in this batch)."
            )

        # Add newly mined words to known word DB.
        # Store mined_form so the local DB matches what Anki stores in the
        # Expression first field (POS-aware via mined_form); Issue #5.
        #
        # The cards already exist in Anki at this point. A locked DB (Anki or a
        # parallel run holding known_words.db) raises OperationalError here; do
        # NOT let it bubble into process_episode's generic except, which would
        # report cards_created=0 with no note IDs — a successful run reported as
        # a failure (T-19). The cache is additive and self-heals on the next
        # run, so dropping this one write is safe; warn and keep the result.
        if self.known_word_db and self.known_word_db.is_available() and card_data:
            mined_words = {payload.word.mined_form for payload in card_data}
            try:
                self.known_word_db.add_words(mined_words, source="mined")
            except sqlite3.OperationalError as e:
                logger.warning(
                    "Could not record %d mined words in known_words.db (%s); "
                    "the cards were still created. The cache will re-sync next run.",
                    len(mined_words),
                    e,
                )

        return cards_created, created_note_ids

    def process_episode(
        self,
        video_file: Path,
        subtitle_file: Path,
        preview_mode: bool = False,
        progress_callback: ProgressCallback | None = None,
        curation_callback: Callable[[list], list | None] | None = None,
        cross_episode_counts: dict[str, int] | None = None,
        episode_name_override: str | None = None,
        series_name_override: str | None = None,
        audio_track_override: int | None = None,
        source_label_override: str | None = None,
        audio_only: bool = False,
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
                filtered words. Returns the user-selected subset (an empty list
                means "confirmed with nothing selected" → a completed run with
                zero new cards), or ``None`` if the user cancelled/rejected the
                dialog → a cancelled result.
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
            source_label_override: Optional override for the card "source" field
                origin. When ``None`` (default) the origin is built from the
                resolved series/episode identity as ``"<series> — <episode>"``
                (em dash, U+2014). Used by ``process_youtube_url`` to stamp the
                actual video title instead of the synthetic ``YT:<video_id>``.
            audio_only: If True (audiobook mining), media extraction skips
                per-word screenshots and reuses the file's embedded cover art
                instead. False (default) preserves existing video behavior.

        Returns:
            ProcessingResult with statistics.

        Raises:
            SetupError: note type or field mapping is misconfigured.
            AnkiConnectionError: AnkiConnect is unreachable.
        """
        series_name = _resolve_identity(series_name_override, video_file.parent.name)
        episode_name = _resolve_identity(episode_name_override, video_file.stem)
        ctx = _EpisodeContext(
            start_time=time.time(),
            video_file_str=str(video_file),
            subtitle_file_str=str(subtitle_file),
            episode_name=episode_name,
            series_name=series_name,
            source_label=source_label_override or f"{series_name} — {episode_name}",
        )
        # Outside the try/except so SetupError propagates to callers instead of
        # being absorbed into a "completed" ProcessingResult.  Before temp-folder
        # allocation so no dir is leaked on failure.
        if not preview_mode:
            self._preflight_card_target()
        run_temp_folder = self._allocate_run_temp_folder()
        keep_temp = bool(os.environ.get("ANKI_MINER_KEEP_TEMP"))

        # Invalidate the per-file audio stream cache before extraction so that
        # cross-run file replacement (re-encode, swap, restore) cannot strand
        # the resolver on stale ffprobe output. Within this run the cache will
        # repopulate on the first probe and protect against double-probes
        # (the 2e0cc13 perf win).
        self.media_extractor.invalidate_audio_stream_cache(video_file)

        try:
            all_words, line_index = self._phase1_parse(ctx, subtitle_file)
            if not all_words:
                self.presenter.show_warning("No words found in subtitles")
                return ctx.build_result()
            if self.cancelled:
                return self._cancelled_result_from_ctx(ctx)

            unknown_words = self._phase2_filter(ctx, all_words, line_index, cross_episode_counts)
            if not unknown_words:
                self.presenter.show_info("All words already in Anki!")
                return ctx.build_result(new_words_found=0)
            if self.cancelled:
                return self._cancelled_result_from_ctx(ctx)

            if curation_callback is not None and not preview_mode:
                curated = curation_callback(unknown_words)
                if curated is None:
                    # The user cancelled/rejected the curation dialog.
                    return self._cancelled_result_from_ctx(ctx)
                unknown_words = curated
                ctx.new_words_found = len(unknown_words)
                if not unknown_words:
                    # The user confirmed with everything deselected: this is an
                    # intentional "card nothing this episode", a completed run
                    # with zero new cards — NOT a cancellation (keeps stats and
                    # batch status accurate).
                    self.presenter.show_info("No words selected for card creation")
                    return ctx.build_result(new_words_found=0)
                self.presenter.show_info(f"User selected {len(unknown_words)} words for card creation")

            if preview_mode:
                self.presenter.show_word_preview(unknown_words)
                return ctx.build_result()

            # Wrap the raw callback so the bar reflects whole-episode progress
            # instead of resetting 0->100 per stage. One weight per stage that
            # reports progress, in firing order: extract, definitions,
            # [glossaries if mapped], cards.
            stage_progress = progress_callback
            if progress_callback is not None:
                stage_weights = [0.40, 0.25]  # extract, definitions
                if self.config.anki_fields.get("glossary"):
                    stage_weights.append(0.10)  # glossaries
                stage_weights.append(0.25)  # cards
                stage_progress = StageWeightedProgress(progress_callback, stage_weights)

            media_results = self._phase3_extract(
                ctx,
                video_file,
                unknown_words,
                stage_progress,
                run_temp_folder,
                audio_track_override,
                audio_only=audio_only,
            )
            if self.cancelled:
                return self._cancelled_result_from_ctx(ctx)
            if not media_results:
                self.presenter.show_warning("No media extracted successfully")
                return ctx.build_result(errors=["Media extraction failed for all words"])
            self.presenter.show_success(f"Extracted media for {len(media_results)} words")

            definitions, glossaries, pitch_data = self._phase4_lookup(ctx, media_results, stage_progress)
            if self.cancelled:
                return self._cancelled_result_from_ctx(ctx)

            cards_created, created_note_ids = self._phase5_create(
                ctx, media_results, definitions, glossaries, pitch_data, stage_progress
            )
            if isinstance(stage_progress, StageWeightedProgress):
                stage_progress.finish()
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

        # The cards already exist in Anki at this point. A locked stats.db
        # raises OperationalError here; do NOT let it bubble into
        # process_episode's generic except, which would report
        # cards_created=0 with no note IDs — a successful run reported as a
        # failure. Same exposure the known_words.db write fixed (T-19);
        # dropping one stats row is safe, so warn and keep the result.
        try:
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
        except sqlite3.OperationalError as e:
            logger.warning(
                "Could not record mining session for %s in stats.db (%s); " "the cards were still created.",
                ctx.episode_name,
                e,
            )

    def _preflight_card_target(self) -> None:
        """Fail fast on a misconfigured Anki target; auto-create the deck (Issue #52)."""
        self.anki_service.verify_card_target()

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
        curation_callback: Callable[[list], list | None] | None = None,
        on_fetched: Callable[[FetchedMedia], None] | None = None,
        preview_mode: bool = False,
        source_label: str | None = None,
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
                forwarded to the fetcher so in-flight yt-dlp can be killed,
                and bridged into the mining pipeline's cancellation
                checkpoints (via :attr:`cancelled`) for the duration of this
                run only.
            progress_callback: Optional ``ProgressCallback`` forwarded to
                ``process_episode`` for mining-phase reporting (media extract,
                definitions, card creation).
            fetch_progress_cb: Optional ``(label, frac)`` callable forwarded
                to ``YouTubeFetcherService.fetch_video`` for download-phase
                reporting. ``frac`` is in [0.0, 1.0] or ``None`` for
                indeterminate stages (merging, post-processing).
            curation_callback: Optional callback for word curation. Forwarded
                unchanged to ``process_episode``; see its docstring for semantics.
            on_fetched: Optional callback invoked with the ``FetchedMedia``
                result after download completes, before the mining pipeline
                starts. Called on the calling thread (the worker thread).
            preview_mode: If True, skip card creation and show previews only.
                Forwarded unchanged to ``process_episode``.
            source_label: Optional origin string for the card "source" field
                (typically the YouTube video title). Forwarded to
                ``process_episode`` as ``source_label_override``. The stats/dedup
                identity (``YT:<video_id>`` / ``YouTube``) is unaffected.

        Returns:
            ProcessingResult from the mining pipeline, with episode identity
            overridden to ``YT:<video_id>``.

        Raises:
            RuntimeError: if no YouTubeFetcherService was injected.
            SetupError: note type or field mapping is misconfigured.
            AnkiConnectionError: AnkiConnect is unreachable.
            Any fetcher exception propagates unchanged (no workspace cleanup
            happens here — the worker handles it).
        """
        if self._youtube_fetcher is None:
            raise RuntimeError("YouTubeFetcherService not injected — check service_factory")

        start_time = time.time()
        if cancel_event.is_set():
            return self._make_cancelled_result(start_time)

        if not preview_mode:
            # Deliberate early check: fail before the video download rather than
            # after.  process_episode re-runs the same pre-flight post-fetch;
            # that double-check is intentional — cheap idempotent localhost calls.
            self._preflight_card_target()

        # Bridge the worker's cancel_event into the mining pipeline for the
        # duration of this run only: process_episode's phase checkpoints and
        # the media extractor's cancelled_check consult self.cancelled, which
        # folds this source in. See the __init__ comment for why the sticky
        # self._cancelled flag must NOT be used here (shared processor reuse).
        self._external_cancel = cancel_event.is_set
        try:
            fetched = self._youtube_fetcher.fetch_video(
                url,
                video_id,
                workspace,
                sub_mode,
                progress_cb=fetch_progress_cb,
                cancel_event=cancel_event,
            )

            if on_fetched is not None:
                on_fetched(fetched)

            if cancel_event.is_set():
                # Cancel landed as the fetch completed (the fetcher only
                # raises for cancels it observed itself): stop before parsing.
                return self._make_cancelled_result(start_time)

            return self.process_episode(
                fetched.video_file,
                fetched.subtitle_file,
                preview_mode=preview_mode,
                progress_callback=progress_callback,
                curation_callback=curation_callback,
                episode_name_override=f"YT:{video_id}",
                series_name_override="YouTube",
                source_label_override=source_label,
            )
        finally:
            self._external_cancel = None
