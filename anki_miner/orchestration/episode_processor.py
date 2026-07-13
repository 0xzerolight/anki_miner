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
from anki_miner.models import CANCELLED_ERROR, CardPayload, MediaData, ProcessingResult, TokenizedWord
from anki_miner.models.youtube import FetchedMedia, SubMode
from anki_miner.orchestration.audio_stage import AudioStage
from anki_miner.orchestration.stage_weighted_progress import StageWeightedProgress
from anki_miner.services import (
    AnkiService,
    DefinitionService,
    MediaExtractorService,
    SubtitleParserService,
    WordFilterService,
)
from anki_miner.services.definition_service import collect_dictionary_css
from anki_miner.services.dictionary.card_style_block import build_card_style_block
from anki_miner.services.frequency.multi_frequency_service import harmonic_rank, min_rank
from anki_miner.services.frequency.render import render_frequency_html
from anki_miner.services.pitch_accent.render import (
    render_pitch_graph_field,
    render_pitch_text_field,
)
from anki_miner.services.reading.images import prepare_card_image
from anki_miner.utils import ensure_directory, katakana_to_hiragana
from anki_miner.utils.i18n import tr_format

logger = logging.getLogger(__name__)


if TYPE_CHECKING:
    from anki_miner.interfaces.expression_audio import ExpressionAudioFetcher
    from anki_miner.interfaces.sentence_audio import SentenceAudioFetcher
    from anki_miner.models import LineLemmas
    from anki_miner.models.reading import ImageRef, ReadingDocument
    from anki_miner.services.dictionary.registry import DictionaryRegistry
    from anki_miner.services.frequency.multi_frequency_service import MultiFrequencyService
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


# Strips a contiguous trailing run of ``[...]`` groups plus an optional
# ``-ReleaseGroup`` suffix (Issue #83). ``[^\]]*`` (no nested brackets) keeps this
# linear-time and confines the match to a *trailing* block, so mid-title brackets
# like ``[Blu-ray]`` survive. End-anchored, so a leading series/season prefix is
# never touched.
_ARR_METADATA_RE = re.compile(r"\s*(?:\[[^\]]*\]\s*)+(?:-\S+)?\s*$")

# Cross-episode frequency floor: a word must appear in at least this many
# episodes to be mined (only active when cross-episode counts are supplied to
# process_episode). Was the hidden `config.min_episode_appearances` knob
# (ARC-004: inlined, never surfaced in any panel). > 1 keeps the filter live;
# Bug-F5 ordering (filter before dedup) is unchanged.
MIN_EPISODE_APPEARANCES = 2


