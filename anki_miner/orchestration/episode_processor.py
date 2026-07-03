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
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PyQt6.QtCore import QCoreApplication

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
from anki_miner.services.frequency.render import render_frequency_html
from anki_miner.utils import ensure_directory, has_katakana, hiragana_to_katakana
from anki_miner.utils.i18n import tr_format

logger = logging.getLogger(__name__)


if TYPE_CHECKING:
    from anki_miner.interfaces.expression_audio import ExpressionAudioFetcher
    from anki_miner.models import LineLemmas
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


def _expression_audio_candidates(word: TokenizedWord) -> list[tuple[str, str]]:
    """Ordered ``(kanji, kana)`` query pairs for the JPod101 audio retry ladder.

    Two failure modes the single-shot query missed:

    * **Katakana loanwords.** JPod101 indexes loanword audio under the katakana
      reading, but ``expression_reading`` is folded to hiragana for card
      display (チップ→ちっぷ → miss).  Each query whose kanji form contains
      katakana gets a katakana-reading variant (チップ→チップ → hit).
    * **Surface-mined fallback.** Subtitle surface forms use variant kanji
      (噓/頰/今さら) that JPod101 lacks; the unidic lemma is the canonical
      orthography (嘘/頬/今更).  Surface-mined words fall back to the lemma with
      the lemma's OWN reading (探す/さがす, not the surface 探す/さがし).

    hiragana↔katakana is lossless and loanwords are unambiguous, so the katakana
    variant carries no homograph risk (Issue #73).  Empty readings are dropped
    (homograph guard) and duplicates are collapsed, so a verb whose
    ``mined_form == lemma`` issues no redundant request.
    """
    pairs: list[tuple[str, str]] = [(word.mined_form, word.expression_reading)]
    if word.lemma and word.lemma != word.mined_form:
        pairs.append((word.lemma, word.lemma_reading))

    candidates: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def _add(kanji: str, kana: str) -> None:
        if not kanji or not kana:
            return
        pair = (kanji, kana)
        if pair not in seen:
            seen.add(pair)
            candidates.append(pair)

    for kanji, kana in pairs:
        _add(kanji, kana)
        if has_katakana(kanji):
            _add(kanji, hiragana_to_katakana(kana))
    return candidates


