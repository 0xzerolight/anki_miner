"""Worker thread for the deck-builder aggregate → preview → build flow.

Runs the whole deck build off the GUI thread in two phases separated by a
confirm gate:

1. **Aggregate + preview** — combine per-file lemma counts across the request's
   file pairs, compute the candidate selection plus a :class:`DeckBuildPreview`,
   emit the preview, then BLOCK on ``self._confirm_event`` until the GUI calls
   :meth:`confirm` or :meth:`reject` (or the worker is cancelled).
2. **Build** (only if confirmed) — ensure the deck exists, then mine each
   episode through the EXISTING ``EpisodeProcessor.process_episode`` pipeline,
   routing cards to the named deck and carding each selected lemma exactly once
   across the whole batch (cross-episode dedup via the curation callback).
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import replace

from PyQt6.QtCore import pyqtSignal

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.presenters import GUIPresenter, GUIProgressCallback
from anki_miner.gui.utils.service_factory import create_episode_processor
from anki_miner.gui.workers.base_worker import CancellableWorker
from anki_miner.models.deck_build import DeckBuildRequest
from anki_miner.orchestration.episode_processor import EpisodeProcessor
from anki_miner.services.corpus_aggregator import aggregate, select


class DeckBuilderWorker(CancellableWorker):
    """Two-phase deck-builder worker with a GUI confirm gate.

    Inherits thread-safe cancellation and the ``error`` signal from
    :class:`CancellableWorker`.
    """

    preview_ready = pyqtSignal(object)  # emits DeckBuildPreview
    item_started = pyqtSignal(str)  # episode display name
    item_completed = pyqtSignal(str, int)  # episode display name, cards_created
    build_finished = pyqtSignal(int, float)  # total_cards_created, projected_coverage_pct
    # error signal inherited from CancellableWorker

    def __init__(
        self,
        request: DeckBuildRequest,
        config: AnkiMinerConfig,
        presenter: GUIPresenter,
        progress_callback: GUIProgressCallback | None = None,
        stats_service=None,
        parent=None,
    ):
        """Initialize the deck-builder worker.

        Args:
            request: The deck build request (pairs, deck name, mode, value, filter).
            config: Application configuration. Per-episode copies with adjusted
                ``anki_deck_name`` / ``include_known_words`` are created via
                ``dataclasses.replace``; the original is never mutated.
            presenter: GUI presenter for output.
            progress_callback: Optional progress callback forwarded to
                ``process_episode`` for mining-phase reporting.
            stats_service: Optional statistics recording service.
            parent: Optional parent QObject.
        """
        super().__init__(parent)
        self.request = request
        self.config = config
        self.presenter = presenter
        self.progress_callback = progress_callback
        self.stats_service = stats_service
        self._confirm_event = threading.Event()
        self._confirmed = False
        # The per-episode processor currently running in Phase 2. Set before
        # each process_episode call so cancel() can propagate into it.
        self._current_processor: EpisodeProcessor | None = None

    # ------------------------------------------------------------------ #
    # GUI-thread control surface
    # ------------------------------------------------------------------ #

    def confirm(self) -> None:
        """Confirm the build (called from the GUI thread after preview)."""
        self._confirmed = True
        self._confirm_event.set()

    def reject(self) -> None:
        """Reject the build, unblocking the confirm gate without building."""
        self._confirmed = False
        self._confirm_event.set()

    def cancel(self) -> None:
        """Cancel the worker, propagate to the active processor, unblock the gate.

        Phase 2 polls ``check_cancelled()`` only between episodes, so a
        mid-episode cancel would otherwise wait out the whole episode (ffmpeg
        media extraction + lookups). Propagating into the current
        ``EpisodeProcessor`` lets ``process_episode`` return promptly — it polls
        ``self._cancelled`` at every phase boundary.
        """
        super().cancel()
        if self._current_processor is not None:
            self._current_processor.cancel()
        # Wake run() if it is currently blocked on the gate so it can observe
        # the cancellation flag and return.
        self._confirm_event.set()

    # ------------------------------------------------------------------ #
    # Worker body
    # ------------------------------------------------------------------ #

    def run(self) -> None:
        """Run the aggregate → preview → (gated) build flow."""
        try:
            # Phase 1: aggregate + preview. Every step here is slow (processor
            # construction: seconds; aggregate: MeCab over the whole corpus,
            # minutes; known-words fetch: 15-30 s of AnkiConnect HTTP), so
            # cancellation is re-checked between steps — otherwise a Cancel
            # keeps grinding through Phase 1 and even emits preview_ready
            # after the user gave up.
            if self.check_cancelled():
                return
            base = create_episode_processor(self.config, self.presenter, self.stats_service)
            if self.check_cancelled():
                return
            counts = aggregate(base.subtitle_parser, self.request.pairs, cancel_check=self.check_cancelled)
            if self.check_cancelled():
                return
            known = self._known_lemmas(base) if self.request.collection_filter else set()
            if self.check_cancelled():
                return
            selected, preview = select(counts, self.request.mode, self.request.value, known)
            self.preview_ready.emit(preview)

            # Gate: block until the GUI confirms/rejects, or until cancel() fires.
            self._confirm_event.wait()
            if self.check_cancelled() or not self._confirmed:
                return

            # Phase 2: build. Ensure the target deck exists before routing cards.
            base.anki_service.ensure_deck(self.request.deck_name)

            carded: set[str] = set()
            total = 0
            for pair in self.request.pairs:
                if self.check_cancelled():
                    break
                name = pair.video.stem
                self.item_started.emit(name)

                cfg = replace(
                    self.config,
                    anki_deck_name=self.request.deck_name,
                    # collection_filter ON  -> exact known-words subtraction in Phase 2
                    #                          (include_known_words=False).
                    # collection_filter OFF -> mine everything (include_known_words=True).
                    include_known_words=not self.request.collection_filter,
                    # Deck Builder is a complete-deck workflow: word selection is
                    # owned by corpus_aggregator.select + the curation callback, so
                    # the per-episode reduction filters (i+1, frequency, word lists,
                    # sentence dedup/length) must NOT run — otherwise the build
                    # diverges from the preview. Always on regardless of the
                    # collection checkbox.
                    bypass_optional_filters=True,
                    # Re-card words that already exist elsewhere in the user's
                    # collection so the deck is genuinely complete.
                    allow_duplicate_cards=True,
                )
                proc = create_episode_processor(cfg, self.presenter, self.stats_service)
                # Cross-phase tokenization cache: reuse the Phase-1 parser whose
                # per-file line cache was filled by aggregate() → count_lemmas
                # above, so Phase 2's parse_subtitle_file* hits the cache instead
                # of re-running MeCab over every file a second time. Safe because
                # SubtitleParserService reads only subtitle_offset /
                # bold_target_in_sentence / allowed_pos / excluded_subtypes /
                # the regex-filter fields — none of which the Phase-2 cfg changes
                # (it only overrides anki_deck_name / include_known_words /
                # bypass_optional_filters / allow_duplicate_cards). Parse output
                # is therefore byte-identical to a freshly-constructed parser.
                proc.subtitle_parser = base.subtitle_parser
                # Register as current BEFORE process_episode so a mid-call
                # cancel() reaches this processor.
                self._current_processor = proc
                # A cancel() that landed while create_episode_processor was
                # still constructing `proc` propagated to the PREVIOUS
                # processor (or None) — and the loop-top check ran before that
                # window opened, so it cannot cover it. Re-check now that
                # `proc` is registered: cancel() sets the flag before reading
                # _current_processor, so any cancel this check misses lands on
                # `proc` directly instead.
                if self.check_cancelled():
                    break
                callback = self._make_curation_callback(selected, carded)
                # Empty-curation pitfall: process_episode treats a curation_callback
                # that returns [] as a cancellation and returns a cancelled-empty
                # ProcessingResult (cards_created=0) — see episode_processor.py:
                #
                #     if curation_callback is not None and not preview_mode:
                #         unknown_words = curation_callback(unknown_words)
                #         ctx.new_words_found = len(unknown_words)
                #         if not unknown_words:
                #             return self._cancelled_result_from_ctx(ctx)
                #
                # That early-return is LOCAL to this single process_episode call and
                # does NOT cancel the worker. We only read ``result.cards_created``
                # (0 for such an episode), so an episode with no newly-selected
                # lemmas simply contributes zero cards and the loop continues. We
                # deliberately let the callback return the (possibly empty) filtered
                # list rather than pre-skipping the episode: pre-skipping would
                # require re-deriving each episode's mineable lemma set here, and a
                # zero-card result is already the correct, harmless outcome.
                result = proc.process_episode(
                    pair.video,
                    pair.subtitle,
                    curation_callback=callback,
                    series_name_override=self.request.deck_name,
                    episode_name_override=name,
                    progress_callback=self.progress_callback,
                )
                total += result.cards_created
                self.item_completed.emit(name, result.cards_created)

            # A mid-build cancel breaks out of the loop above; do NOT emit
            # build_finished in that case — the GUI would otherwise show a
            # "build complete" summary for a partial, cancelled run.
            if self.check_cancelled():
                return
            self.build_finished.emit(total, preview.projected_coverage_pct)
        except Exception as e:  # noqa: BLE001 — surface every failure to the GUI
            self.error.emit(str(e))

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    def _make_curation_callback(
        self,
        selected: set[str],
        carded: set[str],
    ) -> Callable[[list], list]:
        """Build a curation callback enforcing cross-episode single-carding.

        The returned closure keeps a word iff its lemma is in ``selected`` and
        not yet in ``carded``, then records every kept lemma in ``carded`` so the
        same lemma is never carded twice across episodes (it cards at its first
        occurrence in batch order). ``carded`` is shared across all episodes in
        one build.
        """

        def callback(words: list) -> list:
            kept = []
            for w in words:
                if w.lemma in selected and w.lemma not in carded:
                    kept.append(w)
                    carded.add(w.lemma)
            return kept

        return callback

    def _known_lemmas(self, base: EpisodeProcessor) -> set[str]:
        """Fetch known lemmas for the PREVIEW ESTIMATE only.

        Mirrors Phase-2's known-words gate EXACTLY (episode_processor.py): the
        local known-words DB cache is used only when BOTH ``use_known_words_db``
        is on AND the DB is available; otherwise it falls back to
        ``anki_service.get_existing_vocabulary()``. The DB *file* exists for any
        user who curated a word via "Mark known" regardless of the toggle, so
        keying on the file alone made the preview subtract a stale/user-only set
        while the build subtracted live Anki vocab — the "promised 2,401, built
        51" divergence class. The source='user' ignore list (Issue #42) is
        always unioned in, matching the build's always-applied user list.

        NOTE: this is an ESTIMATE. Corpus lemmas are keyed by dictionary lemma,
        while Anki known-words are keyed by ``mined_form`` (surface form for
        nouns). So ``known_skipped`` in the preview is approximate. The EXACT
        known-words filtering happens during the build via the existing Phase-2
        path (collection_filter ON → ``include_known_words=False``). We do not
        attempt to reconcile the two key spaces here.
        """
        # User-curated ignore list (Issue #42): always applied in Phase 2
        # regardless of the use_known_words_db toggle; fold it in for parity.
        user_words: set[str] = set()
        if base.known_word_db and base.known_word_db.is_available():
            user_words = base.known_word_db.get_words_by_source("user")

        if self.config.use_known_words_db and base.known_word_db and base.known_word_db.is_available():
            return base.known_word_db.get_known_words() | user_words
        return base.anki_service.get_existing_vocabulary() | user_words