def _sanitize_source_label(label: str) -> str:
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
    # Lemmas force-included by the user whitelist (populated in _phase2_filter's
    # partition step). Read by the Reading path so its post-phase2 occurrence
    # floor does not re-drop force-included words. Empty on every other path.
    forced_include_lemmas: set[str] = field(default_factory=set)

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
        frequency_service: MultiFrequencyService | None = None,
        known_word_db: KnownWordDB | None = None,
        word_list_service: WordListService | None = None,
        wordset_service: WordsetService | None = None,
        stats_service: StatsService | None = None,
        youtube_fetcher: YouTubeFetcherService | None = None,
        expression_audio_fetcher: ExpressionAudioFetcher | None = None,
        dictionary_registry: DictionaryRegistry | None = None,
        sentence_audio_fetcher: SentenceAudioFetcher | None = None,
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
                schema-staleness backstop (``check_dictionary_staleness``). The
                service factory injects the same handle that built the provider
                chain; ``None`` (test construction / callers that skip the gate)
                disables the backstop.
            sentence_audio_fetcher: Optional sentence-TTS fetcher. Consulted
                ONLY by ``process_reading`` phase 3' (reading sources have no
                source audio); video/YouTube/audiobook paths never touch it.
                Gated by ``_reading_tts_active``. ``None`` is only valid for
                test construction; the service factory always provides a
                (possibly empty-chain) fetcher.
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
        """
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
            self.presenter.show_info(QCoreApplication.translate("EpisodeProcessor", "All words already in Anki!"))

    def _phase1_parse(
        self,
        ctx: _EpisodeContext,
        subtitle_file: Path,
        want_line_index: bool = False,
    ) -> tuple[list[TokenizedWord], list[LineLemmas] | None]:
        """Phase 1: parse subtitles into tokenized words (and optionally a line index).

        Returns the raw parse output; mutates ``ctx.total_words_found``. The
        line index is built when the i+1 filter needs it OR when a caller asks
        via ``want_line_index`` (interactive curation uses it to offer
        alternative example sentences per word).
        """
        self.presenter.show_info(
            tr_format(
                QCoreApplication.translate("EpisodeProcessor", "Step 1/5 — Parsing subtitles: %1"),
                subtitle_file.name,
            )
        )
        line_index: list[LineLemmas] | None = None
        if self.config.use_i_plus_one_filter or want_line_index:
            all_words, line_index = self.subtitle_parser.parse_subtitle_file_with_index(subtitle_file)
        else:
            all_words = self.subtitle_parser.parse_subtitle_file(subtitle_file)
        self.presenter.show_success(
            QCoreApplication.translate("EpisodeProcessor", "Found %n unique word(s)", "", len(all_words))
        )
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
        # Attach frequency data if available (mutates words in-place). Each word
        # gets the per-source breakdown (frequency_sources) for the card display,
        # the min rank (frequency_rank) that drives the top-N filter, and the
        # harmonic-mean rank (frequency_harmonic_rank) that drives the sort field.
        if self.frequency_service and self.frequency_service.is_available():
            for word in all_words:
                # Keyed on mined_form (the card-front spelling), NOT lemma:
                # unidic's canonical lemma collapses kanji variants
                # (懸ける/賭ける/架ける → 掛ける), so lemma-keyed lookups gave
                # every variant the common spelling's rank. Per-spelling sources
                # (JPDB) carry distinct rows per orthography — query the spelling
                # the card actually shows. Reading-scope so homographs stop
                # inheriting each other's ranks; hiragana-normalize so a katakana
                # subtitle reading matches a hiragana-stored frequency reading.
                reading = katakana_to_hiragana(word.expression_reading or word.lemma_reading or word.reading)
                # One per-source fetch, then derive min + harmonic locally via
                # the pure min_rank/harmonic_rank helpers. A single lookup_all
                # feeds both scalars, so the per-source SQL runs once per word
                # instead of once for each derived rank.
                sources = self.frequency_service.lookup_all(word.mined_form, reading)
                # Whole-result miss-only lemma fallback (mirrors the JPod101
                # audio retry ladder): fires only when NO source attests the
                # variant spelling, so a ranked breakdown is always uniformly
                # keyed — all spelling-true or all lemma. Deliberately NOT
                # per-source: a per-source cascade would re-inject the lemma
                # rank from any source lacking the per-spelling row, and since
                # frequency_rank = min_rank(sources) gates the top-N filter,
                # that low lemma rank would keep a rare variant above the
                # max_frequency_rank cutoff it should now fall past. Known
                # edge: a variant attested ONLY by a categorical source (JLPT
                # band, CATEGORICAL_RANK sentinel) counts as attested and
                # suppresses the numeric lemma fallback — accepted for
                # breakdown uniformity; unreachable for per-spelling numeric
                # sources.
                if not sources and word.lemma and word.lemma != word.mined_form:
                    sources = self.frequency_service.lookup_all(
                        word.lemma, katakana_to_hiragana(word.lemma_reading or word.reading)
                    )
                word.frequency_sources = sources
                word.frequency_rank = min_rank(sources)
                word.frequency_harmonic_rank = harmonic_rank(sources)
            ranked_count = sum(1 for w in all_words if w.frequency_rank is not None)
            self.presenter.show_info(
                tr_format(
                    QCoreApplication.translate("EpisodeProcessor", "Frequency data: %1/%2 words ranked"),
                    ranked_count,
                    len(all_words),
                )
            )

        # Filter against existing vocabulary.
        if self.config.include_known_words:
            # Deck Builder "include everything" mode: skip known-words subtraction
            # entirely — including the Issue #42 user ignore list — and mine all
            # words that passed POS/subtype filtering. Coverage-deck builds
            # intentionally re-card words the user already knows.
            self.presenter.show_info(
                QCoreApplication.translate(
                    "EpisodeProcessor", "Step 2/5 — Known-words filter bypassed (include everything mode)"
                )
            )
            unknown_words = all_words
        else:
            self.presenter.show_info(
                QCoreApplication.translate("EpisodeProcessor", "Step 2/5 — Filtering against known vocabulary")
            )
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
                        "Could not read the user ignore list from known_words.db (%s); "
                        "proceeding without it this run.",
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

        # Whitelist force-include (partition-then-merge). A whitelisted lemma is
        # a true force-include: it bypasses every optional COVERAGE filter below
        # (frequency, blacklist, script-type, name-wordsets, dedup,
        # cross-episode-count, i+1, sentence-length). We split it out here and
        # merge it back just before the integrity gates (offline-def existence
        # and within-run duplicate collapse), which it stays subject to.
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
            ctx.forced_include_lemmas = {w.lemma for w in forced_include}

        # Frequency rank cutoff. Gate on an actually-loaded NUMERIC frequency
        # source — NOT just max_frequency_rank > 0, and NOT is_available(). With
        # no source (or only a categorical one, e.g. a JLPT-band dict whose rows
        # all carry CATEGORICAL_RANK), no word gets a numeric rank, so every word
        # keeps frequency_rank=None and filter_by_frequency drops every None-ranked
        # word (word_filter.py) — a configured cutoff would then silently wipe 100%
        # of words and produce zero cards. has_numeric_source() is True only when a
        # non-categorical source is loaded, which is the sole case the cutoff can
        # meaningfully apply.
        if (
            self.config.max_frequency_rank > 0
            and self.frequency_service
            and self.frequency_service.has_numeric_source()
            and not self.config.bypass_optional_filters
        ):
            before = len(unknown_words)
            unknown_words = self.word_filter.filter_by_frequency(unknown_words, self.config.max_frequency_rank)
            filtered_out = before - len(unknown_words)
            if filtered_out > 0:
                self.presenter.show_info(
                    tr_format(
                        QCoreApplication.translate(
                            "EpisodeProcessor", "Frequency filter: removed %1 words outside top %2"
                        ),
                        filtered_out,
                        self.config.max_frequency_rank,
                    )
                )
        elif self.config.max_frequency_rank > 0 and not self.config.bypass_optional_filters:
            # Cutoff configured but no frequency source is loaded: skip it (it
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
            if filtered_out > 0:
                self.presenter.show_info(
                    tr_format(
                        QCoreApplication.translate("EpisodeProcessor", "Name wordset filter: removed %1 words"),
                        filtered_out,
                    )
                )

        # Cross-episode frequency filter. Runs BEFORE sentence dedup: dedup keeps
        # the first word per sentence, so if a below-floor word sorts ahead of a
        # sentence-mate that would pass the floor, dedup-first would keep the loser
        # and the floor would then drop it — the sentence yields no card even though
        # its mate qualified (Bug F5). Filtering by episode count first removes the
        # losers so dedup picks a survivor.
        if cross_episode_counts is not None and MIN_EPISODE_APPEARANCES > 1 and not self.config.bypass_optional_filters:
            before = len(unknown_words)
            unknown_words = self.word_filter.filter_by_episode_count(
                unknown_words,
                cross_episode_counts,
                MIN_EPISODE_APPEARANCES,
            )
            filtered_out = before - len(unknown_words)
            if filtered_out > 0:
                self.presenter.show_info(
                    tr_format(
                        QCoreApplication.translate(
                            "EpisodeProcessor",
                            "Cross-episode filter: removed %1 words appearing in fewer than %2 episodes",
                        ),
                        filtered_out,
                        MIN_EPISODE_APPEARANCES,
                    )
                )

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

        # Merge force-included whitelist words back in before the integrity
        # gates. Prepend so a forced word wins its mined_form slot in the
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

        # Offline definition existence filter. Drops words with no entry in any
        # OFFLINE dictionary so the curation dialog never surfaces words that
        # can never become cards (they would otherwise be silently skipped at
        # Phase 5). Offline-only by design: matches the curator's no-network
        # def-pane and the project's offline-first default (Jisho is off by
        # default). Probes the union of mined_form + lemma per word — the same
        # two keys Phase 4 can resolve (mined_form primary, lemma miss-only
        # fallback) — and keeps a word when either hits. Gated on
        # bypass_optional_filters so the Deck Builder preview-parity path is
        # unaffected (Phase 5 stays the skip point there).
        #
        # Known, intentional asymmetry: this probe is offline-only, but Phase 5
        # looks definitions up over the FULL chain (get_definitions_batch, which
        # includes Jisho when enabled). A user who turns Jisho on therefore has
        # words with a Jisho-only definition dropped here before the curator —
        # accepted on purpose so Phase 2 never blocks on network I/O. Do not
        # "fix" this by calling online providers here. (The probe also doesn't
        # mirror Phase 4's kana-fold/deinflection miss fallback — pre-existing
        # accepted asymmetry.)
        if not self.config.bypass_optional_filters and unknown_words:
            probe_terms = list({t for w in unknown_words for t in (w.mined_form, w.lemma) if t})
            has_def = self.definition_service.has_offline_definitions(probe_terms)
            kept_words = [w for w in unknown_words if has_def.get(w.mined_form) or has_def.get(w.lemma)]
            dropped = [w.mined_form for w in unknown_words if not (has_def.get(w.mined_form) or has_def.get(w.lemma))]
            unknown_words = kept_words
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

        # Within-run duplicate collapse. Two distinct surfaces/lemmas can resolve
        # to the same mined_form in one episode (e.g. a verb's lemma and another
        # token's surface coincide). Anki dedups on the Expression first field,
        # which IS mined_form, so it silently skips the second as a duplicate
        # (anki_service.last_skipped_duplicates, warned at Phase 5). filter_unknown
        # already removes mined_forms that exist as cards in Anki; this collapses
        # the WITHIN-RUN collisions it can't see, so the curator never offers a
        # word Anki will drop. Keep the first occurrence (stable order).
        #
        # Gated on allow_duplicate_cards: the Deck Builder sets it True (and
        # bypass_optional_filters True) to intentionally re-card duplicates, in
        # which case Anki creates both and showing both is correct — collapsing
        # there would diverge from its raw-lemma preview parity.
        if not self.config.allow_duplicate_cards and unknown_words:
            seen: set[str] = set()
            collapsed: list[TokenizedWord] = []
            for word in unknown_words:
                if word.mined_form in seen:
                    continue
                seen.add(word.mined_form)
                collapsed.append(word)
            removed = len(unknown_words) - len(collapsed)
            unknown_words = collapsed
            if removed:
                self.presenter.show_info(
                    tr_format(
                        QCoreApplication.translate("EpisodeProcessor", "Collapsed %1 duplicate-expression word(s)"),
                        removed,
                    )
                )

        # Record difficulty data if stats service available.
        # OVH-024: use the pre-filter comprehension-unknown count (all_unknown_lemmas),
        # NOT the post-filter mineable count (unknown_words). difficulty_score measures
        # how hard the episode is to comprehend; i+1/frequency filters can collapse
        # unknown_words to a handful, making a hard episode appear near-zero difficulty.
        #
        # A locked stats.db (Anki or a parallel run) raises OperationalError here.
        # Do NOT let it bubble into process_episode's generic except — that would
        # report cards_created=0 with no note IDs, turning a successful run into an
        # apparent failure. Dropping one difficulty row is safe; warn and continue.
        if self.stats_service and self.stats_service.is_available():
            try:
                self.stats_service.record_difficulty(
                    series_name=ctx.series_name,
                    episode_name=ctx.episode_name,
                    total_words=len(all_words),
                    unknown_words=len(all_unknown_lemmas),
                    unique_words=len(all_words),
                )
            except (sqlite3.Error, OSError) as e:
                logger.warning(
                    "Could not record difficulty for %s in stats.db (%s); " "the run will continue.",
                    ctx.episode_name,
                    e,
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
        """Phase 3: extract media (screenshots + audio; audio + cover art when
        ``audio_only``) for each unknown word."""
        self.presenter.show_info(
            QCoreApplication.translate("EpisodeProcessor", "Step 3/5 — Extracting media from video")
        )

        # Resolve the animated screenshot format once and announce any fallback
        # in the Activity Log, then thread the same value into the batch so the
        # warning and the encode can never disagree. Only relevant when animated
        # screenshots are configured and we are not in audiobook (audio_only)
        # mode, where screenshots are skipped entirely; otherwise the batch's
        # own default resolves to the static path.
        extra_kwargs: dict[str, str | None] = {}
        if self.config.screenshot_animated and not audio_only:
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

        media_results: list[tuple[TokenizedWord, MediaData]] = self.media_extractor.extract_media_batch(
            video_file,
            unknown_words,
            progress_callback,
            cancelled_check=lambda: self.cancelled,
            temp_folder=run_temp_folder,
            audio_track_override=audio_track_override,
            audio_only=audio_only,
            **extra_kwargs,
        )

        self._audio_stage.fetch_expression_audio(media_results, progress_callback)

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
        self.presenter.show_info(QCoreApplication.translate("EpisodeProcessor", "Step 4/5 — Fetching definitions"))
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
        # Lookup-miss fallback context (5.2): mined_form → (lemma, cType). Only
        # consulted for keys the whole chain misses, so a dictionary storing
        # only the canonical lemma spelling (請う when the source wrote 乞う)
        # still resolves. Set unconditionally: when lemma == mined_form the
        # candidate builder skips the equal alternate but still emits the
        # kana-fold + deinflection miss fallbacks. cType is unavailable on
        # TokenizedWord post-parse, so the deinflection mask stays inert here
        # and the rules-column POS check does the gating. First-seen lemma
        # wins, mirroring the batch's dedup.
        fallback_context: dict[str, tuple[str, str | None]] = {}
        for w in words_with_media:
            fallback_context.setdefault(w.mined_form, (w.lemma, None))
        definitions = self.definition_service.get_definitions_batch(
            lookup_pairs,
            progress_callback,
            fallback_context,
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
            )
            # get_glossaries_batch has no miss-fallback mechanism, so variant
            # spellings absent from every dictionary retry once under the
            # canonical lemma (miss-only, merge by index). Non-variant words
            # and hits pay nothing; None progress avoids a second cycle.
            retry_idx = [
                i
                for i, g in enumerate(glossaries)
                if not g and words_with_media[i].lemma != words_with_media[i].mined_form
            ]
            if retry_idx:
                retry_pairs: list[tuple[str, str | None]] = [
                    (
                        words_with_media[i].lemma,
                        katakana_to_hiragana(words_with_media[i].lemma_reading or words_with_media[i].reading),
                    )
                    for i in retry_idx
                ]
                retry_glossaries = self.definition_service.get_glossaries_batch(retry_pairs, None)
                for i, g in zip(retry_idx, retry_glossaries, strict=True):
                    glossaries[i] = g

        # Pitch accents if available. Deliberately still lemma-keyed (unlike
        # the mined_form-keyed definition/frequency lookups above): pitch is a
        # property of (accent word, reading), kanji variants of one lemma share
        # the reading, and the canonical lemma orthography has the better hit
        # rate in reading-scoped pitch CSVs. Re-keying buys nothing and risks
        # misses.
        pitch_data: list[tuple[str | None, str | None]] = [(None, None)] * len(words_with_media)
        if self.pitch_accent_service and self.pitch_accent_service.is_available():
            pitch_data = self.pitch_accent_service.lookup_batch_detailed(
                [(w.lemma, w.lemma_reading or w.reading, w.pos) for w in words_with_media],
                fmt=self.config.pitch_category_format,
            )
            found_count = sum(1 for pos, _ in pitch_data if pos)
            self.presenter.show_info(
                tr_format(
                    QCoreApplication.translate("EpisodeProcessor", "Pitch accent data: %1/%2 words"),
                    found_count,
                    len(words_with_media),
                )
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
        self.presenter.show_info(QCoreApplication.translate("EpisodeProcessor", "Step 5/5 — Creating Anki cards"))
        card_data: list[CardPayload] = []
        # Self-contained per-card glossary styling: collect the dictionary CSS
        # ONCE per episode (collect_dictionary_css does registry + per-dict
        # SQLite I/O) but assemble the <style> block PER CARD inside the loop —
        # the base sheet is tree-shaken against each card's own HTML (Issue
        # #93; witness/variant scans are cheap cached string work; freshly
        # rendered bodies are born stamped, so witnesses are already
        # post-stamp). Built when EITHER the glossary OR the definition field
        # is mapped — an Anki <style> in any field is card-wide, so the block
        # rides the glossary field when it's mapped and otherwise prepends to
        # the definition field. That way the base sheet (dark-theme SVG
        # recolor, tag chips, structured-content layout) reaches default-config
        # cards too, which map definition="MainDefinition" but leave glossary
        # unmapped. Skipping the collect only when neither is mapped keeps the
        # no-styling path I/O-free.
        glossary_mapped = bool(self.config.anki_fields.get("glossary"))
        definition_mapped = bool(self.config.anki_fields.get("definition"))
        styling_on = glossary_mapped or definition_mapped
        episode_dict_css = collect_dictionary_css(self.config) if styling_on else ""
        for (word, media), definition, glossary, (pitch_position, pitch_category) in zip(
            media_results, definitions, glossaries, pitch_data, strict=True
        ):
            if not definition:
                continue
            # Bound unconditionally ("") so the write-site references are safe.
            style_block = ""
            if styling_on:
                style_block = build_card_style_block(dict_css=episode_dict_css, card_html=(glossary or "") + definition)

            extra_fields: dict[str, str] = {}
            if pitch_position:
                extra_fields["pitch_position"] = pitch_position
                # Inline pitch graph / overline (6.3): rendered self-contained
                # SVG/HTML, gated on the field being mapped so the default config
                # stays byte-identical. Uses the SAME reading the pitch lookup
                # used (lemma_reading or reading) for the morae, and the entry's
                # per-mora nasal/devoice positions. One extra dict lookup only for
                # a pitched word with the field mapped (both off by default).
                want_graph = bool(self.config.anki_fields.get("pitch_graph"))
                want_text = bool(self.config.anki_fields.get("pitch_text"))
                if (want_graph or want_text) and self.pitch_accent_service:
                    reading = word.lemma_reading or word.reading
                    entry = self.pitch_accent_service.lookup_entry(word.lemma, reading)
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
                extra_fields["glossary"] = (style_block + glossary) if style_block else glossary
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

            # When the glossary field isn't the styling carrier (unmapped), prepend
            # the style block to the definition field so the card still carries the
            # base sheet. When glossary IS mapped it already rides the glossary
            # field above (a card-wide <style> only needs to appear once), so the
            # definition stays untouched — keeping glossary-mapped output identical.
            card_definition = definition
            if style_block and not glossary_mapped:
                card_definition = style_block + definition

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

        cards_created = self.anki_service.create_cards_batch(card_data, progress_callback)
        created_note_ids = list(self.anki_service.last_created_note_ids)

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

        # Collect mined_forms from the cards that were actually submitted.
        # Stored as mined_form (POS-aware) to match what Anki records in the
        # Expression field (Issue #5). Returned to the caller so process_episode
        # can stamp ProcessingResult.mined_forms for the Undo path (OVH-030).
        mined_words: set[str] = {payload.word.mined_form for payload in card_data}

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
        # Undo must revert only the 'mined' rows THIS session inserted, never a
        # 'mined' row a prior session created that this run merely re-encountered
        # (Anki-duplicate-skipped). Snapshot the existing 'mined' lemmas BEFORE
        # the insert and report only the genuinely-new ones for the Undo path.
        mined_forms_for_undo = sorted(mined_words)
        if self.known_word_db and self.known_word_db.is_available() and card_data:
            try:
                already_mined = self.known_word_db.get_words_by_source("mined")
                mined_forms_for_undo = sorted(mined_words - already_mined)
                self.known_word_db.add_words(mined_words, source="mined")
            except (sqlite3.Error, OSError) as e:
                logger.warning(
                    "Could not record %d mined words in known_words.db (%s); "
                    "the cards were still created. The cache will re-sync next run.",
                    len(mined_words),
                    e,
                )

        return cards_created, created_note_ids, mined_forms_for_undo

    def _run_pipeline(
        self,
        ctx: _EpisodeContext,
        cancel_event: threading.Event | None,
        body: Callable[[Path], ProcessingResult],
    ) -> ProcessingResult:
        """Shared run skeleton for :meth:`process_episode` / :meth:`process_reading`.

        Owns ONLY the machinery both entry points share verbatim: the pre-flight
        gates (staleness backstop then card-target verify, both *outside* the
        try so a ``SetupError`` propagates instead of collapsing into a
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
        self.check_dictionary_staleness()
        self._preflight_card_target()
        run_temp_folder = self._allocate_run_temp_folder()
        keep_temp = bool(os.environ.get("ANKI_MINER_KEEP_TEMP"))

        # Reset the partial-IDs accumulator before this run so that if it fails
        # mid-batch the except handlers harvest ONLY IDs created during THIS run,
        # not stale IDs left over from a prior run on the same processor instance
        # (OVH-008). create_cards_batch resets it again at its own start — this
        # guard is belt-and-suspenders for a failure before phase 5 even runs.
        self.anki_service.last_created_note_ids = []

        # Bridge the caller's cancel_event into this run's cancellation
        # checkpoints for the duration of this call only: the phase checkpoints
        # and the media extractor's cancelled_check consult self.cancelled, which
        # folds this source in. See __init__ for why the sticky self._cancelled
        # flag must NOT be used here (shared processor reuse across runs); the
        # finally below drops the reference so the bridge is per-run by construction.
        if cancel_event is not None:
            self._external_cancel = cancel_event.is_set
        try:
            return body(run_temp_folder)
        except AnkiMinerException as e:
            ctx.errors.append(str(e))
            partial_ids = list(self.anki_service.last_created_note_ids)
            self.presenter.show_error(tr_format(QCoreApplication.translate("EpisodeProcessor", "Error: %1"), str(e)))
            return self._partial_failure_result(ctx, partial_ids)
        except Exception as e:
            logger.exception("EpisodeProcessor unhandled exception")
            ctx.errors.append(f"Unexpected error: {e}")
            partial_ids = list(self.anki_service.last_created_note_ids)
            self.presenter.show_error(
                tr_format(QCoreApplication.translate("EpisodeProcessor", "Unexpected error: %1"), str(e))
            )
            return self._partial_failure_result(ctx, partial_ids)
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
        curated = curation_callback(unknown_words)
        if curated is None:
            # The user cancelled/rejected the curation dialog.
            return self._cancelled_result_from_ctx(ctx)
        ctx.new_words_found = len(curated)
        if not curated:
            self.presenter.show_info(
                QCoreApplication.translate("EpisodeProcessor", "No words selected for card creation")
            )
            return ctx.build_result(new_words_found=0)
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
        cross_episode_counts: dict[str, int] | None = None,
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
            cross_episode_counts: Optional cross-episode word frequency counts.
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
            source_label=source_label_override or _sanitize_source_label(f"{series_name} — {episode_name}"),
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
            all_words, line_index = self._phase1_parse(ctx, subtitle_file, want_line_index=want_line_index)
            if not all_words:
                self.presenter.show_warning(
                    QCoreApplication.translate("EpisodeProcessor", "No words found in subtitles")
                )
                return ctx.build_result()
            if self.cancelled:
                return self._cancelled_result_from_ctx(ctx)

            unknown_words = self._phase2_filter(ctx, all_words, line_index, cross_episode_counts)
            if not unknown_words:
                self._report_no_mineable_words(ctx)
                return ctx.build_result(new_words_found=0)
            if self.cancelled:
                return self._cancelled_result_from_ctx(ctx)

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

            # Wrap the raw callback so the bar reflects whole-episode progress
            # instead of resetting 0->100 per stage. One weight per stage that
            # reports progress, in firing order: extract, definitions,
            # [glossaries if mapped], cards.
            stage_progress = progress_callback
            if progress_callback is not None:
                # StageWeightedProgress normalizes these internally, so the
                # individual values only express relative weight — sums need
                # not equal 1.0.
                stage_weights = [0.40]  # extract
                if self._expression_audio_active:
                    stage_weights.append(0.10)  # expression audio (right after extract)
                stage_weights.append(0.25)  # definitions
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
                self.presenter.show_warning(
                    QCoreApplication.translate("EpisodeProcessor", "No media extracted successfully")
                )
                return ctx.build_result(errors=["Media extraction failed for all words"])
            self.presenter.show_success(
                QCoreApplication.translate("EpisodeProcessor", "Extracted media for %n word(s)", "", len(media_results))
            )

            definitions, glossaries, pitch_data = self._phase4_lookup(ctx, media_results, stage_progress)
            if self.cancelled:
                return self._cancelled_result_from_ctx(ctx)

            cards_created, created_note_ids, mined_forms = self._phase5_create(
                ctx, media_results, definitions, glossaries, pitch_data, stage_progress
            )
            if isinstance(stage_progress, StageWeightedProgress):
                stage_progress.finish()
            result = ctx.build_result(
                cards_created=cards_created,
                card_ids=created_note_ids,
                mined_forms=mined_forms,
            )
            self._record_session(ctx, result)
            return result

        return self._run_pipeline(ctx, cancel_event, _body)

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
            total_words_found=0,
            new_words_found=0,
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
        # none) — so the image-stage wording differs. The three emissions below
        # stay strictly UNCONDITIONAL (band accounting must not depend on kind);
        # only the text varies. Derived once here, used at the three sites.
        is_book = document.kind in ("book", "subtitle")
        step_banner = (
            QCoreApplication.translate("EpisodeProcessor", "Step 3/5 — Preparing card images")
            if is_book
            else QCoreApplication.translate("EpisodeProcessor", "Step 3/5 — Preparing page images")
        )
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
        self.presenter.show_info(step_banner)
        images_dir = run_temp_folder / "images"
        units_by_index = {unit.index: unit for unit in document.units}

        # YOU own the per-run bookkeeping: a unique-ref → materialized-path memo,
        # a set of archives whose safety gate failed or that are corrupt (skip
        # their remaining refs, warn once each), and a set of individual refs
        # whose page failed to decode (skip re-attempt, warn once each even when
        # the page/cover is shared by many words).
        ref_cache: dict[ImageRef, Path] = {}
        failed_archives: set[Path] = set()
        failed_refs: set[ImageRef] = set()

        media_results: list[tuple[TokenizedWord, MediaData]] = []

        # The image band is consumed UNCONDITIONALLY (on_start / per-ref
        # on_progress / on_complete) — even for text-only volumes with zero
        # image refs — so StageWeightedProgress does not advance into the next
        # band's weight on an imageless run (same discipline as expression audio).
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
            if ref is not None and ref.source not in failed_archives and ref not in failed_refs:
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
                    except (OSError, zipfile.BadZipFile) as exc:
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
            SetupError: note type / field mapping misconfigured, or a stale dict
                index needs reimport.
            AnkiConnectionError: AnkiConnect is unreachable.
        """
        # Manga and subtitle sources carry a meaningful series (mokuro title /
        # parent folder), so prefix it; books use the bare episode title.
        if document.kind in ("manga", "subtitle"):
            source_label = _sanitize_source_label(f"{document.series} — {document.episode}")
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
            self.presenter.show_info(
                tr_format(
                    QCoreApplication.translate("EpisodeProcessor", "Step 1/5 — Parsing text: %1"),
                    document.title,
                )
            )
            all_words, line_index, counts = self.subtitle_parser.parse_text_units(document.units, want_line_index)
            self.presenter.show_success(
                QCoreApplication.translate("EpisodeProcessor", "Found %n unique word(s)", "", len(all_words))
            )
            ctx.total_words_found = len(all_words)
            if not all_words:
                self.presenter.show_warning(
                    QCoreApplication.translate("EpisodeProcessor", "No words found in subtitles")
                )
                return ctx.build_result()
            if self.cancelled:
                return self._cancelled_result_from_ctx(ctx)

            unknown_words = self._phase2_filter(ctx, all_words, line_index, None)
            # Reading-specific in-document occurrence floor (reuses the
            # cross-episode filter's <=1 early-return). counts is the parse
            # Counter — replaces the episode path's count_lemmas(subtitle_file).
            # Force-included whitelist words bypass this floor too (it is a
            # coverage filter applied outside _phase2_filter): floor only the
            # non-forced remainder, then re-prepend. A no-op when nothing was
            # force-included, so the default reading config is unchanged.
            forced = [w for w in unknown_words if w.lemma in ctx.forced_include_lemmas]
            rest = [w for w in unknown_words if w.lemma not in ctx.forced_include_lemmas]
            rest = self.word_filter.filter_by_episode_count(rest, counts, self.config.reading_min_occurrence)
            unknown_words = forced + rest
            ctx.new_words_found = len(unknown_words)
            if not unknown_words:
                self._report_no_mineable_words(ctx)
                return ctx.build_result(new_words_found=0)
            if self.cancelled:
                return self._cancelled_result_from_ctx(ctx)

            if curation_callback is not None:
                outcome = self._run_curation(ctx, unknown_words, line_index, counts, curation_callback)
                if isinstance(outcome, ProcessingResult):
                    return outcome
                unknown_words = outcome

            # Wrap only phases 3'/4/5 in one weighted sweep (no parse/filter
            # bands). Bands, in firing order: image prep, [expression audio],
            # [sentence TTS], definitions, [glossaries], cards — renormalized
            # internally.
            stage_progress = progress_callback
            if progress_callback is not None:
                stage_weights = [0.40]  # image prep
                if self._expression_audio_active:
                    stage_weights.append(0.10)  # expression audio
                if self._reading_tts_active:
                    stage_weights.append(0.10)  # sentence TTS
                stage_weights.append(0.25)  # definitions
                if self.config.anki_fields.get("glossary"):
                    stage_weights.append(0.10)  # glossaries
                stage_weights.append(0.25)  # cards
                stage_progress = StageWeightedProgress(progress_callback, stage_weights)

            media_results = self._phase3_reading_media(ctx, document, unknown_words, stage_progress, run_temp_folder)
            if self.cancelled:
                return self._cancelled_result_from_ctx(ctx)

            definitions, glossaries, pitch_data = self._phase4_lookup(ctx, media_results, stage_progress)
            if self.cancelled:
                return self._cancelled_result_from_ctx(ctx)

            cards_created, created_note_ids, mined_forms = self._phase5_create(
                ctx, media_results, definitions, glossaries, pitch_data, stage_progress
            )
            if isinstance(stage_progress, StageWeightedProgress):
                stage_progress.finish()
            result = ctx.build_result(
                cards_created=cards_created,
                card_ids=created_note_ids,
                mined_forms=mined_forms,
            )
            self._record_session(ctx, result)
            return result

        return self._run_pipeline(ctx, cancel_event, _body)

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
        except (sqlite3.Error, OSError) as e:
            logger.warning(
                "Could not record mining session for %s in stats.db (%s); " "the cards were still created.",
                ctx.episode_name,
                e,
            )

    def _preflight_card_target(self) -> None:
        """Fail fast on a misconfigured Anki target; auto-create the deck (Issue #52)."""
        self.anki_service.verify_card_target()

    def check_dictionary_staleness(self) -> None:
        """Raise SetupError if any enabled indexed dict slot needs reimport (4.0).

        The single-episode backstop for the schema-bump migration gate:
        consults the injected registry's per-slot ``DictMeta.schema_ok`` (NOT the
        built provider chain, which silently drops stale slots) so a user who
        upgraded and mines before reimporting gets one actionable error instead
        of a silent zero-card run. Queue workers front-run this with their own
        pre-loop check so a batch aborts once rather than per item; this covers
        the direct single-episode callers (episode / manual-pair / deck-builder).
        No-op when no registry was injected.
        """
        if self._dictionary_registry is None:
            return
        stale = self._dictionary_registry.stale_enabled(self.config)
        if stale:
            from anki_miner.services.dictionary.registry import format_stale_reimport_message

            raise SetupError(format_stale_reimport_message(stale))

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

        # Deliberate early check: fail before the video download rather than
        # after.  process_episode re-runs the same pre-flight post-fetch;
        # that double-check is intentional — cheap idempotent localhost calls.
        # The staleness backstop is likewise cheap and fails before the
        # download when an enabled index needs reimport.
        self.check_dictionary_staleness()
        self._preflight_card_target()

        # The fetch stage consults cancel_event directly (fetch_video gets it
        # verbatim and the post-fetch check below polls it); the mining stage
        # gets it via process_episode's cancel_event keyword, which installs
        # and removes the per-run self._external_cancel bridge itself.
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
            progress_callback=progress_callback,
            curation_callback=curation_callback,
            episode_name_override=f"YT:{video_id}",
            series_name_override="YouTube",
            source_label_override=source_label,
            cancel_event=cancel_event,
        )
