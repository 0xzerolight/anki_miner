"""Worker thread for processing batch queue of multiple folder pairs."""

import contextlib
import logging
from collections.abc import Callable
from dataclasses import replace
from functools import partial
from pathlib import Path

from PyQt6.QtCore import QCoreApplication, pyqtSignal

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.utils.service_factory import (
    SharedLookupServices,
    create_episode_processor,
    create_shared_lookup_services,
)
from anki_miner.gui.workers._queue_worker_base import RunBoundaryControls, queue_preflight_error
from anki_miner.gui.workers.base_worker import ProcessorOwningWorker
from anki_miner.interfaces.presenter import PresenterProtocol
from anki_miner.interfaces.progress import ProgressCallback
from anki_miner.models.batch_queue import BatchQueue, QueueItem, QueueItemStatus
from anki_miner.orchestration.episode_processor import EpisodeProcessor, require_usable_offline_provider
from anki_miner.services.anki_service import AnkiService
from anki_miner.services.resource_staleness import stale_resource_reimport_error
from anki_miner.services.word_pool import (
    CaptureCurationCallback,
    MinePassStats,
    fixed_selection,
    merge_pools,
    split_selection,
)
from anki_miner.utils.file_pairing import FilePair

logger = logging.getLogger(__name__)


class BatchQueueWorkerThread(RunBoundaryControls, ProcessorOwningWorker):
    """Worker thread for processing multiple folder pairs sequentially.

    Inherits thread-safe cancellation from CancellableWorker.
    """

    # Signals for queue-level progress
    queue_started = pyqtSignal(int)  # total_items
    item_started = pyqtSignal(str, str)  # item_id, display_name
    # Real within-series position: how many of this run's pending pairs have
    # concluded their attempt. This is the only within-item number the bar may
    # draw — it is a count of finished episodes, not a stage-weight estimate.
    item_pairs_progress = pyqtSignal(str, int, int)  # item_id, pairs_done, pairs_total
    item_completed = pyqtSignal(str, int)  # item_id, run_cards_created
    item_failed = pyqtSignal(str, str, int)  # item_id, error_message, run_cards_created
    queue_finished = pyqtSignal(int)  # run_cards_created
    # The run stopped at a series boundary, and later left it again (D29-A).
    run_paused = pyqtSignal()
    run_resumed = pyqtSignal()

    def __init__(
        self,
        batch_queue: BatchQueue,
        config: AnkiMinerConfig,
        presenter: PresenterProtocol,
        progress_callback: ProgressCallback | None = None,
        stats_service=None,
        curation_callback: Callable[[list], list | None] | None = None,
        parent=None,
        items: list[QueueItem] | None = None,
    ):
        """Initialize the batch queue worker thread.

        Args:
            batch_queue: BatchQueue containing items to process
            config: Application configuration (a per-item copy with adjusted subtitle_offset is created via dataclasses.replace; the original is not mutated)
            presenter: GUI presenter for output
            progress_callback: Optional progress callback for updates
            stats_service: Optional statistics recording service
            curation_callback: Optional callable forwarded to process_episode for word curation
            parent: Optional parent QObject
            items: The exact series to run, in the order the user arranged them
                (D29-A). Identities, not copies -- each ``QueueItem`` carries the
                episode receipts a retry must preserve. ``None`` falls back to
                every PENDING row, still snapshotted once at ``run()``: polling
                the live queue between series is what let a mid-run edit change
                what the run was doing.
        """
        super().__init__(parent)
        self.batch_queue = batch_queue
        self._requested_items = items
        self.config = config
        self.presenter = presenter
        self.progress_callback = progress_callback
        self.stats_service = stats_service
        self._current_processor: EpisodeProcessor | None = None
        self.curation_callback = curation_callback
        # Published per-pair for the GUI curation bridge (mirrors ManualPairWorkerThread's
        # _curation_* attrs so BatchProcessingTab reads one attribute name across both workers).
        self._curation_video: Path | None = None
        self._curation_subtitle: Path | None = None
        self._curation_offset: float = 0.0
        # Season mode: episode → (subtitle, offset) for every pre-passed pair,
        # published while the worker is parked at the curation gate so the tab
        # can build per-episode media contexts; cleared once the gate releases.
        self._curation_media_map: dict[Path, tuple[Path, float]] | None = None
        # The series this run will process, frozen at run() start.
        self._run_items: list[QueueItem] = []
        self._init_boundary_controls()

    @property
    def curation_processor(self) -> EpisodeProcessor | None:
        """The per-item processor for the current (or most recent) queue item.

        Set before each item's pairs are processed, so it is always the live
        processor by the time the curation bridge blocks the run loop.
        """
        return self._current_processor

    def cancel(self) -> None:
        """Cancel processing, propagating to the current processor.

        The boundary gate is released unconditionally: a run paused between
        series would otherwise never see the cancel.
        """
        super().cancel()
        self._release_boundary_gate()
        if self._current_processor is not None:
            self._current_processor.cancel()

    def _close_current_processor(self) -> None:
        """Release the current per-item processor's sqlite handles + Session.

        Closing must never abort the queue, so any error is swallowed (the
        processor is being discarded anyway). Between items this prevents run
        N's leaked handles/sockets from accumulating into run N+1 — the Windows
        back-to-back-mining freeze.
        """
        if self._current_processor is None:
            return
        with contextlib.suppress(Exception):
            self._current_processor.close()
        # Drop the reference so the finally-block close after the loop doesn't
        # double-close a processor already released at the top of the next item.
        self._current_processor = None

    def run(self):
        """Process all pending items in queue sequentially."""
        self.log_start("BatchQueueWorkerThread", items=len(self.batch_queue.get_all_items()))
        total_cards = 0
        # Frozen here, before anything runs: from this point the run's item
        # total, its progress numbers and its receipt all describe the same set
        # of series, whatever happens to the panel (D29-A).
        self._run_items = self._snapshot_items()

        self.queue_started.emit(len(self._run_items))

        try:
            total_cards = self._run_queue(total_cards)
        except Exception as e:  # noqa: BLE001 — surface every failure to GUI
            # Setup work OUTSIDE the per-item try (stale-dict gate,
            # AnkiService construction — which raises ValueError on missing
            # anki_fields — and get_next_pending) runs in the reimplemented
            # QThread.run(); an escaping exception here is a PyQt6 FATAL
            # abort. Catch it, surface via error, and still emit
            # queue_finished so the GUI leaves the running state (mirrors
            # ManualPairWorkerThread.run()).
            logger.exception("BatchQueueWorker run failed before/around the item loop")
            self.error.emit(str(e))
        finally:
            # Close the final item's processor on every exit (normal, cancel,
            # or exception) so its sqlite handles / Session don't leak.
            self._close_current_processor()
            self.queue_finished.emit(total_cards)

    def _snapshot_items(self) -> list[QueueItem]:
        """The exact series this run will process, in order.

        The caller's list when it supplied one; otherwise every PENDING row,
        read once. Either way the loop below iterates this snapshot rather than
        re-asking the queue between series.
        """
        if self._requested_items is not None:
            return list(self._requested_items)
        return [item for item in self.batch_queue.get_all_items() if item.status == QueueItemStatus.PENDING]

    def _run_queue(self, total_cards: int) -> int:
        # Schema-staleness pre-loop gate (4.0): if any enabled indexed dict slot
        # needs reimport, abort the WHOLE queue with a single actionable error
        # up front rather than emitting one silent zero-card failure per item.
        stale_msg = stale_resource_reimport_error(self.config)
        if stale_msg is not None:
            self.error.emit(stale_msg)
            return total_cards

        # Build ONE shared AnkiService for the whole run so its vocab cache
        # (get_existing_vocabulary) survives across all queue items. Each item
        # gets a fresh EpisodeProcessor (different subtitle_offset) but shares
        # this instance. AnkiService has no close(); EpisodeProcessor.close()
        # does NOT close it, so _close_current_processor() between items is safe.
        # (ankiconnect_url / anki_fields / excluded_decks are identical for all
        # items in a run — only subtitle_offset differs via config_with_offset.)
        shared_anki_service = AnkiService(self.config)

        # Build the offset-independent lookup stack (dict registry + eager dict
        # load + pitch CSV parse + frequency registry load) ONCE for the whole
        # run instead of once per item — on this worker thread, same as the old
        # per-item builds. Its load messages surface once per run here; the
        # per-item create_episode_processor calls then skip those loads (and
        # their messages) entirely. Processors built over the bundle do NOT
        # close its sqlite handles (owns_lookup_services=False); the finally
        # below is the run-level Issue #30 teardown on every exit path.
        shared_lookup = create_shared_lookup_services(self.config)
        for msg in shared_lookup.load_result.info:
            self.presenter.show_info(msg)
        for msg in shared_lookup.load_result.warnings:
            self.presenter.show_warning(msg)
        try:
            preflight_error = queue_preflight_error(
                shared_anki_service.verify_card_target,
                partial(
                    require_usable_offline_provider,
                    self.config,
                    shared_lookup.definition_service,
                ),
            )
            if preflight_error is not None:
                self.error.emit(preflight_error)
                return total_cards
            total_cards = self._process_items(total_cards, shared_anki_service, shared_lookup)
        finally:
            try:
                shared_lookup.close()
            except Exception as e:  # noqa: BLE001 — cleanup must not replace the run result
                logger.exception("BatchQueueWorker shared lookup close failed")
                self.error.emit(str(e))
        return total_cards

    def _process_items(
        self,
        total_cards: int,
        shared_anki_service: AnkiService,
        shared_lookup: SharedLookupServices,
    ) -> int:
        """Run the per-item loop over the run-scoped shared services."""
        for item in self._run_items:
            if self.check_cancelled():
                break
            # Pause / Finish-current land between series, never inside an
            # episode or a SQLite/ffmpeg call (D29-A).
            if not self._wait_at_boundary():
                break
            # Close the previous item's processor before building the next
            # item's, so handles never accumulate across items.
            self._close_current_processor()

            # OWNERSHIP: during a run, this worker thread owns every QueueItem
            # status/result write, applied synchronously at pick/finish time so
            # get_next_pending() can never re-pick an in-flight item. Relying on
            # the queued GUI slots to write status raced the loop: a finished
            # (or fast-failed) item was still PENDING until the GUI event loop
            # caught up, and got picked again. BatchProcessingTab's slots are
            # render-only; between runs (retry reset, repopulation) the GUI
            # thread owns the model.
            with self._stop_claim_lock:
                if self._stop_after_current.is_set():
                    break
                if item.status != QueueItemStatus.PENDING:
                    continue  # already terminal from an earlier run in this session
                item.status = QueueItemStatus.PROCESSING
            self.item_started.emit(item.id, item.display_name)

            cards_for_item = 0
            try:
                # Create config with item's subtitle offset
                config_with_offset = replace(self.config, subtitle_offset=item.subtitle_offset)

                # Create processor for this item with its specific offset,
                # injecting the shared AnkiService (vocab cache persists) and
                # the shared lookup bundle (dict/pitch/frequency built once per
                # run; the processor won't close them between items).
                episode_processor = create_episode_processor(
                    config_with_offset,
                    self.presenter,
                    self.stats_service,
                    anki_service=shared_anki_service,
                    shared_lookup=shared_lookup,
                )
                self._current_processor = episode_processor

                # Use FilePairMatcher for cross-folder pairing
                from anki_miner.utils.file_pairing import FilePairMatcher

                pairs = FilePairMatcher.find_pairs_by_episode_number(item.video_folder, item.subtitle_folder)

                if not pairs:
                    raise ValueError("No matching video/subtitle pairs found")

                committed_pair_keys = item.committed_pair_keys
                pending_pairs = []
                for pair in pairs:
                    pair_key = (pair.video.resolve(), pair.subtitle.resolve())
                    if pair_key not in committed_pair_keys:
                        pending_pairs.append((pair, pair_key))

                # Process each pair using episode processor. The pending count
                # is real, so the GUI may compose it into the series bar; every
                # concluded attempt (success, soft failure, raise) ticks once.
                pairs_done = 0
                self.item_pairs_progress.emit(item.id, pairs_done, len(pending_pairs))
                interrupted = False
                failed_pairs: list[tuple[str, str]] = []  # (video name, first error)
                if self.curation_callback is not None:
                    # Season mode: one curator for the whole item — pre-pass
                    # every pair, review once, then mine. Replaces the per-pair
                    # loop below; the terminal-status block after it is shared.
                    cards_for_item, failed_pairs, interrupted = self._process_item_pairs_season(
                        item, episode_processor, pending_pairs, committed_pair_keys
                    )
                else:
                    for pair, pair_key in pending_pairs:
                        if self.check_cancelled():
                            interrupted = True
                            break

                        self._curation_video = pair.video
                        self._curation_subtitle = pair.subtitle
                        self._curation_offset = item.subtitle_offset
                        try:
                            result = episode_processor.process_episode(
                                pair.video,
                                pair.subtitle,
                                progress_callback=self.progress_callback,
                                curation_callback=self.curation_callback,
                            )
                        except Exception as e:  # noqa: BLE001 — preflight (Issue #52) can raise
                            # Per-pair guard: process_episode now runs the card-target
                            # preflight (Issue #52) OUTSIDE its own try, so it can raise
                            # SetupError/AnkiConnectionError. Without this guard a single
                            # transient AnkiConnect blip aborted the item's remaining pairs
                            # AND dropped cards already created for earlier pairs from the
                            # count. Record the failure and continue (mirrors
                            # ManualPairWorkerThread's per-pair except).
                            logger.exception("BatchQueueWorker pair %s failed", pair.video.name)
                            failed_pairs.append((pair.video.name, str(e)))
                            pairs_done += 1
                            self.item_pairs_progress.emit(item.id, pairs_done, len(pending_pairs))
                            continue
                        cards_for_item += result.cards_created
                        if result.success:
                            committed_pair_keys.add(pair_key)
                        pairs_done += 1
                        self.item_pairs_progress.emit(item.id, pairs_done, len(pending_pairs))
                        if self.check_cancelled():
                            interrupted = True
                            break
                        if not result.success:
                            # process_episode also returns soft failures as results with
                            # errors populated; surface them per-item so the GUI marks the
                            # item ERROR and offers retry (Issue #51).
                            failed_pairs.append((pair.video.name, result.errors[0]))

                # Partial successes still count toward the queue total (cards
                # created before a cancel exist in Anki).
                total_cards += cards_for_item
                item.cards_created = getattr(item, "cards_created", 0) + cards_for_item
                item.committed_pair_keys = committed_pair_keys
                if interrupted:
                    # Cancelled between pairs: the item is partially processed,
                    # neither completed nor failed, so no terminal signal —
                    # falling through used to mark it COMPLETED. Return it to
                    # PENDING for a future run; the loop iterates a frozen
                    # snapshot and the cancel check at its top ends the run, so
                    # the item cannot be re-picked here.
                    item.status = QueueItemStatus.PENDING
                elif failed_pairs:
                    msg = (
                        f"{len(failed_pairs)}/{len(pending_pairs)} episodes failed "
                        f"(e.g. {failed_pairs[0][0]}: {failed_pairs[0][1]})"
                    )
                    item.status = QueueItemStatus.ERROR
                    item.error_message = msg
                    self.item_failed.emit(item.id, msg, cards_for_item)
                else:
                    item.status = QueueItemStatus.COMPLETED
                    self.item_completed.emit(item.id, cards_for_item)

            except Exception as e:  # noqa: BLE001 — surface every failure to GUI
                logger.exception("BatchQueueWorker item %s failed", item.id)
                item.status = QueueItemStatus.ERROR
                item.error_message = str(e)
                self.item_failed.emit(item.id, str(e), cards_for_item)

        return total_cards

    def _process_item_pairs_season(
        self,
        item: QueueItem,
        episode_processor: EpisodeProcessor,
        pending_pairs: list[tuple[FilePair, tuple[Path, Path]]],
        committed_pair_keys: set[tuple[Path, Path]],
    ) -> tuple[int, list[tuple[str, str]], bool]:
        """Season mode: one curator for the whole series item.

        Pre-pass every pending pair through ``process_episode`` with a capture
        callback (words collected, zero cards), show the curator ONCE with the
        merged season pool, then mine each episode's curated subset. Returns
        ``(cards_for_item, failed_pairs, interrupted)`` for the caller's shared
        terminal-status block.

        The whole season item is one boundary unit: Pause / Finish-current
        still land only between items, and every ``committed_pair_keys`` write
        happens after the curator returns — a cancel at (or before) the
        curator leaves the item fully PENDING, so a retry re-pre-passes every
        uncommitted pair.

        Progress contract: ``item_pairs_progress`` ticks only in the mine pass
        (concluded mining attempts — the bar's documented meaning); the
        pre-pass narrates through the presenter + per-episode stage sweeps.
        """
        pairs_total = len(pending_pairs)
        pairs_done = 0
        cards_for_item = 0
        failed_pairs: list[tuple[str, str]] = []

        # --- Pre-pass: collect every episode's reviewable words (no cards). ---
        self.presenter.show_info(
            QCoreApplication.translate(
                "BatchQueueWorkerThread",
                "Collecting words from %n episode(s) for review...",
                "",
                pairs_total,
            )
        )
        capture = CaptureCurationCallback()
        prepass_ok: list[tuple[FilePair, tuple[Path, Path]]] = []
        for pair, pair_key in pending_pairs:
            if self.check_cancelled():
                return cards_for_item, failed_pairs, True
            capture.set_episode(pair.video)
            try:
                result = episode_processor.process_episode(
                    pair.video,
                    pair.subtitle,
                    progress_callback=self.progress_callback,
                    curation_callback=capture,
                )
            except Exception as e:  # noqa: BLE001 — preflight (Issue #52) can raise
                logger.exception("BatchQueueWorker season pre-pass pair %s failed", pair.video.name)
                failed_pairs.append((pair.video.name, str(e)))
                continue
            if self.check_cancelled():
                return cards_for_item, failed_pairs, True
            if not result.success:
                failed_pairs.append((pair.video.name, result.errors[0]))
                continue
            prepass_ok.append((pair, pair_key))

        pool = merge_pools(capture.pools)
        if not pool or not prepass_ok:
            # Nothing to review anywhere: the pre-passed episodes ARE this
            # item's outcome (zero-card successes), no curator to show.
            for _pair, pair_key in prepass_ok:
                committed_pair_keys.add(pair_key)
                pairs_done += 1
                self.item_pairs_progress.emit(item.id, pairs_done, pairs_total)
            return cards_for_item, failed_pairs, False

        # --- One curator for the merged season pool. ---
        first_pair = prepass_ok[0][0]
        self._curation_video = first_pair.video
        self._curation_subtitle = first_pair.subtitle
        self._curation_offset = item.subtitle_offset
        self._curation_media_map = {pair.video: (pair.subtitle, item.subtitle_offset) for pair, _key in prepass_ok}
        try:
            assert self.curation_callback is not None  # season mode precondition
            selection = self.curation_callback(pool)
        finally:
            self._curation_media_map = None
        if selection is None or self.check_cancelled():
            # Reject cancels the whole run (the bridge already called
            # cancel()); nothing committed, item returns to PENDING.
            return cards_for_item, failed_pairs, True

        # --- Mine pass: each episode gets its curated subset. ---
        subsets = split_selection(selection)
        strays = subsets.pop(None, None)
        if strays:
            # Defensive: unstamped words mine from the first episode.
            subsets.setdefault(first_pair.video, []).extend(strays)

        original_stats = episode_processor.stats_service
        if original_stats is not None:
            # The pre-pass already recorded one difficulty row per episode;
            # the mine pass must not insert duplicates.
            episode_processor.stats_service = MinePassStats(original_stats)
        try:
            for pair, pair_key in prepass_ok:
                if self.check_cancelled():
                    return cards_for_item, failed_pairs, True
                subset = subsets.get(pair.video, [])
                if not subset:
                    # User selected nothing from this episode — the pre-pass
                    # zero-card success stands; mirror per-episode [] = skip.
                    committed_pair_keys.add(pair_key)
                    pairs_done += 1
                    self.item_pairs_progress.emit(item.id, pairs_done, pairs_total)
                    continue
                try:
                    result = episode_processor.process_episode(
                        pair.video,
                        pair.subtitle,
                        progress_callback=self.progress_callback,
                        # Curated objects pass through verbatim to phases 3-5:
                        # chosen sentence, clip_override and times are already
                        # this episode's (same offset both passes).
                        curation_callback=fixed_selection(subset),
                    )
                except Exception as e:  # noqa: BLE001 — preflight (Issue #52) can raise
                    logger.exception("BatchQueueWorker season mine pair %s failed", pair.video.name)
                    failed_pairs.append((pair.video.name, str(e)))
                    pairs_done += 1
                    self.item_pairs_progress.emit(item.id, pairs_done, pairs_total)
                    continue
                cards_for_item += result.cards_created
                if result.success:
                    committed_pair_keys.add(pair_key)
                pairs_done += 1
                self.item_pairs_progress.emit(item.id, pairs_done, pairs_total)
                if self.check_cancelled():
                    return cards_for_item, failed_pairs, True
                if not result.success:
                    failed_pairs.append((pair.video.name, result.errors[0]))
        finally:
            episode_processor.stats_service = original_stats

        return cards_for_item, failed_pairs, False
