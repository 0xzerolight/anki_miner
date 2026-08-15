"""Orchestrator for processing a single episode."""

from __future__ import annotations

import contextlib
import logging
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import uuid
import zipfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PyQt6.QtCore import QCoreApplication

from anki_miner.config import AnkiMinerConfig
from anki_miner.exceptions import AnkiMinerException, SetupError
from anki_miner.interfaces import PresenterProtocol, ProgressCallback
from anki_miner.models import (
    CANCELLED_ERROR,
    AnkiWriteState,
    CardPayload,
    MediaData,
    ProcessingResult,
    TokenizedWord,
)
from anki_miner.models.youtube import FetchedMedia, SubMode
from anki_miner.orchestration.audio_stage import AudioStage
from anki_miner.services import (
    AnkiService,
    DefinitionService,
    MediaExtractorService,
    SubtitleParserService,
    WordFilterService,
)
from anki_miner.services.anki_service import is_transient_anki_transport_error
from anki_miner.services.definition_service import collect_dictionary_css_entries
from anki_miner.services.dictionary.card_style_block import attach_card_style_block
from anki_miner.services.frequency.multi_frequency_service import harmonic_rank, min_rank
from anki_miner.services.frequency.render import render_frequency_html
from anki_miner.services.pitch_accent.render import (
    render_pitch_graph_field,
    render_pitch_text_field,
)
from anki_miner.services.reading.images import ReadingImageArchiveError, ReadingImageMemberError, prepare_card_image
from anki_miner.services.resource_staleness import stale_resource_reimport_error
from anki_miner.services.subtitle_parser import _differs_by_okurigana_only
from anki_miner.utils import ensure_directory, katakana_to_hiragana
from anki_miner.utils.i18n import tr_format
from anki_miner.utils.logging_ext import log_summary
from anki_miner.utils.timing import timed_phase

logger = logging.getLogger(__name__)

#: The mining pipeline is exactly five stages long: parse, filter, media,
#: definitions, cards. Their *order* and *count* are the only whole-run
#: position knowable in advance -- their relative durations are not, which is
#: why no stage weight lives anywhere in this module any more.
PIPELINE_STAGE_COUNT = 5


if TYPE_CHECKING:
    from anki_miner.interfaces.expression_audio import ExpressionAudioFetcher
    from anki_miner.interfaces.sentence_audio import SentenceAudioFetcher
    from anki_miner.models import LineLemmas
    from anki_miner.models.reading import ImageRef, ReadingDocument
    from anki_miner.services.dictionary.registry import DictionaryRegistry
    from anki_miner.services.frequency.multi_frequency_service import MultiFrequencyService
    from anki_miner.services.frequency.registry import FrequencySourceRegistry
    from anki_miner.services.known_word_db import KnownWordDB
    from anki_miner.services.pitch_accent.multi_pitch_service import MultiPitchAccentService
    from anki_miner.services.pitch_accent.registry import PitchSourceRegistry
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


# Strips a contiguous trailing run of ``[...]`` groups plus an optional
# ``-ReleaseGroup`` suffix (Issue #83). ``[^\]]*`` (no nested brackets) keeps this
# linear-time and confines the match to a *trailing* block, so mid-title brackets
# like ``[Blu-ray]`` survive. End-anchored, so a leading series/season prefix is
# never touched.
_ARR_METADATA_RE = re.compile(r"\s*(?:\[[^\]]*\]\s*)+(?:-\S+)?\s*$")

_OFFLINE_DICTIONARY_REQUIRED_MESSAGE = (
    "No usable offline dictionary is installed. Use Tools → Download Recommended Resources or Settings → Dictionaries."
)


def require_usable_offline_provider(
    config: AnkiMinerConfig,
    definition_service: DefinitionService,
) -> None:
    """Fail standard mining when no non-empty offline dictionary can serve it."""
    if config.bypass_optional_filters:
        return
    if not definition_service.has_usable_offline_provider():
        raise SetupError(_OFFLINE_DICTIONARY_REQUIRED_MESSAGE)


