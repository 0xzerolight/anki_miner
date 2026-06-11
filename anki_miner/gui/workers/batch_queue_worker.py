"""Worker thread for processing batch queue of multiple folder pairs."""

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from PyQt6.QtCore import pyqtSignal

from anki_miner.config import AnkiMinerConfig
from anki_miner.gui.presenters import GUIPresenter, GUIProgressCallback
from anki_miner.gui.utils.service_factory import create_episode_processor
from anki_miner.gui.workers.base_worker import CancellableWorker
from anki_miner.models.batch_queue import BatchQueue, QueueItemStatus
from anki_miner.orchestration.episode_processor import EpisodeProcessor


class BatchQueueWorkerThread(CancellableWorker):
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
        presenter: GUIPresenter,
        progress_callback: GUIProgressCallback | None = None,
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
        self._curation_processor: EpisodeProcessor | None = None
        self._curation_video: Path | None = None
        self._curation_subtitle: Path | None = None
        self._curation_offset: float = 0.0

    def cancel(self) -> None:
        """Cancel processing, propagating to the current processor."""
        super().cancel()
        if self._current_processor is not None:
            self._current_processor.cancel()

    def run(self):
        """Process all pending items in queue sequentially."""
        total_cards = 0
        total_items = self.batch_queue.pending_count

        self.queue_started.emit(total_items)

        while not self.check_cancelled():
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

                # Create processor for this item with its specific offset
                episode_processor = create_episode_processor(config_with_offset, self.presenter, self.stats_service)
                self._current_processor = episode_processor

                # Use FilePairMatcher for cross-folder pairing
                from anki_miner.utils.file_pairing import FilePairMatcher

                pairs = FilePairMatcher.find_pairs_by_episode_number(item.anime_folder, item.subtitle_folder)

                if not pairs:
                    raise ValueError("No matching video/subtitle pairs found")

                # Process each pair using episode processor
                cards_for_item = 0
                interrupted = False
                failed_pairs: list[tuple[str, str]] = []  # (video name, first error)
                for pair in pairs:
                    if self.check_cancelled():
                        interrupted = True
                        break

                    self._curation_processor = episode_processor
                    self._curation_video = pair.video
                    self._curation_subtitle = pair.subtitle
                    self._curation_offset = item.subtitle_offset
                    result = episode_processor.process_episode(
                        pair.video,
                        pair.subtitle,
                        preview_mode=False,
                        progress_callback=self.progress_callback,
                        curation_callback=self.curation_callback,
                    )
                    cards_for_item += result.cards_created
                    if not result.success:
                        # process_episode returns failures as results with errors
                        # populated (it never raises); surface them per-item so the
                        # GUI marks the item ERROR and offers retry (Issue #51).
                        failed_pairs.append((pair.video.name, result.errors[0]))

                # Partial successes still count toward the queue total (cards
                # created before a cancel exist in Anki).
                total_cards += cards_for_item
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
                        f"{len(failed_pairs)}/{len(pairs)} episodes failed "
                        f"(e.g. {failed_pairs[0][0]}: {failed_pairs[0][1]})"
                    )
                    item.status = QueueItemStatus.ERROR
                    item.error_message = msg
                    self.item_failed.emit(item.id, msg)
                else:
                    item.status = QueueItemStatus.COMPLETED
                    item.cards_created = cards_for_item
                    self.item_completed.emit(item.id, cards_for_item)

            except Exception as e:  # noqa: BLE001 — surface every failure to GUI
                item.status = QueueItemStatus.ERROR
                item.error_message = str(e)
                self.item_failed.emit(item.id, str(e))

        self.queue_finished.emit(total_cards)