def _audio_failure_diagnosis(counts: dict[str, int], attempts: int) -> str | None:
    """Name the dominant expression-audio failure cause, or None.

    ``counts`` is a ChainedExpressionAudioFetcher ``stats()`` tally keyed by
    failure bucket (ssl/connection/timeout/http_status/non_audio). Only surfaces
    a diagnosis when transient failures DOMINATE the run — a genuine "word not in
    JPod101" miss is never counted, so a high total means something systemic
    (expired certificate, outage, rate-limit) rather than words simply being
    absent. Scattered failures among mostly-successful fetches stay quiet.

    Ties resolve to the earliest bucket (ssl first) via ``max`` over a stable
    key order, matching Yomitan's priority on the most actionable cause.
    """
    total = sum(counts.values())
    if attempts <= 0 or total == 0:
        return None
    # Require failures to cover at least half the attempted words before raising
    # the alarm; below that they are noise beside real hits and misses.
    if total * 2 < attempts:
        return None
    dominant = max(counts, key=lambda key: counts[key])
    if dominant in ("ssl", "connection", "timeout"):
        return QCoreApplication.translate(
            "EpisodeProcessor",
            "JPod101 certificate/connection failure — audio skipped this run, will retry next run",
        )
    if dominant == "http_status":
        return QCoreApplication.translate(
            "EpisodeProcessor",
            "JPod101 returned repeated server errors — audio skipped this run, will retry next run",
        )
    return QCoreApplication.translate(
        "EpisodeProcessor",
        "JPod101 returned non-audio responses (likely rate-limited) — audio skipped this run, will retry next run",
    )


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
        frequency_service: MultiFrequencyService | None = None,
        known_word_db: KnownWordDB | None = None,
        word_list_service: WordListService | None = None,
        wordset_service: WordsetService | None = None,
        stats_service: StatsService | None = None,
        youtube_fetcher: YouTubeFetcherService | None = None,
        expression_audio_fetcher: ExpressionAudioFetcher | None = None,
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
        """True when the expression-audio stage should run and occupy a progress band.

        The two-part gate (Issue #73, simplified): fetcher injected AND the
        expression_audio Anki field mapped (non-empty). The field name is the
        sole on/off switch, matching the frequency/pitch optional fields — no
        dedicated enable flag. Checked in two places — ``process_episode``
        (band registration) and ``_phase3_extract`` (band consumption) — via
        this property so the conditions can't drift apart.
        """
        return self.expression_audio_fetcher is not None and bool(self.config.anki_fields.get("expression_audio"))

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
                word.frequency_sources = self.frequency_service.lookup_all(word.lemma)
                word.frequency_rank = self.frequency_service.lookup_min(word.lemma)
                word.frequency_harmonic_rank = self.frequency_service.lookup_harmonic(word.lemma)
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
            else:
                known_words = self.anki_service.get_existing_vocabulary()

            unknown_words = self.word_filter.filter_unknown(all_words, known_words | user_words)
        self.presenter.show_success(
            QCoreApplication.translate("EpisodeProcessor", "%n new word(s) to mine", "", len(unknown_words))
        )

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
                    "All %n word(s) from this subtitle are already in your Anki collection"
                    " — no new cards will be created. Card-format options (bold target word,"
                    " etc.) only apply to newly mined cards.",
                    "",
                    len(all_words),
                )
            )

        # Issue #74: snapshot the full unknown-lemma set before optional
        # filters (frequency, word-list, script-type, wordset) shrink it.
        # The i+1 check must see ALL words the learner doesn't know, not
        # just the mineable ones.
        all_unknown_lemmas = {w.lemma for w in unknown_words}

        # Frequency rank cutoff.
        if self.config.max_frequency_rank > 0 and not self.config.bypass_optional_filters:
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
        # mistagged them. Whitelist still rescues. Gated like neighbors so the
        # Deck Builder corpus preview (bypass_optional_filters) stays in parity.
        if self.wordset_service and self.wordset_service.is_available() and not self.config.bypass_optional_filters:
            before = len(unknown_words)
            unknown_words = self.word_filter.filter_by_wordsets(
                unknown_words, self.wordset_service, self.word_list_service
            )
            filtered_out = before - len(unknown_words)
            if filtered_out > 0:
                self.presenter.show_info(
                    tr_format(
                        QCoreApplication.translate("EpisodeProcessor", "Name wordset filter: removed %1 words"),
                        filtered_out,
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
                    tr_format(
                        QCoreApplication.translate(
                            "EpisodeProcessor",
                            "Cross-episode filter: removed %1 words appearing in fewer than %2 episodes",
                        ),
                        filtered_out,
                        self.config.min_episode_appearances,
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

        # Offline definition existence filter. Drops words whose lemma has no
        # entry in any OFFLINE dictionary so the curation dialog never surfaces
        # words that can never become cards (they would otherwise be silently
        # skipped at Phase 5). Offline-only by design: matches the curator's
        # no-network def-pane and the project's offline-first default (Jisho is
        # off by default). Keyed on word.lemma, the same key Phase 4 looks up.
        # Gated on bypass_optional_filters so the Deck Builder preview-parity
        # path is unaffected (Phase 5 stays the skip point there).
        #
        # Known, intentional asymmetry: this probe is offline-only, but Phase 5
        # looks definitions up over the FULL chain (get_definitions_batch, which
        # includes Jisho when enabled). A user who turns Jisho on therefore has
        # words with a Jisho-only definition dropped here before the curator —
        # accepted on purpose so Phase 2 never blocks on network I/O. Do not
        # "fix" this by calling online providers here.
        if not self.config.bypass_optional_filters and unknown_words:
            has_def = self.definition_service.has_offline_definitions([w.lemma for w in unknown_words])
            kept_words = [w for w in unknown_words if has_def.get(w.lemma)]
            dropped = [w.lemma for w in unknown_words if not has_def.get(w.lemma)]
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

        # Expression (pronunciation) audio, Issue #73. Sequential on purpose:
        # the fetcher rate-limits and caches internally and never raises, so
        # the loop needs no try/except, no sleep, and no parallelism. Gated on
        # the toggle AND a mapped field — fetching audio no card would use is
        # wasted network. Cancellation: a cancelled_check lambda is passed into
        # each fetch() call (mirrors the extractor's cancelled_check convention)
        # so a slow/timing-out response does not stall the worker beyond the
        # request timeout; the between-words self.cancelled check exits the loop
        # early. The caller's post-phase checkpoint owns the cancel result.
        #
        # Progress note: on_start/on_complete MUST be called unconditionally
        # when _expression_audio_active (even when media_results is empty) to
        # consume the dedicated band that process_episode registered for this
        # stage. Skipping them would cause StageWeightedProgress.on_start to
        # advance into the wrong band on the next phase (definitions), silently
        # stealing its weight. The gate must NOT include `media_results` here.
        if self._expression_audio_active:
            fetched_count = 0
            if progress_callback is not None:
                progress_callback.on_start(len(media_results), "Fetching expression audio")
            for i, (word, media) in enumerate(media_results):
                if self.cancelled:
                    if progress_callback is not None:
                        progress_callback.on_complete()
                    return media_results
                # Source-priority outer / candidate-ladder inner: each source
                # tries ALL candidate forms before the chain falls through to a
                # lower-priority source, so a synthetic fallback can't satisfy
                # the surface form before JPod101 sees the lemma it actually has.
                path = self.expression_audio_fetcher.fetch_candidates(  # type: ignore[union-attr]
                    _expression_audio_candidates(word),
                    cancelled_check=lambda: self.cancelled,
                )
                if path is not None:
                    media.expression_audio_path = path
                    media.expression_audio_filename = path.name
                    fetched_count += 1
                if progress_callback is not None:
                    progress_callback.on_progress(i + 1, f"Expression audio: {word.mined_form}")
            if progress_callback is not None:
                progress_callback.on_complete()
            self.presenter.show_info(
                tr_format(
                    QCoreApplication.translate("EpisodeProcessor", "Expression audio: %1/%2 available"),
                    fetched_count,
                    len(media_results),
                )
            )
            # Diagnose *why* audio failed when transient failures dominate the
            # run, so an expired JPod101 certificate reads as an actionable
            # warning rather than an indistinguishable low "X/Y available".
            # stats() is duck-typed (like close()); the local-pack fetcher omits
            # it, so a chain without a network source simply has nothing to
            # report.
            stats_fn = getattr(self.expression_audio_fetcher, "stats", None)
            if callable(stats_fn):
                counts = stats_fn()
                # isinstance guard: a duck-typed fetcher (or a test MagicMock)
                # that does not return a real counts dict is ignored, never
                # crashing the run over a diagnostic.
                if isinstance(counts, dict):
                    diagnosis = _audio_failure_diagnosis(counts, len(media_results))
                    if diagnosis is not None:
                        self.presenter.show_warning(diagnosis)

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
        definitions = self.definition_service.get_definitions_batch(
            [w.lemma for w in words_with_media],
            progress_callback,
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
                [w.lemma for w in words_with_media],
                progress_callback,
            )

        # Pitch accents if available.
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
            # Conjugation-chain provenance (3.2): the deinflection trace of the
            # accepted inflected span, joined dictionary-form-outward with the
            # Yomitan " « " separator (food « -ます « … reads how Yomitan's
            # {conjugation} field renders). Gated on the field being mapped AND a
            # non-empty chain, so default-config notes stay byte-identical and
            # uninflected words leave the field untouched.
            if self.config.anki_fields.get("conjugation") and word.inflection_chain:
                extra_fields["conjugation"] = " « ".join(word.inflection_chain)
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
                    "%n media file(s) could not be stored in Anki; those cards will have no audio"
                    " or screenshot. Check that Anki/AnkiConnect is running and see the log for details.",
                    "",
                    media_failures,
                )
            )
        skipped_duplicates = self.anki_service.last_skipped_duplicates
        if isinstance(skipped_duplicates, int) and skipped_duplicates > 0:
            self.presenter.show_warning(
                QCoreApplication.translate(
                    "EpisodeProcessor",
                    "Skipped %n word(s) Anki flagged as duplicates (same Expression as an existing"
                    " card or another word in this batch).",
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
        cancel_event: threading.Event | None = None,
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

        # Reset the partial-IDs accumulator before this episode's run so that
        # if the episode fails mid-batch the except handlers harvest ONLY IDs
        # created during THIS run, not any stale IDs left over from a prior
        # episode on the same processor instance (OVH-008). create_cards_batch
        # resets it again at its own start — this guard is belt-and-suspenders
        # for the case where the failure happens before phase 5 even runs.
        self.anki_service.last_created_note_ids = []

        # Bridge the caller's cancel_event into this run's cancellation
        # checkpoints for the duration of this call only: the phase
        # checkpoints below and the media extractor's cancelled_check consult
        # self.cancelled, which folds this source in. See the __init__
        # comment for why the sticky self._cancelled flag must NOT be used
        # here (shared processor reuse across runs); the finally below drops
        # the reference so the bridge is per-run by construction.
        if cancel_event is not None:
            self._external_cancel = cancel_event.is_set

        # Interactive curation offers a per-word sentence picker, which needs
        # the line index (all lines each lemma appears on). Build it for that
        # path too — not just the i+1 filter.
        want_line_index = curation_callback is not None and not preview_mode
        try:
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
                self.presenter.show_info(QCoreApplication.translate("EpisodeProcessor", "All words already in Anki!"))
                return ctx.build_result(new_words_found=0)
            if self.cancelled:
                return self._cancelled_result_from_ctx(ctx)

            if curation_callback is not None and not preview_mode:
                if line_index is not None:
                    # Attach alternative example sentences so the curator can
                    # offer a per-word sentence picker (no-op for words that
                    # appear on a single line).
                    self.word_filter.attach_sentence_candidates(unknown_words, line_index)
                # Attach per-episode occurrence counts for the curator's
                # "Occurrences" column/sort (Issue #88). count_lemmas reuses the
                # phase-1 parse cache, so no second MeCab pass.
                self.word_filter.attach_occurrence_counts(
                    unknown_words, self.subtitle_parser.count_lemmas(subtitle_file)
                )
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
                    self.presenter.show_info(
                        QCoreApplication.translate("EpisodeProcessor", "No words selected for card creation")
                    )
                    return ctx.build_result(new_words_found=0)
                self.presenter.show_info(
                    QCoreApplication.translate(
                        "EpisodeProcessor", "User selected %n word(s) for card creation", "", len(unknown_words)
                    )
                )

            if preview_mode:
                self.presenter.show_word_preview(unknown_words)
                # Preview reports the would-be-mined forms (no cards are created
                # here). This overloads mined_forms' usual "cards actually created"
                # meaning, but is safe: every consumer is gated on card creation —
                # the Undo button only renders when card_ids exist, the undo
                # callback's remove_words finds no source='mined' rows (preview
                # returns before _phase5_create, the sole writer), and history/
                # stats are gated on cards_created > 0.
                return ctx.build_result(
                    mined_forms=[w.mined_form for w in unknown_words],
                )

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

        except AnkiMinerException as e:
            ctx.errors.append(str(e))
            partial_ids = list(self.anki_service.last_created_note_ids)
            if partial_ids:
                ctx.errors.append(
                    f"Run failed after creating {len(partial_ids)} card(s); " f"they remain in Anki and can be undone."
                )
            self.presenter.show_error(tr_format(QCoreApplication.translate("EpisodeProcessor", "Error: %1"), str(e)))
            return ctx.build_result(
                total_words_found=0,
                new_words_found=0,
                cards_created=len(partial_ids),
                card_ids=partial_ids,
            )
        except Exception as e:
            logger.exception("EpisodeProcessor unhandled exception")
            ctx.errors.append(f"Unexpected error: {e}")
            partial_ids = list(self.anki_service.last_created_note_ids)
            if partial_ids:
                ctx.errors.append(
                    f"Run failed after creating {len(partial_ids)} card(s); " f"they remain in Anki and can be undone."
                )
            self.presenter.show_error(
                tr_format(QCoreApplication.translate("EpisodeProcessor", "Unexpected error: %1"), str(e))
            )
            return ctx.build_result(
                total_words_found=0,
                new_words_found=0,
                cards_created=len(partial_ids),
                card_ids=partial_ids,
            )
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
            preview_mode=preview_mode,
            progress_callback=progress_callback,
            curation_callback=curation_callback,
            episode_name_override=f"YT:{video_id}",
            series_name_override="YouTube",
            source_label_override=source_label,
            cancel_event=cancel_event,
        )