def sanitize_source_label(label: str) -> str:
    """Remove *arr release metadata (e.g. ``[WEBRip-1080p][JA]-Trix``) from a
    source label, leaving the human-readable title."""
    return _ARR_METADATA_RE.sub("", label).strip()


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

    # Reading-tab only (Issue: Reading tab): maps a unit index (= int of the
    # dummy start_time) to its human page/chapter/cue label ("p.42" / "1:23").
    # None on the video path (process_episode, where phase5 keeps the HH:MM:SS
    # timestamp format); set by process_reading for manga/novels/subtitles.
    unit_labels: dict[int, str] | None = None

    # Accumulator fields populated as phases progress.
    errors: list[str] = field(default_factory=list)
    total_words_found: int = 0
    new_words_found: int = 0
    # Words that survived the known-vocabulary filter (the "%n new word(s) to
    # mine" count), snapshotted before the optional filters shrink the set. Lets
    # the terminal no-mineable-words message tell "already in Anki" (0 survivors)
    # apart from "removed by active filters" (survivors, then filtered out).
    candidate_words_found: int = 0
    comprehension_percentage: float = 0.0
    difficulty_total_words: int = 0
    difficulty_unknown_words: int = 0

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
        pitch_accent_service: MultiPitchAccentService | None = None,
        frequency_service: MultiFrequencyService | None = None,
        known_word_db: KnownWordDB | None = None,
        word_list_service: WordListService | None = None,
        wordset_service: WordsetService | None = None,
        stats_service: StatsService | None = None,
        youtube_fetcher: YouTubeFetcherService | None = None,
        expression_audio_fetcher: ExpressionAudioFetcher | None = None,
        dictionary_registry: DictionaryRegistry | None = None,
        frequency_registry: FrequencySourceRegistry | None = None,
        pitch_registry: PitchSourceRegistry | None = None,
        sentence_audio_fetcher: SentenceAudioFetcher | None = None,
        owns_lookup_services: bool = True,
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
            expression_audio_fetcher: Optional pronunciation audio fetcher
                (Issue #73). Only consulted in Phase 3 when the
                ``expression_audio`` Anki field is mapped (non-empty).  ``None``
                is only valid for test construction; the service factory always
                provides a (possibly empty-chain) fetcher.
            dictionary_registry: Optional loaded registry backing the 4.0
                schema-staleness backstop (``check_resource_staleness``). The
                service factory injects the same handle that built the provider
                chain; ``None`` (test construction / callers that skip the gate)
                disables the backstop for dictionaries.
            frequency_registry: Optional loaded frequency registry, same role.
                ``None`` whenever frequency is inactive, which is also when it
                must not be gated.
            pitch_registry: Optional loaded pitch registry, same role and same
                inactive-means-ungated rule.
            sentence_audio_fetcher: Optional sentence-TTS fetcher. Consulted
                ONLY by ``process_reading`` phase 3' (reading sources have no
                source audio); video/YouTube/audiobook paths never touch it.
                Gated by ``_reading_tts_active``. ``None`` is only valid for
                test construction; the service factory always provides a
                (possibly empty-chain) fetcher.
            owns_lookup_services: When False, this processor was built over a
                worker-owned :class:`SharedLookupServices` bundle and must NOT
                close the definition/frequency sqlite handles in ``close()`` /
                ``release_dictionary_resources()`` — the sharing worker's
                ``finally`` owns that teardown (frequency providers do NOT
                lazily reopen after close, so a between-items close would
                silently kill frequency data for the rest of the run). Default
                True preserves the per-run ownership of every other caller.
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
        self.expression_audio_fetcher = expression_audio_fetcher
        self.sentence_audio_fetcher = sentence_audio_fetcher
        self._dictionary_registry = dictionary_registry
        self._frequency_registry = frequency_registry
        self._pitch_registry = pitch_registry
        self.owns_lookup_services = owns_lookup_services
        self._cancelled = False
        # Per-run external cancel source (e.g. a worker's threading.Event
        # ``is_set``), installed/removed by process_episode around each run
        # when the caller passes ``cancel_event`` (queue workers do;
        # process_youtube_url forwards its own event down). Worker paths must
        # NOT set the sticky ``_cancelled`` flag: this processor instance is
        # reused across runs (the tabs build it once) and ``_cancelled`` is
        # only reset in __init__, so a sticky flag set on run N would poison
        # run N+1. Dropping the reference in a ``finally`` makes the bridge
        # per-run by construction.
        self._external_cancel: Callable[[], bool] | None = None
        # Expression/sentence-audio stage (the one seam the god-module keep
        # verdict sanctions). The processor still constructs and closes the
        # fetchers; AudioStage only orchestrates the fetch loops. It reads a
        # LIVE cancelled callable (``lambda: self.cancelled``) so it always
        # honors the current run's external-cancel bridge, never a snapshot.
        self._audio_stage = AudioStage(
            config=config,
            presenter=presenter,
            cancelled=lambda: self.cancelled,
            expression_audio_fetcher=expression_audio_fetcher,
            sentence_audio_fetcher=sentence_audio_fetcher,
        )

    def cancel(self) -> None:
        """Request cancellation of processing."""
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        """Check if cancellation has been requested.

        True when :meth:`cancel` was called (sticky; file-based worker path)
        or when the active run's external cancel source — installed by
        :meth:`process_episode` from a caller-supplied ``cancel_event`` —
        reports set.
        """
        if self._cancelled:
            return True
        external = self._external_cancel
        return external is not None and external()

    @property
    def _expression_audio_active(self) -> bool:
        """Delegating alias for :attr:`AudioStage.expression_audio_active`.

        The gate logic (the two-part Issue #73 gate) lives on the audio stage;
        this property stays here because ``process_episode`` (band
        registration) and the tests reach it on the processor.
        """
        return self._audio_stage.expression_audio_active

    @property
    def _reading_tts_active(self) -> bool:
        """Delegating alias for :attr:`AudioStage.reading_tts_active`.

        The gate logic (the four-part reading-TTS gate) lives on the audio
        stage; this property stays here because ``process_reading`` (band
        registration) and the tests reach it on the processor.
        """
        return self._audio_stage.reading_tts_active

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

        The per-run frequency sources hold their own ``index.sqlite`` handles,
        so they are released here too (idempotent; safe when absent).

        Skipped entirely when the lookup services are worker-owned
        (``owns_lookup_services=False``): only the owner closes shared handles,
        in its end-of-run ``finally``.
        """
        if not self.owns_lookup_services:
            return
        self.definition_service.close()
        if self.frequency_service is not None:
            self.frequency_service.close()

    def close(self) -> None:
        """Release ALL per-run resources held by this processor.

        Closes the dictionary provider sqlite handles AND the expression-audio
        fetcher's ``requests.Session`` (when an audio fetcher is present).

        A fresh ``EpisodeProcessor`` is built for every mining run, but its
        resources were never released, so on Windows the leaked sqlite handles
        and audio Session sockets from run N accumulate and collide with run
        N+1's GUI-thread service construction — the app hard-freezes when a
        user mines single episodes back-to-back in one session. The mining tabs
        and the batch queue worker call this between sequential runs to drop
        those handles/sockets before any new ones are opened. Safe only on an
        idle processor; callers must not invoke it mid-run.
        """
        # DEBUG-logged so a Windows reporter can confirm whether close() (vs the
        # subsequent processor build) is where a back-to-back mine blocks.
        logger.debug("closing processor resources")
        # Worker-owned shared lookup services are NOT closed here — frequency
        # providers never reopen after close, so a between-items close would
        # strip frequency data from every later queue item. The owning worker
        # closes the bundle once, in its end-of-run finally.
        if self.owns_lookup_services:
            self.definition_service.close()
            if self.frequency_service is not None:
                self.frequency_service.close()
        if self.expression_audio_fetcher is not None:
            close = getattr(self.expression_audio_fetcher, "close", None)
            if callable(close):
                with contextlib.suppress(Exception):
                    close()
        if self.sentence_audio_fetcher is not None:
            close = getattr(self.sentence_audio_fetcher, "close", None)
            if callable(close):
                with contextlib.suppress(Exception):
                    close()
        logger.debug("closed processor resources")

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
            errors=[CANCELLED_ERROR],
            elapsed_time=time.time() - start_time,
        )

    def _cancelled_result_from_ctx(self, ctx: _EpisodeContext) -> ProcessingResult:
        """Cancellation result populated from the accumulator ctx."""
        return self._make_cancelled_result(
            ctx.start_time,
            total_words_found=ctx.total_words_found,
            new_words_found=ctx.new_words_found,
        )

    def _announce_stage(
        self,
        progress_callback: ProgressCallback | None,
        index: int,
        name: str,
    ) -> None:
        """Say which of the five pipeline stages this run has reached.

        The pipeline knows its stage position exactly, and knows nothing
        whatever about how the stages compare in duration. Stage weights used
        to supply that missing comparison as constants; because they were
        guesses the bar raced through short stages and then sat on a long one.
        Both channels therefore carry the position and nothing else: the
        presenter writes the log line, the per-run callback updates the run's
        own state.

        Args:
            progress_callback: The run's progress callback, if it has one.
            index: 1-based stage position.
            name: The stage's own name.
        """
        self.presenter.show_stage(index, PIPELINE_STAGE_COUNT, name)
        if progress_callback is not None:
            progress_callback.on_stage(index, PIPELINE_STAGE_COUNT, name)

    def _report_no_mineable_words(self, ctx: _EpisodeContext) -> None:
        """Emit the terminal message when no mineable words remain.

        Distinguishes the two ways the set empties: the known-vocabulary filter
        finding zero survivors ("already in Anki") versus survivors that the
        optional filters then removed entirely. The old code always said "already
        in Anki", which misattributed a frequency-cutoff wipe as a re-mine /
        known-words problem. Wording is filter-agnostic: the emptying filter
        varies by path (frequency, word list, script type, dedup, i+1, sentence
        length on the video path; reading occurrence floor on the reading path),
        so it does not enumerate a specific list.
        """
        if ctx.candidate_words_found > 0:
            self.presenter.show_warning(
                tr_format(
                    QCoreApplication.translate(
                        "EpisodeProcessor",
                        "All %1 new word(s) were removed by active filters — no cards created",
                    ),
                    ctx.candidate_words_found,
                )
            )
        else:
            # Kept byte-identical to ``gui.utils.result_copy.nothing_new_to_mine``
            # (D47-B). Orchestration must not import the GUI, so the sentence is
            # duplicated rather than shared; ``test_result_copy`` fails if the two
            # drift apart.
            self.presenter.show_info(
                QCoreApplication.translate("EpisodeProcessor", "No cards created. Every word is already in Anki.")
            )

    def _report_ambiguous_readings(self) -> None:
        """Emit one per-parse receipt for real-token reading mismatches."""
        count = getattr(self.subtitle_parser, "ambiguous_reading_count", 0)
        if type(count) is not int or count <= 0:
            return
        self.presenter.show_warning(
            tr_format(
                QCoreApplication.translate(
                    "EpisodeProcessor",
                    "Ambiguous reading review required for %1 word(s); current readings kept",
                ),
                count,
            )
        )

    def _phase1_parse(
        self,
        ctx: _EpisodeContext,
        subtitle_file: Path,
        progress_callback: ProgressCallback | None = None,
        want_line_index: bool = False,
    ) -> tuple[list[TokenizedWord], list[LineLemmas] | None]:
        """Phase 1: parse subtitles into tokenized words (and optionally a line index).

        Returns the raw parse output; mutates ``ctx.total_words_found``. The
        line index is built when the i+1 filter needs it OR when a caller asks
        via ``want_line_index`` (interactive curation uses it to offer
        alternative example sentences per word).
        """
        self._announce_stage(
            progress_callback,
            1,
            QCoreApplication.translate("EpisodeProcessor", "Parsing subtitles"),
        )
        self.presenter.show_info(
            tr_format(QCoreApplication.translate("EpisodeProcessor", "Subtitles: %1"), subtitle_file.name)
        )
        line_index: list[LineLemmas] | None = None
        if self.config.use_i_plus_one_filter or want_line_index:
            all_words, line_index = self.subtitle_parser.parse_subtitle_file_with_index(subtitle_file)
        else:
            all_words = self.subtitle_parser.parse_subtitle_file(subtitle_file)
        self._report_ambiguous_readings()
        self.presenter.show_success(
            QCoreApplication.translate("EpisodeProcessor", "Found %n unique word(s)", "", len(all_words))
        )
        ctx.total_words_found = len(all_words)
        represented_lines = len(self.subtitle_parser.parse_raw_entries(subtitle_file))
        produced_tokens = sum(self.subtitle_parser.count_lemmas(subtitle_file).values())
        log_summary(
            logger,
            "Phase 1 parse",
            lines=represented_lines,
            tokens=produced_tokens,
            unique=len(all_words),
        )
        return all_words, line_index

    def _phase2_filter(
        self,
        ctx: _EpisodeContext,
        all_words: list[TokenizedWord],
        line_index: list[LineLemmas] | None,
        progress_callback: ProgressCallback | None = None,
        occurrence_counts: dict[str, int] | None = None,
        min_occurrence: int = 1,
    ) -> list[TokenizedWord]:
        """Phase 2: attach frequency data, filter against known vocab, apply optional filters.

        Mutates ``ctx.new_words_found`` and ``ctx.comprehension_percentage``.
        Stages difficulty stats for a successful terminal result.
        """
        frequency_ranked = 0
        known_hits = 0
        known_db_added = 0
        known_db_total = 0
        frequency_rejects = 0
        word_list_rejects = 0
        script_rejects = 0
        wordset_rejects = 0
        episode_rejects = 0
        duplicate_sentence_rejects = 0
        i_plus_one_rejects = 0
        sentence_length_rejects = 0
        whitelist_force_includes = 0
        no_definition_rejects = 0
        duplicate_expression_rejects = 0

        # Attach frequency data if available (mutates words in-place). Each word
        # gets the per-source breakdown (frequency_sources) for the card display,
        # the min rank (frequency_rank) that drives the top-N filter, and the
        # harmonic-mean rank (frequency_harmonic_rank) that drives the sort field.
        if self.frequency_service and self.frequency_service.is_available():
            # Keyed on mined_form (the card-front spelling), NOT lemma:
            # unidic's canonical lemma collapses kanji variants
            # (懸ける/賭ける/架ける → 掛ける), so lemma-keyed lookups gave
            # every variant the common spelling's rank. Per-spelling sources
            # (JPDB) carry distinct rows per orthography — query the spelling
            # the card actually shows. Reading-scope so homographs stop
            # inheriting each other's ranks; hiragana-normalize so a katakana
            # subtitle reading matches a hiragana-stored frequency reading.
            # One batched per-source fetch for the whole word list (an
            # IN-clause query per source instead of one query per word), then
            # derive min + harmonic locally via the pure min_rank/harmonic_rank
            # helpers — a single lookup_all_many feeds both scalars.
            pairs: list[tuple[str, str | None]] = [
                (
                    word.mined_form,
                    katakana_to_hiragana(word.expression_reading or word.lemma_reading or word.reading),
                )
                for word in all_words
            ]
            all_sources = self.frequency_service.lookup_all_many(pairs)
            # Whole-result miss-only lemma fallback (mirrors the JPod101
            # audio retry ladder): fires only when NO source attests the
            # spelling and the alternate differs by okurigana over the same
            # kanji stem. A different-kanji UniDic lemma may be another
            # homograph and must never supply this card's rank. Deliberately
            # NOT per-source: a per-source cascade would re-inject the lemma
            # rank from any source lacking the per-spelling row, and since
            # frequency_rank = min_rank(sources) gates the top-N filter,
            # that low lemma rank would keep a rare variant above the
            # max_frequency_rank cutoff it should now fall past. Known edge:
            # a spelling attested ONLY by a categorical source (JLPT band,
            # CATEGORICAL_RANK sentinel) counts as attested and suppresses the
            # numeric lemma fallback — accepted for breakdown uniformity;
            # unreachable for per-spelling numeric sources.
            fallback_indexes = [
                i
                for i, (word, sources) in enumerate(zip(all_words, all_sources, strict=True))
                if not sources
                and word.lemma
                and word.lemma != word.mined_form
                and _differs_by_okurigana_only(word.mined_form, word.lemma)
            ]
            if fallback_indexes:
                fallback_pairs: list[tuple[str, str | None]] = [
                    (
                        all_words[i].lemma,
                        katakana_to_hiragana(all_words[i].lemma_reading or all_words[i].reading),
                    )
                    for i in fallback_indexes
                ]
                for i, sources in zip(
                    fallback_indexes, self.frequency_service.lookup_all_many(fallback_pairs), strict=True
                ):
                    all_sources[i] = sources
            for word, sources in zip(all_words, all_sources, strict=True):
                word.frequency_sources = sources
                word.frequency_rank = min_rank(sources)
                word.frequency_harmonic_rank = harmonic_rank(sources)
            ranked_count = sum(1 for w in all_words if w.frequency_rank is not None)
            frequency_ranked = ranked_count
            self.presenter.show_info(
                tr_format(
                    QCoreApplication.translate("EpisodeProcessor", "Frequency data: %1/%2 words ranked"),
                    ranked_count,
                    len(all_words),
                )
            )

        # Filter against existing vocabulary.
        self._announce_stage(
            progress_callback,
            2,
            QCoreApplication.translate("EpisodeProcessor", "Filtering against known vocabulary"),
        )
        if self.config.include_known_words:
            # Deck Builder "include everything" mode: skip known-words subtraction
            # entirely — including the Issue #42 user ignore list — and mine all
            # words that passed POS/subtype filtering. Coverage-deck builds
            # intentionally re-card words the user already knows.
            self.presenter.show_info(
                QCoreApplication.translate("EpisodeProcessor", "Known-words filter bypassed (include everything mode)")
            )
            unknown_words = all_words
        else:
            # User-curated ignore list (Issue #42): always applied on the normal
            # mining path, regardless of the use_known_words_db toggle. The DB
            # object is always present now, but the file may not exist for users
            # who never added a word — is_available guards.
            # A locked/raising known_words.db (Manage-Known-Words dialog open, or a
            # second concurrent run holding the file) must NOT abort the run — the
            # same T-19 rationale as the guarded writes below. Each read is wrapped;
            # on failure we drop the user ignore list and fall back to Anki's
            # existing vocabulary, warning and continuing rather than bubbling the
            # sqlite3.OperationalError into process_episode's generic except.
            user_words: set[str] = set()
            if self.known_word_db and self.known_word_db.is_available():
                try:
                    user_words = self.known_word_db.get_words_by_source("user")
                except (sqlite3.Error, OSError) as e:
                    logger.warning(
                        "Could not read the user ignore list from known_words.db (%s); proceeding without it this run.",
                        e,
                    )

            if self.config.use_known_words_db and self.known_word_db and self.known_word_db.is_available():
                try:
                    known_words = self.known_word_db.get_known_words()
                    # Sync with Anki to keep DB up to date. Pass the pre-fetched
                    # ``known_words`` so the DB skips its internal scan; merge the
                    # diff in-memory below to avoid a post-sync re-read.
                    anki_vocab = self.anki_service.get_existing_vocabulary()
                    added, total = self.known_word_db.sync_with_anki(anki_vocab, existing=known_words)
                    known_db_added = added
                    known_db_total = total
                    if added > 0:
                        self.presenter.show_info(
                            tr_format(
                                QCoreApplication.translate(
                                    "EpisodeProcessor", "Known word DB synced: %1 new words (%2 total)"
                                ),
                                added,
                                total,
                            )
                        )
                        known_words = known_words | (anki_vocab - known_words)
                except (sqlite3.Error, OSError) as e:
                    logger.warning(
                        "Could not access known_words.db (%s); falling back to Anki's "
                        "existing vocabulary for this run.",
                        e,
                    )
                    known_words = self.anki_service.get_existing_vocabulary()
            else:
                known_words = self.anki_service.get_existing_vocabulary()

            unknown_words = self.word_filter.filter_unknown(all_words, known_words | user_words)
            known_hits = len(all_words) - len(unknown_words)
        self.presenter.show_success(
            QCoreApplication.translate("EpisodeProcessor", "%n new word(s) to mine", "", len(unknown_words))
        )
        # Snapshot the post-known-vocab survivor count before optional filters
        # shrink it, so the terminal message can distinguish "already in Anki"
        # from "removed by active filters".
        ctx.candidate_words_found = len(unknown_words)

        # Comprehension percentage.
        comprehension = ((len(all_words) - len(unknown_words)) / len(all_words)) * 100 if all_words else 0.0
        self.presenter.show_info(
            tr_format(
                QCoreApplication.translate("EpisodeProcessor", "Comprehension: %1% of words already known"),
                f"{comprehension:.1f}",
            )
        )
        ctx.comprehension_percentage = comprehension

        # Surface the "everything was already known" case explicitly. Without
        # this, users who enable a card-format option (bold target word, etc.)
        # and re-mine the same episode see no visible change because every
        # word was filtered out before card creation. The pipeline silently
        # produces zero cards. Issue #20 (reopened): user mistook silent
        # no-op for "bold isn't working".
        if all_words and not unknown_words:
            self.presenter.show_warning(
                QCoreApplication.translate(
                    "EpisodeProcessor",
                    "All %n word(s) from this subtitle are already in Anki — no new cards created",
                    "",
                    len(all_words),
                )
            )

        # Issue #74: snapshot the full unknown-lemma set before optional
        # filters (frequency, word-list, script-type, wordset) shrink it.
        # The i+1 check must see ALL words the learner doesn't know, not
        # just the mineable ones.
        all_unknown_lemmas = {w.lemma for w in unknown_words}

        # Offline definition existence filter. Drops words with no entry in any
        # OFFLINE dictionary so the curation dialog never surfaces words that
        # can never become cards (they would otherwise be silently skipped at
        # Phase 5). Offline-only by design: matches the curator's no-network
        # def-pane and the project's offline-first default (Jisho is off by
        # default). Probes mined_form plus only same-kanji, okurigana-only lemma
        # alternates; a different-kanji UniDic lemma may be another homograph.
        # Exact misses also use the same rules-validated deinflection candidates
        # as Phase 4, so 帰れる can qualify through 帰る without trusting 返る.
        # Runs before every lossy sentence selector so an undefined first word
        # cannot erase a definition-backed sentence-mate. Gated on
        # bypass_optional_filters so the Deck Builder preview-parity path is
        # unaffected (Phase 5 stays the skip point there).
        #
        # Known, intentional asymmetry: this probe is offline-only, but Phase 5
        # looks definitions up over the FULL chain (get_definitions_batch, which
        # includes Jisho when enabled). A user who turns Jisho on therefore has
        # words with a Jisho-only definition dropped here before the curator —
        # accepted on purpose so Phase 2 never blocks on network I/O. Do not
        # "fix" this by calling online providers here.
        if not self.config.bypass_optional_filters and unknown_words:
            safe_alternates = [
                (
                    w.lemma
                    if w.lemma and (w.lemma == w.mined_form or _differs_by_okurigana_only(w.mined_form, w.lemma))
                    else ""
                )
                for w in unknown_words
            ]
            probe_terms = list(
                {
                    term
                    for w, alternate in zip(unknown_words, safe_alternates, strict=True)
                    for term in (w.mined_form, alternate)
                    if term
                }
            )
            has_def = self.definition_service.has_offline_definitions(probe_terms) or {}
            fallback_candidates = [
                (
                    []
                    if has_def.get(w.mined_form) or has_def.get(alternate)
                    else DefinitionService._fallback_candidates(w.mined_form, alternate, None)
                )
                for w, alternate in zip(unknown_words, safe_alternates, strict=True)
            ]
            fallback_probe = list(
                dict.fromkeys(candidate for candidates in fallback_candidates for candidate in candidates)
            )
            deinflection_hits = (
                self.definition_service.offline_deinflection_terms_exist(fallback_probe) if fallback_probe else set()
            ) or set()
            viable = [
                bool(
                    has_def.get(w.mined_form)
                    or has_def.get(alternate)
                    or any(term in deinflection_hits for term, _conditions in candidates)
                )
                for w, alternate, candidates in zip(
                    unknown_words,
                    safe_alternates,
                    fallback_candidates,
                    strict=True,
                )
            ]
            kept_words = [w for w, keep in zip(unknown_words, viable, strict=True) if keep]
            dropped = [w.mined_form for w, keep in zip(unknown_words, viable, strict=True) if not keep]
            unknown_words = kept_words
            no_definition_rejects = len(dropped)
            if dropped:
                preview = ", ".join(dropped[:10])
                more = f" (+{len(dropped) - 10} more)" if len(dropped) > 10 else ""
                self.presenter.show_warning(
                    tr_format(
                        QCoreApplication.translate(
                            "EpisodeProcessor", "Skipped %1 words with no definition found: %2%3"
                        ),
                        len(dropped),
                        preview,
                        more,
                    )
                )

        # Whitelist force-include (partition-then-merge). A whitelisted lemma is
        # a true force-include: it bypasses every optional COVERAGE filter below
        # (frequency, blacklist, script-type, name-wordsets, reading
        # occurrence counts, dedup, i+1, sentence-length). Definition viability
        # already ran above, so force-included words remain subject to it. We
        # split them out here and merge them back just before the within-run
        # duplicate collapse.
        # Gated on bypass_optional_filters so the Deck Builder preview — which
        # already includes everything — is unchanged.
        forced_include: list[TokenizedWord] = []
        if (
            self.config.use_whitelist
            and self.word_list_service
            and self.word_list_service.is_available()
            and not self.config.bypass_optional_filters
        ):
            forced_include, unknown_words = self.word_filter.partition_whitelisted(
                unknown_words, self.word_list_service
            )
            whitelist_force_includes = len(forced_include)

        # Frequency rank band. Gate on an actually-loaded NUMERIC frequency
        # source — NOT just a configured bound, and NOT is_available(). With
        # no source (or only a categorical one, e.g. a JLPT-band dict whose rows
        # all carry CATEGORICAL_RANK), no word gets a numeric rank, so every word
        # keeps frequency_rank=None and filter_by_frequency drops every None-ranked
        # word (word_filter.py) — a configured cutoff would then silently wipe 100%
        # of words and produce zero cards. has_numeric_source() is True only when a
        # non-categorical source is loaded, which is the sole case the cutoff can
        # meaningfully apply.
        freq_low = self.config.min_frequency_rank
        freq_high = self.config.max_frequency_rank
        if (
            (freq_low > 0 or freq_high > 0)
            and self.frequency_service
            and self.frequency_service.has_numeric_source()
            and not self.config.bypass_optional_filters
        ):
            before = len(unknown_words)
            unknown_words = self.word_filter.filter_by_frequency(
                unknown_words,
                freq_high,
                min_rank=freq_low,
                keep_unranked=self.config.frequency_keep_unranked,
            )
            filtered_out = before - len(unknown_words)
            frequency_rejects = filtered_out
            if filtered_out > 0:
                self.presenter.show_info(self._frequency_filter_notice(filtered_out, freq_low, freq_high))
        elif (freq_low > 0 or freq_high > 0) and not self.config.bypass_optional_filters:
            # Band configured but no frequency source is loaded: skip it (it
            # would drop every word) and tell the user it is inert, so they add a
            # source instead of silently getting zero cards.
            self.presenter.show_warning(
                QCoreApplication.translate(
                    "EpisodeProcessor",
                    "Frequency cutoff set but no frequency source is loaded — cutoff ignored (add a frequency source in Settings).",
                )
            )

        # Word list (blacklist/whitelist) filter.
        if self.word_list_service and self.word_list_service.is_available() and not self.config.bypass_optional_filters:
            before = len(unknown_words)
            unknown_words = self.word_filter.filter_by_word_lists(unknown_words, self.word_list_service)
            filtered_out = before - len(unknown_words)
            word_list_rejects = filtered_out
            if filtered_out > 0:
                self.presenter.show_info(
                    tr_format(
                        QCoreApplication.translate("EpisodeProcessor", "Word list filter: removed %1 words"),
                        filtered_out,
                    )
                )

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
            script_rejects = removed
            if removed > 0:
                kinds = []
                if self.config.exclude_hiragana_only_words:
                    kinds.append("hiragana-only")
                if self.config.exclude_katakana_only_words:
                    kinds.append("katakana-only")
                self.presenter.show_info(
                    tr_format(
                        QCoreApplication.translate("EpisodeProcessor", "Script-type filter: removed %1 %2 words"),
                        removed,
                        "/".join(kinds),
                    )
                )
        # Name wordset filter (Issue #59). Drops proper nouns (people/place
        # names) that slipped past the 固有名詞 POS filter because unidic-lite
        # mistagged them. Force-included whitelist words are already partitioned
        # out above, so they never reach here. Gated like neighbors so the Deck
        # Builder corpus preview (bypass_optional_filters) stays in parity.
        if self.wordset_service and self.wordset_service.is_available() and not self.config.bypass_optional_filters:
            before = len(unknown_words)
            unknown_words = self.word_filter.filter_by_wordsets(unknown_words, self.wordset_service)
            filtered_out = before - len(unknown_words)
            wordset_rejects = filtered_out
            if filtered_out > 0:
                self.presenter.show_info(
                    tr_format(
                        QCoreApplication.translate("EpisodeProcessor", "Name wordset filter: removed %1 words"),
                        filtered_out,
                    )
                )

        # Reading-specific in-document occurrence floor. Runs BEFORE sentence
        # dedup: removing below-floor words first lets a qualifying sentence-mate
        # survive instead of losing the whole sentence to a below-floor first word.
        # Force-included whitelist words were partitioned out above and merge
        # back later, so they continue to bypass this coverage filter.
        if occurrence_counts is not None:
            before = len(unknown_words)
            unknown_words = self.word_filter.filter_by_episode_count(unknown_words, occurrence_counts, min_occurrence)
            episode_rejects += before - len(unknown_words)

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
            duplicate_sentence_rejects = deduped
            if deduped > 0:
                self.presenter.show_info(
                    tr_format(
                        QCoreApplication.translate(
                            "EpisodeProcessor", "Sentence deduplication: removed %1 duplicate-sentence words"
                        ),
                        deduped,
                    )
                )

        # i+1 sentence filtering. Restricts mining to words with an i+1 example
        # sentence (exactly one unknown overall — checked against the pre-filter
        # snapshot, Issue #74 — and that unknown must be mineable). Rescans
        # lines and may swap the chosen sentence per word. Drops words with no
        # i+1 coverage.
        if self.config.use_i_plus_one_filter and not self.config.bypass_optional_filters:
            before = len(unknown_words)
            unknown_words = self.word_filter.filter_i_plus_one(
                unknown_words, line_index or [], all_unknown_lemmas=all_unknown_lemmas
            )
            kept = len(unknown_words)
            i_plus_one_rejects = before - kept
            pct = (kept / before * 100.0) if before else 0.0
            self.presenter.show_info(
                tr_format(
                    QCoreApplication.translate("EpisodeProcessor", "i+1 filter: kept %1/%2 words (%3%)"),
                    kept,
                    before,
                    f"{pct:.0f}",
                )
            )

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
            sentence_length_rejects = filtered_out
            if filtered_out > 0:
                caps = []
                if self.config.max_sentence_duration_seconds > 0.0:
                    caps.append(f"{self.config.max_sentence_duration_seconds:g}s")
                if self.config.max_sentence_chars > 0:
                    caps.append(f"{self.config.max_sentence_chars} chars")
                self.presenter.show_info(
                    tr_format(
                        QCoreApplication.translate(
                            "EpisodeProcessor", "Sentence length filter: removed %1 words (cap: %2)"
                        ),
                        filtered_out,
                        ", ".join(caps),
                    )
                )

        # Merge force-included whitelist words back in before within-run
        # duplicate collapse. Prepend so a forced word wins its mined_form slot in the
        # within-run duplicate collapse below (which keeps the first occurrence)
        # — this makes force-include hold even in the rare cross-lemma homograph
        # collision (a forced verb's orth_base equal to a distinct noun's
        # surface). The tradeoff is that the forced word keeps its own parse-time
        # sentence rather than the collided rest word's (possibly i+1-swapped)
        # one, which is correct for "mine this word as-is".
        if forced_include:
            unknown_words = forced_include + unknown_words
            self.presenter.show_info(
                QCoreApplication.translate(
                    "EpisodeProcessor",
                    "Whitelist: force-included %n word(s)",
                    "",
                    len(forced_include),
                )
            )

        # Within-run duplicate collapse. Exact mined_form collisions mirror
        # Anki's Expression-first-field dedup. Orthographic aliases need a
        # dictionary identity instead: exact-term sequence + contextual reading,
        # scoped by dictionary. Never use the normal term-OR-reading lookup here;
        # it would falsely give reading-only junk such as いでる the identity of
        # 出でる. Keep the first source occurrence (stable order).
        #
        # Gated on allow_duplicate_cards: the Deck Builder sets it True (and
        # bypass_optional_filters True) to intentionally re-card duplicates, in
        # which case Anki creates both and showing both is correct — collapsing
        # there would diverge from its raw-lemma preview parity.
        if not self.config.allow_duplicate_cards and unknown_words:
            identity_pairs: list[tuple[str, str]] = [
                (
                    word.mined_form,
                    katakana_to_hiragana(word.expression_reading or word.lemma_reading or word.reading),
                )
                for word in unknown_words
            ]
            identities_by_pair = self.definition_service.offline_term_identities(identity_pairs)
            seen: set[str] = set()
            seen_identities: set[tuple[str, int, str]] = set()
            collapsed: list[TokenizedWord] = []
            for word, pair in zip(unknown_words, identity_pairs, strict=True):
                identities = identities_by_pair.get(pair, set())
                if word.mined_form in seen or not seen_identities.isdisjoint(identities):
                    continue
                seen.add(word.mined_form)
                seen_identities.update(identities)
                collapsed.append(word)
            removed = len(unknown_words) - len(collapsed)
            unknown_words = collapsed
            duplicate_expression_rejects = removed
            if removed:
                self.presenter.show_info(
                    tr_format(
                        QCoreApplication.translate("EpisodeProcessor", "Collapsed %1 duplicate-expression word(s)"),
                        removed,
                    )
                )

        # Stage the pre-filter comprehension counts. ``_run_pipeline`` commits
        # them only after the body returns a successful terminal result.
        ctx.difficulty_total_words = len(all_words)
        ctx.difficulty_unknown_words = ctx.candidate_words_found
        ctx.new_words_found = len(unknown_words)
        log_summary(
            logger,
            "Phase 2 filter",
            **{
                "in": len(all_words),
                "out": len(unknown_words),
                "frequency_ranked": frequency_ranked,
                "known_hits": known_hits,
                "known_db_added": known_db_added,
                "known_db_total": known_db_total,
                "frequency_rejects": frequency_rejects,
                "word_list_rejects": word_list_rejects,
                "script_rejects": script_rejects,
                "wordset_rejects": wordset_rejects,
                "episode_rejects": episode_rejects,
                "duplicate_sentence_rejects": duplicate_sentence_rejects,
                "i_plus_one_rejects": i_plus_one_rejects,
                "sentence_length_rejects": sentence_length_rejects,
                "whitelist_force_includes": whitelist_force_includes,
                "no_definition_rejects": no_definition_rejects,
                "duplicate_expression_rejects": duplicate_expression_rejects,
            },
        )
        return unknown_words

    @staticmethod
    def _frequency_filter_notice(removed: int, low: int, high: int) -> str:
        """Word the frequency-band report for whichever ends are actually set.

        The max-only string is kept verbatim so its existing translations survive;
        the band and min-only wordings are the only new strings here.
        """
        if low > 0 and high > 0:
            return tr_format(
                QCoreApplication.translate(
                    "EpisodeProcessor", "Frequency filter: removed %1 words outside ranks %2-%3"
                ),
                removed,
                low,
                high,
            )
        if low > 0:
            return tr_format(
                QCoreApplication.translate(
                    "EpisodeProcessor", "Frequency filter: removed %1 words more common than rank %2"
                ),
                removed,
                low,
            )
        return tr_format(
            QCoreApplication.translate("EpisodeProcessor", "Frequency filter: removed %1 words outside top %2"),
            removed,
            high,
        )

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
        """Phase 3: extract media (screenshots + audio; audio + cover art when
        ``audio_only``) for each unknown word."""
        self._announce_stage(
            progress_callback,
            3,
            QCoreApplication.translate("EpisodeProcessor", "Extracting media"),
        )

        # Resolve the animated screenshot format once and announce any fallback
        # in the Activity Log, then thread the same value into the batch so the
        # warning and the encode can never disagree. Only relevant when animated
        # screenshots are configured and we are not in audiobook (audio_only)
        # mode, where screenshots are skipped entirely; otherwise the batch's
        # own default resolves to the static path.
        picture_mapped = bool(self.config.anki_fields.get("picture"))
        audio_mapped = bool(self.config.anki_fields.get("audio"))
        extra_kwargs: dict[str, str | None] = {}
        if picture_mapped and self.config.screenshot_animated and not audio_only:
            animated_fmt = self.media_extractor.resolve_animated_format()
            extra_kwargs["animated_format"] = animated_fmt
            if animated_fmt == "webp" and self.config.screenshot_animated_format == "avif":
                self.presenter.show_warning(
                    QCoreApplication.translate(
                        "EpisodeProcessor",
                        "Using WebP for animated screenshots — this ffmpeg build has no AVIF (libsvtav1) encoder.",
                    )
                )
            elif animated_fmt is None:
                self.presenter.show_warning(
                    QCoreApplication.translate(
                        "EpisodeProcessor",
                        "Animated screenshots unavailable — this ffmpeg build has no AVIF or WebP encoder; "
                        "switch to static screenshots in Settings.",
                    )
                )

        if picture_mapped or audio_mapped:
            media_results = self.media_extractor.extract_media_batch(
                video_file,
                unknown_words,
                progress_callback,
                cancelled_check=lambda: self.cancelled,
                temp_folder=run_temp_folder,
                audio_track_override=audio_track_override,
                audio_only=audio_only,
                include_screenshot=picture_mapped,
                include_audio=audio_mapped,
                **extra_kwargs,
            )
        else:
            media_results = [(word, MediaData()) for word in unknown_words]

        self._audio_stage.fetch_expression_audio(media_results, progress_callback)

        log_summary(
            logger,
            "Phase 3 extract",
            attempted=len(unknown_words),
            produced=len(media_results),
            failures=max(0, len(unknown_words) - len(media_results)),
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
        self._announce_stage(
            progress_callback,
            4,
            QCoreApplication.translate("EpisodeProcessor", "Fetching definitions"),
        )
        words_with_media = [word for word, _ in media_results]
        # Keyed on mined_form (the card-front spelling), NOT lemma: unidic's
        # canonical lemma collapses kanji variants (殺る → 遣る), so lemma-keyed
        # lookups returned the wrong homograph's definition for the spelling
        # the card shows. The sentence's contextual reading rides along as a
        # ranking BOOST (5.1): a homograph like 辛い(からい/つらい) leads with the
        # sense matching this occurrence's reading, the other survives below.
        # expression_reading (the mined form's own, context-disambiguated
        # reading; falling back to lemma/surface reading) hiragana-normalized
        # to match the folded stored readings.
        lookup_pairs: list[tuple[str, str | None]] = [
            (w.mined_form, katakana_to_hiragana(w.expression_reading or w.lemma_reading or w.reading))
            for w in words_with_media
        ]
        # Lookup-miss fallback context (5.2): mined_form → (safe lemma alternate,
        # cType). A non-identical lemma is admitted only when it changes trailing
        # okurigana over the same kanji stem; different-kanji canonicalization can
        # name another homograph. Unsafe alternates become empty, but the
        # candidate builder still emits kana-fold + deinflection hypotheses such
        # as 帰れる→帰る. cType is unavailable on TokenizedWord post-parse, so the
        # deinflection mask stays inert here and the rules-column POS check does
        # the gating. First-seen alternate wins, mirroring the batch's dedup.
        fallback_context: dict[str, tuple[str, str | None]] = {}
        for w in words_with_media:
            alternate = w.lemma
            if alternate != w.mined_form and not _differs_by_okurigana_only(w.mined_form, alternate):
                alternate = ""
            fallback_context.setdefault(w.mined_form, (alternate, None))
        definitions = self.definition_service.get_definitions_batch(
            lookup_pairs,
            progress_callback,
            fallback_context,
            is_cancelled=lambda: self.cancelled,
        )
        self.presenter.show_success(
            QCoreApplication.translate(
                "EpisodeProcessor", "Found %n definition(s)", "", sum(1 for d in definitions if d)
            )
        )

        # Optional: fetch concatenated multi-dict glossary if the user mapped
        # the Glossary field. Skipped otherwise to avoid the extra chain walk
        # per word.
        glossaries: list[str | None] = [None] * len(words_with_media)
        if self.config.anki_fields.get("glossary"):
            glossaries = self.definition_service.get_glossaries_batch(
                lookup_pairs,
                progress_callback,
                is_cancelled=lambda: self.cancelled,
            )
            # get_glossaries_batch has no miss-fallback mechanism, so a miss may
            # retry once under a same-kanji, okurigana-only lemma alternate.
            # Different-kanji UniDic lemmas may be another homograph and are
            # excluded. Hits pay nothing; None progress avoids a second cycle.
            retry_idx = [
                i
                for i, g in enumerate(glossaries)
                if not g
                and words_with_media[i].lemma != words_with_media[i].mined_form
                and _differs_by_okurigana_only(
                    words_with_media[i].mined_form,
                    words_with_media[i].lemma,
                )
            ]
            if retry_idx:
                retry_pairs: list[tuple[str, str | None]] = [
                    (
                        words_with_media[i].lemma,
                        katakana_to_hiragana(words_with_media[i].lemma_reading or words_with_media[i].reading),
                    )
                    for i in retry_idx
                ]
                retry_glossaries = self.definition_service.get_glossaries_batch(
                    retry_pairs,
                    None,
                    is_cancelled=lambda: self.cancelled,
                )
                for i, g in zip(retry_idx, retry_glossaries, strict=True):
                    glossaries[i] = g

        # Pitch follows the same identity ladder as definitions/audio: the card
        # front and its selected reading first, then only a same-kanji,
        # okurigana-only UniDic lemma on a miss. Different-kanji canonicalization
        # can name another word (呪言/じゅごん → 言祝ぎ/ことほぎ).
        # ``resolved_reading`` remains the lemma-fallback realignment for modern
        # じる fronts over archaic ずる lemmas.
        pitch_data: list[tuple[str | None, str | None]] = [(None, None)] * len(words_with_media)
        if self.pitch_accent_service and self.pitch_accent_service.is_available():
            primary_pitch_keys = [
                (
                    w.mined_form,
                    w.expression_reading or w.resolved_reading or w.lemma_reading or w.reading,
                    w.pos,
                )
                for w in words_with_media
            ]
            pitch_data = self.pitch_accent_service.lookup_batch_detailed(
                primary_pitch_keys,
                fmt=self.config.pitch_category_format,
            )
            retry_idx = [
                i
                for i, ((position, _), word) in enumerate(zip(pitch_data, words_with_media, strict=True))
                if not position
                and word.lemma != word.mined_form
                and _differs_by_okurigana_only(word.mined_form, word.lemma)
            ]
            if retry_idx:
                fallback_pitch_keys = [
                    (
                        words_with_media[i].lemma,
                        words_with_media[i].resolved_reading
                        or words_with_media[i].lemma_reading
                        or words_with_media[i].reading,
                        words_with_media[i].pos,
                    )
                    for i in retry_idx
                ]
                fallback_pitch_data = self.pitch_accent_service.lookup_batch_detailed(
                    fallback_pitch_keys,
                    fmt=self.config.pitch_category_format,
                )
                for i, fallback in zip(retry_idx, fallback_pitch_data, strict=True):
                    if fallback[0]:
                        pitch_data[i] = fallback
            found_count = sum(1 for pos, _ in pitch_data if pos)
            self.presenter.show_info(
                tr_format(
                    QCoreApplication.translate("EpisodeProcessor", "Pitch accent data: %1/%2 words"),
                    found_count,
                    len(words_with_media),
                )
            )

        definition_hits = sum(1 for definition in definitions if definition)
        log_summary(
            logger,
            "Phase 4 lookup",
            looked_up=len(words_with_media),
            definition_hits=definition_hits,
            definition_misses=max(0, len(words_with_media) - definition_hits),
            frequency_hits=sum(1 for word in words_with_media if word.frequency_sources),
            pitch_hits=sum(1 for position, _category in pitch_data if position),
            audio_hits=sum(1 for _word, media in media_results if media.audio_path is not None or media.audio_filename),
        )
        return definitions, glossaries, pitch_data

    def _phase5_create(
        self,
        ctx: _EpisodeContext,
        media_results: list[tuple[TokenizedWord, MediaData]],
        definitions: list[str | None],
        glossaries: list[str | None],
        pitch_data: list[tuple[str | None, str | None]],
        progress_callback: ProgressCallback | None,
    ) -> tuple[int, list[int], list[str]]:
        """Phase 5: build CardPayloads and submit them to Anki.

        Returns ``(cards_created, created_note_ids, mined_forms)`` where
        ``mined_forms`` is the list of ``mined_form`` strings for the cards
        that were created — carried onto ``ProcessingResult`` so the Undo
        callback can revert ``source='mined'`` rows in known_words.db (OVH-030).
        """
        self._announce_stage(
            progress_callback,
            5,
            QCoreApplication.translate("EpisodeProcessor", "Creating Anki cards"),
        )
        card_data: list[CardPayload] = []
        # Self-contained PER-FIELD glossary styling: collect the dictionary CSS
        # entries ONCE per episode (collect_dictionary_css_entries does registry
        # + per-dict SQLite I/O) but attach a <style> block to EVERY mapped
        # styled field inside the loop — tree-shaken against that field's own
        # HTML and filtered to the dictionaries present in it (Issue #93;
        # witness/variant scans are cheap cached string work; freshly rendered
        # bodies are born stamped, so witnesses are already post-stamp). Each
        # field must carry its own TRAILING block: JS-driven note types (Kiku)
        # keep fields in inert <template>s and re-inject them one at a time
        # through DOMParser→body.innerHTML, so a <style> in another field never
        # applies and a field-LEADING <style> is hoisted to <head> and dropped
        # (attach_card_style_block enforces both — the old single-carrier
        # "card-wide <style>" model broke every Kiku page). Skipping the collect
        # when neither field is mapped keeps the no-styling path I/O-free.
        glossary_mapped = bool(self.config.anki_fields.get("glossary"))
        definition_mapped = bool(self.config.anki_fields.get("definition"))
        styling_on = glossary_mapped or definition_mapped
        episode_dict_css_entries = collect_dictionary_css_entries(self.config) if styling_on else []
        for (word, media), definition, glossary, (pitch_position, pitch_category) in zip(
            media_results, definitions, glossaries, pitch_data, strict=True
        ):
            if not definition:
                continue

            extra_fields: dict[str, str] = {}
            if pitch_position:
                extra_fields["pitch_position"] = pitch_position
                # Inline pitch graph / overline (6.3): rendered self-contained
                # SVG/HTML, gated on the field being mapped so the default config
                # stays byte-identical. Uses the SAME reading the pitch lookup
                # used for the morae, and the entry's
                # per-mora nasal/devoice positions. One extra dict lookup only for
                # a pitched word with the field mapped (both off by default).
                want_graph = bool(self.config.anki_fields.get("pitch_graph"))
                want_text = bool(self.config.anki_fields.get("pitch_text"))
                if (want_graph or want_text) and self.pitch_accent_service:
                    reading = word.expression_reading or word.resolved_reading or word.lemma_reading or word.reading
                    entry = self.pitch_accent_service.lookup_entry(word.mined_form, reading)
                    if (
                        entry is None
                        and word.lemma != word.mined_form
                        and _differs_by_okurigana_only(word.mined_form, word.lemma)
                    ):
                        fallback_reading = word.resolved_reading or word.lemma_reading or word.reading
                        entry = self.pitch_accent_service.lookup_entry(word.lemma, fallback_reading)
                        if entry is not None:
                            reading = fallback_reading
                    nasal = entry.nasal if entry else ()
                    devoice = entry.devoice if entry else ()
                    if want_graph:
                        graph_html = render_pitch_graph_field(pitch_position, reading)
                        if graph_html:
                            extra_fields["pitch_graph"] = graph_html
                    if want_text:
                        text_html = render_pitch_text_field(pitch_position, reading, nasal, devoice)
                        if text_html:
                            extra_fields["pitch_text"] = text_html
            if pitch_category:
                extra_fields["pitch_category"] = pitch_category
            if word.frequency_sources:
                extra_fields["frequency"] = render_frequency_html(word.frequency_sources)
            # Numeric sort column: the harmonic mean of the per-source ranks
            # (Yomitan getFrequencyHarmonic), with the 9999999 sentinel for
            # words no source ranks so they sort *last* rather than before rank 1
            # (an omitted field reads as empty string in Anki's browser). Gated on
            # the field being mapped so the default config's notes stay byte-for-
            # byte identical; the sentinel is emitted only when a user opts in.
            if self.config.anki_fields.get("frequency_sort"):
                extra_fields["frequency_sort"] = (
                    str(word.frequency_harmonic_rank) if word.frequency_harmonic_rank is not None else "9999999"
                )
            if glossary:
                extra_fields["glossary"] = (
                    attach_card_style_block(glossary, dict_css_entries=episode_dict_css_entries)
                    if glossary_mapped
                    else glossary
                )
            # Stamp the source unconditionally; AnkiService gates the write on a
            # non-empty configured field name (anki_fields["source"]). Reading-tab
            # runs carry a per-unit page/chapter label ("… @ p.42"); a miss
            # (synthetic/rounded start_time) falls back to the timestamp format,
            # never a KeyError. ctx.unit_labels is None on the video path.
            unit_label = ctx.unit_labels.get(int(word.start_time)) if ctx.unit_labels else None
            if unit_label:
                extra_fields["source"] = f"{ctx.source_label} @ {unit_label}"
            else:
                extra_fields["source"] = f"{ctx.source_label} @ {_format_timestamp(word.start_time)}"

            # Per-field self-containment: the definition field carries its OWN
            # trailing block whenever it's mapped — regardless of the glossary
            # field, which JS note types never render alongside it.
            card_definition = definition
            if definition_mapped:
                card_definition = attach_card_style_block(definition, dict_css_entries=episode_dict_css_entries)

            card_data.append(
                CardPayload(
                    word=word,
                    media=media,
                    definition=card_definition,
                    extra_fields=extra_fields if extra_fields else None,
                )
            )

        # Name the mined_form (the lookup key / card front), not the lemma, so
        # the warning lists the spelling that actually missed.
        skipped_words = [
            word.mined_form for (word, _), definition in zip(media_results, definitions, strict=True) if not definition
        ]
        if skipped_words:
            preview = ", ".join(skipped_words[:10])
            more = f" (+{len(skipped_words) - 10} more)" if len(skipped_words) > 10 else ""
            self.presenter.show_warning(
                tr_format(
                    QCoreApplication.translate("EpisodeProcessor", "Skipped %1 words with no definition found: %2%3"),
                    len(skipped_words),
                    preview,
                    more,
                )
            )

        self.anki_service.set_cancelled_check(lambda: self.cancelled)
        try:
            created_note_ids = self.anki_service.create_cards_batch(card_data, progress_callback)
        finally:
            self.anki_service.set_cancelled_check(None)
        cards_created = len(created_note_ids)
        confirmed_mined_forms = list(self.anki_service.last_created_mined_forms)
        if self.cancelled and CANCELLED_ERROR not in ctx.errors:
            ctx.errors.append(CANCELLED_ERROR)

        self.presenter.show_success(
            QCoreApplication.translate("EpisodeProcessor", "Successfully created %n card(s)", "", cards_created)
        )
        media_failures = self.anki_service.last_media_store_failures
        if isinstance(media_failures, int) and media_failures > 0:
            self.presenter.show_warning(
                QCoreApplication.translate(
                    "EpisodeProcessor",
                    "%n media file(s) could not be stored in Anki — those cards have no audio or screenshot",
                    "",
                    media_failures,
                )
            )
        skipped_duplicates = self.anki_service.last_skipped_duplicates
        if isinstance(skipped_duplicates, int) and skipped_duplicates > 0:
            self.presenter.show_warning(
                QCoreApplication.translate(
                    "EpisodeProcessor",
                    "Skipped %n word(s) Anki flagged as duplicates (same Expression)",
                    "",
                    skipped_duplicates,
                )
            )

        # Collect mined_forms from the cards Anki confirmed created.
        # Stored as mined_form (POS-aware) to match what Anki records in the
        # Expression field (Issue #5). Returned to the caller so process_episode
        # The known-words transaction receipt below remains the separate value
        # stamped onto ProcessingResult.mined_forms for Undo (OVH-030).
        mined_words = set(confirmed_mined_forms)

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
        #
        # Undo must revert only the 'mined' rows THIS session inserted. Default
        # empty: any DB failure must be fail-safe and never authorize deletion
        # of a pre-existing row. The insert returns its exact transaction-owned
        # receipt, avoiding a racy before/after snapshot.
        mined_forms_for_undo: list[str] = []
        if self.known_word_db and self.known_word_db.is_available() and mined_words:
            try:
                mined_forms_for_undo = sorted(self.known_word_db.add_words_with_receipt(mined_words, source="mined"))
            except (sqlite3.Error, OSError) as e:
                logger.warning(
                    "Could not record %d mined words in known_words.db (%s); "
                    "the cards were still created. The cache will re-sync next run.",
                    len(mined_words),
                    e,
                )

        media_failure_count = media_failures if isinstance(media_failures, int) and media_failures > 0 else 0
        duplicate_count = skipped_duplicates if isinstance(skipped_duplicates, int) and skipped_duplicates > 0 else 0
        log_summary(
            logger,
            "Phase 5 create",
            attempted=len(card_data),
            created=cards_created,
            duplicates=duplicate_count,
            failures=max(0, len(card_data) - cards_created - duplicate_count),
            no_definition=len(skipped_words),
            media_failures=media_failure_count,
        )
        return cards_created, created_note_ids, mined_forms_for_undo

    def _reset_run_write_state(self) -> None:
        """Clear Anki write provenance before any preflight for a new run."""
        self.anki_service.last_created_note_ids = []
        self.anki_service.anki_write_state = AnkiWriteState.NO_NOTE_WRITE

    def _run_pipeline(
        self,
        ctx: _EpisodeContext,
        cancel_event: threading.Event | None,
        body: Callable[[Path], ProcessingResult],
    ) -> ProcessingResult:
        """Shared run skeleton for :meth:`process_episode` / :meth:`process_reading`.

        Owns ONLY the machinery both entry points share verbatim: the pre-flight
        gates (staleness backstop, card-target verify, then offline dictionary),
        all *outside* the try so a ``SetupError`` propagates instead of collapsing into a
        "completed" result and *before* temp allocation so no dir leaks on
        failure), the per-run temp folder, the partial-IDs reset, the per-run
        ``_external_cancel`` bridge, and the try/except/finally tail (partial-card
        harvest on failure; bridge drop + temp cleanup in ``finally``). ``body``
        receives the allocated ``run_temp_folder`` and returns this run's
        ``ProcessingResult``; it may early-return at phase boundaries and may
        raise (caught here). Everything path-specific — identity/ctx construction,
        the video-only audio-stream-cache invalidation, the reading occurrence
        floor — lives in the caller's ``body`` closure.
        """
        # Reset the run-scoped Anki accumulators FIRST — before the pre-flight
        # gates, which can raise SetupError straight out of this method. A
        # caller that catches that raise still needs the truth about THIS run:
        # on a shared processor/service (Batch mines every pair through one
        # AnkiService) the previous item's confirmed write would otherwise still
        # be standing, and its ids would be attributed to an item that never got
        # as far as Anki.
        #
        # * last_created_note_ids: the except handlers harvest ONLY IDs created
        #   during THIS run (OVH-008).
        # * anki_write_state: nothing has been submitted yet, so the honest
        #   answer is NO_NOTE_WRITE. create_cards_batch escalates it from here
        #   and never resets it, so this reset is the mining-pipeline boundary (D30).
        self._reset_run_write_state()

        self.check_resource_staleness()
        self._preflight_card_target()
        self.check_offline_dictionary()
        run_temp_folder = self._allocate_run_temp_folder()
        keep_temp = bool(os.environ.get("ANKI_MINER_KEEP_TEMP"))

        # Bridge the caller's cancel_event into this run's cancellation
        # checkpoints for the duration of this call only: the phase checkpoints
        # and the media extractor's cancelled_check consult self.cancelled, which
        # folds this source in. See __init__ for why the sticky self._cancelled
        # flag must NOT be used here (shared processor reuse across runs); the
        # finally below drops the reference so the bridge is per-run by construction.
        if cancel_event is not None:
            self._external_cancel = cancel_event.is_set
        try:
            result = body(run_temp_folder)
            if result.success:
                self._record_difficulty(ctx)
            return self._stamp_write_provenance(result)
        except AnkiMinerException as e:
            logger.warning("%s: %s", "EpisodeProcessor", e)
            ctx.errors.append(str(e))
            partial_ids = list(self.anki_service.last_created_note_ids)
            self.presenter.show_error(tr_format(QCoreApplication.translate("EpisodeProcessor", "Error: %1"), str(e)))
            return self._stamp_write_provenance(self._partial_failure_result(ctx, partial_ids), failure=e)
        except MemoryError:
            raise
        except Exception as e:
            logger.exception("EpisodeProcessor unhandled exception")
            ctx.errors.append(f"Unexpected error: {e}")
            partial_ids = list(self.anki_service.last_created_note_ids)
            self.presenter.show_error(
                tr_format(QCoreApplication.translate("EpisodeProcessor", "Unexpected error: %1"), str(e))
            )
            return self._stamp_write_provenance(self._partial_failure_result(ctx, partial_ids), failure=e)
        finally:
            if cancel_event is not None:
                self._external_cancel = None
            if keep_temp:
                logger.info(
                    "ANKI_MINER_KEEP_TEMP set; leaving run temp folder at %s",
                    run_temp_folder,
                )
            else:
                shutil.rmtree(run_temp_folder, ignore_errors=True)

    def _run_curation(
        self,
        ctx: _EpisodeContext,
        unknown_words: list[TokenizedWord],
        line_index: list[LineLemmas] | None,
        occurrence_counts: Mapping[str, int],
        curation_callback: Callable[[list], list | None],
    ) -> list[TokenizedWord] | ProcessingResult:
        """Shared interactive-curation step for both mining paths.

        Attaches the per-word sentence candidates (when a line index exists) and
        occurrence counts the curator dialog needs, then invokes the callback.
        Preserves the trichotomy of the inline blocks it replaces:

        * cancelled/rejected (callback returns ``None``) → returns a cancelled
          ``ProcessingResult`` (caller returns it);
        * confirmed with nothing selected (empty list) → returns a completed
          zero-card ``ProcessingResult`` (caller returns it) — an intentional
          "card nothing this run", NOT a cancellation, so stats/batch status stay
          accurate;
        * a non-empty selection → returns the curated word list (caller continues).

        The caller distinguishes the two outcomes with ``isinstance(..., ProcessingResult)``.
        """
        if line_index is not None:
            # Attach alternative example sentences so the curator can offer a
            # per-word sentence picker (no-op for words on a single line).
            self.word_filter.attach_sentence_candidates(unknown_words, line_index)
        # Attach per-run occurrence counts for the curator's "Occurrences"
        # column/sort (Issue #88).
        self.word_filter.attach_occurrence_counts(unknown_words, occurrence_counts)
        # A callback carrying suppress_curation_messages=True (the season
        # pre-pass capture) asks for a quiet run: its [] return is a capture
        # artifact, not a user decision, so the per-episode info lines would
        # only mislead. The worker narrates the season flow itself.
        quiet = getattr(curation_callback, "suppress_curation_messages", False)
        curated = curation_callback(unknown_words)
        if curated is None:
            # The user cancelled/rejected the curation dialog.
            return self._cancelled_result_from_ctx(ctx)
        ctx.new_words_found = len(curated)
        if not curated:
            if not quiet:
                self.presenter.show_info(
                    QCoreApplication.translate("EpisodeProcessor", "No words selected for card creation")
                )
            return ctx.build_result(new_words_found=0)
        if not quiet:
            self.presenter.show_info(
                QCoreApplication.translate("EpisodeProcessor", "Mining %n selected word(s)", "", len(curated))
            )
        return curated

    def process_episode(
        self,
        video_file: Path,
        subtitle_file: Path,
        progress_callback: ProgressCallback | None = None,
        curation_callback: Callable[[list], list | None] | None = None,
        episode_name_override: str | None = None,
        series_name_override: str | None = None,
        audio_track_override: int | None = None,
        source_label_override: str | None = None,
        audio_only: bool = False,
        cancel_event: threading.Event | None = None,
    ) -> ProcessingResult:
        """Process a single episode and create Anki cards.

        Orchestrates the five phase helpers: parse → filter → extract media →
        lookup definitions/pitch → create cards. Each phase is a small method
        on this class; this entrypoint owns only the phase body (cancellation
        checkpoints and early-return paths), while the shared run skeleton
        (pre-flight, temp allocation, cancel bridge, cleanup) lives in
        :meth:`_run_pipeline`.

        Args:
            video_file: Path to video file.
            subtitle_file: Path to subtitle file.
            progress_callback: Optional progress callback.
            curation_callback: Optional callback for word curation. Receives
                filtered words. Returns the user-selected subset (an empty list
                means "confirmed with nothing selected" → a completed run with
                zero new cards), or ``None`` if the user cancelled/rejected the
                dialog → a cancelled result.
            episode_name_override: Optional override for the episode identity
                passed to stats_service. When ``None`` (default) the identity
                is derived from ``video_file.stem`` (preserves current file-based
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
            cancel_event: Optional threading event set by a worker on
                cancellation. When provided it is bridged into this run's
                phase checkpoints and the media extractor's cancelled_check
                (via :attr:`cancelled`) for the duration of this call only —
                workers must use this instead of the sticky :meth:`cancel`,
                which poisons shared processors across runs (see __init__).

        Returns:
            ProcessingResult with statistics.

        Raises:
            SetupError: note type / field mapping is misconfigured, or no usable
                offline dictionary is installed.
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
            source_label=source_label_override or sanitize_source_label(f"{series_name} — {episode_name}"),
        )

        def _body(run_temp_folder: Path) -> ProcessingResult:
            # Invalidate the per-file audio stream cache before extraction so that
            # cross-run file replacement (re-encode, swap, restore) cannot strand
            # the resolver on stale ffprobe output. Within this run the cache will
            # repopulate on the first probe and protect against double-probes
            # (the 2e0cc13 perf win). Video-only — omitted on the reading path.
            self.media_extractor.invalidate_audio_stream_cache(video_file)

            # Interactive curation offers a per-word sentence picker, which needs
            # the line index (all lines each lemma appears on). Build it for that
            # path too — not just the i+1 filter.
            want_line_index = curation_callback is not None
            with timed_phase("parse", logger):
                all_words, line_index = self._phase1_parse(
                    ctx, subtitle_file, progress_callback, want_line_index=want_line_index
                )
            if self.cancelled:
                return self._cancelled_result_from_ctx(ctx)
            if not all_words:
                self.presenter.show_warning(
                    QCoreApplication.translate("EpisodeProcessor", "No words found in subtitles")
                )
                return ctx.build_result()

            with timed_phase("filter", logger):
                unknown_words = self._phase2_filter(ctx, all_words, line_index, progress_callback)
            if self.cancelled:
                return self._cancelled_result_from_ctx(ctx)
            if not unknown_words:
                self._report_no_mineable_words(ctx)
                return ctx.build_result(new_words_found=0)

            if curation_callback is not None:
                # count_lemmas reuses the phase-1 parse cache, so no second MeCab pass.
                outcome = self._run_curation(
                    ctx,
                    unknown_words,
                    line_index,
                    self.subtitle_parser.count_lemmas(subtitle_file),
                    curation_callback,
                )
                if isinstance(outcome, ProcessingResult):
                    return outcome
                unknown_words = outcome

            with timed_phase("extract", logger):
                media_results = self._phase3_extract(
                    ctx,
                    video_file,
                    unknown_words,
                    progress_callback,
                    run_temp_folder,
                    audio_track_override,
                    audio_only=audio_only,
                )
            if self.cancelled:
                return self._cancelled_result_from_ctx(ctx)
            if not media_results:
                self.presenter.show_warning(
                    QCoreApplication.translate("EpisodeProcessor", "No media extracted successfully")
                )
                return ctx.build_result(errors=["Media extraction failed for all words"])
            self.presenter.show_success(
                QCoreApplication.translate("EpisodeProcessor", "Extracted media for %n word(s)", "", len(media_results))
            )

            with timed_phase("lookup", logger):
                definitions, glossaries, pitch_data = self._phase4_lookup(ctx, media_results, progress_callback)
            if self.cancelled:
                return self._cancelled_result_from_ctx(ctx)

            with timed_phase("cards", logger):
                cards_created, created_note_ids, mined_forms = self._phase5_create(
                    ctx, media_results, definitions, glossaries, pitch_data, progress_callback
                )
            result = ctx.build_result(
                cards_created=cards_created,
                card_ids=created_note_ids,
                mined_forms=mined_forms,
            )
            self._record_session(ctx, result)
            return result

        return self._run_pipeline(ctx, cancel_event, _body)

    def _stamp_write_provenance(
        self,
        result: ProcessingResult,
        *,
        failure: BaseException | None = None,
    ) -> ProcessingResult:
        """Record what this run can prove about Anki note writes (D30).

        The single funnel: every ``ProcessingResult`` :meth:`_run_pipeline`
        hands back — success, early phase return, cancellation, partial failure —
        passes through here, so none can escape still carrying the dataclass
        default. Automatic retry consumes these two fields; the pipeline is the
        last place that can see the live service state and the raised exception
        before both are flattened into ``errors`` strings.

        Fail closed on the write state: a service whose ``anki_write_state`` is
        not a real :class:`AnkiWriteState` (a stub, a mock, a string) has proved
        nothing, so it reports the unsafe answer rather than the retryable one.
        """
        state = getattr(self.anki_service, "anki_write_state", None)
        result.anki_write_state = state if isinstance(state, AnkiWriteState) else AnkiWriteState.NOTE_WRITE_UNCERTAIN
        result.failure_is_transient = failure is not None and is_transient_anki_transport_error(failure)
        return result

    def _partial_failure_result(self, ctx: _EpisodeContext, partial_ids: list[int]) -> ProcessingResult:
        """Shared except-handler tail: note any partial cards and build the failure result."""
        if partial_ids:
            ctx.errors.append(
                QCoreApplication.translate(
                    "EpisodeProcessor",
                    "Run failed after creating %n card(s); they remain in Anki and can be undone.",
                    "",
                    len(partial_ids),
                )
            )
        return ctx.build_result(
            cards_created=len(partial_ids),
            card_ids=partial_ids,
        )

    def _phase3_reading_media(
        self,
        ctx: _EpisodeContext,
        document: ReadingDocument,
        unknown_words: list[TokenizedWord],
        progress_callback: ProgressCallback | None,
        run_temp_folder: Path,
    ) -> list[tuple[TokenizedWord, MediaData]]:
        """Phase 3' (reading): materialize each word's page/cover image, then fetch
        expression audio. No ffmpeg, no sentence audio.

        Each surviving word maps back to its source unit via
        ``int(word.start_time)`` (the parser stamps the unit index as the dummy
        start; an i+1 swap re-stamps it to the chosen line's unit, so the image
        and page label always match the card's sentence). Unique ``ImageRef``s
        materialize once (a page shared by many words, or a book cover shared by
        every word, converts a single time). Image failures never abort the
        volume — it keeps mining imageless (the image band is still consumed
        unconditionally):

        * A ``SetupError`` from an unsafe archive is caught per-archive: one
          warning, the archive is skipped for every remaining ref.
        * A ``zipfile.BadZipFile`` means the whole archive is corrupt/unusable —
          same per-archive skip-and-warn-once handling.
        * A ``PIL.UnidentifiedImageError`` / ``OSError`` (corrupt or undecodable
          page, or a missing codec in a frozen bundle) is per-ref: one warning
          naming the page, that word goes imageless, the rest of the archive
          stays readable.

        Warnings fire once per failing archive/ref (``failed_archives`` /
        ``failed_refs`` memos) even when the ref is shared by many words.
        """
        # Label-only kind split: manga cards carry a distinct page image each,
        # while a book attaches one cover to every card (txt and subtitles have
        # none) — so the image-stage wording differs. Only the text varies.
        # Derived once here, used at the two sites below.
        is_book = document.kind in ("book", "subtitle")
        image_stage_desc = (
            QCoreApplication.translate("EpisodeProcessor", "Preparing card images")
            if is_book
            else QCoreApplication.translate("EpisodeProcessor", "Preparing page images")
        )
        image_item_template = (
            QCoreApplication.translate("EpisodeProcessor", "Card image: %1")
            if is_book
            else QCoreApplication.translate("EpisodeProcessor", "Page image: %1")
        )
        self._announce_stage(progress_callback, 3, image_stage_desc)
        images_dir = run_temp_folder / "images"
        units_by_index = {unit.index: unit for unit in document.units}
        picture_mapped = bool(self.config.anki_fields.get("picture"))

        # YOU own the per-run bookkeeping: a unique-ref → materialized-path memo,
        # a set of archives whose safety gate failed or that are corrupt (skip
        # their remaining refs, warn once each), and a set of individual refs
        # whose page failed to decode (skip re-attempt, warn once each even when
        # the page/cover is shared by many words).
        ref_cache: dict[ImageRef, Path] = {}
        failed_archives: set[Path] = set()
        failed_refs: set[ImageRef] = set()

        media_results: list[tuple[TokenizedWord, MediaData]] = []

        # on_start fires even for text-only volumes with zero image refs, so the
        # stage still declares its true denominator (which is then legitimately
        # zero) rather than going silent.
        if progress_callback is not None:
            progress_callback.on_start(
                len(unknown_words),
                image_stage_desc,
            )
        for i, word in enumerate(unknown_words):
            # Honor cancel WITHIN the loop (mirrors AudioStage._run_stage): a
            # large mokuro volume can hold hundreds of pages, and without this a
            # cancel would only take effect after every page is materialized. Break
            # and return the partial results — the audio fetchers below and the
            # phase-boundary check in process_reading each re-check cancelled.
            if self.cancelled:
                break
            media = MediaData()
            unit = units_by_index.get(int(word.start_time))
            ref = unit.image_ref if unit is not None else None
            if picture_mapped and ref is not None and ref.source not in failed_archives and ref not in failed_refs:
                image_path = ref_cache.get(ref)
                if image_path is None:
                    try:
                        image_path = prepare_card_image(ref, images_dir)
                    except SetupError:
                        # Appending to document.warnings here would be lost (the
                        # up-front drain already ran) — surface directly, once
                        # per archive.
                        failed_archives.add(ref.source)
                        self.presenter.show_warning(
                            tr_format(
                                QCoreApplication.translate(
                                    "EpisodeProcessor",
                                    "Skipped unsafe image archive %1 — its cards have no page image",
                                ),
                                ref.source.name,
                            )
                        )
                        image_path = None
                    except ReadingImageArchiveError:
                        failed_archives.add(ref.source)
                        self.presenter.show_warning(
                            tr_format(
                                QCoreApplication.translate(
                                    "EpisodeProcessor",
                                    "Skipped corrupt image archive %1 — its cards have no page image",
                                ),
                                ref.source.name,
                            )
                        )
                        image_path = None
                    except (
                        ReadingImageMemberError,
                        OSError,
                        ValueError,
                        zipfile.BadZipFile,
                        RuntimeError,
                        NotImplementedError,
                        EOFError,
                    ) as exc:
                        # An image failure must never abort the volume (the plan's
                        # degradation policy: keep mining imageless). A BadZipFile
                        # (NOT an OSError subclass) means the whole archive is
                        # corrupt → skip its remaining refs, warn once, like the
                        # unsafe-archive gate. A PIL UnidentifiedImageError / bare
                        # OSError (undecodable page, missing codec in a frozen
                        # bundle) is per-ref → warn once naming the page, drop this
                        # word's image, leave the rest of the archive readable.
                        if ref.entry is not None and isinstance(exc, zipfile.BadZipFile):
                            failed_archives.add(ref.source)
                            self.presenter.show_warning(
                                tr_format(
                                    QCoreApplication.translate(
                                        "EpisodeProcessor",
                                        "Skipped corrupt image archive %1 — its cards have no page image",
                                    ),
                                    ref.source.name,
                                )
                            )
                        else:
                            failed_refs.add(ref)
                            self.presenter.show_warning(
                                tr_format(
                                    QCoreApplication.translate(
                                        "EpisodeProcessor",
                                        "Skipped unreadable page image %1 — its card has no picture",
                                    ),
                                    ref.entry if ref.entry is not None else ref.source.name,
                                )
                            )
                        image_path = None
                    else:
                        ref_cache[ref] = image_path
                if image_path is not None:
                    media.screenshot_path = image_path
                    media.screenshot_filename = image_path.name
            media_results.append((word, media))
            if progress_callback is not None:
                progress_callback.on_progress(
                    i + 1,
                    tr_format(image_item_template, word.mined_form),
                )
        if progress_callback is not None:
            progress_callback.on_complete()

        self._audio_stage.fetch_expression_audio(media_results, progress_callback)

        self._audio_stage.fetch_sentence_audio(media_results, progress_callback)

        log_summary(
            logger,
            "Phase 3 reading media",
            attempted=len(unknown_words),
            produced=len(media_results),
            failures=max(0, len(unknown_words) - len(media_results)),
            images=len(ref_cache),
            degradations=len(failed_archives) + len(failed_refs),
            archive_failures=len(failed_archives),
            ref_failures=len(failed_refs),
        )
        return media_results

    def process_reading(
        self,
        document: ReadingDocument,
        *,
        progress_callback: ProgressCallback | None = None,
        curation_callback: Callable[[list], list | None] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> ProcessingResult:
        """Mine a loaded reading document (manga volume / novel) into Anki cards.

        Mirrors :meth:`process_episode`'s skeleton over ``ReadingDocument``:
        text-unit parse (phase 1') → filter (phase 2) → image materialization +
        expression audio (phase 3') → definitions (phase 4) → cards (phase 5).
        Video-only steps (ffmpeg extraction, audio-stream cache invalidation)
        are omitted. Each ``document.warnings`` entry (text-only volume, unusable
        cover, unmatched pages) is surfaced up front via
        ``presenter.show_warning`` so load-time degradations stay visible.

        Args:
            document: The loaded document to mine.
            progress_callback: Optional progress callback; wraps only phases
                3'/4/5 in a single weighted sweep.
            curation_callback: Optional per-word curation callback; same
                semantics as :meth:`process_episode`.
            cancel_event: Optional worker cancel event, bridged into this run's
                checkpoints for its duration only (see __init__).

        Returns:
            ProcessingResult with statistics.

        Raises:
            SetupError: note type / field mapping misconfigured, a stale dict
                index needs reimport, or no usable offline dictionary is installed.
            AnkiConnectionError: AnkiConnect is unreachable.
        """
        # Manga and subtitle sources carry a meaningful series (mokuro title /
        # parent folder), so prefix it; books use the bare episode title.
        if document.kind in ("manga", "subtitle"):
            source_label = sanitize_source_label(f"{document.series} — {document.episode}")
        else:
            source_label = document.episode
        ctx = _EpisodeContext(
            start_time=time.time(),
            video_file_str="",
            subtitle_file_str="",
            episode_name=document.episode,
            series_name=document.series,
            source_label=source_label,
            unit_labels={unit.index: unit.location_label for unit in document.units},
        )

        # Surface load-time degradations before anything else (the loaders hand
        # plain strings; emit them verbatim).
        for warning in document.warnings:
            self.presenter.show_warning(warning)

        def _body(run_temp_folder: Path) -> ProcessingResult:
            # D4: fuse the two triggers for building the line index. The episode
            # path splits this across caller (curation) and callee (i+1); here we
            # call parse_text_units directly, so an i+1-enabled Mine run must set
            # want_line_index itself — otherwise the filter gets an empty index
            # and silently drops every word.
            want_line_index = self.config.use_i_plus_one_filter or curation_callback is not None
            self._announce_stage(
                progress_callback,
                1,
                QCoreApplication.translate("EpisodeProcessor", "Parsing text"),
            )
            self.presenter.show_info(
                tr_format(
                    QCoreApplication.translate("EpisodeProcessor", "Text: %1"),
                    document.title,
                )
            )
            with timed_phase("parse", logger):
                # Only the per-cue subtitle kind gets the video path's annotation
                # strip + regex filter; manga/OCR and book text pass it through.
                all_words, line_index, counts = self.subtitle_parser.parse_text_units(
                    document.units, want_line_index, subtitle_cleanup=document.kind == "subtitle"
                )
                log_summary(
                    logger,
                    "Phase 1 parse",
                    lines=len(document.units),
                    tokens=sum(counts.values()),
                    unique=len(all_words),
                )
            self._report_ambiguous_readings()
            self.presenter.show_success(
                QCoreApplication.translate("EpisodeProcessor", "Found %n unique word(s)", "", len(all_words))
            )
            ctx.total_words_found = len(all_words)
            if self.cancelled:
                return self._cancelled_result_from_ctx(ctx)
            if not all_words:
                self.presenter.show_warning(
                    QCoreApplication.translate("EpisodeProcessor", "No words found in subtitles")
                )
                return ctx.build_result()

            with timed_phase("filter", logger):
                unknown_words = self._phase2_filter(
                    ctx,
                    all_words,
                    line_index,
                    progress_callback,
                    occurrence_counts=counts,
                    min_occurrence=self.config.reading_min_occurrence,
                )
            if self.cancelled:
                return self._cancelled_result_from_ctx(ctx)
            if not unknown_words:
                self._report_no_mineable_words(ctx)
                return ctx.build_result(new_words_found=0)

            if curation_callback is not None:
                outcome = self._run_curation(ctx, unknown_words, line_index, counts, curation_callback)
                if isinstance(outcome, ProcessingResult):
                    return outcome
                unknown_words = outcome

            with timed_phase("reading-media", logger):
                media_results = self._phase3_reading_media(
                    ctx, document, unknown_words, progress_callback, run_temp_folder
                )
            if self.cancelled:
                return self._cancelled_result_from_ctx(ctx)

            with timed_phase("lookup", logger):
                definitions, glossaries, pitch_data = self._phase4_lookup(ctx, media_results, progress_callback)
            if self.cancelled:
                return self._cancelled_result_from_ctx(ctx)

            with timed_phase("cards", logger):
                cards_created, created_note_ids, mined_forms = self._phase5_create(
                    ctx, media_results, definitions, glossaries, pitch_data, progress_callback
                )
            result = ctx.build_result(
                cards_created=cards_created,
                card_ids=created_note_ids,
                mined_forms=mined_forms,
            )
            self._record_session(ctx, result)
            return result

        return self._run_pipeline(ctx, cancel_event, _body)

    def _record_difficulty(self, ctx: _EpisodeContext) -> None:
        """Commit staged difficulty counts after a successful terminal result."""
        if not self.stats_service or ctx.difficulty_total_words == 0:
            return
        try:
            self.stats_service.record_difficulty(
                series_name=ctx.series_name,
                episode_name=ctx.episode_name,
                total_words=ctx.difficulty_total_words,
                unknown_words=ctx.difficulty_unknown_words,
            )
        except (sqlite3.Error, OSError) as e:
            logger.warning(
                "Could not record difficulty for %s in stats.db (%s); the run will continue.",
                ctx.episode_name,
                e,
            )

    def _record_session(self, ctx: _EpisodeContext, result: ProcessingResult) -> None:
        """Record a mining session in the stats service if one is configured."""
        if not self.stats_service:
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
        except (sqlite3.Error, OSError) as e:
            logger.warning(
                "Could not record mining session for %s in stats.db (%s); the cards were still created.",
                ctx.episode_name,
                e,
            )

    def _preflight_card_target(self) -> None:
        """Fail fast on a misconfigured Anki target (Issue #52)."""
        self.anki_service.verify_card_target()

    def check_offline_dictionary(self) -> None:
        """Fail fast when standard filtering has no usable offline provider."""
        require_usable_offline_provider(self.config, self.definition_service)

    def check_resource_staleness(self) -> None:
        """Raise SetupError if any enabled indexed slot needs reimport (4.0).

        The single-episode backstop for the schema-bump migration gate, across
        all three indexed families: consults each injected registry's per-slot
        ``schema_ok`` (NOT the built chains, which silently drop stale slots) so
        a user who upgraded and mines before reimporting gets one actionable
        error instead of a silent zero-card run, an unfiltered flood of rare
        words, or a blank pitch field.

        Queue workers front-run this with their own pre-loop check so a batch
        aborts once rather than per item; this covers the direct single-episode
        callers (episode / manual-pair / deck-builder).

        A family whose registry was not injected is skipped — for frequency and
        pitch that is the normal state when the user has not configured them,
        and it is what keeps both optional.
        """
        message = stale_resource_reimport_error(
            self.config,
            dictionary_registry=self._dictionary_registry,
            frequency_registry=self._frequency_registry,
            pitch_registry=self._pitch_registry,
            families=frozenset(
                kind
                for kind, registry in (
                    ("dictionary", self._dictionary_registry),
                    ("frequency", self._frequency_registry),
                    ("pitch", self._pitch_registry),
                )
                if registry is not None
            ),
        )
        if message is not None:
            raise SetupError(message)

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
        source_label: str | None = None,
        fallback_allowed: bool = False,
    ) -> ProcessingResult:
        """Fetch a YouTube video + subs then run the standard mining pipeline.

        The ``workspace`` directory is owned by the caller (the worker) — this
        method only writes into it via the fetcher; cleanup (``rmtree``) is the
        caller's responsibility, typically in a ``try/finally``.

        Episode identity recorded to stats_service is ``YT:<video_id>`` with
        series ``YouTube`` so that YouTube mining rows never collide with
        file-based folders that happen to share a stem.

        Args:
            url: YouTube video URL (or anything yt-dlp accepts).
            video_id: Pre-extracted video_id; must match the ID yt-dlp will
                write file names with (the worker takes it from probe_metadata).
            workspace: Pre-created, caller-owned directory that yt-dlp writes
                the video and subtitle files into.
            sub_mode: "manual_only" or "auto_only" — chosen by the user based
                on what probe_metadata reported as available.
            fallback_allowed: Forwarded to the fetcher. When True (the worker
                passes ``VideoInfo.has_auto_ja_subs``), a ``manual_only`` fetch may
                fall back to the video's *native* auto-captions if the listed manual
                track is unavailable at download time. Gated on the probe's verdict
                so the fallback can never reach a machine-translated track.
            cancel_event: Threading event set by the worker on cancellation;
                forwarded to the fetcher so in-flight yt-dlp can be killed,
                and passed through to ``process_episode``, which bridges it
                into the mining pipeline's cancellation checkpoints (via
                :attr:`cancelled`) for the duration of this run only.
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
            source_label: Optional origin string for the card "source" field
                (typically the YouTube video title). Forwarded to
                ``process_episode`` as ``source_label_override``. The stats/dedup
                identity (``YT:<video_id>`` / ``YouTube``) is unaffected.

        Returns:
            ProcessingResult from the mining pipeline, with episode identity
            overridden to ``YT:<video_id>``.

        Raises:
            RuntimeError: if no YouTubeFetcherService was injected.
            SetupError: note type / field mapping is misconfigured, or no usable
                offline dictionary is installed.
            AnkiConnectionError: AnkiConnect is unreachable.
            Any fetcher exception propagates unchanged (no workspace cleanup
            happens here — the worker handles it).
        """
        if self._youtube_fetcher is None:
            raise RuntimeError("YouTubeFetcherService not injected — check service_factory")

        self._reset_run_write_state()
        start_time = time.time()
        if cancel_event.is_set():
            return self._make_cancelled_result(start_time)

        # Deliberate early check: fail before the video download rather than
        # after.  process_episode re-runs the same pre-flight post-fetch;
        # that double-check is intentional — cheap idempotent localhost calls.
        # The staleness backstop is likewise cheap and fails before the
        # download when an enabled index needs reimport.
        self.check_resource_staleness()
        self._preflight_card_target()
        self.check_offline_dictionary()

        # The fetch stage consults cancel_event directly (fetch_video gets it
        # verbatim and the post-fetch check below polls it); the mining stage
        # gets it via process_episode's cancel_event keyword, which installs
        # and removes the per-run self._external_cancel bridge itself.
        with timed_phase("youtube-fetch", logger):
            fetched = self._youtube_fetcher.fetch_video(
                url,
                video_id,
                workspace,
                sub_mode,
                progress_cb=fetch_progress_cb,
                cancel_event=cancel_event,
                fallback_allowed=fallback_allowed,
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
            progress_callback=progress_callback,
            curation_callback=curation_callback,
            episode_name_override=f"YT:{video_id}",
            series_name_override="YouTube",
            source_label_override=source_label,
            cancel_event=cancel_event,
        )
