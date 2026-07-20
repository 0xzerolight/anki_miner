"""Worker thread for processing batch queue of multiple folder pairs."""

import contextlib
import logging
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from PyQt6.QtCore import pyqtSignal

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.utils.service_factory import (
    SharedLookupServices,
    create_episode_processor,
    create_shared_lookup_services,
)
from anki_miner.gui.workers.base_worker import ProcessorOwningWorker
from anki_miner.interfaces.presenter import PresenterProtocol
from anki_miner.interfaces.progress import ProgressCallback
from anki_miner.models.batch_queue import BatchQueue, QueueItemStatus
from anki_miner.orchestration.episode_processor import EpisodeProcessor
from anki_miner.services.anki_service import AnkiService
from anki_miner.services.dictionary.registry import stale_dict_reimport_error

logger = logging.getLogger(__name__)


class BatchQueueWorkerThread(ProcessorOwningWorker):
    """Worker thread for processing multiple folder pairs sequentially.

    Inherits thread-safe cancellation from CancellableWorker.
    """

    # Signals for queue-level progress
    queue_started = pyqtSignal(int)  # total_items
    item_started = pyqtSignal(str, str)  # item_id, display_name
    item_completed = pyqtSignal(str, int)  # item_id, cards_created
    item_failed = pyqtSignal(str, str)  # item_id, error_message
    queue_finished = pyqtSignal(int)  # total_cards_created

    def __init__(
        self,
        batch_queue: BatchQueue,
        config: AnkiMinerConfig,
        presenter: PresenterProtocol,
        progress_callback: ProgressCallback | None = None,
        stats_service=None,
        curation_callback: Callable[[list], list | None] | None = None,
        parent=None,
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
        """
        super().__init__(parent)
        self.batch_queue = batch_queue
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

    @property
    def curation_processor(self) -> EpisodeProcessor | None:
        """The per-item processor for the current (or most recent) queue item.

        Set before each item's pairs are processed, so it is always the live
        processor by the time the curation bridge blocks the run loop.
        """
        return self._current_processor

    def cancel(self) -> None:
        """Cancel processing, propagating to the current processor."""
        super().cancel()
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
        total_cards = self.batch_queue.total_cards_created
        if not isinstance(total_cards, int):
            total_cards = 0
        total_items = self.batch_queue.pending_count

        self.queue_started.emit(total_items)

        try:
            try:
                self._run_queue(total_cards)
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
                self.queue_finished.emit(total_cards)
        finally:
            # Close the final item's processor on every exit (normal, cancel,
            # or exception) so its sqlite handles / Session don't leak.
            self._close_current_processor()

    def _run_queue(self, total_cards: int) -> None:
        # Schema-staleness pre-loop gate (4.0): if any enabled indexed dict slot
        # needs reimport, abort the WHOLE queue with a single actionable error
        # up front rather than emitting one silent zero-card failure per item.
        stale_msg = stale_dict_reimport_error(self.config)
        if stale_msg is not None:
            self.error.emit(stale_msg)
            self.queue_finished.emit(total_cards)
            return

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
            self._process_items(total_cards, shared_anki_service, shared_lookup)
        finally:
            shared_lookup.close()

    def _process_items(
        self,
        total_cards: int,
        shared_anki_service: AnkiService,
        shared_lookup: SharedLookupServices,
    ) -> None:
        """Run the per-item loop over the run-scoped shared services."""
        while not self.check_cancelled():
            # Close the previous item's processor before building the next
            # item's, so handles never accumulate across items.
            self._close_current_processor()

            item = self.batch_queue.get_next_pending()
            if item is None:
                break  # No more pending items

            # OWNERSHIP: during a run, this worker thread owns every QueueItem
            # status/result write, applied synchronously at pick/finish time so
            # get_next_pending() can never re-pick an in-flight item. Relying on
            # the queued GUI slots to write status raced the loop: a finished
            # (or fast-failed) item was still PENDING until the GUI event loop
            # caught up, and got picked again. BatchProcessingTab's slots are
            # render-only; between runs (retry reset, repopulation) the GUI
            # thread owns the model.
            item.status = QueueItemStatus.PROCESSING
            self.item_started.emit(item.id, item.display_name)

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

                # Process each pair using episode processor
                cards_for_item = 0
                interrupted = False
                failed_pairs: list[tuple[str, str]] = []  # (video name, first error)
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
                        continue
                    cards_for_item += result.cards_created
                    if result.success:
                        committed_pair_keys.add(pair_key)
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
                    # PENDING for a future run; cancellation is sticky
                    # (threading.Event), so the outer while exits before this
                    # item could be re-picked in this run.
                    item.status = QueueItemStatus.PENDING
                elif failed_pairs:
                    msg = (
                        f"{len(failed_pairs)}/{len(pending_pairs)} episodes failed "
                        f"(e.g. {failed_pairs[0][0]}: {failed_pairs[0][1]})"
                    )
                    item.status = QueueItemStatus.ERROR
                    item.error_message = msg
                    self.item_failed.emit(item.id, msg)
                else:
                    item.status = QueueItemStatus.COMPLETED
                    self.item_completed.emit(item.id, item.cards_created)

            except Exception as e:  # noqa: BLE001 — surface every failure to GUI
                logger.exception("BatchQueueWorker item %s failed", item.id)
                item.status = QueueItemStatus.ERROR
                item.error_message = str(e)
                self.item_failed.emit(item.id, str(e))

        self.queue_finished.emit(total_cards)
