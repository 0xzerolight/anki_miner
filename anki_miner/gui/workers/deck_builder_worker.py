"""Deck Builder worker: one show as a Batch season item, with a Build gate.

The show's folders run as a single ``BatchQueueWorkerThread`` season item under
a build-only config (:func:`deck_build_config`), after the deck is created.
Batch's three season hooks do the rest:

- ``_make_capture`` drops sentence alternatives at capture time when Review is
  off, since only the curator ever shows them.
- ``_prepass_message`` narrates the pre-pass as a scan.
- ``_select_season_pool`` counts every pre-passed episode's lemmas, emits a
  :class:`DeckCorpus` preview, blocks until :meth:`DeckBuilderWorker.confirm`
  or :meth:`DeckBuilderWorker.cancel`, then cuts the merged pool to the rows
  whose lemmas the chosen selection hits.

The inherited curator gate (Review on) and mine pass run unchanged.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from PyQt6.QtCore import QCoreApplication, pyqtSignal

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.workers.batch_queue_worker import BatchQueueWorkerThread
from anki_miner.interfaces.presenter import PresenterProtocol
from anki_miner.interfaces.progress import ProgressCallback
from anki_miner.models.batch_queue import BatchQueue
from anki_miner.models.deck_build import DeckBuildRequest, DeckCorpus, DeckSelectionMode
from anki_miner.models.word import TokenizedWord
from anki_miner.orchestration.episode_processor import EpisodeProcessor
from anki_miner.services.anki_service import AnkiService
from anki_miner.services.corpus_aggregator import aggregate, rank_select, row_lemmas
from anki_miner.services.resource_staleness import stale_resource_reimport_error
from anki_miner.services.stats_service import StatsService
from anki_miner.services.word_pool import CaptureCurationCallback, MinePassStats
from anki_miner.utils.file_pairing import FilePair
from anki_miner.utils.logging_ext import log_summary

logger = logging.getLogger(__name__)


def deck_build_config(config: AnkiMinerConfig, request: DeckBuildRequest) -> AnkiMinerConfig:
    """The config a deck build mines under: the request's deck, filters ignored."""
    # Owner decision 2026-09-25: a build ignores Word Filters / Sentences. The
    # three flags are the internal ones the golden contract and e2e harness set.
    return replace(
        config,
        anki_deck_name=request.deck_name,
        include_known_words=not request.skip_known,
        bypass_optional_filters=True,
        allow_duplicate_cards=True,
    )


def _accept_all(words: list) -> list:
    return words


