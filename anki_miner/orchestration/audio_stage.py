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

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from PyQt6.QtCore import QCoreApplication

from anki_miner.config import AnkiMinerConfig
from anki_miner.interfaces import PresenterProtocol, ProgressCallback
from anki_miner.models import MediaData, TokenizedWord
from anki_miner.utils import has_katakana, hiragana_to_katakana
from anki_miner.utils.i18n import tr_format

if TYPE_CHECKING:
    from anki_miner.interfaces.expression_audio import ExpressionAudioFetcher
    from anki_miner.interfaces.sentence_audio import SentenceAudioFetcher


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
    failure bucket (ssl/connection/timeout/http_status/non_audio), aggregated
    across every enabled word-audio source (packs, JPod101, custom URL/JSON,
    gTTS). Threshold/tie-break semantics live in
    :func:`_dominant_transient_failure`.
    """
    dominant = _dominant_transient_failure(counts, attempts)
    if dominant is None:
        return None
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
        start_label: str,
        item_template: str,
        per_item: Callable[[TokenizedWord, MediaData], None],
    ) -> bool:
        """Cancel-aware loop skeleton shared by both fetch entry points.

        Band-invariant (the one behavior a naive extraction drops): once the
        stage is active, ``on_start``/``on_complete`` are called UNCONDITIONALLY
        — even when ``media_results`` is empty — to consume the dedicated
        progress band the processor registered for this stage. Skipping them
        would let ``StageWeightedProgress.on_start`` advance into the wrong band
        on the next phase (definitions), silently stealing its weight. The gate
        (checked by the caller) must NOT include ``media_results``.

        Returns ``False`` when cancelled mid-loop (``on_complete`` already
        emitted, caller must return early without summary/diagnosis), ``True``
        when the loop ran to completion.
        """
        if progress_callback is not None:
            progress_callback.on_start(len(media_results), start_label)
        for i, (word, media) in enumerate(media_results):
            if self._is_cancelled():
                if progress_callback is not None:
                    progress_callback.on_complete()
                return False
            per_item(word, media)
            if progress_callback is not None:
                progress_callback.on_progress(i + 1, tr_format(item_template, word.mined_form))
        if progress_callback is not None:
            progress_callback.on_complete()
        return True

    def _diagnose(
        self,
        fetcher: object,
        diagnose_fn: Callable[[dict[str, int], int], str | None],
        attempts: int,
    ) -> None:
        """Warn when transient failures dominate, so a systemic cause reads as
        actionable rather than an indistinguishable low "X/Y available".

        ``stats()`` is duck-typed (like ``close()``); the local-pack fetcher
        omits it, so a chain without a network source simply has nothing to
        report. The isinstance guard ignores a duck-typed fetcher (or test
        MagicMock) that does not return a real counts dict — never crashing the
        run over a diagnostic.
        """
        stats_fn = getattr(fetcher, "stats", None)
        if callable(stats_fn):
            counts = stats_fn()
            if isinstance(counts, dict):
                diagnosis = diagnose_fn(counts, attempts)
                if diagnosis is not None:
                    self.presenter.show_warning(diagnosis)

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
        if not self.expression_audio_active:
            return
        fetched_count = 0

        def _per_item(word: TokenizedWord, media: MediaData) -> None:
            nonlocal fetched_count
            # Source-priority outer / candidate-ladder inner: each source
            # tries ALL candidate forms before the chain falls through to a
            # lower-priority source, so a synthetic fallback can't satisfy
            # the surface form before JPod101 sees the lemma it actually has.
            path = self.expression_audio_fetcher.fetch_candidates(  # type: ignore[union-attr]
                _expression_audio_candidates(word),
                cancelled_check=self._is_cancelled,
            )
            if path is not None:
                media.expression_audio_path = path
                media.expression_audio_filename = path.name
                fetched_count += 1

        completed = self._run_stage(
            media_results,
            progress_callback,
            QCoreApplication.translate("EpisodeProcessor", "Fetching expression audio"),
            QCoreApplication.translate("EpisodeProcessor", "Expression audio: %1"),
            _per_item,
        )
        if not completed:
            return
        self.presenter.show_info(
            tr_format(
                QCoreApplication.translate("EpisodeProcessor", "Expression audio: %1/%2 available"),
                fetched_count,
                len(media_results),
            )
        )
        # Diagnose *why* audio failed when transient failures dominate the run,
        # so an expired JPod101 certificate reads as an actionable warning.
        self._diagnose(self.expression_audio_fetcher, _audio_failure_diagnosis, len(media_results))

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
        if not self.reading_tts_active:
            return
        # Words share sentences (novel sentence-units, manga bubbles):
        # synthesize once per unique sentence and share the Path. Failures
        # are memoized too, so a failing shared bubble is not re-hammered.
        memo: dict[str, Path | None] = {}

        def _per_item(word: TokenizedWord, media: MediaData) -> None:
            sentence = word.sentence
            if sentence.strip():
                if sentence in memo:
                    path = memo[sentence]
                else:
                    path = self.sentence_audio_fetcher.fetch(  # type: ignore[union-attr]
                        sentence,
                        cancelled_check=self._is_cancelled,
                    )
                    memo[sentence] = path
                if path is not None:
                    media.audio_path = path
                    media.audio_filename = path.name

        completed = self._run_stage(
            media_results,
            progress_callback,
            QCoreApplication.translate("EpisodeProcessor", "Generating sentence audio"),
            QCoreApplication.translate("EpisodeProcessor", "Sentence audio: %1"),
            _per_item,
        )
        if not completed:
            return
        hits = sum(1 for p in memo.values() if p is not None)
        self.presenter.show_info(
            tr_format(
                QCoreApplication.translate("EpisodeProcessor", "Sentence audio: %1/%2 sentences"),
                hits,
                len(memo),
            )
        )
        # Diagnose *why* TTS failed when transient failures dominate. The
        # attempts denominator is the UNIQUE-sentence count (len(memo)): the
        # memo dedups fetch calls, so stats() failures are per unique sentence —
        # a word-count denominator would dilute the ratio.
        self._diagnose(self.sentence_audio_fetcher, _sentence_audio_failure_diagnosis, len(memo))
