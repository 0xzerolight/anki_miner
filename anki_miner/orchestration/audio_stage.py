"""Expression- and sentence-audio stage extracted from ``EpisodeProcessor``.

This is the one seam the god-module keep-verdict sanctions: the audio cluster
touches no pipeline ctx — only the two fetchers, the presenter, the config
gates, and a live cancelled check — so it lifts cleanly out of the phase
methods, which stay inline. ``EpisodeProcessor`` still constructs and closes
the fetchers; :class:`AudioStage` only orchestrates the per-run fetch loops and
their progress-band accounting.

The two fetch entry points are structural clones (source-priority word audio vs
memoized sentence TTS); they share one cancel-aware loop skeleton
(:meth:`AudioStage._run_stage`) and one diagnosis helper
(:meth:`AudioStage._diagnose`).
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PyQt6.QtCore import QCoreApplication

from anki_miner.config import AnkiMinerConfig
from anki_miner.interfaces import PresenterProtocol, ProgressCallback
from anki_miner.models import MediaData, TokenizedWord
from anki_miner.services.audio_fetch_common import (
    expression_audio_candidates as _expression_audio_candidates,
)
from anki_miner.utils.i18n import tr_format
from anki_miner.utils.logging_ext import log_summary
from anki_miner.utils.timing import timed_phase

if TYPE_CHECKING:
    from anki_miner.interfaces.expression_audio import ExpressionAudioFetcher
    from anki_miner.interfaces.sentence_audio import SentenceAudioFetcher

logger = logging.getLogger(__name__)


def _candidate_ladder(fetcher: Any, word: TokenizedWord) -> list[tuple[str, str]]:
    """The fetcher's own ladder, or the Japanese one for a plain duck fetcher.

    The probe is load-bearing, not defensive: several existing suites inject
    minimal fetcher fakes that satisfy the ExpressionAudioFetcher Protocol and
    nothing more, and those must keep mining with the ja ladder.

    It looks the method up on the fetcher's TYPE rather than the instance
    because those suites pass a bare ``MagicMock``, on which every instance
    attribute auto-exists: an instance-level probe would hand the stage a
    MagicMock in place of the candidate list and silently take the ja path off
    the ladder. A real fetcher class declaring ``candidates_for`` is still
    honoured, mock or not.
    """
    if hasattr(type(fetcher), "candidates_for"):
        ladder: list[tuple[str, str]] = fetcher.candidates_for(word)
        return ladder
    return _expression_audio_candidates(word)


def _dominant_transient_failure(counts: dict[str, int], attempts: int) -> str | None:
    """Return the dominant failure bucket when transient failures dominate.

    Shared threshold logic for the expression- and sentence-audio diagnoses.
    Only reports when failures cover at least half the attempted items — a
    genuine "not in any source" miss is never counted, so a high total means
    something systemic (expired certificate, outage, rate-limit) rather than
    items simply being absent. Scattered failures among mostly-successful
    fetches stay quiet (None).

    Ties resolve to the earliest bucket (ssl first) via ``max`` over a stable
    key order, matching Yomitan's priority on the most actionable cause.
    """
    total = sum(counts.values())
    if attempts <= 0 or total == 0:
        return None
    # Require failures to cover at least half the attempts before raising
    # the alarm; below that they are noise beside real hits and misses.
    if total * 2 < attempts:
        return None
    return max(counts, key=lambda key: counts[key])


def _audio_failure_diagnosis(counts: dict[str, int], attempts: int) -> str | None:
    """Name the dominant expression-audio failure cause, or None.

    ``counts`` is a ChainedExpressionAudioFetcher ``stats()`` tally keyed by
    failure bucket (ssl/connection/timeout/http_status/non_audio/slow),
    aggregated across every enabled word-audio source (packs, JPod101, custom
    URL/JSON, gTTS). Threshold/tie-break semantics live in
    :func:`_dominant_transient_failure`.
    """
    dominant = _dominant_transient_failure(counts, attempts)
    if dominant is None:
        return None
    if dominant == "slow":
        # Distinct from every other bucket: nothing failed and nothing is
        # retried differently next run. The source is reachable and answering,
        # just far slower than the per-word budget, so the actionable advice is
        # to reorder or disable it rather than to wait it out.
        return QCoreApplication.translate(
            "EpisodeProcessor",
            "Word-audio source is responding too slowly — audio skipped for those words. "
            "Reorder or disable it in Settings -> Audio if this keeps happening.",
        )
    if dominant in ("ssl", "connection", "timeout"):
        return QCoreApplication.translate(
            "EpisodeProcessor",
            "Word-audio source connection/certificate failure — audio skipped this run, will retry next run",
        )
    if dominant == "http_status":
        return QCoreApplication.translate(
            "EpisodeProcessor",
            "Word-audio source returned repeated server errors — audio skipped this run, will retry next run",
        )
    return QCoreApplication.translate(
        "EpisodeProcessor",
        "Word-audio source returned non-audio responses (likely rate-limited) — audio skipped this run, will retry next run",
    )


def _sentence_audio_failure_diagnosis(counts: dict[str, int], attempts: int) -> str | None:
    """Name the dominant sentence-TTS failure cause, or None.

    Sentence analogue of :func:`_audio_failure_diagnosis`. ``attempts`` must
    be the UNIQUE-sentence count (the per-run memo dedups fetch calls, so the
    stats tally is per unique sentence) — a word-count denominator would
    dilute the ratio and silence the warning exactly when many words share a
    few failing sentences.
    """
    dominant = _dominant_transient_failure(counts, attempts)
    if dominant is None:
        return None
    if dominant in ("ssl", "connection", "timeout"):
        return QCoreApplication.translate(
            "EpisodeProcessor",
            "Sentence-audio TTS connection/certificate failure — sentence audio skipped this run, will retry next run",
        )
    if dominant == "http_status":
        return QCoreApplication.translate(
            "EpisodeProcessor",
            "Sentence-audio TTS returned repeated server errors — sentence audio skipped this run, will retry next run",
        )
    return QCoreApplication.translate(
        "EpisodeProcessor",
        "Sentence-audio TTS returned non-audio responses (likely rate-limited) — sentence audio skipped this run, will retry next run",
    )


class AudioStage:
    """Fetch expression (word) audio and sentence TTS into ``MediaData`` fields.

    Owns neither construction nor teardown of the fetchers — ``EpisodeProcessor``
    builds them and closes their sessions. This stage only decides whether each
    optional audio kind is active (the two gates) and runs its fetch loop.

    ``cancelled`` is a **live** callable (bridge to the processor's per-run
    external-cancel source), NOT a snapshot boolean: it is read afresh at the
    top of every loop iteration and forwarded into each fetch call so a slow
    response cannot stall a cancelled run.
    """

    def __init__(
        self,
        config: AnkiMinerConfig,
        presenter: PresenterProtocol,
        cancelled: Callable[[], bool],
        expression_audio_fetcher: ExpressionAudioFetcher | None,
        sentence_audio_fetcher: SentenceAudioFetcher | None,
    ) -> None:
        self.config = config
        self.presenter = presenter
        self._is_cancelled = cancelled
        self.expression_audio_fetcher = expression_audio_fetcher
        self.sentence_audio_fetcher = sentence_audio_fetcher
        self._started_diagnostic_stages: set[str] = set()

    @property
    def expression_audio_active(self) -> bool:
        """True when the expression-audio stage should run and occupy a progress band.

        The two-part gate (Issue #73, simplified): fetcher injected AND the
        expression_audio Anki field mapped (non-empty). The field name is the
        sole on/off switch, matching the frequency/pitch optional fields — no
        dedicated enable flag. Checked in two places — the processor's
        ``process_episode`` (band registration) and
        :meth:`fetch_expression_audio` (band consumption) — via this property so
        the conditions can't drift apart.
        """
        return self.expression_audio_fetcher is not None and bool(self.config.anki_fields.get("expression_audio"))

    @property
    def reading_tts_active(self) -> bool:
        """True when the sentence-TTS stage should run and occupy a progress band.

        Four-part gate: fetcher injected AND the master flag on AND the
        sentence-audio Anki field (key ``audio``) mapped AND at least one
        provider selected. The dedicated ``reading_tts_enabled`` flag exists
        because — unlike expression_audio — the ``audio`` field is mapped by
        default, so field-presence cannot express consent. Checked in two
        places — the processor's ``process_reading`` (band registration) and
        :meth:`fetch_sentence_audio` (band consumption) — via this property so
        the conditions can't drift apart.
        """
        return (
            self.sentence_audio_fetcher is not None
            and self.config.reading_tts_enabled
            and bool(self.config.anki_fields.get("audio"))
            and (self.config.reading_tts_google_enabled or self.config.reading_tts_papago_enabled)
        )

    def _run_stage(
        self,
        media_results: list[tuple[TokenizedWord, MediaData]],
        progress_callback: ProgressCallback | None,
        stage: str,
        enabled_entries: int,
        fetcher: object,
        diagnose_fn: Callable[[dict[str, int], int], str | None],
        start_label: str,
        item_template: str,
        per_item: Callable[[TokenizedWord, MediaData], bool | None],
    ) -> tuple[bool, int, int, int]:
        """Cancel-aware loop skeleton shared by both fetch entry points.

        Band-invariant (the one behavior a naive extraction drops): once the
        stage is active, ``on_start``/``on_complete`` are called UNCONDITIONALLY
        — even when ``media_results`` is empty — to consume the dedicated
        progress band the processor registered for this stage. Skipping them
        would let ``StageWeightedProgress.on_start`` advance into the wrong band
        on the next phase (definitions), silently stealing its weight. The gate
        (checked by the caller) must NOT include ``media_results``.

        ``per_item`` returns True for a new successful fetch, False for a new
        miss, and None when no fetch was attempted (blank sentence or a memoized
        sentence). This keeps the shared boundary authoritative for attempts,
        hits, and misses without logging inside the hot loop.

        Returns ``(completed, attempts, hits, misses)``. ``completed`` is False
        when cancelled mid-loop (``on_complete`` already emitted; caller must
        return early without a presenter summary). The diagnostic and log
        summary are emitted here only after a completed loop.
        """
        words = len(media_results)
        if stage in self._started_diagnostic_stages:
            failure_counts_before = self._failure_counts(fetcher)
        else:
            # Fetchers are fresh when AudioStage is built, so the first
            # generation starts from zero. This also keeps simple duck-typed
            # test fetchers free to expose only their post-run tally.
            failure_counts_before = {}
            self._started_diagnostic_stages.add(stage)
        log_summary(
            logger,
            "Audio stage",
            stage=stage,
            words=words,
            chain_entries=enabled_entries,
        )
        attempts = 0
        hits = 0
        misses = 0
        with timed_phase(f"{stage}_audio", logger):
            if progress_callback is not None:
                progress_callback.on_start(words, start_label)
            for i, (word, media) in enumerate(media_results):
                if self._is_cancelled():
                    if progress_callback is not None:
                        progress_callback.on_complete()
                    return False, attempts, hits, misses
                outcome = per_item(word, media)
                if outcome is not None:
                    attempts += 1
                    if outcome:
                        hits += 1
                    else:
                        misses += 1
                if progress_callback is not None:
                    progress_callback.on_progress(i + 1, tr_format(item_template, word.mined_form))
            if progress_callback is not None:
                progress_callback.on_complete()

            failure_counts = self._diagnose(
                fetcher,
                diagnose_fn,
                attempts,
                stage,
                failure_counts_before,
            )
            log_summary(
                logger,
                "Audio stage done",
                stage=stage,
                words=words,
                attempts=attempts,
                hits=hits,
                misses=misses,
                **failure_counts,
            )
        return True, attempts, hits, misses

    @staticmethod
    def _failure_counts(fetcher: object) -> dict[str, int]:
        stats_fn = getattr(fetcher, "stats", None)
        if not callable(stats_fn):
            return {}
        counts = stats_fn()
        if not isinstance(counts, dict):
            return {}
        return {key: value for key, value in counts.items() if isinstance(key, str) and isinstance(value, int)}

    def _diagnose(
        self,
        fetcher: object,
        diagnose_fn: Callable[[dict[str, int], int], str | None],
        attempts: int,
        stage: str,
        counts_before: dict[str, int],
    ) -> dict[str, int]:
        """Warn when transient failures dominate, so a systemic cause reads as
        actionable rather than an indistinguishable low "X/Y available".

        ``stats()`` is duck-typed (like ``close()``); the local-pack fetcher
        omits it, so a chain without a network source simply has nothing to
        report. Counts are lifetime-cumulative, so only deltas since this stage
        started may be compared with its per-stage attempt count. Counter resets
        clamp to zero. Invalid stats never break a run.
        """
        current = self._failure_counts(fetcher)
        counts = {key: max(0, value - counts_before.get(key, 0)) for key, value in current.items()}
        diagnosis = diagnose_fn(counts, attempts)
        if diagnosis is not None:
            log_summary(
                logger,
                "Audio stage diagnosis",
                level=logging.WARNING,
                stage=stage,
                diagnosis=diagnosis,
            )
            self.presenter.show_warning(diagnosis)
        return counts

    def fetch_expression_audio(
        self,
        media_results: list[tuple[TokenizedWord, MediaData]],
        progress_callback: ProgressCallback | None,
    ) -> None:
        # Expression (pronunciation) audio, Issue #73. Sequential on purpose:
        # the fetcher rate-limits and caches internally and never raises, so
        # the loop needs no try/except, no sleep, and no parallelism. Gated on
        # the toggle AND a mapped field — fetching audio no card would use is
        # wasted network. Cancellation: a cancelled_check callable is passed into
        # each fetch() call (mirrors the extractor's cancelled_check convention)
        # so a slow/timing-out response does not stall the worker beyond the
        # request timeout; the between-words cancel check in _run_stage exits the
        # loop early. The caller's post-phase checkpoint owns the cancel result.
        #
        # Progress note: on_start/on_complete MUST be called unconditionally when
        # this stage is active (even when media_results is empty) to consume the
        # dedicated band that process_episode registered — see _run_stage.
        active = self.expression_audio_active
        reason: str | None = None
        if not active:
            reason = "field_not_mapped" if not self.config.anki_fields.get("expression_audio") else "chain_empty"
        log_summary(logger, "Expression audio gate", active=active, reason=reason)
        if not active:
            return
        enabled_entries = sum(entry.enabled for entry in self.config.expression_audio_chain)

        def _per_item(word: TokenizedWord, media: MediaData) -> bool:
            # Source-priority outer / candidate-ladder inner: each source
            # tries ALL candidate forms before the chain falls through to a
            # lower-priority source, so a synthetic fallback can't satisfy
            # the surface form before JPod101 sees the lemma it actually has.
            path = self.expression_audio_fetcher.fetch_candidates(  # type: ignore[union-attr]
                _candidate_ladder(self.expression_audio_fetcher, word),
                cancelled_check=self._is_cancelled,
            )
            if path is not None:
                media.expression_audio_path = path
                media.expression_audio_filename = path.name
                return True
            return False

        completed, _, hits, _ = self._run_stage(
            media_results,
            progress_callback,
            "expression",
            enabled_entries,
            self.expression_audio_fetcher,
            _audio_failure_diagnosis,
            QCoreApplication.translate("EpisodeProcessor", "Fetching expression audio"),
            QCoreApplication.translate("EpisodeProcessor", "Expression audio: %1"),
            _per_item,
        )
        if not completed:
            return
        self.presenter.show_info(
            tr_format(
                QCoreApplication.translate("EpisodeProcessor", "Expression audio: %1/%2 available"),
                hits,
                len(media_results),
            )
        )

    def fetch_sentence_audio(
        self,
        media_results: list[tuple[TokenizedWord, MediaData]],
        progress_callback: ProgressCallback | None,
    ) -> None:
        # Sentence TTS for reading sources. Structural clone of
        # fetch_expression_audio: sequential on purpose (the fetcher
        # rate-limits, caches, and never raises — no try/except, no sleep, no
        # parallelism here). Reads word.sentence AFTER curation/i+1 swap
        # (phase order guarantees it), so audio always matches the card's
        # final sentence.
        #
        # Progress note: on_start/on_complete MUST be called unconditionally when
        # this stage is active (even when media_results is empty) to consume the
        # band process_reading registered — same discipline as expression audio,
        # centralized in _run_stage.
        active = self.reading_tts_active
        reason: str | None = None
        enabled_entries = sum((self.config.reading_tts_google_enabled, self.config.reading_tts_papago_enabled))
        if not active:
            if not self.config.reading_tts_enabled:
                reason = "disabled"
            elif not self.config.anki_fields.get("audio"):
                reason = "field_not_mapped"
            else:
                reason = "chain_empty"
        log_summary(logger, "Sentence audio gate", active=active, reason=reason)
        if not active:
            return
        # Words share sentences (novel sentence-units, manga bubbles):
        # synthesize once per unique sentence and share the Path. Failures
        # are memoized too, so a failing shared bubble is not re-hammered.
        memo: dict[str, Path | None] = {}

        def _per_item(word: TokenizedWord, media: MediaData) -> bool | None:
            sentence = word.sentence
            if sentence.strip():
                if sentence in memo:
                    path = memo[sentence]
                    outcome = None
                else:
                    path = self.sentence_audio_fetcher.fetch(  # type: ignore[union-attr]
                        sentence,
                        cancelled_check=self._is_cancelled,
                    )
                    memo[sentence] = path
                    outcome = path is not None
                if path is not None:
                    media.audio_path = path
                    media.audio_filename = path.name
                return outcome
            return None

        completed, attempts, hits, _ = self._run_stage(
            media_results,
            progress_callback,
            "sentence",
            enabled_entries,
            self.sentence_audio_fetcher,
            _sentence_audio_failure_diagnosis,
            QCoreApplication.translate("EpisodeProcessor", "Generating sentence audio"),
            QCoreApplication.translate("EpisodeProcessor", "Sentence audio: %1"),
            _per_item,
        )
        if not completed:
            return
        self.presenter.show_info(
            tr_format(
                QCoreApplication.translate("EpisodeProcessor", "Sentence audio: %1/%2 sentences"),
                hits,
                attempts,
            )
        )