class DeckBuilderWorker(BatchQueueWorkerThread):
    """Builds one show's deck on Batch's season pipeline, gated on a preview.

    Inherits ``item_started``, ``item_pairs_progress``, ``item_completed``,
    ``item_failed``, ``queue_finished``, ``error``, ``finished`` and
    ``curation_processor`` from :class:`BatchQueueWorkerThread`.
    """

    preview_ready = pyqtSignal(object)  # DeckCorpus

    def __init__(
        self,
        request: DeckBuildRequest,
        config: AnkiMinerConfig,
        presenter: PresenterProtocol,
        progress_callback: ProgressCallback | None = None,
        stats_service=None,
        curation_callback: Callable[[list], list | None] | None = None,
        parent=None,
    ) -> None:
        """Initialize the worker for one show.

        Args:
            request: The show's folders, deck name and scan options.
            config: Application configuration; the run uses
                :func:`deck_build_config` over it.
            presenter: GUI presenter for output.
            progress_callback: Optional progress callback for updates.
            stats_service: Optional statistics service. A real
                ``StatsService`` is wrapped so neither pass writes Analytics
                difficulty rows.
            curation_callback: The Word Curator bridge, used only when
                ``request.review`` is set; otherwise every selected row mines.
            parent: Optional parent QObject.
        """
        # Locals first: nothing is set on a QObject before its __init__ runs.
        queue = BatchQueue()
        item = queue.add_item(
            request.video_folder,
            request.subtitle_folder,
            request.deck_name,
            request.subtitle_offset,
            secondary_folder=request.secondary_folder,
            secondary_offset=request.secondary_offset,
        )
        # A preview is not a viewing: no Analytics difficulty rows, pre-pass or mine pass.
        stats = MinePassStats(stats_service) if isinstance(stats_service, StatsService) else stats_service
        callback = curation_callback if (request.review and curation_callback is not None) else _accept_all
        super().__init__(
            queue,
            deck_build_config(config, request),
            presenter,
            progress_callback,
            stats_service=stats,
            curation_callback=callback,
            parent=parent,
            items=[item],
        )
        self.request = request
        self.item = item
        self._confirm_event = threading.Event()
        self._selection: tuple[DeckSelectionMode, float] | None = None
        self._capture: CaptureCurationCallback | None = None
        self.corpus: DeckCorpus | None = None

    def confirm(self, mode: DeckSelectionMode, value: float) -> None:
        """Build with this selection; valid before, at, or after the gate."""
        self._selection = (mode, float(value))
        self._confirm_event.set()

    def cancel(self) -> None:
        """Cancel the run; also releases a worker parked at the Build gate."""
        super().cancel()
        self._confirm_event.set()

    def _run_queue(self, total_cards: int) -> int:
        # The deck must exist before the card-target preflight and each
        # episode's own preflight. A stale-index run is refused by super()
        # without creating anything.
        if stale_resource_reimport_error(self.config) is None:
            try:
                AnkiService(self.config).ensure_deck(self.request.deck_name)
            except Exception as e:  # noqa: BLE001 - surfaced once, the run never starts
                logger.exception("DeckBuilderWorker could not create deck %s", self.request.deck_name)
                self.error.emit(str(e))
                return total_cards
        try:
            return super()._run_queue(total_cards)
        finally:
            # A cancel or exception during the pre-pass or counting can exit
            # before _select_season_pool ever clears the per-episode pools;
            # this is the run's own exit, so it is the last chance to drop
            # them rather than let a finished worker keep holding them.
            self._capture = None

    def _make_capture(self) -> CaptureCurationCallback:
        # Kept so _select_season_pool can drop the per-episode lists once merged.
        self._capture = CaptureCurationCallback(keep_candidates=self.request.review)
        return self._capture

    def _prepass_message(self, pairs_total: int) -> str:
        return QCoreApplication.translate("DeckBuilderWorker", "Scanning %n episode(s)...", "", pairs_total)

    def _select_season_pool(
        self,
        pool: list[TokenizedWord],
        prepass_ok: list[tuple[FilePair, tuple[Path, Path]]],
        episode_processor: EpisodeProcessor,
    ) -> list[TokenizedWord] | None:
        counts = aggregate(
            episode_processor.subtitle_parser,
            [pair.subtitle for pair, _key in prepass_ok],
            cancel_check=self.check_cancelled,
        )
        if self.check_cancelled():
            return None
        rows = [row_lemmas(word) for word in pool]
        # The merged rows own everything the build still needs; the per-episode
        # capture lists would otherwise stay alive through the whole mine pass.
        if self._capture is not None:
            self._capture.pools.clear()
        self.corpus = DeckCorpus(counts=dict(counts), row_lemmas=tuple(rows), episodes=len(prepass_ok))
        log_summary(
            logger,
            "DeckBuilderWorker preview",
            deck=self.request.deck_name,
            episodes=self.corpus.episodes,
            unique_lemmas=len(counts),
            rows=len(rows),
        )
        self.preview_ready.emit(self.corpus)
        self._confirm_event.wait()
        if self.check_cancelled() or self._selection is None:
            return None
        mode, value = self._selection
        selected = rank_select(counts, mode, value)
        chosen = [word for word, lemmas in zip(pool, rows, strict=True) if lemmas & selected]
        log_summary(logger, "DeckBuilderWorker build", mode=mode.value, value=value, cards=len(chosen))
        return chosen
